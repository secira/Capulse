"""
Admin Routes for Target Capital Trading Platform
Separate admin module with authentication and management features
"""

import os
import threading
import time
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from datetime import datetime, timedelta
from sqlalchemy import func, desc
from app import db
from models import Admin, User, PricingPlan, DailyTradingSignal, ResearchList, BlogPost, ContactMessage
from models_broker import BrokerAccount

# ── Batch I-Score Job State ──────────────────────────────────────────────────
# Simple in-memory tracker (survives single server restart scenarios)
_batch_jobs = {}   # job_id -> {status, total, done, errors, started_at, finished_at}
# Import with safe fallback for optional models
try:
    from models import TradingSignal, UserPayment, ExecutedTrade
    TRADING_SIGNALS_AVAILABLE = True
except ImportError:
    TRADING_SIGNALS_AVAILABLE = False
    TradingSignal = UserPayment = ExecutedTrade = None

# Create admin blueprint
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Admin authentication decorator
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def admin_decorated_function(*args, **kwargs):
        if current_user.is_authenticated and current_user.is_admin:
            return f(*args, **kwargs)
        if 'admin_id' not in session:
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return admin_decorated_function

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Try finding in database first
        admin = Admin.query.filter_by(username=username, active=True).first()
        
        if admin and admin.check_password(password):
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            admin.last_login = datetime.utcnow()
            db.session.commit()
            
            flash('Welcome to Target Capital Admin Dashboard!', 'success')
            return redirect(url_for('admin.dashboard'))
            
        # Fallback to hardcoded credentials if not in database
        ADMIN_CREDENTIALS = {
            'admin': os.environ.get('ADMIN_PASSWORD', 'admin123'),
            'tcapital_admin': os.environ.get('ADMIN_PASSWORD', 'tcapital2025')
        }
        if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
            session['admin_id'] = 0  # Special ID for hardcoded admin
            session['admin_username'] = username
            flash('Logged in via emergency credentials', 'warning')
            return redirect(url_for('admin.dashboard'))
            
        flash('Invalid credentials. Please try again.', 'error')
    
    return render_template('admin/login.html')

@admin_bp.route('/logout')
@admin_required
def logout():
    """Admin logout"""
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('admin.login'))

@admin_bp.route('/')
@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Admin dashboard with overview statistics"""
    # Get statistics for dashboard
    total_users = User.query.count()
    active_signals = TradingSignal.query.filter_by(status='ACTIVE').count()
    today_payments = UserPayment.query.filter(
        func.date(UserPayment.created_at) == datetime.utcnow().date(),
        UserPayment.status == 'COMPLETED'
    ).count()
    
    # Recent trading signals
    recent_signals = TradingSignal.query.order_by(desc(TradingSignal.created_at)).limit(5).all()
    
    # Payment summary for current month
    current_month = datetime.utcnow().replace(day=1)
    monthly_revenue = db.session.query(func.sum(UserPayment.amount)).filter(
        UserPayment.created_at >= current_month,
        UserPayment.status == 'COMPLETED'
    ).scalar() or 0
    
    return render_template('admin/dashboard.html',
                         total_users=total_users,
                         active_signals=active_signals,
                         today_payments=today_payments,
                         recent_signals=recent_signals,
                         monthly_revenue=monthly_revenue)

@admin_bp.route('/users')
@admin_bp.route('/users/<int:page>')
@admin_required
def users(page=1):
    """User management page"""
    per_page = 20
    users = User.query.paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('admin/users.html', users=users)

@admin_bp.route('/user/<int:user_id>')
@admin_required
def user_detail(user_id):
    """User detail page with payment and trade history"""
    user = User.query.get_or_404(user_id)
    payments = UserPayment.query.filter_by(user_id=user_id).order_by(desc(UserPayment.created_at)).limit(10).all()
    executed_trades = ExecutedTrade.query.filter_by(user_id=user_id).order_by(desc(ExecutedTrade.executed_at)).limit(10).all()
    brokers = BrokerAccount.query.filter_by(user_id=user_id).all()
    
    return render_template('admin/user_detail.html',
                         user=user,
                         payments=payments,
                         executed_trades=executed_trades,
                         brokers=brokers)

@admin_bp.route('/user/<int:user_id>/update-plan', methods=['POST'])
@admin_required
def update_user_plan(user_id):
    """Update a user's subscription plan"""
    user = User.query.get_or_404(user_id)
    new_plan = request.form.get('pricing_plan')
    valid_plans = ['free', 'target_plus', 'target_pro', 'hni']
    if new_plan and new_plan.lower() in valid_plans:
        from models import PricingPlan
        plan_map = {
            'free': PricingPlan.FREE,
            'target_plus': PricingPlan.TARGET_PLUS,
            'target_pro': PricingPlan.TARGET_PRO,
            'hni': PricingPlan.HNI,
        }
        user.pricing_plan = plan_map[new_plan.lower()]
        db.session.commit()
        flash(f'Plan updated to {new_plan} for {user.username}.', 'success')
    else:
        flash('Invalid plan selected.', 'danger')
    return redirect(url_for('admin.user_detail', user_id=user_id))


