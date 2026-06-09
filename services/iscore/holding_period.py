"""
Holding Period Recommendation Engine
Computes how long to hold a stock based on I-Score components and raw indicators.

Output buckets:
  1 week   — Tactical Trade    (weak signal / high volatility)
  2 weeks  — Short Swing       (moderate signal, watch closely)
  4 weeks  — Swing Trade       (good signal, trend confirmed)
  3 months — Position Trade    (strong signal, low risk, durable trend)
  Exit     — No holding        (sell signal)
"""

from datetime import date, timedelta


def compute_holding_period(
    recommendation: str,
    overall_score: float,
    risk_score: float = 50.0,
    trend_score: float = 50.0,
    quantitative_score: float = 50.0,
    raw_indicators: dict = None,
    is_stock: bool = True,
) -> dict:
    """
    Compute holding period recommendation from I-Score components.

    Returns a dict:
        period        — human label e.g. "4 weeks"
        days          — integer days
        label         — trade style e.g. "Swing Trade"
        entry_timing  — when / how to enter
        exit_trigger  — what to watch to exit
        review_date   — ISO date string for next review
        color         — Bootstrap colour token (success / primary / warning / danger / secondary)
        action        — BUY | HOLD | EXIT
        reasoning     — one-line explanation
    """
    ri = raw_indicators or {}
    rsi = ri.get('rsi', 50.0) or 50.0
    supertrend = (ri.get('supertrend_direction') or 'neutral').lower()
    mom5 = ri.get('momentum_5d', 0.0) or 0.0
    atr_pct = ri.get('atr_pct', 2.0) or 2.0

    rec = (recommendation or 'HOLD').upper()

    # ── Holding period decision ────────────────────────────────────────────
    if rec in ('STRONG_BUY',):
        if risk_score >= 65 and trend_score >= 65:
            days, period, label, color, action = 90, '3 months', 'Position Trade', 'success', 'BUY'
            reasoning = 'Strong I-Score with low risk and durable trend — suitable for a 3-month position.'
        elif risk_score >= 55:
            days, period, label, color, action = 28, '4 weeks', 'Swing Trade', 'success', 'BUY'
            reasoning = 'Strong signal but elevated volatility — take profits within 4 weeks.'
        else:
            days, period, label, color, action = 14, '2 weeks', 'Short Swing', 'primary', 'BUY'
            reasoning = 'Strong score yet high volatility (ATR > 3%). Shorter hold reduces risk.'

    elif rec == 'BUY':
        if trend_score >= 60 and risk_score >= 55:
            days, period, label, color, action = 28, '4 weeks', 'Swing Trade', 'primary', 'BUY'
            reasoning = 'Confirmed uptrend with manageable risk supports a 4-week swing.'
        elif trend_score >= 45:
            days, period, label, color, action = 14, '2 weeks', 'Short Swing', 'primary', 'BUY'
            reasoning = 'Moderate trend strength — short swing with tight stop-loss recommended.'
        else:
            days, period, label, color, action = 7, '1 week', 'Tactical Trade', 'warning', 'BUY'
            reasoning = 'Weak trend despite buy signal — tactical entry only, review in 1 week.'

    elif rec == 'HOLD':
        if trend_score >= 55 and overall_score >= 55:
            days, period, label, color, action = 14, '2 weeks', 'Monitor & Hold', 'warning', 'HOLD'
            reasoning = 'Mixed signals — hold existing position, review in 2 weeks.'
        else:
            days, period, label, color, action = 7, '1 week', 'Review Position', 'warning', 'HOLD'
            reasoning = 'Weak momentum — review and reassess position within 1 week.'

    elif rec == 'CAUTIONARY_SELL':
        days, period, label, color, action = 0, 'Exit Soon', 'Reduce Exposure', 'danger', 'EXIT'
        reasoning = 'Bearish pressure building — reduce exposure and tighten stop-losses.'

    else:
        days, period, label, color, action = 0, 'Exit Now', 'Exit Signal', 'danger', 'EXIT'
        reasoning = 'Strong sell signal across indicators — exit or avoid entering new positions.'

    # ── Entry timing ───────────────────────────────────────────────────────
    if action == 'EXIT':
        entry_timing = 'Avoid / Exit — do not initiate new positions'
    elif rsi < 38:
        entry_timing = 'Strong Entry — RSI oversold, attractive buy zone'
    elif rsi < 50 and supertrend == 'buy':
        entry_timing = 'Good Entry — RSI healthy and SuperTrend bullish'
    elif rsi < 55 and mom5 > 0:
        entry_timing = 'Accumulate — Stagger entry across 2–3 sessions'
    elif rsi > 70:
        entry_timing = 'Wait for Pullback — RSI elevated (>' + str(round(rsi)) + '), enter on dip'
    elif rsi > 62:
        entry_timing = 'Partial Entry — RSI stretched; buy 50% now, rest on pullback'
    else:
        entry_timing = 'Neutral Entry — enter on confirmation of trend direction'

    # ── Exit trigger ───────────────────────────────────────────────────────
    if days == 90:
        exit_trigger = 'Exit if monthly close breaks 50 EMA, or I-Score falls below 65 on re-run'
    elif days == 28:
        exit_trigger = 'Book profit at 8–12% gain; exit if daily close breaks 20 EMA'
    elif days == 14:
        exit_trigger = 'Book profit at 5–8% gain; cut loss on any close below entry –3%'
    elif days == 7:
        exit_trigger = 'Strict stop-loss at –2% from entry; exit on any trend reversal signal'
    else:
        exit_trigger = 'Exit immediately — square off or reduce position size'

    # ── Review date ────────────────────────────────────────────────────────
    review_date = (date.today() + timedelta(days=max(days, 1))).isoformat() if days > 0 else date.today().isoformat()

    return {
        'period': period,
        'days': days,
        'label': label,
        'entry_timing': entry_timing,
        'exit_trigger': exit_trigger,
        'review_date': review_date,
        'color': color,
        'action': action,
        'reasoning': reasoning,
    }
