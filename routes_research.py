"""
Routes for Research & Signals by Asset Type
Provides in-depth research pages for each asset class with I-Score analysis
"""
from flask import render_template, jsonify, request
from flask_login import login_required, current_user
from flask_limiter.util import get_remote_address
from app import app, db, limiter
from decorators import paid_plan_required
from models import TradingSignal, ResearchCache, ResearchRun, ResearchList
from datetime import datetime, timezone, date
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger(__name__)

ASSET_TYPES = {
    'stocks': {
        'name': 'Stocks',
        'title': 'Stocks Research & Signals',
        'subtitle': 'In-depth analysis and trading signals for Indian equities',
        'icon': 'fas fa-chart-bar',
        'description': 'Comprehensive research and real-time signals for NSE and BSE listed stocks.',
        'features': [
            {'icon': 'fas fa-search-dollar', 'title': 'Fundamental Analysis', 'desc': 'Deep-dive into financials, ratios, and company performance'},
            {'icon': 'fas fa-chart-line', 'title': 'Technical Signals', 'desc': 'AI-powered buy/sell signals based on chart patterns'},
            {'icon': 'fas fa-newspaper', 'title': 'Market News', 'desc': 'Real-time news and sentiment analysis'},
            {'icon': 'fas fa-bullseye', 'title': 'Price Targets', 'desc': 'AI-generated price targets with confidence levels'}
        ],
        'market_hours': 'NSE/BSE: 9:15 AM - 3:30 PM IST',
        'signal_type': 'STOCK',
        'example_symbols': 'RELIANCE, TCS, INFY'
    },
    'futures': {
        'name': 'Futures',
        'title': 'Futures Research & Signals',
        'subtitle': 'Advanced analysis for Index and Stock Futures trading',
        'icon': 'fas fa-calendar-alt',
        'description': 'Professional-grade research for NIFTY, BANKNIFTY, and stock futures with expiry-based strategies.',
        'features': [
            {'icon': 'fas fa-layer-group', 'title': 'Open Interest Analysis', 'desc': 'Track OI buildup and unwinding patterns'},
            {'icon': 'fas fa-arrows-alt-v', 'title': 'Rollover Data', 'desc': 'Monthly rollover percentages and trends'},
            {'icon': 'fas fa-balance-scale', 'title': 'Basis & Premium', 'desc': 'Futures premium/discount analysis'},
            {'icon': 'fas fa-clock', 'title': 'Expiry Strategies', 'desc': 'Time-decay aware trading signals'}
        ],
        'market_hours': 'NSE F&O: 9:15 AM - 3:30 PM IST',
        'signal_type': 'FUTURES',
        'example_symbols': 'NIFTY, BANKNIFTY, FINNIFTY'
    },
    'options': {
        'name': 'Options',
        'title': 'Options Research & Signals',
        'subtitle': 'Greeks-based analysis and strategy recommendations',
        'icon': 'fas fa-layer-group',
        'description': 'Sophisticated options analysis with Greeks, IV, and strategy builder for informed trading decisions.',
        'features': [
            {'icon': 'fas fa-chart-area', 'title': 'Implied Volatility', 'desc': 'IV percentile and skew analysis'},
            {'icon': 'fas fa-calculator', 'title': 'Options Greeks', 'desc': 'Delta, Gamma, Theta, Vega tracking'},
            {'icon': 'fas fa-project-diagram', 'title': 'Strategy Builder', 'desc': 'Multi-leg strategy recommendations'},
            {'icon': 'fas fa-fire', 'title': 'Max Pain Analysis', 'desc': 'Weekly max pain levels and trends'}
        ],
        'market_hours': 'NSE F&O: 9:15 AM - 3:30 PM IST',
        'signal_type': 'OPTIONS',
        'example_symbols': 'NIFTY, BANKNIFTY, SENSEX'
    },
    'commodities': {
        'name': 'Commodities',
        'title': 'Commodities Research & Signals',
        'subtitle': 'MCX trading signals for Gold, Silver, Crude and more',
        'icon': 'fas fa-cubes',
        'description': 'Real-time analysis and trading signals for MCX commodities including precious metals and energy.',
        'features': [
            {'icon': 'fas fa-coins', 'title': 'Precious Metals', 'desc': 'Gold, Silver analysis with global cues'},
            {'icon': 'fas fa-oil-can', 'title': 'Energy', 'desc': 'Crude Oil, Natural Gas signals'},
            {'icon': 'fas fa-seedling', 'title': 'Agri Commodities', 'desc': 'Agricultural commodities research'},
            {'icon': 'fas fa-globe', 'title': 'Global Correlation', 'desc': 'International market linkages'}
        ],
        'market_hours': 'MCX: 9:00 AM - 11:30 PM IST',
        'signal_type': 'COMMODITY',
        'example_symbols': 'GOLDM, SILVERM, CRUDEOIL'
    },
    'currency': {
        'name': 'Currency',
        'title': 'Currency Research & Signals',
        'subtitle': 'Forex analysis for INR pairs and cross-currency trading',
        'icon': 'fas fa-rupee-sign',
        'description': 'Expert analysis on USD/INR, EUR/INR and other currency pairs with RBI policy insights.',
        'features': [
            {'icon': 'fas fa-dollar-sign', 'title': 'USD/INR Focus', 'desc': 'Primary pair analysis with technicals'},
            {'icon': 'fas fa-university', 'title': 'RBI Policy', 'desc': 'Central bank intervention tracking'},
            {'icon': 'fas fa-exchange-alt', 'title': 'Cross Rates', 'desc': 'EUR/INR, GBP/INR, JPY/INR'},
            {'icon': 'fas fa-globe-asia', 'title': 'Global FX', 'desc': 'DXY and emerging market correlations'}
        ],
        'market_hours': 'NSE Currency: 9:00 AM - 5:00 PM IST',
        'signal_type': 'CURRENCY',
        'example_symbols': 'USDINR, EURINR, GBPINR'
    },
    'bonds': {
        'name': 'Bonds',
        'title': 'Bonds Research & Signals',
        'subtitle': 'Fixed income analysis for G-Secs and Corporate Bonds',
        'icon': 'fas fa-file-contract',
        'description': 'Yield curve analysis, G-Sec trading signals, and corporate bond recommendations.',
        'features': [
            {'icon': 'fas fa-landmark', 'title': 'G-Sec Analysis', 'desc': 'Government securities yield tracking'},
            {'icon': 'fas fa-chart-line', 'title': 'Yield Curve', 'desc': 'Yield curve shape and movements'},
            {'icon': 'fas fa-building', 'title': 'Corporate Bonds', 'desc': 'Credit rating based recommendations'},
            {'icon': 'fas fa-percentage', 'title': 'Rate Outlook', 'desc': 'RBI rate decision impact analysis'}
        ],
        'market_hours': 'RBI Retail Direct: 9:00 AM - 5:00 PM IST',
        'signal_type': 'BOND',
        'example_symbols': 'GSUMSM, GOVT6, GOVT7'
    },
    'mutual_funds': {
        'name': 'Mutual Funds',
        'title': 'Mutual Funds Research & Signals',
        'subtitle': 'Fund analysis, SIP recommendations and portfolio insights',
        'icon': 'fas fa-piggy-bank',
        'description': 'Comprehensive mutual fund research with category comparisons and SIP timing signals.',
        'features': [
            {'icon': 'fas fa-star', 'title': 'Fund Ratings', 'desc': 'Performance-based fund rankings'},
            {'icon': 'fas fa-sync', 'title': 'SIP Timing', 'desc': 'Optimal SIP entry signals'},
            {'icon': 'fas fa-th-large', 'title': 'Category Analysis', 'desc': 'Sector and thematic fund insights'},
            {'icon': 'fas fa-user-tie', 'title': 'Fund Manager', 'desc': 'Track record and style analysis'}
        ],
        'market_hours': 'AMC: 9:30 AM - 3:00 PM IST (NAV cut-off)',
        'signal_type': 'MF',
        'example_symbols': 'HDFCBANK, ICICIBANK, AXISBANK'
    }
}


