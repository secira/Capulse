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
