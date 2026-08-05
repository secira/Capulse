"""
Confidence scoring — measures how reliable the I-Score is based on data quality
and component agreement.
Scentric Proprietary Model.

Phase 10 additions:
  - freshness of news / financial statements
  - volume of data (bars available)
  - missing indicators
  - use of fallback APIs
  - contradicting signals
  - data quality
"""

import numpy as np


def compute_confidence(component_scores: list, data_quality: dict = None) -> dict:
    if not component_scores:
        return {'level': 'Low', 'value': 0.3, 'reason': 'Insufficient data'}

    std = float(np.std(component_scores))
    mean_score = float(np.mean(component_scores))

    # Base confidence from component agreement
    if std < 8:
        level = 'High'
        base = 0.85
    elif std < 15:
        level = 'Medium'
        base = 0.65
    else:
        level = 'Low'
        base = 0.45

    reasons_positive = []
    reasons_negative = []

    # ── Phase 10: Data freshness & volume ────────────────────────────────
    if data_quality:
        # Real vs fallback indicators
        if data_quality.get('has_real_indicators', False):
            base += 0.05
            reasons_positive.append('Real technical indicators available')
        if data_quality.get('has_volume', False):
            base += 0.03

        # Days of data (bar count)
        days = data_quality.get('days_of_data', 0)
        if days >= 120:
            base += 0.05
            reasons_positive.append(f'{days} days of price history')
        elif days >= 50:
            base += 0.02
        elif days < 20:
            base -= 0.10
            level = 'Low' if level != 'Low' else level
            reasons_negative.append(f'Limited price history ({days} days)')

        # Fallback API
        if data_quality.get('is_fallback', False):
            base -= 0.15
            level = 'Low'
            reasons_negative.append('Using fallback/estimated data')

        # Phase 10: Contradicting signals
        contradictions = int(data_quality.get('contradicting_signals', 0))
        if contradictions >= 3:
            base -= 0.10
            reasons_negative.append(f'{contradictions} contradicting signals across components')
        elif contradictions >= 2:
            base -= 0.05
            reasons_negative.append('Some contradicting signals')

        # Phase 10: Missing indicators
        missing = int(data_quality.get('missing_indicators', 0))
        if missing >= 3:
            base -= 0.10
            reasons_negative.append(f'{missing} indicators missing or estimated')
        elif missing >= 1:
            base -= 0.04

        # Phase 10: Fundamental data age
        fundamentals_stale = data_quality.get('fundamentals_stale', False)
        if fundamentals_stale:
            base -= 0.05
            reasons_negative.append('Fundamental data may be stale (>90 days)')

        # Phase 10: Number of AI analysis components available
        ai_components_available = int(data_quality.get('ai_components_available', 1))
        if ai_components_available >= 3:
            base += 0.04
            reasons_positive.append('Multiple AI analysis sources')
        elif ai_components_available == 0:
            base -= 0.08
            reasons_negative.append('No AI analysis available')

    base = max(0.20, min(0.95, base))

    # Build reason string
    if std < 8:
        reason = 'Components in strong agreement'
    elif std < 15:
        reason = 'Some divergence between components'
    else:
        reason = 'Significant disagreement between components'

    if reasons_negative:
        reason += '. Concerns: ' + '; '.join(reasons_negative[:3])

    return {
        'level': level,
        'value': round(base, 2),
        'std_dev': round(std, 2),
        'mean_score': round(mean_score, 2),
        'reason': reason,
        'positives': reasons_positive,
        'negatives': reasons_negative,
    }


def _count_contradictions(component_scores: dict) -> int:
    """Count how many component pairs are pulling in opposite directions."""
    scores = list(component_scores.values())
    above = sum(1 for s in scores if s > 60)
    below = sum(1 for s in scores if s < 40)
    return min(above, below)


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

    # Phase 1: RS vs Nifty factor
    rs_data = indicators.get('rs_vs_nifty', {})
    rs_20d = rs_data.get('rs_20d', 0)
    if rs_20d > 5:
        factors.append({'type': 'positive', 'text': f'Outperforming Nifty by {rs_20d:+.1f}% (20d)'})
    elif rs_20d < -5:
        factors.append({'type': 'negative', 'text': f'Underperforming Nifty by {rs_20d:.1f}% (20d)'})

    # Phase 1: HH/HL structure factor
    hh_data = indicators.get('hh_hl_structure', {})
    pattern = hh_data.get('pattern', 'neutral')
    if pattern in ('strong_uptrend', 'uptrend'):
        factors.append({'type': 'positive', 'text': f'Higher High / Higher Low structure confirmed ({pattern})'})
    elif pattern in ('strong_downtrend', 'downtrend'):
        factors.append({'type': 'negative', 'text': f'Lower Low / Lower High structure detected ({pattern})'})

    # Phase 1: 52-week high proximity
    h52_data = indicators.get('high_52w', {})
    dist = h52_data.get('distance_pct', 0)
    if dist <= 5:
        factors.append({'type': 'positive', 'text': f'Near 52-week high (within {dist:.1f}%)'})
    elif dist > 30:
        factors.append({'type': 'negative', 'text': f'{dist:.0f}% below 52-week high'})

    # Market intelligence score
    mi_score = component_scores.get('market_intelligence', component_scores.get('qualitative', 0))
    if mi_score > 70:
        factors.append({'type': 'positive', 'text': 'Positive news and market intelligence'})
    elif mi_score < 40:
        factors.append({'type': 'negative', 'text': 'Negative news and market sentiment'})

    return factors