def get_signals_for_asset(signal_type):
    """Get active signals for a specific asset type"""
    plan = current_user.pricing_plan
    plan_value = plan.value if hasattr(plan, 'value') else str(plan)
    if plan_value == 'free':
        return []
    
    signals = TradingSignal.query.filter(
        TradingSignal.status == 'ACTIVE',
        TradingSignal.signal_type == signal_type,
        db.or_(
            TradingSignal.expires_at.is_(None),
            TradingSignal.expires_at > datetime.now(timezone.utc)
        )
    ).order_by(TradingSignal.created_at.desc()).limit(10).all()
    
    return signals


@app.route('/dashboard/research')
@login_required
@paid_plan_required
def research_dashboard():
    """Main research dashboard with asset selection and server-side filtering"""
    from models import Tenant, ResearchList
    from sqlalchemy import func

    # ── Tenant feature flags ────────────────────────────────────────────────
    tenant = Tenant.query.get('live')
    allowed_keys = ['stocks', 'mutual_funds', 'commodities']
    if tenant and tenant.config:
        research_config = tenant.config.get('research_co_pilot', {})
        if research_config:
            allowed_keys = [k for k in ASSET_TYPES if research_config.get(f'show_{k}', False)]
    if not allowed_keys:
        allowed_keys = ['stocks']

    # ── Filter / sort params from request ──────────────────────────────────
    q           = request.args.get('q', '').strip()
    sector      = request.args.get('sector', '').strip()
    reco        = request.args.get('reco', '').strip().upper()
    sort_by     = request.args.get('sort', 'iscore_desc')
    quick       = request.args.get('quick', '').strip()   # best_buys | sell_alerts | high_conf | recent | unanalyzed
    page        = max(1, int(request.args.get('page', 1) or 1))
    per_page    = 25

    # ── Base query: only stocks for the main watch list ────────────────────
    base_q = ResearchList.query.filter(
        ResearchList.is_active == True,
        ResearchList.asset_type == 'stocks'
    )

    # ── Quick-filter presets ───────────────────────────────────────────────
    if quick == 'best_buys':
        base_q = base_q.filter(
            ResearchList.recommendation.in_(['STRONG_BUY', 'BUY']),
            ResearchList.i_score >= 60
        )
    elif quick == 'sell_alerts':
        base_q = base_q.filter(
            ResearchList.recommendation.in_(['SELL', 'STRONG_SELL'])
        )
    elif quick == 'high_conf':
        base_q = base_q.filter(
            ResearchList.confidence >= 75,
            ResearchList.i_score.isnot(None)
        )
    elif quick == 'recent':
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        base_q = base_q.filter(ResearchList.last_computed_at >= cutoff)
    elif quick == 'unanalyzed':
        base_q = base_q.filter(ResearchList.i_score.is_(None))

    # ── Text search ────────────────────────────────────────────────────────
    if q:
        like = f'%{q.lower()}%'
        base_q = base_q.filter(
            db.or_(
                db.func.lower(ResearchList.symbol).like(like),
                db.func.lower(ResearchList.company_name).like(like)
            )
        )

    # ── Sector filter ──────────────────────────────────────────────────────
    if sector:
        base_q = base_q.filter(ResearchList.sector == sector)

    # ── Recommendation filter ──────────────────────────────────────────────
    if reco:
        base_q = base_q.filter(ResearchList.recommendation == reco)

    # ── Sort ───────────────────────────────────────────────────────────────
    sort_map = {
        'iscore_desc':  ResearchList.i_score.desc().nullslast(),
        'iscore_asc':   ResearchList.i_score.asc().nullsfirst(),
        'conf_desc':    ResearchList.confidence.desc().nullslast(),
        'symbol_asc':   ResearchList.symbol.asc(),
        'recent':       ResearchList.last_computed_at.desc().nullslast(),
    }
    base_q = base_q.order_by(sort_map.get(sort_by, sort_map['iscore_desc']))

    # ── Paginate ───────────────────────────────────────────────────────────
    pagination     = base_q.paginate(page=page, per_page=per_page, error_out=False)
    research_list  = pagination.items
    total_stocks   = pagination.total

    # ── Sidebar counts (for quick-filter pills) ────────────────────────────
    analyzed_q = ResearchList.query.filter(
        ResearchList.is_active == True,
        ResearchList.asset_type == 'stocks',
        ResearchList.i_score.isnot(None)
    )
    counts = {
        'total':      ResearchList.query.filter_by(is_active=True, asset_type='stocks').count(),
        'best_buys':  analyzed_q.filter(
                          ResearchList.recommendation.in_(['STRONG_BUY', 'BUY']),
                          ResearchList.i_score >= 60
                      ).count(),
        'sell_alerts': analyzed_q.filter(
                          ResearchList.recommendation.in_(['SELL', 'STRONG_SELL'])
                       ).count(),
        'high_conf':  analyzed_q.filter(ResearchList.confidence >= 75).count(),
        'analyzed':   analyzed_q.count(),
        'unanalyzed': ResearchList.query.filter_by(is_active=True, asset_type='stocks').filter(
                          ResearchList.i_score.is_(None)
                      ).count(),
    }

    # ── Available sectors for dropdown ────────────────────────────────────
    sector_rows = db.session.query(
        ResearchList.sector,
        func.count(ResearchList.id).label('cnt')
    ).filter(
        ResearchList.is_active == True,
        ResearchList.asset_type == 'stocks',
        ResearchList.sector.isnot(None)
    ).group_by(ResearchList.sector).order_by(func.count(ResearchList.id).desc()).all()
    sectors = [(row.sector, row.cnt) for row in sector_rows]

    return render_template('dashboard/research/index.html',
                           asset_types=ASSET_TYPES,
                           allowed_assets=allowed_keys,
                           research_list=research_list,
                           pagination=pagination,
                           total_stocks=total_stocks,
                           counts=counts,
                           sectors=sectors,
                           # Active filter state (so template can show active pills)
                           filter_q=q,
                           filter_sector=sector,
                           filter_reco=reco,
                           filter_sort=sort_by,
                           filter_quick=quick)