@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete a user account permanently"""
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('Admin accounts cannot be deleted.', 'danger')
        return redirect(url_for('admin.users'))
    username = user.username or user.email
    try:
        db.session.delete(user)
        db.session.commit()
        flash(f'User "{username}" has been permanently deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not delete user: {str(e)}', 'danger')
    return redirect(url_for('admin.users'))


@admin_bp.route('/research-list')
@admin_bp.route('/research-list/<int:page>')
@admin_required
def research_list(page=1):
    """Research List management - Pre-computed I-Score for top 500 stocks"""
    per_page = 25
    asset_type_filter = request.args.get('asset_type', '')
    recommendation_filter = request.args.get('recommendation', '')
    search_query = request.args.get('search', '')
    
    query = ResearchList.query.filter_by(is_active=True)
    
    if asset_type_filter:
        query = query.filter(ResearchList.asset_type == asset_type_filter)
    if recommendation_filter:
        query = query.filter(ResearchList.recommendation == recommendation_filter)
    if search_query:
        query = query.filter(
            (ResearchList.symbol.ilike(f'%{search_query}%')) | 
            (ResearchList.company_name.ilike(f'%{search_query}%'))
        )
    
    stocks = query.order_by(ResearchList.i_score.desc().nullslast()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    total_count = ResearchList.query.filter_by(is_active=True).count()
    analyzed_count = ResearchList.query.filter(ResearchList.i_score.isnot(None), ResearchList.is_active==True).count()
    
    return render_template('admin/research_list.html', 
                          stocks=stocks,
                          total_count=total_count,
                          analyzed_count=analyzed_count,
                          asset_type_filter=asset_type_filter,
                          recommendation_filter=recommendation_filter,
                          search_query=search_query)

@admin_bp.route('/research-list/add', methods=['GET', 'POST'])
@admin_required
def add_research_stock():
    """Add new stock to Research List"""
    if request.method == 'POST':
        try:
            symbol = request.form.get('symbol', '').upper().strip()
            
            existing = ResearchList.query.filter_by(symbol=symbol).first()
            if existing:
                flash(f'{symbol} already exists in Research List!', 'warning')
                return redirect(url_for('admin.research_list'))
            
            stock = ResearchList(
                symbol=symbol,
                company_name=request.form.get('company_name', ''),
                asset_type=request.form.get('asset_type', 'stocks'),
                sector=request.form.get('sector', ''),
                is_active=True,
                tenant_id='live'
            )
            
            db.session.add(stock)
            db.session.commit()
            
            flash(f'{symbol} added to Research List! Click "Compute I-Score" to analyze.', 'success')
            return redirect(url_for('admin.research_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding stock: {str(e)}', 'error')
    
    return render_template('admin/add_research_stock.html')

@admin_bp.route('/research-list/<int:stock_id>/refresh', methods=['POST'])
@admin_required
def refresh_research_stock(stock_id):
    """Refresh I-Score for a stock using LangGraph engine"""
    stock = ResearchList.query.get_or_404(stock_id)
    
    try:
        from services.langgraph_iscore_engine import LangGraphIScoreEngine
        engine = LangGraphIScoreEngine()
        
        result = engine.analyze(
            asset_type=stock.asset_type,
            symbol=stock.symbol,
            user_id=1,
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
            }
            stock.update_from_iscore_result(mapped)
            stock.computation_source = 'manual'
            db.session.commit()
            flash(f'I-Score updated for {stock.symbol}: {result.get("iscore", 0):.1f}', 'success')
        else:
            error_msg = result.get('error', 'Analysis returned no data') if result else 'Engine returned no result'
            flash(f'Could not compute I-Score for {stock.symbol}: {error_msg}', 'warning')
            
    except Exception as e:
        db.session.rollback()
        flash(f'Error computing I-Score: {str(e)}', 'error')
    
    return redirect(url_for('admin.research_list'))

@admin_bp.route('/research-list/batch-iscore', methods=['POST'])
@admin_required
def batch_iscore():
    """Start a background job to compute I-Score for all Research List stocks."""
    mode = request.form.get('mode', 'pending')   # 'pending' = unanalyzed only, 'all' = force refresh

    from app import app as flask_app
    stocks_to_run = ResearchList.query.filter_by(is_active=True).all()
    if mode == 'pending':
        stocks_to_run = [s for s in stocks_to_run if s.i_score is None]

    if not stocks_to_run:
        return jsonify({'success': False, 'message': 'No stocks to process.'})

    job_id = str(uuid.uuid4())[:8]
    _batch_jobs[job_id] = {
        'status': 'running',
        'mode': mode,
        'total': len(stocks_to_run),
        'done': 0,
        'success': 0,
        'errors': 0,
        'current_symbol': '',
        'started_at': datetime.utcnow().isoformat(),
        'finished_at': None,
        'log': [],
    }

    stock_ids = [s.id for s in stocks_to_run]

    def _run_batch(app, jid, ids):
        with app.app_context():
            try:
                from services.langgraph_iscore_engine import LangGraphIScoreEngine
                engine = LangGraphIScoreEngine()
            except Exception as e:
                _batch_jobs[jid]['status'] = 'failed'
                _batch_jobs[jid]['log'].append(f'Engine init error: {e}')
                return

            for idx, sid in enumerate(ids):
                try:
                    stock = ResearchList.query.get(sid)
                    if not stock:
                        continue
                    _batch_jobs[jid]['current_symbol'] = stock.symbol
                    result = engine.analyze(
                        asset_type=stock.asset_type,
                        symbol=stock.symbol,
                        user_id=1,
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
                        }
                        stock.update_from_iscore_result(mapped)
                        stock.computation_source = 'batch'
                        db.session.commit()
                        _batch_jobs[jid]['success'] += 1
                        _batch_jobs[jid]['log'].append(f'✓ {stock.symbol}: {result.get("iscore", 0):.1f}')
                    else:
                        err = (result or {}).get('error', 'No result')
                        _batch_jobs[jid]['errors'] += 1
                        _batch_jobs[jid]['log'].append(f'✗ {stock.symbol}: {err}')
                except Exception as ex:
                    db.session.rollback()
                    _batch_jobs[jid]['errors'] += 1
                    _batch_jobs[jid]['log'].append(f'✗ {stock.symbol}: {ex}')
                finally:
                    _batch_jobs[jid]['done'] = idx + 1
                    time.sleep(1.5)   # polite delay between API calls

            _batch_jobs[jid]['status'] = 'completed'
            _batch_jobs[jid]['finished_at'] = datetime.utcnow().isoformat()
            _batch_jobs[jid]['current_symbol'] = ''

    t = threading.Thread(target=_run_batch, args=(flask_app, job_id, stock_ids), daemon=True)
    t.start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'total': len(stock_ids),
        'message': f'Batch job started — processing {len(stock_ids)} stock(s).',
    })


@admin_bp.route('/research-list/batch-iscore/status', methods=['GET'])
@admin_required
def batch_iscore_status():
    """Poll status of a running batch I-Score job."""
    job_id = request.args.get('job_id', '')
    if not job_id or job_id not in _batch_jobs:
        return jsonify({'found': False})
    job = _batch_jobs[job_id]
    pct = round(job['done'] / max(job['total'], 1) * 100)
    recent_log = job['log'][-5:]  # last 5 entries
    return jsonify({
        'found': True,
        'status': job['status'],
        'total': job['total'],
        'done': job['done'],
        'success': job['success'],
        'errors': job['errors'],
        'pct': pct,
        'current_symbol': job.get('current_symbol', ''),
        'recent_log': recent_log,
        'finished_at': job.get('finished_at'),
    })


@admin_bp.route('/research-list/<int:stock_id>/details')
@admin_required
def research_stock_details(stock_id):
    """Get detailed I-Score breakdown for a stock (AJAX)"""
    stock = ResearchList.query.get_or_404(stock_id)
    
    return jsonify({
        'symbol': stock.symbol,
        'company_name': stock.company_name,
        'asset_type': stock.asset_type,
        'sector': stock.sector,
        'i_score': float(stock.i_score) if stock.i_score else None,
        'recommendation': stock.recommendation,
        'confidence': float(stock.confidence) if stock.confidence else None,
        'qualitative_score': float(stock.qualitative_score) if stock.qualitative_score else None,
        'quantitative_score': float(stock.quantitative_score) if stock.quantitative_score else None,
        'search_score': float(stock.search_score) if stock.search_score else None,
        'trend_score': float(stock.trend_score) if stock.trend_score else None,
        'qualitative_details': stock.qualitative_details or {},
        'quantitative_details': stock.quantitative_details or {},
        'search_details': stock.search_details or {},
        'trend_details': stock.trend_details or {},
        'current_price': float(stock.current_price) if stock.current_price else None,
        'previous_close': float(stock.previous_close) if stock.previous_close else None,
        'price_change_pct': float(stock.price_change_pct) if stock.price_change_pct else None,
        'recommendation_summary': stock.recommendation_summary,
        'last_computed_at': stock.last_computed_at.isoformat() if stock.last_computed_at else None,
        'score_age_hours': stock.score_age_hours,
        'is_stale': stock.is_stale
    })

@admin_bp.route('/research-list/<int:stock_id>/delete', methods=['POST'])
@admin_required
def delete_research_stock(stock_id):
    """Delete stock from Research List"""
    stock = ResearchList.query.get_or_404(stock_id)
    
    try:
        db.session.delete(stock)
        db.session.commit()
        flash(f'{stock.symbol} removed from Research List!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing stock: {str(e)}', 'error')
    
    return redirect(url_for('admin.research_list'))

@admin_bp.route('/payments')
@admin_bp.route('/payments/<view_type>')
@admin_required
def payments(view_type='daily'):
    """Payment management with daily/weekly/monthly views"""
    today = datetime.utcnow().date()
    
    if view_type == 'weekly':
        start_date = today - timedelta(days=7)
    elif view_type == 'monthly':
        start_date = today.replace(day=1)
    else:  # daily
        start_date = today
    
    payments = UserPayment.query.filter(
        func.date(UserPayment.created_at) >= start_date
    ).order_by(desc(UserPayment.created_at)).all()
    
    # Calculate summary
    total_amount = sum(p.amount for p in payments if p.status == 'COMPLETED')
    total_count = len([p for p in payments if p.status == 'COMPLETED'])
    
    return render_template('admin/payments.html',
                         payments=payments,
                         view_type=view_type,
                         total_amount=total_amount,
                         total_count=total_count)

@admin_bp.route('/account-handling')
@admin_bp.route('/account-handling/<int:page>')
@admin_required
def account_handling(page=1):
    """Premium user account handling with P&L tracking"""
    per_page = 20
    
    # Get premium users (Target Pro and HNI plans)
    premium_users = User.query.filter(
        User.pricing_plan.in_(['TARGET_PRO', 'HNI'])
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    # Calculate daily P&L for each user
    today = datetime.utcnow().date()
    user_pnl = {}
    
    for user in premium_users.items:
        trades_today = ExecutedTrade.query.filter(
            ExecutedTrade.user_id == user.id,
            func.date(ExecutedTrade.executed_at) == today
        ).all()
        
        daily_pnl = sum(trade.unrealized_pnl + trade.realized_pnl for trade in trades_today)
        user_pnl[user.id] = {
            'daily_pnl': daily_pnl,
            'trades_count': len(trades_today)
        }
    
    return render_template('admin/account_handling.html',
                         premium_users=premium_users,
                         user_pnl=user_pnl)

# API endpoints for admin dashboard
@admin_bp.route('/api/signals/today')
@admin_required
def api_signals_today():
    """API endpoint for today's signals"""
    today = datetime.utcnow().date()
    signals = TradingSignal.query.filter(
        func.date(TradingSignal.created_at) == today
    ).all()
    
    return jsonify([{
        'id': s.id,
        'symbol': s.symbol,
        'action': s.action,
        'signal_type': s.signal_type,
        'created_at': s.created_at.isoformat()
    } for s in signals])

