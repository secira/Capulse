"""
Routes for Live Market Pulse (formerly Daily Trading Signals)
"""
from flask import render_template, request, jsonify, flash, redirect, url_for, send_file
from flask_login import login_required, current_user
from app import app, db
from models import DailyTradingSignal, PricingPlan
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import json
import time

_IST = ZoneInfo('Asia/Kolkata')

def _today_ist() -> date:
    """Return today's date in Indian Standard Time (UTC+5:30)."""
    return datetime.now(_IST).date()

logger = logging.getLogger(__name__)

LANG_NAMES = {
    'en': 'English', 'hi': 'Hindi', 'ta': 'Tamil',
    'te': 'Telugu',  'mr': 'Marathi', 'gu': 'Gujarati', 'kn': 'Kannada',
}

# ── In-memory market data cache (5-minute TTL) ───────────────────────────────
_MARKET_CACHE: dict = {}
_CACHE_TTL         = 300  # seconds — general market data (gainers/losers/movers)
_CACHE_TTL_INDICES = 60   # seconds — index prices refresh every minute


def _market_cache_get(key, ttl=None):
    entry = _MARKET_CACHE.get(key)
    if entry and (time.time() - entry['ts']) < (ttl or _CACHE_TTL):
        return entry['data']
    return None


def _market_cache_set(key, data):
    _MARKET_CACHE[key] = {'data': data, 'ts': time.time()}


NIFTY50_STOCKS = [
    {'symbol': 'RELIANCE',   'name': 'Reliance',      'sector': 'Energy',     'weight': 10.5},
    {'symbol': 'HDFCBANK',   'name': 'HDFC Bank',     'sector': 'Banking',    'weight': 13.0},
    {'symbol': 'ICICIBANK',  'name': 'ICICI Bank',    'sector': 'Banking',    'weight': 7.5},
    {'symbol': 'BHARTIARTL', 'name': 'Airtel',        'sector': 'Telecom',    'weight': 3.5},
    {'symbol': 'INFY',       'name': 'Infosys',       'sector': 'IT',         'weight': 5.0},
    {'symbol': 'TCS',        'name': 'TCS',           'sector': 'IT',         'weight': 5.8},
    {'symbol': 'SBIN',       'name': 'SBI',           'sector': 'Banking',    'weight': 3.0},
    {'symbol': 'ITC',        'name': 'ITC',           'sector': 'FMCG',       'weight': 3.2},
    {'symbol': 'LT',         'name': 'L&T',           'sector': 'Cap Goods',  'weight': 3.0},
    {'symbol': 'KOTAKBANK',  'name': 'Kotak Bank',    'sector': 'Banking',    'weight': 3.5},
    {'symbol': 'BAJFINANCE', 'name': 'Bajaj Fin',     'sector': 'NBFC',       'weight': 3.0},
    {'symbol': 'AXISBANK',   'name': 'Axis Bank',     'sector': 'Banking',    'weight': 2.5},
    {'symbol': 'SUNPHARMA',  'name': 'Sun Pharma',    'sector': 'Pharma',     'weight': 2.2},
    {'symbol': 'MARUTI',     'name': 'Maruti',        'sector': 'Auto',       'weight': 2.0},
    {'symbol': 'M&M',        'name': 'M&M',           'sector': 'Auto',       'weight': 2.2},
    {'symbol': 'HINDUNILVR', 'name': 'HUL',           'sector': 'FMCG',       'weight': 2.8},
    {'symbol': 'NESTLEIND',  'name': 'Nestle',        'sector': 'FMCG',       'weight': 1.3},
    {'symbol': 'WIPRO',      'name': 'Wipro',         'sector': 'IT',         'weight': 1.5},
    {'symbol': 'ULTRACEMCO', 'name': 'UltraTech',     'sector': 'Cement',     'weight': 1.8},
    {'symbol': 'TITAN',      'name': 'Titan',         'sector': 'Consumer',   'weight': 1.8},
    {'symbol': 'HCLTECH',    'name': 'HCL Tech',      'sector': 'IT',         'weight': 2.0},
    {'symbol': 'BAJAJFINSV', 'name': 'Bajaj FS',      'sector': 'NBFC',       'weight': 1.5},
    {'symbol': 'ADANIPORTS', 'name': 'Adani Ports',   'sector': 'Infra',      'weight': 1.2},
    {'symbol': 'POWERGRID',  'name': 'Power Grid',    'sector': 'Power',      'weight': 1.2},
    {'symbol': 'NTPC',       'name': 'NTPC',          'sector': 'Power',      'weight': 1.3},
    {'symbol': 'JSWSTEEL',   'name': 'JSW Steel',     'sector': 'Metals',     'weight': 1.2},
    {'symbol': 'TATAMOTORS', 'name': 'Tata Motors',   'sector': 'Auto',       'weight': 1.5},
    {'symbol': 'COALINDIA',  'name': 'Coal India',    'sector': 'Energy',     'weight': 1.0},
    {'symbol': 'ONGC',       'name': 'ONGC',          'sector': 'Energy',     'weight': 1.3},
    {'symbol': 'ASIANPAINT', 'name': 'Asian Paints',  'sector': 'Consumer',   'weight': 1.5},
    {'symbol': 'HINDALCO',   'name': 'Hindalco',      'sector': 'Metals',     'weight': 1.0},
    {'symbol': 'BEL',        'name': 'BEL',           'sector': 'Defence',    'weight': 0.8},
    {'symbol': 'ADANIENT',   'name': 'Adani Ent',     'sector': 'Conglom',    'weight': 0.9},
    {'symbol': 'TECHM',      'name': 'Tech M',        'sector': 'IT',         'weight': 1.0},
    {'symbol': 'DRREDDY',    'name': 'Dr Reddy',      'sector': 'Pharma',     'weight': 1.0},
    {'symbol': 'EICHER',     'name': 'Eicher',        'sector': 'Auto',       'weight': 0.8},
    {'symbol': 'BPCL',       'name': 'BPCL',          'sector': 'Energy',     'weight': 0.7},
    {'symbol': 'CIPLA',      'name': 'Cipla',         'sector': 'Pharma',     'weight': 0.9},
    {'symbol': 'SHRIRAMFIN', 'name': 'Shriram Fin',   'sector': 'NBFC',       'weight': 0.8},
    {'symbol': 'GRASIM',     'name': 'Grasim',        'sector': 'Cement',     'weight': 0.8},
    {'symbol': 'INDUSINDBK', 'name': 'IndusInd Bk',   'sector': 'Banking',    'weight': 0.8},
    {'symbol': 'TATACONSUM', 'name': 'Tata Cons',     'sector': 'FMCG',       'weight': 0.7},
    {'symbol': 'HEROMOTOCO', 'name': 'Hero Moto',     'sector': 'Auto',       'weight': 0.7},
    {'symbol': 'APOLLOHOSP', 'name': 'Apollo Hosp',   'sector': 'Healthcare', 'weight': 0.7},
    {'symbol': 'TATASTEEL',  'name': 'Tata Steel',    'sector': 'Metals',     'weight': 0.8},
    {'symbol': 'BRITANNIA',  'name': 'Britannia',     'sector': 'FMCG',       'weight': 0.6},
    {'symbol': 'SBILIFE',    'name': 'SBI Life',      'sector': 'Insurance',  'weight': 0.8},
    {'symbol': 'HDFCLIFE',   'name': 'HDFC Life',     'sector': 'Insurance',  'weight': 0.7},
    {'symbol': 'BAJAJ-AUTO', 'name': 'Bajaj Auto',    'sector': 'Auto',       'weight': 1.1},
    {'symbol': 'TRENT',      'name': 'Trent',         'sector': 'Consumer',   'weight': 0.8},
]

