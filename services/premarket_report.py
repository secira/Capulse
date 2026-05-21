"""
Pre-Market Levels Report — Telegram + in-app.

Generates 4 actionable intraday levels per index (NIFTY 50, BANK NIFTY,
FIN NIFTY, SENSEX) using prev-day OHLC pivots and the morning option-chain
OI walls:

    Long Breakout  — trigger = max(today open, R1, nearest CE wall > spot)
                     target  = next CE wall (or R2)
    Long Reversal  — trigger = nearest PE wall < spot (or S1)
                     target  = pivot (or R1)
    Short Breakdown — trigger = min(today open, S1, nearest PE wall < spot)
                      target  = next PE wall (or S2)
    Short Reversal — trigger = nearest CE wall > spot (or R1)
                     target  = pivot (or S1)

Each line is gated on a 5-min candle close beyond the trigger.

Falls back gracefully when the option chain is unavailable pre-market —
pivot-only levels are produced and OI-derived rows show "—".
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

# (engine_index_code, telegram_label, short_label)
INDICES: list[tuple[str, str, str]] = [
    ('NIFTY',     'NIFTY 50',   'NIFTY'),
    ('BANKNIFTY', 'BANK NIFTY', 'BANK NIFTY'),
    ('FINNIFTY',  'FIN NIFTY',  'FIN NIFTY'),
    ('SENSEX',    'SENSEX',     'SENSEX'),
]


# ───────────────────────── helpers ─────────────────────────────────────

def _classic_pivots(prev_h: float, prev_l: float, prev_c: float) -> dict:
    """Classic floor-trader pivots from prev-day H/L/C."""
    if not (prev_h and prev_l and prev_c):
        return {}
    p = (prev_h + prev_l + prev_c) / 3.0
    r1 = 2 * p - prev_l
    s1 = 2 * p - prev_h
    r2 = p + (prev_h - prev_l)
    s2 = p - (prev_h - prev_l)
    r3 = prev_h + 2 * (p - prev_l)
    s3 = prev_l - 2 * (prev_h - p)
    return {'P': p, 'R1': r1, 'R2': r2, 'R3': r3, 'S1': s1, 'S2': s2, 'S3': s3}


def _fetch_prev_day_ohlc(yf_ticker: str) -> dict:
    """Fetch yesterday's daily H/L/C via yfinance. Returns {} on failure."""
    try:
        import yfinance as yf
        hist = yf.Ticker(yf_ticker).history(period='7d', interval='1d')
        if hist is None or hist.empty:
            return {}
        # Use the most recent fully-closed session (yesterday).
        # If today's row exists (mid-session call), drop it.
        last = hist.iloc[-1]
        return {
            'high':  float(last['High']),
            'low':   float(last['Low']),
            'close': float(last['Close']),
        }
    except Exception as e:
        logger.warning(f"premarket: yfinance prev-day fetch failed for {yf_ticker}: {e}")
        return {}


def _round_strike(value: float, interval: int) -> int:
    if not value or not interval:
        return 0
    return int(round(value / interval) * interval)


def _nearest_above(values: list[float], reference: float) -> float | None:
    above = [v for v in values if v is not None and v > reference]
    return min(above) if above else None


def _nearest_below(values: list[float], reference: float) -> float | None:
    below = [v for v in values if v is not None and v < reference]
    return max(below) if below else None


# ───────────────────────── per-index computation ───────────────────────

