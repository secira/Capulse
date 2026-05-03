"""
Stateless Behavioural Analyzer for the B2B Partner API.

Detects the same family of biases as services/behaviour_engine.py
(overtrading, revenge trading, loss aversion, tilt, profit-booking bias,
poor timing, position-sizing inconsistency) but operates purely on the
trade list submitted in the request — no DB lookups, no user context.

Input shape (per trade):
    {
      symbol, side (BUY|SELL), qty, price,
      entry_time (ISO8601), exit_time? (ISO8601), pnl? (number),
      segment? (EQ|FNO|MCX), strategy?
    }
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

# Detection thresholds — mirror behaviour_engine.py constants
_OVERTRADE_PER_DAY      = 5      # >5 trades in any 4h window = overtrading
_OVERTRADE_WINDOW_HOURS = 4
_REVENGE_WINDOW_MINS    = 30     # new trade within 30m of a loss with ≥ size
_LOSS_AVERSION_RATIO    = 1.5    # avg loss > 1.5× avg win = loss-averse
_TILT_SIZE_INCREASE     = 0.5    # +50% size after a loss = tilt
_CONCENTRATION_LIMIT    = 0.4    # >40% of trades on one symbol


def _parse_time(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%d-%m-%Y %H:%M'):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _validate_trades(raw: list) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError('trades must be a list')
    if not raw:
        raise ValueError('trades list is empty')
    if len(raw) > 100:
        raise ValueError(f'Maximum 100 trades per request (got {len(raw)})')

    out = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError(f'trades[{i}] must be an object')
        symbol = (t.get('symbol') or '').strip().upper()
        side   = (t.get('side') or '').strip().upper()
        if not symbol:
            raise ValueError(f'trades[{i}].symbol is required')
        if side not in ('BUY', 'SELL'):
            raise ValueError(f'trades[{i}].side must be BUY or SELL')
        try:
            qty   = float(t.get('qty') or t.get('quantity') or 0)
            price = float(t.get('price') or 0)
        except (TypeError, ValueError):
            raise ValueError(f'trades[{i}] qty/price must be numeric')
        if qty <= 0 or price <= 0:
            raise ValueError(f'trades[{i}] qty and price must be > 0')

        entry = _parse_time(t.get('entry_time'))
        if entry is None:
            raise ValueError(f'trades[{i}].entry_time is required (ISO 8601)')
        exit_ = _parse_time(t.get('exit_time'))

        pnl = t.get('pnl')
        try:
            pnl = float(pnl) if pnl not in (None, '') else None
        except (TypeError, ValueError):
            pnl = None

        out.append({
            'symbol':     symbol,
            'side':       side,
            'qty':        qty,
            'price':      round(price, 4),
            'value':      round(qty * price, 2),
            'entry_time': entry,
            'exit_time':  exit_,
            'pnl':        pnl,
            'segment':    (t.get('segment') or 'EQ').strip().upper(),
            'strategy':   (t.get('strategy') or '').strip() or None,
        })
    out.sort(key=lambda x: x['entry_time'])
    return out


# ── Detectors ───────────────────────────────────────────────────────────────

def _detect_overtrading(trades: list[dict]) -> dict:
    by_day: dict = defaultdict(list)
    for t in trades:
        by_day[t['entry_time'].date()].append(t['entry_time'])

    flagged_days = 0
    worst = 0
    for day, times in by_day.items():
        times.sort()
        for i, anchor in enumerate(times):
            window_end = anchor + timedelta(hours=_OVERTRADE_WINDOW_HOURS)
            in_window = sum(1 for tt in times[i:] if tt <= window_end)
            if in_window > _OVERTRADE_PER_DAY:
                flagged_days += 1
                worst = max(worst, in_window)
                break
    detected = flagged_days > 0
    return {
        'pattern':   'overtrading',
        'detected':  detected,
        'severity':  'high' if worst >= 10 else ('medium' if detected else 'low'),
        'evidence':  f'{flagged_days} day(s) with >{_OVERTRADE_PER_DAY} trades in {_OVERTRADE_WINDOW_HOURS}h (peak: {worst}).' if detected else 'Healthy trade frequency.',
        'metric':    {'flagged_days': flagged_days, 'peak_trades_in_window': worst},
    }


def _detect_revenge_trading(trades: list[dict]) -> dict:
    flagged = 0
    for i in range(1, len(trades)):
        prev = trades[i - 1]
        if (prev.get('pnl') or 0) >= 0:
            continue
        gap_min = (trades[i]['entry_time'] - prev['entry_time']).total_seconds() / 60.0
        if 0 <= gap_min <= _REVENGE_WINDOW_MINS and trades[i]['value'] >= prev['value']:
            flagged += 1
    detected = flagged > 0
    return {
        'pattern':  'revenge_trading',
        'detected': detected,
        'severity': 'high' if flagged >= 3 else ('medium' if detected else 'low'),
        'evidence': f'{flagged} trade(s) entered within {_REVENGE_WINDOW_MINS}m of a loss with equal or larger size.' if detected else 'No revenge-trading pattern observed.',
        'metric':   {'count': flagged},
    }


def _detect_loss_aversion(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get('pnl') is not None]
    wins   = [t['pnl'] for t in closed if t['pnl'] > 0]
    losses = [-t['pnl'] for t in closed if t['pnl'] < 0]
    if not wins or not losses:
        return {'pattern': 'loss_aversion', 'detected': False, 'severity': 'low',
                'evidence': 'Insufficient closed trades to evaluate.', 'metric': {}}
    avg_win  = statistics.mean(wins)
    avg_loss = statistics.mean(losses)
    ratio = (avg_loss / avg_win) if avg_win else 0
    detected = ratio > _LOSS_AVERSION_RATIO
    return {
        'pattern':  'loss_aversion',
        'detected': detected,
        'severity': 'high' if ratio > 2.5 else ('medium' if detected else 'low'),
        'evidence': f'Average loss is {ratio:.2f}× average win — losses are being held longer than winners.' if detected else 'Win/loss sizes are balanced.',
        'metric':   {'avg_win': round(avg_win, 2), 'avg_loss': round(avg_loss, 2), 'loss_to_win_ratio': round(ratio, 2)},
    }


def _detect_tilt(trades: list[dict]) -> dict:
    flagged = 0
    for i in range(1, len(trades)):
        prev = trades[i - 1]
        if (prev.get('pnl') or 0) >= 0:
            continue
        if trades[i]['value'] > prev['value'] * (1 + _TILT_SIZE_INCREASE):
            flagged += 1
    detected = flagged > 0
    return {
        'pattern':  'tilt',
        'detected': detected,
        'severity': 'high' if flagged >= 3 else ('medium' if detected else 'low'),
        'evidence': f'{flagged} occasion(s) where position size jumped >{int(_TILT_SIZE_INCREASE*100)}% after a loss.' if detected else 'No tilt-driven sizing detected.',
        'metric':   {'count': flagged},
    }


def _detect_profit_booking_bias(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get('pnl') is not None and t.get('exit_time')]
    if len(closed) < 5:
        return {'pattern': 'profit_booking_bias', 'detected': False, 'severity': 'low',
                'evidence': 'Insufficient closed trades to evaluate holding times.', 'metric': {}}
    win_holds  = [(t['exit_time'] - t['entry_time']).total_seconds() / 3600 for t in closed if t['pnl'] > 0]
    loss_holds = [(t['exit_time'] - t['entry_time']).total_seconds() / 3600 for t in closed if t['pnl'] < 0]
    if not win_holds or not loss_holds:
        return {'pattern': 'profit_booking_bias', 'detected': False, 'severity': 'low',
                'evidence': 'Need both winners and losers to compare hold times.', 'metric': {}}
    avg_w = statistics.mean(win_holds)
    avg_l = statistics.mean(loss_holds)
    detected = avg_l > avg_w * 1.5
    return {
        'pattern':  'profit_booking_bias',
        'detected': detected,
        'severity': 'high' if avg_l > avg_w * 3 else ('medium' if detected else 'low'),
        'evidence': f'Losers held {avg_l:.1f}h on average vs {avg_w:.1f}h for winners — booking profits too early.' if detected else 'Winner/loser hold-times are balanced.',
        'metric':   {'avg_winner_hours': round(avg_w, 2), 'avg_loser_hours': round(avg_l, 2)},
    }


def _detect_poor_timing(trades: list[dict]) -> dict:
    """Flag if >40% of trades happen in the first or last 15m of the trading day."""
    risky = 0
    for t in trades:
        h, m = t['entry_time'].hour, t['entry_time'].minute
        mins = h * 60 + m
        # Open: 9:15–9:30, Close: 15:15–15:30 (IST)
        if (555 <= mins <= 570) or (915 <= mins <= 930):
            risky += 1
    pct = (risky / len(trades) * 100) if trades else 0
    detected = pct > 40
    return {
        'pattern':  'poor_timing',
        'detected': detected,
        'severity': 'high' if pct > 60 else ('medium' if detected else 'low'),
        'evidence': f'{pct:.0f}% of trades happen in the first/last 15 minutes — high-volatility windows.' if detected else 'Trade timing is well-distributed across the session.',
        'metric':   {'risky_window_pct': round(pct, 1), 'trades_in_risky_window': risky},
    }


def _detect_position_sizing_inconsistency(trades: list[dict]) -> dict:
    if len(trades) < 5:
        return {'pattern': 'position_sizing_inconsistency', 'detected': False, 'severity': 'low',
                'evidence': 'Need at least 5 trades to evaluate sizing.', 'metric': {}}
    sizes = [t['value'] for t in trades]
    mean = statistics.mean(sizes)
    cv = (statistics.pstdev(sizes) / mean) if mean else 0
    detected = cv > 0.75
    return {
        'pattern':  'position_sizing_inconsistency',
        'detected': detected,
        'severity': 'high' if cv > 1.2 else ('medium' if detected else 'low'),
        'evidence': f'Position-size coefficient of variation is {cv:.2f} — sizing is highly inconsistent.' if detected else 'Position sizing is consistent across trades.',
        'metric':   {'coefficient_of_variation': round(cv, 2), 'avg_size': round(mean, 2)},
    }


def _detect_concentration(trades: list[dict]) -> dict:
    counts = Counter(t['symbol'] for t in trades)
    top_sym, top_n = counts.most_common(1)[0]
    pct = top_n / len(trades) * 100
    detected = (top_n / len(trades)) > _CONCENTRATION_LIMIT
    return {
        'pattern':  'symbol_concentration',
        'detected': detected,
        'severity': 'high' if pct > 60 else ('medium' if detected else 'low'),
        'evidence': f'{pct:.0f}% of all trades are on {top_sym} — over-reliance on a single symbol.' if detected else 'Trades are well-distributed across symbols.',
        'metric':   {'top_symbol': top_sym, 'top_symbol_share_pct': round(pct, 1)},
    }


# ── Summary metrics ─────────────────────────────────────────────────────────

def _trade_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get('pnl') is not None]
    wins   = [t['pnl'] for t in closed if t['pnl'] > 0]
    losses = [t['pnl'] for t in closed if t['pnl'] < 0]
    total_pnl = sum(t['pnl'] for t in closed)
    win_rate  = (len(wins) / len(closed) * 100) if closed else 0
    avg_win   = statistics.mean(wins)            if wins   else 0
    avg_loss  = statistics.mean(losses)          if losses else 0
    gross_win   = sum(wins)
    gross_loss  = abs(sum(losses)) or 1e-9
    profit_factor = gross_win / gross_loss if losses else (float('inf') if wins else 0)

    by_segment = Counter(t['segment'] for t in trades)
    by_symbol  = Counter(t['symbol']  for t in trades)
    by_hour    = Counter(t['entry_time'].hour for t in trades)

    return {
        'total_trades':   len(trades),
        'closed_trades':  len(closed),
        'wins':           len(wins),
        'losses':         len(losses),
        'win_rate_pct':   round(win_rate, 2),
        'avg_win':        round(avg_win, 2),
        'avg_loss':       round(avg_loss, 2),
        'total_pnl':      round(total_pnl, 2),
        'profit_factor':  round(profit_factor, 2) if profit_factor != float('inf') else None,
        'segment_breakdown': dict(by_segment),
        'top_5_symbols':  by_symbol.most_common(5),
        'hourly_distribution': dict(sorted(by_hour.items())),
    }


def _discipline_score(patterns: list[dict], stats: dict) -> dict:
    """0–100, higher = more disciplined. Subtract penalties for each detected bias."""
    penalty = 0
    weights = {'high': 18, 'medium': 10, 'low': 0}
    for p in patterns:
        if p['detected']:
            penalty += weights.get(p['severity'], 8)
    # Reward for healthy win rate + profit factor
    bonus = 0
    if stats['win_rate_pct'] >= 50:
        bonus += 5
    if (stats['profit_factor'] or 0) >= 1.5:
        bonus += 5

    score = max(0, min(100, 100 - penalty + bonus))
    if   score >= 80: grade = 'Excellent'
    elif score >= 65: grade = 'Good'
    elif score >= 45: grade = 'Fair'
    elif score >= 25: grade = 'Poor'
    else:             grade = 'Critical'
    return {'score': score, 'grade': grade, 'penalty_points': penalty, 'bonus_points': bonus}


def _recommendations(patterns: list[dict], stats: dict) -> list[str]:
    recs = []
    detected = {p['pattern']: p for p in patterns if p['detected']}
    if 'overtrading' in detected:
        recs.append('Cap daily trades at 5 to reduce overtrading and decision fatigue.')
    if 'revenge_trading' in detected:
        recs.append('Enforce a 30-minute cool-down after any losing trade before re-entering.')
    if 'tilt' in detected:
        recs.append('Use fixed position sizing — never increase size after a loss.')
    if 'loss_aversion' in detected:
        recs.append('Set hard stop-losses and respect them; avoid averaging down losing trades.')
    if 'profit_booking_bias' in detected:
        recs.append('Use trailing stops on winners and rule-based exits to let profits run.')
    if 'poor_timing' in detected:
        recs.append('Avoid the first and last 15 minutes of the session — wait for stable price action.')
    if 'position_sizing_inconsistency' in detected:
        recs.append('Adopt a fixed-percentage risk model (e.g. 1–2% of capital per trade).')
    if 'symbol_concentration' in detected:
        recs.append('Diversify trade ideas across at least 5 distinct symbols.')
    if not recs:
        recs.append('Trading discipline looks healthy. Maintain your current process and journal regularly.')
    return recs


def analyze_behaviour(trades: list, *, lookback_label: str | None = None) -> dict:
    """
    Run full behavioural analysis on the provided trade list.

    Returns:
        {
          summary: { discipline_score, grade, win_rate_pct, ... },
          details: { patterns: [...], stats: {...}, recommendations: [...] }
        }
    """
    rows = _validate_trades(trades)

    patterns = [
        _detect_overtrading(rows),
        _detect_revenge_trading(rows),
        _detect_loss_aversion(rows),
        _detect_tilt(rows),
        _detect_profit_booking_bias(rows),
        _detect_poor_timing(rows),
        _detect_position_sizing_inconsistency(rows),
        _detect_concentration(rows),
    ]
    stats   = _trade_stats(rows)
    score   = _discipline_score(patterns, stats)
    detected_high = [p for p in patterns if p['detected'] and p['severity'] == 'high']
    top_pattern = detected_high[0]['pattern'] if detected_high else (
        next((p['pattern'] for p in patterns if p['detected']), None)
    )

    summary = {
        'lookback':           lookback_label,
        'discipline_score':   score['score'],
        'discipline_grade':   score['grade'],
        'total_trades':       stats['total_trades'],
        'closed_trades':      stats['closed_trades'],
        'win_rate_pct':       stats['win_rate_pct'],
        'profit_factor':      stats['profit_factor'],
        'total_pnl':          stats['total_pnl'],
        'patterns_detected':  sum(1 for p in patterns if p['detected']),
        'top_concern':        top_pattern,
    }

    return {
        'summary': summary,
        'details': {
            'patterns':        patterns,
            'stats':           stats,
            'recommendations': _recommendations(patterns, stats),
        },
    }
