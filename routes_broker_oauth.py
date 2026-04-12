"""
Broker OAuth Routes
Handles the redirect-based login flow for Zerodha, Upstox, and ICICI Direct.
Angel One uses TOTP-based direct connect (no OAuth redirect).
"""

import logging
import secrets
from datetime import datetime

import requests
from flask import (
    Blueprint, flash, redirect, render_template,
    request, session, url_for
)
from flask_login import current_user, login_required

from app import db
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

    return render_template(
        'dashboard/broker_connect.html',
        broker_catalog=catalog,
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

    try:
        import pyotp
        from SmartApi import SmartConnect

        totp = pyotp.TOTP(totp_secret).now()
        smart = SmartConnect(api_key=api_key)
        data = smart.generateSession(client_id, password, totp)

        if not data or data.get('status') is False:
            raise ValueError(data.get('message', 'Angel One login failed'))

        access_token = data['data']['jwtToken']
        refresh_token = data['data'].get('refreshToken', '')

        account = _save_pending_account(
            'angel_broking', api_key, totp_secret, extra=access_token
        )
        account.set_credentials(
            client_id=client_id,
            access_token=access_token,
            api_secret=f"{api_key}:{totp_secret}:{refresh_token}",
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

        account = _save_pending_account('5paisa', client_code, f"{app_key}:{totp}", extra=jwt)
        account.set_credentials(
            client_id=client_code,
            access_token=password,
            api_secret=f"{app_key}:{totp}",
        )
        # Store JWT separately (access_token slot used for password so we re-encode)
        account.set_credentials(
            client_id=client_code,
            access_token=jwt,
            api_secret=f"{app_key}:{totp}",
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
