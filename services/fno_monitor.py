"""
F&O Continuous Monitor — Multi-Index
Scans NIFTY, BANKNIFTY, FINNIFTY, SENSEX every 60 seconds during market hours.
Each index has independent trade-lifecycle state, alert state, and signal history.
"""
import os
import logging
import requests
from datetime import datetime, timedelta
from threading import Lock

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

# Telegram alerts only for Tier 1 (high-conviction) signals.
# Tier 2 (60-74) and Tier 3 (50-59) are surfaced in the app/UI only.
ALERT_CONFIDENCE_THRESHOLD = 75
SIGNAL_COOLDOWN_MINUTES    = 10
MAX_SIGNALS_PER_DAY        = 3          # per index

# Trade Lifecycle constants (shared across indices)
# CONFIRMATION_CANDLES = 2: require two consecutive strong 60-second scans
# before locking a trade. Adds ~60s entry delay but significantly reduces
# false triggers in choppy/reversing markets (high SL-hit rate cause).
CONFIRMATION_CANDLES    = 2
ENTRY_COOLDOWN_MINUTES  = 15
TRADE_MIN_CONFIDENCE    = 80
TRADE_MIN_ADX           = 25
EOD_FORCE_EXIT_HOUR     = 15      # 3:00 PM IST — hard-close every open signal so
EOD_FORCE_EXIT_MINUTE   = 0       # daily P&L is deterministic at end-of-day.
ACTIVE_UPDATE_INTERVAL_MIN = 5   # How often to send Telegram updates while trade is live

# All indices to scan (order determines scan priority)
SCAN_INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX']

_INDEX_DISPLAY = {
    'NIFTY':     'NIFTY 50',
    'BANKNIFTY': 'Bank Nifty',
    'FINNIFTY':  'Fin Nifty',
    'SENSEX':    'SENSEX',
}

_INDEX_PAGE_PATH = {
    'NIFTY':     'nifty',
    'BANKNIFTY': 'banknifty',
    'FINNIFTY':  'finnifty',
    'SENSEX':    'sensex',
}

# Short prefix used to build trade codes: NIFTYT01, BNKTT01, FINTT01, SNXTT01
_INDEX_TRADE_PREFIX = {
    'NIFTY':     'NIFTY',
    'BANKNIFTY': 'BNKT',
    'FINNIFTY':  'FINT',
    'SENSEX':    'SNXT',
}

# ── Per-index mutable state ───────────────────────────────────────────────────
# Each dict maps index_id → value.
def _make_per_index(default):
    return {idx: default for idx in SCAN_INDICES}

_state_lock = Lock()

# Alert state (per index)
_last_signal_time      = _make_per_index(None)
_last_signal_direction = _make_per_index(None)
_daily_signal_count    = _make_per_index(0)
_daily_date            = _make_per_index(None)

# Trade lifecycle state (per index)
_trade_state          = _make_per_index('NONE')   # NONE | CONFIRMING | ACTIVE
_confirmation_count   = _make_per_index(0)
_confirmation_dir     = _make_per_index(None)
_active_trade         = _make_per_index(None)
_last_exit_time       = _make_per_index(None)
_recent_confidences   = _make_per_index([])
_last_active_alert    = _make_per_index(None)   # Timestamp of last TRADE_ACTIVE Telegram update
_active_trade_code    = _make_per_index(None)   # Current trade code (e.g. NIFTYT01)

# ── Per-index circuit breaker ──────────────────────────────────────────────────
# After 2 consecutive SL hits on the SAME index, suppress new entries on THAT
# index only for the rest of the trading session.  Other indices keep trading
# independently — a BANKNIFTY bad-streak must not block a FINNIFTY opportunity.
_consecutive_losses   = _make_per_index(0)     # SL-hit streak per index
_circuit_breaker_on   = _make_per_index(False) # per-index breaker flag
_circuit_breaker_date = _make_per_index(None)  # date breaker was set (auto-resets next day)

_scheduler_started = False
_fno_app = None          # set by start_scheduler; used inside alert helpers that lack app context


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_ist():
    return datetime.utcnow() + IST_OFFSET


def _is_market_hours():
    now = _now_ist()
    if now.weekday() >= 5:
        return False
    # Skip on NSE/BSE trading holidays — broadcast holiday wish instead.
    try:
        from services.market_calendar import is_market_holiday, send_holiday_wish_once
        if is_market_holiday(now.date()):
            send_holiday_wish_once()
            return False
    except Exception:
        pass
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    # Extended to 3:05 PM so the 3:00 PM EOD force-exit always gets at least
    # one scan window to fire (APScheduler can miss the exact :00 second).
    close_ = now.replace(hour=15, minute=5,  second=0, microsecond=0)
    return open_ <= now <= close_


def _alert_state_key(idx: str) -> str:
    return f"fno_alert_state:{idx}"


def _persist_alert_state(idx: str) -> None:
    """Mirror per-index alert dedup state to Redis so it survives restarts
    (and stays consistent if we ever scale beyond a single scheduler worker)."""
    try:
        from services import state_store
        last_ts = _last_signal_time[idx]
        date_   = _daily_date[idx]
        state_store.set(_alert_state_key(idx), {
            'last_signal_time':      last_ts.isoformat() if last_ts else None,
            'last_signal_direction': _last_signal_direction[idx],
            'daily_signal_count':    _daily_signal_count[idx],
            'daily_date':            date_.isoformat() if date_ else None,
        }, ttl=86400 * 2)  # 2-day TTL — covers weekends + restarts
    except Exception as e:
        logger.debug(f"[{idx}] persist_alert_state skipped: {e}")


def _restore_alert_state() -> None:
    """Load alert dedup state for every index from Redis on startup."""
    try:
        from services import state_store
        from datetime import datetime as _dt, date as _date
    except Exception:
        return
    for idx in SCAN_INDICES:
        data = state_store.get(_alert_state_key(idx))
        if not data or not isinstance(data, dict):
            continue
        try:
            ts = data.get('last_signal_time')
            _last_signal_time[idx]      = _dt.fromisoformat(ts) if ts else None
            _last_signal_direction[idx] = data.get('last_signal_direction')
            _daily_signal_count[idx]    = int(data.get('daily_signal_count') or 0)
            d = data.get('daily_date')
            _daily_date[idx]            = _date.fromisoformat(d) if d else None
        except Exception as e:
            logger.warning(f"[{idx}] failed to restore alert state: {e}")


