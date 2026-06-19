"""
Nonlinear penalty system — adjusts raw score based on risk and trend quality.
Scentric Proprietary Model.
"""


def apply_penalties(raw_score: float, risk_score: float, trend_score: float,
                    quant_score: float, indicators: dict) -> tuple:
    penalty = 1.0
    reasons = []

    # ── Risk penalties (softened) ─────────────────────────────────────────
    if risk_score < 30:
        penalty *= 0.80          # was 0.75 — extreme risk only gets hard cut
        reasons.append('High volatility and risk detected')
    elif risk_score < 45:
        penalty *= 0.92          # was 0.88
        reasons.append('Elevated risk levels')

    # ── Trend penalties (softened) ────────────────────────────────────────
    if trend_score < 30:
        penalty *= 0.86          # was 0.82
        reasons.append('Weak trend structure across timeframes')
    elif trend_score < 42:
        penalty *= 0.93          # was 0.90
        reasons.append('Mixed trend signals')

    # ── ATR volatility ────────────────────────────────────────────────────
    atr_pct = indicators.get('atr_pct', 0)
    if atr_pct > 6:              # was 5 — reserve penalty for truly extreme moves
        penalty *= 0.88          # was 0.85
        reasons.append('Extreme price volatility (ATR > 6%)')

    # ── RSI extremes ──────────────────────────────────────────────────────
    rsi = indicators.get('rsi', 50)
    if rsi > 82:                 # was 80
        penalty *= 0.92          # was 0.90
        reasons.append('Overbought territory (RSI > 82)')
    elif rsi < 25:               # oversold = opportunity, give a bigger boost
        bonus = min(1.12, 1.0 + (25 - rsi) / 80)
        penalty *= bonus
        reasons.append('Deep oversold — potential reversal opportunity')

    # ── Drawdown ──────────────────────────────────────────────────────────
    max_dd = abs(indicators.get('max_drawdown', 0))
    if max_dd > 25:              # was 20 — only penalise really significant drawdowns
        penalty *= 0.90          # was 0.85
        reasons.append(f'Significant drawdown ({max_dd:.1f}%)')

    # ── Bullish alignment bonus (made more accessible) ────────────────────
    # Previously: quant>70 AND trend>65 AND risk>60 → +5%
    # Now:        quant>62 AND trend>55 AND risk>52 → +8%
    if quant_score > 62 and trend_score > 55 and risk_score > 52:
        penalty *= 1.08
        reasons.append('Good alignment across quant, trend, and risk')
    elif quant_score > 55 and trend_score > 50:
        # Partial alignment also gets a small lift
        penalty *= 1.03
        reasons.append('Positive quant and trend alignment')

    penalty = max(0.55, min(1.18, penalty))
    final_score = max(0, min(100, raw_score * penalty))

    return round(final_score, 2), round(penalty, 4), reasons
