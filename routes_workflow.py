import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user

from app import db

logger = logging.getLogger(__name__)

workflow_bp = Blueprint('workflow', __name__)

SYMBOL_RE = re.compile(r'^[A-Z0-9&._-]{1,20}$')
MAX_QUERY_LENGTH = 2000
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 3600

_rate_counters: dict = defaultdict(list)


def _check_rate_limit(user_id: int) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    window_start = now - RATE_LIMIT_WINDOW
    _rate_counters[user_id] = [t for t in _rate_counters[user_id] if t > window_start]
    if len(_rate_counters[user_id]) >= RATE_LIMIT_MAX:
        return False
    _rate_counters[user_id].append(now)
    if len(_rate_counters) > 1000:
        stale = [uid for uid, ts in _rate_counters.items() if not ts or ts[-1] < window_start]
        for uid in stale:
            del _rate_counters[uid]
    return True


def rate_limited(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_rate_limit(current_user.id):
            return jsonify({'error': 'Rate limit exceeded. Maximum 10 workflow calls per hour.'}), 429
        return f(*args, **kwargs)
    return decorated


@workflow_bp.route('/dashboard/workflows')
@login_required
def workflow_hub():
    return render_template('dashboard/workflows/workflow_hub.html')


def _get_tenant_id():
    try:
        return getattr(current_user, 'tenant_id', 'live') or 'live'
    except Exception:
        return 'live'


def _save_execution(execution_id, workflow_type, user_id, input_params, result_data, status, error=None):
    try:
        from models import WorkflowExecution, WorkflowStep
        tenant_id = _get_tenant_id()

        execution = WorkflowExecution(
            execution_id=execution_id,
            workflow_type=workflow_type,
            status=status,
            user_id=user_id,
            tenant_id=tenant_id,
            input_params=input_params,
            result=result_data.get('result') if result_data else None,
            error_message=error,
            model_used='claude-sonnet-4-5',
            total_tokens=result_data.get('total_tokens', 0) if result_data else 0,
            total_cost_usd=result_data.get('total_cost', 0.0) if result_data else 0.0,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc) if status in ('completed', 'failed') else None,
            duration_ms=result_data.get('duration_ms', 0) if result_data else 0,
        )
        db.session.add(execution)
        db.session.flush()

        steps = result_data.get('steps', []) if result_data else []
        for i, step in enumerate(steps):
            ws = WorkflowStep(
                execution_id=execution.id,
                step_order=i + 1,
                step_name=step.get('name', f'step_{i+1}'),
                step_type=step.get('type', 'ai_analysis'),
                status=step.get('status', 'completed'),
                output_data=step.get('output'),
                error_message=step.get('error'),
                model_used=step.get('model'),
                tokens_used=step.get('tokens', 0),
                cost_usd=step.get('cost', 0.0),
                started_at=datetime.fromisoformat(step['started_at']) if step.get('started_at') else None,
                completed_at=datetime.fromisoformat(step['completed_at']) if step.get('completed_at') else None,
                duration_ms=step.get('duration_ms', 0),
            )
            db.session.add(ws)

        db.session.commit()
        return execution
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save workflow execution: {e}")
        return None


def _build_step_list(summary):
    steps = []
    node_statuses = summary.get('node_statuses', {})
    node_results = summary.get('node_results', {})
    for name, status in node_statuses.items():
        node_result = node_results.get(name, {})
        steps.append({
            'name': name,
            'type': 'ai_analysis',
            'status': status,
            'output': node_result if isinstance(node_result, dict) else None,
            'model': 'claude-sonnet-4-5' if status == 'completed' else None,
            'tokens': 0,
            'cost': 0.0,
            'duration_ms': 0,
        })
    return steps


@workflow_bp.route('/api/workflow/iscore', methods=['POST'])
@login_required
@rate_limited
def run_iscore_workflow():
    data = request.get_json() or {}
    symbol = data.get('symbol', '').upper().strip()
    asset_type = data.get('asset_type', 'stocks')

    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
    if not SYMBOL_RE.match(symbol):
        return jsonify({'error': 'Invalid symbol format. Use 1-20 uppercase alphanumeric characters.'}), 400

    execution_id = str(uuid.uuid4())[:12]

    try:
        from services.workflow_iscore import IScoreWorkflow
        workflow = IScoreWorkflow()
        result = workflow.calculate_iscore(
            symbol=symbol,
            asset_type=asset_type,
            user_id=current_user.id,
        )

        status = 'completed' if result.get('status') == 'completed' else 'failed'
        result_data = {
            'result': result,
            'total_tokens': 0,
            'total_cost': 0.0,
            'duration_ms': int(result.get('duration_ms', 0)),
            'steps': _build_step_list(result),
        }

        _save_execution(execution_id, 'iscore', current_user.id, data, result_data, status)

        return jsonify({
            'execution_id': execution_id,
            'status': status,
            'workflow_type': 'iscore',
            'result': result,
        })

    except Exception as e:
        logger.error(f"I-Score workflow failed: {e}", exc_info=True)
        _save_execution(execution_id, 'iscore', current_user.id, data, None, 'failed', str(e))
        return jsonify({'error': str(e), 'execution_id': execution_id}), 500