def _restore_active_trade_state(app) -> None:
    """
    On startup, re-hydrate in-memory trade lifecycle state from the DB.
    Prevents a restart from clearing an in-progress trade and re-sending
    a TRADE_TRIGGER Telegram alert for the same signal.
    """
    try:
        import ast
        from app import db
        with app.app_context():
            now_ist     = datetime.utcnow() + IST_OFFSET
            day_start_utc = (now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
                             - IST_OFFSET)
            rows = db.session.execute(db.text("""
                SELECT index_id, direction, confidence, entry_mode,
                       spot_price, atm_strike, trades_json, data_source,
                       trade_code, created_at
                FROM   fno_signal_history
                WHERE  signal_type = 'TRADE_TRIGGER'
                  AND  outcome     IS NULL
                  AND  created_at >= :day_start
                ORDER  BY created_at DESC
            """), {'day_start': day_start_utc}).fetchall()

        for r in rows:
            idx = (r.index_id or 'NIFTY').upper()
            if idx not in SCAN_INDICES:
                continue
            if _trade_state[idx] == 'ACTIVE':
                continue  # already restored (earlier row for same index)

            # Parse ATM trade from trades_json
            atm_trade = {}
            try:
                trades_list = ast.literal_eval(r.trades_json or '[]')
                if isinstance(trades_list, list):
                    atm_trade = next(
                        (t for t in trades_list
                         if isinstance(t, dict) and str(t.get('moneyness', '')).upper() == 'ATM'),
                        trades_list[0] if trades_list else {}
                    )
            except Exception:
                pass

            entry_time = r.created_at + IST_OFFSET if r.created_at else datetime.utcnow() + IST_OFFSET
            _active_trade[idx] = {
                'direction':   r.direction or 'NEUTRAL',
                'entry_time':  entry_time,
                'entry_mode':  r.entry_mode or 'EARLY',
                'confidence':  r.confidence or 0,
                'spot':        r.spot_price or 0,
                'atm_strike':  r.atm_strike or 0,
                'atm_key':     atm_trade.get('symbol', ''),
                'entry_price': atm_trade.get('entry_price', atm_trade.get('ltp', 0)),
                'sl':          atm_trade.get('sl', 0),
                'target':      atm_trade.get('target', 0),
                'target_2':    atm_trade.get('target_2', 0),
                'target_3':    atm_trade.get('target_3', 0),
                'type':        atm_trade.get('type', ''),
                'data_source': r.data_source or '',
                'index_id':    idx,
                'trade_code':  r.trade_code or '',
            }
            _active_trade_code[idx] = r.trade_code
            _trade_state[idx]       = 'ACTIVE'
            logger.info(
                f"[{idx}] ♻️  Restored active trade from DB: "
                f"{r.direction} {r.trade_code} @ entry={atm_trade.get('entry_price', 0):.0f}"
            )
    except Exception as e:
        logger.warning(f"_restore_active_trade_state failed: {e}")


def _close_stale_open_trades(app) -> None:
    """
    On startup, auto-close any TRADE_TRIGGER rows that have no outcome:
    - Past IST days → app was restarted during the session, 3PM exit never fired.
    - Today, after 3PM IST → same issue but same day.
    All are marked '3PM SQUARE OFF' so they appear correctly in P&L Analysis.
    """
    try:
        from app import db
        with app.app_context():
            now_ist        = datetime.utcnow() + IST_OFFSET
            today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = today_start_ist - IST_OFFSET
            eod_ist         = today_start_ist.replace(
                hour=EOD_FORCE_EXIT_HOUR, minute=EOD_FORCE_EXIT_MINUTE)

            # 1. Past days — any trigger with no outcome before today
            closed_past = db.session.execute(db.text("""
                UPDATE fno_signal_history
                   SET outcome   = '3PM SQUARE OFF',
                       exit_time = NOW()
                 WHERE signal_type = 'TRADE_TRIGGER'
                   AND outcome     IS NULL
                   AND created_at  < :today_start
                RETURNING id, index_id, trade_code
            """), {'today_start': today_start_utc}).fetchall()
            for r in closed_past:
                logger.info(
                    f"[{r[1]}] ⏱ Stale trade auto-closed (past day): {r[2]} → 3PM SQUARE OFF"
                )

            # 2. Today, if market is already past 3PM IST
            closed_today = []
            if now_ist >= eod_ist:
                closed_today = db.session.execute(db.text("""
                    UPDATE fno_signal_history
                       SET outcome   = '3PM SQUARE OFF',
                           exit_time = NOW()
                     WHERE signal_type = 'TRADE_TRIGGER'
                       AND outcome     IS NULL
                       AND created_at >= :today_start
                    RETURNING id, index_id, trade_code
                """), {'today_start': today_start_utc}).fetchall()
                for r in closed_today:
                    logger.info(
                        f"[{r[1]}] ⏱ Stale trade auto-closed (after 3PM today): {r[2]} → 3PM SQUARE OFF"
                    )

            db.session.commit()

            # Backfill exit_spot for stale-closed trades so P&L Analysis shows
            # the entry premium (as a 0-P&L breakeven proxy) rather than "—".
            all_closed_ids = [r[0] for r in closed_past] + [r[0] for r in closed_today]
            if all_closed_ids:
                rows_to_fix = db.session.execute(db.text(
                    "SELECT id, trades_json FROM fno_signal_history "
                    "WHERE id = ANY(:ids) AND exit_spot IS NULL"
                ), {'ids': all_closed_ids}).fetchall()
                import ast as _ast
                for fix_row in rows_to_fix:
                    try:
                        trades_list = _ast.literal_eval(fix_row[1] or '[]')
                        entry_p = 0.0
                        if isinstance(trades_list, list):
                            for t in trades_list:
                                if isinstance(t, dict):
                                    ep = t.get('entry_price') or t.get('ltp') or 0
                                    if float(ep or 0) > 0:
                                        entry_p = float(ep)
                                        break
                        if entry_p > 0:
                            db.session.execute(db.text(
                                "UPDATE fno_signal_history "
                                "   SET exit_spot = :ep "
                                " WHERE id = :row_id AND exit_spot IS NULL"
                            ), {'ep': entry_p, 'row_id': fix_row[0]})
                    except Exception as _bp:
                        logger.debug(f"exit_spot backfill failed for id {fix_row[0]}: {_bp}")
                db.session.commit()
    except Exception as e:
        logger.warning(f"_close_stale_open_trades failed: {e}")


def _reset_daily_if_needed(idx: str):
    today = _now_ist().date()
    if _daily_date[idx] != today:
        _daily_date[idx]         = today
        _daily_signal_count[idx] = 0
        _consecutive_losses[idx] = 0
        _persist_alert_state(idx)
    # Auto-reset THIS index's circuit breaker on a new calendar day
    if _circuit_breaker_on[idx] and _circuit_breaker_date[idx] and _circuit_breaker_date[idx] < today:
        _circuit_breaker_on[idx]   = False
        _circuit_breaker_date[idx] = None
        logger.info(f"[{idx}] Per-index circuit breaker reset for new trading day")


def _smoothed_confidence(idx: str, raw: int) -> int:
    buf = _recent_confidences[idx]
    buf.append(raw)
    if len(buf) > 3:
        _recent_confidences[idx] = buf[-3:]
    return int(sum(_recent_confidences[idx]) / len(_recent_confidences[idx]))


# ── Database ───────────────────────────────────────────────────────────────────

def _generate_trade_code(app, index_id: str) -> str:
    """Return the next sequential trade code for today, e.g. NIFTYT01."""
    from app import db
    prefix = _INDEX_TRADE_PREFIX.get(index_id, index_id[:4])
    try:
        with app.app_context():
            from datetime import datetime, timedelta
            now             = datetime.utcnow()
            ist_now         = now + IST_OFFSET
            ist_today_start = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
            utc_today_start = ist_today_start - IST_OFFSET
            count = db.session.execute(db.text("""
                SELECT COUNT(*) FROM fno_signal_history
                WHERE index_id  = :idx
                  AND signal_type = 'TRADE_TRIGGER'
                  AND created_at >= :today_start
            """), {'idx': index_id, 'today_start': utc_today_start}).scalar() or 0
            return f"{prefix}T{count + 1:02d}"
    except Exception as e:
        logger.error(f"[{index_id}] trade_code generation error: {e}")
        return f"{prefix}T01"


