"""
System Health Monitor — powers /admin/notifications

Runs live probes against every external dependency the platform relies on:
  • Telegram bot      (token + chat_id reachable via getMe)
  • Primary data API  (AdminDataBroker priority=1)
  • Secondary data API(AdminDataBroker priority=2)
  • yfinance fallback (importable + sample request)
  • LLM providers     (Anthropic, OpenAI, Perplexity)
  • Billing           (Razorpay)
  • Comms             (Twilio)
  • TC Execution Engine (healthz + version)
  • Nightly schedules (alert_schedule table — enabled/disabled)

Each probe returns:
  {
    'key':       short id,
    'name':      human label,
    'status':    'ok' | 'warn' | 'fail' | 'disabled',
    'message':   one-line summary,
    'detail':    optional dict with raw values,
    'category':  'messaging' | 'data' | 'llm' | 'billing' | 'engine' | 'jobs',
    'severity':  'critical' | 'high' | 'medium' | 'low',
  }

Every probe is wrapped in try/except — a failure in one check NEVER prevents
the others from running. Network calls have a hard 5 s timeout.
"""
from __future__ import annotations

import os
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 5  # seconds — applies to every outbound probe


def _safe(fn, *a, **kw):
    """Run a probe, never raise. Returns the probe dict or a failure card."""
    try:
        return fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health probe %s failed: %s", getattr(fn, '__name__', '?'), exc)
        return {
            'key': getattr(fn, '__name__', 'unknown'),
            'name': getattr(fn, '__name__', 'unknown').replace('_check_', '').replace('_', ' ').title(),
            'status': 'fail',
            'message': f'Probe raised: {exc}',
            'category': 'system',
            'severity': 'medium',
        }


# ── 1. Telegram ──────────────────────────────────────────────────────────────
def _check_telegram() -> Dict[str, Any]:
    from services.messaging_service import _get_telegram_config
    token, chat_id = _get_telegram_config()
    if not token or not chat_id:
        return {
            'key': 'telegram',
            'name': 'Telegram Bot',
            'status': 'fail',
            'message': (
                f"Credentials missing — token={'set' if token else 'MISSING'}, "
                f"chat_id={'set' if chat_id else 'MISSING'}. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            ),
            'category': 'messaging',
            'severity': 'critical',
        }
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=HTTP_TIMEOUT)
        if r.status_code == 200 and r.json().get('ok'):
            info = r.json().get('result', {})
            return {
                'key': 'telegram',
                'name': 'Telegram Bot',
                'status': 'ok',
                'message': f"Connected as @{info.get('username', '?')} ({info.get('first_name', '')})",
                'detail': {'bot_username': info.get('username'), 'chat_id_configured': bool(chat_id)},
                'category': 'messaging',
                'severity': 'critical',
            }
        return {
            'key': 'telegram',
            'name': 'Telegram Bot',
            'status': 'fail',
            'message': f"getMe returned {r.status_code} — token may be invalid or revoked.",
            'category': 'messaging',
            'severity': 'critical',
        }
    except requests.RequestException as e:
        return {
            'key': 'telegram',
            'name': 'Telegram Bot',
            'status': 'fail',
            'message': f"Cannot reach Telegram API: {e}",
            'category': 'messaging',
            'severity': 'critical',
        }


