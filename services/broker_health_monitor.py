"""
Broker Health Monitor (Phase 1 broker hardening)
================================================

Background APScheduler job that pings every connected broker on a schedule,
flips `connection_status` to EXPIRED on 401/403, and dispatches Telegram +
in-app alerts when a user needs to reconnect.

Two alert tiers per broker, both debounced:
  - WARNING  (T-60 min):   sent once before predicted expiry.
  - EXPIRED  (status flip): sent once when the broker formally rejects the
                            saved access token, or when token_expires_at is
                            in the past and we've been unable to refresh.

Cadence:
  - Every 10 min during Indian market hours (09:00–16:00 IST, Mon–Fri).
  - Every 30 min otherwise.

Singleton: uses a Postgres advisory lock so only one gunicorn worker runs
the scheduler — same pattern as services/fno_monitor.py.
"""

import logging
import os
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Prevents the startup one-shot job and the interval job from running
# `run_health_check` concurrently (each has its own APScheduler ID so
# max_instances=1 only protects within the same job). Without this lock,
# two overlapping ticks could double-fire WARNING alerts.
_tick_lock = threading.Lock()

# Postgres advisory-lock ID — pick a value distinct from F&O monitor (which
# uses its own ID in services/fno_monitor.py). Must be a 32-bit int.
_BROKER_HEALTH_ADVISORY_LOCK_ID = 0x42_BB_01_01

_scheduler_started = False

# Health-check cadence (seconds).
_INTERVAL_MARKET_HOURS = 10 * 60       # every 10 min during 09:00–16:00 IST
_INTERVAL_OFF_HOURS    = 30 * 60       # every 30 min otherwise
# Run the scheduler on the shorter cadence — the job itself skips users whose
# next check window hasn't elapsed yet, so this is cheap.
_TICK_SECONDS = _INTERVAL_MARKET_HOURS

# Notify the user this many minutes before predicted expiry.
_WARNING_LEAD_MIN = 60

# Don't repeat an EXPIRED alert if one was sent within this many minutes.
_EXPIRED_ALERT_COOLDOWN_MIN = 12 * 60   # 12h


# ── IST helpers ───────────────────────────────────────────────────────────

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = None


def _now_ist() -> datetime:
    if _IST is None:
        return datetime.utcnow() + timedelta(hours=5, minutes=30)
    return datetime.now(_IST)


def _is_market_hours() -> bool:
    now = _now_ist()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    hhmm = now.hour * 100 + now.minute
    return 915 <= hhmm <= 1530


# ── Notification dispatch ─────────────────────────────────────────────────

def _notify(account, kind: str, message: str) -> None:
    """Send Telegram alert for a broker-health event.

    Falls back to a log warning if Telegram isn't configured. Future:
    also create an in-app notification row.
    """
    try:
        # Admin-only diagnostic — routed to the ops chat, NOT the public
        # signals group. See services.messaging_service for the routing rule.
        from services.messaging_service import send_telegram_admin_message
        text = (
            f"🔴 *Broker {kind}*\n"
            f"User #{account.user_id} — *{account.broker_name}*\n"
            f"{message}"
        )
        send_telegram_admin_message(text)
    except Exception as e:
        logger.warning(f"broker_health: telegram dispatch failed ({kind}): {e}")

    # In-app banner is rendered via the context_processor in app.py
    # (inject_broker_expiry_alerts) — no DB write needed here, the
    # connection_status flag drives the banner.


# ── Per-broker live ping ──────────────────────────────────────────────────

