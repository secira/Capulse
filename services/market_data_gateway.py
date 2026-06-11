"""
Market Data Gateway — single authoritative source for all real-time market data.

Uniform fallback chain applied by every area of the platform
(Market Intelligence, Stock Research, F&O Analysis, Trade Execution):

  1. Admin Broker Pool  — admin-configured broker (Dhan/Zerodha/etc.)
  2. TrueData          — if data_api_plan.plan_type == 'nse_truedata'
  3. System Dhan       — any connected DataApiBroker on the platform
  4. NSEPython         — direct NSE India scrape
  5. yfinance          — universal final fallback

Every function returns a standardised dict with a 'source' key so UI
banners can reflect exactly where the data came from.

AI routing (complementary — not data):
  • Perplexity  → real-time commentary / market search (search_recency='day')
  • OpenAI      → RAG embeddings and research report generation
  • Claude      → portfolio analysis, workflow hub, deep structured reports
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ── Module-level caches ────────────────────────────────────────────────────────
_PRICE_CACHE: Dict[str, tuple] = {}   # key → (value, source, ts)
_OHLCV_CACHE: Dict[str, tuple] = {}   # key → (df, source, ts)

PRICE_TTL = 60    # seconds — live LTP
OHLCV_TTL = 300   # seconds — historical bars

# Canonical source-label constants used by source_badge() and templates
SRC_ADMIN     = "admin_broker"
SRC_TRUEDATA  = "truedata"
SRC_NSE       = "nse"
SRC_YFINANCE  = "yfinance"
SRC_ESTIMATED = "estimated"


def _is_market_open() -> bool:
    """True if NSE is currently live — weekday 09:15–15:30 IST."""
    try:
        from datetime import time as _t, datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        now = _dt.now(_ZI('Asia/Kolkata'))
        if now.weekday() >= 5:
            return False
        t = now.time()
        return _t(9, 15) <= t <= _t(15, 30)
    except Exception:
        return True  # assume open on error


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cache_get(cache: dict, key: str, ttl: int):
    entry = cache.get(key)
    if entry and (time.time() - entry[2]) < ttl:
        return entry[0], entry[1]
    return None, None


def _cache_set(cache: dict, key: str, value, source: str):
    cache[key] = (value, source, time.time())


def _get_admin_plan() -> Tuple[str, Optional[str], Optional[str]]:
    """Return (plan_type, truedata_api_key, truedata_api_secret) from DB."""
    try:
        from app import db
        row = db.session.execute(
            db.text(
                "SELECT plan_type, truedata_api_key, truedata_api_secret "
                "FROM data_api_plan WHERE is_active = true LIMIT 1"
            )
        ).fetchone()
        if row:
            return row[0] or 'user_data', row[1], row[2]
    except Exception as e:
        logger.debug(f"gateway._get_admin_plan: {e}")
    return 'user_data', None, None


def _truedata_ltp(symbol: str, td_key: str) -> float:
    """Fetch a single LTP from TrueData REST API. Returns 0.0 on failure."""
    try:
        import requests
        resp = requests.get(
            'https://api.truedata.in/v1/getltp',
            params={'symbol': symbol},
            headers={'Authorization': f'Bearer {td_key}'},
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            ltp = float(
                data.get('ltp') or
                (data.get('data') or {}).get('ltp', 0) or
                0
            )
            if ltp > 0:
                return ltp
    except Exception as e:
        logger.debug(f"gateway._truedata_ltp({symbol}): {e}")
    return 0.0


def _yfinance_ltp(symbol: str) -> Tuple[float, str]:
    """yfinance fast_info last-traded-price. Returns (price, source_label).

    Always returns SRC_YFINANCE as the canonical source label (prev-close is
    still yfinance data; the calling code can check ltp>0 for freshness).
    """
    try:
        import yfinance as yf
        for ticker_sym in (f"{symbol}.NS", symbol):
            try:
                fi = yf.Ticker(ticker_sym).fast_info
                ltp  = float(getattr(fi, 'last_price',      0) or 0)
                prev = float(getattr(fi, 'previous_close',  0) or 0)
                effective = ltp if ltp > 0 else prev
                if effective > 0:
                    return effective, SRC_YFINANCE   # canonical — never sub-label
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"gateway._yfinance_ltp({symbol}): {e}")
    return 0.0, SRC_ESTIMATED


# ── Public API ────────────────────────────────────────────────────────────────

def get_price(symbol: str, user_id: Optional[int] = None) -> Dict:
    """
    Fetch the last traded price for an NSE symbol.

    Returns::

        {
            "value":         float,    # 0.0 when unavailable
            "source":        str,      # see SRC_* constants
            "source_detail": str,      # optional extra detail (broker name, etc.)
            "success":       bool,
            "cached":        bool,     # True when served from TTL cache
        }
    """
    sym = symbol.upper().strip()
    cache_key = f"price:{sym}:{user_id or 0}"
    cv, cs = _cache_get(_PRICE_CACHE, cache_key, PRICE_TTL)
    if cv is not None:
        return {"value": cv, "source": cs, "success": True, "cached": True}

    # ── 1. Admin Broker Pool ─────────────────────────────────────────────────
    try:
        from services.broker_factory import (
            get_admin_data_brokers,
            get_index_price_with_fallback,
            _underlying_from_symbol,
        )
        underlying = _underlying_from_symbol(sym)
        if underlying:
            # Index symbol — delegate to the battle-tested index price helper
            px, lbl = get_index_price_with_fallback(sym, user_id)
            if px > 0:
                # Normalise to canonical 5-label set
                if lbl.startswith('admin:') or lbl.startswith('user:'):
                    src = SRC_ADMIN
                elif lbl in (SRC_TRUEDATA, SRC_NSE, SRC_YFINANCE, SRC_ESTIMATED):
                    src = lbl
                elif lbl in ('unavailable', 'none', ''):
                    src = SRC_ESTIMATED
                else:
                    src = SRC_ADMIN  # unknown broker label → admin tier
                _cache_set(_PRICE_CACHE, cache_key, px, src)
                return {"value": px, "source": src, "source_detail": lbl, "success": True, "cached": False}
        else:
            # Equity symbol — iterate the admin pool
            for _prio, _btype, bname, broker in get_admin_data_brokers():
                try:
                    if not broker.connect():
                        continue
                    px = float(broker.get_price(sym) or 0)
                    if px > 0:
                        logger.debug(f"gateway: {sym}=₹{px} via admin:{bname}")
                        _cache_set(_PRICE_CACHE, cache_key, px, SRC_ADMIN)
                        return {"value": px, "source": SRC_ADMIN,
                                "source_detail": f"admin:{bname}", "success": True, "cached": False}
                except Exception as _e:
                    logger.debug(f"gateway: admin broker {bname} get_price({sym}): {_e}")
    except Exception as e:
        logger.debug(f"gateway: admin pool error for {sym}: {e}")

    # ── 2. User's own data broker ────────────────────────────────────────────
    if user_id:
        try:
            from services.broker_factory import get_data_broker_for_user
            ub = get_data_broker_for_user(user_id)
            if ub and ub.connect():
                px = float(ub.get_price(sym) or 0)
                if px > 0:
                    bname = getattr(ub, 'BROKER_NAME', 'user_broker')
                    _cache_set(_PRICE_CACHE, cache_key, px, SRC_ADMIN)
                    return {"value": px, "source": SRC_ADMIN,
                            "source_detail": f"user:{bname}", "success": True, "cached": False}
        except Exception as e:
            logger.debug(f"gateway: user broker price({sym}): {e}")

    # ── 3. TrueData (when admin configured plan_type='nse_truedata') ──────────
    plan_type, td_key, _td_secret = _get_admin_plan()
    if plan_type == 'nse_truedata' and td_key:
        px = _truedata_ltp(sym, td_key)
        if px > 0:
            _cache_set(_PRICE_CACHE, cache_key, px, SRC_TRUEDATA)
            return {"value": px, "source": SRC_TRUEDATA, "success": True, "cached": False}

    # ── 4. System Dhan (any connected DataApiBroker) ─────────────────────────
    try:
        from services.dhan_service import get_eq_quote
        dhan_data = get_eq_quote(sym)
        if dhan_data and dhan_data.get("ltp", 0) > 0:
            px = float(dhan_data["ltp"])
            _cache_set(_PRICE_CACHE, cache_key, px, SRC_ADMIN)
            return {"value": px, "source": SRC_ADMIN,
                    "source_detail": "dhan:system", "success": True, "cached": False}
    except Exception as e:
        logger.debug(f"gateway: system Dhan price({sym}): {e}")

    # ── 5. NSEPython ─────────────────────────────────────────────────────────
    try:
        from nsepython import nse_quote
        q = nse_quote(sym)
        if q:
            lp = float(q.get('lastPrice') or 0)
            if lp > 0:
                _cache_set(_PRICE_CACHE, cache_key, lp, SRC_NSE)
                return {"value": lp, "source": SRC_NSE, "success": True, "cached": False}
    except Exception as e:
        logger.debug(f"gateway: NSEPython price({sym}): {e}")

    # ── 6. yfinance ──────────────────────────────────────────────────────────
    px, src = _yfinance_ltp(sym)
    if px > 0:
        _cache_set(_PRICE_CACHE, cache_key, px, src)
        return {"value": px, "source": src, "success": True, "cached": False}

    return {"value": 0.0, "source": SRC_ESTIMATED, "source_detail": "", "success": False, "cached": False}


def get_ohlcv(symbol: str, days: int = 120, user_id: Optional[int] = None) -> Dict:
    """
    Fetch historical OHLCV bars for an NSE equity or index symbol.

    Returns::

        {
            "df":      pd.DataFrame,   # columns: open high low close volume
            "source":  str,
            "success": bool,
        }
    """
    sym = symbol.upper().strip()
    cache_key = f"ohlcv:{sym}:{days}"
    cv, cs = _cache_get(_OHLCV_CACHE, cache_key, OHLCV_TTL)
    if cv is not None:
        return {"df": cv, "source": cs, "success": True, "cached": True}

    # ── 1. Admin Broker Pool (prefer any Dhan entry for equity OHLCV) ────────
    try:
        from services.broker_factory import get_admin_data_brokers
        for _prio, btype, bname, _broker in get_admin_data_brokers():
            if btype.lower() == 'dhan':
                try:
                    from services.dhan_service import get_stock_historical_ohlcv
                    df = get_stock_historical_ohlcv(symbol=sym, days=days)
                    if df is not None and not df.empty and len(df) >= 10:
                        logger.info(f"gateway: OHLCV({sym}) {len(df)} rows via admin:{bname}")
                        _cache_set(_OHLCV_CACHE, cache_key, df, SRC_ADMIN)
                        return {"df": df, "source": SRC_ADMIN, "success": True, "cached": False}
                except Exception as _e:
                    logger.debug(f"gateway: admin Dhan OHLCV({sym}): {_e}")
                break  # only try first Dhan admin broker
    except Exception as e:
        logger.debug(f"gateway: admin pool OHLCV error: {e}")

    # ── 2. System Dhan ────────────────────────────────────────────────────────
    try:
        from services.dhan_service import get_stock_historical_ohlcv
        df = get_stock_historical_ohlcv(symbol=sym, days=days)
        if df is not None and not df.empty and len(df) >= 10:
            logger.info(f"gateway: OHLCV({sym}) {len(df)} rows via system Dhan")
            _cache_set(_OHLCV_CACHE, cache_key, df, SRC_ADMIN)
            return {"df": df, "source": SRC_ADMIN, "source_detail": "dhan:system", "success": True, "cached": False}
    except Exception as e:
        logger.debug(f"gateway: system Dhan OHLCV({sym}): {e}")

    # NOTE — TrueData and NSEPython are intentionally absent from the OHLCV chain.
    # Policy: TrueData exposes only LTP + option chain endpoints (no historical bars).
    # NSEPython provides only current-day quote data, not multi-day OHLCV series.
    # The canonical OHLCV chain is therefore:
    #   Admin Dhan → System Dhan → User Broker → yfinance
    # This is explicitly policy-driven, not an oversight.

    # ── 3. User's own data broker ─────────────────────────────────────────────
    if user_id:
        try:
            from services.broker_factory import get_data_broker_for_user
            ub = get_data_broker_for_user(user_id)
            if ub and hasattr(ub, 'get_historical_ohlcv') and ub.connect():
                df = ub.get_historical_ohlcv(sym, days)
                if df is not None and not df.empty and len(df) >= 10:
                    _cache_set(_OHLCV_CACHE, cache_key, df, SRC_ADMIN)
                    return {"df": df, "source": SRC_ADMIN, "source_detail": f"user:{getattr(ub, 'BROKER_NAME', 'broker')}", "success": True, "cached": False}
        except Exception as e:
            logger.debug(f"gateway: user broker OHLCV({sym}): {e}")

    # ── 4. yfinance ───────────────────────────────────────────────────────────
    try:
        import yfinance as yf
        period = '6mo' if days <= 120 else '1y'
        for ticker_sym in (f"{sym}.NS", f"{sym}.BO"):
            try:
                df = yf.Ticker(ticker_sym).history(period=period)
                if df is None or df.empty:
                    continue
                df = df.rename(columns={
                    'Open': 'open', 'High': 'high', 'Low': 'low',
                    'Close': 'close', 'Volume': 'volume',
                })
                df = df[['open', 'high', 'low', 'close', 'volume']].copy()
                df = df.dropna(subset=['close']).tail(days).reset_index(drop=True)
                if len(df) >= 5:
                    logger.info(f"gateway: OHLCV({sym}) {len(df)} rows via yfinance")
                    _cache_set(_OHLCV_CACHE, cache_key, df, SRC_YFINANCE)
                    return {"df": df, "source": SRC_YFINANCE, "success": True, "cached": False}
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"gateway: yfinance OHLCV({sym}): {e}")

    return {"df": pd.DataFrame(), "source": SRC_ESTIMATED, "success": False, "cached": False}


def get_quotes(symbols: List[str], user_id: Optional[int] = None) -> Dict:
    """
    Batch fetch LTP + change_percent for a list of NSE equity symbols.

    Returns::

        {
            "quotes": {
                "RELIANCE": {"price": float, "change_percent": float, "source": str},
                ...
            },
            "source":  str,    # dominant source across all quotes
            "success": bool,
        }
    """
    if not symbols:
        return {"quotes": {}, "source": SRC_ESTIMATED, "success": False}

    result: Dict[str, Dict] = {}
    remaining = list(symbols)

    # ── 1. Admin Pool — prefer Dhan for batch equity quotes ──────────────────
    try:
        from services.broker_factory import get_admin_data_brokers
        for _prio, btype, bname, _broker in get_admin_data_brokers():
            if btype.lower() == 'dhan':
                try:
                    from services.dhan_service import get_security_id, get_nifty50_stock_quotes
                    sec_id_map = {}
                    for s in symbols:
                        sid = get_security_id(s)
                        if sid:
                            sec_id_map[s] = sid
                    if sec_id_map:
                        raw = get_nifty50_stock_quotes(sec_id_map, timeout=5.0)
                        for sym, d in raw.items():
                            ltp   = float(d.get('ltp', 0) or 0)
                            close = float(d.get('close', 0) or ltp)
                            pchg  = float(d.get('pct_change', 0) or 0)
                            if not pchg and ltp and close:
                                pchg = round((ltp - close) / close * 100, 2)
                            if ltp > 0:
                                result[sym] = {
                                    'price': ltp, 'change_percent': pchg, 'source': SRC_ADMIN,
                                }
                        remaining = [s for s in remaining if s not in result]
                        if result:
                            logger.info(f"gateway: batch quotes {len(result)}/{len(symbols)} via admin:{bname}")
                except Exception as _e:
                    logger.debug(f"gateway: admin Dhan batch quotes: {_e}")
                break  # only try first Dhan admin broker
    except Exception as e:
        logger.debug(f"gateway: admin pool quotes error: {e}")

    # ── 2. TrueData (secondary — when admin plan = nse_truedata) ────────────────
    # TrueData is the designated secondary source; it runs before system Dhan
    # so that admin TrueData configuration takes effect everywhere uniformly.
    # TrueData has no batch equity endpoint so we iterate per-symbol.
    remaining = [s for s in symbols if s not in result]
    if remaining:
        plan_type, td_key, _td_secret = _get_admin_plan()
        if plan_type == 'nse_truedata' and td_key:
            for sym in remaining:
                try:
                    px = _truedata_ltp(sym, td_key)
                    if px > 0:
                        result[sym] = {'price': px, 'change_percent': 0.0, 'source': SRC_TRUEDATA}
                except Exception:
                    pass
            logger.debug(f"gateway: TrueData LTP filled {sum(1 for s in remaining if s in result)} / {len(remaining)} remaining")

    # ── 3. System Dhan batch for remainder ───────────────────────────────────
    remaining = [s for s in symbols if s not in result]
    if remaining:
        try:
            from services.dhan_service import get_security_id, get_nifty50_stock_quotes
            sec_id_map = {s: sid for s in remaining if (sid := get_security_id(s))}
            if sec_id_map:
                raw = get_nifty50_stock_quotes(sec_id_map, timeout=5.0)
                for sym, d in raw.items():
                    ltp   = float(d.get('ltp', 0) or 0)
                    close = float(d.get('close', 0) or ltp)
                    pchg  = float(d.get('pct_change', 0) or 0)
                    if not pchg and ltp and close:
                        pchg = round((ltp - close) / close * 100, 2)
                    if ltp > 0:
                        result[sym] = {'price': ltp, 'change_percent': pchg, 'source': SRC_ADMIN}
                logger.debug(f"gateway: system Dhan batch filled {len(raw)} symbols")
        except Exception as e:
            logger.debug(f"gateway: system Dhan batch quotes: {e}")

    # ── 4. NSEPython (per-symbol, for remaining after TrueData) ──────────────
    # NSEPython is per-symbol only — impractical for 50+ symbols but fills gaps.
    remaining = [s for s in symbols if s not in result]
    if remaining:
        try:
            from nsepython import nse_quote
            for sym in remaining:
                try:
                    q = nse_quote(sym)
                    if q:
                        lp = float(q.get('lastPrice') or 0)
                        if lp > 0:
                            result[sym] = {'price': lp, 'change_percent': 0.0, 'source': SRC_NSE}
                except Exception:
                    pass
            logger.debug(f"gateway: NSEPython filled {sum(1 for s in remaining if s in result)} / {len(remaining)} remaining")
        except Exception as e:
            logger.debug(f"gateway: NSEPython batch quotes: {e}")

    # ── 5. yfinance batch — history(period='2d') for reliable closing data ──────
    # Works both during live market hours and after close when fast_info may return 0.
    remaining = [s for s in symbols if s not in result]
    if remaining:
        try:
            import yfinance as yf
            tickers_str = ' '.join(f"{s}.NS" for s in remaining)
            hist = yf.download(tickers_str, period='2d', auto_adjust=True,
                               progress=False, threads=True)
            if hist is not None and not hist.empty:
                close_df = hist.get('Close', hist)
                for sym in remaining:
                    try:
                        sym_ns = f"{sym}.NS"
                        if hasattr(close_df, 'columns') and sym_ns in close_df.columns:
                            closes = close_df[sym_ns].dropna()
                        else:
                            closes = close_df.dropna()
                        if len(closes) >= 2:
                            ltp  = float(closes.iloc[-1])
                            prev = float(closes.iloc[-2])
                            if ltp > 0 and prev > 0:
                                pchg = round((ltp - prev) / prev * 100, 2)
                                result[sym] = {'price': ltp, 'change_percent': pchg,
                                               'source': SRC_YFINANCE}
                        elif len(closes) == 1:
                            ltp = float(closes.iloc[0])
                            if ltp > 0:
                                result[sym] = {'price': ltp, 'change_percent': 0.0,
                                               'source': SRC_YFINANCE}
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"gateway: yfinance batch quotes: {e}")

    # Dominant source: admin_broker > truedata > nse > yfinance > estimated
    _src_priority = {SRC_ADMIN: 4, SRC_TRUEDATA: 3, SRC_NSE: 2, SRC_YFINANCE: 1, SRC_ESTIMATED: 0}
    dominant = max(
        (v.get('source', SRC_ESTIMATED) for v in result.values()),
        key=lambda s: _src_priority.get(s, 0),
        default=SRC_ESTIMATED,
    ) if result else SRC_ESTIMATED
    return {"quotes": result, "source": dominant, "success": bool(result)}


def get_index_prices(
    symbols: Optional[List[str]] = None,
    user_id: Optional[int] = None,
) -> Dict:
    """
    Fetch live prices for NSE index symbols (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, INDIA VIX).

    Returns::

        {
            "NIFTY":     {"ltp": float, "change": float, "pct_change": float, "source": str},
            "BANKNIFTY": {...},
            ...
            "_source":   str,   # dominant source
            "_success":  bool,
        }
    """
    _DEFAULT = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'INDIA VIX']
    want = [s.upper().strip() for s in (symbols or _DEFAULT)]
    result: Dict = {}

    # ── 1. Admin Broker Pool ──────────────────────────────────────────────────
    try:
        from services.broker_factory import get_admin_data_brokers
        for _prio, btype, bname, broker in get_admin_data_brokers():
            if btype.lower() == 'dhan':
                try:
                    from services.dhan_service import get_index_quotes
                    dhan_data = get_index_quotes(user_id)
                    if dhan_data.get('NIFTY', {}).get('ltp', 0) > 0:
                        for sym in want:
                            d = dhan_data.get(sym, {})
                            if d.get('ltp', 0) > 0:
                                ltp   = float(d['ltp'])
                                close = float(d.get('close', 0))
                                chg   = float(d.get('change', (ltp - close) if close else 0))
                                pct   = float(d.get('pct_change', (chg / close * 100) if close else 0))
                                result[sym] = {
                                    'ltp': ltp, 'change': round(chg, 2),
                                    'pct_change': round(pct, 2), 'source': SRC_ADMIN,
                                }
                        if result:
                            logger.info(f"gateway: index prices {list(result.keys())} via admin:{bname}")
                            result['_source']  = SRC_ADMIN
                            result['_success'] = True
                            return result
                except Exception as _e:
                    logger.debug(f"gateway: admin Dhan index prices: {_e}")
            else:
                # Non-Dhan admin broker — per-symbol get_price()
                try:
                    if broker.connect():
                        for sym in want:
                            if sym in result:
                                continue
                            px = float(broker.get_price(sym) or 0)
                            if px > 0:
                                result[sym] = {'ltp': px, 'change': 0.0, 'pct_change': 0.0, 'source': SRC_ADMIN}
                except Exception as _e:
                    logger.debug(f"gateway: admin broker {bname} index prices: {_e}")

    except Exception as e:
        logger.debug(f"gateway: admin pool index prices: {e}")

    # ── 2. TrueData (if configured) ───────────────────────────────────────────
    plan_type, td_key, _td_secret = _get_admin_plan()
    if plan_type == 'nse_truedata' and td_key:
        for sym in want:
            if sym in result:
                continue
            px = _truedata_ltp(sym, td_key)
            if px > 0:
                result[sym] = {'ltp': px, 'change': 0.0, 'pct_change': 0.0, 'source': SRC_TRUEDATA}

    # ── 3. System Dhan ────────────────────────────────────────────────────────
    missing = [s for s in want if s not in result]
    if missing:
        try:
            from services.dhan_service import get_index_quotes
            dhan_data = get_index_quotes(user_id)
            for sym in missing:
                d = dhan_data.get(sym, {})
                if d.get('ltp', 0) > 0:
                    ltp   = float(d['ltp'])
                    close = float(d.get('close', 0))
                    chg   = float(d.get('change', (ltp - close) if close else 0))
                    pct   = float(d.get('pct_change', (chg / close * 100) if close else 0))
                    result[sym] = {
                        'ltp': ltp, 'change': round(chg, 2),
                        'pct_change': round(pct, 2), 'source': SRC_ADMIN,
                        'source_detail': 'dhan:system',
                    }
        except Exception as e:
            logger.debug(f"gateway: system Dhan index prices: {e}")

    # ── 4. yfinance fast_info ─────────────────────────────────────────────────
    # fast_info always supplies both last_price and previous_close so we never
    # get a pct_change of 0.0 due to history(period='2d') returning a single bar
    # when the market is open mid-session (confirmed bug with ^BSESN).
    _yf_map = {
        'NIFTY':     '^NSEI',
        'BANKNIFTY': '^NSEBANK',
        'FINNIFTY':  '^NSEI',   # no direct yfinance ticker; NIFTY as proxy
        'SENSEX':    '^BSESN',
        'INDIA VIX': '^INDIAVIX',
    }
    missing = [s for s in want if s not in result]
    if missing:
        try:
            import yfinance as yf
            for sym in missing:
                yf_sym = _yf_map.get(sym)
                if not yf_sym:
                    continue
                try:
                    fi = yf.Ticker(yf_sym).fast_info
                    ltp  = float(getattr(fi, 'last_price', 0) or 0)
                    prev = float(getattr(fi, 'previous_close', 0) or 0)
                    if ltp > 0:
                        chg = round(ltp - prev, 2) if prev else 0.0
                        pct = round(chg / prev * 100, 2) if prev else 0.0
                        result[sym] = {
                            'ltp': ltp, 'change': chg, 'pct_change': pct,
                            'source': SRC_YFINANCE,
                        }
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"gateway: yfinance index prices: {e}")

    _vals = [v for v in result.values() if isinstance(v, dict)]
    dominant_src = (
        SRC_ADMIN    if any(v.get('source') == SRC_ADMIN    for v in _vals)
        else SRC_TRUEDATA if any(v.get('source') == SRC_TRUEDATA for v in _vals)
        else SRC_YFINANCE if _vals else SRC_ESTIMATED
    )
    result['_source']  = dominant_src
    result['_success'] = bool(result and any(k != '_source' and k != '_success' for k in result))
    return result


def get_option_chain(
    symbol: str,
    expiry: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict:
    """
    Fetch a full option chain for an NSE F&O index or equity.

    Fallback chain:
      1. Admin Broker Pool  — admin-configured broker get_option_chain()
      2. User's own broker  — user's DataApiBroker get_option_chain()
      3. TrueData           — if plan_type='nse_truedata' and key set
      4. System Dhan        — dhan_service.get_option_chain()
      5. NSEPython OI chain — nse_service.get_option_chain_oi()

    Returns::

        {
            "option_chain": dict,   # {strikeKey: {call_ltp, call_oi, put_ltp, put_oi, iv, …}}
            "spot_price":   float,
            "expiry":       str,
            "source":       str,
            "success":      bool,
        }
    """
    sym = symbol.upper().strip()

    def _normalise(chain_list: list, spot: float, src: str, expiry_str: str) -> Dict:
        """Convert a list of raw chain dicts into the canonical gateway return format."""
        try:
            from services.option_chain_builder import chain_to_engine_format
            engine = chain_to_engine_format(chain_list, spot)
        except Exception:
            engine = {}
        return {
            "option_chain": engine,
            "spot_price":   spot,
            "expiry":       expiry_str or "",
            "source":       src,
            "success":      bool(engine),
        }

    # ── 1. Admin Broker Pool ─────────────────────────────────────────────────
    try:
        from services.broker_factory import get_admin_data_brokers
        for _prio, _btype, bname, broker in get_admin_data_brokers():
            try:
                if not broker.connect():
                    continue
                expiry_list = []
                if hasattr(broker, 'get_expiry_list'):
                    expiry_list = broker.get_expiry_list(sym) or []
                use_expiry = expiry or (expiry_list[0] if expiry_list else None)
                chain_raw  = broker.get_option_chain(sym, use_expiry)
                if not chain_raw:
                    continue
                spot = float(chain_raw[0].get('spot', 0)) if chain_raw else 0.0
                if not spot or spot <= 0:
                    spot = float(broker.get_price(sym) or 0)
                if chain_raw and spot > 0:
                    logger.info(f"gateway: option chain {sym} via admin:{bname} ({len(chain_raw)} strikes)")
                    return _normalise(chain_raw, spot, SRC_ADMIN, use_expiry or "")
            except Exception as _e:
                logger.debug(f"gateway: admin broker {bname} option chain({sym}): {_e}")
    except Exception as e:
        logger.debug(f"gateway: admin pool option chain error: {e}")

    # ── 2. User's own brokers (all connected, in preference order) ───────────
    if user_id:
        try:
            from services.broker_factory import get_all_data_brokers_for_user
            for _ub_name, ub in get_all_data_brokers_for_user(user_id):
                try:
                    if not ub.connect():
                        logger.debug(f"gateway: user broker '{_ub_name}' connect failed — trying next")
                        continue
                    expiry_list = []
                    if hasattr(ub, 'get_expiry_list'):
                        expiry_list = ub.get_expiry_list(sym) or []
                    use_expiry = expiry or (expiry_list[0] if expiry_list else None)
                    chain_raw  = ub.get_option_chain(sym, use_expiry)
                    if chain_raw:
                        spot = float(chain_raw[0].get('spot', 0)) if chain_raw else 0.0
                        if not spot or spot <= 0:
                            spot = float(ub.get_price(sym) or 0)
                        if spot > 0:
                            logger.info(f"gateway: option chain {sym} via user:{_ub_name} ({len(chain_raw)} strikes)")
                            return _normalise(chain_raw, spot, SRC_ADMIN, use_expiry or "")
                    logger.debug(f"gateway: user broker '{_ub_name}' returned no chain — trying next")
                except Exception as _ube:
                    logger.debug(f"gateway: user broker '{_ub_name}' option chain({sym}): {_ube} — trying next")
        except Exception as e:
            logger.debug(f"gateway: user broker loop option chain({sym}): {e}")

    # ── 3. TrueData ───────────────────────────────────────────────────────────
    plan_type, td_key, _td_secret = _get_admin_plan()
    if plan_type == 'nse_truedata' and td_key:
        try:
            import requests as _req
            headers = {'Authorization': f'Bearer {td_key}', 'Content-Type': 'application/json'}
            spot_r = _req.get('https://api.truedata.in/v1/getltp',
                              params={'symbol': sym}, headers=headers, timeout=10)
            if spot_r.status_code == 200:
                sd = spot_r.json()
                spot = float(sd.get('ltp') or (sd.get('data') or {}).get('ltp', 0) or 0)
                if spot > 0:
                    chain_r = _req.get('https://api.truedata.in/v1/optionchain',
                                       params={'symbol': sym}, headers=headers, timeout=15)
                    if chain_r.status_code == 200:
                        cd = chain_r.json()
                        records = cd.get('data', cd.get('optionchain', []))
                        if records:
                            normalised_list = [
                                {
                                    'strike':       rec.get('strike', rec.get('strikePrice', 0)),
                                    'call_ltp':     rec.get('call_ltp', (rec.get('CE') or {}).get('ltp', 0)),
                                    'call_oi':      rec.get('call_oi',  (rec.get('CE') or {}).get('oi',  0)),
                                    'call_iv':      rec.get('call_iv',  (rec.get('CE') or {}).get('iv',  0)),
                                    'call_volume':  rec.get('call_volume', (rec.get('CE') or {}).get('volume', 0)),
                                    'put_ltp':      rec.get('put_ltp',  (rec.get('PE') or {}).get('ltp', 0)),
                                    'put_oi':       rec.get('put_oi',   (rec.get('PE') or {}).get('oi',  0)),
                                    'put_iv':       rec.get('put_iv',   (rec.get('PE') or {}).get('iv',  0)),
                                    'put_volume':   rec.get('put_volume',  (rec.get('PE') or {}).get('volume', 0)),
                                }
                                for rec in records
                            ]
                            logger.info(f"gateway: option chain {sym} via TrueData ({len(normalised_list)} strikes)")
                            return _normalise(normalised_list, spot, SRC_TRUEDATA, "")
        except Exception as e:
            logger.debug(f"gateway: TrueData option chain({sym}): {e}")

    # ── 4. System Dhan ────────────────────────────────────────────────────────
    try:
        from services.dhan_service import get_option_chain as dhan_oc
        chain_raw = dhan_oc(symbol=sym, expiry=expiry)
        if chain_raw:
            spot = float(chain_raw[0].get('spot', 0)) if chain_raw else 0.0
            if spot > 0:
                logger.info(f"gateway: option chain {sym} via system Dhan ({len(chain_raw)} strikes)")
                return _normalise(chain_raw, spot, SRC_ADMIN, expiry or "")
    except Exception as e:
        logger.debug(f"gateway: system Dhan option chain({sym}): {e}")

    # ── 5. NSEPython OI chain ────────────────────────────────────────────────
    try:
        from services.nse_service import NSEService
        nse_data = NSEService().get_option_chain_oi(sym)
        if nse_data and nse_data.get('option_chain'):
            spot = float(nse_data.get('spot_price', 0) or nse_data.get('underlyingValue', 0))
            if spot > 0:
                logger.info(f"gateway: option chain {sym} via NSEPython")
                return {
                    "option_chain": nse_data['option_chain'],
                    "spot_price":   spot,
                    "expiry":       nse_data.get('expiry', expiry or ""),
                    "source":       SRC_NSE,
                    "success":      True,
                }
    except Exception as e:
        logger.debug(f"gateway: NSEPython option chain({sym}): {e}")

    return {"option_chain": {}, "spot_price": 0.0, "expiry": expiry or "", "source": SRC_ESTIMATED, "success": False}


def invalidate_cache(symbol: Optional[str] = None):
    """Evict cache entries. Pass symbol=None to clear everything."""
    if symbol is None:
        _PRICE_CACHE.clear()
        _OHLCV_CACHE.clear()
    else:
        sym = symbol.upper().strip()
        for key in list(_PRICE_CACHE.keys()):
            if f"price:{sym}:" in key:
                del _PRICE_CACHE[key]
        for key in list(_OHLCV_CACHE.keys()):
            if f"ohlcv:{sym}:" in key:
                del _OHLCV_CACHE[key]


def source_badge(source: str) -> Dict[str, str]:
    """
    Map a source string to a UI badge spec.

    Returns::

        {"label": str, "css_class": str, "color": str}

    css_class is a Bootstrap alert-variant name: "success" | "warning" | "secondary".
    Used by templates to render consistent data-source pills.
    """
    # Canonical 5 source labels: admin_broker, truedata, nse, yfinance, estimated.
    # Legacy/alias values (dhan_system, user_broker, Dhan, etc.) kept here for
    # backward compatibility with any callers that still pass the old strings,
    # but the gateway itself no longer emits them.
    _map = {
        SRC_ADMIN:     ("Live · Broker",   "success",   "#16a34a"),
        "admin_broker":("Live · Broker",   "success",   "#16a34a"),
        "user_broker": ("Live · Broker",   "success",   "#16a34a"),   # legacy alias
        "dhan_system": ("Live · Broker",   "success",   "#16a34a"),   # legacy alias
        "Dhan":        ("Live · Broker",   "success",   "#16a34a"),   # legacy alias
        SRC_TRUEDATA:  ("Live · TrueData", "success",   "#16a34a"),
        SRC_NSE:       ("Live · NSE",      "warning",   "#d97706"),
        SRC_YFINANCE:  ("Delayed · Yahoo", "warning",   "#d97706"),
        SRC_ESTIMATED: ("Estimated",       "secondary", "#6b7280"),
        "none":        ("Unavailable",     "secondary", "#6b7280"),
        "unavailable": ("Unavailable",     "secondary", "#6b7280"),
    }
    for key, val in _map.items():
        if source and (source == key or source.startswith(key + ':')):
            return {"label": val[0], "css_class": val[1], "color": val[2]}
    return {"label": "Live", "css_class": "success", "color": "#16a34a"}
