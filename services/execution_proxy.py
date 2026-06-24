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

    # 5) Reclassify broker_error as expired_token when the message
    #    contains well-known broker auth error signatures.  The engine
    #    currently surfaces these inside the broker_error bucket because it
    #    doesn't map every broker error code — we do it client-side instead.
    if bucket == 'broker_error':
        msg_lower = message.lower()
        _AUTH_SIGNATURES = (
            'invalid_authentication', 'invalid authentication',
            'dh-901',                   # Dhan: invalid/expired token
            'dh-902',                   # Dhan: session expired
            'access token',             # generic access-token mention
            'token expired', 'token invalid',
            'session expired', 'session invalid',
            'unauthorised', 'unauthorized',
            'invalid client',
            'ab1010',                   # Zerodha: invalid token
            'invalid api key',
            'authentication failed', 'auth failed',
        )
        if any(sig in msg_lower for sig in _AUTH_SIGNATURES):
            bucket = 'expired_token'

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

    # Flat format: broker_type and user IDs are top-level in the payload.
    broker_type = (payload or {}).get('broker_type', '?')
    tc_user = (payload or {}).get('user_id', '?')
    tc_broker_account = (payload or {}).get('user_broker_id', '?')

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
        broker_type, tc_user, tc_broker_account,
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


# ─── Field-mapping helpers ────────────────────────────────────────────────────

# Exchange aliases → engine/Dhan segment string
_EXCHANGE_MAP: Dict[str, str] = {
    'NSE':          'NSE_EQ',
    'NSE_EQ':       'NSE_EQ',
    'BSE':          'BSE_EQ',
    'BSE_EQ':       'BSE_EQ',
    'NFO':          'NSE_FNO',
    'NSE_FNO':      'NSE_FNO',
    'BFO':          'BSE_FNO',
    'BSE_FNO':      'BSE_FNO',
    'CDS':          'NSE_CURRENCY',
    'NSE_CURRENCY': 'NSE_CURRENCY',
    'MCX':          'NSE_COMM',
    'NSE_COMM':     'NSE_COMM',
    'IDX_I':        'IDX_I',
}

# Order-type aliases → engine enum (MARKET | LIMIT | SL | SL_M)
_ORDER_TYPE_MAP: Dict[str, str] = {
    'MARKET':           'MARKET',
    'LIMIT':            'LIMIT',
    'SL':               'SL',
    'SL-M':             'SL_M',
    'SLM':              'SL_M',
    'SL_M':             'SL_M',
    'STOP_LOSS':        'SL',
    'STOP_LOSS_MARKET': 'SL_M',
}

# Product-type aliases → engine enum (INTRADAY | DELIVERY | CNC | MIS)
_PRODUCT_TYPE_MAP: Dict[str, str] = {
    'INTRADAY':  'INTRADAY',
    'DELIVERY':  'DELIVERY',
    'CNC':       'CNC',
    'MIS':       'MIS',
    'BO':        'MIS',
    'CO':        'MIS',
}


