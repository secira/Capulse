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
        from flask_login import current_user
        uid = getattr(current_user, 'id', None) if current_user and current_user.is_authenticated else None
        engine = NiftyOptionsEngine(user_id=uid)
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
        index_id = request.args.get('index_id', 'NIFTY').upper()
        status = get_monitor_status(index_id=index_id)
        return jsonify({'success': True, 'data': status})
    except Exception as e:
        logger.error(f"Monitor status error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _parse_atm_trade(trades_json_str):
    """Extract the ATM option trade dict from a stored trades_json string."""
    if not trades_json_str:
        return None
    try:
        import ast
        trades = ast.literal_eval(trades_json_str)
        if isinstance(trades, list) and trades:
            for t in trades:
                if isinstance(t, dict) and str(t.get('option_type', '')).upper() == 'ATM':
                    return t
            return trades[0] if isinstance(trades[0], dict) else None
    except Exception:
        pass
    return None


def _calc_trade_pnl(atm_trade, outcome):
    """
    Return (entry_premium, exit_premium, pnl_pct) for a closed trade.
    - TARGET HIT → exit at the ATM target price
    - SL HIT     → exit at the ATM SL price
    - TIME EXIT  → exit price unknown; returns None for pnl_pct
    """
    if not atm_trade:
        return None, None, None
    entry_premium = atm_trade.get('entry_price') or 0
    sl            = atm_trade.get('sl')           or 0
    target        = atm_trade.get('target')       or 0
    if not entry_premium:
        return None, None, None

    if outcome == 'TARGET HIT' and target:
        exit_premium = target
    elif outcome == 'SL HIT' and sl:
        exit_premium = sl
    else:
        return round(entry_premium, 1), None, None   # TIME EXIT — no exact exit price

    pnl_pct = round((exit_premium - entry_premium) / entry_premium * 100, 1)
    return round(entry_premium, 1), round(exit_premium, 1), pnl_pct


@fno_bp.route('/api/signal-history')
@login_required
def fno_signal_history():
    try:
        from app import db
        from datetime import datetime, timedelta
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        ist_today_start = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_today_start = ist_today_start - timedelta(hours=5, minutes=30)
        index_id = request.args.get('index_id', 'NIFTY').upper()

        # Fetch TRIGGER + EXIT rows; for EXIT rows, join the matching TRIGGER to
        # obtain its trades_json (which holds the original ATM entry/sl/target prices).
        rows = db.session.execute(db.text("""
            SELECT h.id, h.signal_type, h.direction, h.confidence, h.confidence_grade,
                   h.entry_mode, h.spot_price, h.atm_strike, h.alert_sent, h.data_source,
                   h.created_at, h.trade_code, h.outcome, h.exit_spot, h.exit_time,
                   COALESCE(d.display_name, h.data_source) AS source_display_name,
                   CASE WHEN h.signal_type = 'TRADE_TRIGGER' THEN h.trades_json
                        ELSE trig.trades_json END AS atm_trades_json
            FROM fno_signal_history h
            LEFT JOIN data_source_config d ON d.source_key = h.data_source
            LEFT JOIN fno_signal_history trig
                   ON h.signal_type = 'TRADE_EXIT'
                  AND trig.trade_code = h.trade_code
                  AND trig.signal_type = 'TRADE_TRIGGER'
            WHERE h.created_at >= :today_start
              AND h.signal_type IN ('TRADE_TRIGGER', 'TRADE_EXIT')
              AND COALESCE(h.index_id, 'NIFTY') = :index_id
            ORDER BY h.created_at ASC
            LIMIT 6
        """), {'today_start': utc_today_start, 'index_id': index_id}).fetchall()

        signals = []
        for r in rows:
            atm = _parse_atm_trade(getattr(r, 'atm_trades_json', None))
            outcome = r.outcome or ''
            entry_premium, exit_premium, pnl_pct = _calc_trade_pnl(atm, outcome)

            signals.append({
                'id':                  r.id,
                'signal_type':         r.signal_type,
                'direction':           r.direction,
                'confidence':          r.confidence,
                'confidence_grade':    r.confidence_grade,
                'entry_mode':          r.entry_mode,
                'spot_price':          r.spot_price,
                'atm_strike':          r.atm_strike,
                'alert_sent':          r.alert_sent,
                'data_source':         r.data_source,
                'source_display_name': r.source_display_name,
                'trade_code':          r.trade_code or '',
                'outcome':             outcome,
                'exit_spot':           r.exit_spot,
                'exit_time':           r.exit_time.replace(tzinfo=timezone.utc).astimezone(IST).strftime('%I:%M %p') if r.exit_time else '',
                'created_at':          r.created_at.replace(tzinfo=timezone.utc).astimezone(IST).strftime('%I:%M %p') if r.created_at else '',
                # P&L fields (ATM option)
                'entry_premium':       entry_premium,
                'exit_premium':        exit_premium,
                'pnl_pct':             pnl_pct,
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
        index_id = request.args.get('index_id', 'NIFTY').upper()
        status = get_monitor_status(index_id=index_id)
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
