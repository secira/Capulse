"""
F&O Analysis Routes — Multi-Index Options Engine
Supports NIFTY 50, Bank Nifty, Fin Nifty and SENSEX.
"""

from flask import Blueprint, render_template, jsonify, request, make_response
from flask_login import login_required, current_user
from decorators import paid_plan_required
from datetime import timezone
import logging
import pytz

IST = pytz.timezone('Asia/Kolkata')

logger = logging.getLogger(__name__)

fno_bp = Blueprint('fno', __name__, url_prefix='/dashboard/fno')

# ── Index page configs (passed to shared template) ─────────────────────────
# lot_size kept in sync with INDEX_CONFIGS in nifty_options_engine.py
_INDEX_PAGE_CONFIGS = {
    'nifty':     {'index_id': 'NIFTY',     'display_name': 'NIFTY 50',   'short_name': 'NIFTY',     'accent': '#3b82f6', 'lot_size': 50},
    'banknifty': {'index_id': 'BANKNIFTY', 'display_name': 'Bank Nifty', 'short_name': 'BANKNIFTY', 'accent': '#8b5cf6', 'lot_size': 15},
    'finnifty':  {'index_id': 'FINNIFTY',  'display_name': 'Fin Nifty',  'short_name': 'FINNIFTY',  'accent': '#10b981', 'lot_size': 40},
    'sensex':    {'index_id': 'SENSEX',    'display_name': 'SENSEX',     'short_name': 'SENSEX',    'accent': '#f59e0b', 'lot_size': 10},
}


def _render_fno_page(index_key: str):
    cfg = _INDEX_PAGE_CONFIGS[index_key]
    resp = make_response(render_template(
        'dashboard/fno_nifty.html',
        fno_index_id=cfg['index_id'],
        fno_display_name=cfg['display_name'],
        fno_short_name=cfg['short_name'],
        fno_accent=cfg['accent'],
        fno_lot_size=cfg['lot_size'],
        fno_active_tab=index_key,
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ── Page routes ─────────────────────────────────────────────────────────────

@fno_bp.route('/')
@login_required
@paid_plan_required
def fno_landing():
    return _render_fno_page('nifty')


@fno_bp.route('/nifty')
@login_required
@paid_plan_required
def fno_nifty():
    return _render_fno_page('nifty')


@fno_bp.route('/banknifty')
@login_required
@paid_plan_required
def fno_banknifty():
    return _render_fno_page('banknifty')


@fno_bp.route('/finnifty')
@login_required
@paid_plan_required
def fno_finnifty():
    return _render_fno_page('finnifty')


@fno_bp.route('/sensex')
@login_required
@paid_plan_required
def fno_sensex():
    return _render_fno_page('sensex')


# ── Analysis API — per-index ─────────────────────────────────────────────────

@fno_bp.route('/api/analysis')
@login_required
def fno_analysis_api():
    """Default (NIFTY) — kept for backward compatibility."""
    return _analysis_for_index('NIFTY')


@fno_bp.route('/api/analysis/<string:index_id>')
@login_required
def fno_analysis_api_index(index_id: str):
    """Generic analysis endpoint for any supported index."""
    return _analysis_for_index(index_id.upper())


def _analysis_for_index(index_id: str):
    try:
        from services.nifty_options_engine import NiftyOptionsEngine, INDEX_CONFIGS
        if index_id not in INDEX_CONFIGS:
            return jsonify({'success': False, 'error': f"Unknown index '{index_id}'"}), 400
        engine = NiftyOptionsEngine(user_id=current_user.id, index=index_id)
        analysis = engine.generate_analysis()
        return jsonify({'success': True, 'data': analysis})
    except Exception as e:
        logger.error(f"F&O analysis error ({index_id}): {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Shared utility APIs ──────────────────────────────────────────────────────

@fno_bp.route('/api/indices')
@login_required
def fno_indices_api():
    try:
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine()
        indices = engine.get_market_indices()
        return jsonify({'success': True, 'data': indices})
    except Exception as e:
        logger.error(f"Indices fetch error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@fno_bp.route('/api/monitor-status')
@login_required
def fno_monitor_status():
    try:
        from services.fno_monitor import get_monitor_status
        status = get_monitor_status()
        return jsonify({'success': True, 'data': status})
    except Exception as e:
        logger.error(f"Monitor status error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@fno_bp.route('/api/signal-history')
@login_required
def fno_signal_history():
    try:
        from app import db
        from datetime import datetime, timedelta
        limit = request.args.get('limit', 20, type=int)
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        ist_today_start = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_today_start = ist_today_start - timedelta(hours=5, minutes=30)
        rows = db.session.execute(db.text("""
            SELECT h.id, h.signal_type, h.direction, h.confidence, h.confidence_grade,
                   h.entry_mode, h.spot_price, h.atm_strike, h.alert_sent, h.data_source,
                   h.created_at,
                   COALESCE(d.display_name, h.data_source) AS source_display_name
            FROM fno_signal_history h
            LEFT JOIN data_source_config d ON d.source_key = h.data_source
            WHERE h.created_at >= :today_start
              AND h.entry_mode != 'NO TRADE'
              AND h.confidence >= 60
            ORDER BY h.created_at DESC
            LIMIT :limit
        """), {'today_start': utc_today_start, 'limit': min(limit, 50)}).fetchall()

        signals = []
        for r in rows:
            signals.append({
                'id': r.id,
                'signal_type': r.signal_type,
                'direction': r.direction,
                'confidence': r.confidence,
                'confidence_grade': r.confidence_grade,
                'entry_mode': r.entry_mode,
                'spot_price': r.spot_price,
                'atm_strike': r.atm_strike,
                'alert_sent': r.alert_sent,
                'data_source': r.data_source,
                'source_display_name': r.source_display_name,
                'created_at': r.created_at.replace(tzinfo=timezone.utc).astimezone(IST).strftime('%d/%m %I:%M %p') if r.created_at else '',
            })
        return jsonify({'success': True, 'data': signals})
    except Exception as e:
        logger.error(f"Signal history error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@fno_bp.route('/api/active-trade')
@login_required
def fno_active_trade():
    try:
        from services.fno_monitor import get_monitor_status
        status = get_monitor_status()
        return jsonify({
            'success': True,
            'trade_state': status.get('trade_state', 'NONE'),
            'active_trade': status.get('active_trade'),
            'confirmation_count': status.get('confirmation_count', 0),
            'confirmation_needed': status.get('confirmation_needed', 2),
            'cooldown_remaining_min': status.get('cooldown_remaining_min', 0),
        })
    except Exception as e:
        logger.error(f"Active trade status error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
