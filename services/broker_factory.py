import logging
from typing import Dict, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)

BROKER_REGISTRY = {
    "dhan": "brokers.dhan.DhanBroker",
    "zerodha": "brokers.zerodha.ZerodhaBroker",
    "fyers": "brokers.fyers.FyersBroker",
    "shoonya": "brokers.shoonya.ShoonyaBroker",
    "upstox": "brokers.upstox.UpstoxBroker",
    "angel_broking": "brokers.angel.AngelBroker",
    "angel": "brokers.angel.AngelBroker",
    "5paisa": "brokers.fivepaisa.FivePaisaBroker",
    "alice_blue": "brokers.aliceblue.AliceBlueBroker",
    "aliceblue": "brokers.aliceblue.AliceBlueBroker",
}


def _import_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def get_broker(broker_name: str, credentials: Dict[str, str]) -> Optional[BrokerBase]:
    broker_name = broker_name.lower().strip()
    if broker_name not in BROKER_REGISTRY:
        logger.error(f"Unsupported broker: {broker_name}")
        return None
    try:
        cls = _import_class(BROKER_REGISTRY[broker_name])
        return cls(credentials)
    except Exception as e:
        logger.error(f"Failed to create broker {broker_name}: {e}")
        return None


def get_data_broker_for_user(user_id: int) -> Optional[BrokerBase]:
    """
    Return a broker instance to use for market data for the given user.
    Priority:
      1. BrokerAccount with is_data_broker=True (user picked from their connected brokers)
      2. DataApiBroker row (legacy separate-credentials model)
    """
    try:
        from app import db
        from models_broker import BrokerAccount
        account = BrokerAccount.query.filter_by(
            user_id=user_id,
            is_data_broker=True,
            is_active=True,
        ).first()

        if account:
            creds = account.get_credentials()
            broker_type = account.broker_type.value if hasattr(account.broker_type, 'value') else str(account.broker_type)
            broker = get_broker(broker_type, creds)
            if broker:
                logger.info(f"Data API broker for user {user_id}: {account.broker_name} (account id={account.id})")
            return broker
    except Exception as e:
        logger.error(f"Failed to get data broker from BrokerAccount for user {user_id}: {e}")

    try:
        from models_broker import DataApiBroker
        legacy = DataApiBroker.query.filter_by(
            user_id=user_id,
            is_active=True,
            connection_status='connected',
        ).first()
        if not legacy:
            return None
        creds = legacy.get_credentials()
        broker = get_broker(legacy.broker_type, creds)
        if broker:
            logger.info(f"Data API broker (legacy) for user {user_id}: {legacy.broker_name}")
        return broker
    except Exception as e:
        logger.error(f"Failed to get data broker for user {user_id}: {e}")
        return None


def get_admin_data_brokers() -> list:
    """
    Return admin-managed data-source brokers ordered by priority (1=primary, 2=secondary).
    Invisible to end users — used as a system-wide fallback after the user's own broker.
    Returns a list of (priority, broker_type, broker_name, BrokerBase) tuples.
    Failures to instantiate are skipped so the chain still falls through.
    """
    out = []
    try:
        from models_broker import AdminDataBroker
        rows = AdminDataBroker.query.filter_by(is_active=True).order_by(AdminDataBroker.priority.asc()).all()
        for row in rows:
            try:
                creds = row.get_credentials()
                broker = get_broker(row.broker_type, creds)
                if broker:
                    out.append((row.priority, row.broker_type, row.broker_name, broker))
                else:
                    logger.warning(f"Admin data broker priority={row.priority} ({row.broker_type}) could not be instantiated")
            except Exception as e:
                logger.error(f"Admin data broker priority={row.priority} init error: {e}")
    except Exception as e:
        logger.error(f"get_admin_data_brokers failed: {e}")
    return out


INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "INDIAVIX", "INDIA VIX"}


def _underlying_from_symbol(symbol: str) -> Optional[str]:
    """
    Map an index / future / option contract symbol to its underlying index name
    that admin / user brokers know via get_price(). Returns None for equities.

    Examples:
      NIFTY                        → NIFTY
      BANKNIFTY                    → BANKNIFTY
      NIFTY25NOV24500CE            → NIFTY     (option)
      NIFTYFUT / NIFTY-FUT         → NIFTY     (future)
      RELIANCE                     → None
    """
    if not symbol:
        return None
    s = symbol.upper().strip().replace(" ", "")
    if s in {"NIFTY", "NIFTY50"}:
        return "NIFTY"
    if s in {"INDIAVIX", "VIX"}:
        return "INDIA VIX"
    for idx in ("BANKNIFTY", "FINNIFTY", "SENSEX", "NIFTY"):
        if s.startswith(idx):
            tail = s[len(idx):]
            if not tail or tail.endswith(("CE", "PE", "FUT")) or tail.startswith(("FUT", "-FUT")):
                return idx
    return None


def get_index_price_with_fallback(symbol: str, user_id: Optional[int] = None) -> tuple:
    """
    Resolve a live index price via the data-broker fallback chain:
      1. User's own data broker (BrokerAccount / DataApiBroker)
      2. Admin primary data broker
      3. Admin secondary data broker

    Returns (price, source_label). price = 0.0 when no broker could supply one.
    source_label is e.g. 'user:Dhan', 'admin:Fyers (P1)', or 'unavailable'.
    """
    underlying = _underlying_from_symbol(symbol) or symbol.upper()

    if user_id:
        try:
            ub = get_data_broker_for_user(user_id)
            if ub:
                try:
                    if ub.connect():
                        px = float(ub.get_price(underlying) or 0)
                        if px > 0:
                            return px, f"user:{ub.__class__.__name__.replace('Broker','')}"
                except Exception as e:
                    logger.debug(f"user data broker get_price({underlying}) failed: {e}")
        except Exception as e:
            logger.debug(f"user data broker lookup failed: {e}")

    for prio, btype, bname, broker in get_admin_data_brokers():
        try:
            if not broker.connect():
                continue
            px = float(broker.get_price(underlying) or 0)
            if px > 0:
                return px, f"admin:{bname} (P{prio})"
        except Exception as e:
            logger.debug(f"admin broker P{prio} {btype} get_price({underlying}) failed: {e}")
            continue

    return 0.0, "unavailable"


def get_supported_brokers() -> list:
    return [
        {"key": "dhan", "name": "Dhan", "supports_direct_chain": True},
        {"key": "zerodha", "name": "Zerodha", "supports_direct_chain": False},
        {"key": "fyers", "name": "Fyers", "supports_direct_chain": True},
        {"key": "shoonya", "name": "Shoonya (Finvasia)", "supports_direct_chain": False},
        {"key": "upstox", "name": "Upstox", "supports_direct_chain": True},
        {"key": "angel_broking", "name": "Angel One", "supports_direct_chain": True},
        {"key": "5paisa", "name": "5 Paisa", "supports_direct_chain": True},
        {"key": "alice_blue", "name": "Alice Blue", "supports_direct_chain": False},
    ]