def _resolve_security_id(order_data: Dict[str, Any], symbol: str,
                          exchange: str) -> str:
    """Return a non-empty security_id string.

    Priority:
      1. Already present in order_data (caller supplied it or in-process
         lookup ran first).
      2. Dhan instrument master (NSE EQ + FNO, loaded at startup).
      3. BrokerHolding / BrokerPosition tables (user must have synced).
    Raises ExecutionProxyError(validation_error) if nothing works.
    """
    sid = str(order_data.get('security_id') or '').strip()
    if sid:
        return sid

    # Try instrument master.
    # Priority:
    #   a) Already loaded in memory (zero cost).
    #   b) Disk cache (shared by all workers — fast pickle read, ≤300 ms,
    #      no network). Written by the first worker to download the CSV.
    #   c) Skip entirely (would trigger a 30 MB CDN download that blocks
    #      the worker thread for 10–30 s).
    try:
        from services.dhan_service import (
            _SECID_LOADED, _DISK_CACHE_PATH, _DISK_CACHE_MAX_AGE_HOURS,
            get_security_id as _dsid,
        )
        import os as _os, time as _time

        if _SECID_LOADED:
            found = _dsid(symbol)
            if found:
                return str(found)
        else:
            # Not loaded in-memory yet — try the disk cache instead.
            try:
                _stat = _os.stat(_DISK_CACHE_PATH)
                _age_h = (_time.time() - _stat.st_mtime) / 3600
                if _age_h < _DISK_CACHE_MAX_AGE_HOURS:
                    import pickle as _pk
                    with open(_DISK_CACHE_PATH, "rb") as _fh:
                        _mapping = _pk.load(_fh)
                    _sym = symbol.upper().replace("-EQ", "").replace("-BE", "").replace("-SM", "")
                    _found = _mapping.get(_sym)
                    if _found:
                        logger.info(
                            "execution_proxy: resolved %s → %s from disk cache (age %.1fh)",
                            symbol, _found, _age_h,
                        )
                        return str(_found)
                    logger.debug("execution_proxy: %s not in disk cache", symbol)
                else:
                    logger.debug("execution_proxy: disk cache stale (%.1fh) — skipping", _age_h)
            except FileNotFoundError:
                logger.debug("execution_proxy: disk cache not yet written — skipping security_id lookup")
            except Exception as _de:
                logger.debug("execution_proxy disk cache lookup failed: %s", _de)
    except Exception as _e:
        logger.debug("execution_proxy dhan_master lookup failed: %s", _e)

    # Try synced holdings / positions as last resort
    try:
        from models_broker import BrokerHolding, BrokerPosition
        sym_upper = symbol.upper().strip()
        h = (BrokerHolding.query
             .filter(BrokerHolding.trading_symbol.ilike(sym_upper) |
                     BrokerHolding.symbol.ilike(sym_upper))
             .filter(BrokerHolding.security_id.isnot(None))
             .first())
        if h and h.security_id:
            return str(h.security_id)
        p = (BrokerPosition.query
             .filter(BrokerPosition.trading_symbol.ilike(sym_upper) |
                     BrokerPosition.symbol.ilike(sym_upper))
             .filter(BrokerPosition.security_id.isnot(None))
             .first())
        if p and p.security_id:
            return str(p.security_id)
    except Exception as _e:
        logger.debug("execution_proxy holdings lookup failed: %s", _e)

    raise ExecutionProxyError(
        'validation_error',
        f"Cannot resolve Dhan securityId for '{symbol}'. "
        "Sync your broker account first, or check the symbol spelling.",
    )


# ─── Market hours helper ─────────────────────────────────────────────────────

def _is_zerodha_amo_required(broker_type_str: str) -> bool:
    """Return True when current IST time is outside NSE/NFO market hours.

    Zerodha Kite requires variety='amo' for orders placed before 9:15 AM or
    after 3:30 PM IST — plain variety='regular' will be rejected with
    "Your order could not be converted to an After Market Order (AMO)."
    We only apply this logic to Zerodha; other brokers handle AMO differently.

    Market hours:  09:15 – 15:30 IST (Monday–Friday)
    Pre-market:    09:00 – 09:15 IST (also treated as regular by Kite)
    """
    if broker_type_str != 'zerodha':
        return False
    try:
        from datetime import datetime, timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        # Weekends — market closed; treat as AMO so the order queues for Monday
        if now_ist.weekday() >= 5:
            return True
        market_open  = now_ist.replace(hour=9,  minute=15, second=0, microsecond=0)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        return not (market_open <= now_ist <= market_close)
    except Exception:
        return False   # safe default — let the engine decide


# ─── Public API ──────────────────────────────────────────────────────────────