@app.route('/dashboard/research/stocks')
@login_required
def research_stocks():
    """Stocks research and signals page"""
    asset = ASSET_TYPES['stocks']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='stocks',
                          prefill_symbol=prefill_symbol)


@app.route('/dashboard/research/futures')
@login_required
def research_futures():
    """Futures research and signals page"""
    asset = ASSET_TYPES['futures']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='futures',
                          prefill_symbol=prefill_symbol)


@app.route('/dashboard/research/options')
@login_required
def research_options():
    """Options research and signals page"""
    asset = ASSET_TYPES['options']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='options',
                          prefill_symbol=prefill_symbol)


@app.route('/dashboard/research/commodities')
@login_required
def research_commodities():
    """Commodities research and signals page"""
    asset = ASSET_TYPES['commodities']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='commodities',
                          prefill_symbol=prefill_symbol)


@app.route('/dashboard/research/currency')
@login_required
def research_currency():
    """Currency research and signals page"""
    asset = ASSET_TYPES['currency']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='currency',
                          prefill_symbol=prefill_symbol)


@app.route('/dashboard/research/bonds')
@login_required
def research_bonds():
    """Bonds research and signals page"""
    asset = ASSET_TYPES['bonds']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='bonds',
                          prefill_symbol=prefill_symbol)


