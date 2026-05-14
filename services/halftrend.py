"""
HalfTrend engine — adaptive ATR-based trend & exit layer.

Per the MVLA + HalfTrend strategy spec, this module is responsible only for
trade lifecycle management:
  • adaptive trend persistence (ATR-driven)
  • structure breakouts (rolling high/low)
  • volatility expansion detection
  • a trend-stability score (0–100)
  • exit signals (buy_exit / sell_exit) consumed by the engine

It deliberately does NOT replicate EMA / RSI / VWAP / Volume / DMI / OI
logic — those live in the MVLA scoring layer (`nifty_options_engine.py`).

Trend convention used here:
  trend == 0  →  BULLISH (CALL bias)
  trend == 1  →  BEARISH (PUT bias)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pine-style RMA (Wilder's moving average)
# ---------------------------------------------------------------------------
def _pine_rma(series: pd.Series, length: int) -> pd.Series:
    if length <= 0 or len(series) == 0:
        return series.copy()
    alpha = 1.0 / length
    rma = np.zeros(len(series), dtype=float)
    rma[0] = float(series.iloc[0])
    for i in range(1, len(series)):
        rma[i] = alpha * float(series.iloc[i]) + (1.0 - alpha) * rma[i - 1]
    return pd.Series(rma, index=series.index)


def _calculate_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high  = df['high']
    low   = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return _pine_rma(tr, length)


# ---------------------------------------------------------------------------
# HalfTrend engine
# ---------------------------------------------------------------------------
def halftrend_engine(
    df: pd.DataFrame,
    amplitude: int = 4,
    atr_length: int = 14,
    channel_deviation: float = 1.5,  # accepted for API parity (spec); not used yet
) -> pd.DataFrame:
    """Run the HalfTrend engine over a DataFrame of OHLC(V) candles.

    Input must contain columns: high, low, close (lowercase). The caller is
    responsible for normalising case before calling.

    Returns the input DataFrame with appended columns:
        trend                — 0 bullish, 1 bearish
        halftrend            — adaptive trend line (price level)
        buy_exit             — True bar where a long (CALL) should exit
        sell_exit            — True bar where a short (PUT) should exit
        stability_score      — 0–100 trend-stability score
        volatility_expansion — bool, current range > 1.2× avg(5)
    """
    data = df.copy().reset_index(drop=True)
    n = len(data)

    if n == 0:
        for col in ('trend', 'halftrend', 'buy_exit', 'sell_exit',
                    'stability_score', 'volatility_expansion'):
            data[col] = pd.Series(dtype=float)
        return data

    high  = data['high'].astype(float)
    low   = data['low'].astype(float)
    close = data['close'].astype(float)

    atr      = _calculate_atr(data, atr_length)
    atr_half = atr / 2.0

    trend           = np.zeros(n, dtype=int)
    halftrend       = np.full(n, np.nan, dtype=float)
    buy_exit        = np.full(n, False)
    sell_exit       = np.full(n, False)
    stability_score = np.full(n, 0.0, dtype=float)

    rolling_high = high.rolling(amplitude).max()
    rolling_low  = low.rolling(amplitude).min()

    avg_range_5  = (high - low).rolling(5).mean()
    cur_range    = high - low
    vol_expand   = cur_range > (avg_range_5 * 1.2)

    current_trend = 0

    for i in range(amplitude, n):
        bullish_structure = close.iloc[i] > rolling_high.iloc[i - 1]
        bearish_structure = close.iloc[i] < rolling_low.iloc[i - 1]

        if bullish_structure:
            current_trend  = 0
            halftrend[i]   = float(rolling_low.iloc[i] - atr_half.iloc[i])
        elif bearish_structure:
            current_trend  = 1
            halftrend[i]   = float(rolling_high.iloc[i] + atr_half.iloc[i])
        else:
            current_trend  = trend[i - 1]
            halftrend[i]   = halftrend[i - 1]

        trend[i] = current_trend

        # ── Exit conditions ──────────────────────────────────────────
        if trend[i] == 0 and not np.isnan(halftrend[i]):
            if close.iloc[i] < halftrend[i]:
                buy_exit[i] = True
        if trend[i] == 1 and not np.isnan(halftrend[i]):
            if close.iloc[i] > halftrend[i]:
                sell_exit[i] = True

        # ── Stability score ──────────────────────────────────────────
        score = 0
        # Trend persistence — same trend for 3 bars in a row
        if i >= 2 and trend[i] == trend[i - 1] == trend[i - 2]:
            score += 10
        # Volatility expansion bar
        if bool(vol_expand.iloc[i]):
            score += 15
        # Distance of price from HalfTrend line in ATR units
        if not np.isnan(halftrend[i]) and atr.iloc[i] > 0:
            distance = abs(close.iloc[i] - halftrend[i])
            if distance > atr.iloc[i] * 0.5:
                score += 10
        # Strong candle vs 5-bar avg range
        if (avg_range_5.iloc[i] is not None
                and not pd.isna(avg_range_5.iloc[i])
                and cur_range.iloc[i] > avg_range_5.iloc[i]):
            score += 10

        stability_score[i] = float(min(score, 100))

    data['trend']                = trend
    data['halftrend']            = halftrend
    data['buy_exit']             = buy_exit
    data['sell_exit']            = sell_exit
    data['stability_score']      = stability_score
    data['volatility_expansion'] = vol_expand
    return data


# ---------------------------------------------------------------------------
# Engine-facing helper — compute current HalfTrend state from our 5-min DF
# ---------------------------------------------------------------------------
def compute_state(df_engine: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Run HalfTrend on the engine's intraday candle DF and return a compact
    dict the F&O engine can consume.

    The engine uses Title-Case columns (Open/High/Low/Close/Volume); we
    rename to lowercase before running halftrend_engine, then extract the
    last bar's signals.

    Returns a safe default block if the DF is too short or missing.
    """
    default = {
        'available':           False,
        'trend':               'NEUTRAL',     # BULLISH / BEARISH / NEUTRAL
        'halftrend_level':     0.0,
        'stability_score':     0,
        'volatility_expansion': False,
        'buy_exit':            False,         # exit CALL
        'sell_exit':           False,         # exit PUT
        'trend_persistence':   0,             # bars current trend has held
        'reason':              'HalfTrend unavailable — insufficient candles',
    }
    if df_engine is None or len(df_engine) < 20:
        return default

    try:
        df = df_engine.rename(columns={
            'High':  'high', 'Low': 'low',
            'Open':  'open', 'Close': 'close',
            'Volume': 'volume',
        })
        # Tolerate already-lowercase
        for col in ('high', 'low', 'close'):
            if col not in df.columns:
                logger.debug(f"halftrend.compute_state: missing column '{col}'")
                return default

        ht = halftrend_engine(df[['open', 'high', 'low', 'close']]
                              if 'open' in df.columns else df[['high', 'low', 'close']]
                              .assign(open=df['close']),
                              amplitude=4, atr_length=14)

        last_idx = len(ht) - 1
        last_trend = int(ht['trend'].iloc[last_idx])
        trend_lbl  = 'BULLISH' if last_trend == 0 else 'BEARISH'

        # How many bars the current trend has persisted
        persistence = 1
        for j in range(last_idx - 1, -1, -1):
            if int(ht['trend'].iloc[j]) == last_trend:
                persistence += 1
            else:
                break

        return {
            'available':            True,
            'trend':                trend_lbl,
            'halftrend_level':      round(float(ht['halftrend'].iloc[last_idx] or 0.0), 2),
            'stability_score':      int(ht['stability_score'].iloc[last_idx]),
            'volatility_expansion': bool(ht['volatility_expansion'].iloc[last_idx]),
            'buy_exit':             bool(ht['buy_exit'].iloc[last_idx]),
            'sell_exit':            bool(ht['sell_exit'].iloc[last_idx]),
            'trend_persistence':    int(persistence),
            'reason':               (
                f"{trend_lbl} for {persistence} bar(s), "
                f"stability {int(ht['stability_score'].iloc[last_idx])}/100"
            ),
        }
    except Exception as e:
        logger.warning(f"HalfTrend compute_state failed: {e}")
        return default
