"""
DhanDataService — thin service layer that wraps the DhanBroker adapter
and provides market data for the Live Market Pulse, dashboard index cards,
and F&O engine when a user has Dhan configured as their Data API broker.
"""
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def _get_dhan_broker(user_id: Optional[int] = None):
    """
    Return a connected DhanBroker instance for the given user's DataApiBroker,
    or None if not configured / not Dhan.
    """
    try:
        from services.broker_factory import get_data_broker_for_user
        if user_id is None:
            return None
        broker = get_data_broker_for_user(user_id)
        if broker is None or broker.BROKER_NAME != "dhan":
            return None
        if not broker.connect():
            logger.warning("DhanDataService: broker connect() returned False")
            return None
        return broker
    except Exception as e:
        logger.error(f"DhanDataService._get_dhan_broker error: {e}")
        return None


def _get_any_dhan_broker():
    """
    Try to find any active Dhan DataApiBroker across users (used for system-level
    data calls when no specific user_id is known).
    Returns a connected DhanBroker or None.
    """
    try:
        from app import db
        from models_broker import DataApiBroker
        accounts = DataApiBroker.query.filter_by(
            broker_type="dhan", is_active=True, connection_status="connected"
        ).all()
        for account in accounts:
            creds = account.get_credentials()
            from brokers.dhan import DhanBroker
            broker = DhanBroker(creds)
            if broker.connect():
                logger.info(f"DhanDataService: using account id={account.id}")
                return broker
    except Exception as e:
        logger.error(f"DhanDataService._get_any_dhan_broker error: {e}")
    return None


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def get_index_quotes(user_id: Optional[int] = None) -> Dict[str, Dict]:
    """
    Return OHLC + LTP + change for major indices using Dhan.
    Result format:
      {
        "NIFTY":     {"ltp": 23500, "open": ..., "high": ..., "low": ..., "change": ..., "pct_change": ...},
        "BANKNIFTY": {...},
        ...
      }
    Returns {} if Dhan is not available.
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        return {}
    try:
        symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "INDIA VIX"]
        data = broker.get_index_ohlc(symbols)
        logger.info(f"DhanDataService.get_index_quotes: received {list(data.keys())}")
        return data
    except Exception as e:
        logger.error(f"DhanDataService.get_index_quotes error: {e}")
        return {}


def get_nifty_spot(user_id: Optional[int] = None) -> float:
    """Return the NIFTY 50 LTP from Dhan, or 0.0 if unavailable."""
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        return 0.0
    try:
        return broker.get_price("NIFTY")
    except Exception as e:
        logger.error(f"DhanDataService.get_nifty_spot error: {e}")
        return 0.0


def get_option_chain(symbol: str = "NIFTY", expiry: Optional[str] = None,
                     user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Fetch option chain from Dhan for the given symbol and expiry.
    Returns a dict compatible with nifty_options_engine:
      {
        "spot_price": float,
        "expiry": str,
        "expiry_list": [str, ...],
        "option_chain": {
            "23500CE": {"ltp": ..., "oi": ..., ...},
            "23500PE": {...},
            ...
        }
      }
    Returns {} on failure.
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        return {}
    try:
        expiry_list = broker.get_expiry_list(symbol)
        if not expiry_list:
            logger.warning(f"DhanDataService: no expiry list for {symbol}")
            return {}

        target_expiry = expiry if expiry and expiry in expiry_list else expiry_list[0]
        chain_raw = broker.get_option_chain(symbol, target_expiry)
        if not chain_raw:
            return {}

        spot = chain_raw[0].get("spot", 0) if chain_raw else 0.0

        from services.option_chain_builder import chain_to_engine_format
        engine_chain = chain_to_engine_format(chain_raw, spot)

        return {
            "spot_price": spot,
            "expiry": target_expiry,
            "expiry_list": expiry_list,
            "option_chain": engine_chain,
            "source": "Dhan",
        }
    except Exception as e:
        logger.error(f"DhanDataService.get_option_chain error: {e}")
        return {}


def get_nifty50_stock_quotes(security_id_map: Dict[str, int],
                              user_id: Optional[int] = None) -> Dict[str, Dict]:
    """
    Fetch OHLC for Nifty 50 stocks by their Dhan NSE_EQ security IDs.

    Args:
        security_id_map: {symbol: dhan_security_id}, e.g. {"RELIANCE": 2885}
    Returns:
        {symbol: {ltp, change, pct_change, ...}}
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        return {}
    try:
        rev_map = {v: k for k, v in security_id_map.items()}
        ids = list(security_id_map.values())
        raw = broker.get_eq_ohlc(ids)
        result = {}
        for sid_str, data in raw.items():
            sym = rev_map.get(int(sid_str), sid_str)
            result[sym] = data
        return result
    except Exception as e:
        logger.error(f"DhanDataService.get_nifty50_stock_quotes error: {e}")
        return {}
