"""
Real technical indicator calculations using proper financial mathematics.
All functions expect a pandas DataFrame with columns: open, high, low, close, volume

Phase 1 additions:
  - compute_relative_strength_vs_nifty(): 20d/60d/120d RS
  - compute_atr_efficiency(): trend_move / ATR
  - compute_higher_high_structure(): HH/HL pattern detection
  - compute_trend_duration(): bars in current direction
  - compute_52week_high_distance(): % from 52-week high
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


# ── Phase 1 additions ────────────────────────────────────────────────────────

def compute_relative_strength_vs_nifty(
    stock_df: pd.DataFrame,
    nifty_df: pd.DataFrame,
    periods: tuple = (20, 60, 120),
) -> dict:
    """
    Relative strength of a stock vs Nifty over 20d, 60d, 120d.
    RS = (stock return) − (nifty return) over the period.
    Returns rs_20d, rs_60d, rs_120d and composite rs_score (0-100).
    Stocks outperforming Nifty on multiple horizons get higher scores.
    """
    result = {'rs_20d': 0.0, 'rs_60d': 0.0, 'rs_120d': 0.0, 'rs_score': 50.0}
    try:
        if stock_df is None or nifty_df is None:
            return result

        def _period_return(df: pd.DataFrame, n: int) -> float:
            closes = df['close'].dropna()
            if len(closes) < n:
                return 0.0
            return float((closes.iloc[-1] / closes.iloc[-n] - 1.0) * 100.0)

        p20, p60, p120 = periods
        s20 = _period_return(stock_df, p20)
        s60 = _period_return(stock_df, p60)
        s120 = _period_return(stock_df, p120)
        n20 = _period_return(nifty_df, p20)
        n60 = _period_return(nifty_df, p60)
        n120 = _period_return(nifty_df, p120)

        rs20  = round(s20  - n20,  2)
        rs60  = round(s60  - n60,  2)
        rs120 = round(s120 - n120, 2)

        # Composite score: 40% weight on 20d, 35% on 60d, 25% on 120d
        composite_rs = 0.40 * rs20 + 0.35 * rs60 + 0.25 * rs120

        # Map to 0-100: +10% outperformance → 100, −10% → 0, linear
        rs_score = max(0.0, min(100.0, 50.0 + composite_rs * 2.5))

        result.update({
            'rs_20d': rs20,
            'rs_60d': rs60,
            'rs_120d': rs120,
            'rs_score': round(rs_score, 2),
        })
    except Exception as e:
        logger.debug(f"RS vs Nifty error: {e}")

    return result


def compute_atr_efficiency(df: pd.DataFrame, atr_period: int = 14, trend_period: int = 20) -> dict:
    """
    ATR Efficiency = absolute trend move over trend_period / (ATR * sqrt(trend_period)).
    Higher efficiency → trend is making directional progress relative to noise.
    Returns atr_efficiency (float) and efficiency_score (0-100).
    """
    result = {'atr_efficiency': 0.0, 'efficiency_score': 50.0}
    try:
        atr_s = compute_atr(df, atr_period)
        latest_atr = float(atr_s.iloc[-1]) if not atr_s.empty and not pd.isna(atr_s.iloc[-1]) else 0.0
        if latest_atr <= 0 or len(df) < trend_period:
            return result

        start_price = float(df['close'].iloc[-trend_period])
        end_price   = float(df['close'].iloc[-1])
        trend_move  = abs(end_price - start_price)

        # Noise baseline = ATR * sqrt(periods) (random walk expectation)
        noise_baseline = latest_atr * (trend_period ** 0.5)
        efficiency = trend_move / noise_baseline if noise_baseline > 0 else 0.0

        # Map: efficiency ≥ 2.0 → 90, ≥ 1.5 → 75, ≥ 1.0 → 65, ≥ 0.5 → 50, < 0.5 → 30
        if efficiency >= 2.0:
            score = 90.0
        elif efficiency >= 1.5:
            score = 75.0
        elif efficiency >= 1.0:
            score = 65.0
        elif efficiency >= 0.5:
            score = 50.0
        else:
            score = 30.0

        result.update({'atr_efficiency': round(efficiency, 3), 'efficiency_score': score})
    except Exception as e:
        logger.debug(f"ATR efficiency error: {e}")

    return result


def compute_higher_high_structure(df: pd.DataFrame, swing_period: int = 5) -> dict:
    """
    Detect Higher High / Higher Low structure over the last N swing points.
    Bullish:  closing price making higher highs + higher lows (HH/HL)
    Bearish:  lower lows + lower highs (LL/LH)
    Returns hh_hl_score (0-100), pattern (str), and swing_count.
    """
    result = {'hh_hl_score': 50.0, 'pattern': 'neutral', 'swing_count': 0}
    try:
        if df is None or len(df) < swing_period * 3:
            return result

        closes = df['close'].values
        n = len(closes)

        # Find local swing highs/lows using a rolling window
        window = swing_period
        swing_highs, swing_lows = [], []
        for i in range(window, n - window):
            seg = closes[i - window: i + window + 1]
            if closes[i] == max(seg):
                swing_highs.append(closes[i])
            if closes[i] == min(seg):
                swing_lows.append(closes[i])

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return result

        # Take last 3 swing highs and lows
        last_highs = swing_highs[-3:]
        last_lows  = swing_lows[-3:]

        hh_count = sum(1 for i in range(1, len(last_highs)) if last_highs[i] > last_highs[i - 1])
        hl_count = sum(1 for i in range(1, len(last_lows))  if last_lows[i]  > last_lows[i - 1])
        ll_count = sum(1 for i in range(1, len(last_lows))  if last_lows[i]  < last_lows[i - 1])
        lh_count = sum(1 for i in range(1, len(last_highs)) if last_highs[i] < last_highs[i - 1])

        bullish_votes = hh_count + hl_count
        bearish_votes = ll_count + lh_count

        if bullish_votes >= 3:
            score, pattern = 85.0, 'strong_uptrend'
        elif bullish_votes == 2:
            score, pattern = 70.0, 'uptrend'
        elif bearish_votes >= 3:
            score, pattern = 20.0, 'strong_downtrend'
        elif bearish_votes == 2:
            score, pattern = 35.0, 'downtrend'
        else:
            score, pattern = 50.0, 'neutral'

        result.update({
            'hh_hl_score': score,
            'pattern': pattern,
            'swing_count': len(swing_highs),
            'hh_count': hh_count,
            'hl_count': hl_count,
        })
    except Exception as e:
        logger.debug(f"HH/HL structure error: {e}")

    return result


def compute_trend_duration(df: pd.DataFrame, ema_span: int = 20) -> dict:
    """
    Count how many consecutive bars the EMA-20 has been sloping upward (bullish)
    or downward (bearish). Longer trend duration → more reliable momentum.
    Returns duration_bars, direction, and duration_score (0-100).
    """
    result = {'duration_bars': 0, 'direction': 'neutral', 'duration_score': 50.0}
    try:
        if df is None or len(df) < ema_span + 5:
            return result

        ema = compute_ema(df, ema_span).values
        n = len(ema)
        if n < 2:
            return result

        # Walk back from latest bar counting consecutive same-direction bars
        diffs = np.diff(ema)  # positive = rising EMA, negative = falling
        latest_dir = 1 if diffs[-1] > 0 else (-1 if diffs[-1] < 0 else 0)

        count = 0
        for d in reversed(diffs):
            bar_dir = 1 if d > 0 else (-1 if d < 0 else 0)
            if bar_dir == latest_dir:
                count += 1
            else:
                break

        direction = 'bullish' if latest_dir == 1 else ('bearish' if latest_dir == -1 else 'neutral')

        # Score: ≥40 bars → 90, ≥20 → 75, ≥10 → 65, ≥5 → 55, <5 → 40
        if count >= 40:
            score = 90.0
        elif count >= 20:
            score = 75.0
        elif count >= 10:
            score = 65.0
        elif count >= 5:
            score = 55.0
        else:
            score = 40.0

        # Bearish trend duration should lower the score
        if direction == 'bearish':
            score = 100.0 - score

        result.update({'duration_bars': count, 'direction': direction, 'duration_score': round(score, 1)})
    except Exception as e:
        logger.debug(f"Trend duration error: {e}")

    return result


def compute_52week_high_distance(df: pd.DataFrame) -> dict:
    """
    % distance from the 52-week (252-bar) high.
    Closer to 52-week high = stronger momentum.
    Returns distance_pct and distance_score (0-100).
    """
    result = {'distance_pct': 0.0, 'distance_score': 50.0, 'high_52w': 0.0}
    try:
        if df is None or df.empty:
            return result

        lookback = min(252, len(df))
        high_52w = float(df['high'].tail(lookback).max())
        current  = float(df['close'].iloc[-1])

        if high_52w <= 0:
            return result

        dist_pct = (high_52w - current) / high_52w * 100.0  # 0% = at 52w high

        # Score: within 5% → 85, within 10% → 70, within 20% → 55, within 30% → 40, >30% → 25
        if dist_pct <= 5:
            score = 85.0
        elif dist_pct <= 10:
            score = 70.0
        elif dist_pct <= 20:
            score = 55.0
        elif dist_pct <= 30:
            score = 40.0
        else:
            score = 25.0

        result.update({
            'distance_pct': round(dist_pct, 2),
            'distance_score': score,
            'high_52w': round(high_52w, 2),
        })
    except Exception as e:
        logger.debug(f"52-week high distance error: {e}")

    return result


# ── Main indicator aggregation ────────────────────────────────────────────────

def compute_all_indicators(df: pd.DataFrame, nifty_df: pd.DataFrame = None) -> dict:
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

    # ── Phase 1 additions ─────────────────────────────────────────────────
    rs_data        = compute_relative_strength_vs_nifty(df, nifty_df) if nifty_df is not None else {'rs_20d': 0.0, 'rs_60d': 0.0, 'rs_120d': 0.0, 'rs_score': 50.0}
    atr_eff        = compute_atr_efficiency(df)
    hh_hl          = compute_higher_high_structure(df)
    trend_dur      = compute_trend_duration(df)
    high_52w       = compute_52week_high_distance(df)

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
        # Phase 1 additions
        'rs_vs_nifty': rs_data,
        'atr_efficiency': atr_eff,
        'hh_hl_structure': hh_hl,
        'trend_duration': trend_dur,
        'high_52w': high_52w,
    }
