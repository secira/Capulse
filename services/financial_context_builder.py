"""
Financial Context Builder — Global Market Data for LLM

Extracts tickers / index names from a user's message (Indian NSE + US equities
+ global indices + crypto), fetches live quotes, and formats the result into a
[LIVE MARKET DATA] block that is prepended to Claude's system prompt.

Design constraints (enforced):
- All alias / index matching uses word-boundary regex — never raw substring search.
- Matched character spans are tracked so overlapping matches are impossible
  (e.g. "BANK NIFTY" consumes the text; "NIFTY" is not also extracted).
- A module-level bounded ThreadPoolExecutor (max 4 workers) is shared across
  requests, preventing thread accumulation under load.
- Indian stocks are fetched via the market_data_gateway (NSE/Dhan/TrueData) with
  yfinance .NS fallback; US/global/crypto symbols use yfinance directly with no
  .NS suffix, routed via _classify_symbol().
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

# Global symbol → market type, populated when global_aliases.json is loaded.
# Keys are yfinance symbols (e.g. "AAPL", "^GSPC", "BTC-USD").
# Values: "us_equity" | "global_index" | "crypto"
_GLOBAL_SYMBOLS: Dict[str, str] = {}

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
                # 8 workers: up to 5 price futures + 5 news futures can run
                # concurrently without queueing behind each other.
                _POOL = ThreadPoolExecutor(
                    max_workers=8,
                    thread_name_prefix='ctx_builder_',
                )
    return _POOL


# ── Alias map loader ──────────────────────────────────────────────────────────

def _load_alias_patterns() -> List[Tuple[re.Pattern, str]]:
    """Load and compile word-boundary patterns from nse_aliases.json and
    global_aliases.json (once, lazily, under a lock).

    Global aliases are merged *after* NSE aliases so Indian names always win
    when there is a conflict.  _GLOBAL_SYMBOLS is populated from the
    market_types section of global_aliases.json.
    """
    global _ALIAS_MAP, _ALIAS_PATTERNS, _GLOBAL_SYMBOLS
    if _ALIAS_PATTERNS is not None:
        return _ALIAS_PATTERNS
    with _ALIAS_LOCK:
        if _ALIAS_PATTERNS is not None:
            return _ALIAS_PATTERNS
        base = os.path.join(os.path.dirname(__file__), '..', 'static', 'data')

        # ── 1. NSE aliases ────────────────────────────────────────────────
        raw: Dict[str, str] = {}
        try:
            with open(os.path.join(base, 'nse_aliases.json'), 'r', encoding='utf-8') as fh:
                nse_data = json.load(fh)
            raw.update({k.lower().strip(): v.upper() for k, v in nse_data.items()})
            logger.info(f"financial_context_builder: loaded {len(raw)} NSE aliases")
        except Exception as exc:
            logger.warning(f"financial_context_builder: could not load nse_aliases.json: {exc}")

        # ── 2. Global aliases (US equities, indices, crypto) ──────────────
        try:
            with open(os.path.join(base, 'global_aliases.json'), 'r', encoding='utf-8') as fh:
                global_data = json.load(fh)
            # Populate _GLOBAL_SYMBOLS from market_types section
            for sym, mtype in global_data.get('market_types', {}).items():
                _GLOBAL_SYMBOLS[sym] = mtype
            # Merge aliases — NSE names take precedence if already present
            for phrase, sym in global_data.get('aliases', {}).items():
                key = phrase.lower().strip()
                if key not in raw:          # NSE alias wins on conflict
                    raw[key] = sym          # keep symbol as-is (may contain ^, -)
            logger.info(
                f"financial_context_builder: loaded {len(_GLOBAL_SYMBOLS)} global symbols"
            )
        except Exception as exc:
            logger.warning(f"financial_context_builder: could not load global_aliases.json: {exc}")

        _ALIAS_MAP = raw
        # Sort longest phrase first so multi-word names match before parts
        _ALIAS_PATTERNS = [
            (re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE), sym)
            for phrase, sym in sorted(raw.items(), key=lambda x: len(x[0]), reverse=True)
        ]
        logger.info(
            f"financial_context_builder: compiled {len(_ALIAS_PATTERNS)} total alias patterns"
        )
    return _ALIAS_PATTERNS


def _classify_symbol(symbol: str) -> str:
    """Return the market type for a symbol to determine fetch routing.

    Returns one of:
      'indian_index'  — handled by get_index_prices() (NSE indices)
      'us_equity'     — yfinance raw symbol, no .NS suffix
      'crypto'        — yfinance raw symbol (e.g. BTC-USD)
      'global_index'  — yfinance raw symbol (e.g. ^GSPC, ^N225)
      'indian'        — get_price() + yfinance with .NS suffix (default)
    """
    if symbol in _INDEX_SYMBOLS:
        return 'indian_index'
    # Check global symbol registry (populated from global_aliases.json)
    market = _GLOBAL_SYMBOLS.get(symbol)
    if market:
        return market
    # Heuristic fallbacks for symbols typed directly (e.g. bare "^GSPC" or "BTC-USD")
    if symbol.startswith('^'):
        return 'global_index'
    if symbol.endswith('-USD') or symbol.endswith('-USDT'):
        return 'crypto'
    return 'indian'


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
    """Fetch LTP + change% + 52w range for one symbol. Silent on failure.

    Routing:
      - Indian NSE indices  → get_index_prices()
      - Indian equities     → get_price() then yfinance with .NS suffix
      - US/global/crypto    → yfinance directly (no .NS suffix)
    """
    # Ensure alias patterns (and _GLOBAL_SYMBOLS) are loaded before classifying
    _load_alias_patterns()
    market = _classify_symbol(symbol)

    data: Dict = {
        'symbol':      symbol,
        'ltp':         0.0,
        'change_pct':  0.0,
        'week52_high': None,
        'week52_low':  None,
        'source':      'unknown',
        'market':      market,
    }

    # ── Indian NSE indices ─────────────────────────────────────────────────
    if market == 'indian_index':
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

    # ── US equities / global indices / crypto → yfinance direct ───────────
    if market in ('us_equity', 'global_index', 'crypto'):
        try:
            import yfinance as yf
            fi     = yf.Ticker(symbol).fast_info
            ltp_yf = float(getattr(fi, 'last_price',     0) or 0)
            prev   = float(getattr(fi, 'previous_close', 0) or 0)
            h52    = float(getattr(fi, 'year_high',      0) or 0)
            l52    = float(getattr(fi, 'year_low',       0) or 0)

            if ltp_yf > 0:
                data['ltp']    = round(ltp_yf, 2)
                data['source'] = 'yfinance'
            if ltp_yf > 0 and prev > 0:
                data['change_pct'] = round((ltp_yf - prev) / prev * 100, 2)
            if h52 > 0:
                data['week52_high'] = round(h52, 2)
            if l52 > 0:
                data['week52_low']  = round(l52, 2)
        except Exception as exc:
            logger.debug(f"ctx_builder: yfinance({symbol}): {exc}")
        return symbol, data

    # ── Indian equities — LTP via gateway, enriched by yfinance .NS ───────
    try:
        from services.market_data_gateway import get_price
        pr = get_price(symbol, user_id=user_id)
        if pr.get('success') and float(pr.get('value', 0) or 0) > 0:
            data['ltp']    = round(float(pr['value']), 2)
            data['source'] = pr.get('source', 'gateway')
    except Exception as exc:
        logger.debug(f"ctx_builder: get_price({symbol}): {exc}")

    try:
        import yfinance as yf
        fi     = yf.Ticker(f"{symbol}.NS").fast_info
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
            data['week52_low']  = round(l52, 2)
    except Exception as exc:
        logger.debug(f"ctx_builder: yfinance fast_info({symbol}.NS): {exc}")

    # ── Global fallback: if NSE paths both failed, try raw symbol ──────────
    # Handles valid US/global tickers typed directly (e.g. MELI, MRNA, SHOP)
    # that are not yet in global_aliases.json.  If yfinance returns a price
    # for the raw symbol, reclassify as us_equity and use $ currency.
    if data['ltp'] <= 0:
        try:
            import yfinance as yf
            fi_raw = yf.Ticker(symbol).fast_info
            ltp_raw = float(getattr(fi_raw, 'last_price', 0) or 0)
            if ltp_raw > 0:
                prev_raw = float(getattr(fi_raw, 'previous_close', 0) or 0)
                h52_raw  = float(getattr(fi_raw, 'year_high',      0) or 0)
                l52_raw  = float(getattr(fi_raw, 'year_low',       0) or 0)
                data['ltp']    = round(ltp_raw, 2)
                data['source'] = 'yfinance_global'
                data['market'] = 'us_equity'   # reclassify: dollar, US flag
                if ltp_raw > 0 and prev_raw > 0:
                    data['change_pct'] = round((ltp_raw - prev_raw) / prev_raw * 100, 2)
                if h52_raw > 0:
                    data['week52_high'] = round(h52_raw, 2)
                if l52_raw > 0:
                    data['week52_low']  = round(l52_raw, 2)
                logger.debug(f"ctx_builder: global fallback hit for {symbol}")
        except Exception as exc:
            logger.debug(f"ctx_builder: global fallback({symbol}): {exc}")

    return symbol, data


# ── News fetch helper ─────────────────────────────────────────────────────────

def _fetch_news_safe(symbol: str) -> Tuple[str, List[Dict]]:
    """Fetch news headlines for one symbol via news_service. Always returns a
    (symbol, list) tuple — never raises."""
    try:
        _load_alias_patterns()          # ensure _GLOBAL_SYMBOLS is populated
        market = _classify_symbol(symbol)
        from services.news_service import get_stock_news
        items = get_stock_news(symbol, market=market, max_items=3)
        return symbol, items
    except Exception as exc:
        logger.debug(f"ctx_builder: _fetch_news_safe({symbol}): {exc}")
        return symbol, []


# ── Parallel fetch via shared bounded pool ────────────────────────────────────

def fetch_live_context(
    symbols: List[str],
    user_id: Optional[int] = None,
    timeout: float = 2.5,
) -> Dict[str, Dict]:
    """Fetch live quotes + recent news for ≤5 symbols using the shared executor.

    Submits one price future and one news future per symbol simultaneously.
    The pool (8 workers) handles both concurrently within the total `timeout`
    budget.  Running futures cannot be interrupted; queued ones are cancelled
    after the deadline to prevent backlog accumulation.
    """
    if not symbols:
        return {}

    pool = _get_pool()
    # Tag each future with its kind so we can route results correctly
    price_futures: Dict[Future, str] = {
        pool.submit(_fetch_one, sym, user_id): sym for sym in symbols
    }
    news_futures: Dict[Future, str] = {
        pool.submit(_fetch_news_safe, sym): sym for sym in symbols
    }
    all_futures: Dict[Future, Tuple[str, str]] = {
        **{f: ('price', sym) for f, sym in price_futures.items()},
        **{f: ('news',  sym) for f, sym in news_futures.items()},
    }

    results: Dict[str, Dict] = {}         # sym → price data
    news_map: Dict[str, List[Dict]] = {}  # sym → headline list
    deadline = time.monotonic() + timeout

    try:
        for fut in as_completed(all_futures, timeout=timeout):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            kind, sym = all_futures[fut]
            try:
                value = fut.result(timeout=min(remaining, 0.3))
                if kind == 'price':
                    _, data = value
                    if float(data.get('ltp', 0) or 0) > 0:
                        results[sym] = data
                else:
                    _, items = value
                    if items:
                        news_map[sym] = items
            except Exception as exc:
                logger.debug(f"ctx_builder: future result error ({kind}, {sym}): {exc}")
    except Exception as exc:
        logger.debug(f"ctx_builder: as_completed timeout: {exc}")
    finally:
        for fut in all_futures:
            if not fut.done():
                fut.cancel()

    # Attach news to price results (news for symbols without a price is dropped)
    for sym in results:
        results[sym]['news'] = news_map.get(sym, [])

    return results


# ── Prompt block formatter ────────────────────────────────────────────────────

def _market_display(market: str) -> Tuple[str, str]:
    """Return (currency_prefix, flag_emoji) for a market type."""
    if market in ('indian', 'indian_index'):
        return '₹', '🇮🇳'
    if market == 'us_equity':
        return '$', '🇺🇸'
    if market == 'crypto':
        return '$', '₿'
    if market == 'global_index':
        return '',  '🌍'
    return '₹', '🇮🇳'   # safe default


def build_context_block(quotes: Dict[str, Dict]) -> str:
    """Format fetched quotes + news into a compact [LIVE MARKET DATA] block.

    Prices are shown with the correct currency symbol and a market flag:
      🇮🇳 Indian equity / index  →  ₹
      🇺🇸 US equity / ETF        →  $
      ₿  Crypto                  →  $
      🌍 Global index             →  (no currency — point value)

    When news headlines are present for a symbol, up to 3 are appended
    beneath the price line as compact bullet points with source and age.
    """
    if not quotes:
        return ''

    now_ist = datetime.now(_IST).strftime('%H:%M IST, %d %b %Y')
    lines = [f'[LIVE MARKET DATA — as of {now_ist}]']
    has_news = False

    for sym, d in quotes.items():
        ltp     = float(d.get('ltp', 0) or 0)
        chg_pct = float(d.get('change_pct', 0) or 0)
        h52     = d.get('week52_high')
        l52     = d.get('week52_low')
        market  = d.get('market', 'indian')
        news    = d.get('news') or []

        if ltp <= 0:
            continue

        ccy, flag = _market_display(market)
        arrow     = '▲' if chg_pct > 0 else ('▼' if chg_pct < 0 else '—')
        chg_str   = f"{arrow} {abs(chg_pct):.2f}%"
        # Use comma-formatted with 2 dp; for crypto show up to 6 dp if < $1
        if market == 'crypto' and ltp < 1:
            price_str = f"{ccy}{ltp:.6f}"
        else:
            price_str = f"{ccy}{ltp:,.2f}"
        line = f"  • {flag} {sym}: {price_str}  {chg_str}"
        if h52 and l52:
            line += f"  |  52w: {ccy}{l52:,.2f} – {ccy}{h52:,.2f}"
        lines.append(line)

        # ── News headlines (compact, indented under the price line) ───────
        if news:
            has_news = True
            lines.append(f"    📰 Recent news:")
            for item in news[:3]:
                title = item.get('title', '').strip()
                if not title:
                    continue
                pub = item.get('publisher', '').strip()
                age = item.get('age_str', '').strip()
                meta = ''
                if pub and age:
                    meta = f" [{pub} · {age}]"
                elif pub:
                    meta = f" [{pub}]"
                elif age:
                    meta = f" [{age}]"
                lines.append(f"       – {title}{meta}")

    if len(lines) <= 1:
        return ''

    # Footer instruction — tell Claude to use news for narrative context
    footer_parts = [
        'Use these live figures in your response.',
        'Do not reference training-data prices.',
    ]
    if has_news:
        footer_parts.append(
            'Use the news headlines to explain recent price moves when relevant. '
            'Do not fabricate events not listed here.'
        )
    footer_parts.append('Always note that figures are live/delayed market data.')
    lines.append('[' + ' '.join(footer_parts) + ']')
    return '\n'.join(lines)


# ── Fundamentals fetch ────────────────────────────────────────────────────────

def _rec_label(rec_mean: Optional[float], rec_key: str = '') -> Optional[str]:
    """Map yfinance recommendationMean (1=Strong Buy … 5=Sell) to a display label."""
    if rec_mean is not None:
        if rec_mean <= 1.5:  return 'Strong Buy'
        if rec_mean <= 2.5:  return 'Buy'
        if rec_mean <= 3.5:  return 'Hold'
        if rec_mean <= 4.5:  return 'Underperform'
        return 'Sell'
    return rec_key.replace('_', ' ').title() or None


def _fmt_market_cap(mc: float, market: str) -> str:
    """Format raw market-cap bytes → human-readable string (₹ Cr for India, $B for US)."""
    if market == 'indian':
        cr = mc / 1e7
        if cr >= 1e5:
            return f"₹{cr/1e5:.2f}L Cr"
        if cr >= 1e3:
            return f"₹{cr/1e3:.1f}K Cr"
        return f"₹{cr:,.0f} Cr"
    # US / global / crypto
    b = mc / 1e9
    if b >= 1e3:
        return f"${b/1e3:.2f}T"
    return f"${b:,.1f}B"


def _fetch_fundamentals(symbol: str, market: str) -> Tuple[str, Dict]:
    """Fetch key fundamentals for one symbol via yfinance.Ticker.info.

    Returns (symbol, dict) — dict is {} on any error.
    Fields: market_cap, trailing_pe, forward_pe, trailing_eps, dividend_yield,
    week52_high, week52_low, target_price, recommendation, recommendation_mean,
    analyst_count, company_name, sector, industry.
    """
    try:
        import yfinance as yf
        yf_sym = f"{symbol}.NS" if market == 'indian' else symbol
        info = yf.Ticker(yf_sym).info
        if not info:
            return symbol, {}

        rec_mean = info.get('recommendationMean')
        rec_label = _rec_label(rec_mean, info.get('recommendationKey', ''))

        div_raw = info.get('dividendYield')
        div_pct = round(div_raw * 100, 2) if div_raw else None

        mc_raw = info.get('marketCap')
        mc_str = _fmt_market_cap(mc_raw, market) if mc_raw else None

        result = {
            'symbol':             symbol,
            'market':             market,
            'company_name':       info.get('longName') or info.get('shortName', ''),
            'sector':             info.get('sector', ''),
            'industry':           info.get('industry', ''),
            'market_cap':         mc_raw,
            'market_cap_str':     mc_str,
            'trailing_pe':        info.get('trailingPE'),
            'forward_pe':         info.get('forwardPE'),
            'trailing_eps':       info.get('trailingEps'),
            'dividend_yield':     div_pct,
            'week52_high':        info.get('fiftyTwoWeekHigh'),
            'week52_low':         info.get('fiftyTwoWeekLow'),
            'target_price':       info.get('targetMeanPrice'),
            'recommendation':     rec_label,
            'recommendation_mean': rec_mean,
            'analyst_count':      info.get('numberOfAnalystOpinions'),
        }
        return symbol, result
    except Exception as exc:
        logger.debug(f"ctx_builder: _fetch_fundamentals({symbol}): {exc}")
        return symbol, {}


def fetch_fundamentals(
    symbol: str,
    user_id: Optional[int] = None,
    timeout: float = 3.5,
) -> Dict:
    """Fetch fundamentals for a single symbol using the shared bounded pool.

    Returns {} on timeout or error — never raises.
    """
    _load_alias_patterns()
    market = _classify_symbol(symbol)
    pool   = _get_pool()
    fut    = pool.submit(_fetch_fundamentals, symbol, market)
    try:
        _, data = fut.result(timeout=timeout)
        return data
    except Exception as exc:
        logger.debug(f"ctx_builder: fetch_fundamentals({symbol}) error: {exc}")
        if not fut.done():
            fut.cancel()
        return {}


def build_fundamentals_context_block(symbol: str, quote: Dict, fundamentals: Dict) -> str:
    """Format live price + fundamentals into a compact [FUNDAMENTALS] LLM context block."""
    if not fundamentals:
        return ''

    market  = fundamentals.get('market') or quote.get('market', 'indian')
    ccy, flag = _market_display(market)
    ltp     = float(quote.get('ltp', 0) or 0)
    chg_pct = float(quote.get('change_pct', 0) or 0)

    now_ist = datetime.now(_IST).strftime('%H:%M IST, %d %b %Y')
    lines   = [f'[FUNDAMENTALS — {flag} {symbol} as of {now_ist}]']

    if ltp > 0:
        arrow = '▲' if chg_pct > 0 else ('▼' if chg_pct < 0 else '—')
        price_str = f"{ccy}{ltp:.6f}" if (market == 'crypto' and ltp < 1) else f"{ccy}{ltp:,.2f}"
        lines.append(f"  Current price: {price_str}  {arrow} {abs(chg_pct):.2f}%")

    if fundamentals.get('market_cap_str'):
        lines.append(f"  Market Cap: {fundamentals['market_cap_str']}")

    pe_parts = []
    if fundamentals.get('trailing_pe') is not None:
        pe_parts.append(f"P/E (TTM): {fundamentals['trailing_pe']:.1f}x")
    if fundamentals.get('forward_pe') is not None:
        pe_parts.append(f"Fwd P/E: {fundamentals['forward_pe']:.1f}x")
    if fundamentals.get('trailing_eps') is not None:
        pe_parts.append(f"EPS: {ccy}{fundamentals['trailing_eps']:.2f}")
    if pe_parts:
        lines.append(f"  {' | '.join(pe_parts)}")

    if fundamentals.get('dividend_yield'):
        lines.append(f"  Dividend Yield: {fundamentals['dividend_yield']:.2f}%")

    h52, l52 = fundamentals.get('week52_high'), fundamentals.get('week52_low')
    if h52 and l52:
        lines.append(f"  52w Range: {ccy}{l52:,.2f} – {ccy}{h52:,.2f}")

    rec, n_an = fundamentals.get('recommendation'), fundamentals.get('analyst_count')
    target    = fundamentals.get('target_price')
    ap = []
    if rec:
        ap.append(f"Consensus: {rec}" + (f" ({n_an} analysts)" if n_an else ""))
    if target and ltp > 0:
        upside = (target - ltp) / ltp * 100
        sign   = '↑' if upside > 0 else '↓'
        ap.append(f"Target: {ccy}{target:,.2f} ({sign}{abs(upside):.1f}%)")
    if ap:
        lines.append(f"  {' | '.join(ap)}")

    if len(lines) <= 1:
        return ''

    lines.append(
        '[Use these fundamentals in your commentary. '
        'Data from Yahoo Finance — treat as indicative, not audited financials.]'
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
