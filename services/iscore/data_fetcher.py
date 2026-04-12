"""
Historical data fetcher for I-Score calculations.
Uses yfinance for reliable multi-day OHLCV data.
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)


def fetch_historical_ohlcv(symbol: str, days: int = 120) -> pd.DataFrame:
    try:
        import yfinance as yf
        nse_symbol = f"{symbol}.NS"
        ticker = yf.Ticker(nse_symbol)
        period = '6mo' if days <= 120 else '1y'
        df = ticker.history(period=period)

        if df is None or df.empty:
            bse_symbol = f"{symbol}.BO"
            ticker = yf.Ticker(bse_symbol)
            df = ticker.history(period=period)

        if df is None or df.empty:
            logger.warning(f"No historical data from yfinance for {symbol}")
            return pd.DataFrame()

        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume',
        })
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df = df.dropna(subset=['close'])
        df = df.tail(days)
        df = df.reset_index(drop=True)
        logger.info(f"Fetched {len(df)} days of OHLCV for {symbol}")
        return df

    except Exception as e:
        logger.error(f"Historical data fetch error for {symbol}: {e}")
        return pd.DataFrame()


def fetch_market_index_history(symbol: str = '^NSEI', days: int = 60) -> pd.DataFrame:
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
        df = df.tail(days)
        df = df.reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Market index history error: {e}")
        return pd.DataFrame()
