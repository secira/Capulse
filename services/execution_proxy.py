"""
Execution Engine Proxy Client
==============================
Forwards broker order placement / cancel / status to the standalone
`tc-execution-engine` service (Railway today, AWS EC2 with static IP later).

Architecture model: the engine is a **stateless thin executor**. TC owns
every byte of state — users, broker accounts, encrypted credentials,
mappings. On every call TC sends a fully self-contained payload:

    {
      "broker":  { name, client_id, access_token, api_secret, ... },
      "asset":   { symbol, exchange, security_id, instrument_type, ... },
      "trade":   { side, quantity, order_type, price, trigger_price,
                   target_price, stop_loss, product_type, validity },
      "context": { tc_user_id, tc_broker_account_id, correlation_id }
    }

The engine: (1) connects to the broker with the credentials in the
request, (2) places/cancels/queries the order, (3) returns
broker_order_id + status. It writes nothing to its own DB — TC writes
the resulting row into `broker_orders`.

Wire contract: HMAC-SHA256 over `timestamp + "." + raw_body` using the
shared EXECUTION_HMAC_SECRET, with a 60-second timestamp window and a
UUID v4 idempotency key. Engine caches the idempotency key for 24h.

This module is a pure HTTP client — it never imports Flask request state.
The caller (routes.py) decides whether to use it based on the env-level
USE_REMOTE_EXEC switch AND the per-user use_remote_execution flag.

With the flag off, this file is dead code and the existing in-process
TradingService path runs unchanged.
"""

import os
import json
import time
import uuid
import hmac
import hashlib
import logging
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15  # seconds — broker side can be 200-800ms; leave headroom


class ExecutionProxyError(Exception):
    """Base class for proxy-side errors.

    `bucket` mirrors the engine's taxonomy so callers can map to UI
    messages:
        broker_error        — broker rejected / timed out
        validation_error    — payload invalid before the broker was contacted
        auth_error          — HMAC / signature failure on either side
        invalid_credentials — broker rejected client_id/api_key (engine sub-bucket)
        expired_token       — broker access_token expired and needs refresh
        halted              — kill switch is engaged on the engine
        network_error       — proxy could not reach the engine at all
    """

    def __init__(self, bucket: str, message: str, request_id: Optional[str] = None,
                 status_code: Optional[int] = None,
                 broker_name: Optional[str] = None):
        super().__init__(message)
        self.bucket = bucket
        self.message = message
        self.request_id = request_id
        self.status_code = status_code
        self.broker_name = broker_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            'bucket': self.bucket,
            'error': self.message,
            'user_message': self.user_message(),
            'request_id': self.request_id,
        }

    def user_message(self) -> str:
        """Translate the technical bucket into a message safe for end-users."""
        b = (self.bucket or '').lower()
        broker = self.broker_name or 'your broker'
        # Local auth errors (no status_code) come from missing/invalid
        # EXECUTION_HMAC_SECRET — that's a server config issue, not a
        # broker login problem.
        if b == 'auth_error' and not self.status_code:
            return ("Trade execution is temporarily misconfigured on the "
                    "server. Please contact support.")
        if b == 'invalid_credentials' or (
                b == 'auth_error' and self.status_code and self.status_code >= 400):
            return (f"Login to {broker} was rejected. Please reconnect the "
                    f"broker from Settings → Broker Accounts and try again.")
        if b in ('expired_token', 'token_expired'):
            return (f"Your {broker} session has expired. Please reconnect "
                    f"the broker (a fresh access token is required daily for "
                    f"most Indian brokers).")
        if b == 'halted':
            return ("Trading is temporarily paused on the execution engine. "
                    "Please try again in a few minutes.")
        if b == 'network_error':
            return ("Could not reach the trade execution service. Please try "
                    "again in a moment.")
        if b == 'validation_error':
            return f"Order rejected: {self.message}"
        # broker_error / unknown
        return f"{broker} rejected the order: {self.message}"


class BrokerCredentialError(ExecutionProxyError):
    """Raised when a BrokerAccount is missing the credentials a given
    broker requires (e.g. an Angel One account with no TOTP secret).

    Surfaced before any HTTP call is made so we never leak partial data
    to the engine and never count against engine rate-limits.
    """

    def __init__(self, message: str, broker_name: Optional[str] = None):
        super().__init__('validation_error', message, broker_name=broker_name)