def place_order(broker_account, order_data: Dict[str, Any],
                user_id: Optional[int] = None,
                idempotency_key: Optional[str] = None,
                request_id: Optional[str] = None) -> Dict[str, Any]:
    """Place an order via the TC Execution Engine.

    Credentials are decrypted from TC's DB and included in every request
    so the engine always uses the freshest token — it never needs to rely
    on its own credential store (which can become stale after token rotation).

    Engine's PlaceOrderRequest (flat body):
        user_id, user_broker_id, broker_type, client_id, access_token,
        symbol, exchange, security_id, transaction_type, quantity,
        order_type, product_type, price, trigger_price, validity,
        tenant_id, tag
    """
    if broker_account is None:
        raise ExecutionProxyError('validation_error',
                                  'broker_account is required for remote execution')

    uid = int(user_id) if user_id is not None else int(broker_account.user_id)

    symbol = (order_data.get('symbol') or order_data.get('trading_symbol') or '').strip()
    trading_symbol = (order_data.get('trading_symbol') or symbol).strip()

    ex_raw = (order_data.get('exchange') or 'NSE').upper().strip()
    exchange = _EXCHANGE_MAP.get(ex_raw, ex_raw)

    ot_raw = (order_data.get('order_type') or 'MARKET').upper().strip()
    order_type = _ORDER_TYPE_MAP.get(ot_raw, 'MARKET')

    pt_raw = (order_data.get('product_type') or 'MIS').upper().strip()
    product_type = _PRODUCT_TYPE_MAP.get(pt_raw, pt_raw)

    # ── Zerodha F&O market-protection fix ───────────────────────────────────
    # Kite API rejects plain MARKET orders on F&O exchanges (NFO / BFO / MCX)
    # with "Market orders without market protection are not allowed via API."
    # Zerodha's recommended approach is to place a LIMIT order at a price
    # slightly above LTP (buy) or below LTP (sell) — equivalent to their UI
    # "market protection" feature.  We convert MARKET→LIMIT using the signal
    # entry price when one is present; otherwise we block the order with a
    # clear error so the user sets an explicit price.
    _FNO_EXCHANGES = {'NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NSE_FO', 'BSE_FO'}
    _bt_check = getattr(broker_account, 'broker_type', None)
    _bt_check_str = (
        _bt_check.value if hasattr(_bt_check, 'value') else str(_bt_check or '')
    ).lower()
    if _bt_check_str == 'zerodha' and order_type == 'MARKET' and exchange in _FNO_EXCHANGES:
        _limit_price = float(order_data.get('price') or 0)
        if _limit_price > 0:
            # Use the signal/user-supplied price as a LIMIT order —
            # effectively acts as market protection at that price level.
            order_type = 'LIMIT'
            logger.info(
                "execution_proxy: Zerodha F&O MARKET→LIMIT at %.2f "
                "(exchange=%s symbol=%s)", _limit_price, exchange, symbol
            )
        else:
            # No price available — block order and ask user to enter one.
            raise ExecutionProxyError(
                'validation_error',
                'Zerodha does not allow plain Market orders on F&O (NFO/BFO). '
                'Please select "Limit" order type and enter a price. '
                'Tip: use the signal\'s entry price as your limit price — '
                'Zerodha will execute at or better than that price.',
                broker_name=getattr(broker_account, 'broker_name', 'Zerodha'),
            )
    # ────────────────────────────────────────────────────────────────────────

    # ── Engine broker support check ──────────────────────────────────────────
    # The TC execution engine only has credential-management support for Dhan
    # and Zerodha.  All other brokers (Angel One, Upstox, Fyers, Shoonya, etc.)
    # must go through the local in-process broker_service path.  Raising with
    # bucket='broker_not_supported' tells the caller (routes.py) to fall
    # through to BrokerService.place_order_via_broker() immediately.
    # NOTE: reuse _bt_check_str (computed above); broker_type_str is defined
    # later in this function after credential decryption.
    _ENGINE_SUPPORTED_BROKERS = {'dhan', 'zerodha'}
    if _bt_check_str not in _ENGINE_SUPPORTED_BROKERS:
        raise ExecutionProxyError(
            'broker_not_supported',
            f"Broker '{_bt_check_str}' is not handled by the remote execution "
            f"engine. Falling through to in-process broker path.",
            broker_name=getattr(broker_account, 'broker_name', _bt_check_str),
        )
    # ────────────────────────────────────────────────────────────────────────

    logger.info(
        "execution_proxy place_order ENTER user=%s broker_account=%s symbol=%s "
        "exchange=%s order_type=%s product=%s qty=%s side=%s",
        uid, broker_account.id, symbol, exchange, order_type, product_type,
        order_data.get('quantity'), (order_data.get('transaction_type') or 'BUY').upper(),
    )

    security_id = _resolve_security_id(order_data, symbol, exchange)

    # Decrypt fresh credentials from TC's DB so the engine always uses the
    # current token — it must NOT fall back to its own (possibly stale) store.
    creds: Dict[str, Any] = {}
    try:
        raw = broker_account.get_credentials()
        creds = {k: v for k, v in (raw or {}).items() if v}
    except Exception as _ce:
        logger.warning("execution_proxy: could not decrypt broker credentials: %s", _ce)

    broker_name = (
        getattr(broker_account, 'broker_name', None) or
        getattr(broker_account, 'broker_type', None) or
        'dhan'
    )

    # broker_type must be the raw type identifier ('zerodha', 'dhan', etc.),
    # NOT broker_name which may contain the display name e.g. "Zerodha (ZB9220)".
    _bt = getattr(broker_account, 'broker_type', None)
    broker_type_str = (
        _bt.value if hasattr(_bt, 'value') else str(_bt)
    ).lower().strip() if _bt else 'dhan'

    payload: Dict[str, Any] = {
        'user_id':          uid,
        'user_broker_id':   int(broker_account.id),
        'broker_type':      broker_type_str,
        # Fresh credentials — engine must prefer these over its own store.
        # api_key = App API Key (Zerodha / Upstox etc.) or client ID (Dhan)
        'api_key':          creds.get('api_key') or creds.get('client_id', ''),
        # client_id = broker login/user ID (e.g. ZB9220); falls back to api_key for Dhan
        'client_id':        creds.get('broker_client_id') or creds.get('client_id', ''),
        'access_token':     creds.get('access_token', ''),
        'symbol':           symbol,
        'trading_symbol':   trading_symbol,
        'exchange':         exchange,
        'security_id':      security_id,
        'transaction_type': (order_data.get('transaction_type') or 'BUY').upper(),
        'quantity':         int(order_data.get('quantity') or 1),
        'order_type':       order_type,
        'product_type':     product_type,
        'price':            float(order_data.get('price') or 0),
        'trigger_price':    float(order_data.get('trigger_price') or 0),
        'validity':         (order_data.get('validity') or 'DAY').upper(),
        'after_market_order': bool(order_data.get('after_market_order', False)) or _is_zerodha_amo_required(broker_type_str),
        'tenant_id':        getattr(broker_account, 'tenant_id', 'live') or 'live',
        'tag':              'tc-app',
    }

    # Include api_secret / totp_secret when present (needed for some brokers).
    if creds.get('api_secret'):
        payload['api_secret'] = creds['api_secret']
    if creds.get('totp_secret'):
        payload['totp_secret'] = creds['totp_secret']

    logger.info(
        "execution_proxy place_order user=%s broker_account=%s symbol=%s "
        "exchange=%s security_id=%s qty=%s side=%s order_type=%s product=%s "
        "creds_injected=%s",
        uid, broker_account.id, symbol, exchange, security_id,
        payload['quantity'], payload['transaction_type'],
        order_type, product_type,
        bool(creds.get('access_token')),
    )

    return _request(
        'POST', '/v1/orders', payload, idempotency_key, request_id,
        broker_name=broker_name,
    )