@workflow_bp.route('/api/workflow/research', methods=['POST'])
@login_required
@rate_limited
def run_research_workflow():
    data = request.get_json() or {}
    query = data.get('query', '').strip()

    if not query:
        return jsonify({'error': 'Query is required'}), 400
    if len(query) > MAX_QUERY_LENGTH:
        return jsonify({'error': f'Query too long. Maximum {MAX_QUERY_LENGTH} characters.'}), 400

    execution_id = str(uuid.uuid4())[:12]

    try:
        from services.workflow_research import ResearchWorkflowPipeline
        pipeline = ResearchWorkflowPipeline()
        result = pipeline.execute(
            query=query,
            user_id=current_user.id,
        )

        execution_summary = result.get('execution', {})
        status = 'completed' if execution_summary.get('status') == 'completed' else 'failed'
        result_data = {
            'result': result,
            'total_tokens': 0,
            'total_cost': 0.0,
            'duration_ms': int(execution_summary.get('duration_ms', 0)),
            'steps': _build_step_list(execution_summary),
        }

        _save_execution(execution_id, 'research', current_user.id, data, result_data, status)

        if status == 'completed':
            try:
                pipeline.save_research_results(query, current_user.id, result, _get_tenant_id())
            except Exception as save_err:
                logger.warning(f"Research result persistence failed: {save_err}")

        return jsonify({
            'execution_id': execution_id,
            'status': status,
            'workflow_type': 'research',
            'result': result,
        })

    except Exception as e:
        logger.error(f"Research workflow failed: {e}", exc_info=True)
        _save_execution(execution_id, 'research', current_user.id, data, None, 'failed', str(e))
        return jsonify({'error': str(e), 'execution_id': execution_id}), 500


@workflow_bp.route('/api/workflow/portfolio', methods=['POST'])
@login_required
@rate_limited
def run_portfolio_workflow():
    data = request.get_json() or {}
    execution_id = str(uuid.uuid4())[:12]

    try:
        from services.workflow_portfolio import PortfolioAnalysisWorkflow
        workflow = PortfolioAnalysisWorkflow()
        result = workflow.run(user_id=current_user.id)

        status = 'completed' if result.get('status') == 'completed' else 'failed'
        result_data = {
            'result': result,
            'total_tokens': 0,
            'total_cost': 0.0,
            'duration_ms': int(result.get('duration_ms', 0)),
            'steps': _build_step_list(result),
        }

        _save_execution(execution_id, 'portfolio', current_user.id, data, result_data, status)

        return jsonify({
            'execution_id': execution_id,
            'status': status,
            'workflow_type': 'portfolio',
            'result': result,
        })

    except Exception as e:
        logger.error(f"Portfolio workflow failed: {e}", exc_info=True)
        _save_execution(execution_id, 'portfolio', current_user.id, data, None, 'failed', str(e))
        return jsonify({'error': str(e), 'execution_id': execution_id}), 500


@workflow_bp.route('/api/workflow/executions', methods=['GET'])
@login_required
def list_executions():
    workflow_type = request.args.get('type')
    limit = min(int(request.args.get('limit', 20)), 100)

    try:
        from models import WorkflowExecution
        tenant_id = _get_tenant_id()
        query = WorkflowExecution.query.filter_by(user_id=current_user.id, tenant_id=tenant_id)
        if workflow_type:
            query = query.filter_by(workflow_type=workflow_type)
        query = query.order_by(WorkflowExecution.created_at.desc()).limit(limit)
        executions = query.all()

        return jsonify({
            'executions': [e.to_dict() for e in executions],
            'count': len(executions),
        })
    except Exception as e:
        logger.error(f"Failed to list executions: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_bp.route('/api/workflow/executions/<execution_id>', methods=['GET'])
@login_required
def get_execution(execution_id):
    try:
        from models import WorkflowExecution
        execution = WorkflowExecution.query.filter_by(
            execution_id=execution_id,
            user_id=current_user.id,
        ).first()

        if not execution:
            return jsonify({'error': 'Execution not found'}), 404

        return jsonify(execution.to_dict())
    except Exception as e:
        logger.error(f"Failed to get execution: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_bp.route('/api/workflow/connectors', methods=['GET'])
@login_required
def list_connectors():
    try:
        from services.data_connectors import ConnectorRegistry
        registry = ConnectorRegistry()
        registered = registry.list_registered()
        return jsonify({
            'connectors': registered,
            'count': len(registered),
        })
    except Exception as e:
        logger.error(f"Failed to list connectors: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_bp.route('/api/workflow/connectors/health', methods=['GET'])
@login_required
def check_connector_health():
    try:
        from services.data_connectors import ConnectorRegistry
        registry = ConnectorRegistry()
        health = registry.health_check_all()
        return jsonify({'health': health})
    except Exception as e:
        logger.error(f"Failed to check connector health: {e}")
        return jsonify({'error': str(e)}), 500
