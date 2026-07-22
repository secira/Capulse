"""
Email service for Capulse — uses Flask-Mail (Gmail SMTP).
All methods fail gracefully when credentials are not configured.
"""
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_mail():
    """Return the Flask-Mail instance lazily to avoid circular imports."""
    from app import mail
    return mail


def _send_async(app, msg):
    """Send a mail message in a background thread."""
    try:
        from flask_mail import Message as _Msg  # noqa
        with app.app_context():
            _get_mail().send(msg)
    except Exception as e:
        logger.error(f"Async email send failed: {e}")


def _send(subject, recipients, html_body, text_body=None, async_send=True):
    """
    Low-level helper. Sends to a list of recipients.
    Returns True on success (or when queued async), False on config error.
    """
    try:
        from flask_mail import Message
        from flask import current_app

        app_cfg = current_app._get_current_object().config
        if not app_cfg.get('MAIL_USERNAME'):
            logger.warning("Email skipped — MAIL_USERNAME not configured.")
            return False

        msg = Message(
            subject=subject,
            recipients=recipients if isinstance(recipients, list) else [recipients],
            html=html_body,
            body=text_body or '',
        )

        if async_send:
            app_obj = current_app._get_current_object()
            t = threading.Thread(target=_send_async, args=(app_obj, msg), daemon=True)
            t.start()
            return True
        else:
            _get_mail().send(msg)
            return True

    except Exception as e:
        logger.error(f"Email send error (subject='{subject}'): {e}")
        return False


# ── Shared brand layout ────────────────────────────────────────────────────────
def _wrap(title: str, body_html: str) -> str:
    year = datetime.utcnow().year
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:32px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
      <!-- Header -->
      <tr>
        <td style="background:#00091a;padding:28px 36px;">
          <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:0.5px;">
            🎯 Capulse
          </h1>
          <p style="margin:4px 0 0;color:#a0aec0;font-size:13px;">Scentric AI Decision Engine</p>
        </td>
      </tr>
      <!-- Body -->
      <tr>
        <td style="padding:36px;">
          {body_html}
        </td>
      </tr>
      <!-- Footer -->
      <tr>
        <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 36px;text-align:center;">
          <p style="margin:0;color:#718096;font-size:12px;">
            © {year} Capulse · Scentric Networks Pvt. Ltd.<br>
            This is an automated message — please do not reply.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


# ── 1. OTP Email ───────────────────────────────────────────────────────────────
def send_otp_email(to_email: str, otp: str, purpose: str = 'login') -> bool:
    purpose_label = 'Login Verification' if purpose == 'login' else 'Registration Verification'
    body = f"""
    <h2 style="color:#00091a;margin:0 0 16px;">Your OTP Code</h2>
    <p style="color:#4a5568;margin:0 0 24px;">
      Use the code below to complete your <strong>{purpose_label}</strong>.
      It expires in <strong>10 minutes</strong>.
    </p>
    <div style="text-align:center;margin:28px 0;">
      <span style="display:inline-block;background:#00091a;color:#ffffff;
                   font-size:36px;font-weight:800;letter-spacing:10px;
                   padding:16px 32px;border-radius:8px;">{otp}</span>
    </div>
    <p style="color:#718096;font-size:13px;margin:0;">
      If you did not request this OTP, please ignore this email.
    </p>
    """
    return _send(
        subject=f"Your Capulse OTP: {otp}",
        recipients=[to_email],
        html_body=_wrap("OTP Verification — Capulse", body),
    )


