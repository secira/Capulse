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
# CONFIRMATION_CANDLES dropped from 2 → 1 so the trigger fires on the CURRENT
# forming 5-min candle. Two-candle confirmation was adding 5–10 min lag and
# causing breakouts (e.g. 2:18) to be signalled only after consolidation (2:28).
CONFIRMATION_CANDLES    = 1
TRADE_MAX_DURATION_MIN  = 30
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

_scheduler_started = False


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_ist():
    return datetime.utcnow() + IST_OFFSET


def _is_market_hours():
    now = _now_ist()
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=25, second=0, microsecond=0)
    close_ = now.replace(hour=14, minute=59, second=0, microsecond=0)
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


def _reset_daily_if_needed(idx: str):
    today = _now_ist().date()
    if _daily_date[idx] != today:
        _daily_date[idx]         = today
        _daily_signal_count[idx] = 0
        _persist_alert_state(idx)


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
    if 'target' in r:
        return 'TARGET HIT'
    if 'stop' in r or 'sl' in r:
        return 'SL HIT'
    if 'eod' in r or '3:00 pm' in r:
        return 'EOD CLOSE'
    if 'time limit' in r or 'time' in r:
        return 'TIME EXIT'
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
                     alert_sent, data_source, trade_code, outcome)
                VALUES
                    (:index_id, :signal_type, :direction, :confidence, :confidence_grade,
                     :entry_mode, :spot_price, :atm_strike, :trades_json, :layers_json,
                     :alert_sent, :data_source, :trade_code, :outcome)
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
                'alert_sent':  signal_data.get('alert_sent', False),
                'data_source': signal_data.get('data_source', 'nse_python'),
                'trade_code':  trade_code,
                'outcome':     outcome,
            })

            # On exit: also update the TRIGGER record with the outcome
            if signal_data.get('signal_type') == 'TRADE_EXIT' and trade_code and outcome:
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
                    'exit_spot':  signal_data.get('spot_price', 0),
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


