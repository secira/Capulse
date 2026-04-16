import os
import logging
import requests
from datetime import datetime, timedelta
from threading import Lock

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

NIFTY_LOT_SIZE = 50
DEFAULT_SL_POINTS = 10
ALERT_CONFIDENCE_THRESHOLD = 75
SIGNAL_COOLDOWN_MINUTES = 10
MAX_SIGNALS_PER_DAY = 3

# Trade Lifecycle constants
CONFIRMATION_CANDLES = 2        # consecutive scans required to lock a trade
TRADE_MAX_DURATION_MIN = 30     # exit after 30 minutes regardless
ENTRY_COOLDOWN_MINUTES = 15     # wait after a trade exits before new entry
TRADE_MIN_CONFIDENCE = 80       # minimum confidence to trigger a trade
TRADE_MIN_ADX = 25              # minimum ADX to trigger a trade

_state_lock = Lock()

# Alert state
_last_signal_time = None
_last_signal_direction = None
_daily_signal_count = 0
_daily_date = None
_scheduler_started = False

# Trade Lifecycle state
_trade_state = 'NONE'           # NONE | CONFIRMING | ACTIVE
_confirmation_count = 0
_confirmation_direction = None
_active_trade = None            # dict with full trade details when ACTIVE
_last_trade_exit_time = None    # when last trade ended (for cooldown)
_recent_confidences = []        # last 3 confidence values for smoothing


def _now_ist():
    return datetime.utcnow() + IST_OFFSET


def _reset_daily_if_needed():
    global _daily_signal_count, _daily_date
    today = _now_ist().date()
    if _daily_date != today:
        _daily_date = today
        _daily_signal_count = 0


def _is_market_hours():
    now = _now_ist()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def _smoothed_confidence(raw_confidence: int) -> int:
    global _recent_confidences
    _recent_confidences.append(raw_confidence)
    if len(_recent_confidences) > 3:
        _recent_confidences = _recent_confidences[-3:]
    return int(sum(_recent_confidences) / len(_recent_confidences))


def _save_signal_to_db(app, signal_data):
    try:
        from app import db
        with app.app_context():
            db.session.execute(db.text("""
                INSERT INTO fno_signal_history
                    (signal_type, direction, confidence, confidence_grade, entry_mode,
                     spot_price, atm_strike, trades_json, layers_json, alert_sent, data_source)
                VALUES
                    (:signal_type, :direction, :confidence, :confidence_grade, :entry_mode,
                     :spot_price, :atm_strike, :trades_json, :layers_json, :alert_sent, :data_source)
            """), {
                'signal_type': signal_data.get('signal_type', 'TRADE'),
                'direction': signal_data.get('trade_direction', 'NEUTRAL'),
                'confidence': signal_data.get('confidence', 0),
                'confidence_grade': signal_data.get('confidence_grade', 'Weak'),
                'entry_mode': signal_data.get('entry_mode', 'NO TRADE'),
                'spot_price': signal_data.get('spot_price', 0),
                'atm_strike': signal_data.get('atm_strike', 0),
                'trades_json': str(signal_data.get('trades', [])),
                'layers_json': str({
                    'time_filter': signal_data.get('time_filter', {}),
                    'direction': signal_data.get('direction', {}),
                    'strength': signal_data.get('strength', {}),
                    'oi_analysis': signal_data.get('oi_analysis', {}),
                }),
                'alert_sent': signal_data.get('alert_sent', False),
                'data_source': signal_data.get('data_source', 'nse_python'),
            })
            db.session.commit()
    except Exception as e:
        logger.error(f"Failed to save F&O signal to DB: {e}")


