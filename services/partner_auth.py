"""
B2B Partner API auth — Bearer token (`Authorization: Bearer tc_live_…`).

Tokens are generated server-side, returned ONCE at signup, and stored only as
a Werkzeug password hash. The 12-char prefix (`tc_live_xxxx`) is stored in
clear so we can do a fast indexed lookup before the constant-time compare.
"""
import logging
import secrets
from datetime import datetime
from functools import wraps

from flask import g, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from models_partner_api import ApiPartner

logger = logging.getLogger(__name__)

KEY_ENV   = 'live'   # could later swap to 'test' for sandbox keys
KEY_BYTES = 32       # 256 bits of entropy


def generate_api_key() -> tuple[str, str, str]:
    """
    Returns (raw_key, prefix, hash). Show raw_key to the partner ONCE.
    """
    body   = secrets.token_urlsafe(KEY_BYTES).rstrip('=')
    raw    = f"tc_{KEY_ENV}_{body}"
    prefix = raw[:16]                       # "tc_live_xxxxxxxx"
    hashed = generate_password_hash(raw)
    return raw, prefix, hashed


def _extract_bearer() -> str | None:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    # Fallback: also accept ?api_key= for quick curl tests
    return request.args.get('api_key')


def authenticate_partner(raw_key: str) -> ApiPartner | None:
    if not raw_key or len(raw_key) < 16:
        return None
    prefix = raw_key[:16]
    # Index-bound first pass — usually 1 row; we still constant-time compare.
    candidates = ApiPartner.query.filter_by(api_key_prefix=prefix, is_active=True).all()
    for p in candidates:
        try:
            if check_password_hash(p.api_key_hash, raw_key):
                return p
        except Exception:
            continue
    return None


def partner_api_key_required(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        raw = _extract_bearer()
        if not raw:
            return jsonify({
                'success': False,
                'error': 'Missing Authorization header. Use: Authorization: Bearer <api_key>',
                'code': 'NO_API_KEY',
            }), 401

        partner = authenticate_partner(raw)
        if partner is None:
            return jsonify({'success': False, 'error': 'Invalid or revoked API key',
                            'code': 'INVALID_API_KEY'}), 401

        # Touch last_seen_at (best-effort, never blocks the request)
        try:
            partner.last_seen_at = datetime.utcnow()
            db.session.commit()
        except Exception:
            db.session.rollback()

        g.partner = partner
        return fn(*args, **kwargs)

    return _wrapped
