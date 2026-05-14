"""
Messaging Service for WhatsApp and Telegram Integration
Sends trading signals to group chats
"""
import os
import requests
import logging
from datetime import datetime, timezone

# Module-local logger only — do NOT call logging.basicConfig(), which would
# hijack the root logger config for the entire app.
logger = logging.getLogger(__name__)

# Hard timeout for all outbound messaging calls so a slow Telegram/WhatsApp
# API never blocks a gunicorn worker indefinitely.
HTTP_TIMEOUT_SECONDS = float(os.environ.get("MESSAGING_HTTP_TIMEOUT", "8"))

# WhatsApp Business API Configuration
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
WHATSAPP_GROUP_ID = os.environ.get('WHATSAPP_GROUP_ID')

# Telegram Bot Configuration — read PER-CALL via _get_telegram_config().
# Reading these at module-import time was a Railway production bug: on some
# Railway deployments env vars are populated *after* the gunicorn worker
# imports modules, so TELEGRAM_BOT_TOKEN ended up as an empty string for
# the lifetime of the process and every send silently returned False.
import re

def _get_telegram_config():
    """Resolve Telegram bot token + chat id at call time (not import time).

    Cleans the token (some users paste 'Bot 123:abc' or wrap it in quotes),
    and normalises the chat id (strips quotes / whitespace).
    Returns ``(token, chat_id)`` — either may be an empty string if missing.
    """
    raw_token = os.environ.get('TELEGRAM_BOT_TOKEN', '') or ''
    raw_chat  = os.environ.get('TELEGRAM_CHAT_ID', '') or ''
    m = re.search(r'(\d+:[A-Za-z0-9_-]+)', raw_token)
    token = m.group(1) if m else raw_token.strip().strip('"').strip("'")
    chat_id = raw_chat.strip().strip('"').strip("'")
    return token, chat_id

# Backwards-compatible accessors — kept for any callers that imported these
# names directly. Now they reflect the *current* env, not the import-time env.
def __getattr__(name):
    if name == 'TELEGRAM_BOT_TOKEN':
        return _get_telegram_config()[0]
    if name == 'TELEGRAM_CHAT_ID':
        return _get_telegram_config()[1]
    raise AttributeError(name)

