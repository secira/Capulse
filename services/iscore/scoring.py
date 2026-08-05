"""
Component scoring functions — converts raw indicator values to 0-100 scores.
Scentric Proprietary Model.

Phase 1 changes:
  - compute_quant_score(): Relative Strength 25%, EMA 20%, Momentum 20%, RSI 15%,
    SuperTrend 10%, ATR Efficiency 10%  (reduces RSI/EMA/Momentum correlation)
  - compute_trend_score_from_indicators(): EMA Alignment 20%, Higher High/HL 20%,
    Trend Duration 15%, Relative Strength 20%, Volume 15%, 52-week High Distance 10%
"""


def score_rsi(rsi: float) -> float:
    if rsi < 25:
        return 85.0
    elif rsi < 30:
        return 75.0
    elif rsi < 40:
        return 65.0
    elif rsi < 60:
        return 50.0
    elif rsi < 70:
        return 60.0
    elif rsi < 80:
        return 35.0
    else:
        return 20.0


def score_ema_alignment(ema9, ema20, ema50) -> float:
    if ema50 is None:
        if ema9 is not None and ema20 is not None:
            return 70.0 if ema9 > ema20 else 30.0
        return 50.0
    if ema9 > ema20 > ema50:
        return 90.0
    elif ema9 > ema20:
        return 70.0
    elif ema20 > ema50:
        return 55.0
    elif ema9 < ema20 < ema50:
        return 10.0
    elif ema9 < ema20:
        return 30.0
    return 45.0


def score_momentum(m5: float, m20: float = 0.0) -> float:
    base = 50.0
    if m5 > 5:
        base += 25
    elif m5 > 2:
        base += 15
    elif m5 > 0:
        base += 5
    elif m5 > -2:
        base -= 5
    elif m5 > -5:
        base -= 15
    else:
        base -= 25

    if m20 > 5:
        base += 10
    elif m20 > 0:
        base += 5
    elif m20 < -5:
        base -= 10
    elif m20 < 0:
        base -= 5

    return max(0.0, min(100.0, base))


def score_supertrend(direction: str) -> float:
    return 75.0 if direction == 'buy' else 25.0


def score_volatility_risk(atr_pct: float) -> float:
    if atr_pct < 1.0:
        return 90.0
    elif atr_pct < 2.0:
        return 75.0
    elif atr_pct < 3.0:
        return 60.0
    elif atr_pct < 5.0:
        return 40.0
    else:
        return 20.0


def score_drawdown(max_dd: float) -> float:
    dd = abs(max_dd)
    if dd < 3:
        return 90.0
    elif dd < 7:
        return 70.0
    elif dd < 15:
        return 50.0
    elif dd < 25:
        return 30.0
    else:
        return 15.0


def score_beta(beta: float) -> float:
    if 0.8 <= beta <= 1.2:
        return 75.0
    elif 0.5 <= beta <= 1.5:
        return 60.0
    elif beta < 0.5:
        return 50.0
    else:
        return 30.0


def score_volume(volume_ratio: float, is_spike: bool) -> float:
    if is_spike:
        return 75.0
    elif volume_ratio > 1.0:
        return 65.0
    elif volume_ratio > 0.5:
        return 50.0
    else:
        return 35.0


def score_multi_timeframe(short: str, medium: str, long: str) -> float:
    score_map = {'bullish': 1, 'neutral': 0, 'bearish': -1}
    total = score_map.get(short, 0) + score_map.get(medium, 0) + score_map.get(long, 0)
    mapping = {3: 95, 2: 80, 1: 65, 0: 50, -1: 35, -2: 20, -3: 10}
    return float(mapping.get(total, 50))