def _build_index_levels(index_code: str) -> dict:
    """Return a structured dict of 4 levels for one index. Never raises."""
    from services.dhan_service import get_index_quotes, get_option_chain
    from services.nifty_options_engine import NiftyOptionsEngine, INDEX_CONFIGS

    cfg = INDEX_CONFIGS.get(index_code, {})
    yf_ticker = cfg.get('yf_ticker')
    strike_interval = int(cfg.get('strike_interval') or 50)

    out: dict[str, Any] = {
        'index_code':   index_code,
        'spot':         None,
        'open':         None,
        'prev_close':   None,
        'prev_high':    None,
        'prev_low':     None,
        'pivots':       {},
        'ce_walls':     [],   # sorted ascending
        'pe_walls':     [],   # sorted ascending
        'has_oi':       False,
        'long_breakout':  {'trigger': None, 'target': None, 'source': '—'},
        'long_reversal':  {'trigger': None, 'target': None, 'source': '—'},
        'short_breakdown':{'trigger': None, 'target': None, 'source': '—'},
        'short_reversal': {'trigger': None, 'target': None, 'source': '—'},
    }

    # 1) Spot + today's open + prev close from Dhan
    try:
        quotes = get_index_quotes()
    except Exception as e:
        logger.warning(f"premarket: get_index_quotes failed: {e}")
        quotes = {}
    q = quotes.get(index_code, {}) if isinstance(quotes, dict) else {}
    ltp        = float(q.get('ltp')   or 0)
    today_open = float(q.get('open')  or 0)
    prev_close = float(q.get('close') or 0)
    out['spot']       = ltp or None
    out['open']       = today_open or None
    out['prev_close'] = prev_close or None

    # 2) Prev-day H/L/C via yfinance (Dhan only exposes prev close)
    prev_ohlc = _fetch_prev_day_ohlc(yf_ticker) if yf_ticker else {}
    if prev_ohlc:
        out['prev_high']  = prev_ohlc['high']
        out['prev_low']   = prev_ohlc['low']
        # Prefer yfinance close only if Dhan didn't provide one
        if not prev_close:
            prev_close = prev_ohlc['close']
            out['prev_close'] = prev_close

    # 3) Pivots
    pivots = _classic_pivots(out['prev_high'] or 0, out['prev_low'] or 0, prev_close or 0)
    out['pivots'] = pivots

    # 4) OI walls (top 3 CE = resistance, top 3 PE = support)
    spot_for_ref = ltp or today_open or prev_close or 0
    try:
        chain_data = get_option_chain(index_code) if spot_for_ref else {}
        chain = (chain_data or {}).get('option_chain') or {}
        if chain and spot_for_ref:
            engine = NiftyOptionsEngine(index=index_code)
            atm = _round_strike(spot_for_ref, engine.strike_interval)
            oi  = engine._compute_oi_metrics(chain, atm, spot_for_ref)
            top_ce = [int(x['strike']) for x in (oi.get('top_ce_strikes') or [])[:5]]
            top_pe = [int(x['strike']) for x in (oi.get('top_pe_strikes') or [])[:5]]
            out['ce_walls'] = sorted(set(top_ce))
            out['pe_walls'] = sorted(set(top_pe))
            out['has_oi']   = bool(top_ce or top_pe)
            if not out['spot']:
                out['spot'] = float(chain_data.get('spot_price') or 0) or None
                spot_for_ref = out['spot'] or spot_for_ref
    except Exception as e:
        logger.warning(f"premarket: option chain unavailable for {index_code}: {e}")

    # 5) Build the 4 levels
    ref = out['spot'] or today_open or prev_close or 0
    if not ref:
        return out

    ce_walls = out['ce_walls']
    pe_walls = out['pe_walls']
    P  = pivots.get('P');  R1 = pivots.get('R1'); R2 = pivots.get('R2')
    S1 = pivots.get('S1'); S2 = pivots.get('S2')

    # Long Breakout
    ce_above   = _nearest_above(ce_walls, ref)
    candidates = [v for v in [today_open, R1, ce_above] if v]
    if candidates:
        trig = max(candidates)
        next_ce = _nearest_above(ce_walls, trig)
        target  = next_ce if next_ce else R2
        if target and target > trig:
            out['long_breakout'] = {
                'trigger': round(trig, 2),
                'target':  round(target, 2),
                'source':  'CE OI wall' if (next_ce or ce_above) else 'Pivot R2',
            }

    # Long Reversal — bounce off support
    pe_below = _nearest_below(pe_walls, ref)
    trig = pe_below if pe_below else S1
    if trig:
        target = P if (P and P > trig) else R1
        if target and target > trig:
            out['long_reversal'] = {
                'trigger': round(trig, 2),
                'target':  round(target, 2),
                'source':  'PE OI wall' if pe_below else 'Pivot S1',
            }

    # Short Breakdown
    pe_below2 = _nearest_below(pe_walls, ref)
    candidates = [v for v in [today_open, S1, pe_below2] if v]
    if candidates:
        trig = min(candidates)
        next_pe = _nearest_below(pe_walls, trig)
        target  = next_pe if next_pe else S2
        if target and target < trig:
            out['short_breakdown'] = {
                'trigger': round(trig, 2),
                'target':  round(target, 2),
                'source':  'PE OI wall' if (next_pe or pe_below2) else 'Pivot S2',
            }

    # Short Reversal — rejection from resistance
    ce_above2 = _nearest_above(ce_walls, ref)
    trig = ce_above2 if ce_above2 else R1
    if trig:
        target = P if (P and P < trig) else S1
        if target and target < trig:
            out['short_reversal'] = {
                'trigger': round(trig, 2),
                'target':  round(target, 2),
                'source':  'CE OI wall' if ce_above2 else 'Pivot R1',
            }

    return out