@app.route('/dashboard/research/mutual-funds')
@login_required
def research_mutual_funds():
    """Mutual Funds research and signals page"""
    asset = ASSET_TYPES['mutual_funds']
    signals = get_signals_for_asset(asset['signal_type'])
    prefill_symbol = request.args.get('symbol', '').strip().upper()
    return render_template('dashboard/research/asset_research.html', 
                          asset=asset, signals=signals, asset_key='mutual_funds',
                          prefill_symbol=prefill_symbol)


# ============================================================================
# I-SCORE API ENDPOINTS
# ============================================================================

@app.route('/api/research/analyze', methods=['POST'])
@login_required
@limiter.limit("20 per hour", key_func=lambda: f"u{current_user.id}" if current_user.is_authenticated else get_remote_address())
def api_research_analyze():
    """
    Run I-Score analysis on a symbol
    
    Request JSON:
        - symbol: Stock/asset symbol (e.g., 'RELIANCE', 'NIFTY')
        - asset_type: Type of asset (stocks, futures, options, etc.)
    
    Returns:
        - I-Score out of 100
        - Recommendation (STRONG_BUY, BUY, HOLD, CAUTIONARY_SELL, STRONG_SELL)
        - Component scores and details
    """
    try:
        plan = current_user.pricing_plan
        plan_value = plan.value if hasattr(plan, 'value') else str(plan)
        if plan_value == 'free':
            return jsonify({
                'success': False,
                'error': 'I-Score analysis requires Target Plus subscription or higher'
            }), 403
        
        data = request.get_json(silent=True, force=True)
        if not data:
            return jsonify({'success': False, 'error': 'No data provided — send JSON body with symbol and asset_type'}), 400
        
        symbol = data.get('symbol', '').upper().strip()
        asset_type = data.get('asset_type', 'stocks').lower()
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        
        if asset_type not in ASSET_TYPES:
            return jsonify({'success': False, 'error': f'Invalid asset type: {asset_type}'}), 400

        # ── Pre-flight: required env vars (catches missing keys on deploy) ─
        missing_keys = [k for k in ('OPENAI_API_KEY',) if not os.environ.get(k)]
        if missing_keys:
            msg = (
                f"Research Co-Pilot is mis-configured on this server: "
                f"missing environment variable(s) {', '.join(missing_keys)}. "
                "Please set them in your hosting platform (e.g. Railway → Variables) and redeploy."
            )
            logger.error(msg)
            return jsonify({'success': False, 'error': msg}), 503

        # ── Check shared ResearchCache first (saves API costs + time) ──────
        import hashlib
        today_str = date.today().isoformat()
        cache_key = hashlib.md5(
            f"iscore:{asset_type}:{symbol}:{today_str}".encode()
        ).hexdigest()

        cached = ResearchCache.query.filter_by(
            cache_key=cache_key,
            is_valid=True
        ).first()
        if cached and cached.expires_at > datetime.utcnow():
            cached.hit_count += 1
            cached.last_hit_at = datetime.utcnow()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            payload = dict(cached.result_payload or {})
            # Defensive: cache only ever stores successful results, but older
            # rows may pre-date the `success` field. Always mark cached hits
            # as successful so the frontend doesn't show "Analysis failed".
            payload['success'] = True
            payload.setdefault('symbol', symbol)
            payload.setdefault('asset_type', asset_type)
            payload.setdefault('iscore', cached.overall_score or 0)
            payload.setdefault('recommendation', cached.recommendation or 'HOLD')
            payload['_from_cache'] = True
            payload['_cached_at']  = cached.computed_at.isoformat() if cached.computed_at else today_str
            # Backfill holding_period for cache rows written before the field existed
            if not payload.get('holding_period'):
                try:
                    from services.iscore.holding_period import compute_holding_period as _chp
                    comps = payload.get('components', {})
                    payload['holding_period'] = _chp(
                        recommendation=payload.get('recommendation', 'HOLD'),
                        overall_score=float(payload.get('iscore') or payload.get('overall_score') or 50),
                        risk_score=float((comps.get('risk') or {}).get('score') or 50),
                        trend_score=float((comps.get('trend') or {}).get('score') or 50),
                        quantitative_score=float((comps.get('quantitative') or {}).get('score') or 50),
                        is_stock=(asset_type == 'stocks'),
                    )
                except Exception:
                    pass
            logger.info(
                f"Returning cached I-Score for {symbol} "
                f"(hits={cached.hit_count}, size={len(str(payload))}b, "
                f"has_components={'components' in payload}, "
                f"has_holding_period={'holding_period' in payload})"
            )
            db.session.close()
            return jsonify(payload)

        from services.langgraph_iscore_engine import LangGraphIScoreEngine
        
        engine = LangGraphIScoreEngine()
        result = engine.analyze(
            asset_type=asset_type,
            symbol=symbol,
            user_id=current_user.id,
            asset_name=symbol
        )

        # ── Persist result to ResearchList so the Watch List stays fresh ──
        if result and result.get('success'):
            try:
                components = result.get('components', {})
                market     = result.get('market_data', {})

                mapped = {
                    'overall_score':        result.get('iscore', 0),
                    'overall_confidence':   result.get('confidence', 0),
                    'recommendation':       result.get('recommendation', 'HOLD'),
                    'recommendation_summary': result.get('summary', ''),
                    'qualitative_score':       components.get('qualitative', {}).get('score', 0),
                    'quantitative_score':      components.get('quantitative', {}).get('score', 0),
                    'search_score':            components.get('search',       {}).get('score', 0),
                    'trend_score':             components.get('trend',        {}).get('score', 0),
                    'risk_score':              components.get('risk', {}).get('score') if components.get('risk') else None,
                    'market_context_score':    components.get('market_context', {}).get('score') if components.get('market_context') else None,
                    'qualitative_details':     components.get('qualitative', {}).get('details', {}),
                    'quantitative_details':    components.get('quantitative', {}).get('details', {}),
                    'search_details':          components.get('search',       {}).get('details', {}),
                    'trend_details':           components.get('trend',        {}).get('details', {}),
                    'risk_details':            components.get('risk', {}).get('details') if components.get('risk') else None,
                    'market_context_details':  components.get('market_context', {}).get('details') if components.get('market_context') else None,
                    'current_price':           market.get('current_price'),
                    'previous_close':          market.get('previous_close'),
                    'price_change_pct':        market.get('change_pct'),
                    'data_source':             result.get('data_source', ''),
                }

                entry = ResearchList.query.filter_by(symbol=symbol).first()
                if entry is None:
                    entry = ResearchList(
                        symbol=symbol,
                        asset_type=asset_type,
                        tenant_id='live',
                        is_active=True,
                    )
                    db.session.add(entry)

                entry.update_from_iscore_result(mapped)
                entry.computation_source  = 'real_time'
                entry.last_requested_at   = datetime.utcnow()
                db.session.flush()   # flush before cache write

                # ── Save to shared ResearchCache (expires at midnight tonight) ──
                # Any other user who requests the same symbol today will get
                # this result instantly without re-running expensive AI calls.
                expires = datetime.combine(date.today(), datetime.max.time()).replace(microsecond=0)
                existing_cache = ResearchCache.query.filter_by(cache_key=cache_key).first()
                if existing_cache is None:
                    existing_cache = ResearchCache(
                        cache_key=cache_key,
                        tenant_id='live',
                        asset_type=asset_type,
                        symbol=symbol,
                        analysis_date=date.today(),
                    )
                    db.session.add(existing_cache)
                existing_cache.result_payload   = result
                existing_cache.overall_score    = mapped['overall_score']
                existing_cache.recommendation   = mapped['recommendation']
                existing_cache.computed_at      = datetime.utcnow()
                existing_cache.expires_at       = expires
                existing_cache.is_valid         = True

                db.session.commit()
                logger.info(
                    f"ResearchList + ResearchCache saved for {symbol}: "
                    f"I-Score={mapped['overall_score']}, expires={expires.isoformat()}"
                )

            except Exception as save_err:
                db.session.rollback()
                logger.warning(f"Could not persist ResearchList/Cache for {symbol}: {save_err}")
                # Non-fatal — still return result to user

        # Stamp the fresh result with the server-side computed time (UTC ISO)
        # so the frontend can show a consistent IST timestamp in both the
        # "Data as of" header and the info banner.
        if result and result.get('success'):
            result['_computed_at'] = datetime.utcnow().isoformat()
        return jsonify(result)
        
    except Exception as e:
        db.session.rollback()
        # Surface the actual root cause so production users (e.g. Railway)
        # can see *why* the analysis failed (missing API key, broker token
        # expired, upstream rate limit, etc.) instead of a generic message.
        import traceback as _tb
        err_type = type(e).__name__
        err_msg  = str(e) or 'no detail'
        sym = symbol if 'symbol' in dir() and symbol else 'unknown'
        logger.error(
            f"I-Score analysis error for {sym}: {err_type}: {err_msg}\n"
            f"{_tb.format_exc()}"
        )
        # Friendly hint for the most common Railway cause
        hint = ''
        low = err_msg.lower()
        if 'api key' in low or 'apikey' in low or 'authentication' in low or 'unauthorized' in low:
            hint = ' Hint: check that OPENAI_API_KEY (and PERPLEXITY_API_KEY) are set on your server.'
        elif 'timeout' in low or 'timed out' in low:
            hint = ' Hint: upstream data provider timed out — try again in a few seconds.'
        return jsonify({
            'success': False,
            'error': f'Analysis failed ({err_type}): {err_msg}.{hint}'
        }), 500


