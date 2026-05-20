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

# ------------------------------------------------------------------
# OHLC quote cache — short-TTL in-memory cache for get_eq_quote()
# Avoids redundant Dhan API calls when the same symbol is requested
# multiple times within a short window (e.g. across pages in a session).
# ------------------------------------------------------------------
_QUOTE_CACHE_TTL = 60  # seconds
_QUOTE_CACHE_LOCK = threading.Lock()
_QUOTE_CACHE: Dict[str, tuple] = {}  # symbol → (timestamp: float, data: dict)


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


def get_security_id(symbol: str) -> Optional[int]:
    """Public alias for _get_security_id — returns Dhan NSE_EQ security ID or None."""
    return _get_security_id(symbol)


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


def get_eq_quote(symbol: str, user_id: Optional[int] = None) -> Optional[Dict]:
    """
    Fetch current OHLC + LTP for a single NSE equity symbol via Dhan.

    Returns a dict with keys: ltp, open, high, low, close, change, pct_change
    (all floats), plus 'source': 'Dhan'.  Returns None when Dhan is unavailable
    or the symbol is not in the instrument master.

    Results are cached in memory for _QUOTE_CACHE_TTL seconds to avoid redundant
    API calls when the same symbol is requested multiple times in quick succession.
    """
    import time
    # Normalise cache key the same way _get_security_id() does so that
    # "RELIANCE" and "RELIANCE-EQ" resolve to the same cache entry.
    sym_key = symbol.upper().replace("-EQ", "").replace("-BE", "").replace("-SM", "")

    # Check cache first (thread-safe)
    with _QUOTE_CACHE_LOCK:
        cached = _QUOTE_CACHE.get(sym_key)
        if cached is not None:
            cached_ts, cached_data = cached
            age = time.time() - cached_ts
            if age < _QUOTE_CACHE_TTL:
                logger.debug(f"get_eq_quote({symbol}): cache hit (age {age:.1f}s)")
                return dict(cached_data)  # shallow copy to prevent caller mutation

    sec_id = _get_security_id(symbol)
    if sec_id is None:
        logger.debug(f"get_eq_quote({symbol}): no security ID in instrument master")
        return None

    # Wrap broker resolve + OHLC fetch in a hard timeout — both
    # broker.connect() (calls Dhan get_fund_limits) and broker.get_eq_ohlc()
    # are blocking HTTP calls with no per-request timeout in the SDK.
    # Without this guard, a slow/unreachable Dhan endpoint hangs the whole
    # Flask request forever (observed: "Fetching live price…" never resolved
    # on /dashboard/trade-now).
    import concurrent.futures

    def _do_dhan_fetch():
        broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
        if broker is None:
            return ("no_broker", None)
        raw = broker.get_eq_ohlc([sec_id])
        return ("ok", raw)

    try:
        logger.debug(f"get_eq_quote({symbol}): cache miss — fetching from Dhan")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_dhan_fetch)
            try:
                status, raw = future.result(timeout=4.0)
            except concurrent.futures.TimeoutError:
                logger.warning(f"get_eq_quote({symbol}): Dhan call timed out after 4s — falling through")
                return None

        if status == "no_broker" or raw is None:
            logger.debug(f"get_eq_quote({symbol}): no Dhan broker available")
            return None
        row = raw.get(str(sec_id))
        if row and row.get("ltp", 0) > 0:
            row["source"] = "Dhan"
            with _QUOTE_CACHE_LOCK:
                _QUOTE_CACHE[sym_key] = (time.time(), dict(row))  # store a copy
            return row
    except Exception as e:
        logger.error(f"get_eq_quote({symbol}) error: {e}")
    return None


def get_nifty50_stock_quotes(security_id_map: Dict[str, int],
                              user_id: Optional[int] = None,
                              timeout: float = 5.0) -> Dict[str, Dict]:
    """
    Fetch OHLC for Nifty 50 stocks by their Dhan NSE_EQ security IDs.

    Args:
        security_id_map: {symbol: dhan_security_id}, e.g. {"RELIANCE": 2885}
        timeout: max seconds to wait for the Dhan API (default 5s)
    Returns:
        {symbol: {ltp, change, pct_change, ...}}
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        return {}
    try:
        import concurrent.futures
        rev_map = {v: k for k, v in security_id_map.items()}
        ids = list(security_id_map.values())

        def _fetch():
            return broker.get_eq_ohlc(ids)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_fetch)
            try:
                raw = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning(f"DhanDataService.get_nifty50_stock_quotes timed out after {timeout}s")
                return {}

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
    """Fetch today's intraday NIFTY 50 candles via Dhan (backward-compat wrapper)."""
    return get_index_intraday_candles(
        security_id=13, exchange_segment='IDX_I',
        interval=interval, user_id=user_id, index_label='NIFTY',
    )


def get_index_intraday_candles(security_id: int,
                                exchange_segment: str = 'IDX_I',
                                interval: int = 5,
                                user_id: Optional[int] = None,
                                index_label: str = 'INDEX') -> "pd.DataFrame":
    """
    Generic intraday candles fetcher for any Dhan-supported index.

    Parameters
    ----------
    security_id      : Dhan internal security ID (13 = NIFTY, 25 = BANKNIFTY, …)
    exchange_segment : e.g. 'IDX_I'
    interval         : candle interval in minutes (default 5)
    user_id          : optional user for broker selection
    index_label      : used only for logging

    Returns a DataFrame with columns: Open, High, Low, Close, Volume.
    Returns empty DataFrame on failure.
    """
    broker = _get_dhan_broker(user_id) or _get_any_dhan_broker()
    if broker is None:
        logger.debug(f"get_index_intraday_candles({index_label}): no Dhan broker available")
        return pd.DataFrame()
    try:
        today     = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        # Fetch the last 5 calendar days of intraday candles so Wilder ADX-14,
        # EMA-21 and ATR-14 have enough warm-up history to converge. With only
        # today's candles, the morning session (≤21 bars) gave indicator
        # values that didn't match TradingView/Kite. 5 days ≈ 3 trading
        # sessions ≈ 225 candles — comfortable buffer for all indicators.
        from_date_str = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        rows = broker.get_intraday_candles(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type="INDEX",
            from_date=from_date_str,
            to_date=today_str,
            interval=interval,
        )
        if not rows:
            logger.warning(f"get_index_intraday_candles({index_label}): Dhan returned no rows")
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # Parse timestamp into a tz-aware datetime index so VWAP can filter
        # to today's session and indicators have proper time context.
        # Dhan returns timestamps as Unix seconds (integers). pd.to_datetime
        # without unit='s' treats them as nanoseconds → wrong dates. Detect
        # numeric dtype and use unit='s' accordingly.
        if "timestamp" in df.columns:
            try:
                ts_col = df["timestamp"]
                if pd.api.types.is_numeric_dtype(ts_col):
                    df["timestamp"] = pd.to_datetime(ts_col, unit='s', utc=True)
                else:
                    df["timestamp"] = pd.to_datetime(ts_col, utc=True)
                df = df.set_index("timestamp")
                df = df.sort_index()
            except Exception as ts_err:
                logger.warning(f"get_index_intraday_candles({index_label}): timestamp parse failed: {ts_err}")
                df = df.drop(columns=["timestamp"], errors="ignore")
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[keep].copy()
        df = df.dropna(subset=["Close"])
        logger.info(f"get_index_intraday_candles({index_label}): {len(df)} candles from Dhan (7d)")
        return df
    except Exception as e:
        logger.error(f"get_index_intraday_candles({index_label}) error: {e}")
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