# ── 2. Welcome Email ───────────────────────────────────────────────────────────
def send_welcome_email(user) -> bool:
    name = (getattr(user, 'first_name', '') or '').strip() or (getattr(user, 'username', '') or 'Trader')
    body = f"""
    <h2 style="color:#00091a;margin:0 0 8px;">Welcome to Capulse, {name}!</h2>
    <p style="color:#4a5568;margin:0 0 20px;">
      Your account is active and your <strong>14-day free trial</strong> has started.
      You have full access to every feature — no credit card needed.
    </p>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
      <tr>
        <td style="padding:12px;background:#f0f4ff;border-radius:8px;border-left:4px solid #00091a;">
          <p style="margin:0;font-weight:600;color:#00091a;">What you can do right now:</p>
          <ul style="margin:8px 0 0 0;padding-left:20px;color:#4a5568;">
            <li>Run an <strong>I-Score analysis</strong> on any NSE stock</li>
            <li>Check live <strong>F&amp;O signals</strong> (NIFTY Options)</li>
            <li>Connect your broker for <strong>portfolio analytics</strong></li>
            <li>Ask the <strong>AI Research Assistant</strong> anything</li>
          </ul>
        </td>
      </tr>
    </table>
    <div style="text-align:center;margin:28px 0;">
      <a href="https://capulse.tech/dashboard"
         style="display:inline-block;background:#00091a;color:#ffffff;
                text-decoration:none;padding:14px 32px;border-radius:6px;
                font-weight:600;font-size:15px;">
        Go to Dashboard →
      </a>
    </div>
    <p style="color:#718096;font-size:13px;margin:0;">
      Questions? Reach us at <a href="mailto:support@capulse.tech" style="color:#3182ce;">support@capulse.tech</a>
    </p>
    """
    return _send(
        subject="Welcome to Capulse — Your AI Trading Co-Pilot is Ready",
        recipients=[user.email],
        html_body=_wrap("Welcome — Capulse", body),
    )


# ── 3. Password Reset Email ────────────────────────────────────────────────────
def send_password_reset_email(to_email: str, reset_url: str, name: str = '') -> bool:
    greeting = f"Hi {name}," if name else "Hi,"
    body = f"""
    <h2 style="color:#00091a;margin:0 0 8px;">Reset Your Password</h2>
    <p style="color:#4a5568;margin:0 0 20px;">
      {greeting} We received a request to reset the password for your Capulse account.
      Click the button below to set a new password. This link is valid for <strong>1 hour</strong>.
    </p>
    <div style="text-align:center;margin:28px 0;">
      <a href="{reset_url}"
         style="display:inline-block;background:#dc2626;color:#ffffff;
                text-decoration:none;padding:14px 32px;border-radius:6px;
                font-weight:600;font-size:15px;">
        Reset Password →
      </a>
    </div>
    <p style="color:#4a5568;font-size:13px;margin:0 0 8px;">
      Or copy this link into your browser:
    </p>
    <p style="word-break:break-all;font-size:12px;color:#718096;background:#f8fafc;
              padding:10px;border-radius:4px;">{reset_url}</p>
    <p style="color:#718096;font-size:13px;margin:16px 0 0;">
      If you did not request a password reset, please ignore this email —
      your account remains secure.
    </p>
    """
    return _send(
        subject="Password Reset Request — Capulse",
        recipients=[to_email],
        html_body=_wrap("Password Reset — Capulse", body),
    )