SECTOR_COLORS = {
    'Banking': '#3b82f6', 'IT': '#8b5cf6', 'FMCG': '#f59e0b',
    'Energy': '#f97316', 'Auto': '#10b981', 'Pharma': '#06b6d4',
    'Cap Goods': '#6366f1', 'NBFC': '#ec4899', 'Metals': '#78716c',
    'Power': '#84cc16', 'Infra': '#0ea5e9', 'Cement': '#a16207',
    'Consumer': '#d946ef', 'Telecom': '#14b8a6', 'Defence': '#64748b',
    'Conglom': '#71717a', 'Insurance': '#22c55e', 'Healthcare': '#0284c7',
}


def _call_perplexity_structured(prompt: str, timeout: int = 15) -> str:
    """
    Direct Perplexity sonar call with today's recency filter.
    Returns raw response text, or '' on failure.
    """
    import os as _os, requests as _req
    api_key = _os.environ.get('PERPLEXITY_API_KEY', '')
    if not api_key:
        return ''
    try:
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a financial data API that returns only valid JSON. "
                        "Never add markdown fences, explanations, or extra text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 2000,
            "temperature": 0.1,
            "search_recency_filter": "day",
            "stream": False,
        }
        resp = _req.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        logger.warning(f"Perplexity structured call HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"Perplexity structured call error: {e}")
    return ''


def _fetch_perplexity_market_data() -> dict:
    """
    Fetch Nifty50 treemap + top movers from Perplexity (sonar-pro web search, day recency).
    Cached 5 minutes.  Returns:
      {
        "nifty50": {"RELIANCE": -0.5, "HDFCBANK": 0.8, ...},
        "top_gainers": [{"symbol":..,"company":..,"change_percent":..,"price":..}, ...],
        "top_losers":  [...],
        "most_active": [...,"volume":"1.2Cr"},  ...],
      }
    """
    cached = _market_cache_get('perplexity_market', ttl=300)
    if cached is not None:
        return cached

    import re as _re, json as _json
    symbols_csv = ', '.join(s['symbol'] for s in NIFTY50_STOCKS)
    prompt = (
        "Provide live NSE market data for today's trading session. "
        "Return ONLY a valid JSON object — no markdown, no code fences, no explanation.\n"
        "Use this exact schema:\n"
        "{\n"
        '  "top_gainers": [{"symbol":"INFY","company":"Infosys Ltd","change_percent":2.5,"price":1450.0}, '
        "...8 stocks with highest gain today],\n"
        '  "top_losers":  [{"symbol":"TATASTEEL","company":"Tata Steel Ltd","change_percent":-3.1,"price":120.5}, '
        "...8 stocks with biggest fall today],\n"
        '  "most_active": [{"symbol":"SBIN","company":"State Bank of India","change_percent":1.2,'
        '"price":610.0,"volume":"1.5Cr"}, ...8 most traded by volume today],\n'
        '  "nifty50":     {"RELIANCE":-0.5,"HDFCBANK":0.8,...all 50 stocks}\n'
        "}\n\n"
        f"The 50 Nifty50 stock symbols are: {symbols_csv}\n"
        "Include EVERY one of the 50 stocks in the nifty50 object with their actual % change today."
    )
    text = _call_perplexity_structured(prompt, timeout=15)
    if text:
        try:
            json_match = _re.search(r'\{[\s\S]*\}', text)
            if json_match:
                data = _json.loads(json_match.group())
                _market_cache_set('perplexity_market', data)
                logger.info(
                    f"Perplexity market data OK — "
                    f"gainers={len(data.get('top_gainers', []))}, "
                    f"nifty50={len(data.get('nifty50', {}))}"
                )
                return data
        except Exception as e:
            logger.warning(f"Perplexity market data JSON parse error: {e} | raw: {text[:200]}")
    return {}


def _fetch_nse_nifty50_stocks() -> dict:
    """
    Fetch live Nifty 50 stock data from NSE equity-stockIndices API.
    Returns dict keyed by symbol:
      { "RELIANCE": {"change_percent": 1.2, "price": 2500.0, "volume": 1234567}, ... }
    Cached 2 minutes.
    """
    cached = _market_cache_get('nse_nifty50_stocks', ttl=120)
    if cached is not None:
        return cached
    try:
        import requests as _req
        sess = _req.Session()
        sess.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.nseindia.com/',
            'Origin': 'https://www.nseindia.com',
        })
        # Establish session cookie first
        sess.get('https://www.nseindia.com', timeout=6)
        r = sess.get(
            'https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050',
            timeout=8,
        )
        if r.status_code == 200:
            rows = r.json().get('data', [])
            result = {}
            for row in rows:
                sym = row.get('symbol', '')
                if not sym or sym in ('NIFTY 50',):  # skip the index row itself
                    continue
                pchg = row.get('pChange') or row.get('percentChange', 0)
                price = row.get('lastPrice') or row.get('last', 0)
                vol   = row.get('totalTradedVolume') or row.get('volume', 0)
                result[sym] = {
                    'change_percent': round(float(pchg), 2),
                    'price':          round(float(price), 2),
                    'volume':         int(vol or 0),
                }
            if result:
                _market_cache_set('nse_nifty50_stocks', result)
                logger.info(f"NSE Nifty50 live stocks fetched: {len(result)} symbols")
                return result
    except Exception as e:
        logger.warning(f"NSE Nifty50 stock fetch failed: {e}")
    return {}