def _ping_broker(account) -> tuple[bool, str]:
    """Run a cheap liveness check against the broker API.

    Returns (ok, message). On 401/403 / token rejection, returns
    (False, '<reason>') and the caller will flip status to EXPIRED.
    Network errors are returned as (False, '<reason>') too but are *not*
    treated as token expiry — see _is_auth_error().
    """
    btype = (account.broker_type or '').lower()
    try:
        creds = account.get_credentials()
    except Exception as e:
        return False, f"creds decrypt failed: {e}"

    try:
        if btype == 'zerodha':
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=creds.get('client_id') or '')
            kite.set_access_token(creds.get('access_token') or '')
            kite.profile()   # raises TokenException on bad/expired token
            return True, 'ok'

        if btype == 'dhan':
            # Cheap profile fetch — Dhan returns 401 on expired access token.
            import requests
            access_token = creds.get('access_token') or ''
            client_id = creds.get('client_id') or ''
            if not access_token:
                return False, 'no access token'
            r = requests.get(
                "https://api.dhan.co/v2/profile",
                headers={
                    'access-token': access_token,
                    'client-id': client_id,
                    'Accept': 'application/json',
                },
                timeout=8,
            )
            if r.status_code == 401 or r.status_code == 403:
                return False, f"HTTP {r.status_code} {r.text[:80]}"
            if r.status_code >= 500:
                return False, f"broker 5xx (transient): {r.status_code}"
            r.raise_for_status()
            return True, 'ok'

        if btype in ('angel_broking', 'angel'):
            # SmartAPI's SmartConnect.getProfile() takes a refresh_token, but
            # we only store the JWT (access token) — so call the underlying
            # REST endpoint directly with the JWT in the Authorization header.
            # This avoids the SDK-signature mismatch that previously caused
            # false EXPIRED flips on healthy Angel sessions.
            import requests
            parts = (creds.get('api_secret') or '').split(':')
            api_key = parts[0] if parts else ''
            jwt = creds.get('access_token') or ''
            if not jwt or not api_key:
                return False, 'missing jwt or api_key'
            r = requests.get(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/user/v1/getProfile",
                headers={
                    'Authorization': f'Bearer {jwt}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'X-UserType': 'USER',
                    'X-SourceID': 'WEB',
                    'X-ClientLocalIP': '127.0.0.1',
                    'X-ClientPublicIP': '127.0.0.1',
                    'X-MACAddress': '00:00:00:00:00:00',
                    'X-PrivateKey': api_key,
                },
                timeout=8,
            )
            if r.status_code in (401, 403):
                return False, f"HTTP {r.status_code} {r.text[:80]}"
            if r.status_code >= 500:
                return False, f"broker 5xx (transient): {r.status_code}"
            try:
                body = r.json()
            except Exception:
                return False, f"unparseable response: {r.text[:80]}"
            if body.get('status') is False:
                msg = body.get('message', 'unknown')
                code = (body.get('errorcode') or body.get('errorCode') or '').upper()
                # AB1010 = invalid token; AG8001/AG8002 = session expired.
                if code in ('AB1010', 'AG8001', 'AG8002') or '401' in str(code):
                    return False, f"angel rejected ({code}): {msg}"
                return False, f"broker 5xx (transient): {msg}"
            return True, 'ok'

        # Phase 2 brokers — fall back to "predicted expiry" only (no live ping).
        return True, 'skipped (phase-2 broker)'
    except Exception as e:
        msg = str(e)
        return False, msg[:200]


def _ping_admin_pool_row(row) -> tuple[bool, str]:
    """Cheap liveness check for an AdminDataBroker pool slot.

    Mirrors `_ping_broker(account)` but takes an AdminDataBroker row and
    decrypts credentials via row.get_credentials(). Returns (ok, message).
    """
    btype = (row.broker_type or '').lower()
    try:
        creds = row.get_credentials()
    except Exception as e:
        return False, f"creds decrypt failed: {e}"

    try:
        if btype == 'zerodha':
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=creds.get('client_id') or '')
            kite.set_access_token(creds.get('access_token') or '')
            kite.profile()
            return True, 'ok'

        if btype == 'dhan':
            import requests
            access_token = creds.get('access_token') or ''
            client_id = creds.get('client_id') or ''
            if not access_token:
                return False, 'no access token'
            r = requests.get(
                "https://api.dhan.co/v2/profile",
                headers={
                    'access-token': access_token,
                    'client-id': client_id,
                    'Accept': 'application/json',
                },
                timeout=8,
            )
            if r.status_code in (401, 403):
                return False, f"HTTP {r.status_code} {r.text[:80]}"
            if r.status_code >= 500:
                return False, f"broker 5xx (transient): {r.status_code}"
            r.raise_for_status()
            return True, 'ok'

        if btype in ('angel_broking', 'angel'):
            import requests
            parts = (creds.get('api_secret') or '').split(':')
            api_key = parts[0] if parts else ''
            jwt = creds.get('access_token') or ''
            if not jwt or not api_key:
                return False, 'missing jwt or api_key'
            r = requests.get(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/user/v1/getProfile",
                headers={
                    'Authorization': f'Bearer {jwt}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'X-UserType': 'USER', 'X-SourceID': 'WEB',
                    'X-ClientLocalIP': '127.0.0.1', 'X-ClientPublicIP': '127.0.0.1',
                    'X-MACAddress': '00:00:00:00:00:00',
                    'X-PrivateKey': api_key,
                },
                timeout=8,
            )
            if r.status_code in (401, 403):
                return False, f"HTTP {r.status_code} {r.text[:80]}"
            if r.status_code >= 500:
                return False, f"broker 5xx (transient): {r.status_code}"
            try:
                body = r.json()
            except Exception:
                return False, f"unparseable response: {r.text[:80]}"
            if body.get('status') is False:
                msg = body.get('message', 'unknown')
                code = (body.get('errorcode') or body.get('errorCode') or '').upper()
                if code in ('AB1010', 'AG8001', 'AG8002') or '401' in str(code):
                    return False, f"angel rejected ({code}): {msg}"
                return False, f"broker 5xx (transient): {msg}"
            return True, 'ok'

        return True, 'skipped (phase-2 broker)'
    except Exception as e:
        return False, str(e)[:200]