def _exit_reason_to_outcome(exit_reason: str) -> str:
    """Map raw exit_reason string to a clean outcome label."""
    if not exit_reason:
        return 'EXITED'
    r = exit_reason.lower()
    if 'target 3' in r or 't3=' in r:
        return 'TARGET 3 HIT'
    if 'target 2' in r or 't2=' in r:
        return 'TARGET 2 HIT'
    if 'target' in r:
        return 'TARGET 1 HIT'
    if 'stop' in r or 'sl' in r:
        return 'SL HIT'
    if '3pm' in r or 'eod' in r or '3:00 pm' in r or 'square' in r:
        return '3PM SQUARE OFF'
    if 'closed' in r:
        return 'MARKET CLOSED'
    return exit_reason[:50]


def _save_signal_to_db(app, signal_data: dict, index_id: str,
                       trade_code: str = None, outcome: str = None):
    try:
        from app import db
        with app.app_context():
            db.session.execute(db.text("""
                INSERT INTO fno_signal_history
                    (index_id, signal_type, direction, confidence, confidence_grade,
                     entry_mode, spot_price, atm_strike, trades_json, layers_json,
                     alert_sent, data_source, trade_code, outcome, market_regime)
                VALUES
                    (:index_id, :signal_type, :direction, :confidence, :confidence_grade,
                     :entry_mode, :spot_price, :atm_strike, :trades_json, :layers_json,
                     :alert_sent, :data_source, :trade_code, :outcome, :market_regime)
            """), {
                'index_id':        index_id,
                'signal_type':     signal_data.get('signal_type', 'SCAN'),
                'direction':       signal_data.get('trade_direction', 'NEUTRAL'),
                'confidence':      signal_data.get('confidence', 0),
                'confidence_grade':signal_data.get('confidence_grade', 'Weak'),
                'entry_mode':      signal_data.get('entry_mode', 'NO TRADE'),
                'spot_price':      signal_data.get('spot_price', 0),
                'atm_strike':      signal_data.get('atm_strike', 0),
                'trades_json':     str(signal_data.get('trades', [])),
                'layers_json':     str({
                    'time_filter': signal_data.get('time_filter', {}),
                    'direction':   signal_data.get('direction', {}),
                    'strength':    signal_data.get('strength', {}),
                    'oi_analysis': signal_data.get('oi_analysis', {}),
                }),
                'alert_sent':    signal_data.get('alert_sent', False),
                'data_source':   signal_data.get('data_source', 'nse_python'),
                'trade_code':    trade_code,
                'outcome':       outcome,
                'market_regime': signal_data.get('market_regime', 'trending'),
            })

            # On exit: also update the TRIGGER record with the outcome.
            # exit_spot stores the ATM *option* LTP at exit time (not the index
            # spot) so P&L Analysis can calculate real profit/loss for 3PM exits.
            if signal_data.get('signal_type') == 'TRADE_EXIT' and trade_code and outcome:
                # Use captured option LTP only — never fall back to index spot_price
                # because spot_price is the underlying index value (e.g. 23 300 for NIFTY)
                # which would show a wildly wrong exit premium in P&L Analysis.
                _exit_ltp = float(signal_data.get('exit_option_ltp') or 0)
                _exit_s = _exit_ltp if _exit_ltp > 0 else None
                db.session.execute(db.text("""
                    UPDATE fno_signal_history
                       SET outcome   = :outcome,
                           exit_spot = :exit_spot,
                           exit_time = NOW()
                     WHERE trade_code  = :trade_code
                       AND signal_type = 'TRADE_TRIGGER'
                       AND index_id    = :index_id
                """), {
                    'outcome':    outcome,
                    'exit_spot':  _exit_s,
                    'trade_code': trade_code,
                    'index_id':   index_id,
                })

            db.session.commit()
    except Exception as e:
        logger.error(f"[{index_id}] Failed to save signal to DB: {e}")


# ── Telegram ───────────────────────────────────────────────────────────────────

def _dispatch_partner_webhook(signal_data: dict, index_id: str) -> None:
    """
    Fan out an MVLA F&O signal to every active B2B partner subscription on this
    index whose min_confidence threshold is met. Never raises — the F&O scan
    loop must keep running even if a partner endpoint is misbehaving.
    """
    try:
        confidence = float(signal_data.get('confidence') or 0)
        # Mirror Telegram threshold: only push real trade events (TRIGGER/EXIT/ACTIVE)
        sig_type = signal_data.get('signal_type') or ''
        if confidence < 50 or sig_type not in ('TRADE_TRIGGER', 'TRADE_EXIT', 'TRADE_ACTIVE'):
            return

        if confidence >= 75:
            tier = 'HIGH'
        elif confidence >= 60:
            tier = 'REGULAR'
        else:
            tier = 'AGGRESSIVE'

        payload = {
            'event':           sig_type,
            'index':           index_id,
            'score':           confidence,
            'confidence':      confidence,
            'tier':            tier,
            'trade_direction': signal_data.get('trade_direction'),
            'entry_mode':      signal_data.get('entry_mode'),
            'atm_strike':      signal_data.get('atm_strike'),
            'entry_price':     signal_data.get('entry_price'),
            'sl':              signal_data.get('sl'),
            'target':          signal_data.get('target'),
            'trade_code':      signal_data.get('trade_code'),
            'reasons':         signal_data.get('reasons'),
            'data_source':     signal_data.get('data_source'),
        }
        from services.partner_webhook import dispatch_event
        dispatch_event('fno', index_id, payload, score=confidence)
    except Exception as e:
        logger.warning(f"[{index_id}] partner webhook dispatch failed: {e}")


def _build_teaser_message(signal_data: dict, index_id: str) -> str:
    """
    Teaser format — gives direction + conviction grade but NO prices, strikes or
    option symbols. Goal: pique interest and drive users to the site.
    """
    display     = _INDEX_DISPLAY.get(index_id, index_id)
    direction   = signal_data.get('trade_direction', 'NEUTRAL')
    confidence  = signal_data.get('confidence', 0)
    grade       = signal_data.get('confidence_grade', '')
    signal_type = signal_data.get('signal_type', 'TRADE')
    trade_code  = signal_data.get('trade_code', '')
    page_path   = _INDEX_PAGE_PATH.get(index_id, 'nifty')

    dir_label = 'CALL + PUT' if direction == 'BOTH' else direction
    dir_emoji = '🟢' if direction == 'BULLISH' else '🔴' if direction == 'BEARISH' else '🟣' if direction == 'BOTH' else '🟡'

    # Conviction label from confidence score
    if confidence >= 85:
        conviction = 'Very High'
    elif confidence >= 75:
        conviction = 'High'
    elif confidence >= 65:
        conviction = 'Moderate'
    else:
        conviction = 'Developing'

    code_tag = f" · <code>{trade_code}</code>" if trade_code else ''

    if signal_type == 'TRADE_TRIGGER':
        header = f"🔒 <b>{display} — New Trade Signal</b>{code_tag}"
        body = (
            f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
            f"⭐ <b>Conviction:</b> {conviction}\n"
            f"⏰ <i>{_now_ist().strftime('%d %b %Y, %I:%M %p')} IST</i>\n\n"
            f"🔐 <i>Full entry, Stop-Loss &amp; Targets are exclusive to Target Capital members.</i>\n\n"
            f"👉 <a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>View Signal on Target Capital →</a>\n"
            f"🚀 <a href='https://www.targetcapital.ai/pricing'>Start Free Trial — targetcapital.ai</a>"
        )
    elif signal_type == 'TRADE_EXIT':
        outcome     = signal_data.get('outcome', signal_data.get('exit_reason', 'Closed'))
        outcome_map = {
            'TARGET 1 HIT':   ('🎯',  'Target 1 Hit'),
            'TARGET 2 HIT':   ('🎯🎯', 'Target 2 Hit'),
            'TARGET 3 HIT':   ('🏆',  'Target 3 Hit'),
            'SL HIT':         ('🛑',  'Stop-Loss Hit'),
            '3PM SQUARE OFF': ('🕒',  '3PM Square Off'),
            # legacy labels kept for older history records
            'TARGET HIT': ('🎯', 'Target Hit'),
            'EOD CLOSE':  ('🕒', '3PM Square Off'),
        }
        out_emoji, out_label = outcome_map.get(outcome, ('🚪', outcome or 'Trade Closed'))
        header = f"🚪 <b>{display} — Trade Closed</b>{code_tag}"
        body = (
            f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
            f"{out_emoji} <b>Result:</b> {out_label}\n"
            f"⏰ <i>{_now_ist().strftime('%d %b %Y, %I:%M %p')} IST</i>\n\n"
            f"📊 <i>Full trade history &amp; P&amp;L analytics are on our platform.</i>\n\n"
            f"👉 <a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>Track Your Trades →</a>"
        )
    else:
        header = f"📡 <b>{display} — Signal Update</b>"
        body   = (
            f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
            f"⏰ <i>{_now_ist().strftime('%d %b %Y, %I:%M %p')} IST</i>\n\n"
            f"👉 <a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>View on Target Capital →</a>"
        )

    return f"{header}\n\n{body}"


