"""
Confidence scoring — measures how reliable the I-Score is based on data quality
and component agreement.
Scentric Proprietary Model.
"""

import numpy as np


def compute_confidence(component_scores: list, data_quality: dict = None) -> dict:
    if not component_scores:
        return {'level': 'Low', 'value': 0.3, 'reason': 'Insufficient data'}

    std = float(np.std(component_scores))
    mean_score = float(np.mean(component_scores))

    if std < 8:
        level = 'High'
        base = 0.85
    elif std < 15:
        level = 'Medium'
        base = 0.65
    else:
        level = 'Low'
        base = 0.45

    if data_quality:
        if data_quality.get('has_real_indicators', False):
            base += 0.05
        if data_quality.get('has_volume', False):
            base += 0.03
        if data_quality.get('days_of_data', 0) >= 50:
            base += 0.05
        elif data_quality.get('days_of_data', 0) < 20:
            base -= 0.10
            level = 'Low' if level != 'Low' else level
        if data_quality.get('is_fallback', False):
            base -= 0.15
            level = 'Low'

    base = max(0.20, min(0.95, base))

    if std < 8:
        reason = 'Components in strong agreement'
    elif std < 15:
        reason = 'Some divergence between components'
    else:
        reason = 'Significant disagreement between components'

    return {
        'level': level,
        'value': round(base, 2),
        'std_dev': round(std, 2),
        'reason': reason,
    }


def generate_score_factors(indicators: dict, component_scores: dict) -> list:
    factors = []

    rsi = indicators.get('rsi', 50)
    if rsi < 30:
        factors.append({'type': 'positive', 'text': f'Oversold RSI ({rsi:.0f}) — potential upside'})
    elif rsi > 70:
        factors.append({'type': 'negative', 'text': f'Overbought RSI ({rsi:.0f}) — potential pullback'})
    else:
        factors.append({'type': 'neutral', 'text': f'RSI in neutral zone ({rsi:.0f})'})

    st = indicators.get('supertrend_direction', 'sell')
    if st == 'buy':
        factors.append({'type': 'positive', 'text': 'SuperTrend indicates BUY signal'})
    else:
        factors.append({'type': 'negative', 'text': 'SuperTrend indicates SELL signal'})

    short = indicators.get('short_trend', 'neutral')
    medium = indicators.get('medium_trend', 'neutral')
    long = indicators.get('long_trend', 'neutral')
    bullish_count = sum(1 for t in [short, medium, long] if t == 'bullish')
    if bullish_count == 3:
        factors.append({'type': 'positive', 'text': 'All timeframes aligned bullish'})
    elif bullish_count == 0:
        factors.append({'type': 'negative', 'text': 'All timeframes bearish'})
    else:
        factors.append({'type': 'neutral', 'text': f'{bullish_count}/3 timeframes bullish'})

    mom5 = indicators.get('momentum_5d', 0)
    if mom5 > 3:
        factors.append({'type': 'positive', 'text': f'Strong 5-day momentum (+{mom5:.1f}%)'})
    elif mom5 < -3:
        factors.append({'type': 'negative', 'text': f'Weak 5-day momentum ({mom5:.1f}%)'})

    atr_pct = indicators.get('atr_pct', 0)
    if atr_pct > 4:
        factors.append({'type': 'negative', 'text': f'High volatility (ATR {atr_pct:.1f}%)'})
    elif atr_pct < 1.5:
        factors.append({'type': 'positive', 'text': f'Low volatility (ATR {atr_pct:.1f}%)'})

    vol = indicators.get('volume', {})
    if vol.get('is_spike'):
        factors.append({'type': 'positive', 'text': f'Volume spike ({vol.get("volume_ratio", 1):.1f}x avg)'})
    elif vol.get('volume_ratio', 1) < 0.5:
        factors.append({'type': 'negative', 'text': 'Low trading volume'})

    max_dd = abs(indicators.get('max_drawdown', 0))
    if max_dd > 15:
        factors.append({'type': 'negative', 'text': f'Significant recent drawdown ({max_dd:.1f}%)'})

    qual = component_scores.get('qualitative', 0)
    if qual > 70:
        factors.append({'type': 'positive', 'text': 'Positive news and sentiment'})
    elif qual < 40:
        factors.append({'type': 'negative', 'text': 'Negative news sentiment'})

    return factors