@app.route('/api/research/cached/<symbol>')
@login_required
def api_research_cached(symbol):
    """
    Return an existing I-Score for `symbol` without running fresh analysis.
    Priority:
      1. ResearchCache row valid today  → full result_payload
      2. ResearchList master entry       → reconstruct compatible payload
      3. Nothing found                   → {cached: False}
    The JS caller uses data.result to feed displayIScoreResult() directly.
    """
    try:
        plan = current_user.pricing_plan
        plan_value = plan.value if hasattr(plan, 'value') else str(plan)
        if plan_value == 'free':
            return jsonify({'success': False, 'cached': False}), 403

        symbol = symbol.upper().strip()
        asset_type = request.args.get('asset_type', 'stocks')

        # ── 1. Today's ResearchCache (full payload stored after last analysis) ─
        import hashlib
        cache_key = hashlib.md5(
            f"iscore:{asset_type}:{symbol}:{date.today().isoformat()}".encode()
        ).hexdigest()
        cached = ResearchCache.query.filter_by(cache_key=cache_key, is_valid=True).first()
        if cached and cached.expires_at > datetime.utcnow() and cached.result_payload:
            cached.hit_count += 1
            cached.last_hit_at = datetime.utcnow()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            payload = dict(cached.result_payload) if isinstance(cached.result_payload, dict) else cached.result_payload
            cached_at_iso = cached.computed_at.isoformat() if cached.computed_at else None
            if isinstance(payload, dict):
                md = dict(payload.get('market_data') or {})
                if not md.get('timestamp') and cached_at_iso:
                    md['timestamp'] = cached_at_iso
                    payload['market_data'] = md
                # Older cache rows pre-date holding_period — recompute if missing
                if not payload.get('holding_period'):
                    try:
                        from services.iscore.holding_period import compute_holding_period as _chp
                        comps = payload.get('components', {})
                        payload['holding_period'] = _chp(
                            recommendation=payload.get('recommendation', 'HOLD'),
                            overall_score=float(payload.get('iscore') or payload.get('overall_score') or 50),
                            risk_score=float((comps.get('risk') or {}).get('score') or 50),
                            trend_score=float((comps.get('trend') or {}).get('score') or 50),
                            quantitative_score=float((comps.get('quantitative') or {}).get('score') or 50),
                            is_stock=(asset_type == 'stocks'),
                        )
                    except Exception:
                        pass
            db.session.close()
            return jsonify({
                'success': True,
                'cached': True,
                'cached_at': cached_at_iso,
                'result': payload,
            })

        # ── 2. ResearchList master entry (any vintage) ────────────────────────
        rl = ResearchList.query.filter_by(symbol=symbol, is_active=True).first()
        if rl and rl.i_score is not None:
            computed_at = rl.last_computed_at or rl.updated_at
            # Recompute holding period from stored scores (ResearchList only
            # stores period/label/days, not the full rich dict the UI needs)
            rl_holding_period = None
            try:
                from services.iscore.holding_period import compute_holding_period as _chp
                rl_holding_period = _chp(
                    recommendation=rl.recommendation or 'HOLD',
                    overall_score=float(rl.i_score or 50),
                    risk_score=float(rl.risk_score or 50) if hasattr(rl, 'risk_score') and rl.risk_score else 50,
                    trend_score=float(rl.trend_score or 50),
                    quantitative_score=float(rl.quantitative_score or 50),
                    is_stock=(asset_type == 'stocks'),
                )
            except Exception:
                pass
            # Build a response shape compatible with displayIScoreResult()
            payload = {
                'success': True,
                'symbol': symbol,
                'asset_name': rl.company_name or symbol,
                'iscore': float(rl.i_score),
                'confidence': float(rl.confidence) if rl.confidence else 0,
                'recommendation': rl.recommendation or 'HOLD',
                'summary': rl.recommendation_summary or '',
                'holding_period': rl_holding_period,
                'components': {
                    'qualitative':  {'score': float(rl.qualitative_score or 0),  'details': rl.qualitative_details or {}},
                    'quantitative': {'score': float(rl.quantitative_score or 0), 'details': rl.quantitative_details or {}},
                    'search':       {'score': float(rl.search_score or 0),       'details': rl.search_details or {}},
                    'trend':        {'score': float(rl.trend_score or 0),        'details': rl.trend_details or {}},
                },
                'market_data': {
                    'current_price':  float(rl.current_price) if rl.current_price else None,
                    'previous_close': float(rl.previous_close) if rl.previous_close else None,
                    'change_pct':     float(rl.price_change_pct) if rl.price_change_pct else None,
                    'timestamp':      computed_at.isoformat() if computed_at else None,
                },
                'data_source': rl.hist_data_source or '',
                '_from_cache': True,
            }
            db.session.close()
            return jsonify({
                'success': True,
                'cached': True,
                'cached_at': computed_at.isoformat() if computed_at else None,
                'result': payload,
            })

        db.session.close()
        return jsonify({'success': True, 'cached': False, 'symbol': symbol})

    except Exception as e:
        db.session.rollback()
        logger.error(f"I-Score cache lookup error for {symbol}: {e}")
        return jsonify({'success': True, 'cached': False, 'symbol': symbol})