def _build_full_message(signal_data: dict, index_id: str, enabled: set) -> str:
    """
    Full call message — entry + T1/T2/T3 + SL + trailing stop advice.
    Format matches the Target Capital Telegram channel standard.
    """
    display     = _INDEX_DISPLAY.get(index_id, index_id)
    page_path   = _INDEX_PAGE_PATH.get(index_id, 'nifty')
    direction   = signal_data.get('trade_direction', 'NEUTRAL')
    confidence  = signal_data.get('confidence', 0)
    signal_type = signal_data.get('signal_type', 'TRADE')
    trade_code  = signal_data.get('trade_code', '')

    if confidence >= 85:
        conviction = 'Very High'
    elif confidence >= 75:
        conviction = 'High'
    elif confidence >= 65:
        conviction = 'Moderate'
    else:
        conviction = 'Developing'

    dir_emoji = '🟢' if direction == 'BULLISH' else '🔴' if direction == 'BEARISH' else '🟣' if direction == 'BOTH' else '🟡'
    dir_label = 'BULLISH' if direction == 'BULLISH' else 'BEARISH' if direction == 'BEARISH' else 'CALL + PUT' if direction == 'BOTH' else direction
    code_tag  = f" · <code>{trade_code}</code>" if trade_code else ''
    ts        = _now_ist().strftime('%d %b %Y, %I:%M %p')

    if signal_type == 'TRADE_TRIGGER':
        trades    = signal_data.get('trades', [])
        atm_trade = next((t for t in trades if t.get('moneyness') == 'ATM'), trades[0] if trades else None)

        if atm_trade:
            t_emoji  = '📗' if atm_trade.get('type') == 'CE' else '📕'
            symbol   = atm_trade.get('symbol', '')
            entry_px = atm_trade.get('entry_price', 0)
            t1_px    = atm_trade.get('target',   0)
            t2_px    = atm_trade.get('target_2', 0)
            t3_px    = atm_trade.get('target_3', 0)
            sl_px    = atm_trade.get('sl', 0)

            trade_block = (
                f"\n{t_emoji} <b>{symbol}</b>\n"
                f"Entry      ₹{entry_px:,.0f}\n"
                f"Target 1   ₹{t1_px:,.0f}\n"
            )
            if t2_px:
                trade_block += f"Target 2   ₹{t2_px:,.0f}\n"
            if t3_px:
                trade_block += f"Target 3   ₹{t3_px:,.0f}\n"
            trade_block += f"Stop Loss  ₹{sl_px:,.0f}\n"
            trailing = f"\n⚠️ <i>Once Target 1 is hit, trail your Stop Loss to ₹{t1_px:,.0f}</i>\n"
        else:
            trade_block = ''
            trailing    = ''

        msg = (
            f"🔒 <b>{display} — New Trade Signal{code_tag}</b>\n\n"
            f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
            f"⏰ <i>{ts} IST</i>\n"
            f"{trade_block}"
            f"{trailing}\n"
            f"⭐ <b>Conviction:</b> {conviction}\n\n"
            f"👉 <a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>View Signal on Target Capital →</a>\n"
            f"🚀 <a href='https://www.targetcapital.ai/pricing'>Start Free Trial — targetcapital.ai</a>"
        )

    elif signal_type == 'TRADE_EXIT':
        outcome     = signal_data.get('outcome', signal_data.get('exit_reason', 'Closed'))
        outcome_map = {
            'TARGET 1 HIT':   ('🎯',  'Target 1 Hit'),
            'TARGET 2 HIT':   ('🎯🎯', 'Target 2 Hit'),
            'TARGET 3 HIT':   ('🏆',  'Target 3 Hit'),
            'SL HIT':         ('🛑',  'Stop-Loss Hit'),
            '3PM SQUARE OFF': ('🕒',  '3PM Square Off'),
            'TARGET HIT':     ('🎯',  'Target Hit'),
            'EOD CLOSE':      ('🕒',  '3PM Square Off'),
        }
        out_emoji, out_label = outcome_map.get(outcome, ('🚪', outcome or 'Trade Closed'))

        msg = (
            f"🚪 <b>{display} — Trade Closed{code_tag}</b>\n\n"
            f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
            f"{out_emoji} <b>Result:</b> {out_label}\n"
            f"⏰ <i>{ts} IST</i>\n\n"
            f"📊 <i>Full trade history &amp; P&amp;L analytics on Target Capital.</i>\n\n"
            f"👉 <a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>Track Your Trades →</a>\n"
            f"🚀 <a href='https://www.targetcapital.ai/pricing'>Start Free Trial — targetcapital.ai</a>"
        )

    else:
        msg = (
            f"📍 <b>{display} — Active Signal{code_tag}</b>\n\n"
            f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
            f"⏰ <i>{ts} IST</i>\n\n"
            f"👉 <a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>View on Target Capital →</a>"
        )

    return msg


