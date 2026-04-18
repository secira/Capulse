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
from models_broker import BrokerAccount, BrokerType, ConnectionStatus

logger = logging.getLogger(__name__)

broker_oauth = Blueprint('broker_oauth', __name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    api_key = request.form.get('api_key', '').strip()
    api_secret = request.form.get('api_secret', '').strip()
    if not api_key or not api_secret:
        flash('API Key and API Secret are required for Zerodha.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('zerodha')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    account = _save_pending_account('zerodha', api_key, api_secret)
    session['zerodha_account_id'] = account.id

    # Redirect to Zerodha Kite login
    kite_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    return redirect(kite_url)


@broker_oauth.route('/broker/callback/zerodha')
@login_required
def callback_zerodha():
    request_token = request.args.get('request_token')
    if not request_token:
        flash('Zerodha login was cancelled or failed.', 'error')
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

        if not access_token:
            raise ValueError("Empty access token returned by Zerodha")

        account.set_credentials(
            client_id=api_key,
            access_token=access_token,
            api_secret=api_secret,
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()

        flash('Zerodha connected successfully!', 'success')
        logger.info(f"Zerodha connected for user {current_user.id}")
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
    api_key = request.form.get('api_key', '').strip()
    api_secret = request.form.get('api_secret', '').strip()
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
    app_key = request.form.get('api_key', '').strip()
    app_secret = request.form.get('api_secret', '').strip()
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
    client_id = request.form.get('client_id', '').strip()
    api_key = request.form.get('api_key', '').strip()
    totp_secret = request.form.get('totp_secret', '').strip()
    password = request.form.get('password', '').strip()

    if not all([client_id, api_key, totp_secret, password]):
        flash('All fields are required for Angel One.', 'error')
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

        account = _save_pending_account(
            'angel_broking', api_key, totp_secret, extra=access_token
        )
        account.set_credentials(
            client_id=client_id,
            access_token=access_token,
            api_secret=f"{api_key}:{totp_secret}:{password}",
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
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
        account.set_credentials(
            client_id=client_id,
            access_token=new_token,
            api_secret=f"{api_key}:{totp_secret}:{stored_pin}",
        )
        account.connection_status = ConnectionStatus.CONNECTED.value
        account.last_connected = datetime.utcnow()
        db.session.commit()
        flash('Angel One session refreshed successfully!', 'success')
        logger.info(f"Angel One token refreshed for user {current_user.id}")
    except Exception as e:
        logger.error(f"Angel One reconnect failed: {e}")
        flash(f'Angel One reconnect failed: {str(e)}', 'error')

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
        flash(f'Alice Blue reconnect failed: {str(e)}', 'error')

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
    account.connection_status = ConnectionStatus.DISCONNECTED.value
    db.session.commit()

    kite_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    return redirect(kite_url)


# ---------------------------------------------------------------------------
# Groww  (token-based — user provides Partner API access token directly)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/groww', methods=['POST'])
@login_required
def auth_groww():
    access_token = request.form.get('access_token', '').strip()
    if not access_token:
        flash('Access Token is required for Groww.', 'error')
        return redirect(url_for('broker_oauth.broker_connect'))
    limit_err = _check_broker_plan_limit('groww')
    if limit_err:
        flash(limit_err, 'error')
        return redirect(url_for('broker_oauth.broker_connect'))

    try:
        account = _save_pending_account('groww', 'groww_user', '', extra=access_token)
        account.set_credentials(client_id='groww_user', access_token=access_token, api_secret='')
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
    user_id = request.form.get('user_id', '').strip().upper()
    api_key = request.form.get('api_key', '').strip()
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
    client_code = request.form.get('client_code', '').strip()
    password = request.form.get('password', '').strip()
    app_key = request.form.get('app_key', '').strip()
    totp = request.form.get('totp', '').strip()

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
        flash(f'5 Paisa reconnect failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Fyers  (API v3 OAuth redirect)
# ---------------------------------------------------------------------------

FYERS_AUTH_URL = "https://api-t2.fyers.in/api/v3/generate-authcode"
FYERS_TOKEN_URL = "https://api-t2.fyers.in/api/v3/validate-authcode"


@broker_oauth.route('/broker/auth/fyers', methods=['POST'])
@login_required
def auth_fyers():
    client_id = request.form.get('client_id', '').strip()
    secret_key = request.form.get('secret_key', '').strip()
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
    user_id_field = request.form.get('user_id', '').strip()
    password = request.form.get('password', '').strip()
    totp_secret = request.form.get('totp_secret', '').strip()
    vendor_code = request.form.get('vendor_code', '').strip()
    api_secret = request.form.get('api_secret', '').strip()

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
        flash(f'Shoonya reconnect failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Dhan  (token-based direct connect)
# ---------------------------------------------------------------------------

@broker_oauth.route('/broker/auth/dhan', methods=['POST'])
@login_required
def auth_dhan():
    client_id = request.form.get('client_id', '').strip()
    access_token = request.form.get('access_token', '').strip()
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
        db.session.commit()
        flash('Dhan connected successfully!', 'success')
        logger.info(f"Dhan connected for user {current_user.id}")
    except Exception as e:
        logger.error(f"Dhan connect failed: {e}")
        flash(f'Dhan connection failed: {str(e)}', 'error')

    return redirect(url_for('broker_oauth.broker_connect'))


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

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
