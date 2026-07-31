"""
Financial Context Builder — Phase 1 (Live Data for LLM)

Extracts NSE tickers / index names from a user's message, fetches live quotes
via the existing market_data_gateway, and formats the result into a
[LIVE MARKET DATA] block that is prepended to Claude's system prompt.

No new data sources or DB tables — purely wires together what already exists.
"""

import re
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IST = ZoneInfo('Asia/Kolkata')

# ── Alias map (loaded lazily once) ────────────────────────────────────────────
_ALIAS_MAP: Optional[Dict[str, str]] = None   # lowercase phrase → SYMBOL

# Index keywords → gateway symbol (order matters: longest first for matching)
_INDEX_KEYWORDS: Dict[str, str] = {
    'india vix':  'INDIA VIX',
    'bank nifty': 'BANKNIFTY',
    'banknifty':  'BANKNIFTY',
    'fin nifty':  'FINNIFTY',
    'finnifty':   'FINNIFTY',
    'nifty 50':   'NIFTY',
    'nifty50':    'NIFTY',
    'sensex':     'SENSEX',
    'nifty':      'NIFTY',
    'vix':        'INDIA VIX',
}

# ALL-CAPS words that are common English / finance abbreviations — not tickers
_STOP_WORDS = frozenset({
    'I', 'A', 'AN', 'THE', 'IS', 'IT', 'IN', 'ON', 'AT', 'TO', 'DO',
    'MY', 'ME', 'WE', 'US', 'OR', 'AND', 'BUT', 'FOR', 'NOT', 'OF',
    'BE', 'BY', 'IF', 'NO', 'SO', 'UP', 'OK', 'GO', 'HI', 'AM',
    'EMA', 'RSI', 'SMA', 'PE', 'EPS', 'CEO', 'CFO', 'IPO', 'NSE', 'BSE',
    'FNO', 'OI', 'IV', 'ATM', 'ITM', 'OTM', 'SL', 'TP', 'CE', 'AI',
    'LTP', 'MF', 'NAV', 'ETF', 'FII', 'DII', 'SIP', 'CAGR', 'XIRR',
    'NPS', 'PPF', 'FD', 'RD', 'EMI', 'GST', 'TDS', 'PAN', 'KYC', 'UPI',
    'BUY', 'SELL', 'HOLD', 'YES', 'NO', 'OK', 'USD', 'INR', 'GDP',
    'CPI', 'WPI', 'RBI', 'SEBI', 'IRDAI', 'AMFI', 'CDSL', 'NSDL',
    'NSE', 'BSE', 'MCX', 'NCDEX', 'SGX', 'US', 'UK', 'EU', 'IT',
    'API', 'URL', 'PDF', 'CSV', 'APP', 'UI', 'UX', 'SQL', 'OTP',
})