# ── 2 + 3. Admin data brokers (primary + secondary) ─────────────────────────
def _check_admin_data_broker(priority: int) -> Dict[str, Any]:
    from models_broker import AdminDataBroker
    label = 'Primary' if priority == 1 else 'Secondary'
    row = AdminDataBroker.query.filter_by(priority=priority).first()
    if not row:
        return {
            'key': f'admin_data_{priority}',
            'name': f'{label} Data Broker',
            'status': 'disabled',
            'message': 'Not configured. Add it from Admin → Data Sources & API Plan.',
            'category': 'data',
            'severity': 'high' if priority == 1 else 'medium',
        }
    if not row.is_active:
        return {
            'key': f'admin_data_{priority}',
            'name': f'{label} Data Broker ({row.broker_name})',
            'status': 'disabled',
            'message': 'Configured but marked inactive.',
            'category': 'data',
            'severity': 'medium',
        }
    has_creds = bool(row.api_key or row.access_token)
    if not has_creds:
        return {
            'key': f'admin_data_{priority}',
            'name': f'{label} Data Broker ({row.broker_name})',
            'status': 'fail',
            'message': 'No api_key / access_token stored.',
            'category': 'data',
            'severity': 'high' if priority == 1 else 'medium',
        }
    last = row.last_connected.strftime('%d %b %Y %H:%M UTC') if row.last_connected else 'never'
    if (row.connection_status or '').lower() == 'connected':
        return {
            'key': f'admin_data_{priority}',
            'name': f'{label} Data Broker ({row.broker_name})',
            'status': 'ok',
            'message': f"{row.broker_type.upper()} connected. Last verified: {last}.",
            'detail': {'broker_type': row.broker_type, 'priority': priority},
            'category': 'data',
            'severity': 'high' if priority == 1 else 'medium',
        }
    return {
        'key': f'admin_data_{priority}',
        'name': f'{label} Data Broker ({row.broker_name})',
        'status': 'warn',
        'message': f"Status: {row.connection_status or 'unknown'}. Last connected: {last}.",
        'category': 'data',
        'severity': 'high' if priority == 1 else 'medium',
    }


# ── 4. yfinance fallback ────────────────────────────────────────────────────
def _check_yfinance() -> Dict[str, Any]:
    try:
        import yfinance as yf  # noqa: F401
    except ImportError:
        return {
            'key': 'yfinance',
            'name': 'yfinance Fallback',
            'status': 'fail',
            'message': 'yfinance package not installed.',
            'category': 'data',
            'severity': 'medium',
        }
    try:
        t = yf.Ticker('^NSEI')
        info = t.history(period='1d', timeout=HTTP_TIMEOUT)
        if info is not None and not info.empty:
            return {
                'key': 'yfinance',
                'name': 'yfinance Fallback',
                'status': 'ok',
                'message': f"Reachable. Last NIFTY close from yfinance: ₹{float(info['Close'].iloc[-1]):,.2f}.",
                'category': 'data',
                'severity': 'low',
            }
        return {
            'key': 'yfinance',
            'name': 'yfinance Fallback',
            'status': 'warn',
            'message': 'Reachable but returned empty data for ^NSEI.',
            'category': 'data',
            'severity': 'low',
        }
    except Exception as e:  # noqa: BLE001
        return {
            'key': 'yfinance',
            'name': 'yfinance Fallback',
            'status': 'warn',
            'message': f'Reachable check failed: {e}',
            'category': 'data',
            'severity': 'low',
        }


# ── 5. Third-party providers (env-var presence + lightweight ping) ──────────
def _env_card(key: str, name: str, env_vars: List[str], category: str,
              severity: str, doc_url: str = '') -> Dict[str, Any]:
    """Check that all env_vars are non-empty. Used for license/credential checks."""
    missing = [v for v in env_vars if not (os.environ.get(v) or '').strip()]
    if not missing:
        return {
            'key': key,
            'name': name,
            'status': 'ok',
            'message': f"All credentials present ({', '.join(env_vars)}).",
            'category': category,
            'severity': severity,
        }
    return {
        'key': key,
        'name': name,
        'status': 'fail',
        'message': f"Missing env var(s): {', '.join(missing)}. {doc_url}".strip(),
        'category': category,
        'severity': severity,
    }


