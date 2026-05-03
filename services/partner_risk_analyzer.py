"""
Stateless Portfolio Risk Analyzer for the B2B Partner API.

Operates purely on the JSON payload submitted by a partner — no DB lookups,
no user context. Mirrors the scoring philosophy of services/risk_engine.py
but is safe to expose as a SaaS endpoint.

Input shape (per holding):
    {symbol, qty, avg_price, current_price?, sector?, asset_class?}
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

# Mirrors services/risk_engine.ASSET_RISK_CONFIG (kept local to avoid coupling).
_ASSET_RISK = {
    'equities':           {'score': 8,  'label': 'High'},
    'equity':             {'score': 8,  'label': 'High'},
    'stocks':             {'score': 8,  'label': 'High'},
    'mutual_funds':       {'score': 5,  'label': 'Medium'},
    'mf':                 {'score': 5,  'label': 'Medium'},
    'fixed_deposits':     {'score': 2,  'label': 'Low'},
    'fd':                 {'score': 2,  'label': 'Low'},
    'real_estate':        {'score': 5,  'label': 'Medium'},
    'gold':               {'score': 4,  'label': 'Medium'},
    'commodities':        {'score': 4,  'label': 'Medium'},
    'crypto':             {'score': 9,  'label': 'Very High'},
    'cryptocurrency':     {'score': 9,  'label': 'Very High'},
    'fno':                {'score': 10, 'label': 'Very High'},
    'derivatives':        {'score': 10, 'label': 'Very High'},
    'insurance':          {'score': 1,  'label': 'Very Low'},
    'bonds':              {'score': 3,  'label': 'Low'},
}

_DEFAULT_RISK = {'score': 6, 'label': 'Medium'}


def _norm(s: Any) -> str:
    return str(s or '').strip().lower().replace(' ', '_').replace('&', '').replace('/', '_')


def _validate_holdings(raw: list) -> list[dict]:
    """Coerce, validate, and normalise holdings. Raises ValueError on bad input."""
    if not isinstance(raw, list):
        raise ValueError('holdings must be a list')
    if not raw:
        raise ValueError('holdings list is empty')
    if len(raw) > 50:
        raise ValueError(f'Maximum 50 holdings per request (got {len(raw)})')

    out = []
    for i, h in enumerate(raw):
        if not isinstance(h, dict):
            raise ValueError(f'holdings[{i}] must be an object')
        symbol = (h.get('symbol') or '').strip().upper()
        if not symbol:
            raise ValueError(f'holdings[{i}].symbol is required')
        try:
            qty       = float(h.get('qty') or h.get('quantity') or 0)
            avg_price = float(h.get('avg_price') or h.get('avg_cost') or 0)
        except (TypeError, ValueError):
            raise ValueError(f'holdings[{i}] qty/avg_price must be numeric')
        if qty <= 0 or avg_price <= 0:
            raise ValueError(f'holdings[{i}] qty and avg_price must be > 0')

        cur = h.get('current_price')
        try:
            current_price = float(cur) if cur not in (None, '') else avg_price
        except (TypeError, ValueError):
            current_price = avg_price

        out.append({
            'symbol':        symbol,
            'qty':           qty,
            'avg_price':     round(avg_price, 4),
            'current_price': round(current_price, 4),
            'sector':        (h.get('sector') or 'Unknown').strip() or 'Unknown',
            'asset_class':   (h.get('asset_class') or 'Equities').strip() or 'Equities',
        })
    return out


def _per_holding_metrics(h: dict) -> dict:
    invested = h['qty'] * h['avg_price']
    current  = h['qty'] * h['current_price']
    pnl      = current - invested
    pnl_pct  = (pnl / invested * 100) if invested else 0.0
    risk     = _ASSET_RISK.get(_norm(h['asset_class']), _DEFAULT_RISK)
    return {
        **h,
        'invested':       round(invested, 2),
        'current_value':  round(current, 2),
        'pnl':            round(pnl, 2),
        'pnl_pct':        round(pnl_pct, 2),
        'risk_score':     risk['score'],
        'risk_label':     risk['label'],
    }


def _allocation(rows: list[dict], key: str, total: float) -> list[dict]:
    bucket = defaultdict(lambda: {'invested': 0.0, 'current_value': 0.0, 'count': 0})
    for r in rows:
        b = bucket[r[key] or 'Unknown']
        b['invested']      += r['invested']
        b['current_value'] += r['current_value']
        b['count']         += 1
    out = []
    for name, v in bucket.items():
        weight = (v['current_value'] / total * 100) if total else 0
        out.append({
            'name':         name,
            'invested':     round(v['invested'], 2),
            'current_value':round(v['current_value'], 2),
            'weight_pct':   round(weight, 2),
            'holdings':     v['count'],
        })
    out.sort(key=lambda x: x['weight_pct'], reverse=True)
    return out


def _hhi(weights_pct: list[float]) -> float:
    """Herfindahl-Hirschman Index on percentages → 0..10000. >2500 = highly concentrated."""
    return round(sum(w * w for w in weights_pct), 2)


def _portfolio_risk_score(rows: list[dict], total: float, hhi: float, sector_count: int) -> dict:
    """0–100 score. Higher = riskier. Penalises concentration + high-risk asset weight."""
    if not rows or total <= 0:
        return {'score': 50, 'grade': 'Unknown'}

    # Weighted-average asset risk (0..10) → scale to 0..70
    weighted_risk = sum((r['current_value'] / total) * r['risk_score'] for r in rows)
    base = (weighted_risk / 10.0) * 70.0

    # Concentration penalty 0..20 (HHI 1000 → 4 pts, 2500 → 10, 5000+ → 20)
    conc_pen = min(20.0, (hhi / 5000.0) * 20.0)

    # Diversification penalty 0..10 (sectors: 1→10, 5→2, 10+→0)
    div_pen = max(0.0, 10.0 - (sector_count - 1) * 1.2)

    score = max(0.0, min(100.0, base + conc_pen + div_pen))
    if   score < 30: grade = 'Conservative'
    elif score < 50: grade = 'Balanced'
    elif score < 70: grade = 'Aggressive'
    else:            grade = 'Highly Aggressive'
    return {'score': round(score, 1), 'grade': grade}


def _build_warnings(rows: list[dict], sector_alloc: list[dict], total: float) -> list[dict]:
    warns = []
    # Single-holding concentration > 25%
    for r in rows:
        w = (r['current_value'] / total * 100) if total else 0
        if w > 25:
            warns.append({
                'type': 'concentration',
                'severity': 'high' if w > 40 else 'medium',
                'message': f"{r['symbol']} is {w:.1f}% of the portfolio — single-stock concentration risk.",
            })
    # Sector concentration > 40%
    for s in sector_alloc:
        if s['weight_pct'] > 40 and s['name'] != 'Unknown':
            warns.append({
                'type': 'sector_concentration',
                'severity': 'high' if s['weight_pct'] > 60 else 'medium',
                'message': f"Sector '{s['name']}' is {s['weight_pct']:.1f}% of the portfolio.",
            })
    # High-risk asset exposure
    high_risk_val = sum(r['current_value'] for r in rows if r['risk_score'] >= 9)
    high_risk_pct = (high_risk_val / total * 100) if total else 0
    if high_risk_pct > 30:
        warns.append({
            'type': 'high_risk_exposure',
            'severity': 'high' if high_risk_pct > 50 else 'medium',
            'message': f"{high_risk_pct:.1f}% of the portfolio is in very-high-risk assets (F&O / crypto).",
        })
    return warns


def _recommendations(score: dict, warnings: list[dict], sector_count: int) -> list[str]:
    recs = []
    if score['score'] >= 70:
        recs.append('Reduce overall risk exposure — current portfolio score is in the Highly Aggressive band.')
    if any(w['type'] == 'concentration' for w in warnings):
        recs.append('Trim single-stock positions above 25% of the portfolio to manage idiosyncratic risk.')
    if any(w['type'] == 'sector_concentration' for w in warnings):
        recs.append('Diversify across sectors — current allocation is heavily skewed to one industry.')
    if sector_count < 4:
        recs.append('Add positions across at least 5 distinct sectors to improve diversification.')
    if any(w['type'] == 'high_risk_exposure' for w in warnings):
        recs.append('Cap F&O / crypto exposure at 25–30% of total portfolio value.')
    if not recs:
        recs.append('Portfolio composition is balanced. Continue periodic rebalancing every quarter.')
    return recs


def analyze_portfolio(holdings: list, *, currency: str = 'INR') -> dict:
    """
    Run full risk analysis on the provided holdings.

    Returns:
        {
          summary: {...},
          details: {
            holdings:           [...],
            sector_allocation:  [...],
            asset_allocation:   [...],
            risk_heatmap:       [...],
            warnings:           [...],
            recommendations:    [...],
          }
        }
    """
    rows = [_per_holding_metrics(h) for h in _validate_holdings(holdings)]

    invested = sum(r['invested']      for r in rows)
    current  = sum(r['current_value'] for r in rows)
    pnl      = current - invested
    pnl_pct  = (pnl / invested * 100) if invested else 0.0

    sector_alloc = _allocation(rows, 'sector',      current)
    asset_alloc  = _allocation(rows, 'asset_class', current)
    weights      = [r['current_value'] / current * 100 for r in rows] if current else []
    hhi          = _hhi(weights)
    score        = _portfolio_risk_score(rows, current, hhi, len(sector_alloc))
    warnings     = _build_warnings(rows, sector_alloc, current)

    # Per-holding heatmap (top contributors to risk)
    heatmap = sorted([
        {
            'symbol':       r['symbol'],
            'weight_pct':   round((r['current_value'] / current * 100), 2) if current else 0,
            'risk_score':   r['risk_score'],
            'risk_label':   r['risk_label'],
            'pnl_pct':      r['pnl_pct'],
        } for r in rows
    ], key=lambda x: x['weight_pct'] * x['risk_score'], reverse=True)

    top = max(rows, key=lambda r: r['current_value']) if rows else None
    top_conc = round((top['current_value'] / current * 100), 2) if (top and current) else 0

    summary = {
        'currency':              currency,
        'holdings_count':        len(rows),
        'sector_count':          len(sector_alloc),
        'total_invested':        round(invested, 2),
        'total_current_value':   round(current, 2),
        'total_pnl':             round(pnl, 2),
        'total_pnl_pct':         round(pnl_pct, 2),
        'risk_score':            score['score'],
        'risk_grade':            score['grade'],
        'concentration_hhi':     hhi,
        'top_holding_pct':       top_conc,
        'top_holding_symbol':    top['symbol'] if top else None,
        'warnings_count':        len(warnings),
    }

    return {
        'summary': summary,
        'details': {
            'holdings':          rows,
            'sector_allocation': sector_alloc,
            'asset_allocation':  asset_alloc,
            'risk_heatmap':      heatmap,
            'warnings':          warnings,
            'recommendations':   _recommendations(score, warnings, len(sector_alloc)),
        },
    }