def compute_quant_score(indicators: dict) -> dict:
    """
    Phase 1: Reduced-correlation quantitative score.
    Weights: Relative Strength 25%, EMA 20%, Momentum 20%, RSI 15%,
             SuperTrend 10%, ATR Efficiency 10%.

    Relative Strength and ATR Efficiency are orthogonal to RSI/EMA/Momentum,
    breaking the triple-counting of the same price move.
    """
    rsi_s  = score_rsi(indicators['rsi'])
    ema_s  = score_ema_alignment(indicators.get('ema9'), indicators.get('ema20'), indicators.get('ema50'))
    mom_s  = score_momentum(indicators.get('momentum_5d', 0), indicators.get('momentum_20d', 0))
    st_s   = score_supertrend(indicators.get('supertrend_direction', 'sell'))

    # Phase 1 additions
    rs_data = indicators.get('rs_vs_nifty', {})
    rs_s    = float(rs_data.get('rs_score', 50.0))

    atr_eff_data = indicators.get('atr_efficiency', {})
    eff_s = float(atr_eff_data.get('efficiency_score', 50.0))

    # Phase 1 weights — Relative Strength 25%, EMA 20%, Momentum 20%, RSI 15%,
    # SuperTrend 10%, ATR Efficiency 10%
    composite = (
        0.25 * rs_s  +
        0.20 * ema_s +
        0.20 * mom_s +
        0.15 * rsi_s +
        0.10 * st_s  +
        0.10 * eff_s
    )

    return {
        'composite': round(composite, 2),
        'rsi_score': round(rsi_s, 2),
        'ema_score': round(ema_s, 2),
        'momentum_score': round(mom_s, 2),
        'supertrend_score': round(st_s, 2),
        'relative_strength_score': round(rs_s, 2),
        'atr_efficiency_score': round(eff_s, 2),
    }


def compute_risk_score(indicators: dict, beta: float = 1.0) -> dict:
    vol_s  = score_volatility_risk(indicators.get('atr_pct', 3.0))
    dd_s   = score_drawdown(indicators.get('max_drawdown', 0))
    beta_s = score_beta(beta)

    composite = 0.40 * vol_s + 0.35 * dd_s + 0.25 * beta_s
    return {
        'composite': round(composite, 2),
        'volatility_score': round(vol_s, 2),
        'drawdown_score': round(dd_s, 2),
        'beta_score': round(beta_s, 2),
    }


def compute_trend_score_from_indicators(indicators: dict) -> dict:
    """
    Phase 1: Expanded trend score.
    Weights: EMA Alignment 20%, Higher High/HL 20%, Trend Duration 15%,
             Relative Strength 20%, Volume 15%, 52-week High Distance 10%.

    Previously just MTF EMA (60%) + Volume (40%).
    """
    # EMA alignment (replaces multi-timeframe — same data, but labelled correctly)
    ema_align_s = score_multi_timeframe(
        indicators.get('short_trend', 'neutral'),
        indicators.get('medium_trend', 'neutral'),
        indicators.get('long_trend', 'neutral'),
    )

    # Higher High / Higher Low structure
    hh_data = indicators.get('hh_hl_structure', {})
    hh_s = float(hh_data.get('hh_hl_score', 50.0))

    # Trend duration
    dur_data = indicators.get('trend_duration', {})
    dur_s = float(dur_data.get('duration_score', 50.0))

    # Relative Strength vs Nifty
    rs_data = indicators.get('rs_vs_nifty', {})
    rs_s = float(rs_data.get('rs_score', 50.0))

    # Volume
    vol_data = indicators.get('volume', {})
    vol_s = score_volume(
        vol_data.get('volume_ratio', 1.0),
        vol_data.get('is_spike', False),
    )

    # 52-week high proximity
    h52_data = indicators.get('high_52w', {})
    h52_s = float(h52_data.get('distance_score', 50.0))

    composite = (
        0.20 * ema_align_s +
        0.20 * hh_s        +
        0.15 * dur_s       +
        0.20 * rs_s        +
        0.15 * vol_s       +
        0.10 * h52_s
    )

    return {
        'composite': round(composite, 2),
        # Legacy key kept for backward compatibility
        'multi_timeframe_score': round(ema_align_s, 2),
        'volume_score': round(vol_s, 2),
        # Phase 1 additions
        'ema_alignment_score': round(ema_align_s, 2),
        'hh_hl_score': round(hh_s, 2),
        'trend_duration_score': round(dur_s, 2),
        'relative_strength_score': round(rs_s, 2),
        'distance_52w_score': round(h52_s, 2),
    }
