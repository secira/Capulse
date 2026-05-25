"""
Admin Data-Broker Pool — runway-aware selection across the admin slots.

The admin configures up to 2 broker accounts (slot A = priority 1, slot B =
priority 2) used as the primary market-data source for all users. To avoid
both slots dying at the same time, slot A and slot B should be DIFFERENT
brokers (e.g. Dhan + Zerodha) so their daily token-expiry windows are offset.

get_active_admin_broker() returns the slot with the longest remaining runway
above the safety threshold; if both are under the threshold it picks the
longer one anyway so we don't return None just because both are close to
expiry.

Used by services/broker_factory.get_admin_data_brokers() and by anywhere
that needs a single live admin broker for a quick data lookup.
"""

import logging
from datetime import datetime
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# A slot must have at least this much runway to be considered "fresh".
_RUNWAY_THRESHOLD_MIN = 30


def _list_pool_rows():
    """Return active AdminDataBroker rows (priority-ordered). Empty on error."""
    try:
        from models_broker import AdminDataBroker
        return (
            AdminDataBroker.query
            .filter_by(is_active=True)
            .order_by(AdminDataBroker.priority.asc())
            .all()
        )
    except Exception as e:
        logger.error(f"admin_pool: list_pool_rows failed: {e}")
        return []


def _runway_minutes(row) -> Optional[int]:
    """Minutes until row's predicted token expiry. None when not stamped."""
    try:
        return row.minutes_until_expiry()
    except Exception:
        return None


def list_pool_status() -> List[dict]:
    """Snapshot of all configured slots for the admin UI."""
    out = []
    for row in _list_pool_rows():
        out.append({
            "id": row.id,
            "priority": row.priority,
            "slot": "A" if row.priority == 1 else ("B" if row.priority == 2 else f"P{row.priority}"),
            "broker_type": row.broker_type,
            "broker_name": row.broker_name,
            "connection_status": row.connection_status,
            "minutes_left": _runway_minutes(row),
            "expiry_human": row.expiry_human() if hasattr(row, "expiry_human") else "—",
            "last_health_check": row.last_health_check,
            "health_check_message": row.health_check_message,
        })
    return out


def get_active_admin_broker():
    """Return (BrokerBase instance, row) for the slot with the longest runway.

    Selection logic:
      1. Skip slots whose connection_status == 'expired'.
      2. Among remaining slots, prefer the one with the longest runway above
         the safety threshold (>30 min).
      3. If none above threshold but at least one is still positive, pick the
         longest-runway slot anyway (better than nothing).
      4. If no rows are usable, return (None, None).
    """
    rows = [r for r in _list_pool_rows() if (r.connection_status or '') != 'expired']
    if not rows:
        return None, None

    # An unstamped-but-healthy slot should NOT be excluded just because we don't
    # know its expiry yet. Treat unknown runway as "very long" so it sorts above
    # known-near-expiry slots but a freshly stamped longer-runway slot still wins.
    UNKNOWN_RUNWAY = 10 ** 9
    scored: List[Tuple[int, object]] = []
    for r in rows:
        m = _runway_minutes(r)
        scored.append((m if m is not None else UNKNOWN_RUNWAY, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_m, best_row = scored[0]

    # If even the best slot is already past expiry, give up. Unknown-runway
    # slots are kept (they sort as UNKNOWN_RUNWAY, far above 0).
    if best_m <= 0:
        return None, best_row

    try:
        from services.broker_factory import get_broker
        creds = best_row.get_credentials()
        broker = get_broker(best_row.broker_type, creds)
        if broker is None:
            logger.warning(
                f"admin_pool: best slot ({best_row.broker_type}, P{best_row.priority}) "
                f"could not be instantiated"
            )
            return None, best_row
        logger.debug(
            f"admin_pool: selected P{best_row.priority} {best_row.broker_name} "
            f"({best_m}m runway)"
        )
        return broker, best_row
    except Exception as e:
        logger.error(f"admin_pool: get_active_admin_broker init error: {e}")
        return None, best_row


def get_admin_brokers_by_runway() -> List[Tuple[int, str, str, object, object]]:
    """All admin slots ordered by remaining runway desc (most-runway first).

    Returns list of (priority, broker_type, broker_name, BrokerBase, row).
    Skips rows that fail to instantiate. Skips already-expired rows.
    """
    rows = [r for r in _list_pool_rows() if (r.connection_status or '') != 'expired']
    scored = []
    for r in rows:
        m = _runway_minutes(r)
        scored.append((m if m is not None else 0, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    try:
        from services.broker_factory import get_broker
        for runway, row in scored:
            try:
                creds = row.get_credentials()
                broker = get_broker(row.broker_type, creds)
                if broker is None:
                    continue
                out.append((row.priority, row.broker_type, row.broker_name, broker, row))
            except Exception as e:
                logger.debug(f"admin_pool: row P{row.priority} init failed: {e}")
                continue
    except Exception as e:
        logger.error(f"admin_pool: get_admin_brokers_by_runway failed: {e}")
    return out