@admin_bp.route('/api/share-signal/<int:signal_id>', methods=['POST'])
@admin_required
def api_share_signal(signal_id):
    """API endpoint to share signal to WhatsApp/Telegram"""
    signal = TradingSignal.query.get_or_404(signal_id)
    platform = request.json.get('platform')  # 'whatsapp' or 'telegram'
    
    try:
        if platform == 'whatsapp':
            signal.shared_whatsapp = True
            signal.whatsapp_shared_at = datetime.utcnow()
            message = "Signal shared to WhatsApp successfully!"
            
        elif platform == 'telegram':
            signal.shared_telegram = True
            signal.telegram_shared_at = datetime.utcnow()
            message = "Signal shared to Telegram successfully!"
        else:
            return jsonify({'success': False, 'message': 'Invalid platform'}), 400
        
        db.session.commit()
        return jsonify({'success': True, 'message': message})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================================
# I-SCORE RESEARCH CONFIGURATION
# ============================================================================

@admin_bp.route('/research-config')
@admin_required
def research_config():
    """I-Score research configuration page"""
    from models import ResearchWeightConfig, ResearchThresholdConfig, Tenant
    
    tenant = Tenant.query.get('live')
    research_flags = tenant.config.get('research_co_pilot', {}) if tenant and tenant.config else {}
    portfolio_flags = tenant.config.get('portfolio_hub', {}) if tenant and tenant.config else {}
    
    # Define all possible asset keys from routes_research
    from routes_research import ASSET_TYPES
    all_asset_keys = ASSET_TYPES.keys()
    
    # Portfolio hub sections
    portfolio_sections = [
        'banks', 'insurance', 'equities', 'mutual_funds', 
        'fixed_deposits', 'futures_options', 'real_estate', 'commodities'
    ]
    
    weight_config = ResearchWeightConfig.get_active_config() or ResearchWeightConfig()
    threshold_config = ResearchThresholdConfig.get_active_config() or ResearchThresholdConfig()
    
    tech_params = weight_config.tech_params or {
        'rsi_period': 14,
        'rsi_overbought': 70,
        'rsi_oversold': 30,
        'supertrend_period': 10,
        'supertrend_multiplier': 3,
        'ema_short': 9,
        'ema_long': 20
    }
    
    trend_params = weight_config.trend_params or {
        'oi_change_threshold': 5,
        'pcr_bullish_threshold': 0.7,
        'pcr_bearish_threshold': 1.3,
        'vix_low': 15,
        'vix_high': 25
    }
    
    qualitative_sources = weight_config.qualitative_sources or {
        'annual_reports': True,
        'twitter': True,
        'moneycontrol': True,
        'economic_times': True,
        'nse_india': True,
        'bse_india': True,
        'screener': True,
        'glassdoor': True
    }
    
    config_history = ResearchWeightConfig.query.order_by(
        ResearchWeightConfig.created_at.desc()
    ).limit(10).all()

    return render_template('admin/research_config.html',
                          weight_config=weight_config,
                          threshold_config=threshold_config,
                          tech_params=tech_params,
                          trend_params=trend_params,
                          qualitative_sources=qualitative_sources,
                          research_flags=research_flags,
                          portfolio_flags=portfolio_flags,
                          portfolio_sections=portfolio_sections,
                          all_asset_keys=all_asset_keys,
                          config_history=config_history)