# ── 4. I-Score Report Email ────────────────────────────────────────────────────
def send_iscore_report_email(to_email: str, symbol: str, result: dict) -> bool:
    score = result.get('iscore') or result.get('overall_score') or 0
    rec   = result.get('recommendation', 'HOLD')
    summary = result.get('summary', '')
    comps   = result.get('components', {})

    rec_color = {
        'STRONG BUY': '#16a34a', 'BUY': '#22c55e',
        'HOLD': '#d97706', 'SELL': '#dc2626', 'STRONG SELL': '#991b1b',
    }.get(rec.upper(), '#4a5568')

    comp_rows = ''
    comp_map = {
        'quantitative': ('📊', 'Quantitative Technical', '30%'),
        'trend':        ('📈', 'Trend Analysis',          '20%'),
        'risk':         ('🛡️', 'Risk Assessment',         '20%'),
        'qualitative':  ('🤖', 'AI Sentiment',            '15%'),
        'search':       ('🔍', 'Search Sentiment',        '10%'),
        'market_context': ('🌍', 'Market Context',        '5%'),
    }
    for key, (icon, label, weight) in comp_map.items():
        c = comps.get(key, {})
        s = c.get('score')
        if s is not None:
            bar_w = max(0, min(100, int(s)))
            bar_color = '#16a34a' if s >= 63 else '#d97706' if s >= 42 else '#dc2626'
            comp_rows += f"""
            <tr>
              <td style="padding:8px 0;color:#4a5568;font-size:13px;">{icon} {label} <span style="color:#9ca3af;font-size:11px;">({weight})</span></td>
              <td style="padding:8px 0;text-align:right;font-weight:600;color:{bar_color};">{s:.0f}</td>
            </tr>"""

    body = f"""
    <h2 style="color:#00091a;margin:0 0 4px;">I-Score Report: {symbol}</h2>
    <p style="color:#718096;font-size:13px;margin:0 0 24px;">Generated on {datetime.utcnow().strftime('%d %b %Y at %H:%M UTC')}</p>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;background:#f8fafc;border-radius:8px;overflow:hidden;">
      <tr>
        <td style="padding:20px;text-align:center;border-right:1px solid #e2e8f0;">
          <p style="margin:0;color:#718096;font-size:12px;text-transform:uppercase;letter-spacing:1px;">I-Score</p>
          <p style="margin:4px 0 0;font-size:42px;font-weight:800;color:#00091a;">{score:.0f}</p>
          <p style="margin:4px 0 0;font-size:11px;color:#9ca3af;">out of 100</p>
        </td>
        <td style="padding:20px;text-align:center;">
          <p style="margin:0;color:#718096;font-size:12px;text-transform:uppercase;letter-spacing:1px;">Recommendation</p>
          <p style="margin:8px 0 0;display:inline-block;background:{rec_color};color:#fff;
                    padding:6px 16px;border-radius:20px;font-weight:700;font-size:15px;">{rec}</p>
        </td>
      </tr>
    </table>

    {f'<p style="color:#4a5568;margin:0 0 24px;line-height:1.6;">{summary}</p>' if summary else ''}

    <h3 style="color:#00091a;font-size:15px;margin:0 0 12px;border-bottom:1px solid #e2e8f0;padding-bottom:8px;">Component Breakdown</h3>
    <table width="100%" cellpadding="0" cellspacing="0">
      {comp_rows}
    </table>

    <div style="margin-top:28px;text-align:center;">
      <a href="https://capulse.tech/dashboard/research/stocks"
         style="display:inline-block;background:#00091a;color:#ffffff;
                text-decoration:none;padding:12px 28px;border-radius:6px;
                font-weight:600;font-size:14px;">
        View Full Analysis →
      </a>
    </div>
    """
    return _send(
        subject=f"I-Score Report: {symbol} — {rec} ({score:.0f}/100)",
        recipients=[to_email],
        html_body=_wrap(f"I-Score: {symbol} — Capulse", body),
    )


