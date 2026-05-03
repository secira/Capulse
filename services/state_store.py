"""
State Store — Redis-backed key/value with safe in-memory fallback.

Used for cross-worker shared state: AI insight caches, alert dedup
counters, scheduler state, etc. If Redis is unavailable, transparently
falls back to an in-process dict so dev works without Redis. In
production, REDIS_URL must be set so all workers share the same store.

Values are JSON-serialized. TTLs are enforced by Redis directly and
mimicked in the in-memory fallback.
"""
import json
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from caching.redis_cache import RedisCache
    _redis = RedisCache()
except Exception as e:
    logger.warning(f"state_store: RedisCache unavailable, using in-memory fallback ({e})")
    _redis = None

_mem_lock = threading.Lock()
_mem_store: dict = {}


def _mem_get(key: str) -> Optional[Any]:
    with _mem_lock:
        entry = _mem_store.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if expires_at and time.time() > expires_at:
            _mem_store.pop(key, None)
            return None
        return value


def _mem_set(key: str, value: Any, ttl: Optional[int]) -> None:
    with _mem_lock:
        expires_at = (time.time() + ttl) if ttl else None
        _mem_store[key] = (value, expires_at)


def _mem_delete(key: str) -> None:
    with _mem_lock:
        _mem_store.pop(key, None)


def get(key: str) -> Optional[Any]:
    """Get a JSON-decoded value from the store."""
    if _redis and _redis.is_available():
        try:
            return _redis.get(key)
        except Exception as e:
            logger.warning(f"state_store.get redis error for {key}: {e}")
    return _mem_get(key)


def set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Store a value with optional TTL (seconds)."""
    if _redis and _redis.is_available():
        try:
            return _redis.set(key, value, expiry=ttl or 0)
        except Exception as e:
            logger.warning(f"state_store.set redis error for {key}: {e}")
    _mem_set(key, value, ttl)
    return True


def delete(key: str) -> bool:
    """Remove a key."""
    if _redis and _redis.is_available():
        try:
            _redis.delete(key)
        except Exception as e:
            logger.warning(f"state_store.delete redis error for {key}: {e}")
    _mem_delete(key)
    return True


def is_redis_backed() -> bool:
    """Return True if Redis is currently being used (vs in-memory fallback)."""
    return bool(_redis and _redis.is_available())