@admin_bp.route('/save-portfolio-flags', methods=['POST'])
@admin_required
def save_portfolio_flags():
    """Save asset visibility flags for Portfolio Hub"""
    from models import Tenant
    
    # Portfolio hub sections mapping
    PORTFOLIO_SECTIONS = [
        'banks', 'insurance', 'equities', 'mutual_funds', 
        'fixed_deposits', 'futures_options', 'real_estate', 'commodities'
    ]
    
    try:
        tenant = Tenant.query.get('live')
        if not tenant:
            flash('Default tenant not found', 'error')
            return redirect(url_for('admin.research_config'))
            
        new_flags = {}
        for key in PORTFOLIO_SECTIONS:
            new_flags[f'show_{key}'] = (request.form.get(f'show_{key}') == 'on')
            
        if not tenant.config:
            tenant.config = {}
            
        # Ensure deep update
        config = dict(tenant.config)
        config['portfolio_hub'] = new_flags
        tenant.config = config
        
        db.session.commit()
        flash('Portfolio Hub visibility updated!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating flags: {str(e)}', 'error')
        
    return redirect(url_for('admin.research_config'))

@admin_bp.route('/save-research-flags', methods=['POST'])
@admin_required
def save_research_flags():
    """Save asset visibility flags for Research Co-Pilot"""
    from models import Tenant
    from routes_research import ASSET_TYPES
    
    try:
        tenant = Tenant.query.get('live')
        if not tenant:
            flash('Default tenant not found', 'error')
            return redirect(url_for('admin.research_config'))
            
        new_flags = {}
        for key in ASSET_TYPES.keys():
            new_flags[f'show_{key}'] = (request.form.get(f'show_{key}') == 'on')
            
        if not tenant.config:
            tenant.config = {}
            
        # Ensure deep update
        config = dict(tenant.config)
        config['research_co_pilot'] = new_flags
        tenant.config = config
        
        db.session.commit()
        flash('Research asset visibility updated!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating flags: {str(e)}', 'error')
        
    return redirect(url_for('admin.research_config'))