def _get_live_nifty50_data(nse_data: dict = None) -> tuple:
    """
    Build Nifty50 treemap list using NSE live data.
    Falls back to yfinance per-symbol if NSE unavailable.
    Never uses random/seeded fake values — shows 0.0 with data_available=False when
    no real source is reachable.

    Returns (list_of_stocks, source_label) where source_label is one of:
      'nse'  — live from NSE equity-stockIndices API
      'yfinance' — fallback individual fetches
      'unavailable' — no live data; all changes shown as 0.0
    """
    cached = _market_cache_get('nifty50_v2')
    if cached is not None:
        return cached  # already a (list, source) tuple

    # Priority 1: NSE
    if nse_data is None:
        nse_data = _fetch_nse_nifty50_stocks()

    result = []
    if nse_data:
        for stock in NIFTY50_STOCKS:
            d = nse_data.get(stock['symbol'], {})
            result.append({
                'symbol':          stock['symbol'],
                'name':            stock['name'],
                'sector':          stock['sector'],
                'weight':          stock['weight'],
                'change_percent':  d.get('change_percent', 0.0),
                'price':           d.get('price', 0.0),
                'data_available':  bool(d),
            })
        _market_cache_set('nifty50_v2', (result, 'nse'))
        return result, 'nse'

    # Priority 2: yfinance individual fetches
    try:
        import yfinance as _yf
        yf_result = {}
        symbols_ns = [s['symbol'] + '.NS' for s in NIFTY50_STOCKS]
        tickers = _yf.Tickers(' '.join(symbols_ns))
        for stock in NIFTY50_STOCKS:
            key = stock['symbol'] + '.NS'
            try:
                fi    = tickers.tickers[key].fast_info
                ltp   = float(getattr(fi, 'last_price', 0) or 0)
                prev  = float(getattr(fi, 'previous_close', 0) or 0)
                if ltp > 0 and prev > 0:
                    pchg = round((ltp - prev) / prev * 100, 2)
                    yf_result[stock['symbol']] = {'change_percent': pchg, 'price': ltp}
            except Exception:
                pass
        if yf_result:
            for stock in NIFTY50_STOCKS:
                d = yf_result.get(stock['symbol'], {})
                result.append({
                    'symbol':         stock['symbol'],
                    'name':           stock['name'],
                    'sector':         stock['sector'],
                    'weight':         stock['weight'],
                    'change_percent': d.get('change_percent', 0.0),
                    'price':          d.get('price', 0.0),
                    'data_available': bool(d),
                })
            _market_cache_set('nifty50_v2', (result, 'yfinance'))
            return result, 'yfinance'
    except Exception as e:
        logger.warning(f"yfinance Nifty50 fallback failed: {e}")

    # No data available — return all tiles with 0.0, clearly marked
    for stock in NIFTY50_STOCKS:
        result.append({
            'symbol':         stock['symbol'],
            'name':           stock['name'],
            'sector':         stock['sector'],
            'weight':         stock['weight'],
            'change_percent': 0.0,
            'price':          0.0,
            'data_available': False,
        })
    _market_cache_set('nifty50_v2', (result, 'unavailable'))
    return result, 'unavailable'


def _get_sector_summary(nifty50_data):
    """Aggregate Nifty 50 data into sector-level performance."""
    from collections import defaultdict
    sectors = defaultdict(lambda: {'total_weight': 0, 'weighted_change': 0, 'stocks': 0})
    for s in nifty50_data:
        sec = s['sector']
        sectors[sec]['total_weight'] += s['weight']
        sectors[sec]['weighted_change'] += s['change_percent'] * s['weight']
        sectors[sec]['stocks'] += 1

    result = []
    for sec, d in sectors.items():
        avg_chg = round(d['weighted_change'] / d['total_weight'], 2) if d['total_weight'] else 0
        result.append({
            'sector': sec,
            'change_percent': avg_chg,
            'stocks': d['stocks'],
            'color': SECTOR_COLORS.get(sec, '#64748b'),
        })
    result.sort(key=lambda x: x['change_percent'], reverse=True)
    return result


