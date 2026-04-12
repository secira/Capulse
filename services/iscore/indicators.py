"""
Real technical indicator calculations using proper financial mathematics.
All functions expect a pandas DataFrame with columns: open, high, low, close, volume
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_ema(df: pd.DataFrame, span: int) -> pd.Series:
    return df['close'].ewm(span=span, adjust=False).mean()


def compute_sma(df: pd.DataFrame, span: int) -> pd.Series:
    return df['close'].rolling(window=span).mean()


def compute_momentum(df: pd.DataFrame, period: int = 5) -> pd.Series:
    return (df['close'] / df['close'].shift(period) - 1.0) * 100.0


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    atr = compute_atr(df, period)
    hl2 = (df['high'] + df['low']) / 2.0
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = pd.Series(np.nan, index=df.index)
    direction = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        if pd.isna(atr.iloc[i]):
            continue

        if df['close'].iloc[i] > upper_band.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df['close'].iloc[i] < lower_band.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

        if direction.iloc[i] == 1:
            lower_band.iloc[i] = max(lower_band.iloc[i], lower_band.iloc[i - 1]) if direction.iloc[i - 1] == 1 else lower_band.iloc[i]
            supertrend.iloc[i] = lower_band.iloc[i]
        else:
            upper_band.iloc[i] = min(upper_band.iloc[i], upper_band.iloc[i - 1]) if direction.iloc[i - 1] == -1 else upper_band.iloc[i]
            supertrend.iloc[i] = upper_band.iloc[i]

    result = pd.DataFrame({'supertrend': supertrend, 'direction': direction}, index=df.index)
    return result


def compute_max_drawdown(df: pd.DataFrame, period: int = 50) -> float:
    prices = df['close'].tail(period)
    if len(prices) < 5:
        return 0.0
    cummax = prices.cummax()
    drawdowns = (prices - cummax) / cummax * 100.0
    return float(drawdowns.min())


def compute_beta(stock_returns: pd.Series, market_returns: pd.Series) -> float:
    if len(stock_returns) < 10 or len(market_returns) < 10:
        return 1.0
    aligned = pd.DataFrame({'stock': stock_returns, 'market': market_returns}).dropna()
    if len(aligned) < 10:
        return 1.0
    cov = aligned['stock'].cov(aligned['market'])
    var = aligned['market'].var()
    if var == 0:
        return 1.0
    return float(cov / var)


def compute_volume_profile(df: pd.DataFrame, lookback: int = 20) -> dict:
    if len(df) < lookback + 1:
        lookback = max(len(df) - 1, 1)
    recent_vol = df['volume'].tail(lookback)
    avg_vol = recent_vol.mean()
    latest_vol = df['volume'].iloc[-1]
    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

    return {
        'current_volume': int(latest_vol),
        'avg_volume_20d': int(avg_vol),
        'volume_ratio': round(float(vol_ratio), 2),
        'is_spike': bool(vol_ratio > 1.5),
        'signal': 'high_interest' if vol_ratio > 1.5 else ('low_interest' if vol_ratio < 0.5 else 'normal'),
    }


def compute_all_indicators(df: pd.DataFrame) -> dict:
    if df is None or df.empty or len(df) < 15:
        return None

    rsi = compute_rsi(df, 14)
    ema9 = compute_ema(df, 9)
    ema20 = compute_ema(df, 20)
    ema50 = compute_ema(df, 50) if len(df) >= 50 else pd.Series(dtype=float)
    ema200 = compute_ema(df, 200) if len(df) >= 200 else pd.Series(dtype=float)
    momentum_5 = compute_momentum(df, 5)
    momentum_20 = compute_momentum(df, 20) if len(df) >= 20 else pd.Series(dtype=float)
    atr = compute_atr(df, 14)
    st = compute_supertrend(df, 10, 3.0)
    max_dd = compute_max_drawdown(df, min(50, len(df)))
    vol_profile = compute_volume_profile(df)

    latest = df.iloc[-1]
    latest_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
    latest_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
    atr_pct = (latest_atr / latest['close'] * 100) if latest['close'] > 0 else 0.0

    short_trend = 'bullish' if (not ema9.empty and not ema20.empty and ema9.iloc[-1] > ema20.iloc[-1]) else 'bearish'
    medium_trend = 'neutral'
    if not ema20.empty and not ema50.empty and len(ema50) > 0 and not pd.isna(ema50.iloc[-1]):
        medium_trend = 'bullish' if ema20.iloc[-1] > ema50.iloc[-1] else 'bearish'
    long_trend = 'neutral'
    if not ema50.empty and not ema200.empty and len(ema200) > 0 and not pd.isna(ema200.iloc[-1]):
        long_trend = 'bullish' if ema50.iloc[-1] > ema200.iloc[-1] else 'bearish'

    st_direction = 'buy' if (not st.empty and int(st['direction'].iloc[-1]) == 1) else 'sell'

    def _safe_float(val, default=0.0):
        if val is None:
            return default
        try:
            f = float(val)
            if np.isnan(f) or np.isinf(f):
                return default
            return round(f, 2)
        except (TypeError, ValueError):
            return default

    def _safe_ema(series):
        if series is None or series.empty or len(series) == 0:
            return None
        v = series.iloc[-1]
        if pd.isna(v):
            return None
        return round(float(v), 2)

    return {
        'rsi': _safe_float(latest_rsi, 50.0),
        'ema9': _safe_ema(ema9),
        'ema20': _safe_ema(ema20),
        'ema50': _safe_ema(ema50),
        'ema200': _safe_ema(ema200),
        'momentum_5d': _safe_float(momentum_5.iloc[-1] if not momentum_5.empty else None),
        'momentum_20d': _safe_float(momentum_20.iloc[-1] if not momentum_20.empty else None),
        'atr': _safe_float(latest_atr),
        'atr_pct': _safe_float(atr_pct),
        'supertrend_direction': st_direction,
        'max_drawdown': _safe_float(max_dd),
        'short_trend': short_trend,
        'medium_trend': medium_trend,
        'long_trend': long_trend,
        'volume': vol_profile,
        'price': {
            'current': float(latest['close']),
            'open': float(latest['open']),
            'high': float(latest['high']),
            'low': float(latest['low']),
            'prev_close': float(df['close'].iloc[-2]) if len(df) >= 2 else float(latest['close']),
            'change_pct': round(float((latest['close'] - df['close'].iloc[-2]) / df['close'].iloc[-2] * 100) if len(df) >= 2 else 0.0, 2),
        },
    }