@admin_bp.route('/save-research-weights', methods=['POST'])
@admin_required
def admin_save_research_weights():
    """Weights are now hardcoded as proprietary IP — admin editing disabled."""
    flash('I-Score weights are now proprietary and hardcoded (Quant 30%, Trend 20%, Risk 20%, Qual 15%, Search 10%, Market 5%). Admin editing has been disabled.', 'info')
    return redirect(url_for('admin.research_config'))


@admin_bp.route('/save-tech-params', methods=['POST'])
@admin_required
def admin_save_tech_params():
    """Save technical indicator parameters"""
    from models import ResearchWeightConfig
    from sqlalchemy.orm.attributes import flag_modified
    
    try:
        tech_params = {
            'rsi_period': int(request.form.get('rsi_period', 14)),
            'rsi_overbought': int(request.form.get('rsi_overbought', 70)),
            'rsi_oversold': int(request.form.get('rsi_oversold', 30)),
            'supertrend_period': int(request.form.get('supertrend_period', 10)),
            'supertrend_multiplier': float(request.form.get('supertrend_multiplier', 3)),
            'ema_short': int(request.form.get('ema_short', 9)),
            'ema_long': int(request.form.get('ema_long', 20))
        }
        
        config = ResearchWeightConfig.get_active_config()
        if config:
            config.tech_params = tech_params
            flag_modified(config, 'tech_params')
            config.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Technical indicator parameters saved!', 'success')
        else:
            new_config = ResearchWeightConfig(
                tech_params=tech_params,
                is_active=True
            )
            db.session.add(new_config)
            db.session.commit()
            flash('Technical indicator parameters saved!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving parameters: {str(e)}', 'error')
    
    return redirect(url_for('admin.research_config'))


@admin_bp.route('/save-threshold-config', methods=['POST'])
@admin_required
def admin_save_threshold_config():
    """Save I-Score recommendation thresholds"""
    from models import ResearchThresholdConfig
    
    try:
        strong_buy = int(request.form.get('strong_buy_threshold', 80))
        buy = int(request.form.get('buy_threshold', 65))
        hold_low = int(request.form.get('hold_low', 45))
        hold_high = int(request.form.get('hold_high', 64))
        sell = int(request.form.get('sell_threshold', 30))
        min_confidence = float(request.form.get('min_confidence', 60)) / 100.0
        
        existing = ResearchThresholdConfig.get_active_config()
        if existing:
            existing.strong_buy_threshold = strong_buy
            existing.buy_threshold = buy
            existing.hold_low = hold_low
            existing.hold_high = hold_high
            existing.sell_threshold = sell
            existing.min_confidence = min_confidence
            existing.updated_at = datetime.utcnow()
        else:
            new_config = ResearchThresholdConfig(
                strong_buy_threshold=strong_buy,
                buy_threshold=buy,
                hold_low=hold_low,
                hold_high=hold_high,
                sell_threshold=sell,
                min_confidence=min_confidence,
                is_active=True,
                created_by=session.get('admin_id')
            )
            db.session.add(new_config)
        
        db.session.commit()
        flash('I-Score thresholds saved successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving thresholds: {str(e)}', 'error')
    
    return redirect(url_for('admin.research_config'))


@admin_bp.route('/save-qualitative-sources', methods=['POST'])
@admin_required
def admin_save_qualitative_sources():
    """Save qualitative data source configuration"""
    from models import ResearchWeightConfig
    
    try:
        sources = {
            'annual_reports': 'annual_reports' in request.form,
            'twitter': 'twitter' in request.form,
            'moneycontrol': 'moneycontrol' in request.form,
            'economic_times': 'economic_times' in request.form,
            'nse_india': 'nse_india' in request.form,
            'bse_india': 'bse_india' in request.form,
            'screener': 'screener' in request.form,
            'glassdoor': 'glassdoor' in request.form,
            'zerodha_varsity': 'zerodha_varsity' in request.form,
            'investing_com': 'investing_com' in request.form,
            'stockedge': 'stockedge' in request.form,
            'groww': 'groww' in request.form,
            'upstox': 'upstox' in request.form,
            'angelone': 'angelone' in request.form
        }
        
        from sqlalchemy.orm.attributes import flag_modified
        config = ResearchWeightConfig.get_active_config()
        if config:
            config.qualitative_sources = sources
            flag_modified(config, 'qualitative_sources')
            config.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Qualitative data sources saved!', 'success')
        else:
            new_config = ResearchWeightConfig(
                qualitative_sources=sources,
                is_active=True
            )
            db.session.add(new_config)
            db.session.commit()
            flash('Qualitative data sources saved!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving sources: {str(e)}', 'error')
    
    return redirect(url_for('admin.research_config'))


@admin_bp.route('/save-trend-params', methods=['POST'])
@admin_required
def admin_save_trend_params():
    """Save trend analysis parameters"""
    from models import ResearchWeightConfig
    
    try:
        trend_params = {
            'oi_change_threshold': float(request.form.get('oi_change_threshold', 5)),
            'pcr_bullish_threshold': float(request.form.get('pcr_bullish_threshold', 0.7)),
            'pcr_bearish_threshold': float(request.form.get('pcr_bearish_threshold', 1.3)),
            'vix_low': float(request.form.get('vix_low', 15)),
            'vix_high': float(request.form.get('vix_high', 25))
        }
        
        from sqlalchemy.orm.attributes import flag_modified
        config = ResearchWeightConfig.get_active_config()
        if config:
            config.trend_params = trend_params
            flag_modified(config, 'trend_params')
            config.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Trend analysis parameters saved!', 'success')
        else:
            new_config = ResearchWeightConfig(
                trend_params=trend_params,
                is_active=True
            )
            db.session.add(new_config)
            db.session.commit()
            flash('Trend analysis parameters saved!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving parameters: {str(e)}', 'error')
    
    return redirect(url_for('admin.research_config'))