def send_whatsapp_message(message_text):
    """Send message to WhatsApp group"""
    try:
        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
            logger.warning("WhatsApp credentials not configured")
            return False
            
        url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
        
        headers = {
            'Authorization': f'Bearer {WHATSAPP_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        # Format message for WhatsApp
        formatted_message = format_message_for_whatsapp(message_text)
        
        payload = {
            'messaging_product': 'whatsapp',
            'to': WHATSAPP_GROUP_ID,  # Group ID or individual number
            'type': 'text',
            'text': {
                'body': formatted_message
            }
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        
        if response.status_code == 200:
            logger.info("WhatsApp message sent successfully")
            return True
        else:
            logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        return False

def send_telegram_message(message_text, parse_mode='Markdown'):
    """Send a message to the configured Telegram chat.

    ``parse_mode`` may be 'Markdown', 'MarkdownV2', 'HTML', or None. Pass
    'HTML' when the message body already contains HTML tags (used by the
    daily-signal formatter so it matches the F&O alert style).
    """
    try:
        token, chat_id = _get_telegram_config()
        if not token or not chat_id:
            logger.warning(
                "Telegram credentials not configured "
                f"(token_present={bool(token)}, chat_id_present={bool(chat_id)})"
            )
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        # Only run the Markdown beautifier when the caller asked for Markdown.
        if parse_mode == 'Markdown':
            text_to_send = format_message_for_telegram(message_text)
        else:
            text_to_send = message_text

        payload = {
            'chat_id': chat_id,
            'text': text_to_send,
            'disable_web_page_preview': True,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode

        response = requests.post(url, json=payload, timeout=HTTP_TIMEOUT_SECONDS)

        if response.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        logger.error(f"Telegram API error: {response.status_code} - {response.text}")
        return False

    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Daily Signal → Telegram (F&O-style formatter)
# ─────────────────────────────────────────────────────────────────────────────
def format_daily_signal_telegram(signal) -> str:
    """Format a `DailyTradingSignal` as an HTML Telegram message that matches
    the visual style of the F&O monitor alert (`services/fno_monitor.py`).
    """
    asset    = (signal.asset_type or '').upper()
    sub      = (signal.sub_type or '').upper()
    action   = (signal.action or 'BUY').upper()
    duration = (signal.trade_duration or '').upper()
    risk     = (signal.risk_level or 'MEDIUM').upper()

    # Direction emoji
    if sub == 'CE' or action == 'BUY':
        dir_emoji = '🟢'
    elif sub == 'PE' or action == 'SELL':
        dir_emoji = '🔴'
    else:
        dir_emoji = '🟡'

    type_emoji  = '📡'  # "Our Signal" — broadcast
    duration_lbl = {'DAY': 'Intraday', 'WEEK': 'Swing', 'MONTH': 'Long Term'}.get(duration, duration or '—')

    # Targets list
    targets = []
    for px, label in ((signal.target_1, 'T1'), (signal.target_2, 'T2'), (signal.target_3, 'T3')):
        if px is not None:
            try:
                targets.append(f"{label} ₹{float(px):,.2f}")
            except (TypeError, ValueError):
                pass

    msg  = f"{type_emoji} <b>Our Signal #{signal.signal_number} — {asset}</b>\n\n"
    msg += f"{dir_emoji} <b>Action:</b> {action} <code>{signal.script}</code>\n"
    msg += f"⏳ <b>Duration:</b> {duration_lbl}\n"
    msg += f"📊 <b>Strategy:</b> {signal.strategy_name or 'Trend Following'}\n"
    msg += f"⚠️ <b>Risk:</b> {risk}\n\n"

    if signal.current_price:
        try:
            msg += f"💹 <b>LTP:</b> ₹{float(signal.current_price):,.2f}\n"
        except (TypeError, ValueError):
            pass

    msg += f"💰 <b>Entry:</b> ₹{float(signal.buy_above):,.2f}\n"
    msg += f"🛑 <b>Stop Loss:</b> ₹{float(signal.stop_loss):,.2f}\n"
    if targets:
        msg += f"🎯 <b>Targets:</b> {' / '.join(targets)}\n"

    if signal.notes:
        notes = signal.notes if len(signal.notes) <= 200 else signal.notes[:200] + '…'
        msg += f"\n📝 <i>{notes}</i>\n"

    msg += "\n<i>Place SL-Limit orders to avoid slippage on fast moves.</i>\n"
    msg += f"\n⏰ <i>{datetime.now(timezone.utc).strftime('%d/%m/%Y %I:%M %p')} UTC</i>"
    msg += "\n\n<a href='https://www.targetcapital.ai/dashboard/live-market-pulse'>View on Target Capital</a>"
    return msg


def send_daily_signal_telegram(signal) -> bool:
    """Render a daily signal as an F&O-style HTML message and broadcast it.

    Updates ``signal.shared_telegram`` + ``signal.telegram_shared_at`` on
    success and commits the change.
    """
    try:
        body = format_daily_signal_telegram(signal)
        ok = send_telegram_message(body, parse_mode='HTML')
        if ok:
            from app import db
            signal.shared_telegram = True
            signal.telegram_shared_at = datetime.utcnow()
            db.session.commit()
        return ok
    except Exception as e:
        logger.error(f"send_daily_signal_telegram failed for signal id={getattr(signal, 'id', None)}: {e}")
        return False


def telegram_diagnostics() -> dict:
    """Return a non-secret diagnostic snapshot of the current Telegram config.

    Used by the Admin → Telegram page so an operator can see *why* sends are
    failing in production (missing env, malformed token, wrong chat id, etc.)
    without ever exposing the raw credentials.
    """
    token, chat_id = _get_telegram_config()
    info = {
        'token_present':   bool(token),
        'token_format_ok': bool(re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token or '')),
        'token_preview':   (f"{token[:4]}…{token[-4:]}" if token and len(token) > 8 else ''),
        'chat_id_present': bool(chat_id),
        'chat_id_preview': chat_id if chat_id else '',
        'bot_username':    None,
        'bot_reachable':   False,
        'error':           None,
    }
    if not info['token_present'] or not info['token_format_ok']:
        info['error'] = 'TELEGRAM_BOT_TOKEN missing or malformed'
        return info
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if r.status_code == 200 and r.json().get('ok'):
            info['bot_reachable'] = True
            info['bot_username']  = r.json().get('result', {}).get('username')
        else:
            info['error'] = f"getMe failed: HTTP {r.status_code} — {r.text[:200]}"
    except Exception as e:
        info['error'] = f"getMe exception: {e}"
    return info

def format_message_for_whatsapp(message):
    """Format trading signal message for WhatsApp"""
    # WhatsApp supports basic formatting
    formatted = message.replace('🚨', '*')
    formatted = formatted.replace('💰', '₹')
    formatted = formatted.replace('🎯', 'Target:')
    formatted = formatted.replace('🛑', 'SL:')
    
    return formatted

def format_message_for_telegram(message):
    """Format trading signal message for Telegram with Markdown"""
    # Convert to Telegram Markdown format
    formatted = message.replace('🚨 NEW TRADING SIGNAL 🚨', '*🚨 NEW TRADING SIGNAL 🚨*')
    
    # Make important fields bold
    formatted = formatted.replace('Symbol:', '*Symbol:*')
    formatted = formatted.replace('Action:', '*Action:*')
    formatted = formatted.replace('Type:', '*Type:*')
    formatted = formatted.replace('Strategy:', '*Strategy:*')
    formatted = formatted.replace('Risk Level:', '*Risk Level:*')
    
    return formatted

def send_signal_notification(signal):
    """Send trading signal to both WhatsApp and Telegram"""
    try:
        # Format comprehensive signal message
        message = f"""🚨 NEW TRADING SIGNAL 🚨

Symbol: {signal.symbol}
{f"Company: {signal.company_name}" if signal.company_name else ""}
Action: {signal.action}
Type: {signal.signal_type.replace('_', ' ').title()}

💰 Entry: ₹{signal.entry_price or 'Market Price'}
🎯 Target: ₹{signal.target_price or 'TBD'}
🛑 Stop Loss: ₹{signal.stop_loss or 'TBD'}

{f"Quantity: {signal.quantity}" if signal.quantity else ""}
{f"Time Frame: {signal.time_frame}" if signal.time_frame else ""}
{f"Strategy: {signal.strategy_name}" if signal.strategy_name else ""}
Risk Level: {signal.risk_level or 'Medium'}

{f"Notes: {signal.notes[:100]}..." if signal.notes else ""}

⚠️ Trade at your own risk. This is for educational purposes only.

- Target Capital Team
Generated: {datetime.now(timezone.utc).strftime('%d/%m/%Y %I:%M %p')}"""

        # Send to both platforms
        whatsapp_sent = send_whatsapp_message(message)
        telegram_sent = send_telegram_message(message)
        
        # Update signal record
        if whatsapp_sent:
            signal.sent_to_whatsapp = True
        if telegram_sent:
            signal.sent_to_telegram = True
            
        # Commit changes
        from app import db
        db.session.commit()
        
        logger.info(f"Signal notification sent - WhatsApp: {whatsapp_sent}, Telegram: {telegram_sent}")
        return whatsapp_sent or telegram_sent
        
    except Exception as e:
        logger.error(f"Error sending signal notification: {e}")
        return False

def send_signup_notification(user):
    """Send notification email for new user signup"""
    try:
        from app import app
        from flask_mail import Message, Mail
        
        # Check if mail is configured
        if not app.config.get('MAIL_SERVER'):
            logger.warning("Mail server not configured for signup notifications")
            return False
            
        mail = Mail(app)
        msg = Message(
            subject="New User Signup: Target Capital",
            recipients=["uday@targetcapital.ai"],
            body=f"Hello Uday,\n\nA new user has just signed up on Target Capital!\n\nUser Details:\nName: {user.first_name} {user.last_name}\nUsername: {user.username}\nEmail: {user.email}\nSignup Time: {datetime.now(timezone.utc).strftime('%d/%m/%Y %I:%M %p')}\n\nBest regards,\nTarget Capital System"
        )
        mail.send(msg)
        logger.info(f"Signup notification sent for user {user.email}")
        return True
    except Exception as e:
        logger.error(f"Error sending signup notification: {e}")
        return False

def test_messaging_setup():
    """Test messaging configuration"""
    test_message = "🧪 Test message from Target Capital Admin\n\nThis is a test to verify messaging setup is working correctly."
    
    print("Testing messaging setup...")
    
    # Test WhatsApp
    whatsapp_result = send_whatsapp_message(test_message)
    print(f"WhatsApp test: {'✅ Success' if whatsapp_result else '❌ Failed'}")
    
    # Test Telegram
    telegram_result = send_telegram_message(test_message)
    print(f"Telegram test: {'✅ Success' if telegram_result else '❌ Failed'}")
    
    return whatsapp_result, telegram_result

if __name__ == "__main__":
    # Test the messaging setup
    test_messaging_setup()