def _try_angel_refresh(account) -> bool:
    """T007 — Attempt invisible Angel JWT refresh.

    Reads the persisted Angel refreshToken, calls AngelBroker.refresh_jwt(),
    and on success writes the new JWT + refresh token back to the account
    (encrypted) and stamps the expiry so the account stays 'connected'.

    Returns True only if the refresh succeeded AND the new token was saved.
    """
    try:
        stored_rt = account.get_refresh_token() if hasattr(account, 'get_refresh_token') else None
        if not stored_rt:
            return False
        from brokers.angel import AngelBroker
        creds = account.get_credentials()
        broker = AngelBroker(creds)
        result = broker.refresh_jwt(stored_rt)
        if not result or not result.get('jwt'):
            return False
        # Persist new tokens. Keep api_secret format intact so other code paths
        # that parse "{api_key}:{totp_secret}:{password}" still work.
        new_jwt = result['jwt']
        new_rt = result.get('refresh_token') or stored_rt
        parts = (creds.get('api_secret') or '').split(':')
        api_key = parts[0] if parts else ''
        totp_secret = parts[1] if len(parts) > 1 else ''
        password = parts[2] if len(parts) > 2 else ''
        account.set_credentials(
            client_id=creds.get('client_id') or '',
            access_token=new_jwt,
            api_secret=f"{api_key}:{totp_secret}:{password}",
        )
        account.set_refresh_token(new_rt)
        account.stamp_token_issued()
        # Explicitly mark for persistence — we're in a background worker thread
        # and want the next db.session.commit() to flush these changes even if
        # SQLAlchemy's autoflush has been bypassed elsewhere in this tick.
        from app import db as _db
        _db.session.add(account)
        try:
            _db.session.commit()
        except Exception as commit_err:
            _db.session.rollback()
            logger.warning(f"_try_angel_refresh: commit failed, rolling back: {commit_err}")
            return False
        return True
    except Exception as e:
        logger.warning(f"_try_angel_refresh failed: {e}")
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return False


def _notify_admin_pool(row, kind: str, message: str) -> None:
    """Telegram alert for an admin-pool slot event (no user attached).

    Routed to the admin/ops Telegram chat ONLY — never to the public
    signals group. Requires ``TELEGRAM_ADMIN_CHAT_ID``; otherwise logged.
    """
    try:
        from services.messaging_service import send_telegram_admin_message
        text = (
            f"🛡️ *Admin Pool {kind}*\n"
            f"Slot P{row.priority} — *{row.broker_name}* ({row.broker_type})\n"
            f"{message}"
        )
        send_telegram_admin_message(text)
    except Exception as e:
        logger.warning(f"broker_health: admin-pool telegram dispatch failed ({kind}): {e}")


_AUTH_ERR_NEEDLES = (
    '401', '403', 'token', 'invalid api', 'session expired', 'unauthor',
    'TokenException', 'tokenexception',
)


def _is_auth_error(msg: str) -> bool:
    if not msg:
        return False
    low = msg.lower()
    return any(n.lower() in low for n in _AUTH_ERR_NEEDLES)


# ── Main job ──────────────────────────────────────────────────────────────