@app.route('/api/research/recent')
@login_required
def api_research_recent():
    """Get recent I-Score analyses for the current user"""
    try:
        plan = current_user.pricing_plan
        plan_value = plan.value if hasattr(plan, 'value') else str(plan)
        if plan_value == 'free':
            return jsonify({'success': False, 'analyses': []}), 403
        
        recent = ResearchRun.query.filter_by(
            user_id=current_user.id,
            status='completed'
        ).order_by(ResearchRun.created_at.desc()).limit(10).all()
        
        analyses = []
        for run in recent:
            analyses.append({
                'id': run.id,
                'symbol': run.symbol,
                'asset_type': run.asset_type,
                'iscore': float(run.overall_score) if run.overall_score else 0,
                'recommendation': run.recommendation,
                'summary': run.recommendation_summary,
                'analyzed_at': run.created_at.isoformat() if run.created_at else None
            })
        
        return jsonify({'success': True, 'analyses': analyses})
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"I-Score recent lookup error: {e}")
        return jsonify({'success': True, 'analyses': []})


@app.route('/api/research/thresholds')
@login_required
def api_research_thresholds():
    """Get current I-Score thresholds for display"""
    try:
        from models import ResearchThresholdConfig
        
        config = ResearchThresholdConfig.get_active_config()
        
        if config:
            return jsonify({
                'success': True,
                'thresholds': {
                    'strong_buy': config.strong_buy_threshold,
                    'buy': config.buy_threshold,
                    'hold_low': config.hold_low,
                    'hold_high': config.hold_high,
                    'sell': config.sell_threshold,
                    'min_confidence': float(config.min_confidence)
                }
            })
        
        return jsonify({
            'success': True,
            'thresholds': {
                'strong_buy': 70,
                'buy': 56,
                'hold_low': 40,
                'hold_high': 55,
                'sell': 28,
                'min_confidence': 0.38
            }
        })
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"I-Score thresholds lookup error: {e}")
        return jsonify({
            'success': True,
            'thresholds': {
                'strong_buy': 70,
                'buy': 56,
                'hold_low': 40,
                'hold_high': 55,
                'sell': 28,
                'min_confidence': 0.38
            }
        })


