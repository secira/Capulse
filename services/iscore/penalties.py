"""
Nonlinear penalty system — adjusts raw score based on risk and trend quality.
Scentric Proprietary Model.

Phase 11 additions:
  - fundamental_deterioration: weak earnings, promoter selling, institutional exit
  - sector_underperformance: RS vs Nifty deep negative
  - negative_revision: bearish AI market intelligence signal
  Penalty multiplier range preserved: 0.55 – 1.18
"""


def apply_penalties(
    raw_score: float,
    risk_score: float,
    trend_score: float,
    quant_score: float,
    indicators: dict,
    market_intelligence_score: float = 50.0,
    fundamental_score: float = 50.0,
    institutional_score: float = 50.0,
) -> tuple:
    penalty = 1.0
    reasons = []

    # ── Risk penalties ────────────────────────────────────────────────────
    if risk_score < 30:
        penalty *= 0.80
        reasons.append('High volatility and risk detected')
    elif risk_score < 45:
        penalty *= 0.92
        reasons.append('Elevated risk levels')

    # ── Trend penalties ───────────────────────────────────────────────────
    if trend_score < 30:
        penalty *= 0.86
        reasons.append('Weak trend structure across timeframes')
    elif trend_score < 42:
        penalty *= 0.93
        reasons.append('Mixed trend signals')

    # ── ATR volatility ────────────────────────────────────────────────────
    atr_pct = indicators.get('atr_pct', 0)
    if atr_pct > 6:
        penalty *= 0.88
        reasons.append('Extreme price volatility (ATR > 6%)')

    # ── RSI extremes ──────────────────────────────────────────────────────
    rsi = indicators.get('rsi', 50)
    if rsi > 82:
        penalty *= 0.92
        reasons.append('Overbought territory (RSI > 82)')
    elif rsi < 25:
        bonus = min(1.12, 1.0 + (25 - rsi) / 80)
        penalty *= bonus
        reasons.append('Deep oversold — potential reversal opportunity')

    # ── Drawdown ──────────────────────────────────────────────────────────
    max_dd = abs(indicators.get('max_drawdown', 0))
    if max_dd > 25:
        penalty *= 0.90
        reasons.append(f'Significant drawdown ({max_dd:.1f}%)')

    # ── Phase 11: Fundamental deterioration ──────────────────────────────
    # Low fundamental score signals weak earnings / promoter selling
    if fundamental_score < 30:
        penalty *= 0.85
        reasons.append('Fundamental deterioration — weak earnings or promoter selling detected')
    elif fundamental_score < 42:
        penalty *= 0.93
        reasons.append('Weak fundamentals — below-average business quality')

    # ── Phase 11: Sector underperformance (RS vs Nifty) ──────────────────
    rs_data = indicators.get('rs_vs_nifty', {})
    rs_20d = rs_data.get('rs_20d', 0.0)
    rs_60d = rs_data.get('rs_60d', 0.0)
    if rs_20d < -8 and rs_60d < -5:
        # Deeply underperforming on both short and medium horizon
        penalty *= 0.88
        reasons.append(f'Sector/stock underperformance vs Nifty (20d: {rs_20d:+.1f}%, 60d: {rs_60d:+.1f}%)')
    elif rs_20d < -5:
        penalty *= 0.94
        reasons.append(f'Short-term underperformance vs Nifty (20d: {rs_20d:+.1f}%)')

    # ── Phase 11: Negative market intelligence (AI sentiment) ────────────
    if market_intelligence_score < 30:
        penalty *= 0.88
        reasons.append('Strongly negative market intelligence — bearish news and sentiment')
    elif market_intelligence_score < 40:
        penalty *= 0.94
        reasons.append('Negative news flow and market sentiment')

    # ── Phase 11: Institutional exit ─────────────────────────────────────
    if institutional_score < 30:
        penalty *= 0.92
        reasons.append('Institutional quality concerns — promoter/FII/DII activity weak')

    # ── Bullish alignment bonus ───────────────────────────────────────────
    if quant_score > 62 and trend_score > 55 and risk_score > 52:
        penalty *= 1.08
        reasons.append('Good alignment across quant, trend, and risk')
    elif quant_score > 55 and trend_score > 50:
        penalty *= 1.03
        reasons.append('Positive quant and trend alignment')

    # ── Phase 11: Fundamental + market intelligence bonus ────────────────
    if fundamental_score > 70 and market_intelligence_score > 65:
        penalty *= 1.05
        reasons.append('Strong fundamentals with positive market intelligence')

    penalty = max(0.55, min(1.18, penalty))
    final_score = max(0, min(100, raw_score * penalty))

    return round(final_score, 2), round(penalty, 4), reasons
