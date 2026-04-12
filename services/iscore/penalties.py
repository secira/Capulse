"""
Nonlinear penalty system — adjusts raw score based on risk and trend quality.
Scentric Proprietary Model.
"""


def apply_penalties(raw_score: float, risk_score: float, trend_score: float,
                    quant_score: float, indicators: dict) -> tuple:
    penalty = 1.0
    reasons = []

    if risk_score < 35:
        penalty *= 0.75
        reasons.append('High volatility and risk detected')
    elif risk_score < 50:
        penalty *= 0.88
        reasons.append('Elevated risk levels')

    if trend_score < 35:
        penalty *= 0.82
        reasons.append('Weak trend structure across timeframes')
    elif trend_score < 45:
        penalty *= 0.90
        reasons.append('Mixed trend signals')

    atr_pct = indicators.get('atr_pct', 0)
    if atr_pct > 5:
        penalty *= 0.85
        reasons.append('Extreme price volatility (ATR > 5%)')

    rsi = indicators.get('rsi', 50)
    if rsi > 80:
        penalty *= 0.90
        reasons.append('Overbought territory (RSI > 80)')
    elif rsi < 20:
        bonus = min(1.10, 1.0 + (20 - rsi) / 100)
        penalty *= bonus
        reasons.append('Deep oversold — potential reversal opportunity')

    max_dd = abs(indicators.get('max_drawdown', 0))
    if max_dd > 20:
        penalty *= 0.85
        reasons.append(f'Significant drawdown ({max_dd:.1f}%)')

    if quant_score > 70 and trend_score > 65 and risk_score > 60:
        penalty *= 1.05
        reasons.append('Strong alignment across quant, trend, and risk')

    penalty = max(0.50, min(1.15, penalty))
    final_score = max(0, min(100, raw_score * penalty))

    return round(final_score, 2), round(penalty, 4), reasons