def cancel_order(broker_account, broker_order_id: str,
                 user_id: Optional[int] = None,
                 request_id: Optional[str] = None) -> Dict[str, Any]:
    """Cancel an existing order. Engine only needs user + broker identity."""
    if broker_account is None:
        raise ExecutionProxyError('validation_error',
                                  'broker_account is required for cancel')
    uid = int(user_id) if user_id is not None else int(broker_account.user_id)
    payload = {
        'user_id':        uid,
        'user_broker_id': int(broker_account.id),
    }
    return _request(
        'POST', f'/v1/orders/{broker_order_id}/cancel', payload,
        request_id=request_id,
        broker_name=getattr(broker_account, 'broker_name', None),
    )


def get_order_status(broker_account, broker_order_id: str,
                     user_id: Optional[int] = None,
                     request_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch order status. Engine exposes GET /v1/orders/{id} (no body)."""
    raw_body = b''
    try:
        headers, _, rid = _headers(raw_body, request_id=request_id)
        url = f"{_engine_url()}/v1/orders/{broker_order_id}"
    except ExecutionProxyError:
        raise
    started = time.time()
    try:
        resp = requests.get(url, data=raw_body, headers=headers,
                            timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise ExecutionProxyError('network_error', f'Engine unreachable: {e}',
                                  broker_name=getattr(broker_account, 'broker_name', None)) from e
    latency_ms = int((time.time() - started) * 1000)
    try:
        body = resp.json()
    except ValueError:
        raise ExecutionProxyError('broker_error',
                                  f'Engine returned non-JSON (status {resp.status_code})',
                                  broker_name=getattr(broker_account, 'broker_name', None))
    if resp.status_code >= 400:
        bucket, message = _extract_error(body, resp.status_code)
        raise ExecutionProxyError(bucket, message, rid, resp.status_code,
                                  broker_name=getattr(broker_account, 'broker_name', None))
    body.setdefault('request_id', rid)
    body.setdefault('latency_ms', latency_ms)
    return body


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


# ─── Engine credential sync ──────────────────────────────────────────────────

def push_broker_credentials(broker_account) -> Dict[str, Any]:
    """Push decrypted broker credentials to the engine's admin store.

    The engine has its own credential DB and ignores `access_token` injected
    in order payloads.  This function syncs TC's live token to the engine
    every time the user saves broker settings, so the engine never uses
    stale credentials.

    Tries two endpoint conventions (upsert / update) and returns a result
    dict with {ok, status_code, body, error}.  Always safe to call —
    failures are logged but never raise.
    """
    result: Dict[str, Any] = {'ok': False, 'error': 'not attempted'}
    try:
        creds = broker_account.get_credentials() or {}
        uid   = int(broker_account.user_id)
        bid   = int(broker_account.id)
        bname = (
            getattr(broker_account, 'broker_name', None) or
            getattr(broker_account, 'broker_type', None) or 'dhan'
        )
        _bt2 = getattr(broker_account, 'broker_type', None)
        btype = (
            _bt2.value if hasattr(_bt2, 'value') else str(_bt2)
        ).lower().strip() if _bt2 else 'dhan'

        payload = {
            'user_id':        uid,
            'user_broker_id': bid,
            'broker_type':    btype,
            # api_key = App API Key (Zerodha / Upstox etc.) or client ID (Dhan)
            'api_key':        creds.get('api_key') or creds.get('client_id', ''),
            # client_id = the broker user/login ID (e.g. ZB9220 for Zerodha)
            'client_id':      creds.get('broker_client_id') or creds.get('client_id', ''),
            'access_token':   creds.get('access_token', ''),
        }
        if creds.get('api_secret'):
            payload['api_secret'] = creds['api_secret']

        raw_body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')

        admin_token = os.environ.get('EXECUTION_ADMIN_TOKEN', '')
        base_url = _engine_url()  # raises ExecutionProxyError if not configured

        # Try the two most common engine admin endpoint patterns.
        for path in (f'/admin/api/broker-accounts/{bid}',
                     '/admin/api/broker-accounts'):
            url = f"{base_url}{path}"
            headers: Dict[str, str] = {
                'Content-Type': 'application/json',
                'X-Request-ID': str(uuid.uuid4()),
            }
            if admin_token:
                headers['X-TC-Admin-Token'] = admin_token

            try:
                resp = requests.request(
                    'PUT' if str(bid) in path else 'POST',
                    url, data=raw_body, headers=headers, timeout=8,
                )
                body_text = resp.text[:400]
                try:
                    body = resp.json()
                except ValueError:
                    body = {'raw': body_text}

                if resp.status_code < 400:
                    logger.info(
                        "push_broker_credentials: synced user=%s broker=%s to engine "
                        "path=%s status=%s",
                        uid, bid, path, resp.status_code,
                    )
                    return {'ok': True, 'status_code': resp.status_code, 'body': body}

                logger.warning(
                    "push_broker_credentials: engine path=%s status=%s body=%s",
                    path, resp.status_code, body_text,
                )
                result = {'ok': False, 'status_code': resp.status_code, 'body': body}

            except requests.RequestException as _re:
                logger.warning("push_broker_credentials: network error path=%s err=%s", path, _re)
                result = {'ok': False, 'error': str(_re)}

    except ExecutionProxyError as _ee:
        result = {'ok': False, 'error': _ee.message}
    except Exception as _e:
        logger.warning("push_broker_credentials unexpected error: %s", _e)
        result = {'ok': False, 'error': str(_e)}

    return result


# ─── Routing helpers ─────────────────────────────────────────────────────────

def env_switch_on() -> bool:
    return os.environ.get('USE_REMOTE_EXEC', '').lower() in ('1', 'true', 'yes', 'on')


# Brokers whose credentials the remote execution engine understands.
# Any broker NOT in this set is handled locally by broker_service.py.
_ENGINE_SUPPORTED_BROKER_TYPES = frozenset({'dhan', 'zerodha'})


def is_broker_supported(broker_account) -> bool:
    """Return True only when the remote engine can handle this broker type.

    Used as a pre-flight gate in routes.py so we never even attempt to send
    unsupported brokers (Angel One, Upstox, Fyers, etc.) to the engine.
    """
    _bt = getattr(broker_account, 'broker_type', None)
    bt_str = (
        _bt.value if hasattr(_bt, 'value') else str(_bt or '')
    ).lower().strip()
    return bt_str in _ENGINE_SUPPORTED_BROKER_TYPES


def is_enabled_for_user(user) -> bool:
    """Route orders via the TC Execution Engine when the env switch is on.

    Gate 1: env-level `USE_REMOTE_EXEC` must be on.
    Gate 2 (per-user opt-in) is bypassed — when the env switch is on ALL
    users are routed through the engine. Set USE_REMOTE_EXEC=false/unset
    to revert everyone back to the in-process path instantly.
    """
    return env_switch_on()


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
