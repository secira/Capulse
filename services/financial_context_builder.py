"""
Financial Context Builder — Phase 1 (Live Data for LLM)

Extracts NSE tickers / index names from a user's message, fetches live quotes
via the existing market_data_gateway, and formats the result into a
[LIVE MARKET DATA] block that is prepended to Claude's system prompt.

Design constraints (enforced):
- All alias / index matching uses word-boundary regex — never raw substring search.
- Matched character spans are tracked so overlapping matches are impossible
  (e.g. "BANK NIFTY" consumes the text; "NIFTY" is not also extracted).
- A module-level bounded ThreadPoolExecutor (max 4 workers) is shared across
  requests, preventing thread accumulation under load.
"""

import re
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IST = ZoneInfo('Asia/Kolkata')

# ── Alias map (loaded lazily once) ────────────────────────────────────────────
_ALIAS_MAP: Optional[Dict[str, str]] = None          # lowercase phrase → SYMBOL
_ALIAS_PATTERNS: Optional[List[Tuple[re.Pattern, str]]] = None  # compiled once
_ALIAS_LOCK = threading.Lock()

# Index keywords → gateway symbol.
# Sorted longest-first at load time so "BANK NIFTY" is matched before "NIFTY".
_INDEX_KEYWORDS_RAW: Dict[str, str] = {
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

# Pre-compiled word-boundary patterns for each index keyword (longest first)
_INDEX_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE), sym)
    for kw, sym in sorted(
        _INDEX_KEYWORDS_RAW.items(), key=lambda x: len(x[0]), reverse=True
    )
]

# ALL-CAPS words that are common English / finance abbreviations — not tickers
_STOP_WORDS = frozenset({
    'I', 'A', 'AN', 'THE', 'IS', 'IT', 'IN', 'ON', 'AT', 'TO', 'DO',
    'MY', 'ME', 'WE', 'US', 'OR', 'AND', 'BUT', 'FOR', 'NOT', 'OF',
    'BE', 'BY', 'IF', 'NO', 'SO', 'UP', 'OK', 'GO', 'HI', 'AM',
    'EMA', 'RSI', 'SMA', 'PE', 'EPS', 'CEO', 'CFO', 'IPO',
    'NSE', 'BSE', 'FNO', 'OI', 'IV', 'ATM', 'ITM', 'OTM', 'SL', 'TP',
    'CE', 'AI', 'LTP', 'MF', 'NAV', 'ETF', 'FII', 'DII', 'SIP',
    'CAGR', 'XIRR', 'NPS', 'PPF', 'FD', 'RD', 'EMI', 'GST', 'TDS',
    'PAN', 'KYC', 'UPI', 'BUY', 'SELL', 'HOLD', 'YES', 'USD', 'INR',
    'GDP', 'CPI', 'WPI', 'RBI', 'SEBI', 'IRDAI', 'AMFI', 'CDSL', 'NSDL',
    'MCX', 'NCDEX', 'SGX', 'US', 'UK', 'EU', 'IT', 'API', 'URL',
    'PDF', 'CSV', 'APP', 'UI', 'UX', 'SQL', 'OTP',
    # Index component words — prevent partial re-match after index consumed
    'NIFTY', 'BANK', 'FIN', 'INDIA', 'VIX',
})

