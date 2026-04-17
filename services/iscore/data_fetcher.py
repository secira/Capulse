"""
Historical data fetcher for I-Score calculations.

Priority chain:
  1. Dhan API (user's connected Data API broker) — paid, reliable
  2. yfinance (Yahoo Finance) — free fallback, less reliable in cloud
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)


def fetch_historical_ohlcv(symbol: str, days: int = 120) -> pd.DataFrame:
    """
    Return a DataFrame with columns [open, high, low, close, volume] for
    the requested NSE stock symbol covering the last `days` trading days.

    Source priority:
      1. Dhan API  (primary — reliable, paid)
      2. yfinance  (fallback — free, may be rate-limited in cloud)
    """
    df, _ = fetch_historical_ohlcv_with_source(symbol, days)
    return df


def fetch_historical_ohlcv_with_source(symbol: str, days: int = 120) -> tuple:
    """
    Same as fetch_historical_ohlcv but also returns the source name.

    Returns:
        (DataFrame, source_name) where source_name is one of:
          'Dhan', 'yfinance', or '' (empty on failure)
    """
    df = pd.DataFrame()

    # ── Priority 1: Dhan ──────────────────────────────────────────────
    try:
        from services.dhan_service import get_stock_historical_ohlcv
        df_dhan = get_stock_historical_ohlcv(symbol=symbol, days=days)
        if df_dhan is not None and not df_dhan.empty and len(df_dhan) >= 10:
            logger.info(f"fetch_historical_ohlcv({symbol}): {len(df_dhan)} rows from Dhan")
            return df_dhan, 'Dhan'
    except Exception as e:
        logger.warning(f"fetch_historical_ohlcv({symbol}): Dhan failed — {e}")

    # ── Priority 2: yfinance fallback ─────────────────────────────────
    try:
        import yfinance as yf
        period = '6mo' if days <= 120 else '1y'

        nse_symbol = f"{symbol}.NS"
        ticker = yf.Ticker(nse_symbol)
        df = ticker.history(period=period)

        if df is None or df.empty:
            bse_symbol = f"{symbol}.BO"
            ticker = yf.Ticker(bse_symbol)
            df = ticker.history(period=period)

        if df is None or df.empty:
            logger.warning(f"fetch_historical_ohlcv({symbol}): no data from yfinance either")
            return pd.DataFrame(), ''

        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume',
        })
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df = df.dropna(subset=['close'])
        df = df.tail(days).reset_index(drop=True)
        logger.info(f"fetch_historical_ohlcv({symbol}): {len(df)} rows from yfinance (fallback)")
        return df, 'yfinance'

    except Exception as e:
        logger.error(f"fetch_historical_ohlcv({symbol}): yfinance error — {e}")
        return pd.DataFrame(), ''


def fetch_market_index_history(symbol: str = '^NSEI', days: int = 60) -> pd.DataFrame:
    """
    Return a DataFrame with columns [open, high, low, close, volume] for
    a market index.  Used by the I-Score Market Context component.

    For NIFTY (symbol='^NSEI' or 'NIFTY'), Dhan is tried first.
    All others fall through to yfinance directly.
    """
    df = pd.DataFrame()

    # ── Priority 1: Dhan (NIFTY index only) ──────────────────────────
    nifty_aliases = {'^NSEI', 'NIFTY', '^NIFTY', 'NIFTY50', 'NIFTY 50'}
    if symbol.upper() in nifty_aliases:
        try:
            from services.dhan_service import get_index_historical_ohlcv
            df_dhan = get_index_historical_ohlcv(days=days)
            if df_dhan is not None and not df_dhan.empty and len(df_dhan) >= 5:
                logger.info(f"fetch_market_index_history(NIFTY): {len(df_dhan)} rows from Dhan")
                return df_dhan
        except Exception as e:
            logger.warning(f"fetch_market_index_history: Dhan NIFTY failed — {e}")

    # ── Priority 2: yfinance fallback ─────────────────────────────────
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(period='3mo')
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume',
        })
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df = df.dropna(subset=['close'])
        df = df.tail(days).reset_index(drop=True)
        logger.info(f"fetch_market_index_history({symbol}): {len(df)} rows from yfinance")
        return df
    except Exception as e:
        logger.error(f"fetch_market_index_history({symbol}): yfinance error — {e}")
        return pd.DataFrame()
