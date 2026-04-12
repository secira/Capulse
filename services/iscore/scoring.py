"""
Component scoring functions — converts raw indicator values to 0-100 scores.
Scentric Proprietary Model.
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
    rsi_s = score_rsi(indicators['rsi'])
    ema_s = score_ema_alignment(indicators.get('ema9'), indicators.get('ema20'), indicators.get('ema50'))
    mom_s = score_momentum(indicators.get('momentum_5d', 0), indicators.get('momentum_20d', 0))
    st_s = score_supertrend(indicators.get('supertrend_direction', 'sell'))

    composite = 0.30 * rsi_s + 0.25 * ema_s + 0.25 * mom_s + 0.20 * st_s
    return {
        'composite': round(composite, 2),
        'rsi_score': round(rsi_s, 2),
        'ema_score': round(ema_s, 2),
        'momentum_score': round(mom_s, 2),
        'supertrend_score': round(st_s, 2),
    }


def compute_risk_score(indicators: dict, beta: float = 1.0) -> dict:
    vol_s = score_volatility_risk(indicators.get('atr_pct', 3.0))
    dd_s = score_drawdown(indicators.get('max_drawdown', 0))
    beta_s = score_beta(beta)

    composite = 0.40 * vol_s + 0.35 * dd_s + 0.25 * beta_s
    return {
        'composite': round(composite, 2),
        'volatility_score': round(vol_s, 2),
        'drawdown_score': round(dd_s, 2),
        'beta_score': round(beta_s, 2),
    }


def compute_trend_score_from_indicators(indicators: dict) -> dict:
    mtf_s = score_multi_timeframe(
        indicators.get('short_trend', 'neutral'),
        indicators.get('medium_trend', 'neutral'),
        indicators.get('long_trend', 'neutral'),
    )
    vol_s = score_volume(
        indicators.get('volume', {}).get('volume_ratio', 1.0),
        indicators.get('volume', {}).get('is_spike', False),
    )
    composite = 0.60 * mtf_s + 0.40 * vol_s
    return {
        'composite': round(composite, 2),
        'multi_timeframe_score': round(mtf_s, 2),
        'volume_score': round(vol_s, 2),
    }
