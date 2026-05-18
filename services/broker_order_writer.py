"""
Broker Order Writer
===================
Persists the execution engine's response into TC's local `broker_orders`
table and, when the engine reports an auth failure, flips the
corresponding `BrokerAccount.connection_status` to `EXPIRED` so the user
is prompted to reconnect.

The engine itself writes nothing to TC's DB — this module is the single
seam where engine responses cross into TC's persistence layer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app import db
from models_broker import (
    BrokerAccount, BrokerOrder, ConnectionStatus,
    OrderStatus, OrderType, ProductType, TransactionType,
)

logger = logging.getLogger(__name__)


# ─── enum coercion ──────────────────────────────────────────────────────────

# Per-enum alias tables. Kept separate so a wayward "buy" string can never
# accidentally resolve to a TransactionType when an OrderType was expected.
_ENUM_ALIASES = {
    TransactionType: {
        'b': TransactionType.BUY, 'buy': TransactionType.BUY,
        's': TransactionType.SELL, 'sell': TransactionType.SELL,
    },
    OrderType: {
        'mkt': OrderType.MARKET, 'market': OrderType.MARKET,
        'lmt': OrderType.LIMIT, 'limit': OrderType.LIMIT,
        'sl_m': OrderType.SL_M, 'slm': OrderType.SL_M,
        'stop_loss': OrderType.SL, 'sl': OrderType.SL,
    },
    ProductType: {
        'mis': ProductType.MIS, 'cnc': ProductType.CNC,
        'intraday': ProductType.INTRADAY, 'delivery': ProductType.DELIVERY,
    },
    OrderStatus: {
        'open': OrderStatus.OPEN, 'pending': OrderStatus.PENDING,
        'complete': OrderStatus.COMPLETE, 'completed': OrderStatus.COMPLETE,
        'filled': OrderStatus.COMPLETE,
        'cancelled': OrderStatus.CANCELLED, 'canceled': OrderStatus.CANCELLED,
        'rejected': OrderStatus.REJECTED,
    },
}


def _coerce_enum(enum_cls, raw, default):
    """Map a free-form string from the engine/UI into the given Enum.

    Aliases are scoped per enum class — passing 'buy' with enum_cls=OrderType
    will return the default, never TransactionType.BUY.
    """
    if raw is None:
        return default
    s = str(raw).strip().lower().replace('-', '_')
    for m in enum_cls:
        if m.value == s or m.name.lower() == s:
            return m
    return _ENUM_ALIASES.get(enum_cls, {}).get(s, default)


# ─── success path ───────────────────────────────────────────────────────────

def record_engine_order(
    broker_account: BrokerAccount,
    order_data: Dict[str, Any],
    engine_response: Dict[str, Any],
) -> BrokerOrder:
    """Insert a `broker_orders` row from the engine's response.

    Commits the row. Returns the persisted `BrokerOrder`. Designed to be
    idempotent on (broker_account_id, broker_order_id) — if the engine
    retries and we already have a row with the same broker_order_id for
    the same broker_account, we update it in place instead of inserting
    a duplicate.
    """
    broker_order_id = engine_response.get('broker_order_id') or engine_response.get('order_id')
    status_raw = (engine_response.get('order_status')
                  or engine_response.get('status')
                  or 'PENDING')

    existing: Optional[BrokerOrder] = None
    if broker_order_id:
        existing = BrokerOrder.query.filter_by(
            broker_account_id=broker_account.id,
            broker_order_id=str(broker_order_id),
        ).first()

    target = existing or BrokerOrder(
        broker_account_id=broker_account.id,
        tenant_id=broker_account.tenant_id or 'live',
        broker_order_id=str(broker_order_id) if broker_order_id else None,
        correlation_id=engine_response.get('request_id'),
        symbol=order_data.get('symbol') or '',
        trading_symbol=order_data.get('trading_symbol') or order_data.get('symbol') or '',
        exchange=order_data.get('exchange') or 'NSE',
        security_id=order_data.get('security_id'),
        transaction_type=_coerce_enum(
            TransactionType,
            order_data.get('transaction_type') or order_data.get('side'),
            TransactionType.BUY,
        ),
        order_type=_coerce_enum(
            OrderType, order_data.get('order_type'), OrderType.MARKET,
        ),
        product_type=_coerce_enum(
            ProductType,
            order_data.get('product_type') or order_data.get('product'),
            ProductType.MIS,
        ),
        quantity=int(order_data.get('quantity') or order_data.get('qty') or 0),
        price=float(order_data.get('price') or 0) or 0.0,
        trigger_price=float(order_data.get('trigger_price') or 0) or 0.0,
        order_time=datetime.utcnow(),
    )

    target.order_status = _coerce_enum(OrderStatus, status_raw, OrderStatus.PENDING)
    target.status_message = engine_response.get('status_message')
    target.filled_quantity = int(engine_response.get('filled_quantity') or 0)
    target.pending_quantity = max(target.quantity - target.filled_quantity, 0)
    if engine_response.get('avg_execution_price'):
        target.avg_execution_price = float(engine_response['avg_execution_price'])
    target.last_updated = datetime.utcnow()
    if target.order_status == OrderStatus.COMPLETE and not target.execution_time:
        target.execution_time = datetime.utcnow()

    if not existing:
        db.session.add(target)
    db.session.commit()

    logger.info(
        "broker_order_writer recorded broker_order_id=%s tc_broker_order_id=%s "
        "broker_account=%s status=%s qty=%s filled=%s",
        target.broker_order_id, target.id, broker_account.id,
        target.order_status.value if target.order_status else None,
        target.quantity, target.filled_quantity,
    )
    return target


# ─── failure path ───────────────────────────────────────────────────────────

_AUTH_BUCKETS = {'invalid_credentials', 'expired_token', 'token_expired'}


def handle_engine_failure(broker_account: BrokerAccount, bucket: str,
                          message: str) -> None:
    """When the engine reports an auth-related failure, flip the broker
    account's connection_status to EXPIRED so the UI shows the
    'Reconnect' affordance and the user knows to act.

    Non-auth failures (broker_error, validation_error, network_error)
    don't touch the connection_status — those are transient.
    """
    b = (bucket or '').lower()
    if b not in _AUTH_BUCKETS:
        return
    try:
        broker_account.connection_status = ConnectionStatus.EXPIRED.value
        db.session.commit()
        logger.warning(
            "broker_order_writer marked_expired broker_account=%s broker_type=%s "
            "bucket=%s msg=%s",
            broker_account.id, broker_account.broker_type, bucket, message[:200],
        )
    except Exception as e:
        db.session.rollback()
        logger.error(
            "broker_order_writer failed_to_mark_expired broker_account=%s err=%s",
            broker_account.id, e,
        )