@app.route('/dashboard/daily-signals')
@login_required
def dashboard_daily_signals():
    if not current_user.is_authenticated or not current_user.can_access_menu('dashboard_trading_signals'):
        flash("This feature requires a Target Plus or higher subscription.", "warning")
        return redirect(url_for('pricing'))

    # Holiday short-circuit — render only the holiday wish card, skip the
    # expensive index / treemap / NSE fetches entirely. Template hides every
    # market-data section when is_holiday is truthy.
    try:
        from services.market_calendar import get_holiday
        _h = get_holiday()
        if _h:
            return render_template(
                'dashboard/live_market_pulse.html',
                is_holiday=True,
                holiday=_h,
                signals=[], selected_date=_today_ist(), date_range=[],
                asset_type_filter='all', duration_filter='all', status_filter='all',
                summary_stats={}, market_indices={}, top_gainers=[], top_losers=[],
                most_active=[], nifty50_data=[], nifty50_source='holiday',
                sector_data=[],
            )
    except Exception:
        pass

    selected_date_str = request.args.get('date')
    asset_type_filter = request.args.get('asset_type', 'all')
    duration_filter   = request.args.get('duration', 'all')
    status_filter     = request.args.get('status', 'all')

    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = _today_ist()
    else:
        selected_date = _today_ist()

    query = DailyTradingSignal.query.filter(DailyTradingSignal.signal_date == selected_date)
    if asset_type_filter != 'all':
        query = query.filter(DailyTradingSignal.asset_type == asset_type_filter)
    if duration_filter != 'all':
        query = query.filter(DailyTradingSignal.trade_duration == duration_filter)
    if status_filter != 'all':
        query = query.filter(DailyTradingSignal.status == status_filter)

    try:
        signals = query.order_by(DailyTradingSignal.signal_number.asc()).all()
    except Exception as _e:
        # Schema drift safety net (e.g. missing column on prod): roll back the
        # aborted transaction so the rest of the page can still render, and
        # show an empty signals list instead of returning 500.
        from flask import current_app
        current_app.logger.error(f"daily_signals query failed: {_e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        signals = []

    date_range = [_today_ist() - timedelta(days=i) for i in range(30)]
    try:
        summary_stats = calculate_daily_summary(selected_date)
    except Exception as _e:
        from flask import current_app
        current_app.logger.error(f"daily_signals summary failed: {_e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        # Same shape as calculate_daily_summary's empty-result branch — the
        # template dereferences keys like .success_rate in numeric comparisons,
        # so an empty dict would itself raise UndefinedError.
        summary_stats = {
            'total_signals': 0, 'active': 0, 'target_1_hit': 0, 'target_2_hit': 0,
            'sl_hit': 0, 'early_exit': 0, 'total_profit_points': 0,
            'total_loss_points': 0, 'net_points': 0, 'success_rate': 0,
            'by_asset_type': {}, 'by_duration': {},
        }

    # ── Market data: Dhan (indices) + Perplexity (movers + treemap), parallel ──
    # NO hardcoded values — start with empty placeholders. UI shows "No Data"
    # if Dhan + NSE + yfinance all fail.
    market_indices = {
        'nifty_50':   {'label': 'NIFTY 50',   'value': None, 'change_percent': None, 'live': False},
        'nifty_bank': {'label': 'BANK NIFTY', 'value': None, 'change_percent': None, 'live': False},
        'sensex':     {'label': 'SENSEX',     'value': None, 'change_percent': None, 'live': False},
        'nifty_it':   {'label': 'NIFTY IT',   'value': None, 'change_percent': None, 'live': False},
        'india_vix':  {'label': 'INDIA VIX',  'value': None, 'change_percent': None, 'live': False},
    }
    top_gainers = []
    top_losers  = []
    most_active = []
    nifty50_data = []

    def _fetch_dhan_indices():
        """Fetch index prices: Dhan → NSE allIndices → yfinance. 60-second cache."""
        cached = _market_cache_get('indices', ttl=_CACHE_TTL_INDICES)
        if cached is not None:
            return cached
        result = {}
        ALL_KEYS = {'nifty_50', 'nifty_bank', 'sensex', 'nifty_it', 'india_vix'}

        # Priority 1: Dhan
        try:
            from services.dhan_service import get_index_quotes
            dhan_data = get_index_quotes(_captured_uid)
            DHAN_KEY_MAP = {
                'NIFTY':     'nifty_50',
                'BANKNIFTY': 'nifty_bank',
                'SENSEX':    'sensex',
                'INDIA VIX': 'india_vix',
            }
            for dhan_sym, key in DHAN_KEY_MAP.items():
                d = dhan_data.get(dhan_sym, {})
                ltp = float(d.get('ltp', 0))
                if ltp > 0:
                    chg = float(d.get('pct_change', 0))
                    if not chg:
                        close = float(d.get('close', ltp) or ltp)
                        chg = round((ltp - close) / close * 100, 2) if close else 0.0
                    result[key] = {'value': round(ltp, 2), 'change_percent': chg, 'live': True, 'source': 'Dhan'}
            logger.info(f"Dhan indices fetched: {list(result.keys())}")
        except Exception as e:
            logger.warning(f"Dhan index fetch failed: {e}")

        # Priority 2: NSE allIndices for missing
        missing = ALL_KEYS - set(result.keys())
        if missing:
            try:
                import requests
                sess = requests.Session()
                sess.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
                    'Accept': 'application/json',
                    'Referer': 'https://www.nseindia.com/',
                })
                sess.get('https://www.nseindia.com', timeout=5)
                r = sess.get('https://www.nseindia.com/api/allIndices', timeout=8)
                if r.status_code == 200:
                    nse_lookup = {row['index']: row for row in r.json().get('data', [])}
                    NSE_KEY_MAP = {
                        'nifty_50': 'NIFTY 50', 'nifty_bank': 'NIFTY BANK',
                        'sensex': 'SENSEX', 'nifty_it': 'NIFTY IT', 'india_vix': 'INDIA VIX',
                    }
                    for key in list(missing):
                        row = nse_lookup.get(NSE_KEY_MAP[key])
                        if row:
                            ltp = float(row.get('last', 0) or 0)
                            chg = float(row.get('percentChange', row.get('pChange', 0)) or 0)
                            if ltp > 0:
                                result[key] = {'value': round(ltp, 2), 'change_percent': chg, 'live': True, 'source': 'NSE'}
                logger.info(f"NSE fallback merged: now have {list(result.keys())}")
            except Exception as e:
                logger.warning(f"NSE allIndices fallback failed: {e}")

        # Priority 3: yfinance for missing
        missing = ALL_KEYS - set(result.keys())
        if missing:
            try:
                import yfinance as yf
                YF_MAP = {
                    'nifty_50': '^NSEI', 'nifty_bank': '^NSEBANK',
                    'sensex': '^BSESN', 'nifty_it': '^CNXIT', 'india_vix': '^INDIAVIX',
                }
                for key in list(missing):
                    try:
                        fi = yf.Ticker(YF_MAP[key]).fast_info
                        ltp  = float(getattr(fi, 'last_price', 0) or 0)
                        prev = float(getattr(fi, 'previous_close', 0) or 0)
                        if ltp > 0:
                            chg = round((ltp - prev) / prev * 100, 2) if prev else 0
                            result[key] = {'value': round(ltp, 2), 'change_percent': chg, 'live': True, 'source': 'yfinance'}
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"yfinance fallback failed: {e}")

        if result:
            _market_cache_set('indices', result)
        return result

    # Capture user ID BEFORE spawning threads — current_user proxy is not thread-safe
    try:
        _captured_uid = current_user.id if current_user.is_authenticated else None
    except Exception:
        _captured_uid = None

    import concurrent.futures as _cf
    nifty50_source = 'unavailable'
    try:
        # Fetch index prices (Dhan→NSE→yfinance) and Nifty50 stock data (NSE) in parallel
        pool = _cf.ThreadPoolExecutor(max_workers=2)
        f_dhan = pool.submit(_fetch_dhan_indices)
        f_nse  = pool.submit(_fetch_nse_nifty50_stocks)

        done, _ = _cf.wait([f_dhan, f_nse], timeout=14)
        pool.shutdown(wait=False)

        dhan_result = {}
        nse_stocks  = {}
        try:
            if f_dhan in done:
                dhan_result = f_dhan.result() or {}
        except Exception as e:
            logger.warning(f"Dhan future error: {e}")
        try:
            if f_nse in done:
                nse_stocks = f_nse.result() or {}
        except Exception as e:
            logger.warning(f"NSE future error: {e}")

        # Merge Dhan index data
        for key in ('nifty_50', 'nifty_bank', 'sensex', 'nifty_it', 'india_vix'):
            d = dhan_result.get(key, {})
            v = d.get('value', 0)
            c = d.get('change_percent', None)
            if v and float(v) > 0:
                market_indices[key]['value'] = float(v)
                if c is not None:
                    market_indices[key]['change_percent'] = float(c)
                market_indices[key]['live'] = True

        # Build Nifty50 treemap from real NSE data
        nifty50_data, nifty50_source = _get_live_nifty50_data(nse_stocks)

        # Derive top gainers / losers / most active from NSE live data (accurate, no AI)
        if nse_stocks:
            sym_map = {s['symbol']: s for s in NIFTY50_STOCKS}
            all_nse = []
            for sym, d in nse_stocks.items():
                meta = sym_map.get(sym, {})
                all_nse.append({
                    'symbol':         sym,
                    'company_name':   meta.get('name', sym),
                    'change_percent': d.get('change_percent', 0.0),
                    'current_price':  d.get('price', 0.0),
                    'volume':         str(d.get('volume', '')),
                })
            sorted_by_chg = sorted(all_nse, key=lambda x: x['change_percent'], reverse=True)
            top_gainers = [r for r in sorted_by_chg if r['change_percent'] > 0][:8]
            top_losers  = list(reversed([r for r in sorted_by_chg if r['change_percent'] < 0]))[:8]
            most_active = sorted(all_nse, key=lambda x: float(x['volume'] or 0), reverse=True)[:8]

    except Exception as e:
        logger.warning(f"Market data parallel fetch error: {e}")
        nifty50_data, nifty50_source = _get_live_nifty50_data()

    sector_data = _get_sector_summary(nifty50_data)

    return render_template(
        'dashboard/live_market_pulse.html',
        signals=signals,
        selected_date=selected_date,
        date_range=date_range,
        asset_type_filter=asset_type_filter,
        duration_filter=duration_filter,
        status_filter=status_filter,
        summary_stats=summary_stats,
        market_indices=market_indices,
        top_gainers=top_gainers,
        top_losers=top_losers,
        most_active=most_active,
        nifty50_data=nifty50_data,
        nifty50_source=nifty50_source,
        sector_data=sector_data,
    )