def run_health_check(app):
    """Iterate every active broker account and update health/alerts.

    Cheap when there are no active brokers; runs in O(N) per tick.
    Wrapped in an in-process lock so the startup one-shot and recurring
    interval jobs can't run concurrently.
    """
    if not _tick_lock.acquire(blocking=False):
        logger.debug("broker_health: tick already in progress — skipping")
        return
    try:
      with app.app_context():
        try:
            from models_broker import BrokerAccount, ConnectionStatus
            from app import db

            interval_min = _INTERVAL_MARKET_HOURS // 60 if _is_market_hours() else _INTERVAL_OFF_HOURS // 60
            stale_before = datetime.utcnow() - timedelta(minutes=interval_min)

            # Only check accounts that are currently connected. Already-EXPIRED
            # rows stay expired until the user reconnects via the OAuth flow,
            # which clears the flags via stamp_token_issued(); re-pinging an
            # already-rejected token wastes broker quota and gains nothing.
            # NOTE: this is a background thread with no request context, so the
            # tenant middleware would otherwise restrict us to the DEFAULT
            # tenant ('live') and silently skip accounts from other tenants.
            # We must monitor ALL tenants' brokers — bypass tenant filter by
            # setting `_tenant_bypass = True` on the select statement (read
            # by the do_orm_execute listener in middleware.tenant_sqlalchemy).
            from sqlalchemy import select
            stmt = (
                select(BrokerAccount)
                .where(BrokerAccount.is_active.is_(True))
                .where(BrokerAccount.connection_status == 'connected')
            )
            stmt._tenant_bypass = True  # type: ignore[attr-defined]
            accounts = db.session.execute(stmt).scalars().all()

            checked = 0
            flipped = 0
            warned  = 0

            for acc in accounts:
                # Skip if checked recently (avoid hammering broker APIs).
                if acc.last_health_check and acc.last_health_check > stale_before:
                    pass  # still process WARNING tier below — that's free
                else:
                    # Only live-ping the priority brokers in Phase 1.
                    if (acc.broker_type or '').lower() in (
                        'zerodha', 'dhan', 'angel_broking', 'angel'
                    ):
                        ok, msg = _ping_broker(acc)
                        acc.last_health_check = datetime.utcnow()
                        acc.health_check_message = msg[:255]
                        checked += 1

                        if not ok and _is_auth_error(msg):
                            # T007 — for Angel, try invisible JWT refresh BEFORE
                            # flipping to EXPIRED. Many "expired" events are just
                            # the JWT rotating out — if we have a refresh token
                            # on file, the user never has to do anything.
                            recovered = False
                            if (acc.broker_type or '').lower() in ('angel_broking', 'angel'):
                                recovered = _try_angel_refresh(acc)

                            if recovered:
                                acc.health_check_message = 'auto-refreshed JWT'
                                logger.info(f"broker_health: Angel JWT auto-refreshed for user {acc.user_id}")
                            else:
                                # EXPIRED → flip status + alert (debounced).
                                already_alerted = (
                                    acc.expiry_alerted_at
                                    and acc.expiry_alerted_at > datetime.utcnow() - timedelta(minutes=_EXPIRED_ALERT_COOLDOWN_MIN)
                                )
                                acc.connection_status = ConnectionStatus.EXPIRED.value
                                if not already_alerted:
                                    _notify(
                                        acc,
                                        'EXPIRED',
                                        f"Token rejected by broker. Reason: {msg[:120]}\n"
                                        f"User must reconnect from dashboard.",
                                    )
                                    acc.expiry_alerted_at = datetime.utcnow()
                                    flipped += 1

                # T-60 WARNING tier — fires once per token cycle.
                if (
                    acc.connection_status == 'connected'
                    and acc.is_expiring_soon(threshold_min=_WARNING_LEAD_MIN)
                    and not acc.expiry_warning_sent_at
                ):
                    _notify(
                        acc,
                        'WARNING',
                        f"Token expires in ~{acc.expiry_human()}. "
                        f"User should reconnect soon to avoid trade interruptions.",
                    )
                    acc.expiry_warning_sent_at = datetime.utcnow()
                    warned += 1

            # ── Admin data-broker pool ─────────────────────────────────
            # Mirror the same logic for the admin slots so the pool stays
            # healthy without an admin manually clicking "Test".
            try:
                from models_broker import AdminDataBroker
                admin_rows = (
                    AdminDataBroker.query
                    .filter_by(is_active=True)
                    .filter(AdminDataBroker.connection_status != 'expired')
                    .all()
                )
                for arow in admin_rows:
                    btype = (arow.broker_type or '').lower()
                    if btype not in ('zerodha', 'dhan', 'angel_broking', 'angel'):
                        continue  # only Phase 1 brokers get live-ping
                    if arow.last_health_check and arow.last_health_check > stale_before:
                        # Still process WARNING tier below.
                        pass
                    else:
                        ok, msg = _ping_admin_pool_row(arow)
                        arow.last_health_check = datetime.utcnow()
                        arow.health_check_message = msg[:255]
                        checked += 1
                        if not ok and _is_auth_error(msg):
                            already_alerted = (
                                arow.expiry_alerted_at
                                and arow.expiry_alerted_at > datetime.utcnow() - timedelta(minutes=_EXPIRED_ALERT_COOLDOWN_MIN)
                            )
                            arow.connection_status = 'expired'
                            if not already_alerted:
                                _notify_admin_pool(
                                    arow,
                                    'EXPIRED',
                                    f"Admin pool slot P{arow.priority} ({arow.broker_name}) token rejected: {msg[:120]}",
                                )
                                arow.expiry_alerted_at = datetime.utcnow()
                                flipped += 1

                    # T-60 WARNING tier for admin pool (same pattern as users).
                    if (
                        arow.connection_status not in ('expired',)
                        and hasattr(arow, 'is_expiring_soon')
                        and arow.is_expiring_soon(threshold_min=_WARNING_LEAD_MIN)
                        and not arow.expiry_warning_sent_at
                    ):
                        _notify_admin_pool(
                            arow,
                            'WARNING',
                            f"Admin pool slot P{arow.priority} ({arow.broker_name}) "
                            f"expires in ~{arow.expiry_human()}. Other slot will take over.",
                        )
                        arow.expiry_warning_sent_at = datetime.utcnow()
                        warned += 1
            except Exception as e:
                logger.error(f"broker_health: admin pool tick failed: {e}", exc_info=True)

            db.session.commit()

            if checked or flipped or warned:
                logger.info(
                    f"broker_health: checked={checked} flipped={flipped} warned={warned}"
                )
        except Exception as e:
            logger.error(f"broker_health: tick failed: {e}", exc_info=True)
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
    finally:
        _tick_lock.release()


