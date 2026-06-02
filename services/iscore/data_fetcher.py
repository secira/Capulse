"""
Historical data fetcher for I-Score calculations.

Priority chain (via Market Data Gateway):
  1. Admin Broker Pool — admin-configured broker (Dhan/Zerodha/etc.)
  2. System Dhan       — any connected DataApiBroker on the platform
  3. User's broker     — user's own connected data broker
  4. yfinance          — free universal fallback
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)


def fetch_historical_ohlcv(symbol: str, days: int = 120) -> pd.DataFrame:
    """
    Return a DataFrame with columns [open, high, low, close, volume] for
    the requested NSE stock symbol covering the last `days` trading days.

    Delegates to the Market Data Gateway for a uniform fallback chain:
    Admin Broker Pool → System Dhan → yfinance.
    """
    df, _ = fetch_historical_ohlcv_with_source(symbol, days)
    return df


def fetch_historical_ohlcv_with_source(symbol: str, days: int = 120) -> tuple:
    """
    Same as fetch_historical_ohlcv but also returns the source name.

    Returns:
        (DataFrame, source_name) where source_name reflects the actual data
        origin: 'admin_broker', 'dhan_system', 'yfinance', or '' on failure.
    """
    # ── Delegate to the uniform Market Data Gateway ───────────────────
    try:
        from services.market_data_gateway import get_ohlcv
        result = get_ohlcv(symbol, days=days)
        if result.get('success') and result['df'] is not None and not result['df'].empty:
            logger.info(
                f"fetch_historical_ohlcv({symbol}): {len(result['df'])} rows "
                f"from {result['source']}"
            )
            return result['df'], result['source']
    except Exception as e:
        logger.warning(f"fetch_historical_ohlcv({symbol}): gateway error — {e}")

    # ── Direct yfinance fallback (gateway already tries this, but keep as ──
    # ── safety net in case the import fails entirely) ─────────────────────
    try:
        import yfinance as yf
        period = '6mo' if days <= 120 else '1y'
        for ticker_sym in (f"{symbol}.NS", f"{symbol}.BO"):
            ticker = yf.Ticker(ticker_sym)
            df = ticker.history(period=period)
            if df is None or df.empty:
                continue
            df = df.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume',
            })
            df = df[['open', 'high', 'low', 'close', 'volume']].copy()
            df = df.dropna(subset=['close']).tail(days).reset_index(drop=True)
            if not df.empty:
                logger.info(f"fetch_historical_ohlcv({symbol}): {len(df)} rows from yfinance (direct)")
                return df, 'yfinance'
    except Exception as e:
        logger.error(f"fetch_historical_ohlcv({symbol}): yfinance direct error — {e}")

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