def _send_telegram_alert(signal_data):
    try:
        raw_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')

        if not raw_token or not chat_id:
            logger.warning("Telegram not configured for F&O alerts")
            return False

        import re
        match = re.search(r'(\d+:[A-Za-z0-9_-]+)', raw_token)
        token = match.group(1) if match else raw_token

        direction = signal_data.get('trade_direction', 'NEUTRAL')
        confidence = signal_data.get('confidence', 0)
        entry_mode = signal_data.get('entry_mode', 'NO TRADE')
        spot = signal_data.get('spot_price', 0)
        atm = signal_data.get('atm_strike', 0)
        signal_type = signal_data.get('signal_type', 'TRADE')

        dir_emoji = '🟢' if direction == 'BULLISH' else '🔴' if direction == 'BEARISH' else '🟡'
        type_emoji = '🔒' if signal_type == 'TRADE_TRIGGER' else '🚪' if signal_type == 'TRADE_EXIT' else '📡'

        msg = f"{type_emoji} <b>NIFTY F&amp;O — {signal_type.replace('_', ' ')}</b>\n\n"
        msg += f"{dir_emoji} <b>Direction:</b> {direction}\n"
        msg += f"📊 <b>Confidence:</b> {confidence}/100 ({signal_data.get('confidence_grade', '')})\n"
        msg += f"🎯 <b>Entry Mode:</b> {entry_mode}\n"
        msg += f"💰 <b>Spot:</b> ₹{spot:,.2f} | ATM: {atm}\n\n"

        trades = signal_data.get('trades', [])
        if trades:
            msg += f"<b>Trades ({len(trades)}):</b>\n"
            for t in trades[:3]:
                t_emoji = '📗' if t.get('type') == 'CE' else '📕'
                msg += f"{t_emoji} {t.get('symbol', '')} — Entry ₹{t.get('entry_price', 0):,.0f}, "
                msg += f"Target ₹{t.get('target', 0):,.0f}, SL ₹{t.get('sl', 0):,.0f}\n"

        if signal_type == 'TRADE_EXIT':
            exit_reason = signal_data.get('exit_reason', 'Unknown')
            msg += f"\n🚪 <b>Exit Reason:</b> {exit_reason}\n"

        msg += f"\n⏰ <i>{_now_ist().strftime('%d/%m/%Y %I:%M %p')} IST</i>"
        msg += f"\n\n<a href='https://tcapital.com/dashboard/fno/nifty'>View on Target Capital</a>"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': msg,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"F&O Telegram alert sent: {signal_type}")
            return True
        else:
            logger.error(f"Telegram API error: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram alert error: {e}")
        return False


def _should_send_alert(signal_data):
    global _last_signal_time, _last_signal_direction, _daily_signal_count

    _reset_daily_if_needed()

    confidence = signal_data.get('confidence', 0)
    direction = signal_data.get('trade_direction', 'NEUTRAL')
    signal_type = signal_data.get('signal_type', 'SCAN')

    # Always alert on trade trigger and exit events
    if signal_type in ('TRADE_TRIGGER', 'TRADE_EXIT'):
        return True

    if confidence < ALERT_CONFIDENCE_THRESHOLD:
        return False

    if signal_data.get('entry_mode') == 'NO TRADE':
        return False

    if direction == 'NEUTRAL':
        return False

    if _daily_signal_count >= MAX_SIGNALS_PER_DAY:
        logger.info(f"Daily signal limit reached ({MAX_SIGNALS_PER_DAY})")
        return False

    now = _now_ist()
    if _last_signal_time and (now - _last_signal_time).total_seconds() < SIGNAL_COOLDOWN_MINUTES * 60:
        logger.info(f"Signal cooldown active (last: {_last_signal_time})")
        return False

    if _last_signal_direction == direction:
        logger.info(f"Duplicate direction signal ignored ({direction})")
        return False

    return True


def _get_data_broker_user_id():
    try:
        from app import db
        result = db.session.execute(
            db.text("SELECT user_id FROM data_api_broker WHERE is_active = true AND connection_status = 'connected' LIMIT 1")
        ).fetchone()
        if result:
            return result[0]
    except Exception as e:
        logger.debug(f"No data broker user found: {e}")
    return None


def _check_active_trade_exit(analysis):
    """
    Given fresh analysis, decide if the active trade should be exited.
    Returns (should_exit, exit_reason) or (False, None).
    """
    global _active_trade
    if not _active_trade:
        return False, None

    now = _now_ist()
    entry_time = _active_trade.get('entry_time')

    # Time-based exit: 30 minutes
    if entry_time and (now - entry_time).total_seconds() >= TRADE_MAX_DURATION_MIN * 60:
        return True, f'Time limit reached ({TRADE_MAX_DURATION_MIN} min)'

    # Check if current option LTP has crossed SL or Target
    trades = analysis.get('trades', [])
    if trades and _active_trade.get('atm_key'):
        atm_key = _active_trade['atm_key']
        for t in trades:
            symbol = t.get('symbol', '')
            if symbol == atm_key:
                current_ltp = t.get('ltp', 0)
                sl = _active_trade.get('sl', 0)
                target = _active_trade.get('target', 0)
                if current_ltp > 0 and sl > 0 and current_ltp <= sl:
                    return True, f'Stop-loss hit (SL={sl:.0f}, LTP={current_ltp:.0f})'
                if current_ltp > 0 and target > 0 and current_ltp >= target:
                    return True, f'Target reached (Target={target:.0f}, LTP={current_ltp:.0f})'

    # Market hours ended
    if not _is_market_hours():
        return True, 'Market closed'

    return False, None