# ── Scheduler bootstrap (singleton) ───────────────────────────────────────

def _try_acquire_scheduler_lock(app, lock_id: int) -> bool:
    """Return True if this worker should own the scheduler (advisory lock).

    Falls back to True on errors so a misconfigured DB doesn't silently
    disable the monitor.
    """
    try:
        # Delegate to fno_monitor's helper: it uses a dedicated, detached,
        # module-pinned connection so the session-level lock survives for the
        # worker's lifetime (a pooled session connection would return to the
        # pool and silently release the lock).
        from services.fno_monitor import _try_acquire_scheduler_lock as _acquire
        return _acquire(app, lock_id)
    except Exception as e:
        logger.warning(f"broker_health advisory-lock check failed ({e}); starting anyway")
        return True


def start_scheduler(app):
    global _scheduler_started
    if _scheduler_started:
        return
    if os.environ.get("SKIP_SCHEDULER"):
        return
    if not _try_acquire_scheduler_lock(app, _BROKER_HEALTH_ADVISORY_LOCK_ID):
        logger.info("Broker health monitor skipped on this worker (another worker holds the lock)")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            run_health_check, 'interval', seconds=_TICK_SECONDS,
            args=[app], id='broker_health_check',
            replace_existing=True, max_instances=1,
        )
        # Run once 30s after startup so newly-deployed pods immediately
        # surface any expired tokens instead of waiting a full cycle.
        scheduler.add_job(
            run_health_check, 'date',
            run_date=datetime.utcnow() + timedelta(seconds=30),
            args=[app], id='broker_health_initial',
        )
        scheduler.start()
        _scheduler_started = True
        logger.info(
            f"Broker health monitor started ({_TICK_SECONDS}s tick, "
            f"singleton worker)"
        )
    except Exception as e:
        logger.error(f"Failed to start broker health scheduler: {e}")
