"""
Execution Engine Proxy Client
==============================
Forwards broker order placement / cancel / status to the standalone
`tc-execution-engine` service (Railway today, AWS EC2 with static IP later).

Wire contract: HMAC-SHA256 over `timestamp + raw_body` using the shared
EXECUTION_HMAC_SECRET, with a 60-second timestamp window and a UUID v4
idempotency key. Engine caches the idempotency key for 24h.

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

    `bucket` mirrors the engine's four-bucket taxonomy so callers can map
    to existing UI messages:
        broker_error     — broker rejected / timed out
        validation_error — payload invalid before the broker was contacted
        auth_error       — HMAC / signature failure on either side
        halted           — kill switch is engaged on the engine
        network_error    — proxy could not reach the engine at all
    """

    def __init__(self, bucket: str, message: str, request_id: Optional[str] = None,
                 status_code: Optional[int] = None):
        super().__init__(message)
        self.bucket = bucket
        self.message = message
        self.request_id = request_id
        self.status_code = status_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            'bucket': self.bucket,
            'error': self.message,
            'request_id': self.request_id,
        }


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
    mac = hmac.new(_hmac_secret(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode('utf-8'))
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


def _request(method: str, path: str, payload: Optional[Dict[str, Any]] = None,
             idempotency_key: Optional[str] = None,
             request_id: Optional[str] = None,
             timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    url = f"{_engine_url()}{path}"
    raw_body = json.dumps(payload or {}, separators=(',', ':'), sort_keys=True).encode('utf-8')
    headers, idem, rid = _headers(raw_body, idempotency_key, request_id)

    started = time.time()
    try:
        resp = requests.request(method, url, data=raw_body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        logger.error(
            "execution_proxy network_error request_id=%s path=%s err=%s",
            rid, path, e,
        )
        raise ExecutionProxyError('network_error', f'Engine unreachable: {e}', rid) from e

    latency_ms = int((time.time() - started) * 1000)
    logger.info(
        "execution_proxy %s %s status=%s latency_ms=%d request_id=%s idem=%s",
        method, path, resp.status_code, latency_ms, rid, idem,
    )

    try:
        body = resp.json()
    except ValueError:
        raise ExecutionProxyError(
            'broker_error',
            f'Engine returned non-JSON response (status {resp.status_code}): {resp.text[:200]}',
            rid, resp.status_code,
        )

    if resp.status_code >= 400:
        bucket = (body.get('bucket') or body.get('error_type') or 'broker_error')
        message = body.get('error') or body.get('message') or f'Engine error ({resp.status_code})'
        raise ExecutionProxyError(bucket, message, rid, resp.status_code)

    body.setdefault('request_id', rid)
    body.setdefault('latency_ms', latency_ms)
    return body


# ─── Public API ──────────────────────────────────────────────────────────────

def place_order(user_id: int, broker_account_id: int, order_data: Dict[str, Any],
                idempotency_key: Optional[str] = None,
                request_id: Optional[str] = None) -> Dict[str, Any]:
    """Place an order via the remote execution engine.

    Returns a dict with at least: order_id, broker_order_id, status,
    request_id, latency_ms. The engine writes the trade/broker_order rows
    into the shared Postgres so no DB writes are needed on this side.
    """
    payload = {
        'user_id': user_id,
        'broker_account_id': broker_account_id,
        'order': order_data,
    }
    return _request('POST', '/v1/orders', payload, idempotency_key, request_id)


def cancel_order(user_id: int, broker_account_id: int, broker_order_id: str,
                 request_id: Optional[str] = None) -> Dict[str, Any]:
    payload = {
        'user_id': user_id,
        'broker_account_id': broker_account_id,
    }
    return _request(
        'POST', f'/v1/orders/{broker_order_id}/cancel', payload,
        request_id=request_id,
    )


def get_order_status(user_id: int, broker_account_id: int, broker_order_id: str,
                     request_id: Optional[str] = None) -> Dict[str, Any]:
    payload = {
        'user_id': user_id,
        'broker_account_id': broker_account_id,
    }
    return _request(
        'GET', f'/v1/orders/{broker_order_id}', payload,
        request_id=request_id,
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
    """Simple single-switch routing.

    If the env-level USE_REMOTE_EXEC switch is on, route every user's trades
    through the engine. Otherwise, use the existing in-process path.
    The per-user `use_remote_execution` column is still honoured as an
    additional opt-out: an admin can set it to False to keep a specific
    user on the local path even when the env switch is on.
    """
    if not env_switch_on():
        return False
    opt_out = getattr(user, 'use_remote_execution', None)
    if opt_out is False:
        return False
    return True


def map_bucket_to_status(bucket: str) -> int:
    """Map the engine's error taxonomy to an HTTP status for the existing
    Trade Now JSON response shape."""
    return {
        'validation_error': 400,
        'auth_error': 401,
        'halted': 503,
        'network_error': 502,
        'broker_error': 500,
    }.get(bucket, 500)