# Index symbols handled via get_index_prices()
_INDEX_SYMBOLS = frozenset({'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'INDIA VIX'})


# ── Bounded shared executor ───────────────────────────────────────────────────
# A single pool capped at 4 workers prevents thread accumulation across
# concurrent requests.  We never use the `with` statement (which calls
# shutdown(wait=True) and blocks on hung broker threads).

_POOL: Optional[ThreadPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix='ctx_builder_',
                )
    return _POOL


# ── Alias map loader ──────────────────────────────────────────────────────────

def _load_alias_patterns() -> List[Tuple[re.Pattern, str]]:
    """Load and compile word-boundary patterns for nse_aliases.json (once)."""
    global _ALIAS_MAP, _ALIAS_PATTERNS
    if _ALIAS_PATTERNS is not None:
        return _ALIAS_PATTERNS
    with _ALIAS_LOCK:
        if _ALIAS_PATTERNS is not None:
            return _ALIAS_PATTERNS
        try:
            path = os.path.join(
                os.path.dirname(__file__), '..', 'static', 'data', 'nse_aliases.json'
            )
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            # Build lowercase phrase → symbol, then compile patterns
            raw = {k.lower().strip(): v.upper() for k, v in data.items()}
            _ALIAS_MAP = raw
            # Sort longest phrase first so multi-word names match before parts
            _ALIAS_PATTERNS = [
                (re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE), sym)
                for phrase, sym in sorted(raw.items(), key=lambda x: len(x[0]), reverse=True)
            ]
            logger.info(
                f"financial_context_builder: compiled {len(_ALIAS_PATTERNS)} alias patterns"
            )
        except Exception as exc:
            logger.warning(f"financial_context_builder: could not load nse_aliases.json: {exc}")
            _ALIAS_MAP = {}
            _ALIAS_PATTERNS = []
    return _ALIAS_PATTERNS


# ── Ticker extractor ──────────────────────────────────────────────────────────

def extract_tickers(text: str) -> List[str]:
    """Extract NSE ticker symbols from free-form user text.

    Steps (each step consumes matched spans so later steps cannot overlap):
      1. Index keywords  — longest first, word-boundary (e.g. BANK NIFTY → BANKNIFTY)
      2. Alias map       — longest first, word-boundary (e.g. "hdfc bank" → HDFCBANK,
                           "sbi" → SBIN); runs BEFORE ALL-CAPS scan so that alias
                           remapping wins over bare token extraction.
      3. ALL-CAPS tokens — only from text not yet consumed by steps 1–2.

    Returns ≤5 symbols.

    Examples:
      "recommend a mutual fund"  → []            (not RECLTD)
      "BANK NIFTY outlook"       → ['BANKNIFTY'] (not also NIFTY or BANK)
      "HDFC Bank quarterly"      → ['HDFCBANK']  (alias wins over bare HDFC)
      "SBI loan growth"          → ['SBIN']      (alias remaps SBI → SBIN)
      "Compare TCS and Infosys"  → ['TCS', 'INFY']
    """
    found: List[str] = []
    seen: set = set()
    # Track consumed character positions so no span is matched twice
    consumed: set = set()

    def _try_match(pattern: re.Pattern, sym: str) -> bool:
        """Try to match pattern; consume span and record sym if successful."""
        if sym in seen or len(found) >= 5:
            return False
        m = pattern.search(text)
        if not m:
            return False
        span = set(range(m.start(), m.end()))
        if span & consumed:
            return False
        found.append(sym)
        seen.add(sym)
        consumed.update(span)
        return True

    # ── 1. Index keywords (longest first, word-boundary) ──────────────────
    for pattern, sym in _INDEX_PATTERNS:
        _try_match(pattern, sym)

    # ── 2. Alias map (longest first, word-boundary) ───────────────────────
    # Run BEFORE ALL-CAPS so multi-word aliases (e.g. "hdfc bank") and
    # acronym remappings (e.g. "sbi" → SBIN) take priority over raw tokens.
    for pattern, sym in _load_alias_patterns():
        if len(found) >= 5:
            break
        _try_match(pattern, sym)

    # ── 3. ALL-CAPS tokens from text not already consumed by steps 1–2 ────
    clean = ''.join(c if i not in consumed else ' ' for i, c in enumerate(text))
    for m in re.finditer(r'\b([A-Z][A-Z0-9&\-]{1,14})\b', clean):
        if len(found) >= 5:
            break
        word = m.group(1)
        if word in seen or word in _STOP_WORDS:
            continue
        found.append(word)
        seen.add(word)
        consumed.update(range(m.start(), m.end()))

    return found


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
                data['ltp']        = round(float(entry['ltp']), 2)
                data['change_pct'] = round(float(entry.get('pct_change', 0) or 0), 2)
                data['source']     = entry.get('source', 'gateway')
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
    try:
        import yfinance as yf
        fi = yf.Ticker(f"{symbol}.NS").fast_info
        ltp_yf = float(getattr(fi, 'last_price',     0) or 0)
        prev   = float(getattr(fi, 'previous_close', 0) or 0)
        h52    = float(getattr(fi, 'year_high',      0) or 0)
        l52    = float(getattr(fi, 'year_low',       0) or 0)

        if data['ltp'] <= 0 and ltp_yf > 0:
            data['ltp']    = round(ltp_yf, 2)
            data['source'] = 'yfinance'

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


# ── Parallel fetch via shared bounded pool ────────────────────────────────────

def fetch_live_context(
    symbols: List[str],
    user_id: Optional[int] = None,
    timeout: float = 2.5,
) -> Dict[str, Dict]:
    """Fetch live quotes for ≤5 symbols using the shared bounded executor.

    Submits futures to the module-level pool (max 4 workers).  After `timeout`
    seconds, we stop waiting and cancel any futures that are still only queued
    (running futures cannot be interrupted in CPython, but the pool cap prevents
    unlimited thread accumulation).
    """
    if not symbols:
        return {}

    pool = _get_pool()
    future_to_sym: Dict[Future, str] = {
        pool.submit(_fetch_one, sym, user_id): sym
        for sym in symbols
    }

    results: Dict[str, Dict] = {}
    deadline = time.monotonic() + timeout

    try:
        for fut in as_completed(future_to_sym, timeout=timeout):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                sym, data = fut.result(timeout=min(remaining, 0.3))
                if float(data.get('ltp', 0) or 0) > 0:
                    results[sym] = data
            except Exception as exc:
                logger.debug(f"ctx_builder: future result error: {exc}")
    except Exception as exc:
        logger.debug(f"ctx_builder: as_completed timeout: {exc}")
    finally:
        # Cancel any futures still waiting in the queue (no effect on running ones,
        # but prevents queued work from executing if the pool is backlogged)
        for fut in future_to_sym:
            if not fut.done():
                fut.cancel()

    return results


# ── Prompt block formatter ────────────────────────────────────────────────────

def build_context_block(quotes: Dict[str, Dict]) -> str:
    """Format fetched quotes into a compact [LIVE MARKET DATA] block."""
    if not quotes:
        return ''

    now_ist = datetime.now(_IST).strftime('%H:%M IST, %d %b %Y')
    lines = [f'[LIVE MARKET DATA — as of {now_ist}]']

    for sym, d in quotes.items():
        ltp     = float(d.get('ltp', 0) or 0)
        chg_pct = float(d.get('change_pct', 0) or 0)
        h52     = d.get('week52_high')
        l52     = d.get('week52_low')

        if ltp <= 0:
            continue

        arrow   = '▲' if chg_pct > 0 else ('▼' if chg_pct < 0 else '—')
        chg_str = f"{arrow} {abs(chg_pct):.2f}%"
        line    = f"  • {sym}: ₹{ltp:,.2f}  {chg_str}"
        if h52 and l52:
            line += f"  |  52w: ₹{l52:,.2f} – ₹{h52:,.2f}"
        lines.append(line)

    if len(lines) <= 1:
        return ''

    lines.append(
        '[Use these live figures in your response. '
        'Do not reference training-data prices. '
        'Always note that figures are live/delayed market data.]'
    )
    return '\n'.join(lines)


# ── Public entry point ────────────────────────────────────────────────────────

def get_live_context_for_message(
    message: str,
    user_id: Optional[int] = None,
) -> str:
    """Extract tickers → fetch live data → return formatted context block.

    Returns empty string when no tickers are found or all fetches fail.
    Never raises.
    """
    try:
        tickers = extract_tickers(message)
        if not tickers:
            return ''
        logger.debug(f"ctx_builder: tickers={tickers}")
        quotes = fetch_live_context(tickers, user_id=user_id)
        block  = build_context_block(quotes)
        if block:
            logger.info(f"ctx_builder: live data injected for {list(quotes.keys())}")
        return block
    except Exception as exc:
        logger.warning(f"get_live_context_for_message failed: {exc}")
        return ''