def _send_telegram_alert(signal_data: dict, index_id: str) -> bool:
    try:
        raw_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id   = os.environ.get('TELEGRAM_CHAT_ID', '')
        if not raw_token or not chat_id:
            logger.warning("Telegram not configured for F&O alerts")
            return False

        import re
        match = re.search(r'(\d+:[A-Za-z0-9_-]+)', raw_token)
        token = match.group(1) if match else raw_token

        display    = _INDEX_DISPLAY.get(index_id, index_id)
        direction  = signal_data.get('trade_direction', 'NEUTRAL')
        confidence = signal_data.get('confidence', 0)
        entry_mode = signal_data.get('entry_mode', 'NO TRADE')
        spot       = signal_data.get('spot_price', 0)
        atm        = signal_data.get('atm_strike', 0)
        signal_type = signal_data.get('signal_type', 'TRADE')

        dir_label  = 'CALL + PUT' if direction == 'BOTH' else direction
        dir_emoji  = '🟢' if direction == 'BULLISH' else '🔴' if direction == 'BEARISH' else '🟣' if direction == 'BOTH' else '🟡'
        if signal_type == 'TRADE_TRIGGER':
            type_emoji = '🔒'
        elif signal_type == 'TRADE_EXIT':
            type_emoji = '🚪'
        elif signal_type == 'TRADE_ACTIVE':
            type_emoji = '📍'
        else:
            type_emoji = '📡'

        trade_code = signal_data.get('trade_code', '')
        code_tag   = f" <code>{trade_code}</code>" if trade_code else ''
        msg  = f"{type_emoji} <b>{display} F&amp;O — {signal_type.replace('_', ' ')}</b>{code_tag}\n\n"
        msg += f"{dir_emoji} <b>Direction:</b> {dir_label}\n"
        msg += f"📊 <b>Confidence:</b> {confidence}/100 ({signal_data.get('confidence_grade', '')})\n"
        msg += f"🎯 <b>Entry Mode:</b> {entry_mode}\n"
        msg += f"💰 <b>Spot:</b> ₹{spot:,.2f} | ATM: {atm}\n\n"

        # For TRADE_ACTIVE updates, show live trade details with elapsed time
        if signal_type == 'TRADE_ACTIVE':
            active = signal_data.get('active_trade', {})
            if active:
                elapsed = active.get('elapsed_min', 0)
                remaining = active.get('remaining_min', TRADE_MAX_DURATION_MIN)
                entry_px  = active.get('entry_price', 0)
                sl_px     = active.get('sl', 0)
                tgt_px    = active.get('target', 0)
                ltp       = active.get('ltp', 0)
                pnl_pts   = round(ltp - entry_px, 2) if ltp and entry_px else 0
                pnl_emoji = '📈' if pnl_pts >= 0 else '📉'
                opt_type  = active.get('type', '')
                msg += f"<b>Active Trade ({opt_type}):</b>\n"
                msg += f"  ⏱ Running: {elapsed} min | {remaining} min left\n"
                msg += f"  🏷 Entry: ₹{entry_px:,.0f}\n"
                if ltp:
                    msg += f"  {pnl_emoji} LTP: ₹{ltp:,.0f} ({'+' if pnl_pts >= 0 else ''}{pnl_pts:.0f} pts)\n"
                msg += f"  🛑 SL: ₹{sl_px:,.0f}  |  🎯 Target: ₹{tgt_px:,.0f}\n"
        else:
            trades = signal_data.get('trades', [])
            if trades:
                msg += f"<b>Trades ({len(trades)}):</b>\n"
                for t in trades[:3]:
                    t_emoji = '📗' if t.get('type') == 'CE' else '📕'
                    msg += (
                        f"{t_emoji} {t.get('symbol', '')} — "
                        f"Entry ₹{t.get('entry_price', 0):,.0f}, "
                        f"Target ₹{t.get('target', 0):,.0f}, "
                        f"SL ₹{t.get('sl', 0):,.0f}\n"
                    )

        if signal_type == 'TRADE_EXIT':
            msg += f"\n🚪 <b>Exit Reason:</b> {signal_data.get('exit_reason', 'Unknown')}\n"

        page_path = _INDEX_PAGE_PATH.get(index_id, 'nifty')
        msg += f"\n⏰ <i>{_now_ist().strftime('%d/%m/%Y %I:%M %p')} IST</i>"
        msg += f"\n\n<a href='https://www.targetcapital.ai/dashboard/fno/{page_path}'>View on Target Capital</a>"

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML',
                  'disable_web_page_preview': True},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"[{index_id}] Telegram alert sent: {signal_type}")
            return True
        logger.error(f"[{index_id}] Telegram error {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"[{index_id}] Telegram alert error: {e}")
        return False


# ── Alert gate ─────────────────────────────────────────────────────────────────

def _should_send_alert(signal_data: dict, idx: str) -> bool:
    """Telegram is restricted to TRADE_TRIGGER (entry) and TRADE_EXIT (close)
    only. Periodic TRADE_ACTIVE updates and pre-trigger SCAN tier-1 signals
    are no longer broadcast — they were creating notification noise."""
    _reset_daily_if_needed(idx)
    signal_type = signal_data.get('signal_type', 'SCAN')
    return signal_type in ('TRADE_TRIGGER', 'TRADE_EXIT')


# ── Trade lifecycle (per index) ────────────────────────────────────────────────

def _check_active_trade_exit(analysis: dict, idx: str):
    trade = _active_trade[idx]
    if not trade:
        return False, None

    now        = _now_ist()
    entry_time = trade.get('entry_time')

    # Hard EOD close — every open signal closes at 3:00 PM IST so the day's
    # P&L is fully realised before the cash market close. Runs BEFORE the
    # SL/Target/duration checks so EOD always wins.
    eod = now.replace(hour=EOD_FORCE_EXIT_HOUR, minute=EOD_FORCE_EXIT_MINUTE,
                      second=0, microsecond=0)
    if now >= eod:
        return True, 'EOD auto-close (3:00 PM IST)'

    if entry_time and (now - entry_time).total_seconds() >= TRADE_MAX_DURATION_MIN * 60:
        return True, f'Time limit reached ({TRADE_MAX_DURATION_MIN} min)'

    trades  = analysis.get('trades', [])
    atm_key = trade.get('atm_key', '')
    if trades and atm_key:
        for t in trades:
            if t.get('symbol') == atm_key:
                ltp    = t.get('ltp', 0)
                sl     = trade.get('sl', 0)
                target = trade.get('target', 0)
                if ltp > 0 and sl > 0 and ltp <= sl:
                    return True, f'Stop-loss hit (SL={sl:.0f}, LTP={ltp:.0f})'
                if ltp > 0 and target > 0 and ltp >= target:
                    return True, f'Target reached (Target={target:.0f}, LTP={ltp:.0f})'

    if not _is_market_hours():
        return True, 'Market closed'

    return False, None


def _try_trigger_trade(analysis: dict, idx: str):
    """Returns 'TRADE_TRIGGER' if a new trade should be locked, else None."""
    direction  = analysis.get('trade_direction', 'NEUTRAL')
    confidence = analysis.get('smoothed_confidence', analysis.get('confidence', 0))
    entry_mode = analysis.get('entry_mode', 'NO TRADE')
    adx        = analysis.get('strength', {}).get('adx', 0)

    now  = _now_ist()
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
                    remaining  = max(0, TRADE_MAX_DURATION_MIN - elapsed)
                    logger.info(
                        f"[{idx}] Trade ACTIVE: {at.get('direction')} "
                        f"since {entry_time.strftime('%H:%M')} IST "
                        f"({elapsed} min elapsed)"
                    )

                    # Periodic TRADE_ACTIVE Telegram updates are disabled —
                    # only entry (TRADE_TRIGGER) and exit (TRADE_EXIT) are sent.
                    # Partner webhooks still receive ACTIVE pings for downstream
                    # systems that need live tracking.
                    atm_key = at.get('atm_key', '')
                    ltp = 0.0
                    for t in analysis.get('trades', []):
                        if t.get('symbol') == atm_key:
                            ltp = float(t.get('ltp', 0))
                            break
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
                else:
                    return  # no-trade scan — skip DB

        analysis['alert_sent'] = alert_sent
        if signal_type:
            _save_signal_to_db(
                app, analysis, idx,
                trade_code=analysis.get('trade_code'),
                outcome=analysis.get('outcome'),
            )

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
    global _scheduler_started
    if _scheduler_started:
        return
    if not _try_acquire_scheduler_lock(app, _FNO_ADVISORY_LOCK_ID):
        logger.info("F&O scheduler skipped on this worker (another worker holds the lock)")
        return
    try:
        # Restore alert dedup state from Redis so a restart doesn't blow
        # away the daily-cap counter (would otherwise allow re-spamming).
        _restore_alert_state()
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
        remaining  = max(0, TRADE_MAX_DURATION_MIN - elapsed)
        active = {
            'direction':   trade.get('direction'),
            'type':        trade.get('type'),
            'atm_strike':  trade.get('atm_strike'),
            'entry_price': trade.get('entry_price'),
            'sl':          trade.get('sl'),
            'target':      trade.get('target'),
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
        'all_states': {
            i: {
                'trade_state': _trade_state[i],
                'signals_today': _daily_signal_count[i],
                'active': bool(_active_trade[i]),
            } for i in SCAN_INDICES
        },
    }
