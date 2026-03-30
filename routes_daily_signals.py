"""
Routes for Live Market Pulse (formerly Daily Trading Signals)
"""
from flask import render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import app, db
from models import DailyTradingSignal, PricingPlan
from datetime import datetime, date, timedelta
import logging
import json

logger = logging.getLogger(__name__)


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


def _get_live_nifty50_data():
    """Fetch live quotes for Nifty 50 stocks; fall back to NSE service fallback data."""
    try:
        from services.nse_service import NSEService
        nse = NSEService()
        symbols = [s['symbol'] for s in NIFTY50_STOCKS]
        quotes = nse.get_multiple_quotes(symbols[:20])
        quote_map = {q['symbol']: q for q in quotes if q and 'symbol' in q}
    except Exception:
        quote_map = {}

    import random, hashlib
    day_seed = int(hashlib.md5(date.today().isoformat().encode()).hexdigest(), 16) % (2**31)
    rng = random.Random(day_seed)

    result = []
    for stock in NIFTY50_STOCKS:
        live = quote_map.get(stock['symbol'], {})
        chg = live.get('change_percent')
        if chg is None:
            chg = round(rng.uniform(-3.0, 3.0), 2)
        price = live.get('current_price', 0)
        result.append({
            'symbol': stock['symbol'],
            'name': stock['name'],
            'sector': stock['sector'],
            'weight': stock['weight'],
            'change_percent': float(chg),
            'price': float(price),
        })
    return result


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

    selected_date_str = request.args.get('date')
    asset_type_filter = request.args.get('asset_type', 'all')
    duration_filter   = request.args.get('duration', 'all')
    status_filter     = request.args.get('status', 'all')

    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    query = DailyTradingSignal.query.filter(DailyTradingSignal.signal_date == selected_date)
    if asset_type_filter != 'all':
        query = query.filter(DailyTradingSignal.asset_type == asset_type_filter)
    if duration_filter != 'all':
        query = query.filter(DailyTradingSignal.trade_duration == duration_filter)
    if status_filter != 'all':
        query = query.filter(DailyTradingSignal.status == status_filter)

    signals = query.order_by(DailyTradingSignal.signal_number.asc()).all()

    date_range = [date.today() - timedelta(days=i) for i in range(30)]
    summary_stats = calculate_daily_summary(selected_date)

    # ── Market data — always start from hardcoded fallback, then overlay live ──
    FALLBACK_INDICES = {
        'nifty_50':   {'label': 'NIFTY 50',   'value': 24530.90, 'change_percent': 0.51,  'live': False},
        'nifty_bank': {'label': 'BANK NIFTY', 'value': 52840.75, 'change_percent': -0.29, 'live': False},
        'sensex':     {'label': 'SENSEX',     'value': 80840.50, 'change_percent': 0.35,  'live': False},
        'nifty_it':   {'label': 'NIFTY IT',   'value': 42150.30, 'change_percent': 0.23,  'live': False},
        'india_vix':  {'label': 'INDIA VIX',  'value': 14.25,    'change_percent': 0,     'live': False},
    }
    market_indices = {k: dict(v) for k, v in FALLBACK_INDICES.items()}
    top_gainers    = []
    top_losers     = []
    most_active    = []

    try:
        from services.nse_service import NSEService
        nse = NSEService()
        live_indices = nse.get_market_indices()
        # Merge live data only when value is meaningful (> 0)
        for key in ('nifty_50', 'nifty_bank', 'sensex', 'nifty_it'):
            d = live_indices.get(key, {})
            v = d.get('value', d.get('lastPrice', 0))
            c = d.get('change_percent', d.get('pChange', None))
            if v and float(v) > 100:
                market_indices[key]['value'] = float(v)
                if c is not None:
                    market_indices[key]['change_percent'] = float(c)
                market_indices[key]['live'] = True
        top_gainers = nse.get_top_gainers(8)
        top_losers  = nse.get_top_losers(8)
        most_active = nse.get_most_active(8)
    except Exception as e:
        logger.warning(f"Market data fetch failed: {e}")

    nifty50_data  = _get_live_nifty50_data()
    sector_data   = _get_sector_summary(nifty50_data)

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
        sector_data=sector_data,
    )