# ───────────────────────── public report builder ───────────────────────

def build_premarket_report() -> dict:
    """Return {generated_at_ist, indices: [ {label, short_label, levels...}, ... ]}"""
    items = []
    for code, label, short in INDICES:
        try:
            data = _build_index_levels(code)
        except Exception as e:
            logger.error(f"premarket: build_index_levels failed for {code}: {e}", exc_info=True)
            data = {'index_code': code}
        data['label'] = label
        data['short_label'] = short
        items.append(data)
    return {
        'generated_at_ist': datetime.now(IST).strftime('%d %b %Y · %I:%M %p IST'),
        'indices': items,
    }


# ───────────────────────── Telegram formatter ──────────────────────────

def _fmt_price(v) -> str:
    if v is None:
        return '—'
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return '—'


def _format_index_block(item: dict) -> str:
    short = item.get('short_label') or item.get('index_code', '')
    lb = item['long_breakout']  if 'long_breakout'  in item else {}
    lr = item['long_reversal']  if 'long_reversal'  in item else {}
    sb = item['short_breakdown'] if 'short_breakdown' in item else {}
    sr = item['short_reversal'] if 'short_reversal' in item else {}

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"*{short}*",
        "",
        "*Long*",
        f"1. {short} can give up move when it *breaks {_fmt_price(lb.get('trigger'))}* "
        f"for the target of *{_fmt_price(lb.get('target'))}*",
        f"2. {short} can give up move when it *takes reversal from {_fmt_price(lr.get('trigger'))}* "
        f"for the target of *{_fmt_price(lr.get('target'))}*",
        "",
        "*Short*",
        f"1. {short} can give down move when it *breaks {_fmt_price(sb.get('trigger'))}* "
        f"for the support of *{_fmt_price(sb.get('target'))}*",
        f"2. {short} can give down move when it *takes reversal {_fmt_price(sr.get('trigger'))}* "
        f"for the support of *{_fmt_price(sr.get('target'))}*",
        "",
        "_(If 5 mins candle close on above/below levels)_",
    ]
    return "\n".join(lines)


def format_premarket_report_telegram(report: dict) -> str:
    header = [
        "🌅 *Pre-Market Levels Report*",
        f"_{report.get('generated_at_ist','')}_",
        "",
    ]
    blocks = [_format_index_block(item) for item in report.get('indices', [])]
    footer = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "_Educational purpose. Not for trading._",
    ]
    return "\n".join(header) + "\n".join(blocks) + "\n".join(footer)


# ───────────────────────── public entrypoint ───────────────────────────

def send_premarket_report() -> bool:
    """Build and broadcast the pre-market report to Telegram."""
    from services.messaging_service import send_telegram_message
    try:
        report = build_premarket_report()
        body = format_premarket_report_telegram(report)
        ok = send_telegram_message(body, parse_mode='Markdown')
        if ok:
            logger.info(f"📨 Pre-market report sent ({len(report.get('indices', []))} indices)")
        else:
            logger.warning("Pre-market report Telegram send returned False")
        return bool(ok)
    except Exception as e:
        logger.error(f"send_premarket_report failed: {e}", exc_info=True)
        return False