def _check_anthropic() -> Dict[str, Any]:
    key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not key:
        return {'key': 'anthropic', 'name': 'Anthropic (Claude)', 'status': 'fail',
                'message': 'ANTHROPIC_API_KEY not set.', 'category': 'llm', 'severity': 'high'}
    try:
        r = requests.get(
            'https://api.anthropic.com/v1/models',
            headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            return {'key': 'anthropic', 'name': 'Anthropic (Claude)', 'status': 'ok',
                    'message': 'API key valid, /v1/models reachable.', 'category': 'llm', 'severity': 'high'}
        if r.status_code in (401, 403):
            return {'key': 'anthropic', 'name': 'Anthropic (Claude)', 'status': 'fail',
                    'message': f'License/auth issue (HTTP {r.status_code}). Key may be revoked or out of credit.',
                    'category': 'llm', 'severity': 'critical'}
        if r.status_code == 429:
            return {'key': 'anthropic', 'name': 'Anthropic (Claude)', 'status': 'warn',
                    'message': 'Rate-limited (HTTP 429).', 'category': 'llm', 'severity': 'high'}
        return {'key': 'anthropic', 'name': 'Anthropic (Claude)', 'status': 'warn',
                'message': f'HTTP {r.status_code} from /v1/models.', 'category': 'llm', 'severity': 'medium'}
    except requests.RequestException as e:
        return {'key': 'anthropic', 'name': 'Anthropic (Claude)', 'status': 'warn',
                'message': f'Cannot reach api.anthropic.com: {e}', 'category': 'llm', 'severity': 'medium'}


def _check_openai() -> Dict[str, Any]:
    key = (os.environ.get('OPENAI_API_KEY') or '').strip()
    if not key:
        return {'key': 'openai', 'name': 'OpenAI (GPT)', 'status': 'fail',
                'message': 'OPENAI_API_KEY not set.', 'category': 'llm', 'severity': 'high'}
    try:
        r = requests.get(
            'https://api.openai.com/v1/models',
            headers={'Authorization': f'Bearer {key}'},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            return {'key': 'openai', 'name': 'OpenAI (GPT)', 'status': 'ok',
                    'message': 'API key valid, /v1/models reachable.', 'category': 'llm', 'severity': 'high'}
        if r.status_code in (401, 403):
            return {'key': 'openai', 'name': 'OpenAI (GPT)', 'status': 'fail',
                    'message': f'License/auth issue (HTTP {r.status_code}). Key may be revoked or out of credit.',
                    'category': 'llm', 'severity': 'critical'}
        if r.status_code == 429:
            return {'key': 'openai', 'name': 'OpenAI (GPT)', 'status': 'warn',
                    'message': 'Rate-limited (HTTP 429) — quota may be exhausted.',
                    'category': 'llm', 'severity': 'high'}
        return {'key': 'openai', 'name': 'OpenAI (GPT)', 'status': 'warn',
                'message': f'HTTP {r.status_code} from /v1/models.', 'category': 'llm', 'severity': 'medium'}
    except requests.RequestException as e:
        return {'key': 'openai', 'name': 'OpenAI (GPT)', 'status': 'warn',
                'message': f'Cannot reach api.openai.com: {e}', 'category': 'llm', 'severity': 'medium'}


def _check_perplexity() -> Dict[str, Any]:
    return _env_card('perplexity', 'Perplexity (Sonar)', ['PERPLEXITY_API_KEY'],
                     category='llm', severity='medium')


def _check_razorpay() -> Dict[str, Any]:
    key_id = (os.environ.get('RAZORPAY_KEY_ID') or '').strip()
    key_secret = (os.environ.get('RAZORPAY_KEY_SECRET') or '').strip()
    if not key_id or not key_secret:
        return {'key': 'razorpay', 'name': 'Razorpay (Billing)', 'status': 'fail',
                'message': 'RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET missing.',
                'category': 'billing', 'severity': 'high'}
    try:
        r = requests.get('https://api.razorpay.com/v1/payments?count=1',
                         auth=(key_id, key_secret), timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return {'key': 'razorpay', 'name': 'Razorpay (Billing)', 'status': 'ok',
                    'message': 'Credentials valid, /v1/payments reachable.',
                    'category': 'billing', 'severity': 'high'}
        if r.status_code in (401, 403):
            return {'key': 'razorpay', 'name': 'Razorpay (Billing)', 'status': 'fail',
                    'message': f'Auth failed (HTTP {r.status_code}). Key may be revoked.',
                    'category': 'billing', 'severity': 'critical'}
        return {'key': 'razorpay', 'name': 'Razorpay (Billing)', 'status': 'warn',
                'message': f'HTTP {r.status_code} from Razorpay.',
                'category': 'billing', 'severity': 'medium'}
    except requests.RequestException as e:
        return {'key': 'razorpay', 'name': 'Razorpay (Billing)', 'status': 'warn',
                'message': f'Cannot reach api.razorpay.com: {e}',
                'category': 'billing', 'severity': 'medium'}


def _check_twilio() -> Dict[str, Any]:
    sid = (os.environ.get('TWILIO_ACCOUNT_SID') or '').strip()
    tok = (os.environ.get('TWILIO_AUTH_TOKEN') or '').strip()
    if not sid or not tok:
        return {'key': 'twilio', 'name': 'Twilio (SMS / WhatsApp)', 'status': 'fail',
                'message': 'TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN missing.',
                'category': 'messaging', 'severity': 'medium'}
    try:
        r = requests.get(f'https://api.twilio.com/2010-04-01/Accounts/{sid}.json',
                         auth=(sid, tok), timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            j = r.json()
            return {'key': 'twilio', 'name': 'Twilio (SMS / WhatsApp)', 'status': 'ok',
                    'message': f"Account active — status: {j.get('status', 'unknown')}.",
                    'category': 'messaging', 'severity': 'medium'}
        if r.status_code in (401, 403):
            return {'key': 'twilio', 'name': 'Twilio (SMS / WhatsApp)', 'status': 'fail',
                    'message': f'Auth failed (HTTP {r.status_code}). Credentials may be revoked.',
                    'category': 'messaging', 'severity': 'high'}
        return {'key': 'twilio', 'name': 'Twilio (SMS / WhatsApp)', 'status': 'warn',
                'message': f'HTTP {r.status_code} from Twilio.',
                'category': 'messaging', 'severity': 'medium'}
    except requests.RequestException as e:
        return {'key': 'twilio', 'name': 'Twilio (SMS / WhatsApp)', 'status': 'warn',
                'message': f'Cannot reach api.twilio.com: {e}',
                'category': 'messaging', 'severity': 'medium'}


# ── 6. TC Execution Engine ──────────────────────────────────────────────────
def _check_tc_engine() -> Dict[str, Any]:
    url = (os.environ.get('EXECUTION_ENGINE_URL') or '').strip()
    if not url:
        return {'key': 'tc_engine', 'name': 'TC Execution Engine', 'status': 'disabled',
                'message': 'EXECUTION_ENGINE_URL not set — engine routing disabled.',
                'category': 'engine', 'severity': 'low'}
    try:
        from services import execution_proxy
        h = execution_proxy.healthz()
        if h.get('ok'):
            ver = execution_proxy.version() or {}
            return {'key': 'tc_engine', 'name': 'TC Execution Engine', 'status': 'ok',
                    'message': f"Reachable ({h.get('latency_ms', 0)} ms). "
                               f"Version: {ver.get('git_sha', 'unknown')[:8] if ver.get('git_sha') else 'n/a'}.",
                    'detail': {'latency_ms': h.get('latency_ms'),
                                'git_sha_short': (ver.get('git_sha') or '')[:8] or None,
                                'engine_version': ver.get('version')},
                    'category': 'engine', 'severity': 'high'}
        return {'key': 'tc_engine', 'name': 'TC Execution Engine', 'status': 'fail',
                'message': f"Health probe failed: HTTP {h.get('status_code')} — {h.get('error', '')}",
                'detail': {'status_code': h.get('status_code'), 'latency_ms': h.get('latency_ms')},
                'category': 'engine', 'severity': 'high'}
    except Exception as e:  # noqa: BLE001
        return {'key': 'tc_engine', 'name': 'TC Execution Engine', 'status': 'fail',
                'message': f'Probe error: {e}', 'category': 'engine', 'severity': 'high'}


# ── 7. Nightly jobs (alert_schedule table) ───────────────────────────────────
def _check_scheduled_jobs() -> List[Dict[str, Any]]:
    """Return one card per row in alert_schedule plus a global APScheduler status."""
    from app import db
    out: List[Dict[str, Any]] = []
    try:
        rows = db.session.execute(db.text("""
            SELECT schedule_key, display_name, hour, minute, days_of_week,
                   enabled, updated_at
            FROM   alert_schedule
            ORDER  BY sort_order ASC, schedule_key ASC
        """)).fetchall()
    except Exception as e:  # noqa: BLE001
        return [{
            'key': 'alert_schedule',
            'name': 'Scheduled Jobs',
            'status': 'fail',
            'message': f'Could not read alert_schedule table: {e}',
            'category': 'jobs',
            'severity': 'high',
        }]

    for r in rows:
        when = f"{int(r.hour):02d}:{int(r.minute):02d} IST ({r.days_of_week})"
        if not r.enabled:
            out.append({
                'key': f'job_{r.schedule_key}',
                'name': f'Job · {r.display_name}',
                'status': 'disabled',
                'message': f'Disabled. Was scheduled for {when}.',
                'category': 'jobs',
                'severity': 'low',
            })
        else:
            out.append({
                'key': f'job_{r.schedule_key}',
                'name': f'Job · {r.display_name}',
                'status': 'ok',
                'message': f'Enabled — runs at {when}.',
                'category': 'jobs',
                'severity': 'medium',
            })

    # Scheduler liveness — is APScheduler actually running in this worker?
    try:
        from app import _scheduler  # type: ignore[attr-defined]
        if _scheduler and _scheduler.running:
            n_jobs = len(_scheduler.get_jobs())
            out.insert(0, {
                'key': 'scheduler_runtime',
                'name': 'APScheduler Runtime',
                'status': 'ok',
                'message': f'Running with {n_jobs} job(s) registered.',
                'category': 'jobs',
                'severity': 'high',
            })
        else:
            out.insert(0, {
                'key': 'scheduler_runtime',
                'name': 'APScheduler Runtime',
                'status': 'fail',
                'message': 'Scheduler is NOT running — nightly jobs will not fire.',
                'category': 'jobs',
                'severity': 'critical',
            })
    except Exception:
        out.insert(0, {
            'key': 'scheduler_runtime',
            'name': 'APScheduler Runtime',
            'status': 'warn',
            'message': 'Scheduler state unknown (only the lock-holding worker runs jobs).',
            'category': 'jobs',
            'severity': 'medium',
        })
    return out


# ── 8. Partner broker connections (per-user trading/data broker accounts) ───
def _check_partner_brokers() -> List[Dict[str, Any]]:
    """One card per configured partner broker.

    Surfaces licensing / authentication failures (e.g. Dhan DH-901, Zerodha
    expired token, Angel One TOTP failures) and stale syncs so admins can
    proactively reach out to users / reissue tokens.
    """
    from app import db
    from models_broker import BrokerAccount
    from datetime import datetime, timezone

    out: List[Dict[str, Any]] = []
    try:
        rows = (BrokerAccount.query
                .filter(BrokerAccount.is_active.is_(True))
                .order_by(BrokerAccount.broker_type.asc(),
                          BrokerAccount.last_connected.desc().nullslast())
                .limit(50)
                .all())
    except Exception as e:  # noqa: BLE001
        return [{
            'key': 'partner_brokers',
            'name': 'Partner Broker Connections',
            'status': 'fail',
            'message': f'Could not read user_brokers table: {e}',
            'category': 'partners',
            'severity': 'high',
        }]

    if not rows:
        return [{
            'key': 'partner_brokers',
            'name': 'Partner Broker Connections',
            'status': 'disabled',
            'message': 'No partner broker accounts configured yet.',
            'category': 'partners',
            'severity': 'low',
        }]

    # Aggregate per broker_type to keep the page compact
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        bt = (r.broker_type or 'unknown').lower()
        g = grouped.setdefault(bt, {
            'total': 0, 'connected': 0, 'failed': 0, 'pending': 0,
            'sync_failed': 0, 'stale': 0, 'last_error_at': None,
        })
        g['total'] += 1
        cs = (r.connection_status or '').lower()
        if cs == 'connected':
            g['connected'] += 1
        elif cs in ('failed', 'rejected', 'expired', 'invalid'):
            g['failed'] += 1
        else:
            g['pending'] += 1
        if (r.sync_status or '').lower() == 'failed':
            g['sync_failed'] += 1
            if r.last_sync and (not g['last_error_at'] or r.last_sync > g['last_error_at']):
                g['last_error_at'] = r.last_sync
        # Stale = no last_connected, or > 24h old
        if not r.last_connected:
            g['stale'] += 1
        else:
            age = datetime.utcnow() - r.last_connected.replace(tzinfo=None)
            if age.total_seconds() > 86400:
                g['stale'] += 1

    for bt, g in sorted(grouped.items()):
        label = bt.title()
        if g['failed'] >= g['total']:
            status, sev = 'fail', 'critical'
            msg = (f"{g['failed']}/{g['total']} {label} accounts REJECTED by broker "
                   f"(licensing/auth). Users need to reconnect.")
        elif g['failed'] or g['sync_failed']:
            status, sev = 'warn', 'high'
            parts = []
            if g['failed']:
                parts.append(f"{g['failed']} auth-rejected")
            if g['sync_failed']:
                parts.append(f"{g['sync_failed']} sync-failed")
            msg = f"{g['connected']}/{g['total']} {label} connected — " + ", ".join(parts) + "."
        elif g['stale'] >= g['total']:
            status, sev = 'warn', 'medium'
            msg = f"All {g['total']} {label} accounts stale (>24h since last connect)."
        else:
            status, sev = 'ok', 'medium'
            msg = (f"{g['connected']}/{g['total']} {label} accounts healthy"
                   + (f" ({g['stale']} stale)" if g['stale'] else "") + ".")

        out.append({
            'key': f'partner_{bt}',
            'name': f'Partner · {label}',
            'status': status,
            'message': msg,
            'detail': {
                'total': g['total'], 'connected': g['connected'],
                'auth_failed': g['failed'], 'sync_failed': g['sync_failed'],
                'stale_over_24h': g['stale'],
                'last_error_at': g['last_error_at'].isoformat() if g['last_error_at'] else None,
            },
            'category': 'partners',
            'severity': sev,
        })
    return out


# ── Public entry point ──────────────────────────────────────────────────────
def run_all_checks() -> Dict[str, Any]:
    started = time.time()
    cards: List[Dict[str, Any]] = []

    cards.append(_safe(_check_telegram))
    cards.append(_safe(_check_admin_data_broker, 1))
    cards.append(_safe(_check_admin_data_broker, 2))
    cards.append(_safe(_check_yfinance))
    cards.append(_safe(_check_anthropic))
    cards.append(_safe(_check_openai))
    cards.append(_safe(_check_perplexity))
    cards.append(_safe(_check_razorpay))
    cards.append(_safe(_check_twilio))
    partners = _safe(_check_partner_brokers)
    if isinstance(partners, list):
        cards.extend(partners)
    elif isinstance(partners, dict):
        cards.append(partners)
    cards.append(_safe(_check_tc_engine))
    jobs = _safe(_check_scheduled_jobs)
    if isinstance(jobs, list):
        cards.extend(jobs)
    elif isinstance(jobs, dict):
        cards.append(jobs)

    # Summary counts
    counts = {'ok': 0, 'warn': 0, 'fail': 0, 'disabled': 0}
    for c in cards:
        counts[c.get('status', 'fail')] = counts.get(c.get('status', 'fail'), 0) + 1

    return {
        'cards': cards,
        'counts': counts,
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'elapsed_ms': int((time.time() - started) * 1000),
    }