@app.route('/api/market-pulse/commentary')
@login_required
def market_pulse_commentary():
    """Generate an AI-powered market commentary using Perplexity (uses in-memory cache for speed)."""
    try:
        # Holiday gate — no market commentary on trading holidays.
        try:
            from services.market_calendar import get_holiday
            h = get_holiday()
            if h:
                return jsonify({
                    'success': True,
                    'is_holiday': True,
                    'commentary': (
                        f"🌸 Happy {h['name']}! Markets are closed today. "
                        "Wishing you and your family a wonderful day from Target Capital. "
                        "We'll be back with live market commentary on the next trading day."
                    ),
                    'lang': 'en',
                })
        except Exception:
            pass

        from services.perplexity_api import PerplexityAPI

        # Reuse cached data to avoid hitting slow external APIs a second time
        idx_cache  = _market_cache_get('indices', ttl=_CACHE_TTL_INDICES) or {}
        plex_cache = _market_cache_get('perplexity_market', ttl=300) or {}

        nifty_val  = idx_cache.get('nifty_50', {}).get('value')
        nifty_chg  = idx_cache.get('nifty_50', {}).get('change_percent')
        bnifty_chg = idx_cache.get('nifty_bank', {}).get('change_percent')

        nifty_str  = (f"{float(nifty_val):,.0f} ({'+' if float(nifty_chg or 0) >= 0 else ''}{float(nifty_chg or 0):.2f}%)"
                      if nifty_val else "N/A (data unavailable)")
        bnifty_str = (f"{'+' if float(bnifty_chg) >= 0 else ''}{float(bnifty_chg):.2f}%"
                      if bnifty_chg is not None else "N/A")

        gainers = plex_cache.get('top_gainers', [])
        losers  = plex_cache.get('top_losers',  [])
        top_g = ', '.join(f"{g.get('symbol','')} (+{float(g.get('change_percent',0)):.1f}%)" for g in gainers[:3])
        top_l = ', '.join(f"{l.get('symbol','')} ({float(l.get('change_percent',0)):.1f}%)"  for l in losers[:3])

        lang_code = getattr(current_user, 'preferred_language', 'en') or 'en'
        lang_name = LANG_NAMES.get(lang_code, 'English')
        lang_instr = (
            f" Respond entirely in {lang_name}. Every word of your response must be written in {lang_name}."
            if lang_code != 'en' else ''
        )

        prompt = (
            f"You are a concise Indian market analyst writing for retail traders.\n\n"
            f"Today's Indian market snapshot:\n"
            f"• Nifty 50: {nifty_str}\n"
            f"• Bank Nifty change: {bnifty_str}\n"
            f"• Top gainers: {top_g or 'N/A'}\n"
            f"• Top losers: {top_l or 'N/A'}\n\n"
            "Write a concise 2-paragraph market commentary (max 120 words total) for an Indian retail trader. "
            "First paragraph: today's market tone and key index moves. "
            "Second paragraph: 1-2 sector themes or stocks to watch. "
            f"Use plain, direct language. No disclaimers.{lang_instr}"
        )

        perplexity = PerplexityAPI()
        text, _ = perplexity.get_investment_advice(prompt)

        return jsonify({'success': True, 'commentary': (text or '').strip(), 'lang': lang_code})

    except Exception as e:
        logger.error(f"AI commentary error: {e}")
        return jsonify({'success': False, 'commentary': ''})