def _send_telegram_alert(signal_data: dict, index_id: str) -> bool:
    try:
        # ── Gate 0: NIFTY-only Telegram policy ───────────────────────────────
        # Bank Nifty, Fin Nifty, and SENSEX signals are shown on the platform
        # only. Only NIFTY 50 signals are broadcast to Telegram.
        if index_id != 'NIFTY':
            logger.info(f"[{index_id}] Telegram alert skipped — non-NIFTY index (platform-only)")
            return False

        # ── Gate 0.5: 9:30 AM IST minimum time gate ──────────────────────────
        # No Telegram signals before 9:30 AM IST — not even sample/test sends.
        # The opening 9:15 candle is noisy; proper signals only form after 9:30.
        _now = _now_ist()
        _market_open = _now.replace(hour=9, minute=30, second=0, microsecond=0)
        if _now < _market_open:
            logger.info(
                f"[{index_id}] Telegram alert skipped — before 9:30 AM IST "
                f"(current time {_now.strftime('%H:%M IST')})"
            )
            return False

        # Helper: push a Flask app context if one isn't already active.
        # _send_telegram_alert is called outside the engine's app_context block,
        # so DB queries (fno_config, schedule table) would fail without this.
        def _with_ctx(fn):
            """Run fn() inside an app context; return None on error."""
            try:
                if _fno_app is None:
                    return fn()
                try:
                    from flask import current_app
                    current_app._get_current_object()   # raises if no active ctx
                    return fn()                         # already inside a context
                except RuntimeError:
                    with _fno_app.app_context():
                        return fn()
            except Exception as _ctx_err:
                logger.warning(f"[{index_id}] _with_ctx error: {_ctx_err}")
                return None

        # ── Gate 1: per-index toggle (Admin → F&O Settings) ──────────────────
        try:
            from services.fno_config import is_index_telegram_enabled
            enabled = _with_ctx(lambda: is_index_telegram_enabled(index_id))
            if enabled is False:
                logger.info(f"[{index_id}] Telegram alert skipped — index de-selected in F&O Settings")
                return False
        except Exception as _gate_err:
            logger.warning(f"[{index_id}] per-index telegram gate failed (continuing): {_gate_err}")

        # ── Gate 2: master on/off from Alert Schedules page ──────────────────
        # Admin → Alert Schedules → "F&O Trade Signals" enabled toggle.
        # sched_on == False  → explicitly disabled by admin → block
        # sched_on == None   → DB read failed → treat as disabled (fail-safe)
        # sched_on == True   → enabled → allow
        try:
            from services.iscore_alert_dispatcher import _is_schedule_enabled
            sched_on = _with_ctx(lambda: _is_schedule_enabled('fno_signals', default=True))
            logger.debug(f"[{index_id}] fno_signals schedule enabled={sched_on!r}")
            if not sched_on:
                logger.info(f"[{index_id}] Telegram alert skipped — 'fno_signals' disabled in Alert Schedules")
                return False
        except Exception as _sched_err:
            logger.warning(f"[{index_id}] schedule gate check failed (skipping alert): {_sched_err}")
            return False

        # ── Resolve credentials ───────────────────────────────────────────────
        from services.messaging_service import _get_telegram_config
        token, chat_id = _get_telegram_config()
        if not token or not chat_id:
            logger.warning(
                f"[{index_id}] Telegram not configured — "
                f"token_present={bool(token)} chat_id_present={bool(chat_id)}"
            )
            return False

        # ── Build message (teaser vs full) ────────────────────────────────────
        try:
            from services.fno_config import get_fno_config, DEFAULT_TELEGRAM_FIELDS
            cfg  = _with_ctx(get_fno_config) or {}
            mode = cfg.get('telegram_mode', 'full')
            logger.info(f"[{index_id}] Telegram mode from config: {mode!r}")
        except Exception as _cfg_err:
            logger.warning(f"[{index_id}] get_fno_config failed: {_cfg_err} — using full mode")
            mode = 'full'
            cfg  = {}

        signal_type = signal_data.get('signal_type', 'TRADE')
        direction   = signal_data.get('trade_direction', 'NEUTRAL')
        confidence  = signal_data.get('confidence', 0)

        if mode == 'full':
            enabled = set(cfg.get('telegram_fields') or DEFAULT_TELEGRAM_FIELDS)
            msg = _build_full_message(signal_data, index_id, enabled)
        else:
            msg = _build_teaser_message(signal_data, index_id)

        # Delegate the actual HTTP call to messaging_service.send_telegram_message
        from services.messaging_service import send_telegram_message
        ok = send_telegram_message(msg, parse_mode='HTML')
        if ok:
            logger.info(f"[{index_id}] Telegram alert sent ({mode} mode): {signal_type}")
        else:
            logger.error(
                f"[{index_id}] Telegram alert failed "
                f"(signal_type={signal_type}, direction={direction}, conf={confidence}, mode={mode})"
            )
        return ok
    except Exception as e:
        logger.error(f"[{index_id}] Telegram alert error: {e}", exc_info=True)
        return False


# ── Alert gate ─────────────────────────────────────────────────────────────────

SCAN_ALERT_MIN_CONFIDENCE = 70   # only broadcast SCAN signals at/above this
SCAN_ALERT_COOLDOWN_MIN   = 15   # min minutes between two SCAN telegrams (per index)
SCAN_ALERT_MAX_PER_DAY    = 4    # safety cap (per index) — covers TRIGGER + SCAN combined


def _should_send_alert(signal_data: dict, idx: str) -> bool:
    """Telegram alert gate.

    Policy:
      • TRADE_TRIGGER — one message when a trade locks in. Subject to the
        daily cap (TRIGGER + EXIT combined ≤ SCAN_ALERT_MAX_PER_DAY * 2).
      • TRADE_EXIT    — one closing message per trade. Not counted toward the
        daily cap so every trigger always gets its matching exit ping.
      • SCAN / TRADE_ACTIVE — suppressed (no pre-trigger noise, no spam).
    """
    _reset_daily_if_needed(idx)
    signal_type = signal_data.get('signal_type')

    if signal_type == 'TRADE_EXIT':
        # Always send exit — no daily-cap check (paired with its trigger)
        return True

    if signal_type != 'TRADE_TRIGGER':
        return False

    direction = (signal_data.get('trade_direction') or '').upper()
    if direction not in ('BULLISH', 'BEARISH', 'BOTH'):
        return False

    # Hard daily cap (safety net against runaway flips)
    if _daily_signal_count[idx] >= SCAN_ALERT_MAX_PER_DAY:
        return False

    return True


# ── Trade lifecycle (per index) ────────────────────────────────────────────────

def _find_option_ltp(
    trades: list,
    atm_key: str,
    entry_strike,
    entry_type: str,
    raw_atm_chain: dict = None,
) -> float:
    """
    Locate the current LTP for the active-trade option using three fallbacks:

    1. Exact symbol match in the engine's recommended ``trades`` list.
    2. Strike + type match in ``trades`` (handles minor symbol format drift).
    3. Raw chain lookup in ``raw_atm_chain`` (bypasses MIN_PREMIUM and all
       liquidity filters) — critical when the option drops into SL territory
       and falls off the recommendation list.

    Returns 0.0 if the price cannot be determined from any source.
    """
    # 1. Exact symbol match
    for t in trades:
        if t.get('symbol') == atm_key:
            return float(t.get('ltp', 0) or 0)

    # 2. Strike + type match (symbol format may differ slightly)
    if entry_strike and entry_type:
        e_strike = float(entry_strike)
        e_type   = str(entry_type).upper()
        for t in trades:
            if (abs(float(t.get('strike', 0) or 0) - e_strike) < 0.5
                    and str(t.get('type', '')).upper() == e_type):
                return float(t.get('ltp', 0) or 0)

    # 3. Raw chain (bypasses MIN_PREMIUM — most important fallback)
    if raw_atm_chain and entry_strike and entry_type:
        ckey = f"{int(float(entry_strike))}{str(entry_type).upper()}"
        data = raw_atm_chain.get(ckey, {})
        if data:
            return float(data.get('ltp', 0) or 0)

    return 0.0


