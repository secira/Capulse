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
        # Hide raw data-source plumbing from every user (admin or not).
        # Estimated/no-broker is an operational concern reported via Telegram
        # admin alerts and the Admin → Data API Plan page — never on the
        # user-facing F&O banner.
        if isinstance(analysis, dict):
            analysis = dict(analysis)
            analysis['data_source'] = 'live'
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


def _parse_all_trades(trades_json_str):
    """
    Parse and return all trade dicts (ATM/OTM/ITM) from a stored trades_json
    string.  Returns a list with at most 3 items in moneyness order:
    Recommended (ATM) → Aggressive (OTM) → Conservative (ITM).
    """
    if not trades_json_str:
        return []
    try:
        import ast
        trades = ast.literal_eval(trades_json_str)
        if not isinstance(trades, list):
            return []
        valid = [t for t in trades if isinstance(t, dict)]
        order = {'ATM': 0, 'OTM': 1, 'ITM': 2}
        valid.sort(key=lambda t: order.get(str(t.get('moneyness', '')).upper(), 9))
        return [
            {
                'symbol':       t.get('symbol', ''),
                'moneyness':    t.get('moneyness', ''),
                'type':         t.get('type', ''),
                'label':        t.get('label', ''),
                'ltp':          t.get('ltp', 0),
                'entry_price':  t.get('entry_price', t.get('ltp', 0)),
                'target':       t.get('target', 0),
                'target_2':     t.get('target_2', 0),
                'target_3':     t.get('target_3', 0),
                'sl':           t.get('sl', 0),
                'target_points':    t.get('target_points', 0),
                'target_2_points':  t.get('target_2_points', 0),
                'target_3_points':  t.get('target_3_points', 0),
                'sl_points':        t.get('sl_points', 0),
                'risk_reward':      t.get('risk_reward', ''),
                'suggested_for':    t.get('suggested_for', ''),
            }
            for t in valid[:3]
        ]
    except Exception:
        pass
    return []


