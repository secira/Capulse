"""
F&O Analysis Routes — NIFTY Options Engine
"""

from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user
from decorators import paid_plan_required
from datetime import timezone
import logging
import pytz

IST = pytz.timezone('Asia/Kolkata')

logger = logging.getLogger(__name__)

fno_bp = Blueprint('fno', __name__, url_prefix='/dashboard/fno')


@fno_bp.route('/')
@login_required
@paid_plan_required
def fno_landing():
    return render_template('dashboard/fno_nifty.html')


@fno_bp.route('/nifty')
@login_required
@paid_plan_required
def fno_nifty():
    from flask import make_response
    resp = make_response(render_template('dashboard/fno_nifty.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@fno_bp.route('/api/analysis')
@login_required
def fno_analysis_api():
    try:
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine(user_id=current_user.id)
        analysis = engine.generate_analysis()

        return jsonify({'success': True, 'data': analysis})
    except Exception as e:
        logger.error(f"F&O analysis error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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
        limit = request.args.get('limit', 20, type=int)
        from datetime import datetime, timedelta
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        ist_today_start = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_today_start = ist_today_start - timedelta(hours=5, minutes=30)
        # Only show actual trade recommendations — no NO TRADE scans
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