def _check_active_trade_exit(analysis: dict, idx: str):
    trade = _active_trade[idx]
    if not trade:
        return False, None

    now        = _now_ist()
    entry_time = trade.get('entry_time')

    # ── Previous-day guard ────────────────────────────────────────────────────
    # If the active trade was entered on a different IST calendar day than today,
    # force-close it immediately. This handles the APScheduler timing race where
    # the 3:00 PM EOD scan fires 1-3 seconds after market close, making
    # _is_market_hours() return False and skipping the EOD exit — leaving the
    # in-memory _active_trade set overnight, which blocks today's signal generation.
    if entry_time and entry_time.date() < now.date():
        logger.info(
            f"[{idx}] Previous-day trade detected "
            f"(entered {entry_time.date()}, today={now.date()}) — force-closing"
        )
        return True, '3PM Square Off'
    # ─────────────────────────────────────────────────────────────────────────

    # Hard 3PM square-off — every open intraday trade closes at 3:00 PM IST
    # so daily P&L is fully realised before cash market close.
    eod = now.replace(hour=EOD_FORCE_EXIT_HOUR, minute=EOD_FORCE_EXIT_MINUTE,
                      second=0, microsecond=0)
    if now >= eod:
        return True, '3PM Square Off'

    trades        = analysis.get('trades', [])
    raw_chain     = analysis.get('raw_atm_chain', {})
    atm_key       = trade.get('atm_key', '')
    entry_strike  = trade.get('atm_strike', 0)
    entry_type    = trade.get('type', '')
    ltp = _find_option_ltp(trades, atm_key, entry_strike, entry_type, raw_chain)
    if ltp > 0:
        # Update last_known_ltp each time we get a valid price so the EOD
        # square-off always has a real premium to record.
        if _active_trade[idx]:
            _active_trade[idx]['last_known_ltp'] = ltp
        sl  = trade.get('sl', 0)
        t1  = trade.get('target',   0)
        t2  = trade.get('target_2', 0)
        t3  = trade.get('target_3', 0)
        if sl > 0 and ltp <= sl:
            return True, f'Stop-loss hit (SL={sl:.0f}, LTP={ltp:.0f})'
        if t3 > 0 and ltp >= t3:
            return True, f'Target 3 reached (T3={t3:.0f}, LTP={ltp:.0f})'
        if t2 > 0 and ltp >= t2:
            return True, f'Target 2 reached (T2={t2:.0f}, LTP={ltp:.0f})'
        if t1 > 0 and ltp >= t1:
            return True, f'Target 1 reached (T1={t1:.0f}, LTP={ltp:.0f})'

    if not _is_market_hours():
        return True, 'Market closed'

    return False, None


def _try_trigger_trade(analysis: dict, idx: str):
    """Returns 'TRADE_TRIGGER' if a new trade should be locked, else None."""
    # Per-index circuit breaker: 2 consecutive SL hits on THIS index only blocks THIS index.
    if _circuit_breaker_on[idx]:
        logger.info(f"[{idx}] Circuit breaker ACTIVE — skipping new entry (2+ consecutive losses today on {idx})")
        return None

    direction  = analysis.get('trade_direction', 'NEUTRAL')
    confidence = analysis.get('smoothed_confidence', analysis.get('confidence', 0))
    entry_mode = analysis.get('entry_mode', 'NO TRADE')
    adx        = analysis.get('strength', {}).get('adx', 0)

    now  = _now_ist()

    # No new entries at or after 3:00 PM IST — the extended market-hours window
    # (3:00–3:05 PM) exists only to allow EOD exits to fire, not for new entries.
    eod = now.replace(hour=EOD_FORCE_EXIT_HOUR, minute=EOD_FORCE_EXIT_MINUTE,
                      second=0, microsecond=0)
    if now >= eod:
        logger.info(f"[{idx}] No new entries after 3:00 PM IST (EOD window)")
        _confirmation_count[idx] = 0
        _confirmation_dir[idx]   = None
        _trade_state[idx]        = 'NONE'
        return None

    last_exit = _last_exit_time[idx]
    if last_exit and (now - last_exit).total_seconds() < ENTRY_COOLDOWN_MINUTES * 60:
        remaining = int((ENTRY_COOLDOWN_MINUTES * 60 - (now - last_exit).total_seconds()) / 60)
        logger.info(f"[{idx}] Entry cooldown: {remaining}m remaining")
        _confirmation_count[idx] = 0
        _confirmation_dir[idx]   = None
        _trade_state[idx]        = 'NONE'
        return None

    strong = (
        confidence >= TRADE_MIN_CONFIDENCE
        and entry_mode in ('CONFIRMED', 'EARLY')
        and direction != 'NEUTRAL'
        and adx >= TRADE_MIN_ADX
    )

    if not strong:
        if _confirmation_dir[idx] and direction != _confirmation_dir[idx]:
            logger.info(f"[{idx}] Direction changed, resetting confirmation")
        elif _confirmation_count[idx] > 0:
            logger.info(f"[{idx}] Signal weakened (conf={confidence}, adx={adx}), resetting")
        _confirmation_count[idx] = 0
        _confirmation_dir[idx]   = None
        _trade_state[idx]        = 'NONE'
        return None

    # Accumulate confirmations
    if _confirmation_dir[idx] != direction:
        _confirmation_count[idx] = 1
        _confirmation_dir[idx]   = direction
        _trade_state[idx]        = 'CONFIRMING'
        logger.info(f"[{idx}] Confirmation 1/{CONFIRMATION_CANDLES} for {direction}")
        return None

    _confirmation_count[idx] += 1
    logger.info(f"[{idx}] Confirmation {_confirmation_count[idx]}/{CONFIRMATION_CANDLES} for {direction}")

    if _confirmation_count[idx] >= CONFIRMATION_CANDLES:
        trades    = analysis.get('trades', [])
        atm_trade = next((t for t in trades if t.get('moneyness') == 'ATM'), trades[0] if trades else None)
        _active_trade[idx] = {
            'direction':   direction,
            'entry_time':  now,
            'entry_mode':  entry_mode,
            'confidence':  confidence,
            'spot':        analysis.get('spot_price', 0),
            'atm_strike':  analysis.get('atm_strike', 0),
            'atm_key':     atm_trade.get('symbol', '') if atm_trade else '',
            'entry_price': atm_trade.get('entry_price', 0) if atm_trade else 0,
            'sl':          atm_trade.get('sl', 0) if atm_trade else 0,
            'target':      atm_trade.get('target', 0) if atm_trade else 0,
            'target_2':    atm_trade.get('target_2', 0) if atm_trade else 0,
            'target_3':    atm_trade.get('target_3', 0) if atm_trade else 0,
            'type':        atm_trade.get('type', '') if atm_trade else '',
            'data_source': analysis.get('data_source', ''),
            'index_id':    idx,
        }
        _trade_state[idx]        = 'ACTIVE'
        _confirmation_count[idx] = 0
        _confirmation_dir[idx]   = None
        t = _active_trade[idx]
        logger.info(
            f"[{idx}] 🔒 Trade LOCKED: {direction} {t.get('type','')} "
            f"@ {t['entry_price']:.0f}  SL={t['sl']:.0f}  Target={t['target']:.0f}"
        )
        return 'TRADE_TRIGGER'

    return None


# ── Per-index scan ─────────────────────────────────────────────────────────────