def _engine_url() -> str:
    url = os.environ.get('EXECUTION_ENGINE_URL', '').rstrip('/')
    if not url:
        raise ExecutionProxyError(
            'validation_error',
            'EXECUTION_ENGINE_URL is not configured',
        )
    return url


def _hmac_secret() -> bytes:
    secret = os.environ.get('EXECUTION_HMAC_SECRET', '')
    if not secret:
        raise ExecutionProxyError(
            'auth_error',
            'EXECUTION_HMAC_SECRET is not configured',
        )
    return secret.encode('utf-8')


def _sign(timestamp: str, raw_body: bytes) -> str:
    # Engine scheme: HMAC-SHA256(timestamp + "." + raw_body, secret)
    mac = hmac.new(_hmac_secret(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode('utf-8'))
    mac.update(b'.')
    mac.update(raw_body)
    return mac.hexdigest()


def _headers(raw_body: bytes,
             idempotency_key: Optional[str] = None,
             request_id: Optional[str] = None) -> Tuple[Dict[str, str], str, str]:
    ts = str(int(time.time()))
    idem = idempotency_key or str(uuid.uuid4())
    rid = request_id or str(uuid.uuid4())
    headers = {
        'Content-Type': 'application/json',
        'X-TC-Signature': _sign(ts, raw_body),
        'X-TC-Timestamp': ts,
        'X-TC-Idempotency': idem,
        'X-TC-Request-ID': rid,
    }
    return headers, idem, rid


# Strings the engine (or its underlying FastAPI layer) may use as bucket
# values OR as bare `error` strings. Mapped to our canonical bucket.
_KNOWN_BUCKETS = {
    'halted', 'validation_error', 'auth_error',
    'invalid_credentials', 'expired_token', 'token_expired',
    'network_error', 'broker_error',
}


def _extract_error(body: Any, status_code: int) -> Tuple[str, str]:
    """Normalise the engine's many possible error shapes into (bucket, message).

    Handles:
      * `{"bucket": "...", "error": "..."}`         — engine canonical shape
      * `{"error_type": "...", "message": "..."}`   — alt engine shape
      * `{"error": "halted"}`                       — kill-switch shortcut
      * `{"detail": "..."}` / `{"detail": [...]}`   — FastAPI default 422/4xx
      * `{}` or unknown                              — derive from status_code
      * non-dict (list, str, None)                   — coerced safely
    """
    # Defensive: engine *should* always send a JSON object, but tolerate
    # lists, bare strings, None, etc. without throwing.
    if not isinstance(body, dict):
        bucket_inferred = (
            'halted' if status_code == 503 else
            'auth_error' if status_code in (401, 403) else
            'validation_error' if status_code in (400, 422) else
            'broker_error'
        )
        return bucket_inferred, (str(body)[:200] if body else f'Engine error ({status_code})')

    # 1) Explicit bucket field
    bucket = (body.get('bucket') or body.get('error_type') or '').strip().lower()

    # 2) `error` may either be free text or one of our known bucket names
    err_field = body.get('error')
    if not bucket and isinstance(err_field, str):
        if err_field.strip().lower() in _KNOWN_BUCKETS:
            bucket = err_field.strip().lower()

    # 3) Message — prefer the most specific source available
    message: str
    if isinstance(err_field, str) and err_field.strip().lower() not in _KNOWN_BUCKETS:
        message = err_field
    elif body.get('message'):
        message = str(body['message'])
    elif 'detail' in body:
        d = body['detail']
        if isinstance(d, list):
            # FastAPI validation error list — flatten to a short summary
            parts = []
            for item in d[:5]:
                loc = '.'.join(str(x) for x in (item.get('loc') or [])[-2:])
                msg = item.get('msg') or item.get('type') or 'invalid'
                parts.append(f"{loc}: {msg}" if loc else msg)
            message = '; '.join(parts) or f'Engine error ({status_code})'
            if not bucket:
                bucket = 'validation_error'
        else:
            message = str(d)
            if not bucket and status_code in (401, 403):
                bucket = 'auth_error'
    else:
        message = f'Engine error ({status_code})'

    # 4) Last-resort bucket inference from HTTP status when engine sent none
    if not bucket:
        if status_code == 503:
            bucket = 'halted'
        elif status_code in (401, 403):
            bucket = 'auth_error'
        elif status_code in (400, 422):
            bucket = 'validation_error'
        else:
            bucket = 'broker_error'

    return bucket, message


def _request(method: str, path: str, payload: Optional[Dict[str, Any]] = None,
             idempotency_key: Optional[str] = None,
             request_id: Optional[str] = None,
             timeout: int = DEFAULT_TIMEOUT,
             broker_name: Optional[str] = None) -> Dict[str, Any]:
    """Send a signed HTTPS call to the engine and return parsed JSON.

    All sensitive fields (broker.client_id/access_token/api_secret/...)
    are scrubbed from logs. Only the request_id, path, status, latency,
    and broker_type are logged — never credentials.
    """
    url = f"{_engine_url()}{path}"
    raw_body = json.dumps(payload or {}, separators=(',', ':'), sort_keys=True).encode('utf-8')
    headers, idem, rid = _headers(raw_body, idempotency_key, request_id)

    broker_type = ((payload or {}).get('broker') or {}).get('broker_type', '?')
    tc_ctx = (payload or {}).get('context') or {}

    started = time.time()
    try:
        resp = requests.request(method, url, data=raw_body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        logger.error(
            "execution_proxy network_error request_id=%s path=%s broker_type=%s err=%s",
            rid, path, broker_type, e,
        )
        raise ExecutionProxyError(
            'network_error', f'Engine unreachable: {e}', rid,
            broker_name=broker_name,
        ) from e

    latency_ms = int((time.time() - started) * 1000)
    logger.info(
        "execution_proxy %s %s status=%s latency_ms=%d request_id=%s idem=%s "
        "broker_type=%s tc_user=%s tc_broker_account=%s",
        method, path, resp.status_code, latency_ms, rid, idem,
        broker_type, tc_ctx.get('tc_user_id'), tc_ctx.get('tc_broker_account_id'),
    )

    try:
        body = resp.json()
    except ValueError:
        raise ExecutionProxyError(
            'broker_error',
            f'Engine returned non-JSON response (status {resp.status_code}): {resp.text[:200]}',
            rid, resp.status_code, broker_name=broker_name,
        )

    if resp.status_code >= 400:
        bucket, message = _extract_error(body, resp.status_code)
        logger.warning(
            "execution_proxy engine_error request_id=%s path=%s status=%s "
            "bucket=%s broker_type=%s msg=%s",
            rid, path, resp.status_code, bucket, broker_type, message[:200],
        )
        raise ExecutionProxyError(
            bucket, message, rid, resp.status_code,
            broker_name=broker_name,
        )

    body.setdefault('request_id', rid)
    body.setdefault('latency_ms', latency_ms)
    return body


# ─── Public API ──────────────────────────────────────────────────────────────

_TRADE_FIELD_ALIASES = {
    'side': 'transaction_type',
    'qty': 'quantity',
    'product': 'product_type',
}

# Fields routed into the `asset` block (everything else goes into `trade`)
_ASSET_FIELDS = {
    'symbol', 'trading_symbol', 'exchange', 'security_id',
    'instrument_type', 'expiry', 'strike', 'option_type', 'lot_size',
}


# Per-broker required-credential matrix.
# Engine maps these canonical fields to broker-SDK fields on its side.
# If any required field is missing/empty we raise BEFORE calling the engine
# so the user gets an actionable error and we don't burn an engine call.
_BROKER_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    'dhan':           ('client_id', 'access_token'),
    'zerodha':        ('client_id', 'access_token'),
    'upstox':         ('access_token',),
    'fyers':          ('client_id', 'access_token', 'api_secret'),
    '5paisa':         ('client_id', 'access_token'),
    'alice_blue':     ('client_id', 'access_token'),
    'shoonya':        ('client_id', 'access_token', 'api_secret'),
    'angel_broking':  ('client_id', 'api_secret', 'totp_secret'),
}


def _build_broker_block(broker_account) -> Dict[str, Any]:
    """Decrypt the credentials on a BrokerAccount row and shape them for
    the engine. Engine is stateless: it uses these creds inline, never
    stores them.

    For Angel One the api_secret field is stored as `"<api_secret>|<totp>"`
    after decryption — we split it back out here.

    Validates against the per-broker required-field matrix and raises
    BrokerCredentialError with a clear message when something is missing.
    """
    if broker_account is None:
        raise ExecutionProxyError(
            'validation_error',
            'broker_account is required for remote execution',
        )

    client_id = broker_account.decrypt_data(broker_account.api_key)
    access_token = broker_account.decrypt_data(broker_account.access_token)
    api_secret_raw = broker_account.decrypt_data(broker_account.api_secret)

    api_secret: Optional[str] = api_secret_raw
    totp_secret: Optional[str] = None
    if api_secret_raw and '|' in api_secret_raw:
        api_secret, totp_secret = api_secret_raw.split('|', 1)

    broker_type = (broker_account.broker_type or '').lower()
    broker_name = broker_account.broker_name or broker_type

    block: Dict[str, Any] = {
        'broker_type': broker_type,
        'broker_name': broker_name,
        'client_id': client_id,
        'access_token': access_token,
        'api_secret': api_secret,
    }
    if totp_secret:
        block['totp_secret'] = totp_secret

    required = _BROKER_REQUIRED_FIELDS.get(broker_type)
    if required is None:
        raise BrokerCredentialError(
            f"Remote execution is not yet supported for broker '{broker_type}'. "
            f"Supported: {sorted(_BROKER_REQUIRED_FIELDS.keys())}.",
            broker_name=broker_name,
        )
    missing = [f for f in required if not block.get(f)]
    if missing:
        logger.warning(
            "execution_proxy missing_credentials broker_type=%s tc_broker_account=%s missing=%s",
            broker_type, broker_account.id, missing,
        )
        raise BrokerCredentialError(
            f"{broker_name} is missing required credentials: "
            f"{', '.join(missing)}. Please reconnect this broker from "
            f"Settings → Broker Accounts.",
            broker_name=broker_name,
        )
    return block


def _split_order_into_asset_and_trade(order_data: Dict[str, Any]
                                      ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Partition the flat order_data dict TC passes today into the
    engine's `asset` and `trade` blocks, applying field-name aliases."""
    asset: Dict[str, Any] = {}
    trade: Dict[str, Any] = {}
    for k, v in (order_data or {}).items():
        if k in _ASSET_FIELDS:
            asset[k] = v
        else:
            trade[_TRADE_FIELD_ALIASES.get(k, k)] = v
    return asset, trade


def _context_block(broker_account, user_id: Optional[int] = None,
                   correlation_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        'tc_user_id': int(user_id) if user_id is not None else int(broker_account.user_id),
        'tc_broker_account_id': int(broker_account.id),
        'tc_tenant_id': broker_account.tenant_id or 'live',
        'correlation_id': correlation_id,
    }


def place_order(broker_account, order_data: Dict[str, Any],
                user_id: Optional[int] = None,
                idempotency_key: Optional[str] = None,
                request_id: Optional[str] = None) -> Dict[str, Any]:
    """Place an order via the remote execution engine.

    Sends a fully self-contained payload — broker credentials, asset
    details, trade parameters, and TC identifiers — so the engine can
    execute without any local state of its own.

    Returns a dict with at least: order_id, broker_order_id, status,
    request_id, latency_ms.
    """
    broker = _build_broker_block(broker_account)
    asset, trade = _split_order_into_asset_and_trade(order_data)
    payload = {
        'broker': broker,
        'asset': asset,
        'trade': trade,
        'context': _context_block(broker_account, user_id, request_id),
    }
    return _request(
        'POST', '/v1/orders', payload, idempotency_key, request_id,
        broker_name=broker.get('broker_name'),
    )


def cancel_order(broker_account, broker_order_id: str,
                 user_id: Optional[int] = None,
                 request_id: Optional[str] = None) -> Dict[str, Any]:
    broker = _build_broker_block(broker_account)
    payload = {
        'broker': broker,
        'broker_order_id': broker_order_id,
        'context': _context_block(broker_account, user_id, request_id),
    }
    return _request(
        'POST', f'/v1/orders/{broker_order_id}/cancel', payload,
        request_id=request_id,
        broker_name=broker.get('broker_name'),
    )


def get_order_status(broker_account, broker_order_id: str,
                     user_id: Optional[int] = None,
                     request_id: Optional[str] = None) -> Dict[str, Any]:
    # NOTE: POST (not GET) is intentional. The engine is stateless and
    # must receive the decrypted broker block to talk to the broker for
    # status — a GET cannot carry that body safely. Same pattern as
    # /v1/orders and /v1/orders/{id}/cancel.
    broker = _build_broker_block(broker_account)
    payload = {
        'broker': broker,
        'broker_order_id': broker_order_id,
        'context': _context_block(broker_account, user_id, request_id),
    }
    return _request(
        'POST', f'/v1/orders/{broker_order_id}/status', payload,
        request_id=request_id,
        broker_name=broker.get('broker_name'),
    )


def healthz() -> Dict[str, Any]:
    """Unauthenticated liveness probe for the admin page."""
    started = time.time()
    try:
        url = f"{_engine_url()}/healthz"
    except ExecutionProxyError as e:
        return {'ok': False, 'status_code': None, 'latency_ms': 0, 'error': e.message}
    try:
        resp = requests.get(url, timeout=5)
        body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {'raw': resp.text}
        return {
            'ok': resp.status_code == 200,
            'status_code': resp.status_code,
            'latency_ms': int((time.time() - started) * 1000),
            'body': body,
        }
    except requests.RequestException as e:
        return {
            'ok': False,
            'status_code': None,
            'latency_ms': int((time.time() - started) * 1000),
            'error': str(e),
        }


def version() -> Dict[str, Any]:
    """Return engine deployed git SHA (or error dict)."""
    try:
        url = f"{_engine_url()}/version"
    except ExecutionProxyError as e:
        return {'error': e.message}
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return {'error': 'Engine /version returned non-JSON'}
        return {'error': f'Engine /version returned {resp.status_code}'}
    except requests.RequestException as e:
        return {'error': str(e)}


def get_halt() -> Dict[str, Any]:
    """Return current halt state from the engine.

    Signs an empty body and sends it as the actual request body so the
    engine's HMAC verification matches exactly the transmitted bytes.
    """
    raw_body = b''
    try:
        headers, _, _ = _headers(raw_body)
        url = f"{_engine_url()}/v1/halt"
    except ExecutionProxyError as e:
        return {'error': e.message}
    try:
        resp = requests.get(url, data=raw_body, headers=headers, timeout=5)
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return {'error': 'Engine /v1/halt returned non-JSON'}
        return {'error': f'Engine returned {resp.status_code}: {resp.text[:200]}'}
    except requests.RequestException as e:
        return {'error': str(e)}


def set_halt(halted: bool, admin_token: Optional[str] = None) -> Dict[str, Any]:
    """Toggle the engine kill switch. Engine requires X-TC-Admin-Token."""
    payload = {'halted': bool(halted)}
    raw_body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    try:
        headers, _, _ = _headers(raw_body)
        url = f"{_engine_url()}/v1/halt"
    except ExecutionProxyError as e:
        return {'ok': False, 'error': e.message}
    token = admin_token or os.environ.get('EXECUTION_ADMIN_TOKEN', '')
    if token:
        headers['X-TC-Admin-Token'] = token
    try:
        resp = requests.put(url, data=raw_body, headers=headers, timeout=5)
        if resp.status_code >= 400:
            return {'ok': False, 'status_code': resp.status_code, 'error': resp.text[:200]}
        try:
            return {'ok': True, 'body': resp.json()}
        except ValueError:
            return {'ok': True, 'body': {'raw': resp.text}}
    except requests.RequestException as e:
        return {'ok': False, 'error': str(e)}


# ─── Routing helpers ─────────────────────────────────────────────────────────

def env_switch_on() -> bool:
    return os.environ.get('USE_REMOTE_EXEC', '').lower() in ('1', 'true', 'yes', 'on')


def is_enabled_for_user(user) -> bool:
    """Two-gate routing — both gates must explicitly be True.

    Gate 1: env-level `USE_REMOTE_EXEC` must be on.
    Gate 2: per-user `User.use_remote_execution` must be exactly True
            (None / missing / anything-not-True → local path).

    Explicit opt-in keeps users on the proven in-process path until an
    operator deliberately moves them to the engine — important during
    rollout and any partial-outage of the engine.
    """
    if not env_switch_on():
        return False
    return getattr(user, 'use_remote_execution', False) is True


def map_bucket_to_status(bucket: str) -> int:
    """Map the engine's error taxonomy to an HTTP status for the existing
    Trade Now JSON response shape."""
    return {
        'validation_error': 400,
        'auth_error': 401,
        'invalid_credentials': 401,
        'expired_token': 401,
        'token_expired': 401,
        'halted': 503,
        'network_error': 502,
        'broker_error': 500,
    }.get((bucket or '').lower(), 500)