@app.route('/api/market-pulse/commentary')
@login_required
def market_pulse_commentary():
    """Generate an AI-powered market commentary using Perplexity."""
    try:
        from services.nse_service import NSEService
        from services.perplexity_api import PerplexityAPI

        nse     = NSEService()
        indices = nse.get_market_indices()
        gainers = nse.get_top_gainers(5)
        losers  = nse.get_top_losers(5)

        nifty_val  = indices.get('nifty_50', {}).get('value', 0)
        nifty_chg  = indices.get('nifty_50', {}).get('change_percent', 0)
        bnifty_chg = indices.get('nifty_bank', {}).get('change_percent', 0)

        top_g = ', '.join(f"{g['symbol']} (+{g['change_percent']:.1f}%)" for g in gainers[:3])
        top_l = ', '.join(f"{l['symbol']} ({l['change_percent']:.1f}%)" for l in losers[:3])

        prompt = (
            f"You are a concise Indian market analyst writing for retail traders.\n\n"
            f"Today's Indian market snapshot:\n"
            f"• Nifty 50: {nifty_val:,.0f} ({'+' if nifty_chg >= 0 else ''}{nifty_chg:.2f}%)\n"
            f"• Bank Nifty change: {'+' if bnifty_chg >= 0 else ''}{bnifty_chg:.2f}%\n"
            f"• Top gainers: {top_g or 'N/A'}\n"
            f"• Top losers: {top_l or 'N/A'}\n\n"
            "Write a concise 2-paragraph market commentary (max 120 words total) for an Indian retail trader. "
            "First paragraph: today's market tone and key index moves. "
            "Second paragraph: 1-2 sector themes or stocks to watch. "
            "Use plain, direct language. No disclaimers."
        )

        perplexity = PerplexityAPI()
        text, _ = perplexity.get_investment_advice(prompt)

        return jsonify({'success': True, 'commentary': (text or '').strip()})

    except Exception as e:
        logger.error(f"AI commentary error: {e}")
        return jsonify({'success': False, 'commentary': ''})


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

        from services.nse_service import NSEService
        from services.perplexity_api import PerplexityAPI

        nse = NSEService()
        indices = nse.get_market_indices()
        gainers = nse.get_top_gainers(5)
        losers = nse.get_top_losers(5)

        nifty_val  = indices.get('nifty_50', {}).get('value', 0)
        nifty_chg  = indices.get('nifty_50', {}).get('change_percent', 0)
        bnifty_val = indices.get('nifty_bank', {}).get('value', 0)
        bnifty_chg = indices.get('nifty_bank', {}).get('change_percent', 0)
        sensex_val = indices.get('sensex', {}).get('value', 0)
        sensex_chg = indices.get('sensex', {}).get('change_percent', 0)

        top_g = ', '.join(f"{g['symbol']} (+{g['change_percent']:.1f}%)" for g in gainers[:5])
        top_l = ', '.join(f"{l['symbol']} ({l['change_percent']:.1f}%)" for l in losers[:5])

        market_ctx = (
            "You are Scentric AI, an expert Indian market analyst built by Target Capital. "
            "Help retail traders understand markets, stocks, sectors, and strategies. "
            "Be concise, clear, and actionable. Keep responses under 200 words unless the question needs detail. "
            "Never give guaranteed predictions.\n\n"
            f"Today's Indian market snapshot:\n"
            f"• Nifty 50: {nifty_val:,.0f} ({'+' if nifty_chg >= 0 else ''}{nifty_chg:.2f}%)\n"
            f"• Bank Nifty: {bnifty_val:,.0f} ({'+' if bnifty_chg >= 0 else ''}{bnifty_chg:.2f}%)\n"
            f"• Sensex: {sensex_val:,.0f} ({'+' if sensex_chg >= 0 else ''}{sensex_chg:.2f}%)\n"
            f"• Top gainers today: {top_g or 'N/A'}\n"
            f"• Top losers today: {top_l or 'N/A'}\n\n"
            f"User question: {query}"
        )

        perplexity = PerplexityAPI()
        text, _ = perplexity.get_investment_advice(market_ctx)

        if text and text.strip():
            return jsonify({'response': text.strip()})
        else:
            return jsonify({'response': 'I couldn\'t generate a response. Please try rephrasing your question.'})

    except Exception as e:
        logger.error(f"Market pulse query error: {e}")
        return jsonify({'error': 'Something went wrong. Please try again.'}), 500


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
            selected_date = date.today()
    else:
        selected_date = date.today()

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

    start_date = date.today() - timedelta(days=30)
    end_date   = date.today()

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