# ── Market Direction — 4-index EMA/Supertrend/VWAP/RSI direction bar ──────────

_DIRECTION_INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX']

def _fetch_one_direction(index: str, user_id: int) -> dict:
    """Thread-safe worker: fetch direction for a single index."""
    try:
        from services.nifty_options_engine import NiftyOptionsEngine
        eng = NiftyOptionsEngine(user_id=user_id, index=index)
        return eng.get_market_direction()
    except Exception as ex:
        logger.warning(f"market_direction({index}): {ex}")
        return {
            'index': index, 'label': index, 'direction': 'SIDEWAYS',
            'reason': 'Data unavailable', 'score': {'bull': 0, 'bear': 0},
            'signals': {}, 'data_ok': False,
        }


@app.route('/api/market-pulse/market-direction')
@login_required
def api_market_pulse_direction():
    # Holiday gate — no market direction data on trading holidays.
    try:
        from services.market_calendar import get_holiday
        h = get_holiday()
        if h:
            return jsonify({
                'success': True,
                'is_holiday': True,
                'holiday': {'name': h['name']},
                'data': {},
                'timestamp': datetime.now(_IST).strftime('%H:%M:%S IST'),
            })
    except Exception:
        pass
    return _api_market_pulse_direction_impl()


def _api_market_pulse_direction_impl():
    """Return BULLISH / BEARISH / SIDEWAYS for NIFTY, BANKNIFTY, FINNIFTY, SENSEX.

    Uses the same EMA 9/21 + Supertrend + VWAP + RSI logic as the F&O engine.
    Candles are shared via the module-level cache, so this is fast after the
    first call. Results are cached for 58 seconds (matches the F&O monitor cycle).
    """
    try:
        uid = current_user.id
        results = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_one_direction, idx, uid): idx
                       for idx in _DIRECTION_INDICES}
            for fut in as_completed(futures, timeout=12):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as ex:
                    logger.warning(f"market_direction fut({idx}): {ex}")
                    results[idx] = {
                        'index': idx, 'label': idx, 'direction': 'SIDEWAYS',
                        'reason': 'Error', 'score': {'bull': 0, 'bear': 0},
                        'signals': {}, 'data_ok': False,
                    }

        now_ist = datetime.now(_IST).strftime('%H:%M:%S IST')
        return jsonify({'success': True, 'data': results, 'timestamp': now_ist})

    except Exception as e:
        logger.error(f"api_market_pulse_direction error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/market-pulse/query', methods=['POST'])
@login_required
def market_pulse_query():
    """Handle user market queries via Scentric AI on Market Pulse page."""
    try:
        data = request.get_json()
        query = (data or {}).get('message', '').strip()
        if not query or len(query) < 2:
            return jsonify({'error': 'Please enter a valid question.'}), 400
        if len(query) > 2000:
            return jsonify({'error': 'Question is too long (max 2000 chars).'}), 400

        from services.perplexity_api import PerplexityAPI

        # Use in-memory cache — avoids another slow NSE call on every query
        idx_cache  = _market_cache_get('indices', ttl=_CACHE_TTL_INDICES) or {}
        plex_cache = _market_cache_get('perplexity_market', ttl=300) or {}

        def _ix_str(key):
            d = idx_cache.get(key, {}) or {}
            v = d.get('value')
            c = d.get('change_percent')
            if not v:
                return "N/A (data unavailable)"
            cf = float(c or 0)
            return f"{float(v):,.0f} ({'+' if cf >= 0 else ''}{cf:.2f}%)"
        nifty_line  = _ix_str('nifty_50')
        bnifty_line = _ix_str('nifty_bank')
        sensex_line = _ix_str('sensex')

        gainers = plex_cache.get('top_gainers', [])
        losers  = plex_cache.get('top_losers',  [])
        top_g = ', '.join(f"{g.get('symbol','')} (+{float(g.get('change_percent',0)):.1f}%)" for g in gainers[:5])
        top_l = ', '.join(f"{l.get('symbol','')} ({float(l.get('change_percent',0)):.1f}%)"  for l in losers[:5])

        lang_code = getattr(current_user, 'preferred_language', 'en') or 'en'
        lang_name = LANG_NAMES.get(lang_code, 'English')
        lang_instr = (
            f"\n\nIMPORTANT: You must respond entirely in {lang_name}. Every word must be in {lang_name}."
            if lang_code != 'en' else ''
        )

        market_ctx = (
            "You are Scentric AI, an expert Indian market analyst built by Target Capital. "
            "Help retail traders understand markets, stocks, sectors, and strategies. "
            "Be concise, clear, and actionable. Keep responses under 200 words unless the question needs detail. "
            "Never give guaranteed predictions.\n\n"
            f"Today's Indian market snapshot:\n"
            f"• Nifty 50: {nifty_line}\n"
            f"• Bank Nifty: {bnifty_line}\n"
            f"• Sensex: {sensex_line}\n"
            f"• Top gainers today: {top_g or 'N/A'}\n"
            f"• Top losers today: {top_l or 'N/A'}\n\n"
            f"User question: {query}{lang_instr}"
        )

        perplexity = PerplexityAPI()
        text, _ = perplexity.get_investment_advice(market_ctx)

        if text and text.strip():
            return jsonify({'response': text.strip(), 'lang': lang_code})
        else:
            return jsonify({'response': 'I couldn\'t generate a response. Please try rephrasing your question.', 'lang': lang_code})

    except Exception as e:
        logger.error(f"Market pulse query error: {e}")
        return jsonify({'error': 'Something went wrong. Please try again.'}), 500


@app.route('/api/market-pulse/tts', methods=['POST'])
@login_required
def market_pulse_tts():
    """
    Server-side Text-to-Speech using gTTS.
    Converts text to MP3 audio in the user's preferred language.
    Works on all devices and browsers — no OS language pack required.
    """
    try:
        from gtts import gTTS
        import io

        data = request.get_json() or {}
        text = data.get('text', '').strip()

        if not text:
            return jsonify({'error': 'No text provided'}), 400

        # Truncate to keep TTS fast and avoid hitting gTTS limits
        text = text[:2000]

        lang = getattr(current_user, 'preferred_language', 'en') or 'en'

        # Indian regional languages need Google India servers (co.in) for
        # correct audio encoding — the default US (com) server produces
        # a stream Chrome cannot decode for Tamil, Telugu, Kannada, etc.
        INDIAN_LANGS = {'hi', 'ta', 'te', 'mr', 'gu', 'kn'}
        tld = 'co.in' if lang in INDIAN_LANGS else 'com'

        tts = gTTS(text=text, lang=lang, slow=False, tld=tld)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)

        return send_file(
            buf,
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='speech.mp3'
        )

    except Exception as e:
        logger.error(f"gTTS error: {e}")
        return jsonify({'error': 'Could not generate audio. Please try again.'}), 500