# ============================================================================
# DAILY TRADING SIGNALS MANAGEMENT
# ============================================================================

@admin_bp.route('/daily-signals')
@admin_bp.route('/daily-signals/<int:page>')
@admin_required
def daily_signals(page=1):
    """List and manage daily trading signals"""
    per_page = 20
    
    # Get filter parameters
    date_filter = request.args.get('date')
    asset_type_filter = request.args.get('asset_type', 'all')
    status_filter = request.args.get('status', 'all')
    
    # Build query
    query = DailyTradingSignal.query
    
    if date_filter:
        from datetime import datetime as dt
        try:
            filter_date = dt.strptime(date_filter, '%Y-%m-%d').date()
            query = query.filter(DailyTradingSignal.signal_date == filter_date)
        except ValueError:
            pass
    
    if asset_type_filter != 'all':
        query = query.filter(DailyTradingSignal.asset_type == asset_type_filter)
    
    if status_filter != 'all':
        query = query.filter(DailyTradingSignal.status == status_filter)
    
    # Order by date descending, then signal number
    signals = query.order_by(
        desc(DailyTradingSignal.signal_date),
        DailyTradingSignal.signal_number
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('admin/daily_signals.html',
                          signals=signals,
                          date_filter=date_filter,
                          asset_type_filter=asset_type_filter,
                          status_filter=status_filter)


@admin_bp.route('/daily-signals/add', methods=['GET', 'POST'])
@admin_required
def add_daily_signal():
    """Add new daily trading signal"""
    if request.method == 'POST':
        try:
            # Parse form data
            signal_date_str = request.form.get('signal_date')
            signal_date = datetime.strptime(signal_date_str, '%Y-%m-%d').date() if signal_date_str else datetime.utcnow().date()
            
            # Get next signal number for this date
            last_signal = DailyTradingSignal.query.filter_by(signal_date=signal_date).order_by(
                desc(DailyTradingSignal.signal_number)
            ).first()
            signal_number = (last_signal.signal_number + 1) if last_signal else 1
            
            # Build script name based on asset type
            asset_type = request.form.get('asset_type')
            sub_type = request.form.get('sub_type')
            symbol = request.form.get('symbol', '').upper()
            strike_price = request.form.get('strike_price')
            
            if asset_type in ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY']:
                if sub_type in ['CE', 'PE']:
                    script = f"{asset_type}-{strike_price}-{sub_type}"
                else:
                    script = f"{asset_type}-FUT"
            else:
                if sub_type in ['CE', 'PE']:
                    script = f"{symbol}-{strike_price}-{sub_type}"
                elif sub_type == 'FUT':
                    script = f"{symbol}-FUT"
                else:
                    script = symbol
            
            # Create new signal
            new_signal = DailyTradingSignal(
                signal_number=signal_number,
                signal_date=signal_date,
                asset_type=asset_type,
                sub_type=sub_type,
                symbol=symbol if asset_type == 'STOCK' else asset_type,
                strike_price=float(strike_price) if strike_price else None,
                strike_type=request.form.get('strike_type'),
                script=script,
                trade_duration=request.form.get('trade_duration'),
                action=request.form.get('action', 'BUY'),
                current_price=float(request.form.get('current_price')) if request.form.get('current_price') else None,
                buy_above=float(request.form.get('buy_above')),
                stop_loss=float(request.form.get('stop_loss')),
                target_1=float(request.form.get('target_1')) if request.form.get('target_1') else None,
                target_2=float(request.form.get('target_2')) if request.form.get('target_2') else None,
                target_3=float(request.form.get('target_3')) if request.form.get('target_3') else None,
                risk_level=request.form.get('risk_level', 'MEDIUM'),
                strategy_name=request.form.get('strategy_name', 'Trend Following'),
                notes=request.form.get('notes'),
                created_by=session.get('admin_id'),
                analyst_name=session.get('admin_username'),
                status='ACTIVE'
            )
            
            db.session.add(new_signal)
            db.session.commit()
            
            flash(f'Daily Signal #{signal_number} created successfully for {signal_date}!', 'success')
            return redirect(url_for('admin.daily_signals'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating signal: {str(e)}', 'error')
    
    # Get today's date for default
    today = datetime.utcnow().date()
    
    return render_template('admin/add_daily_signal.html', today=today)


@admin_bp.route('/daily-signals/edit/<int:signal_id>', methods=['GET', 'POST'])
@admin_required
def edit_daily_signal(signal_id):
    """Edit existing daily trading signal"""
    signal = DailyTradingSignal.query.get_or_404(signal_id)
    
    if request.method == 'POST':
        try:
            # Update signal fields
            signal.asset_type = request.form.get('asset_type')
            signal.sub_type = request.form.get('sub_type')
            signal.symbol = request.form.get('symbol', '').upper()
            signal.strike_price = float(request.form.get('strike_price')) if request.form.get('strike_price') else None
            signal.strike_type = request.form.get('strike_type')
            signal.trade_duration = request.form.get('trade_duration')
            signal.action = request.form.get('action', 'BUY')
            signal.current_price = float(request.form.get('current_price')) if request.form.get('current_price') else signal.current_price
            signal.buy_above = float(request.form.get('buy_above'))
            signal.stop_loss = float(request.form.get('stop_loss'))
            signal.target_1 = float(request.form.get('target_1')) if request.form.get('target_1') else None
            signal.target_2 = float(request.form.get('target_2')) if request.form.get('target_2') else None
            signal.target_3 = float(request.form.get('target_3')) if request.form.get('target_3') else None
            signal.risk_level = request.form.get('risk_level', 'MEDIUM')
            signal.strategy_name = request.form.get('strategy_name', 'Trend Following')
            signal.notes = request.form.get('notes')
            signal.status = request.form.get('status', signal.status)
            
            # Rebuild script name
            asset_type = signal.asset_type
            sub_type = signal.sub_type
            strike_price = signal.strike_price
            symbol = signal.symbol
            
            if asset_type in ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY']:
                if sub_type in ['CE', 'PE']:
                    signal.script = f"{asset_type}-{strike_price}-{sub_type}"
                else:
                    signal.script = f"{asset_type}-FUT"
            else:
                if sub_type in ['CE', 'PE']:
                    signal.script = f"{symbol}-{strike_price}-{sub_type}"
                elif sub_type == 'FUT':
                    signal.script = f"{symbol}-FUT"
                else:
                    signal.script = symbol
            
            db.session.commit()
            flash(f'Signal #{signal.signal_number} updated successfully!', 'success')
            return redirect(url_for('admin.daily_signals'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating signal: {str(e)}', 'error')
    
    return render_template('admin/edit_daily_signal.html', signal=signal)


@admin_bp.route('/daily-signals/delete/<int:signal_id>', methods=['POST'])
@admin_required
def delete_daily_signal(signal_id):
    """Delete a daily trading signal"""
    signal = DailyTradingSignal.query.get_or_404(signal_id)
    
    try:
        db.session.delete(signal)
        db.session.commit()
        flash(f'Signal #{signal.signal_number} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting signal: {str(e)}', 'error')
    
    return redirect(url_for('admin.daily_signals'))


@admin_bp.route('/daily-signals/update-status/<int:signal_id>', methods=['POST'])
@admin_required
def update_signal_status(signal_id):
    """Update signal status (target hit, stop loss, etc.)"""
    signal = DailyTradingSignal.query.get_or_404(signal_id)
    
    try:
        new_status = request.form.get('status')
        trade_outcome = request.form.get('trade_outcome')
        profit_points = request.form.get('profit_points')
        loss_points = request.form.get('loss_points')
        
        signal.status = new_status
        signal.trade_outcome = trade_outcome
        
        if profit_points:
            signal.profit_points = float(profit_points)
        if loss_points:
            signal.loss_points = float(loss_points)
        
        if new_status in ['TARGET_1_HIT', 'TARGET_2_HIT', 'SL_HIT', 'CLOSED', 'EXPIRED']:
            signal.closed_at = datetime.utcnow()
        
        db.session.commit()
        flash(f'Signal #{signal.signal_number} status updated to {new_status}!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating status: {str(e)}', 'error')
    
    return redirect(url_for('admin.daily_signals'))


# ============================================================================
# CONTACT MESSAGES
# ============================================================================

@admin_bp.route('/contact-messages')
@admin_required
def contact_messages():
    """Admin page to view and manage contact messages"""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'all')

    query = ContactMessage.query

    if status_filter != 'all':
        query = query.filter_by(status=status_filter)

    messages = query.order_by(ContactMessage.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    stats = {
        'total': ContactMessage.query.count(),
        'new': ContactMessage.query.filter_by(status='new').count(),
        'read': ContactMessage.query.filter_by(status='read').count(),
        'replied': ContactMessage.query.filter_by(status='replied').count(),
        'closed': ContactMessage.query.filter_by(status='closed').count(),
    }

    return render_template('admin/contact_messages.html',
                           messages=messages,
                           stats=stats,
                           status_filter=status_filter)


@admin_bp.route('/contact-message/<int:message_id>')
@admin_required
def view_contact_message(message_id):
    """View individual contact message"""
    message = ContactMessage.query.get_or_404(message_id)

    if message.status == 'new':
        message.status = 'read'
        db.session.commit()

    return render_template('admin/view_contact_message.html', message=message)


@admin_bp.route('/contact-message/<int:message_id>/update-status', methods=['POST'])
@admin_required
def update_contact_status(message_id):
    """Update contact message status"""
    message = ContactMessage.query.get_or_404(message_id)
    new_status = request.form.get('status')

    if new_status in ['new', 'read', 'replied', 'closed']:
        message.status = new_status
        if new_status == 'replied':
            message.replied_at = datetime.utcnow()
        db.session.commit()
        flash(f'Message status updated to {new_status}.', 'success')

    return redirect(url_for('admin.contact_messages'))


@admin_bp.route('/contact-message/<int:message_id>/delete', methods=['POST'])
@admin_required
def delete_contact_message(message_id):
    """Delete a contact message permanently"""
    message = ContactMessage.query.get_or_404(message_id)
    try:
        db.session.delete(message)
        db.session.commit()
        flash('Contact message deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not delete message: {str(e)}', 'danger')
    return redirect(url_for('admin.contact_messages'))


# ============================================================================
# RESEARCH LIST SEEDING
# ============================================================================

@admin_bp.route('/seed-research-list', methods=['GET', 'POST'])
@admin_required
def seed_research_list():
    """Seed the research list with 501 stocks — safe to run on Railway"""
    from seed_data import RESEARCH_LIST_STOCKS

    current_count = ResearchList.query.filter_by(is_active=True).count()

    if request.method == 'POST':
        try:
            action = request.form.get('action', 'merge')

            if action == 'replace':
                # Remove all existing records first
                ResearchList.query.delete()
                db.session.flush()

            inserted = 0
            skipped = 0
            for stock in RESEARCH_LIST_STOCKS:
                symbol = stock['symbol']
                existing = ResearchList.query.filter_by(symbol=symbol).first()
                if existing:
                    # Update basic fields without touching i_score
                    existing.company_name = stock['company_name']
                    existing.asset_type = stock['asset_type']
                    existing.sector = stock['sector']
                    existing.is_active = True
                    existing.tenant_id = 'live'
                    skipped += 1
                else:
                    new_stock = ResearchList(
                        symbol=symbol,
                        company_name=stock['company_name'],
                        asset_type=stock['asset_type'],
                        sector=stock['sector'],
                        is_active=True,
                        tenant_id='live',
                    )
                    db.session.add(new_stock)
                    inserted += 1

            db.session.commit()
            flash(f'Seed complete — {inserted} inserted, {skipped} updated. Total active: {ResearchList.query.filter_by(is_active=True).count()}', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Seed failed: {str(e)}', 'danger')
        return redirect(url_for('admin.seed_research_list'))

    return render_template('admin/seed_research_list.html',
                           current_count=current_count,
                           seed_count=len(RESEARCH_LIST_STOCKS),
                           seed_preview=RESEARCH_LIST_STOCKS[:20])


# ============================================================================
# BLOG MANAGEMENT
# ============================================================================

@admin_bp.route('/blog')
@admin_required
def blog_list():
    """Admin blog post list"""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'all')

    query = BlogPost.query
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)

    posts = query.order_by(BlogPost.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    return render_template('admin/blog_list.html', posts=posts, status_filter=status_filter)


@admin_bp.route('/blog/new', methods=['GET', 'POST'])
@admin_required
def blog_new():
    """Create new blog post"""
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        excerpt = request.form.get('excerpt')
        category = request.form.get('category')
        tags = request.form.get('tags')
        featured_image = request.form.get('featured_image')
        meta_description = request.form.get('meta_description')
        status = request.form.get('status', 'draft')
        is_featured = request.form.get('is_featured') == 'on'

        if not title or not content:
            flash('Title and content are required.', 'error')
            return render_template('admin/blog_form.html')

        post = BlogPost(
            title=title,
            content=content,
            excerpt=excerpt,
            author_id=current_user.id,
            author_name=current_user.get_full_name(),
            category=category,
            tags=tags,
            featured_image=featured_image,
            meta_description=meta_description,
            status=status,
            is_featured=is_featured
        )

        post.slug = post.generate_slug()

        if status == 'published':
            post.published_at = datetime.utcnow()

        db.session.add(post)
        db.session.commit()

        flash('Blog post created successfully!', 'success')
        return redirect(url_for('admin.blog_list'))

    return render_template('admin/blog_form.html', post=None)


@admin_bp.route('/blog/<int:post_id>/edit', methods=['GET', 'POST'])
@admin_required
def blog_edit(post_id):
    """Edit blog post"""
    post = BlogPost.query.get_or_404(post_id)

    if request.method == 'POST':
        post.title = request.form.get('title')
        post.content = request.form.get('content')
        post.excerpt = request.form.get('excerpt')
        post.category = request.form.get('category')
        post.tags = request.form.get('tags')
        post.featured_image = request.form.get('featured_image')
        post.meta_description = request.form.get('meta_description')

        old_status = post.status
        post.status = request.form.get('status', 'draft')
        post.is_featured = request.form.get('is_featured') == 'on'

        new_slug = post.generate_slug()
        if new_slug != post.slug:
            post.slug = new_slug

        if old_status != 'published' and post.status == 'published':
            post.published_at = datetime.utcnow()

        post.updated_at = datetime.utcnow()

        db.session.commit()

        flash('Blog post updated successfully!', 'success')
        return redirect(url_for('admin.blog_list'))

    return render_template('admin/blog_form.html', post=post)


@admin_bp.route('/blog/<int:post_id>/delete', methods=['POST'])
@admin_required
def blog_delete(post_id):
    """Delete blog post"""
    post = BlogPost.query.get_or_404(post_id)

    db.session.delete(post)
    db.session.commit()

    flash('Blog post deleted successfully!', 'success')
    return redirect(url_for('admin.blog_list'))


@admin_bp.route('/branding', methods=['GET', 'POST'])
@admin_required
def branding():
    """Manage platform branding settings — broker name, etc."""
    from models import SiteConfig

    if request.method == 'POST':
        broker_name = request.form.get('broker_name', '').strip()
        if not broker_name:
            flash('Broker name cannot be empty.', 'error')
        elif len(broker_name) > 80:
            flash('Broker name must be 80 characters or fewer.', 'error')
        else:
            SiteConfig.set_value('broker_name', broker_name)
            flash(f'Broker name updated to "{broker_name}".', 'success')
        return redirect(url_for('admin.branding'))

    current_broker_name = SiteConfig.get('broker_name', 'Scentric Networks')
    return render_template('admin/branding.html', broker_name=current_broker_name)


@admin_bp.route('/blog/<int:post_id>/toggle-featured', methods=['POST'])
@admin_required
def blog_toggle_featured(post_id):
    """Toggle featured status of blog post"""
    post = BlogPost.query.get_or_404(post_id)
    post.is_featured = not post.is_featured
    db.session.commit()

    status_label = 'featured' if post.is_featured else 'unfeatured'
    flash(f'Post "{post.title}" has been {status_label}.', 'success')
    return redirect(url_for('admin.blog_list'))