def _calc_trade_pnl(atm_trade, outcome, exit_spot=None):
    """
    Return (entry_premium, exit_premium, pnl_pct) for a closed trade.
    - TARGET 1/2/3 HIT  → exit at the respective target price
    - SL HIT            → exit at the SL price
    - 3PM SQUARE OFF    → exit at the captured option LTP (exit_spot from DB)
                          if available; otherwise returns None for pnl_pct
    """
    if not atm_trade:
        return None, None, None
    entry_premium = atm_trade.get('entry_price') or 0
    sl      = atm_trade.get('sl')       or 0
    target  = atm_trade.get('target')   or 0
    target2 = atm_trade.get('target_2') or 0
    target3 = atm_trade.get('target_3') or 0
    if not entry_premium:
        return None, None, None

    if outcome in ('TARGET HIT', 'TARGET 1 HIT') and target:
        exit_premium = target
    elif outcome == 'TARGET 2 HIT' and target2:
        exit_premium = target2
    elif outcome == 'TARGET 3 HIT' and target3:
        exit_premium = target3
    elif outcome == 'SL HIT' and sl:
        exit_premium = sl
    elif ('3PM' in outcome or 'SQUARE' in outcome) and exit_spot and float(exit_spot) > 0:
        # exit_spot now stores the ATM option LTP at the time of 3PM square-off
        exit_premium = float(exit_spot)
    else:
        return round(entry_premium, 1), None, None   # unknown / no price data

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
            raw_trades_json = getattr(r, 'atm_trades_json', None)
            atm = _parse_atm_trade(raw_trades_json)
            all_trades = _parse_all_trades(raw_trades_json) if r.signal_type == 'TRADE_TRIGGER' else []
            outcome = r.outcome or ''
            entry_premium, exit_premium, pnl_pct = _calc_trade_pnl(atm, outcome, exit_spot=r.exit_spot)

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
                # All 3 trade options (ATM/OTM/ITM) for TRADE_TRIGGER rows
                'all_trades':          all_trades,
            })
        return jsonify({'success': True, 'data': signals})
    except Exception as e:
        logger.error(f"Signal history error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@fno_bp.route('/premarket')
@login_required
@paid_plan_required
def fno_premarket():
    from services.premarket_report import build_premarket_report
    try:
        report = build_premarket_report()
    except Exception as e:
        logger.error(f"premarket page build failed: {e}", exc_info=True)
        report = {'generated_at_ist': '', 'indices': []}
    resp = make_response(render_template(
        'dashboard/fno_premarket.html',
        fno_active_tab='premarket',
        report=report,
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@fno_bp.route('/pnl-analysis')
@login_required
@paid_plan_required
def fno_pnl_analysis():
    resp = make_response(render_template(
        'dashboard/fno_pnl_analysis.html',
        fno_active_tab='pnl',
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@fno_bp.route('/api/pnl-history')
@login_required
def fno_pnl_history():
    try:
        from app import db
        from datetime import datetime, timedelta
        from collections import OrderedDict

        period       = request.args.get('period', 'months')   # 'week' | 'month' | 'months'
        months_back  = min(int(request.args.get('months', 6)), 24)
        index_filter = request.args.get('index_id', 'ALL').upper()

        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)

        week_start_ist = week_end_ist = None
        if period == 'week':
            days_since_mon = ist_now.weekday()          # 0 = Monday
            week_start_ist = (ist_now - timedelta(days=days_since_mon)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            week_end_ist   = week_start_ist + timedelta(days=6)
            utc_from       = week_start_ist - timedelta(hours=5, minutes=30)
        elif period == 'month':
            month_start_ist = ist_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            utc_from        = month_start_ist - timedelta(hours=5, minutes=30)
        else:
            m        = ist_now.month - months_back
            y        = ist_now.year + (m - 1) // 12
            m        = ((m - 1) % 12) + 1
            from_ist = ist_now.replace(year=y, month=m, day=1,
                                       hour=0, minute=0, second=0, microsecond=0)
            utc_from = from_ist - timedelta(hours=5, minutes=30)

        index_clause = "AND COALESCE(index_id,'NIFTY') = :index_id" if index_filter != 'ALL' else ""
        params: dict = {'from_date': utc_from}
        if index_filter != 'ALL':
            params['index_id'] = index_filter

        rows = db.session.execute(db.text(f"""
            SELECT index_id, direction, confidence, atm_strike, trades_json,
                   created_at, trade_code, outcome, exit_spot, exit_time
            FROM   fno_signal_history
            WHERE  signal_type = 'TRADE_TRIGGER'
              AND  created_at >= :from_date
              {index_clause}
            ORDER  BY created_at DESC
        """), params).fetchall()

        trades = []
        for r in rows:
            atm     = _parse_atm_trade(getattr(r, 'trades_json', None))
            outcome = r.outcome if r.outcome else ('ACTIVE' if r.exit_time is None else '')
            entry_p, exit_p, pnl_pct = _calc_trade_pnl(atm, outcome, exit_spot=r.exit_spot)
            points   = round(exit_p - entry_p, 1) if (exit_p is not None and entry_p) else None
            opt_type = 'CE' if r.direction == 'BULLISH' else ('PE' if r.direction == 'BEARISH' else '—')
            entry_ist = r.created_at.replace(tzinfo=timezone.utc).astimezone(IST) if r.created_at else None
            exit_ist  = r.exit_time.replace(tzinfo=timezone.utc).astimezone(IST)  if r.exit_time  else None

            # Week-in-month label (1-indexed by calendar week number within month)
            week_of_month      = ((entry_ist.day - 1) // 7 + 1) if entry_ist else 0
            month_key          = entry_ist.strftime('%Y-%m')  if entry_ist else '0000-00'
            month_label        = entry_ist.strftime('%B %Y')  if entry_ist else 'Unknown'
            week_in_month_key  = f"{month_key}-W{week_of_month}"
            week_in_month_lbl  = f"Week {week_of_month} — {month_label}"

            trades.append({
                'trade_code':         r.trade_code or '—',
                'index_id':           (r.index_id or 'NIFTY'),
                'direction':          r.direction or '—',
                'option_type':        opt_type,
                'strike':             r.atm_strike,
                'confidence':         r.confidence,
                'outcome':            outcome,
                'entry_premium':      entry_p,
                'exit_premium':       exit_p,
                'points':             points,
                'pnl_pct':            pnl_pct,
                'exit_spot':          r.exit_spot,
                'date':               entry_ist.strftime('%d %b %Y') if entry_ist else '—',
                'entry_time':         entry_ist.strftime('%I:%M %p')  if entry_ist else '—',
                'exit_time':          exit_ist.strftime('%I:%M %p')   if exit_ist  else '—',
                'month_key':          month_key,
                'month_label':        month_label,
                'week_in_month_key':  week_in_month_key,
                'week_in_month_lbl':  week_in_month_lbl,
            })

        _TIME_EXIT_OUTCOMES = {'3PM SQUARE OFF', 'TIME EXIT', 'MARKET CLOSED', 'MANUAL CLOSE'}

        def _summarise(tlist):
            active   = [t for t in tlist if t['outcome'] == 'ACTIVE']
            closed   = [t for t in tlist if t['outcome'] != 'ACTIVE']
            sl_hits  = [t for t in closed if t['outcome'] == 'SL HIT']
            tgt_hits = [t for t in closed if t['outcome'] and t['outcome'].startswith('TARGET')]
            t_exits  = [t for t in closed if t['outcome'] in _TIME_EXIT_OUTCOMES]
            with_pnl = [t for t in closed if t['pnl_pct'] is not None]
            wins     = [t for t in with_pnl if t['pnl_pct'] > 0]
            losses   = [t for t in with_pnl if t['pnl_pct'] <= 0]
            tot_pts  = round(sum(t['points'] for t in with_pnl if t['points']), 1)
            cum_pnl  = round(sum(t['pnl_pct'] for t in with_pnl), 1) if with_pnl else None
            return {
                'total_trades': len(closed),
                'active_count': len(active),
                'wins':         len(wins),
                'losses':       len(losses),
                'sl_hits':      len(sl_hits),
                'target_hits':  len(tgt_hits),
                'time_exits':   len(t_exits),
                'square_offs':  len(closed) - len(with_pnl),
                'win_rate':     round(len(wins) / len(with_pnl) * 100, 1) if with_pnl else None,
                'total_points': tot_pts,
                'cum_pnl_pct':  cum_pnl,
            }

        groups = []
        if period == 'week':
            mon_lbl = week_start_ist.strftime('%d %b')
            sun_lbl = week_end_ist.strftime('%d %b %Y')
            groups  = [{'month_label': f'This Week  ({mon_lbl} – {sun_lbl})',
                        'month_key': 'this_week',
                        'trades': trades,
                        'summary': _summarise(trades)}]
        elif period == 'month':
            by_week: OrderedDict = OrderedDict()
            for t in trades:
                k = t['week_in_month_key']
                if k not in by_week:
                    by_week[k] = {'month_label': t['week_in_month_lbl'],
                                  'month_key': k, 'trades': []}
                by_week[k]['trades'].append(t)
            for k, data in by_week.items():
                data['summary'] = _summarise(data['trades'])
                groups.append(data)
        else:
            by_month: OrderedDict = OrderedDict()
            for t in trades:
                mk = t['month_key']
                if mk not in by_month:
                    by_month[mk] = {'month_label': t['month_label'],
                                    'month_key': mk, 'trades': []}
                by_month[mk]['trades'].append(t)
            for mk, data in by_month.items():
                data['summary'] = _summarise(data['trades'])
                groups.append(data)

        total_closed = sum(1 for t in trades if t['outcome'] != 'ACTIVE')
        total_active = len(trades) - total_closed
        return jsonify({
            'success':       True,
            'months':        groups,
            'total_trades':  len(trades),
            'total_closed':  total_closed,
            'total_active':  total_active,
        })
    except Exception as e:
        logger.error(f"P&L history error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@fno_bp.route('/api/correct-trade-outcome', methods=['POST'])
@login_required
def fno_correct_trade_outcome():
    """
    Admin-only endpoint to manually correct a trade's outcome and exit price.
    Useful for fixing records where the automated exit reason was wrong
    (e.g. 'SL HIT' misclassified as '3PM SQUARE OFF' due to symbol tracking failure).

    POST body (JSON):
        trade_code   - required, e.g. "NIFTYT01"
        outcome      - required, e.g. "SL HIT", "TARGET HIT", "3PM SQUARE OFF"
        exit_spot    - optional float, actual exit premium (₹)
        exit_time    - optional string "HH:MM" in IST (defaults to current time)
    """
    from flask_login import current_user
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Admin access required'}), 403

    data       = request.get_json(force=True) or {}
    trade_code = (data.get('trade_code') or '').strip().upper()
    outcome    = (data.get('outcome') or '').strip()
    exit_spot  = data.get('exit_spot')
    exit_time_str = (data.get('exit_time') or '').strip()

    if not trade_code or not outcome:
        return jsonify({'success': False, 'error': 'trade_code and outcome are required'}), 400

    VALID_OUTCOMES = {'SL HIT', 'TARGET HIT', 'TARGET 2 HIT', 'TARGET 3 HIT',
                      '3PM SQUARE OFF', 'MARKET CLOSED', 'MANUAL CLOSE'}
    if outcome.upper() not in VALID_OUTCOMES:
        return jsonify({'success': False, 'error': f'Invalid outcome. Valid values: {sorted(VALID_OUTCOMES)}'}), 400

    try:
        from app import db
        from datetime import datetime, timedelta

        # Determine exit timestamp
        if exit_time_str:
            ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
            try:
                hh, mm = exit_time_str.split(':')
                exit_dt_ist = ist_now.replace(hour=int(hh), minute=int(mm),
                                              second=0, microsecond=0)
                exit_dt_utc = exit_dt_ist - timedelta(hours=5, minutes=30)
            except Exception:
                return jsonify({'success': False, 'error': 'exit_time must be HH:MM format'}), 400
        else:
            exit_dt_utc = None

        # Build update parts
        update_parts = ['outcome = :outcome']
        params = {'outcome': outcome.upper(), 'trade_code': trade_code}

        if exit_spot is not None:
            update_parts.append('exit_spot = :exit_spot')
            params['exit_spot'] = float(exit_spot)

        if exit_dt_utc:
            update_parts.append('exit_time = :exit_time')
            params['exit_time'] = exit_dt_utc

        sql = db.text(
            f"UPDATE fno_signal_history SET {', '.join(update_parts)} "
            f"WHERE trade_code = :trade_code AND signal_type = 'TRADE_EXIT'"
        )
        result = db.session.execute(sql, params)
        db.session.commit()

        if result.rowcount == 0:
            return jsonify({'success': False,
                            'error': f'No TRADE_EXIT row found for trade_code={trade_code}. '
                                     'The trade may still be active or the code is wrong.'}), 404

        logger.info(
            f"Admin correction: trade_code={trade_code}, outcome={outcome}, "
            f"exit_spot={exit_spot}, exit_time={exit_time_str} "
            f"(by user {current_user.id})"
        )
        return jsonify({
            'success': True,
            'message': f'Trade {trade_code} updated: outcome={outcome}',
            'rows_updated': result.rowcount,
        })

    except Exception as e:
        logger.error(f"Trade outcome correction error: {e}")
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