# ── 5. Trade Alert Email ───────────────────────────────────────────────────────
def send_trade_alert_email(to_emails, signal_data: dict, index_id: str = 'NIFTY') -> bool:
    if not to_emails:
        return False
    direction  = signal_data.get('trade_direction', 'N/A')
    confidence = signal_data.get('confidence', 0)
    sig_type   = signal_data.get('signal_type', 'SCAN')
    trades     = signal_data.get('trades', [])
    trade_code = signal_data.get('trade_code', '')

    dir_color = '#16a34a' if 'BULL' in direction.upper() else '#dc2626' if 'BEAR' in direction.upper() else '#d97706'

    trade_rows = ''
    for t in trades[:3]:
        trade_rows += f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:8px 4px;font-size:13px;color:#4a5568;">{t.get('option_type','')}&nbsp;{t.get('strike','')}</td>
          <td style="padding:8px 4px;font-size:13px;color:#4a5568;text-align:center;">{t.get('action','')}</td>
          <td style="padding:8px 4px;font-size:13px;color:#4a5568;text-align:right;">₹{t.get('ltp',0):.0f}</td>
        </tr>"""

    badge = f'<span style="background:{dir_color};color:#fff;padding:4px 12px;border-radius:12px;font-weight:700;">{direction}</span>'
    body = f"""
    <h2 style="color:#00091a;margin:0 0 4px;">F&amp;O Signal Alert — {index_id}</h2>
    <p style="color:#718096;font-size:13px;margin:0 0 20px;">{sig_type} · {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}</p>

    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:8px;margin:0 0 24px;">
      <tr>
        <td style="padding:16px;text-align:center;border-right:1px solid #e2e8f0;">{badge}</td>
        <td style="padding:16px;text-align:center;">
          <p style="margin:0;color:#718096;font-size:12px;">Confidence</p>
          <p style="margin:4px 0 0;font-size:26px;font-weight:800;color:#00091a;">{confidence:.0f}%</p>
        </td>
        {f'<td style="padding:16px;text-align:center;border-left:1px solid #e2e8f0;"><p style="margin:0;color:#718096;font-size:12px;">Trade Code</p><p style="margin:4px 0 0;font-size:14px;font-weight:700;color:#00091a;font-family:monospace;">{trade_code}</p></td>' if trade_code else ''}
      </tr>
    </table>

    {f'''<h3 style="color:#00091a;font-size:14px;margin:0 0 8px;">Recommended Options</h3>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
      <tr style="background:#f8fafc;">
        <th style="padding:8px 4px;font-size:12px;color:#718096;text-align:left;">Option</th>
        <th style="padding:8px 4px;font-size:12px;color:#718096;text-align:center;">Action</th>
        <th style="padding:8px 4px;font-size:12px;color:#718096;text-align:right;">LTP</th>
      </tr>
      {trade_rows}
    </table>''' if trade_rows else ''}

    <p style="color:#718096;font-size:12px;margin:0;border-top:1px solid #e2e8f0;padding-top:16px;">
      ⚠️ This is an AI-generated signal for informational purposes only. Always trade with proper risk management.
    </p>
    """
    return _send(
        subject=f"🎯 {index_id} Signal: {direction} | Confidence {confidence:.0f}%",
        recipients=to_emails if isinstance(to_emails, list) else [to_emails],
        html_body=_wrap(f"{index_id} F&O Alert — Capulse", body),
    )


# ── 6. Subscription Update Email ──────────────────────────────────────────────
PLAN_DISPLAY = {
    'FREE': 'Starter (Free)',
    'TARGET_PLUS': 'Growth',
    'TARGET_PRO': 'Pro',
    'HNI': 'Elite',
}

def send_subscription_update_email(user, old_plan: str, new_plan: str) -> bool:
    name = (getattr(user, 'first_name', '') or '').strip() or 'Trader'
    old_label = PLAN_DISPLAY.get(old_plan.upper(), old_plan)
    new_label = PLAN_DISPLAY.get(new_plan.upper(), new_plan)
    end_date  = getattr(user, 'subscription_end_date', None)
    end_str   = end_date.strftime('%d %b %Y') if end_date else 'N/A'

    is_upgrade = new_plan.upper() != 'FREE'
    subject = (
        f"Subscription Upgraded to {new_label} — Capulse"
        if is_upgrade else
        f"Your Capulse Trial Has Ended"
    )
    action_text = "upgraded to" if is_upgrade else "changed to"

    body = f"""
    <h2 style="color:#00091a;margin:0 0 8px;">
      {'🎉 Subscription Upgraded!' if is_upgrade else '⏰ Trial Ended'}
    </h2>
    <p style="color:#4a5568;margin:0 0 20px;">
      Hi {name}, your plan has been {action_text} <strong>{new_label}</strong>
      (previously <em>{old_label}</em>).
    </p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:8px;margin:0 0 24px;">
      <tr>
        <td style="padding:16px;border-right:1px solid #e2e8f0;text-align:center;">
          <p style="margin:0;color:#718096;font-size:12px;">New Plan</p>
          <p style="margin:6px 0 0;font-size:18px;font-weight:700;color:#00091a;">{new_label}</p>
        </td>
        <td style="padding:16px;text-align:center;">
          <p style="margin:0;color:#718096;font-size:12px;">{'Valid Until' if is_upgrade else 'Expired'}</p>
          <p style="margin:6px 0 0;font-size:18px;font-weight:700;color:#00091a;">{end_str}</p>
        </td>
      </tr>
    </table>
    <div style="text-align:center;margin:24px 0;">
      <a href="{'https://capulse.tech/dashboard' if is_upgrade else 'https://capulse.tech/pricing'}"
         style="display:inline-block;background:#00091a;color:#ffffff;
                text-decoration:none;padding:14px 32px;border-radius:6px;
                font-weight:600;font-size:15px;">
        {'Go to Dashboard →' if is_upgrade else 'View Plans →'}
      </a>
    </div>
    <p style="color:#718096;font-size:13px;margin:0;">
      Questions? <a href="mailto:support@capulse.tech" style="color:#3182ce;">Contact support</a>
    </p>
    """
    return _send(
        subject=subject,
        recipients=[user.email],
        html_body=_wrap("Subscription Update — Capulse", body),
    )
