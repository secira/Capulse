"""
DhanDataService — thin service layer that wraps the DhanBroker adapter
and provides market data for the Live Market Pulse, dashboard index cards,
F&O engine, and I-Score engine when a user has Dhan configured as their
Data API broker.
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import pandas as pd

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Instrument master — lazy-loaded security ID map for NSE equities
# Dhan requires numeric security IDs; we fetch their master CSV once
# and keep it in memory.
# ------------------------------------------------------------------
_SECID_LOCK    = threading.Lock()
_SECID_MAP: Dict[str, int] = {}   # symbol (upper) → security_id
_SECID_LOADED  = False


def _load_security_id_map() -> Dict[str, int]:
    """
    Download the Dhan instrument master CSV (NSE_EQ equities only) and
    build a dict {trading_symbol → security_id}.  Cached for the process
    lifetime — call once per startup.
    """
    global _SECID_MAP, _SECID_LOADED
    with _SECID_LOCK:
        if _SECID_LOADED:
            return _SECID_MAP
        try:
            import requests
            url = "https://images.dhan.co/api-data/api-scrip-master.csv"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            import io
            df = pd.read_csv(io.StringIO(resp.text), low_memory=False)
            # Keep only NSE equity segment rows
            eq_mask = (
                df["SEM_EXM_EXCH_ID"].astype(str).str.strip().str.upper().eq("NSE") &
                df["SEM_INSTRUMENT_NAME"].astype(str).str.strip().str.upper().isin(["EQUITY", "EQLF"])
            )
            eq = df[eq_mask].copy()
            mapping = {}
            for _, row in eq.iterrows():
                try:
                    raw_sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                    # Dhan symbols can be "RELIANCE-EQ" — strip the suffix
                    sym = raw_sym.replace("-EQ", "").replace("-BE", "").replace("-SM", "")
                    sec_id = int(row["SEM_SMST_SECURITY_ID"])
                    if sym:
                        mapping[sym] = sec_id
                except Exception:
                    continue
            _SECID_MAP = mapping
            _SECID_LOADED = True
            logger.info(f"Dhan instrument master loaded: {len(mapping)} NSE equity symbols")
        except Exception as e:
            logger.warning(f"Dhan instrument master load failed: {e} — will fall back to yfinance")
            _SECID_LOADED = True  # mark as attempted so we don't retry on every call
        return _SECID_MAP


def _get_security_id(symbol: str) -> Optional[int]:
    """Return Dhan NSE_EQ security ID for a stock symbol, or None if unknown."""
    m = _load_security_id_map()
    sym = symbol.upper().replace("-EQ", "")
    return m.get(sym)


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


# ------------------------------------------------------------------
# Historical / Intraday candle helpers
# ------------------------------------------------------------------

def get_nifty_intraday_candles(interval: int = 5,
                                user_id: Optional[int] = None) -> "pd.DataFrame":
    """
    Fetch today's intraday NIFTY 50 candles via Dhan and return a DataFrame
    with columns matching yfinance output: Open, High, Low, Close, Volume.
    Returns empty DataFrame on failure.
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        logger.debug("get_nifty_intraday_candles: no Dhan broker available")
        return pd.DataFrame()
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        # Dhan intraday API covers last 5 trading days; use today → today
        rows = broker.get_intraday_candles(
            security_id=13,
            exchange_segment="IDX_I",
            instrument_type="INDEX",
            from_date=today_str,
            to_date=today_str,
            interval=interval,
        )
        if not rows:
            logger.warning("get_nifty_intraday_candles: Dhan returned no rows")
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df = df.dropna(subset=["Close"])
        logger.info(f"get_nifty_intraday_candles: {len(df)} candles from Dhan")
        return df
    except Exception as e:
        logger.error(f"get_nifty_intraday_candles error: {e}")
        return pd.DataFrame()


def get_stock_historical_ohlcv(symbol: str, days: int = 120,
                                user_id: Optional[int] = None) -> "pd.DataFrame":
    """
    Fetch daily OHLCV history for an NSE equity symbol via Dhan and return
    a DataFrame with lowercase columns: open, high, low, close, volume.
    Returns empty DataFrame if Dhan is not available or symbol is unknown.
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        logger.debug(f"get_stock_historical_ohlcv({symbol}): no Dhan broker")
        return pd.DataFrame()
    sec_id = _get_security_id(symbol)
    if sec_id is None:
        logger.debug(f"get_stock_historical_ohlcv({symbol}): security ID not found")
        return pd.DataFrame()
    try:
        to_date   = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
        rows = broker.get_historical_daily_data(
            security_id=sec_id,
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_date,
            to_date=to_date,
        )
        if not rows:
            logger.warning(f"get_stock_historical_ohlcv({symbol}): Dhan returned no rows")
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df.dropna(subset=["close"])
        df = df.tail(days).reset_index(drop=True)
        logger.info(f"get_stock_historical_ohlcv({symbol}): {len(df)} rows from Dhan")
        return df
    except Exception as e:
        logger.error(f"get_stock_historical_ohlcv({symbol}) error: {e}")
        return pd.DataFrame()


def get_index_historical_ohlcv(days: int = 60,
                                user_id: Optional[int] = None) -> "pd.DataFrame":
    """
    Fetch daily OHLCV history for NIFTY 50 index via Dhan.
    Returns DataFrame with lowercase columns: open, high, low, close, volume.
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        return pd.DataFrame()
    try:
        to_date   = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
        rows = broker.get_historical_daily_data(
            security_id=13,
            exchange_segment="IDX_I",
            instrument_type="INDEX",
            from_date=from_date,
            to_date=to_date,
        )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df.dropna(subset=["close"])
        df = df.tail(days).reset_index(drop=True)
        logger.info(f"get_index_historical_ohlcv: {len(df)} rows from Dhan")
        return df
    except Exception as e:
        logger.error(f"get_index_historical_ohlcv error: {e}")
        return pd.DataFrame()