@app.route('/dashboard/daily-signals/api')
@login_required
def daily_signals_api():
    selected_date_str = request.args.get('date')
    asset_type = request.args.get('asset_type', 'all')
    duration   = request.args.get('duration', 'all')

    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = _today_ist()
    else:
        selected_date = _today_ist()

    query = DailyTradingSignal.query.filter(DailyTradingSignal.signal_date == selected_date)
    if asset_type != 'all':
        query = query.filter(DailyTradingSignal.asset_type == asset_type)
    if duration != 'all':
        query = query.filter(DailyTradingSignal.trade_duration == duration)

    signals = query.order_by(DailyTradingSignal.signal_number.asc()).all()

    signals_data = [{
        'id': s.id, 'signal_number': s.signal_number,
        'signal_date': s.signal_date.isoformat(),
        'asset_type': s.asset_type, 'sub_type': s.sub_type,
        'symbol': s.symbol, 'script': s.script,
        'strike_price': float(s.strike_price) if s.strike_price else None,
        'strike_type': s.strike_type, 'trade_duration': s.trade_duration,
        'duration_display': s.duration_display, 'action': s.action,
        'buy_above': float(s.buy_above), 'stop_loss': float(s.stop_loss),
        'target_1': float(s.target_1) if s.target_1 else None,
        'target_2': float(s.target_2) if s.target_2 else None,
        'target_3': float(s.target_3) if s.target_3 else None,
        'profit_points': float(s.profit_points) if s.profit_points else 0,
        'loss_points':   float(s.loss_points)   if s.loss_points   else 0,
        'final_points':  float(s.final_points)  if s.final_points  else 0,
        'trade_outcome': s.trade_outcome, 'status': s.status,
        'risk_level': s.risk_level,
        'potential_return_pct': round(s.potential_return_pct, 2),
        'risk_pct': round(s.risk_pct, 2),
        'risk_reward_ratio': round(s.risk_reward_ratio, 2),
        'notes': s.notes, 'formatted_signal': s.formatted_signal,
        'created_at': s.created_at.isoformat() if s.created_at else None,
    } for s in signals]

    return jsonify({
        'signals': signals_data,
        'summary': calculate_daily_summary(selected_date),
        'date': selected_date.isoformat(),
    })