def _try_trigger_trade(analysis):
    """
    Check if conditions are strong enough to trigger (or continue confirming) a trade.
    Updates _trade_state, _confirmation_count, _active_trade.
    Returns signal_type to save ('TRADE_TRIGGER' | None).
    """
    global _trade_state, _confirmation_count, _confirmation_direction
    global _active_trade, _last_trade_exit_time

    direction = analysis.get('trade_direction', 'NEUTRAL')
    confidence = analysis.get('smoothed_confidence', analysis.get('confidence', 0))
    entry_mode = analysis.get('entry_mode', 'NO TRADE')
    adx = analysis.get('strength', {}).get('adx', 0)
    oi_signal = analysis.get('oi_analysis', {}).get('oi_signal', 'NEUTRAL')

    # Entry cooldown after previous trade
    now = _now_ist()
    if _last_trade_exit_time:
        elapsed = (now - _last_trade_exit_time).total_seconds()
        if elapsed < ENTRY_COOLDOWN_MINUTES * 60:
            remaining = int((ENTRY_COOLDOWN_MINUTES * 60 - elapsed) / 60)
            logger.info(f"Trade lifecycle: entry cooldown active, {remaining}m remaining")
            _confirmation_count = 0
            _confirmation_direction = None
            _trade_state = 'NONE'
            return None

    # Conditions for a valid signal
    strong_signal = (
        confidence >= TRADE_MIN_CONFIDENCE
        and entry_mode in ('CONFIRMED', 'EARLY')
        and direction != 'NEUTRAL'
        and adx >= TRADE_MIN_ADX
    )

    if not strong_signal:
        # Reset confirmation if direction changed or signal weakened
        if _confirmation_direction and direction != _confirmation_direction:
            logger.info(f"Trade lifecycle: direction changed ({_confirmation_direction}→{direction}), resetting")
            _confirmation_count = 0
            _confirmation_direction = None
            _trade_state = 'NONE'
        elif _confirmation_count > 0:
            logger.info(f"Trade lifecycle: signal weakened (conf={confidence}, adx={adx}), resetting")
            _confirmation_count = 0
            _confirmation_direction = None
            _trade_state = 'NONE'
        return None

    # Signal is strong — accumulate confirmations
    if _confirmation_direction != direction:
        _confirmation_count = 1
        _confirmation_direction = direction
        _trade_state = 'CONFIRMING'
        logger.info(f"Trade lifecycle: confirmation 1/{CONFIRMATION_CANDLES} for {direction} (conf={confidence}, ADX={adx})")
        return None

    _confirmation_count += 1
    logger.info(f"Trade lifecycle: confirmation {_confirmation_count}/{CONFIRMATION_CANDLES} for {direction}")

    if _confirmation_count >= CONFIRMATION_CANDLES:
        # Lock the trade
        trades = analysis.get('trades', [])
        atm_trade = next((t for t in trades if t.get('moneyness') == 'ATM'), trades[0] if trades else None)
        _active_trade = {
            'direction': direction,
            'entry_time': now,
            'entry_mode': entry_mode,
            'confidence': confidence,
            'spot': analysis.get('spot_price', 0),
            'atm_strike': analysis.get('atm_strike', 0),
            'atm_key': atm_trade.get('symbol', '') if atm_trade else '',
            'entry_price': atm_trade.get('entry_price', 0) if atm_trade else 0,
            'sl': atm_trade.get('sl', 0) if atm_trade else 0,
            'target': atm_trade.get('target', 0) if atm_trade else 0,
            'type': atm_trade.get('type', '') if atm_trade else '',
            'data_source': analysis.get('data_source', ''),
        }
        _trade_state = 'ACTIVE'
        _confirmation_count = 0
        _confirmation_direction = None
        logger.info(
            f"🔒 Trade LOCKED: {direction} {atm_trade.get('type','')} @ {_active_trade['entry_price']:.0f} "
            f"SL={_active_trade['sl']:.0f} Target={_active_trade['target']:.0f}"
        )
        return 'TRADE_TRIGGER'

    return None


