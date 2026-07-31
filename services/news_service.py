"""
News Service — live headlines for any stock symbol via yfinance.

Public API:
    get_stock_news(symbol, market, max_items, timeout) → List[{title, publisher, age_str}]

Design:
- Primary source: yfinance.Ticker(symbol).news (works for NSE, US, crypto).
- NSE symbols get a .NS suffix; Indian indices are skipped (no per-index news).
- Always returns [] on any error or when no news is found — never raises.
- A thread-level hard timeout is enforced by the caller (via ThreadPoolExecutor
  future.result(timeout=…)); this module does not spawn its own threads.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)


def _age_str(publish_ts: int) -> str:
    """Convert a Unix timestamp to a human-readable relative age string.

    Examples: '5m ago', '3h ago', '2d ago', '1w ago'.
    Returns '' if the timestamp is missing or invalid.
    """
    try:
        if not publish_ts:
            return ''
        now = datetime.now(timezone.utc)
        pub = datetime.fromtimestamp(int(publish_ts), tz=timezone.utc)
        delta = now - pub
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return ''
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{max(minutes, 1)}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            return f"{days}d ago"
        weeks = days // 7
        return f"{weeks}w ago"
    except Exception:
        return ''


def _yf_symbol(symbol: str, market: str) -> str:
    """Return the yfinance symbol to use for news lookup."""
    if market in ('us_equity', 'global_index', 'crypto'):
        return symbol
    # Indian equity — yfinance news uses .NS suffix
    return f"{symbol}.NS"


def get_stock_news(
    symbol: str,
    market: str = 'indian',
    max_items: int = 3,
) -> List[Dict]:
    """Fetch up to max_items recent news headlines for a stock symbol.

    Args:
        symbol:    Ticker symbol (e.g. "RELIANCE", "AAPL", "BTC-USD", "^GSPC").
        market:    Market classification from _classify_symbol() — determines
                   whether to use a .NS suffix or the raw symbol.
        max_items: Maximum number of headlines to return (default 3).

    Returns:
        A list of dicts, each with keys:
            title      (str)  — headline text
            publisher  (str)  — news source name
            age_str    (str)  — human-readable age, e.g. "3h ago"

        Returns [] on any error, timeout, or when no news is available.
        Never raises.
    """
    # Indian indices (NIFTY, BANKNIFTY, etc.) don't have stock-specific news
    if market == 'indian_index':
        return []

    try:
        import yfinance as yf
        yf_sym = _yf_symbol(symbol, market)
        ticker = yf.Ticker(yf_sym)

        # .news can be None, an empty list, or a list of dicts
        news_raw = ticker.news
        if not news_raw:
            return []

        results: List[Dict] = []
        for item in news_raw:
            if len(results) >= max_items:
                break

            # yfinance ≥ 0.2.x changed the schema; handle both old and new
            # Old: {'title', 'publisher', 'link', 'providerPublishTime', ...}
            # New (content-wrapped): {'content': {'title', 'provider': {'displayName'}, ...}}
            content = item.get('content') if isinstance(item, dict) else None
            if content and isinstance(content, dict):
                title     = (content.get('title') or '').strip()
                publisher = (
                    (content.get('provider') or {}).get('displayName')
                    or content.get('publisher', '')
                ).strip()
                ts = content.get('pubDate') or content.get('providerPublishTime') or 0
                # pubDate may be an ISO string in newer versions
                if isinstance(ts, str):
                    try:
                        from datetime import datetime as _dt
                        ts = int(_dt.fromisoformat(ts.replace('Z', '+00:00')).timestamp())
                    except Exception:
                        ts = 0
            else:
                title     = (item.get('title', '') or '').strip()
                publisher = (item.get('publisher', '') or '').strip()
                ts        = item.get('providerPublishTime', 0) or 0

            if not title:
                continue

            results.append({
                'title':     title,
                'publisher': publisher,
                'age_str':   _age_str(int(ts)) if ts else '',
            })

        return results

    except Exception as exc:
        logger.debug(f"news_service: get_stock_news({symbol}): {exc}")
        return []