@app.route('/dashboard/daily-signals/<int:signal_id>')
@login_required
def daily_signal_detail(signal_id):
    signal = DailyTradingSignal.query.get_or_404(signal_id)
    return render_template('dashboard/daily_signal_detail.html', signal=signal)


@app.route('/dashboard/daily-signals/analysis')
@login_required
def daily_signals_analysis():
    start_date_str = request.args.get('start_date')
    end_date_str   = request.args.get('end_date')

    start_date = _today_ist() - timedelta(days=30)
    end_date   = _today_ist()

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    signals = DailyTradingSignal.query.filter(
        DailyTradingSignal.signal_date >= start_date,
        DailyTradingSignal.signal_date <= end_date
    ).order_by(DailyTradingSignal.signal_date.desc(), DailyTradingSignal.signal_number.asc()).all()

    analysis_data  = calculate_period_analysis(signals)
    daily_breakdown = {}
    for signal in signals:
        key = signal.signal_date.isoformat()
        if key not in daily_breakdown:
            daily_breakdown[key] = {'date': signal.signal_date, 'total_signals': 0,
                                    'profitable': 0, 'loss': 0, 'total_points': 0}
        daily_breakdown[key]['total_signals'] += 1
        if signal.final_points:
            fp = float(signal.final_points)
            if fp > 0: daily_breakdown[key]['profitable'] += 1
            elif fp < 0: daily_breakdown[key]['loss'] += 1
            daily_breakdown[key]['total_points'] += fp

    return render_template('dashboard/daily_signals_analysis.html',
                           signals=signals, analysis_data=analysis_data,
                           daily_breakdown=list(daily_breakdown.values()),
                           start_date=start_date, end_date=end_date)


def calculate_daily_summary(signal_date):
    signals = DailyTradingSignal.query.filter(DailyTradingSignal.signal_date == signal_date).all()
    if not signals:
        return {'total_signals': 0, 'active': 0, 'target_1_hit': 0, 'target_2_hit': 0,
                'sl_hit': 0, 'early_exit': 0, 'total_profit_points': 0,
                'total_loss_points': 0, 'net_points': 0, 'success_rate': 0,
                'by_asset_type': {}, 'by_duration': {}}

    active       = sum(1 for s in signals if s.status == 'ACTIVE')
    target_1_hit = sum(1 for s in signals if s.trade_outcome and '1st Target' in s.trade_outcome)
    target_2_hit = sum(1 for s in signals if s.trade_outcome and '2nd Target' in s.trade_outcome)
    sl_hit       = sum(1 for s in signals if s.trade_outcome and 'Stop Loss' in s.trade_outcome)
    early_exit   = sum(1 for s in signals if s.trade_outcome and 'Early Exit' in s.trade_outcome)
    total_profit = sum(float(s.profit_points) for s in signals if s.profit_points)
    total_loss   = sum(float(s.loss_points)   for s in signals if s.loss_points)
    net_points   = sum(float(s.final_points)  for s in signals if s.final_points)

    completed = [s for s in signals if s.trade_outcome]
    profitable = sum(1 for s in completed if s.final_points and float(s.final_points) > 0)
    success_rate = (profitable / len(completed) * 100) if completed else 0

    by_asset_type = {}
    by_duration   = {}
    for s in signals:
        by_asset_type[s.asset_type]   = by_asset_type.get(s.asset_type, 0) + 1
        by_duration[s.trade_duration] = by_duration.get(s.trade_duration, 0) + 1

    return {
        'total_signals': len(signals), 'active': active,
        'target_1_hit': target_1_hit, 'target_2_hit': target_2_hit,
        'sl_hit': sl_hit, 'early_exit': early_exit,
        'total_profit_points': round(total_profit, 2),
        'total_loss_points':   round(total_loss, 2),
        'net_points':          round(net_points, 2),
        'success_rate':        round(success_rate, 1),
        'by_asset_type': by_asset_type,
        'by_duration':   by_duration,
    }


def calculate_period_analysis(signals):
    if not signals:
        return {'total_signals': 0, 'total_profit_points': 0, 'total_loss_points': 0,
                'net_points': 0, 'success_rate': 0, 'avg_points_per_trade': 0,
                'best_trade': None, 'worst_trade': None,
                'by_asset_type': {}, 'by_sub_type': {}, 'by_duration': {}}

    total_profit = sum(float(s.profit_points) for s in signals if s.profit_points)
    total_loss   = sum(float(s.loss_points)   for s in signals if s.loss_points)
    net_points   = sum(float(s.final_points)  for s in signals if s.final_points)

    completed  = [s for s in signals if s.trade_outcome and s.final_points]
    profitable = sum(1 for s in completed if float(s.final_points) > 0)
    success_rate = (profitable / len(completed) * 100) if completed else 0
    avg_points   = net_points / len(completed) if completed else 0

    best_trade  = max(completed, key=lambda s: float(s.final_points)) if completed else None
    worst_trade = min(completed, key=lambda s: float(s.final_points)) if completed else None

    by_asset_type = {}
    by_sub_type   = {}
    by_duration   = {}
    for s in signals:
        for d, k in [(by_asset_type, s.asset_type), (by_sub_type, s.sub_type), (by_duration, s.trade_duration)]:
            if k not in d:
                d[k] = {'count': 0, 'net_points': 0}
            d[k]['count'] += 1
            if s.final_points:
                d[k]['net_points'] += float(s.final_points)

    return {
        'total_signals': len(signals),
        'total_profit_points': round(total_profit, 2),
        'total_loss_points':   round(total_loss, 2),
        'net_points':          round(net_points, 2),
        'success_rate':        round(success_rate, 1),
        'avg_points_per_trade': round(avg_points, 2),
        'best_trade': best_trade, 'worst_trade': worst_trade,
        'by_asset_type': by_asset_type,
        'by_sub_type':   by_sub_type,
        'by_duration':   by_duration,
    }