# ── User Watchlist I-Score Refresh ───────────────────────────────────────────
_user_refresh_jobs = {}   # job_id -> progress dict


@app.route('/api/research/refresh-watchlist-iscores', methods=['POST'])
@login_required
def refresh_watchlist_iscores():
    """
    Start a background I-Score refresh for the current user's watchlist symbols
    that exist in the shared ResearchList table.
    Returns a job_id for progress polling.
    """
    from models import WatchlistItem
    try:
        watchlist_symbols = [
            w.symbol.upper() for w in
            WatchlistItem.query.filter_by(user_id=current_user.id).all()
        ]
    except Exception:
        watchlist_symbols = []

    if not watchlist_symbols:
        return jsonify({'success': False, 'message': 'Your watchlist is empty. Add stocks first.'})

    # Find ResearchList entries for these symbols
    stocks = ResearchList.query.filter(
        ResearchList.symbol.in_(watchlist_symbols),
        ResearchList.is_active == True
    ).all()

    if not stocks:
        return jsonify({
            'success': False,
            'message': 'None of your watchlist symbols are in the Research List yet.'
        })

    job_id = str(uuid.uuid4())[:8]
    _user_refresh_jobs[job_id] = {
        'status': 'running',
        'total': len(stocks),
        'done': 0,
        'success': 0,
        'errors': 0,
        'current_symbol': '',
        'log': [],
        'finished_at': None,
    }

    from app import app as flask_app
    stock_ids = [s.id for s in stocks]

    def _run_refresh(flask_app, jid, ids):
        with flask_app.app_context():
            try:
                from services.langgraph_iscore_engine import LangGraphIScoreEngine
                engine = LangGraphIScoreEngine()
            except Exception as e:
                _user_refresh_jobs[jid]['status'] = 'failed'
                _user_refresh_jobs[jid]['log'].append(f'Engine error: {e}')
                return

            for idx, sid in enumerate(ids):
                try:
                    stock = ResearchList.query.get(sid)
                    if not stock:
                        continue
                    _user_refresh_jobs[jid]['current_symbol'] = stock.symbol
                    result = engine.analyze(
                        asset_type=stock.asset_type,
                        symbol=stock.symbol,
                        user_id=current_user.id,
                        asset_name=stock.company_name or stock.symbol
                    )
                    if result and result.get('success'):
                        components = result.get('components', {})
                        market = result.get('market_data', {})
                        mapped = {
                            'overall_score': result.get('iscore', 0),
                            'overall_confidence': result.get('confidence', 0),
                            'recommendation': result.get('recommendation', 'HOLD'),
                            'recommendation_summary': result.get('summary', ''),
                            'qualitative_score': components.get('qualitative', {}).get('score', 0),
                            'quantitative_score': components.get('quantitative', {}).get('score', 0),
                            'search_score': components.get('search', {}).get('score', 0),
                            'trend_score': components.get('trend', {}).get('score', 0),
                            'qualitative_details': components.get('qualitative', {}).get('details', {}),
                            'quantitative_details': components.get('quantitative', {}).get('details', {}),
                            'search_details': components.get('search', {}).get('details', {}),
                            'trend_details': components.get('trend', {}).get('details', {}),
                            'current_price': market.get('current_price'),
                            'previous_close': market.get('previous_close'),
                            'price_change_pct': market.get('change_pct'),
                            'data_source': result.get('data_source', ''),
                        }
                        stock.update_from_iscore_result(mapped)
                        stock.computation_source = 'user_refresh'
                        db.session.commit()
                        _user_refresh_jobs[jid]['success'] += 1
                        _user_refresh_jobs[jid]['log'].append(f'✓ {stock.symbol}: {result.get("iscore", 0):.1f}')
                    else:
                        err = (result or {}).get('error', 'No result')
                        _user_refresh_jobs[jid]['errors'] += 1
                        _user_refresh_jobs[jid]['log'].append(f'✗ {stock.symbol}: {err}')
                except Exception as ex:
                    db.session.rollback()
                    _user_refresh_jobs[jid]['errors'] += 1
                    _user_refresh_jobs[jid]['log'].append(f'✗ error: {ex}')
                finally:
                    _user_refresh_jobs[jid]['done'] = idx + 1
                    time.sleep(1.5)

            _user_refresh_jobs[jid]['status'] = 'completed'
            _user_refresh_jobs[jid]['finished_at'] = datetime.utcnow().isoformat()
            _user_refresh_jobs[jid]['current_symbol'] = ''

    t = threading.Thread(target=_run_refresh, args=(flask_app, job_id, stock_ids), daemon=True)
    t.start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'total': len(stock_ids),
        'message': f'Refreshing I-Scores for {len(stock_ids)} stock(s) in your watchlist.',
    })


