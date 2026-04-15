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

_state_lock = Lock()
_last_signal_time = None
_last_signal_direction = None
_daily_signal_count = 0
_daily_date = None
_scheduler_started = False


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
                'signal_type': signal_data.get('signal_type', 'SCAN'),
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

        dir_emoji = '🟢' if direction == 'BULLISH' else '🔴' if direction == 'BEARISH' else '🟡'

        msg = f"🔔 <b>NIFTY F&amp;O Signal Alert</b>\n\n"
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
            logger.info("F&O Telegram alert sent successfully")
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
    entry_mode = signal_data.get('entry_mode', 'NO TRADE')

    if confidence < ALERT_CONFIDENCE_THRESHOLD:
        return False

    if entry_mode == 'NO TRADE':
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


def run_scan(app):
    if not _is_market_hours():
        return

    try:
        with app.app_context():
            from services.nifty_options_engine import NiftyOptionsEngine
            data_broker_user_id = _get_data_broker_user_id()
            engine = NiftyOptionsEngine(user_id=data_broker_user_id)
            analysis = engine.generate_analysis()

        if not analysis:
            logger.warning("F&O scan: engine returned no analysis")
            return

        logger.info(
            f"F&O scan: spot={analysis.get('spot_price', 0):.2f} "
            f"dir={analysis.get('trade_direction', 'N/A')} "
            f"conf={analysis.get('confidence', 0)} "
            f"mode={analysis.get('entry_mode', 'N/A')} "
            f"src={analysis.get('data_source', 'N/A')}"
        )

        analysis['signal_type'] = 'SCAN'
        alert_sent = False

        with _state_lock:
            global _last_signal_time, _last_signal_direction, _daily_signal_count

            if _should_send_alert(analysis):
                alert_sent = _send_telegram_alert(analysis)
                if alert_sent:
                    _last_signal_time = _now_ist()
                    _last_signal_direction = analysis.get('trade_direction')
                    _daily_signal_count += 1
                    logger.info(f"F&O alert #{_daily_signal_count} sent: {analysis.get('trade_direction')} conf={analysis.get('confidence')}")

        analysis['alert_sent'] = alert_sent
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
    return {
        'running': _scheduler_started,
        'market_hours': _is_market_hours(),
        'last_signal_time': _last_signal_time.strftime('%I:%M %p') if _last_signal_time else None,
        'last_direction': _last_signal_direction,
        'signals_today': _daily_signal_count,
        'max_signals': MAX_SIGNALS_PER_DAY,
        'cooldown_minutes': SIGNAL_COOLDOWN_MINUTES,
        'confidence_threshold': ALERT_CONFIDENCE_THRESHOLD,
    }