def _scan_index(app, idx: str, data_broker_user_id):
    try:
        with app.app_context():
            from services.nifty_options_engine import NiftyOptionsEngine, INDEX_CONFIGS
            if idx not in INDEX_CONFIGS:
                logger.warning(f"[{idx}] Not in INDEX_CONFIGS, skipping")
                return
            engine   = NiftyOptionsEngine(user_id=data_broker_user_id, index=idx)
            analysis = engine.generate_analysis()

        if not analysis:
            logger.warning(f"[{idx}] Engine returned no analysis")
            return

        # Flat market gate — ADX < 20 = sideways conditions.
        # Skip all new signal generation, DB save, and Telegram alerts.
        # Exception: if a trade is ACTIVE we still monitor it for exits.
        if analysis.get('market_regime') == 'flat' and _trade_state[idx] != 'ACTIVE':
            logger.info(
                f"[{idx}] 🟡 Flat market (ADX={analysis.get('strength', {}).get('adx', 0):.1f} < 20) "
                f"— signals suppressed, skipping scan"
            )
            return

        raw_conf = analysis.get('confidence', 0)
        smoothed = _smoothed_confidence(idx, raw_conf)
        analysis['smoothed_confidence'] = smoothed

        logger.info(
            f"[{idx}] spot={analysis.get('spot_price', 0):.2f} "
            f"dir={analysis.get('trade_direction', 'N/A')} "
            f"conf={raw_conf}(~{smoothed}) "
            f"mode={analysis.get('entry_mode', 'N/A')} "
            f"src={analysis.get('data_source', 'N/A')} "
            f"state={_trade_state[idx]}"
        )

        signal_type = None
        alert_sent  = False

        with _state_lock:
            _reset_daily_if_needed(idx)

            if _trade_state[idx] == 'ACTIVE':
                should_exit, exit_reason = _check_active_trade_exit(analysis, idx)
                if should_exit:
                    outcome    = _exit_reason_to_outcome(exit_reason)
                    trade_code = _active_trade_code[idx]
                    logger.info(f"[{idx}] 🚪 Trade EXIT: {exit_reason} → {outcome} ({trade_code})")
                    analysis['signal_type']    = 'TRADE_EXIT'
                    analysis['exit_reason']    = exit_reason
                    analysis['outcome']        = outcome
                    analysis['trade_code']     = trade_code
                    analysis['trade_direction'] = _active_trade[idx].get('direction', analysis.get('trade_direction'))
                    analysis['confidence']      = _active_trade[idx].get('confidence', analysis.get('confidence'))
                    # Capture the ATM option LTP at the moment of exit so P&L
                    # can be calculated. Use the 3-tier helper so we get a real
                    # price even when the option dropped below MIN_PREMIUM filter
                    # (e.g. SL-zone options disappear from the recommended list).
                    _at_exit  = _active_trade[idx]
                    _exit_ltp = _find_option_ltp(
                        analysis.get('trades', []),
                        _at_exit.get('atm_key', ''),
                        _at_exit.get('atm_strike', 0),
                        _at_exit.get('type', ''),
                        analysis.get('raw_atm_chain', {}),
                    )
                    # Final fallback: last polled LTP (updated every 60 s while ACTIVE)
                    if _exit_ltp <= 0:
                        _exit_ltp = float(_at_exit.get('last_known_ltp', 0) or 0)
                    analysis['exit_option_ltp'] = _exit_ltp
                    _last_exit_time[idx]    = _now_ist()
                    _active_trade[idx]      = None
                    _active_trade_code[idx] = None
                    _trade_state[idx]       = 'NONE'
                    signal_type             = 'TRADE_EXIT'
                    if _should_send_alert(analysis, idx):
                        alert_sent = _send_telegram_alert(analysis, idx)
                    _dispatch_partner_webhook(analysis, idx)
                else:
                    at   = _active_trade[idx]
                    now  = _now_ist()
                    entry_time = at.get('entry_time')
                    elapsed    = int((now - entry_time).total_seconds() / 60) if entry_time else 0
                    eod_today  = now.replace(hour=EOD_FORCE_EXIT_HOUR, minute=EOD_FORCE_EXIT_MINUTE, second=0, microsecond=0)
                    remaining  = max(0, int((eod_today - now).total_seconds() / 60))
                    logger.info(
                        f"[{idx}] Trade ACTIVE: {at.get('direction')} "
                        f"since {entry_time.strftime('%H:%M')} IST "
                        f"({elapsed} min elapsed)"
                    )

                    # Periodic TRADE_ACTIVE Telegram updates are disabled —
                    # only entry (TRADE_TRIGGER) and exit (TRADE_EXIT) are sent.
                    # Partner webhooks still receive ACTIVE pings for downstream
                    # systems that need live tracking.
                    ltp = _find_option_ltp(
                        analysis.get('trades', []),
                        at.get('atm_key', ''),
                        at.get('atm_strike', 0),
                        at.get('type', ''),
                        analysis.get('raw_atm_chain', {}),
                    )
                    # Keep a rolling snapshot so 3PM square-off / SL checks
                    # always have a real premium price even when the option
                    # falls off the MIN_PREMIUM-filtered recommended list.
                    if ltp > 0 and _active_trade[idx]:
                        _active_trade[idx]['last_known_ltp'] = ltp
                    active_info = {
                        'direction':   at.get('direction'),
                        'type':        at.get('type', ''),
                        'entry_price': at.get('entry_price', 0),
                        'sl':          at.get('sl', 0),
                        'target':      at.get('target', 0),
                        'ltp':         ltp,
                        'elapsed_min': elapsed,
                        'remaining_min': remaining,
                    }
                    active_analysis = dict(analysis)
                    active_analysis['signal_type']    = 'TRADE_ACTIVE'
                    active_analysis['trade_direction'] = at.get('direction', analysis.get('trade_direction'))
                    active_analysis['confidence']      = at.get('confidence', analysis.get('confidence'))
                    active_analysis['active_trade']    = active_info
                    active_analysis['trade_code']      = _active_trade_code[idx]
                    _dispatch_partner_webhook(active_analysis, idx)
                    return

            else:
                trigger = _try_trigger_trade(analysis, idx)
                if trigger == 'TRADE_TRIGGER':
                    signal_type            = 'TRADE_TRIGGER'
                    analysis['signal_type'] = 'TRADE_TRIGGER'
                    # Generate and persist the trade code
                    trade_code = _generate_trade_code(app, idx)
                    _active_trade_code[idx]              = trade_code
                    _active_trade[idx]['trade_code']     = trade_code
                    analysis['trade_code']               = trade_code
                    logger.info(f"[{idx}] 🆔 Trade code assigned: {trade_code}")
                    if _should_send_alert(analysis, idx):
                        alert_sent = _send_telegram_alert(analysis, idx)
                        if alert_sent:
                            _last_signal_time[idx]      = _now_ist()
                            _last_signal_direction[idx] = analysis.get('trade_direction')
                            _daily_signal_count[idx]   += 1
                            _persist_alert_state(idx)
                    _dispatch_partner_webhook(analysis, idx)

                elif analysis.get('entry_mode') != 'NO TRADE' and smoothed >= 60:
                    signal_type             = 'SCAN'
                    analysis['signal_type'] = 'SCAN'
                    # Broadcast high-confidence directional SCAN to Telegram
                    # immediately (gate enforces cooldown + daily cap).
                    if _should_send_alert(analysis, idx):
                        alert_sent = _send_telegram_alert(analysis, idx)
                        if alert_sent:
                            _last_signal_time[idx]      = _now_ist()
                            _last_signal_direction[idx] = analysis.get('trade_direction')
                            _daily_signal_count[idx]   += 1
                            _persist_alert_state(idx)
                    _dispatch_partner_webhook(analysis, idx)
                else:
                    return  # no-trade scan — skip DB

        analysis['alert_sent'] = alert_sent
        if signal_type:
            _save_signal_to_db(
                app, analysis, idx,
                trade_code=analysis.get('trade_code'),
                outcome=analysis.get('outcome'),
            )

        # ── Per-index circuit breaker: track SL streaks independently ─────────
        # Two consecutive SL hits on THIS index blocks only THIS index.
        # Other indices remain unaffected and can continue trading.
        if signal_type == 'TRADE_EXIT':
            outcome_now = analysis.get('outcome', '')
            if outcome_now == 'SL HIT':
                _consecutive_losses[idx] += 1
                logger.info(
                    f"[{idx}] SL hit streak: {_consecutive_losses[idx]} consecutive"
                )
                if _consecutive_losses[idx] >= 2 and not _circuit_breaker_on[idx]:
                    _circuit_breaker_on[idx]   = True
                    _circuit_breaker_date[idx] = _now_ist().date()
                    logger.warning(
                        f"[{idx}] ⚡ CIRCUIT BREAKER TRIPPED — "
                        f"{_consecutive_losses[idx]} consecutive SL hits on {idx}. "
                        f"New {idx} entries suppressed for today. Other indices unaffected."
                    )
            else:
                # Any non-SL exit (Target hit / 3PM square-off) resets the streak
                _consecutive_losses[idx] = 0

    except Exception as e:
        logger.error(f"[{idx}] Scan error: {e}", exc_info=True)