# Index symbols handled specially via get_index_prices()
_INDEX_SYMBOLS = frozenset({'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'INDIA VIX', 'MIDCAPNIFTY'})


# ── Alias map loader ──────────────────────────────────────────────────────────

def _load_alias_map() -> Dict[str, str]:
    """Load nse_aliases.json once at first call. Thread-safe (GIL)."""
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    try:
        path = os.path.join(
            os.path.dirname(__file__), '..', 'static', 'data', 'nse_aliases.json'
        )
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        _ALIAS_MAP = {k.lower().strip(): v.upper() for k, v in data.items()}
        logger.info(f"financial_context_builder: loaded {len(_ALIAS_MAP)} NSE aliases")
    except Exception as exc:
        logger.warning(f"financial_context_builder: could not load nse_aliases.json: {exc}")
        _ALIAS_MAP = {}
    return _ALIAS_MAP


# ── Ticker extractor ──────────────────────────────────────────────────────────

def extract_tickers(text: str) -> List[str]:
    """Extract NSE ticker symbols from free-form user text.

    Strategy (in priority order):
      1. Index keywords (longest-match; avoids 'nifty' matching inside 'bank nifty')
      2. ALL-CAPS words that look like NSE symbols (2–15 chars, not stop-words)
      3. Company-name aliases from nse_aliases.json (case-insensitive phrase match)

    Returns a deduplicated list of ≤5 symbols, indices first.
    """
    found: List[str] = []
    seen: set = set()
    low = text.lower()

    # 1. Index keywords — sorted longest first to avoid partial matches
    for kw, sym in sorted(_INDEX_KEYWORDS.items(), key=lambda x: len(x[0]), reverse=True):
        if kw in low and sym not in seen:
            found.append(sym)
            seen.add(sym)

    # 2. ALL-CAPS token regex — e.g. RELIANCE, HDFCBANK, TCS
    for word in re.findall(r'\b([A-Z][A-Z0-9&\-]{1,14})\b', text):
        if word in seen:
            continue
        if word in _STOP_WORDS:
            continue
        found.append(word)
        seen.add(word)

    # 3. Alias map — e.g. "reliance" → "RELIANCE", "hdfc bank" → "HDFCBANK"
    alias_map = _load_alias_map()
    # Sort by phrase length descending so multi-word names match before single words
    for phrase in sorted(alias_map.keys(), key=len, reverse=True):
        if alias_map[phrase] in seen:
            continue
        if phrase in low:
            found.append(alias_map[phrase])
            seen.add(alias_map[phrase])

    return found[:5]   # cap at 5 — keeps latency manageable


# ── Per-symbol quote fetcher ──────────────────────────────────────────────────

def _fetch_one(symbol: str, user_id: Optional[int]) -> Tuple[str, Dict]:
    """Fetch LTP + change% + 52w range for one symbol. Silent on failure."""
    data: Dict = {
        'symbol':      symbol,
        'ltp':         0.0,
        'change_pct':  0.0,
        'week52_high': None,
        'week52_low':  None,
        'source':      'unknown',
    }

    # ── Index symbols ──────────────────────────────────────────────────────
    if symbol in _INDEX_SYMBOLS:
        try:
            from services.market_data_gateway import get_index_prices
            result = get_index_prices([symbol], user_id=user_id)
            entry  = result.get(symbol, {})
            if isinstance(entry, dict) and float(entry.get('ltp', 0) or 0) > 0:
                data['ltp']       = round(float(entry['ltp']), 2)
                data['change_pct'] = round(float(entry.get('pct_change', 0) or 0), 2)
                data['source']    = entry.get('source', 'gateway')
        except Exception as exc:
            logger.debug(f"ctx_builder: index {symbol}: {exc}")
        return symbol, data

    # ── Equity symbol — LTP via gateway ───────────────────────────────────
    try:
        from services.market_data_gateway import get_price
        pr = get_price(symbol, user_id=user_id)
        if pr.get('success') and float(pr.get('value', 0) or 0) > 0:
            data['ltp']    = round(float(pr['value']), 2)
            data['source'] = pr.get('source', 'gateway')
    except Exception as exc:
        logger.debug(f"ctx_builder: get_price({symbol}): {exc}")

    # ── Enrich with yfinance fast_info (change%, 52w range) ───────────────
    # fast_info is a low-latency single HTTP call — suitable for a 2.5 s budget
    try:
        import yfinance as yf
        fi = yf.Ticker(f"{symbol}.NS").fast_info
        ltp_yf = float(getattr(fi, 'last_price',     0) or 0)
        prev   = float(getattr(fi, 'previous_close', 0) or 0)
        h52    = float(getattr(fi, 'year_high',      0) or 0)
        l52    = float(getattr(fi, 'year_low',       0) or 0)

        # Use yfinance LTP only if gateway gave nothing
        if data['ltp'] <= 0 and ltp_yf > 0:
            data['ltp']    = round(ltp_yf, 2)
            data['source'] = 'yfinance'

        # Compute change% from previous close when gateway doesn't supply it
        ltp = data['ltp']
        if ltp > 0 and prev > 0 and data['change_pct'] == 0.0:
            data['change_pct'] = round((ltp - prev) / prev * 100, 2)

        if h52 > 0:
            data['week52_high'] = round(h52, 2)
        if l52 > 0:
            data['week52_low'] = round(l52, 2)

    except Exception as exc:
        logger.debug(f"ctx_builder: yfinance fast_info({symbol}): {exc}")

    return symbol, data


# ── Parallel fetch ────────────────────────────────────────────────────────────

def fetch_live_context(
    symbols: List[str],
    user_id: Optional[int] = None,
    timeout: float = 2.5,
) -> Dict[str, Dict]:
    """Fetch live quotes for ≤5 symbols in parallel threads within `timeout` seconds.

    Silently omits any symbol that fails or times out.
    Uses ThreadPoolExecutor WITHOUT the `with` statement to avoid the known
    hang-on-shutdown issue with broker SDK calls.
    """
    if not symbols:
        return {}

    results: Dict[str, Dict] = {}
    pool = ThreadPoolExecutor(max_workers=min(len(symbols), 5))
    try:
        futures = {pool.submit(_fetch_one, sym, user_id): sym for sym in symbols}
        deadline = time.monotonic() + timeout
        for fut in as_completed(futures, timeout=timeout):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                sym, data = fut.result(timeout=min(remaining, 0.5))
                if float(data.get('ltp', 0) or 0) > 0:
                    results[sym] = data
            except Exception as exc:
                logger.debug(f"ctx_builder: future result error: {exc}")
    except Exception as exc:
        logger.debug(f"ctx_builder: fetch_live_context outer: {exc}")
    finally:
        pool.shutdown(wait=False)   # non-blocking — avoids blocking on hung threads

    return results


# ── Prompt block formatter ────────────────────────────────────────────────────

def build_context_block(quotes: Dict[str, Dict]) -> str:
    """Format fetched quotes into a compact [LIVE MARKET DATA] block.

    The block is prepended to the LLM system prompt so Claude grounds
    its answer in today's actual figures rather than training memory.
    """
    if not quotes:
        return ''

    now_ist = datetime.now(_IST).strftime('%H:%M IST, %d %b %Y')
    lines = [f'[LIVE MARKET DATA — as of {now_ist}]']

    for sym, d in quotes.items():
        ltp      = float(d.get('ltp', 0) or 0)
        chg_pct  = float(d.get('change_pct', 0) or 0)
        h52      = d.get('week52_high')
        l52      = d.get('week52_low')

        if ltp <= 0:
            continue

        arrow   = '▲' if chg_pct > 0 else ('▼' if chg_pct < 0 else '—')
        chg_str = f"{arrow} {abs(chg_pct):.2f}%"
        line    = f"  • {sym}: ₹{ltp:,.2f}  {chg_str}"

        if h52 and l52:
            line += f"  |  52w: ₹{l52:,.2f} – ₹{h52:,.2f}"

        lines.append(line)

    if len(lines) <= 1:
        return ''   # no successful quotes

    lines.append(
        '[Use these live figures in your response. '
        'Do not reference training-data prices. '
        'Always mention that data is live/delayed from the market.]'
    )
    return '\n'.join(lines)


# ── Public entry point ────────────────────────────────────────────────────────

def get_live_context_for_message(
    message: str,
    user_id: Optional[int] = None,
) -> str:
    """Extract tickers → fetch live data → return a formatted context block.

    Returns an empty string when:
      - no recognisable tickers found in the message
      - all data fetches fail / time out
    Always safe to call; never raises.
    """
    try:
        tickers = extract_tickers(message)
        if not tickers:
            return ''
        logger.debug(f"ctx_builder: tickers found: {tickers}")
        quotes = fetch_live_context(tickers, user_id=user_id)
        block  = build_context_block(quotes)
        if block:
            logger.info(
                f"ctx_builder: injecting live data for "
                f"{list(quotes.keys())} into LLM prompt"
            )
        return block
    except Exception as exc:
        logger.warning(f"get_live_context_for_message failed: {exc}")
        return ''