@app.route('/api/research/refresh-watchlist-iscores/status', methods=['GET'])
@login_required
def refresh_watchlist_iscores_status():
    """Poll progress of a user watchlist I-Score refresh job."""
    job_id = request.args.get('job_id', '')
    if not job_id or job_id not in _user_refresh_jobs:
        return jsonify({'found': False})
    job = _user_refresh_jobs[job_id]
    pct = round(job['done'] / max(job['total'], 1) * 100)
    return jsonify({
        'found': True,
        'status': job['status'],
        'total': job['total'],
        'done': job['done'],
        'success': job['success'],
        'errors': job['errors'],
        'pct': pct,
        'current_symbol': job.get('current_symbol', ''),
        'recent_log': job['log'][-5:],
        'finished_at': job.get('finished_at'),
    })


@app.route('/api/research/email-report', methods=['POST'])
@login_required
def api_research_email_report():
    """Email the last cached I-Score report for a symbol to the logged-in user."""
    try:
        data       = request.get_json(silent=True) or {}
        symbol     = (request.form.get('symbol') or data.get('symbol', '')).upper().strip()
        asset_type = request.form.get('asset_type') or data.get('asset_type', 'stocks')
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400
        if not current_user.email:
            return jsonify({'success': False, 'error': 'No email on your account'}), 400

        import hashlib
        cache_key = hashlib.md5(
            f"iscore:{asset_type}:{symbol}:{date.today().isoformat()}".encode()
        ).hexdigest()
        cached = ResearchCache.query.filter_by(cache_key=cache_key, is_valid=True).first()
        if cached and cached.result_payload:
            result = dict(cached.result_payload) if isinstance(cached.result_payload, dict) else cached.result_payload
        else:
            rl = ResearchList.query.filter_by(symbol=symbol).first()
            if not rl or not rl.overall_score:
                return jsonify({'success': False, 'error': 'No I-Score found. Run analysis first.'}), 404
            result = {
                'iscore': rl.overall_score,
                'recommendation': rl.recommendation or 'HOLD',
                'summary': rl.recommendation_summary or '',
                'components': {
                    'quantitative':   {'score': rl.quantitative_score},
                    'trend':          {'score': rl.trend_score},
                    'risk':           {'score': rl.risk_score},
                    'qualitative':    {'score': rl.qualitative_score},
                    'search':         {'score': rl.search_score},
                    'market_context': {'score': rl.market_context_score},
                },
            }

        from services.email_service import send_iscore_report_email
        ok = send_iscore_report_email(current_user.email, symbol, result)
        if ok:
            return jsonify({'success': True, 'message': f'Report emailed to {current_user.email}'})
        return jsonify({'success': False, 'error': 'Email delivery failed — check mail configuration'}), 500

    except Exception as e:
        logger.error(f"Email report error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