# ── Main scheduler entry point ─────────────────────────────────────────────────

def run_scan(app):
    if not _is_market_hours():
        return

    try:
        from app import db
        with app.app_context():
            result = db.session.execute(
                db.text("SELECT user_id FROM data_api_broker WHERE is_active = true AND connection_status = 'connected' LIMIT 1")
            ).fetchone()
            data_broker_user_id = result[0] if result else None
    except Exception as e:
        logger.debug(f"No data broker user found: {e}")
        data_broker_user_id = None

    for idx in SCAN_INDICES:
        _scan_index(app, idx, data_broker_user_id)


_FNO_ADVISORY_LOCK_ID = 728193001  # arbitrary unique int per scheduler


def _try_acquire_scheduler_lock(app, lock_id: int) -> bool:
    """
    Acquire a Postgres session-level advisory lock so only ONE gunicorn worker
    runs the scheduler. Held for the lifetime of the connection (the worker
    process). Returns True if this worker won the lock.
    """
    if os.environ.get("DISABLE_SCHEDULERS", "").lower() in ("1", "true", "yes"):
        return False
    try:
        from sqlalchemy import text
        from app import db
        with app.app_context():
            # Use a dedicated, never-released connection so the lock survives
            # for the worker's lifetime.
            conn = db.engine.connect()
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_id}
            ).scalar()
            if got:
                # Intentionally never close `conn` — keeps the lock held.
                return True
            conn.close()
            return False
    except Exception as e:
        # If Postgres advisory locks aren't available (e.g. SQLite dev),
        # fall back to running the scheduler in this worker.
        logger.warning(f"Advisory lock check failed ({e}); starting scheduler anyway")
        return True


def start_scheduler(app):
    global _scheduler_started, _fno_app
    _fno_app = app          # store so alert helpers can push an app context
    if _scheduler_started:
        return
    if not _try_acquire_scheduler_lock(app, _FNO_ADVISORY_LOCK_ID):
        logger.info("F&O scheduler skipped on this worker (another worker holds the lock)")
        return
    try:
        # Restore alert dedup state from Redis so a restart doesn't blow
        # away the daily-cap counter (would otherwise allow re-spamming).
        _restore_alert_state()
        # Restore in-progress trade state from DB so a restart doesn't
        # re-fire a TRADE_TRIGGER alert for an already-active trade.
        _restore_active_trade_state(app)
        # Auto-close any orphaned TRIGGER rows from past sessions that
        # were never marked with an outcome (app was restarted mid-trade).
        _close_stale_open_trades(app)
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            run_scan, 'interval', seconds=60,
            args=[app], id='fno_multi_scan',
            replace_existing=True, max_instances=1,
        )
        scheduler.start()
        _scheduler_started = True
        logger.info("F&O continuous monitor started (60s interval, 4 indices) — singleton worker")
    except Exception as e:
        logger.error(f"Failed to start F&O scheduler: {e}")


# ── Status API (NIFTY primary for dashboard, plus per-index summary) ───────────

def get_monitor_status(index_id: str = 'NIFTY') -> dict:
    """Return lifecycle status for one index (defaults to NIFTY for backward compat)."""
    idx = index_id if index_id in SCAN_INDICES else 'NIFTY'
    _reset_daily_if_needed(idx)

    active = None
    trade  = _active_trade[idx]
    if trade:
        now        = _now_ist()
        entry_time = trade.get('entry_time')
        elapsed    = int((now - entry_time).total_seconds() / 60) if entry_time else 0
        eod_today  = now.replace(hour=EOD_FORCE_EXIT_HOUR, minute=EOD_FORCE_EXIT_MINUTE, second=0, microsecond=0)
        remaining  = max(0, int((eod_today - now).total_seconds() / 60))
        active = {
            'direction':   trade.get('direction'),
            'type':        trade.get('type'),
            'atm_strike':  trade.get('atm_strike'),
            'entry_price': trade.get('entry_price'),
            'sl':          trade.get('sl'),
            'target':      trade.get('target'),
            'target_2':    trade.get('target_2', 0),
            'target_3':    trade.get('target_3', 0),
            'entry_time':  entry_time.strftime('%I:%M %p') if entry_time else None,
            'elapsed_min': elapsed,
            'remaining_min': remaining,
            'confidence':  trade.get('confidence'),
            'data_source': trade.get('data_source'),
            'trade_code':  _active_trade_code[idx],
        }

    last_exit = _last_exit_time[idx]
    cooldown  = 0
    if last_exit:
        elapsed  = (_now_ist() - last_exit).total_seconds()
        cooldown = max(0, int((ENTRY_COOLDOWN_MINUTES * 60 - elapsed) / 60))

    return {
        'running':              _scheduler_started,
        'market_hours':         _is_market_hours(),
        'index_id':             idx,
        'last_signal_time':     _last_signal_time[idx].strftime('%I:%M %p') if _last_signal_time[idx] else None,
        'last_direction':       _last_signal_direction[idx],
        'signals_today':        _daily_signal_count[idx],
        'max_signals':          MAX_SIGNALS_PER_DAY,
        'cooldown_minutes':     SIGNAL_COOLDOWN_MINUTES,
        'confidence_threshold': ALERT_CONFIDENCE_THRESHOLD,
        'trade_state':          _trade_state[idx],
        'confirmation_count':   _confirmation_count[idx],
        'confirmation_needed':  CONFIRMATION_CANDLES,
        'active_trade':         active,
        'cooldown_remaining_min': cooldown,
        'entry_cooldown_min':   ENTRY_COOLDOWN_MINUTES,
        'circuit_breaker':      _circuit_breaker_on[idx],
        'consecutive_losses':   _consecutive_losses[idx],
        'all_states': {
            i: {
                'trade_state':        _trade_state[i],
                'signals_today':      _daily_signal_count[i],
                'active':             bool(_active_trade[i]),
                'circuit_breaker':    _circuit_breaker_on[i],
                'consecutive_losses': _consecutive_losses[i],
            } for i in SCAN_INDICES
        },
    }