def run_scan(app):
    if not _is_market_hours():
        return

    global _trade_state, _active_trade, _last_trade_exit_time
    global _last_signal_time, _last_signal_direction, _daily_signal_count

    try:
        with app.app_context():
            from services.nifty_options_engine import NiftyOptionsEngine
            data_broker_user_id = _get_data_broker_user_id()
            engine = NiftyOptionsEngine(user_id=data_broker_user_id)
            analysis = engine.generate_analysis()

        if not analysis:
            logger.warning("F&O scan: engine returned no analysis")
            return

        # Smooth confidence over last 3 scans
        raw_conf = analysis.get('confidence', 0)
        smoothed = _smoothed_confidence(raw_conf)
        analysis['smoothed_confidence'] = smoothed

        logger.info(
            f"F&O scan: spot={analysis.get('spot_price', 0):.2f} "
            f"dir={analysis.get('trade_direction', 'N/A')} "
            f"conf={raw_conf}(smooth={smoothed}) "
            f"mode={analysis.get('entry_mode', 'N/A')} "
            f"src={analysis.get('data_source', 'N/A')} "
            f"state={_trade_state}"
        )

        signal_type = None
        alert_sent = False

        with _state_lock:
            _reset_daily_if_needed()

            if _trade_state == 'ACTIVE':
                # Direction is locked — only check for exit
                should_exit, exit_reason = _check_active_trade_exit(analysis)
                if should_exit:
                    logger.info(f"🚪 Trade EXIT: {exit_reason}")
                    analysis['signal_type'] = 'TRADE_EXIT'
                    analysis['exit_reason'] = exit_reason
                    analysis['trade_direction'] = _active_trade.get('direction', analysis.get('trade_direction'))
                    analysis['confidence'] = _active_trade.get('confidence', analysis.get('confidence'))
                    # Restore trade info for the record
                    analysis['entry_price_locked'] = _active_trade.get('entry_price')
                    _last_trade_exit_time = _now_ist()
                    _active_trade = None
                    _trade_state = 'NONE'
                    signal_type = 'TRADE_EXIT'
                    if _should_send_alert(analysis):
                        alert_sent = _send_telegram_alert(analysis)
                else:
                    # Trade still active — don't spam DB, just log
                    logger.info(
                        f"Trade ACTIVE: {_active_trade.get('direction')} since {_active_trade.get('entry_time').strftime('%H:%M')} IST"
                    )
                    return

            else:
                # NONE or CONFIRMING — look for a new trade trigger
                trigger = _try_trigger_trade(analysis)
                if trigger == 'TRADE_TRIGGER':
                    signal_type = 'TRADE_TRIGGER'
                    analysis['signal_type'] = 'TRADE_TRIGGER'
                    if _should_send_alert(analysis):
                        alert_sent = _send_telegram_alert(analysis)
                        if alert_sent:
                            _last_signal_time = _now_ist()
                            _last_signal_direction = analysis.get('trade_direction')
                            _daily_signal_count += 1
                    # Fall through to save signal

                elif analysis.get('entry_mode') != 'NO TRADE' and smoothed >= 60:
                    # Qualified scan — save as a trade recommendation for history
                    signal_type = 'SCAN'
                    analysis['signal_type'] = 'SCAN'
                else:
                    # No-trade scan — skip DB entirely
                    return

        analysis['alert_sent'] = alert_sent
        if signal_type:
            _save_signal_to_db(app, analysis)

    except Exception as e:
        logger.error(f"F&O monitor scan error: {e}")


def start_scheduler(app):
    global _scheduler_started
    if _scheduler_started:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            run_scan,
            'interval',
            seconds=60,
            args=[app],
            id='fno_nifty_scan',
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler_started = True
        logger.info("F&O continuous monitor started (60s interval)")
    except Exception as e:
        logger.error(f"Failed to start F&O scheduler: {e}")


def get_monitor_status():
    _reset_daily_if_needed()
    active = None
    if _active_trade:
        now = _now_ist()
        entry_time = _active_trade.get('entry_time')
        elapsed_min = int((now - entry_time).total_seconds() / 60) if entry_time else 0
        remaining_min = max(0, TRADE_MAX_DURATION_MIN - elapsed_min)
        active = {
            'direction': _active_trade.get('direction'),
            'type': _active_trade.get('type'),
            'atm_strike': _active_trade.get('atm_strike'),
            'entry_price': _active_trade.get('entry_price'),
            'sl': _active_trade.get('sl'),
            'target': _active_trade.get('target'),
            'entry_time': entry_time.strftime('%I:%M %p') if entry_time else None,
            'elapsed_min': elapsed_min,
            'remaining_min': remaining_min,
            'confidence': _active_trade.get('confidence'),
            'data_source': _active_trade.get('data_source'),
        }

    cooldown_remaining = 0
    if _last_trade_exit_time:
        elapsed = (_now_ist() - _last_trade_exit_time).total_seconds()
        cooldown_remaining = max(0, int((ENTRY_COOLDOWN_MINUTES * 60 - elapsed) / 60))

    return {
        'running': _scheduler_started,
        'market_hours': _is_market_hours(),
        'last_signal_time': _last_signal_time.strftime('%I:%M %p') if _last_signal_time else None,
        'last_direction': _last_signal_direction,
        'signals_today': _daily_signal_count,
        'max_signals': MAX_SIGNALS_PER_DAY,
        'cooldown_minutes': SIGNAL_COOLDOWN_MINUTES,
        'confidence_threshold': ALERT_CONFIDENCE_THRESHOLD,
        'trade_state': _trade_state,
        'confirmation_count': _confirmation_count,
        'confirmation_needed': CONFIRMATION_CANDLES,
        'active_trade': active,
        'cooldown_remaining_min': cooldown_remaining,
        'entry_cooldown_min': ENTRY_COOLDOWN_MINUTES,
    }
