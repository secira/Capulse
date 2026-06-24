"""
Broker OAuth Routes
Handles the redirect-based login flow for Zerodha, Upstox, and ICICI Direct.
Angel One uses TOTP-based direct connect (no OAuth redirect).
"""

import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Optional

import requests
from flask import (
    Blueprint, flash, redirect, render_template,
    request, session, url_for
)
from flask_login import current_user, login_required

from app import db
from decorators import paid_plan_required
from models_broker import BrokerAccount, BrokerType, ConnectionStatus, compute_token_expiry

logger = logging.getLogger(__name__)

broker_oauth = Blueprint('broker_oauth', __name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _popup_success_response(account):
    """T006 — Render the popup-success template when reconnect was triggered
    from the Trade Now popup (?popup=1). The template posts a message back
    to window.opener and self-closes."""
    return render_template(
        'broker/popup_success.html',
        account_id=account.id,
        broker_name=account.broker_name or account.broker_type,
    )


def _is_popup_request() -> bool:
    return (request.args.get('popup') or request.form.get('popup') or '').strip() == '1'

def _get_callback_url(broker_slug: str) -> str:
    from flask import request as req
    base = req.host_url.rstrip('/')
    return f"{base}/broker/callback/{broker_slug}"


def _check_broker_plan_limit(broker_type_value: str) -> Optional[str]:
    """Return an error string if adding this broker would exceed the plan limit.

    Updating an existing broker account of the same type is always allowed.
    Only creating a brand-new broker type counts against the limit.
    """
    existing_same = BrokerAccount.query.filter_by(
        user_id=current_user.id,
        broker_type=broker_type_value,
        is_active=True,
    ).first()
    if existing_same:
        return None

    max_brokers = current_user.get_max_broker_connections()
    if max_brokers == 0:
        return (
            'Broker connections are not available on the Starter Plan. '
            'Upgrade to Growth Plan or higher to connect a broker.'
        )
    existing_count = BrokerAccount.query.filter_by(
        user_id=current_user.id, is_active=True,
    ).count()
    if existing_count >= max_brokers:
        plan = current_user.get_plan_display_name()
        return (
            f'Your {plan} allows up to {max_brokers} broker connection(s). '
            'Remove an existing broker or upgrade your plan.'
        )
    return None


def _save_pending_account(broker_type_value: str, client_id: str,
                           api_secret: str, extra: str = None) -> BrokerAccount:
    """Create or update a BrokerAccount in 'pending' state before OAuth redirect."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id,
        broker_type=broker_type_value,
        is_active=True,
    ).first()

    if not account:
        account = BrokerAccount(
            user_id=current_user.id,
            broker_type=broker_type_value,
            broker_name=broker_type_value.replace('_', ' ').title(),
            connection_status=ConnectionStatus.DISCONNECTED.value,
            is_active=True,
        )
        db.session.add(account)

    # Store credentials minus the access token (not yet obtained)
    account.set_credentials(
        client_id=client_id,
        access_token=extra or '',
        api_secret=api_secret,
    )
    account.connection_status = ConnectionStatus.DISCONNECTED.value
    db.session.commit()
    return account


# ---------------------------------------------------------------------------
# Stored-credential helpers
# ---------------------------------------------------------------------------

def _existing_account(broker_type_value: str) -> Optional['BrokerAccount']:
    """Return the user's active BrokerAccount for a broker, or None."""
    return BrokerAccount.query.filter_by(
        user_id=current_user.id,
        broker_type=broker_type_value,
        is_active=True,
    ).first()


def _merge_with_stored(broker_type_value: str, **form_fields) -> dict:
    """For each `form_fields[name] = submitted_value`, fall back to stored
    credential when the submitted value is blank. Returns a dict {name: value}.

    The mapping of form field name → stored credential field is:
        api_key / client_id / user_id / client_code / app_key  → 'client_id'
        api_secret / secret_key / app_secret                    → 'api_secret'
        access_token                                            → 'access_token'
        password / totp_secret / vendor_code                    → parsed from
            api_secret (broker-specific composite, see set_credentials).

    Callers pass only the simple top-level fields they actually need; for
    composite fields (Angel/Shoonya/5Paisa) the auth handlers continue to
    parse the colon-separated stored secret manually.
    """
    account = _existing_account(broker_type_value)
    stored = account.get_credentials() if account else {}

    field_to_cred = {
        'api_key': 'client_id',
        'client_id': 'client_id',
        'user_id': 'client_id',
        'client_code': 'client_id',
        'app_key': 'client_id',
        'api_secret': 'api_secret',
        'secret_key': 'api_secret',
        'app_secret': 'api_secret',
        'access_token': 'access_token',
    }

    merged = {}
    for name, submitted in form_fields.items():
        submitted = (submitted or '').strip()
        if submitted:
            merged[name] = submitted
        else:
            cred_key = field_to_cred.get(name)
            merged[name] = (stored.get(cred_key) or '') if cred_key else ''
    return merged


def _mark_expired_if_connected(broker_type_value: str, error_msg: str = '') -> None:
    """When a reconnect/refresh fails, mark the account as 'expired' so the UI
    surfaces an actionable banner with an 'Update Credentials' CTA. No-op if
    the account doesn't exist."""
    account = _existing_account(broker_type_value)
    if account:
        account.connection_status = ConnectionStatus.EXPIRED.value
        db.session.commit()
        logger.warning(
            f"Marked {broker_type_value} as EXPIRED for user {current_user.id}: {error_msg}"
        )


# ---------------------------------------------------------------------------
# Connect page
# ---------------------------------------------------------------------------

@broker_oauth.route('/dashboard/broker-connect')
@login_required
@paid_plan_required
def broker_connect():
    """Broker login page — all active brokers."""
    from routes_broker import BROKER_CATALOG
    user_accounts = {
        acc.broker_type: acc
        for acc in BrokerAccount.query.filter_by(
            user_id=current_user.id, is_active=True
        ).all()
    }

    catalog = [
        {**b, 'account': user_accounts.get(b['type'].value)}
        for b in BROKER_CATALOG
        if b.get('status') == 'active'
    ]

    max_brokers = current_user.get_max_broker_connections()
    used_brokers = len(user_accounts)
    at_limit = (max_brokers > 0 and used_brokers >= max_brokers)

    return render_template(
        'dashboard/broker_connect.html',
        broker_catalog=catalog,
        now=datetime.utcnow(),
        max_brokers=max_brokers,
        used_brokers=used_brokers,
        at_limit=at_limit,
        plan_name=current_user.get_plan_display_name(),
    )


# ---------------------------------------------------------------------------
# Zerodha  (KiteConnect OAuth)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/zerodha', methods=['POST'])
@login_required
def auth_zerodha():
    """Two connect modes for Zerodha:
      1) Direct Token (recommended) — user pastes api_key + access_token directly,
         we validate against Kite, save it, done. Works exactly like Dhan.
      2) OAuth Redirect — user pastes api_key + api_secret, we redirect to
         kite.zerodha.com which redirects back to /broker/callback/zerodha.
    The form's "Direct Token" tab uses *_direct field names so we can tell them
    apart without a hidden mode switch.
    """
    # ── Mode 1: Direct Token (Dhan-style: paste credentials → save) ──────────
    direct_api_key      = (request.form.get('api_key_direct') or '').strip()
    direct_access_token = (request.form.get('access_token_direct') or '').strip()
    direct_api_secret   = (request.form.get('api_secret_direct') or '').strip()

    if direct_access_token:
        # Fall back to stored values for any blank fields
        merged = _merge_with_stored(
            'zerodha',
            api_key=direct_api_key,
            api_secret=direct_api_secret,
            access_token=direct_access_token,
        )
        api_key = merged['api_key']
        access_token = merged['access_token']
        api_secret = merged['api_secret']  # optional
        if not api_key or not access_token:
            flash('Direct Token mode needs both API Key and Access Token.', 'error')
            return redirect(url_for('broker_oauth.broker_connect'))

        limit_err = _check_broker_plan_limit('zerodha')
        if limit_err:
            flash(limit_err, 'error')
            return redirect(url_for('broker_oauth.broker_connect'))

        # Live-validate against Kite (and capture user_id) before saving as CONNECTED
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
            profile = kite.profile()
            kite_user_id = profile.get('user_id') or ''
            kite_user_name = profile.get('user_name') or ''
        except Exception as e:
            # Detailed diagnostic: api_key is a public client identifier
            # (not a secret) so safe to log. access_token IS secret — log
            # only length + first/last 4 chars so we can spot truncation
            # or whitespace issues without leaking the full value.
            _at = access_token or ''
            _at_preview = f"{_at[:4]}…{_at[-4:]}" if len(_at) > 8 else "<short>"
            logger.error(
                f"Zerodha direct-token validation FAILED for user {current_user.id} | "
                f"api_key={api_key!r} (len={len(api_key)}, bytes={api_key.encode('utf-8')!r}) | "
                f"access_token preview={_at_preview} (len={len(_at)}) | "
                f"kite error: {e}"
            )
            flash(
                f'Zerodha rejected those credentials: {e}. '
                'This usually means (1) the API Key is wrong — copy it again from '
                'developers.kite.trade (it is shown right under your app name, lowercase '
                'alphanumeric, ~10–12 chars), or (2) the access_token is stale — Zerodha '
                'tokens expire daily at ~06:00 IST and must be regenerated.',
                'error',
            )
            return redirect(url_for('broker_oauth.broker_connect'))

        # All good — upsert + mark connected
        account = _save_pending_account('zerodha', api_key, api_secret or '')
        account.set_credentials(
            client_id=api_key,
            access_token=access_token,
            api_secret=api_secret or '',
        )
        if kite_user_id:
            account.broker_name = f"Zerodha ({kite_user_id})"
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        account.stamp_token_issued()
        db.session.commit()

        who = f" as {kite_user_name} ({kite_user_id})" if kite_user_id else ""
        flash(f'Zerodha connected successfully{who}!', 'success')
        logger.info(
            f"Zerodha direct-token connected for user {current_user.id} — "
            f"kite_user_id={kite_user_id}"
        )
        return redirect(url_for('broker_oauth.broker_connect'))

    # ── Mode 2: OAuth redirect (original flow) ───────────────────────────────
    # Strip whitespace — Kite API keys/secrets are alphanumeric; any stray
    # space from a copy-paste will make Kite reject the OAuth round-trip with
    # an opaque "Missing or empty field `authorize`" InputException.
    merged = _merge_with_stored(
        'zerodha',
        api_key=(request.form.get('api_key') or '').strip(),
        api_secret=(request.form.get('api_secret') or '').strip(),
    )
    api_key = (merged['api_key'] or '').strip()
    api_secret = (merged['api_secret'] or '').strip()
    if not api_key or not api_secret:
        flash('API Key and API Secret are required for Zerodha.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    # Basic shape check — Kite API keys are typically 6–32 alphanumeric chars.
    # Catching obviously wrong inputs here gives a useful error instead of a
    # cryptic Kite-side rejection.
    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9]{4,64}', api_key):
        flash(
            'That API Key looks malformed — Kite Connect keys are alphanumeric '
            '(no spaces/quotes/punctuation). Please copy it again from '
            'developers.kite.trade.',
            'error',
        )
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('zerodha')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account = _save_pending_account('zerodha', api_key, api_secret)
    session['zerodha_account_id'] = account.id

    # FORCE FRESH OAUTH: clear any stale access_token so this account is in a
    # known "pending" state. The callback will then either save a new token
    # (success) or leave the account empty (failure) — never a half-state
    # where Test Connection silently reuses a dead token. Also marks status
    # as 'expired' so the UI clearly shows the account needs re-auth until
    # the callback completes successfully.
    try:
        account.set_credentials(
            client_id=api_key,
            access_token='',          # blank = no usable token
            api_secret=api_secret,
        )
        account.connection_status = ConnectionStatus.EXPIRED.value
        db.session.commit()
        logger.info(
            f"Zerodha OAuth: cleared stale access_token on account {account.id} "
            f"before redirect (user {current_user.id})"
        )
    except Exception as _clr_err:
        logger.warning(
            f"Zerodha OAuth: failed to clear stale token on account {account.id}: {_clr_err}"
        )
        db.session.rollback()

    # Redirect to Zerodha Kite login. URL-encode api_key defensively even
    # though valid keys never need encoding.
    from urllib.parse import quote as _urlquote
    kite_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={_urlquote(api_key, safe='')}"
    # Full diagnostic log — api_key is a PUBLIC client identifier (like an
    # OAuth client_id), not a secret. Safe to log so we can debug "Missing
    # or empty field `api_key`" errors from Kite's /finish endpoint.
    logger.info(
        f"Zerodha OAuth: user {current_user.id} → Kite | "
        f"api_key={api_key!r} (len={len(api_key)}, "
        f"bytes={api_key.encode('utf-8')!r}) | url={kite_url}"
    )
    # Return JSON for fetch/XHR callers (JS opens Kite in a new tab from a
    # user-gesture context, which is the only reliable way to avoid the
    # "Missing or empty field `authorize`" error caused by Kite running inside
    # a cross-origin iframe where browsers block its session cookies).
    from flask import jsonify as _jsonify
    if (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            'application/json' in request.headers.get('Accept', '')):
        return _jsonify({'ok': True, 'kite_url': kite_url})

    # Fallback for plain form POST (rare): return a redirect directly so the
    # browser navigates to Kite at the top level.
    return redirect(kite_url)


@broker_oauth.route('/broker/callback/zerodha')
@login_required
def callback_zerodha():
    request_token = request.args.get('request_token')
    if not request_token:
        # Kite redirects here WITHOUT a request_token when its own /finish step
        # rejected the login. Surface the actual Kite error params so the user
        # can see the real reason instead of a vague "cancelled" message.
        kite_status = request.args.get('status', '')
        kite_type   = request.args.get('type', '')
        kite_msg    = request.args.get('message', '') or request.args.get('error_type', '')
        logger.warning(
            f"Zerodha callback hit without request_token — "
            f"status={kite_status!r} type={kite_type!r} msg={kite_msg!r} "
            f"args={dict(request.args)}"
        )
        if kite_msg or kite_type:
            flash(
                f'Zerodha login failed: {kite_msg or kite_type}. '
                f'This is almost always caused by the Redirect URL in your Kite Connect app '
                f'(developers.kite.trade) not matching '
                f'{request.url_root.rstrip("/")}/broker/callback/zerodha exactly. '
                f'Also verify your Kite app Type is "Connect" and the ₹2,000/mo subscription is active.',
                'error'
            )
        else:
            flash(
                'Zerodha login was cancelled or rejected by Kite before reaching our server. '
                f'Check that the Redirect URL on your Kite Connect app exactly equals '
                f'{request.url_root.rstrip("/")}/broker/callback/zerodha (no trailing slash).',
                'error'
            )
        return redirect(url_for('broker_oauth.broker_connect'))

    account_id = session.pop('zerodha_account_id', None)
    account = BrokerAccount.query.filter_by(
        id=account_id, user_id=current_user.id
    ).first() if account_id else None

    if not account:
        # Try to find by broker_type
        account = BrokerAccount.query.filter_by(
            user_id=current_user.id, broker_type='zerodha', is_active=True
        ).first()

    if not account:
        flash('Broker account not found. Please try again.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        from kiteconnect import KiteConnect
        creds = account.get_credentials()
        api_key = creds.get('client_id')
        api_secret = creds.get('api_secret')

        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data.get('access_token')
        kite_user_id = data.get('user_id') or ''
        kite_user_name = data.get('user_name') or ''

        if not access_token:
            raise ValueError("Empty access token returned by Zerodha")

        account.set_credentials(
            client_id=api_key,
            access_token=access_token,
            api_secret=api_secret,
        )
        # Surface the Zerodha account identity in the broker_name so the UI shows
        # "Zerodha (AB1234)" — useful when a user connects multiple brokers.
        if kite_user_id:
            account.broker_name = f"Zerodha ({kite_user_id})"
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        account.stamp_token_issued()
        db.session.commit()

        # Push fresh token to TC Execution Engine immediately.
        # Zerodha tokens rotate daily so every OAuth login must sync the engine.
        try:
            from services.execution_proxy import push_broker_credentials
            import os
            if os.environ.get('USE_REMOTE_EXEC', '').lower() in ('true', '1'):
                push_result = push_broker_credentials(account)
                if push_result.get('ok'):
                    logger.info(
                        f"Zerodha engine credential push OK for broker={account.id}"
                    )
                else:
                    logger.warning(
                        f"Zerodha engine credential push failed for broker={account.id}: "
                        f"{push_result}"
                    )
        except Exception as _pe:
            logger.warning(f"Zerodha engine push non-critical error: {_pe}")

        who = f" as {kite_user_name} ({kite_user_id})" if kite_user_id else ""
        # T006 — if reconnect was launched from a Trade Now popup, render the
        # auto-closing success page so the opener can resume the order flow.
        if session.pop('broker_reauth_popup', False):
            return _popup_success_response(account)
        flash(f'Zerodha connected successfully{who}!', 'success')
        logger.info(
            f"Zerodha connected for user {current_user.id} — kite_user_id={kite_user_id}"
        )
    except Exception as e:
        logger.error(f"Zerodha token exchange failed: {e}")
        flash(f'Zerodha connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Upstox  (v2 REST OAuth — no external SDK needed)
# ---------------------------------------------------------------------------

UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


@broker_oauth.route('/broker/auth/upstox', methods=['POST'])
@login_required
def auth_upstox():
    merged = _merge_with_stored(
        'upstox',
        api_key=request.form.get('api_key', ''),
        api_secret=request.form.get('api_secret', ''),
    )
    api_key, api_secret = merged['api_key'], merged['api_secret']
    if not api_key or not api_secret:
        flash('API Key and API Secret are required for Upstox.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('upstox')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account = _save_pending_account('upstox', api_key, api_secret)
    session['upstox_account_id'] = account.id

    callback = _get_callback_url('upstox')
    state = secrets.token_urlsafe(16)
    session['upstox_state'] = state

    auth_url = (
        f"{UPSTOX_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={api_key}"
        f"&redirect_uri={callback}"
        f"&state={state}"
    )
    return redirect(auth_url)


@broker_oauth.route('/broker/callback/upstox')
@login_required
def callback_upstox():
    code = request.args.get('code')
    state = request.args.get('state')

    if not code:
        flash('Upstox login was cancelled or failed.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    if state != session.pop('upstox_state', None):
        flash('Invalid state parameter. Please try again.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account_id = session.pop('upstox_account_id', None)
    account = BrokerAccount.query.filter_by(
        id=account_id, user_id=current_user.id
    ).first() if account_id else BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='upstox', is_active=True
    ).first()

    if not account:
        flash('Broker account not found. Please try again.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        creds = account.get_credentials()
        api_key = creds.get('client_id')
        api_secret = creds.get('api_secret')
        callback = _get_callback_url('upstox')

        resp = requests.post(UPSTOX_TOKEN_URL, data={
            'code': code,
            'client_id': api_key,
            'client_secret': api_secret,
            'redirect_uri': callback,
            'grant_type': 'authorization_code',
        }, headers={'Accept': 'application/json'}, timeout=15)
        resp.raise_for_status()
        token_data = resp.json()
        access_token = token_data.get('access_token')

        if not access_token:
            raise ValueError("Empty access token returned by Upstox")

        account.set_credentials(
            client_id=api_key,
            access_token=access_token,
            api_secret=api_secret,
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()

        flash('Upstox connected successfully!', 'success')
        logger.info(f"Upstox connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Upstox token exchange failed: {e}")
        flash(f'Upstox connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# ICICI Direct  (Breeze Connect OAuth)
# ---------------------------------------------------------------------------

ICICI_AUTH_URL = "https://api.icicidirect.com/apiuser/login"


@broker_oauth.route('/broker/auth/icici', methods=['POST'])
@login_required
def auth_icici():
    merged = _merge_with_stored(
        'icicidirect',
        api_key=request.form.get('api_key', ''),
        api_secret=request.form.get('api_secret', ''),
    )
    app_key, app_secret = merged['api_key'], merged['api_secret']
    if not app_key or not app_secret:
        flash('App Key and App Secret are required for ICICI Direct.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('icicidirect')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account = _save_pending_account('icicidirect', app_key, app_secret)
    session['icici_account_id'] = account.id

    auth_url = f"{ICICI_AUTH_URL}?api_key={app_key}"
    return redirect(auth_url)


@broker_oauth.route('/broker/callback/icici')
@login_required
def callback_icici():
    """
    ICICI Direct redirects back with ?apisession=SESSION_TOKEN
    """
    session_token = request.args.get('apisession')
    if not session_token:
        flash('ICICI Direct login was cancelled or failed.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account_id = session.pop('icici_account_id', None)
    account = BrokerAccount.query.filter_by(
        id=account_id, user_id=current_user.id
    ).first() if account_id else BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='icicidirect', is_active=True
    ).first()

    if not account:
        flash('Broker account not found. Please try again.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        creds = account.get_credentials()
        app_key = creds.get('client_id')
        app_secret = creds.get('api_secret')

        # Validate with Breeze
        from breeze_connect import BreezeConnect
        breeze = BreezeConnect(api_key=app_key)
        breeze.generate_session(api_secret=app_secret, session_token=session_token)

        account.set_credentials(
            client_id=app_key,
            access_token=session_token,
            api_secret=app_secret,
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()

        flash('ICICI Direct connected successfully!', 'success')
        logger.info(f"ICICI Direct connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"ICICI Direct session exchange failed: {e}")
        flash(f'ICICI Direct connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Angel One  (TOTP-based — no redirect OAuth)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/angel', methods=['POST'])
@login_required
def auth_angel():
    # Angel composite secret = "api_key:totp_secret:password" → parse stored
    existing = _existing_account('angel_broking')
    stored = existing.get_credentials() if existing else {}
    stored_parts = (stored.get('api_secret') or '').split(':')
    stored_api_key = stored_parts[0] if stored_parts else ''
    stored_totp = stored_parts[1] if len(stored_parts) > 1 else ''
    stored_pin = stored_parts[2] if len(stored_parts) > 2 else ''

    client_id = (request.form.get('client_id', '').strip() or stored.get('client_id') or '')
    api_key = (request.form.get('api_key', '').strip() or stored_api_key)
    totp_secret_raw = (request.form.get('totp_secret', '').strip() or stored_totp)
    password = (request.form.get('password', '').strip() or stored_pin)

    import re as _re
    totp_secret = _re.sub(r'[\s\-_]', '', totp_secret_raw).upper()

    if not all([client_id, api_key, totp_secret, password]):
        flash('All fields are required for Angel One.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    if not _re.fullmatch(r'[A-Z2-7]+=*', totp_secret) or len(totp_secret) < 16:
        flash(
            'Invalid TOTP Secret. It must be the long base32 string (A–Z and 2–7 only, '
            '16+ chars) you got when enabling 2FA on smartapi.angelbroking.com — NOT the '
            '6-digit code from your authenticator app. Reset 2FA on SmartAPI and click '
            '"Can\'t scan? Use this key" to copy the correct secret.',
            'error',
        )
        return redirect(url_for('broker_oauth.broker_connect'))

    limit_err = _check_broker_plan_limit('angel_broking')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        import pyotp
        from SmartApi import SmartConnect

        totp = pyotp.TOTP(totp_secret).now()
        smart = SmartConnect(api_key=api_key)
        data = smart.generateSession(client_id, password, totp)

        if not data or data.get('status') is False:
            raise ValueError(data.get('message', 'Angel One login failed'))

        access_token = data['data']['jwtToken']
        refresh_token_value = data['data'].get('refreshToken') or ''

        account = _save_pending_account(
            'angel_broking', api_key, totp_secret, extra=access_token
        )
        account.set_credentials(
            client_id=client_id,
            access_token=access_token,
            api_secret=f"{api_key}:{totp_secret}:{password}",
        )
        # T007 — persist Angel refreshToken so the monitor can auto-refresh
        # the JWT in the background before it has to flip status to EXPIRED.
        if refresh_token_value:
            account.set_refresh_token(refresh_token_value)
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        account.stamp_token_issued()
        db.session.commit()

        flash('Angel One connected successfully!', 'success')
        logger.info(f"Angel One connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Angel One connect failed: {e}")
        flash(f'Angel One connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


@broker_oauth.route('/broker/reconnect/angel', methods=['POST'])
@login_required
def reconnect_angel():
    """Re-generate Angel One JWT using stored TOTP secret + PIN (no re-entry needed)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='angel_broking', is_active=True,
    ).first()
    if not account:
        flash('Angel One account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        import pyotp
        from SmartApi import SmartConnect

        creds = account.get_credentials()
        parts = (creds.get('api_secret') or '').split(':')
        api_key = parts[0] if parts else ''
        totp_secret = parts[1] if len(parts) > 1 else ''
        stored_pin = parts[2] if len(parts) > 2 else ''
        client_id = creds.get('client_id', '')

        if not all([api_key, totp_secret, stored_pin, client_id]):
            flash('Stored credentials incomplete — please re-connect Angel One with all fields.', 'error')
            return redirect(url_for('broker_oauth.broker_connect'))

        totp = pyotp.TOTP(totp_secret).now()
        smart = SmartConnect(api_key=api_key)
        data = smart.generateSession(client_id, stored_pin, totp)

        if not data or data.get('status') is False:
            raise ValueError(data.get('message', 'Angel One refresh failed'))

        new_token = data['data']['jwtToken']
        new_refresh = data['data'].get('refreshToken') or ''
        account.set_credentials(
            client_id=client_id,
            access_token=new_token,
            api_secret=f"{api_key}:{totp_secret}:{stored_pin}",
        )
        if new_refresh:
            account.set_refresh_token(new_refresh)
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        account.stamp_token_issued()
        db.session.commit()
        # T006 — popup-aware return so Trade Now closes the popup and proceeds.
        if _is_popup_request():
            return _popup_success_response(account)
        flash('Angel One session refreshed successfully!', 'success')
        logger.info(f"Angel One token refreshed for user {current_user.id}")
    except Exception as e:
        logger.error(f"Angel One reconnect failed: {e}")
        _mark_expired_if_connected('angel_broking', str(e))
        flash(f'Angel One token rejected — please update your credentials. ({str(e)})', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


@broker_oauth.route('/broker/reconnect/upstox', methods=['POST'])
@login_required
def reconnect_upstox():
    """Re-start Upstox OAuth flow using stored API key (daily token refresh)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='upstox', is_active=True,
    ).first()
    if not account:
        flash('Upstox account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    creds = account.get_credentials()
    api_key = creds.get('client_id', '')
    if not api_key:
        flash('Stored credentials missing — please reconnect Upstox.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    session['upstox_account_id'] = account.id
    callback = _get_callback_url('upstox')
    state = secrets.token_urlsafe(16)
    session['upstox_state'] = state

    auth_url = (
        f"{UPSTOX_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={api_key}"
        f"&redirect_uri={callback}"
        f"&state={state}"
    )
    return redirect(auth_url)


@broker_oauth.route('/broker/reconnect/alice', methods=['POST'])
@login_required
def reconnect_alice():
    """Re-authenticate Alice Blue using stored user_id + api_key (daily session refresh)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='alice_blue', is_active=True,
    ).first()
    if not account:
        flash('Alice Blue account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        creds = account.get_credentials()
        user_id = creds.get('client_id', '')
        api_key = creds.get('api_secret', '')
        if not user_id or not api_key:
            flash('Stored credentials incomplete — please reconnect Alice Blue.', 'error')
            return redirect(url_for('broker_oauth.broker_connect'))

        import base64 as _b64
        checksum = hashlib.sha256(f"{user_id}{api_key}".encode()).hexdigest()
        encoded = _b64.b64encode(checksum.encode()).decode()

        resp = requests.post(
            'https://ant.aliceblueonline.com/rest/AliceBlueAPIService/api/customer/getUserSID',
            json={"userId": user_id, "userData": encoded},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        session_id = result.get("sessionID") or result.get("SID") or result.get("result")
        if not session_id:
            raise ValueError(f"Auth failed: {result}")

        account.set_credentials(client_id=user_id, access_token=session_id, api_secret=api_key)
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('Alice Blue session refreshed successfully!', 'success')
        logger.info(f"Alice Blue session refreshed for user {current_user.id}")
    except Exception as e:
        logger.error(f"Alice Blue reconnect failed: {e}")
        _mark_expired_if_connected('alice_blue', str(e))
        flash(f'Alice Blue session rejected — please update your credentials. ({str(e)})', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


@broker_oauth.route('/broker/reconnect/zerodha', methods=['POST'])
@login_required
def reconnect_zerodha():
    """Re-start Zerodha OAuth flow using stored API key (daily token refresh)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='zerodha', is_active=True,
    ).first()
    if not account:
        flash('Zerodha account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    creds = account.get_credentials()
    api_key = creds.get('client_id', '')
    if not api_key:
        flash('Stored credentials missing — please reconnect Zerodha.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    session['zerodha_account_id'] = account.id
    # T006 — remember that this OAuth round-trip started from a Trade Now popup,
    # so the success callback renders popup_success.html instead of redirecting
    # back to the broker_connect page in the popup window.
    session['broker_reauth_popup'] = _is_popup_request()
    account.connection_status = ConnectionStatus.DISCONNECTED.value
    db.session.commit()

    kite_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    from flask import jsonify as _jsonify
    if (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            'application/json' in request.headers.get('Accept', '')):
        return _jsonify({'ok': True, 'kite_url': kite_url})
    return redirect(kite_url)


# ---------------------------------------------------------------------------
# Groww  (token-based — user provides Partner API access token directly)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/groww', methods=['POST'])
@login_required
def auth_groww():
    merged = _merge_with_stored('groww', access_token=request.form.get('access_token', ''))
    access_token = merged['access_token']
    if not access_token:
        flash('Access Token is required for Groww.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('groww')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        # Per-user client_id so multiple users don't collide on the same row
        groww_client_id = f'groww_{current_user.id}'
        account = _save_pending_account('groww', groww_client_id, '', extra=access_token)
        account.set_credentials(client_id=groww_client_id, access_token=access_token, api_secret='')
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('Groww connected successfully!', 'success')
    except Exception as e:
        logger.error(f"Groww connect failed: {e}")
        flash(f'Groww connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Alice Blue  (ANT API v2 — SHA-256 checksum direct connect)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/alice', methods=['POST'])
@login_required
def auth_alice():
    merged = _merge_with_stored(
        'alice_blue',
        user_id=request.form.get('user_id', ''),
        api_key=request.form.get('api_key', ''),
    )
    user_id = merged['user_id'].upper()
    api_key = merged['api_key']
    if not user_id or not api_key:
        flash('User ID and API Key are required for Alice Blue.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('alice_blue')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        import hashlib, base64
        checksum = hashlib.sha256(f"{user_id}{api_key}".encode()).hexdigest()
        encoded = base64.b64encode(checksum.encode()).decode()

        resp = requests.post(
            'https://ant.aliceblueonline.com/rest/AliceBlueAPIService/api/customer/getUserSID',
            json={"userId": user_id, "userData": encoded},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        session_id = result.get("sessionID") or result.get("SID") or result.get("result")
        if not session_id:
            raise ValueError(f"Auth failed: {result}")

        account = _save_pending_account('alice_blue', user_id, api_key, extra=session_id)
        account.set_credentials(client_id=user_id, access_token=session_id, api_secret=api_key)
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('Alice Blue connected successfully!', 'success')
        logger.info(f"Alice Blue connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Alice Blue connect failed: {e}")
        flash(f'Alice Blue connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# 5 Paisa  (direct REST API connect with App Key + credentials)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/fivepaisa', methods=['POST'])
@login_required
def auth_fivepaisa():
    # 5 Paisa composite secret = "app_key:password" — parse stored
    existing = _existing_account('5paisa')
    stored = existing.get_credentials() if existing else {}
    stored_parts = (stored.get('api_secret') or '').split(':')
    stored_app_key = stored_parts[0] if stored_parts else ''
    stored_pw = stored_parts[1] if len(stored_parts) > 1 else ''

    client_code = (request.form.get('client_code', '').strip() or stored.get('client_id') or '')
    password = (request.form.get('password', '').strip() or stored_pw)
    app_key = (request.form.get('app_key', '').strip() or stored_app_key)
    totp = request.form.get('totp', '').strip()  # always fresh, never stored

    if not all([client_code, password, app_key]):
        flash('Client Code, Password, and App Key are required for 5 Paisa.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('5paisa')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        payload = {
            "head": {"AppKey": app_key},
            "body": {
                "ClientCode": client_code,
                "Password": password,
                "TOTP": totp,
            },
        }
        resp = requests.post(
            'https://openapi.5paisa.com/VendorsAPI/Service1.svc/V4/LoginRequestMobileNewbyEmail',
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        body = result.get("body", {})
        jwt = body.get("JWTToken") or body.get("AccessToken")

        if not jwt:
            raise ValueError(body.get("Message", "5 Paisa login failed — check credentials"))

        account = _save_pending_account('5paisa', client_code, f"{app_key}:{password}", extra=jwt)
        account.set_credentials(
            client_id=client_code,
            access_token=jwt,
            api_secret=f"{app_key}:{password}",
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('5 Paisa connected successfully!', 'success')
        logger.info(f"5 Paisa connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"5 Paisa connect failed: {e}")
        flash(f'5 Paisa connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


@broker_oauth.route('/broker/reconnect/fivepaisa', methods=['POST'])
@login_required
def reconnect_fivepaisa():
    """Re-authenticate 5 Paisa using stored App Key + password (gets a new JWT)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='5paisa', is_active=True,
    ).first()
    if not account:
        flash('5 Paisa account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        creds = account.get_credentials()
        client_code = creds.get('client_id', '')
        parts = (creds.get('api_secret') or '').split(':')
        app_key = parts[0] if parts else ''
        password = parts[1] if len(parts) > 1 else ''

        if not all([client_code, app_key, password]):
            flash('Stored credentials incomplete — please reconnect 5 Paisa.', 'error')
            return redirect(url_for('broker_oauth.broker_connect'))

        payload = {
            "head": {"AppKey": app_key},
            "body": {"ClientCode": client_code, "Password": password, "TOTP": ""},
        }
        resp = requests.post(
            'https://openapi.5paisa.com/VendorsAPI/Service1.svc/V4/LoginRequestMobileNewbyEmail',
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        body = result.get("body", {})
        jwt = body.get("JWTToken") or body.get("AccessToken")

        if not jwt:
            raise ValueError(body.get("Message", "5 Paisa re-login failed — TOTP may be required"))

        account.set_credentials(client_id=client_code, access_token=jwt, api_secret=f"{app_key}:{password}")
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('5 Paisa session refreshed successfully!', 'success')
        logger.info(f"5 Paisa session refreshed for user {current_user.id}")
    except Exception as e:
        logger.error(f"5 Paisa reconnect failed: {e}")
        _mark_expired_if_connected('5paisa', str(e))
        flash(f'5 Paisa token rejected — please update your credentials. ({str(e)})', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Fyers  (API v3 OAuth redirect)
# ---------------------------------------------------------------------------

FYERS_AUTH_URL = "https://api-t2.fyers.in/api/v3/generate-authcode"
FYERS_TOKEN_URL = "https://api-t2.fyers.in/api/v3/validate-authcode"


@broker_oauth.route('/broker/auth/fyers', methods=['POST'])
@login_required
def auth_fyers():
    merged = _merge_with_stored(
        'fyers',
        client_id=request.form.get('client_id', ''),
        secret_key=request.form.get('secret_key', ''),
    )
    client_id, secret_key = merged['client_id'], merged['secret_key']
    if not client_id or not secret_key:
        flash('App ID (Client ID) and Secret Key are required for Fyers.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('fyers')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account = _save_pending_account('fyers', client_id, secret_key)
    session['fyers_account_id'] = account.id

    callback = _get_callback_url('fyers')
    state = secrets.token_urlsafe(16)
    session['fyers_state'] = state

    auth_url = (
        f"{FYERS_AUTH_URL}"
        f"?client_id={client_id}"
        f"&redirect_uri={callback}"
        f"&response_type=code"
        f"&state={state}"
    )
    return redirect(auth_url)


@broker_oauth.route('/broker/callback/fyers')
@login_required
def callback_fyers():
    auth_code = request.args.get('auth_code') or request.args.get('code')
    state = request.args.get('state')

    if not auth_code:
        flash('Fyers login was cancelled or failed.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    if state != session.pop('fyers_state', None):
        flash('Invalid state parameter. Please try again.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account_id = session.pop('fyers_account_id', None)
    account = BrokerAccount.query.filter_by(
        id=account_id, user_id=current_user.id,
    ).first() if account_id else BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='fyers', is_active=True,
    ).first()

    if not account:
        flash('Broker account not found. Please try again.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        creds = account.get_credentials()
        client_id = creds.get('client_id', '')
        secret_key = creds.get('api_secret', '')

        app_id_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()

        resp = requests.post(
            FYERS_TOKEN_URL,
            json={"grant_type": "authorization_code", "appIdHash": app_id_hash, "code": auth_code},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get('s') != 'ok':
            raise ValueError(data.get('message', 'Fyers token exchange failed'))

        access_token = data.get('access_token')
        if not access_token:
            raise ValueError("Empty access token from Fyers")

        account.set_credentials(client_id=client_id, access_token=access_token, api_secret=secret_key)
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()

        flash('Fyers connected successfully!', 'success')
        logger.info(f"Fyers connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Fyers token exchange failed: {e}")
        flash(f'Fyers connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


@broker_oauth.route('/broker/reconnect/fyers', methods=['POST'])
@login_required
def reconnect_fyers():
    """Re-start Fyers OAuth flow using stored client_id + secret_key (daily token refresh)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='fyers', is_active=True,
    ).first()
    if not account:
        flash('Fyers account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    creds = account.get_credentials()
    client_id = creds.get('client_id', '')
    secret_key = creds.get('api_secret', '')
    if not client_id or not secret_key:
        flash('Stored Fyers credentials incomplete — please reconnect with App ID and Secret Key.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    session['fyers_account_id'] = account.id
    callback = _get_callback_url('fyers')
    state = secrets.token_urlsafe(16)
    session['fyers_state'] = state

    auth_url = (
        f"{FYERS_AUTH_URL}"
        f"?client_id={client_id}"
        f"&redirect_uri={callback}"
        f"&response_type=code"
        f"&state={state}"
    )
    return redirect(auth_url)


# ---------------------------------------------------------------------------
# Shoonya / Finvasia  (NorenOMS TOTP-based)
# ---------------------------------------------------------------------------

SHOONYA_AUTH_URL = "https://api.shoonya.com/NorenWClientTP/QuickAuth"


def _shoonya_quickauth(user_id: str, password: str, api_secret: str,
                        vendor_code: str = "", totp_secret: str = "") -> str:
    """Call Shoonya QuickAuth and return the session token. Raises on failure."""
    import pyotp as _pyotp
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    app_key_hash = hashlib.sha256(f"{user_id}|{api_secret}".encode()).hexdigest()

    totp_code = ""
    if totp_secret:
        try:
            totp_code = _pyotp.TOTP(totp_secret).now()
        except Exception:
            pass

    jdata = json.dumps({
        "apkversion": "1.0.0",
        "uid": user_id,
        "pwd": pwd_hash,
        "factor2": totp_code,
        "vc": vendor_code or user_id,
        "appkey": app_key_hash,
        "imei": "api",
        "source": "API",
    })
    resp = requests.post(
        SHOONYA_AUTH_URL,
        data=f"jData={jdata}&jKey={app_key_hash}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("stat") != "Ok":
        raise ValueError(data.get("emsg", "Shoonya login failed"))
    token = data.get("susertoken", "")
    if not token:
        raise ValueError("Empty session token from Shoonya")
    return token


@broker_oauth.route('/broker/auth/shoonya', methods=['POST'])
@login_required
def auth_shoonya():
    # Shoonya composite secret = "api_secret:vendor_code:totp_secret:password"
    existing = _existing_account('shoonya')
    stored = existing.get_credentials() if existing else {}
    parts = (stored.get('api_secret') or '').split(':')
    s_secret = parts[0] if parts else ''
    s_vendor = parts[1] if len(parts) > 1 else ''
    s_totp = parts[2] if len(parts) > 2 else ''
    s_pw = parts[3] if len(parts) > 3 else ''

    user_id_field = (request.form.get('user_id', '').strip() or stored.get('client_id') or '')
    password = (request.form.get('password', '').strip() or s_pw)
    totp_secret = (request.form.get('totp_secret', '').strip() or s_totp)
    vendor_code = (request.form.get('vendor_code', '').strip() or s_vendor)
    api_secret = (request.form.get('api_secret', '').strip() or s_secret)

    if not all([user_id_field, password, api_secret]):
        flash('User ID, Password and API Secret are required for Shoonya.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('shoonya')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        session_token = _shoonya_quickauth(
            user_id_field, password, api_secret, vendor_code, totp_secret,
        )
        account = BrokerAccount.query.filter_by(
            user_id=current_user.id, broker_type='shoonya', is_active=True,
        ).first()
        if not account:
            account = BrokerAccount(
                user_id=current_user.id,
                broker_type='shoonya',
                broker_name='Shoonya (Finvasia)',
                is_active=True,
            )
            db.session.add(account)
        account.set_credentials(
            client_id=user_id_field,
            access_token=session_token,
            api_secret=f"{api_secret}:{vendor_code}:{totp_secret}:{password}",
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('Shoonya (Finvasia) connected successfully!', 'success')
        logger.info(f"Shoonya connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Shoonya connect failed: {e}")
        flash(f'Shoonya connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


@broker_oauth.route('/broker/reconnect/shoonya', methods=['POST'])
@login_required
def reconnect_shoonya():
    """Re-authenticate Shoonya using stored credentials (daily session refresh)."""
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id, broker_type='shoonya', is_active=True,
    ).first()
    if not account:
        flash('Shoonya account not found. Please connect first.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        creds = account.get_credentials()
        user_id = creds.get('client_id', '')
        parts = (creds.get('api_secret') or '').split(':')
        api_secret = parts[0] if parts else ''
        vendor_code = parts[1] if len(parts) > 1 else ''
        totp_secret = parts[2] if len(parts) > 2 else ''
        password = parts[3] if len(parts) > 3 else ''

        if not all([user_id, api_secret, password]):
            flash('Stored credentials incomplete — please reconnect Shoonya.', 'error')
            return redirect(url_for('broker_oauth.broker_connect'))

        session_token = _shoonya_quickauth(user_id, password, api_secret, vendor_code, totp_secret)
        account.set_credentials(
            client_id=user_id,
            access_token=session_token,
            api_secret=creds.get('api_secret', ''),
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('Shoonya session refreshed successfully!', 'success')
        logger.info(f"Shoonya session refreshed for user {current_user.id}")
    except Exception as e:
        logger.error(f"Shoonya reconnect failed: {e}")
        _mark_expired_if_connected('shoonya', str(e))
        flash(f'Shoonya session rejected — please update your credentials. ({str(e)})', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Dhan  (token-based direct connect)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/dhan', methods=['POST'])
@login_required
def auth_dhan():
    merged = _merge_with_stored(
        'dhan',
        client_id=request.form.get('client_id', ''),
        access_token=request.form.get('access_token', ''),
    )
    client_id, access_token = merged['client_id'], merged['access_token']
    if not client_id or not access_token:
        flash('Client ID and Access Token are required for Dhan.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('dhan')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        account = BrokerAccount.query.filter_by(
            user_id=current_user.id, broker_type='dhan', is_active=True,
        ).first()
        if not account:
            account = BrokerAccount(
                user_id=current_user.id,
                broker_type='dhan',
                broker_name='Dhan',
                is_active=True,
            )
            db.session.add(account)
        account.set_credentials(client_id=client_id, access_token=access_token, api_secret='')
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        account.stamp_token_issued()
        db.session.commit()
        flash('Dhan connected successfully!', 'success')
        logger.info(f"Dhan connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Dhan connect failed: {e}")
        flash(f'Dhan connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Generic Reconnect — entry point used by the site-wide expiry banner and
# the "Reconnect" button on each broker card.  Routes to the right broker
# auth flow based on broker_type, reusing stored credentials wherever possible.
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/reconnect/<int:account_id>', methods=['POST'])
@login_required
def reconnect_broker(account_id: int):
    """Single entry point for "Reconnect <broker>" buttons.

    Dispatches to the broker-specific reconnect / auth-start flow based on
    broker_type. For brokers that already have a dedicated reconnect endpoint
    (Angel, Upstox) we call it directly; for Zerodha we kick off Kite OAuth
    using the stored api_key + api_secret; for Dhan (manual access-token flow)
    we send the user back to the broker page where they can paste a fresh
    token.
    """
    account = BrokerAccount.query.filter_by(
        id=account_id, user_id=current_user.id, is_active=True,
    ).first()
    if not account:
        flash('Broker account not found.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    btype = (account.broker_type or '').lower()

    # Angel One — uses stored TOTP secret + PIN, fully automatic.
    if btype in ('angel_broking', 'angel'):
        return redirect(url_for('broker_oauth.reconnect_angel'), code=307)

    # Upstox — has dedicated reconnect endpoint.
    if btype == 'upstox':
        return redirect(url_for('broker_oauth.reconnect_upstox'), code=307)

    # Zerodha — kick off the Kite OAuth redirect using stored api_key.
    if btype == 'zerodha':
        creds = account.get_credentials()
        api_key = creds.get('client_id') or ''
        if not api_key:
            flash('Stored Zerodha credentials missing — please re-enter API key.', 'error')
            return redirect(url_for('broker_oauth.broker_connect'))
        # Match the session-key contract used by callback_zerodha so the
        # callback can find this exact account when Kite redirects back.
        session['zerodha_account_id'] = account.id
        return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}")

    # Dhan — no OAuth, user must paste a fresh access token. Send them to the
    # broker page with a hint flash.
    if btype == 'dhan':
        flash('Generate a fresh Dhan access token from web.dhan.co → API Access, '
              'then paste it into the Access Token field below.', 'info')
        return redirect(url_for('broker_oauth.broker_connect') + '#dhan')

    # Fallback — send to broker page so user can manually reconnect.
    flash(f'Please re-enter credentials for {account.broker_name} to reconnect.', 'info')
    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

@broker_oauth.route('/api/broker/<int:account_id>/health', methods=['GET'])
@login_required
def api_broker_health(account_id: int):
    """T006 — Pre-trade health probe for a single broker account.

    Returns JSON the Trade Now form can use to decide whether to open the
    popup re-auth modal BEFORE submitting an order. Cheap: no live broker
    ping, just reads `connection_status` + `token_expires_at`.
    """
    from flask import jsonify
    acc = BrokerAccount.query.filter_by(
        id=account_id, user_id=current_user.id, is_active=True,
    ).first()
    if not acc:
        return jsonify({'ok': False, 'error': 'broker account not found'}), 404

    btype = (acc.broker_type or '').lower()
    # Map broker_type → existing reconnect route (popup mode flag passed via ?popup=1).
    _reauth_map = {
        'zerodha': 'broker_oauth.reconnect_zerodha',
        'dhan':    'broker_oauth.reconnect_dhan',
        'angel_broking': 'broker_oauth.reconnect_angel',
        'angel':         'broker_oauth.reconnect_angel',
        'upstox':  'broker_oauth.reconnect_upstox',
    }
    endpoint = _reauth_map.get(btype)
    try:
        reauth_url = url_for(endpoint) + '?popup=1' if endpoint else url_for('broker_oauth.broker_connect')
    except Exception:
        reauth_url = url_for('broker_oauth.broker_connect')

    minutes_left = acc.minutes_until_expiry() if hasattr(acc, 'minutes_until_expiry') else None
    expired = bool(acc.needs_reconnect()) if hasattr(acc, 'needs_reconnect') else (acc.connection_status == 'expired')
    expiring_soon = bool(acc.is_expiring_soon(threshold_min=15)) if hasattr(acc, 'is_expiring_soon') else False

    return jsonify({
        'ok': not expired,
        'expired': expired,
        'expiring_soon': expiring_soon,
        'minutes_left': minutes_left,
        'connection_status': acc.connection_status,
        'broker_type': btype,
        'broker_name': acc.broker_name,
        'reauth_url': reauth_url,
        'account_id': acc.id,
    })


@broker_oauth.route('/broker/disconnect/<broker_slug>', methods=['POST'])
@login_required
def disconnect_broker_oauth(broker_slug: str):
    account = BrokerAccount.query.filter_by(
        user_id=current_user.id,
        broker_type=broker_slug,
        is_active=True,
    ).first()

    if account:
        account.connection_status = ConnectionStatus.DISCONNECTED.value
        account.access_token = None
        db.session.commit()
        flash(f'{account.broker_name or broker_slug} disconnected.', 'success')
    else:
        flash('Broker account not found.', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))
