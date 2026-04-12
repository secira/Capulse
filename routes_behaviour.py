"""
Behavioural AI Routes — Target Capital
Serves the Behavioural Insights dashboard, sub-pages, and pre-trade check API.
"""
from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from app import app, db
import logging

logger = logging.getLogger(__name__)


def _get_engine():
    from services.behaviour_engine import BehaviourEngine
    return BehaviourEngine(current_user.id, current_user.tenant_id or 'live')


@app.route('/dashboard/behavioural-insights')
@login_required
def behavioural_insights():
    try:
        engine = _get_engine()
        analysis = engine.get_full_analysis()
    except Exception as e:
        logger.error(f"Behavioural analysis error: {e}")
        analysis = None

    return render_template(
        'dashboard/behaviour/overview.html',
        analysis=analysis,
        page_title='Behavioural Insights',
    )


@app.route('/dashboard/behavioural-insights/trading')
@login_required
def behavioural_trading():
    try:
        engine = _get_engine()
        data = engine.get_trading_behavior()
        stats = {
            'total_trades': len(engine._get_trades()),
            'by_hour': engine.get_win_rate_by_hour(),
        }
    except Exception as e:
        logger.error(f"Trading behavior error: {e}")
        data = None
        stats = {}

    return render_template(
        'dashboard/behaviour/trading.html',
        data=data, stats=stats,
        page_title='Trading Behavior',
    )


@app.route('/dashboard/behavioural-insights/risk')
@login_required
def behavioural_risk():
    try:
        engine = _get_engine()
        data = engine.get_risk_behavior()
    except Exception as e:
        logger.error(f"Risk behavior error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/risk.html',
        data=data,
        page_title='Risk Analysis',
    )


@app.route('/dashboard/behavioural-insights/portfolio')
@login_required
def behavioural_portfolio():
    try:
        engine = _get_engine()
        data = engine.get_portfolio_behavior()
    except Exception as e:
        logger.error(f"Portfolio behavior error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/portfolio.html',
        data=data,
        page_title='Portfolio Behavior',
    )


@app.route('/dashboard/behavioural-insights/performance')
@login_required
def behavioural_performance():
    try:
        engine = _get_engine()
        data = engine.get_performance_patterns()
    except Exception as e:
        logger.error(f"Performance patterns error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/performance.html',
        data=data,
        page_title='Performance Patterns',
    )


@app.route('/dashboard/behavioural-insights/psychology')
@login_required
def behavioural_psychology():
    try:
        engine = _get_engine()
        data = engine.get_psychology_patterns()
    except Exception as e:
        logger.error(f"Psychology patterns error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/psychology.html',
        data=data,
        page_title='Psychological Patterns',
    )


@app.route('/api/behaviour/pre-trade-check', methods=['POST'])
@login_required
def behaviour_pre_trade_check():
    try:
        engine = _get_engine()
        warnings = engine.pre_trade_check()
        return jsonify({'warnings': warnings})
    except Exception as e:
        logger.error(f"Pre-trade check error: {e}")
        return jsonify({'warnings': []})


@app.route('/api/behaviour/alert/<int:alert_id>/acknowledge', methods=['POST'])
@login_required
def acknowledge_behaviour_alert(alert_id):
    from models import BehaviouralAlert
    from datetime import datetime
    alert = BehaviouralAlert.query.filter_by(
        id=alert_id, user_id=current_user.id
    ).first_or_404()
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})
