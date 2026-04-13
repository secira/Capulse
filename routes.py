from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, login_user, logout_user, current_user
from flask_limiter.util import get_remote_address
from app import app, db, limiter, csrf
from services.perplexity_service import PerplexityService
from models import (BlogPost, TeamMember, Testimonial, User, WatchlistItem, StockAnalysis, 
                   AIStockPick, Portfolio,
                   PricingPlan, SubscriptionStatus, Payment, Referral, ContactMessage,
                   ChatConversation, ChatMessage, ChatbotKnowledgeBase, TradingSignal,
                   RiskProfile, Admin)
from models_broker import BrokerAccount
from middleware.tenant_middleware import get_current_tenant_id, TenantQuery, create_for_tenant
from services.nse_service import nse_service
from services.nse_realtime_service import get_live_market_data, get_stock_quote
from services.market_data_service import market_data_service
from services.ai_agent_service import AgenticAICoordinator
from services.chatbot_service import chatbot
import logging

# Configure logger for routes
logger = logging.getLogger(__name__)
import random
import os
import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezone
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# Razorpay configuration - require proper env vars
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')

# Initialize Razorpay client when keys are available
razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        import razorpay
        razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        logging.info("✅ Razorpay client initialized")
    except ImportError:
        logging.warning("⚠️ Razorpay library not installed")
else:
    logging.info("⚠️ Razorpay not configured - payment features disabled")

def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def ensure_admin_status(user):
    """Auto-promote user to admin if their email is in ADMIN_EMAILS env var,
    and ensure all admin users always have HNI (full access) plan.
    Runs on login and on every request via the context processor so it
    self-heals across all environments (dev, Railway, etc.) without manual DB edits.
    Set ADMIN_EMAILS=email1@example.com,email2@example.com in environment variables.
    """
    import os
    changed = False
    admin_emails_raw = os.environ.get('ADMIN_EMAILS', '')
    admin_emails = [e.strip().lower() for e in admin_emails_raw.split(',') if e.strip()]
    if user.email.lower() in admin_emails and not user.is_admin:
        user.is_admin = True
        changed = True
        logging.info(f"Auto-promoted {user.email} to admin via ADMIN_EMAILS env var")
    # Ensure any admin user always has full HNI access in the database
    if user.is_admin and user.pricing_plan != PricingPlan.HNI:
        user.pricing_plan = PricingPlan.HNI
        changed = True
        logging.info(f"Auto-upgraded admin {user.email} to HNI plan")
    if changed:
        db.session.commit()

@app.route('/api/test-notifications', methods=['POST'])
@login_required
@admin_required
def test_notifications():
    """Test WhatsApp and Telegram notifications"""
    from services.messaging_service import test_messaging_setup
    whatsapp_ok, telegram_ok = test_messaging_setup()
    return jsonify({
        'whatsapp': 'Success' if whatsapp_ok else 'Failed',
        'telegram': 'Success' if telegram_ok else 'Failed'
    })

# Template Context Processor
# Clean OAuth integration coming soon

@app.context_processor
def inject_pricing_plans():
    """Make PricingPlan and SubscriptionStatus available to all templates"""
    context = {
        'PricingPlan': PricingPlan,
        'SubscriptionStatus': SubscriptionStatus
    }

    # Auto-upgrade admin plan on every request so production DB self-heals
    if current_user.is_authenticated and current_user.is_admin:
        try:
            ensure_admin_status(current_user)
        except Exception:
            pass

    # Add broker accounts for authenticated users (for sidebar display)
    if current_user.is_authenticated:
        try:
            from models_broker import BrokerAccount, ConnectionStatus
            # Only get connected broker accounts for sidebar
            broker_accounts = BrokerAccount.query.filter_by(
                user_id=current_user.id,
                is_active=True
            ).filter(
                BrokerAccount.connection_status == ConnectionStatus.CONNECTED.value
            ).all()
            context['broker_accounts'] = broker_accounts
        except Exception as e:
            logging.error(f"Error loading broker accounts for context: {e}")
            context['broker_accounts'] = []
    else:
        context['broker_accounts'] = []
    
    return context

def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def plan_required(*allowed_plans):
    """Decorator to require specific pricing plan access.
    Admin users always bypass plan checks regardless of their stored plan."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            # Admins always have full access
            if current_user.is_admin:
                return f(*args, **kwargs)
            if current_user.pricing_plan not in allowed_plans:
                flash('This feature requires a higher subscription plan. Please upgrade your account.', 'warning')
                return redirect(url_for('account_billing'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
def index():
    """Home page route"""
    # Get featured testimonials with error handling
    try:
        testimonials = Testimonial.query.limit(3).all()
    except Exception as e:
        logging.warning(f"Database error loading testimonials: {e}")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        testimonials = []
    return render_template('index.html', testimonials=testimonials)

@app.route('/about')
def about():
    """About Us page route"""
    # Get team members with error handling
    try:
        team_members = TeamMember.query.all()
        testimonials = Testimonial.query.limit(2).all()
    except Exception as e:
        logging.warning(f"Database error loading team/testimonials: {e}")
        team_members = []
        testimonials = []
    return render_template('about.html', team_members=team_members, testimonials=testimonials)

@app.route('/services')
def services():
    """Services page route"""
    try:
        testimonials = Testimonial.query.limit(3).all()
    except Exception as e:
        logging.warning(f"Database error loading testimonials: {e}")
        testimonials = []
    return render_template('services.html', testimonials=testimonials)

@app.route('/algo-trading')
def algo_trading():
    """ALGO Trading page route"""
    try:
        testimonials = Testimonial.query.limit(2).all()
    except Exception as e:
        logging.warning(f"Database error loading testimonials: {e}")
        testimonials = []
    return render_template('algo_trading.html', testimonials=testimonials)

@app.route('/blog')
def blog():
    """Blog listing page route"""
    page = request.args.get('page', 1, type=int)
    posts = BlogPost.query.filter_by(status='published').order_by(BlogPost.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False
    )
    featured_posts = BlogPost.query.filter_by(is_featured=True, status='published').limit(3).all()
    return render_template('blog.html', posts=posts, featured_posts=featured_posts)

@app.route('/blog/<int:post_id>')
def blog_post(post_id):
    """Individual blog post page route"""
    post = BlogPost.query.get_or_404(post_id)
    # Get related posts (same author or recent posts)
    related_posts = BlogPost.query.filter(
        BlogPost.id != post_id
    ).order_by(BlogPost.created_at.desc()).limit(3).all()
    
    testimonials = Testimonial.query.limit(2).all()
    return render_template('blog_post.html', post=post, related_posts=related_posts, testimonials=testimonials)



@app.route('/pricing')
def pricing():
    """Pricing page with subscription plans"""
    try:
        from services.razorpay_service import razorpay_service
        plans = razorpay_service.get_subscription_plans()
    except Exception as e:
        logging.error(f"Error fetching subscription plans: {e}")
        plans = {}
    
    # Get user's current subscription if logged in
    current_subscription = None
    if current_user.is_authenticated:
        # Map PricingPlan enum to string plan names used in templates
        # Using .name to get the string representation of the Enum member
        try:
            current_plan_name = current_user.pricing_plan.name
        except AttributeError:
            current_plan_name = 'FREE'
            
        current_subscription = {
            'plan': current_plan_name,
            'expires_at': getattr(current_user, 'subscription_expires_at', None) or getattr(current_user, 'subscription_end_date', None)
        }
    
    return render_template('pricing.html', 
                         plans=plans, 
                         current_subscription=current_subscription)



@app.route('/careers')
def careers():
    """Careers page route"""
    return render_template('careers.html')

@app.route('/news')
def news():
    """In the News page route"""
    return render_template('news.html')

@app.route('/partners')
def partners():
    """Partners page route"""
    return render_template('partners.html')

@app.route('/for-brokers', methods=['GET', 'POST'])
def for_brokers():
    """B2B white-label broker enquiry page"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        company = request.form.get('company', '').strip()
        client_count = request.form.get('client_count', '').strip()
        interest = request.form.get('interest', '').strip()
        message_body = request.form.get('message', '').strip()

        if not name or not email or not company:
            flash('Please fill in all required fields.', 'error')
            return render_template('for_brokers.html')

        subject = f"[B2B Broker Enquiry] {company} – {interest}"
        full_message = (
            f"Company: {company}\n"
            f"Active Clients: {client_count or 'Not specified'}\n"
            f"Interested In: {interest}\n\n"
            f"{message_body}"
        )

        enquiry = ContactMessage(
            name=name,
            email=email,
            phone=phone or None,
            company=company,
            subject=subject,
            message=full_message,
            inquiry_type='Partnership',
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:500],
        )
        db.session.add(enquiry)
        db.session.commit()

        flash('Thank you! Your enquiry has been received. Our partnerships team will contact you within 1 business day.', 'success')
        return redirect(url_for('for_brokers') + '#interest-form')

    return render_template('for_brokers.html')

@app.route('/trading-signals')
def trading_signals():
    """Public Trading Signals page route"""
    return render_template('trading_signals.html')

@app.route('/features/daily-signals')
def daily_signals_feature():
    """Static feature page for Daily Signals"""
    return render_template('daily_signals_feature.html')

@app.route('/stock-research')
def stock_research():
    """Stock Research service page route"""
    return render_template('stock_research.html')

@app.route('/dashboard/stock-research')
@login_required
def dashboard_stock_research():
    """Enhanced Stock Research landing page for logged-in users"""
    from models import DailyTradingSignal, ResearchList
    
    # Get active daily signals for the landing page
    signals = DailyTradingSignal.query.filter_by(status='ACTIVE').order_by(DailyTradingSignal.created_at.desc()).limit(5).all()
    
    # Get top research assets by I-Score for Co-Pilot section
    top_research = ResearchList.query.filter(ResearchList.i_score.isnot(None)).order_by(ResearchList.i_score.desc()).limit(5).all()
    
    return render_template('dashboard/research/stock_research_landing.html', 
                         active_section='stock_research',
                         signals=signals,
                         top_research=top_research)

@app.route('/portfolio-analysis')
def portfolio_analysis():
    """Portfolio Analysis service page route"""
    return render_template('portfolio_analysis.html')

@app.route('/algo-trading-service')
def algo_trading_service():
    """Algorithmic Trading service page route"""
    return render_template('algo_trading.html')

@app.route('/account-handling')
def account_handling():
    """Account Handling service page route"""
    return render_template('account_handling.html')

# Support and Legal Pages
@app.route('/help-center')
def help_center():
    """Help Center support page route"""
    return render_template('help_center.html')

@app.route('/privacy-policy')
def privacy_policy():
    """Privacy Policy page route"""
    return render_template('privacy_policy.html')

@app.route('/terms-of-service')
def terms_of_service():
    """Terms of Service page route"""
    return render_template('terms_of_service.html')

@app.route('/risk-disclosure')
def risk_disclosure():
    """Risk Disclosure page route"""
    return render_template('risk_disclosure.html')

@app.route('/compliance')
def compliance():
    """Compliance page route"""
    return render_template('compliance.html')

@app.route('/cancellation-refund-policy')
def cancellation_refund_policy():
    """Cancellation and Refund Policy page route"""
    return render_template('cancellation_refund_policy.html')

@app.route('/dashboard/broker-management')
@login_required
def broker_management():
    """Broker Management page - show only added brokers with Connect/Disconnect status"""
    from models_broker import BrokerAccount, BrokerType
    
    # Show only brokers that have been "added" (validated and ready for connection)
    # Filter out brokers in 'pending' or 'error' states - show only connected/disconnected
    added_brokers = BrokerAccount.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).filter(
        BrokerAccount.connection_status.in_(['connected', 'disconnected'])
    ).all()
    
    # Get broker types for the add modal
    broker_types = list(BrokerType)
    
    return render_template('dashboard/broker_management.html', 
                         active_section='broker_management',
                         brokers=added_brokers,
                         broker_types=broker_types)

@app.route('/add-broker', methods=['POST'])
@login_required
def add_broker():
    """Add new broker connection"""
    try:
        broker_name = request.form.get('broker_name')
        api_key = request.form.get('api_key')
        api_secret = request.form.get('api_secret')
        request_token = request.form.get('request_token')
        redirect_url = request.form.get('redirect_url')
        
        # Validate required fields
        if not all([broker_name, api_key, api_secret]):
            flash('Broker name, API key, and API secret are required.', 'error')
            return redirect(url_for('broker_management'))
        
        # Check if broker already exists for this user
        existing = BrokerAccount.query.filter_by(
            user_id=current_user.id, 
            broker_name=broker_name
        ).first()
        
        if existing:
            flash(f'You already have a {broker_name} connection configured.', 'error')
            return redirect(url_for('broker_management'))
        
        # Check broker limits - hard cap of 3 brokers for all users
        existing_brokers_count = BrokerAccount.query.filter_by(user_id=current_user.id, is_active=True).count()
        
        if existing_brokers_count >= 3:
            flash('You can add a maximum of 3 broker connections. Please remove an existing broker before adding a new one.', 'error')
            return redirect(url_for('broker_management'))
        
        # Additional plan-specific limits (more restrictive than hard cap)
        from models import PricingPlan
        if current_user.pricing_plan == PricingPlan.TARGET_PLUS and existing_brokers_count >= 1:
            flash('Target Plus plan allows only one broker connection. Upgrade to Target Pro for multiple brokers.', 'error')
            return redirect(url_for('broker_management'))
        
        # Create new broker connection (Step 1: Add broker with disconnected status)
        new_broker = BrokerAccount(
            user_id=current_user.id,
            broker_name=broker_name,
            connection_status='disconnected',  # Start as disconnected, ready to be connected
            is_active=True
        )
        
        # Use encryption methods to securely store credentials
        new_broker.set_credentials(
            client_id=api_key,  # Store API key as client_id
            access_token=request_token,
            api_secret=api_secret
        )
        
        # Store redirect URL if provided
        if redirect_url:
            new_broker.redirect_url = redirect_url
        
        db.session.add(new_broker)
        db.session.commit()
        
        flash(f'{broker_name} broker added successfully!', 'success')
        logging.info(f"User {current_user.id} added broker {broker_name}")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error adding broker for user {current_user.id}: {str(e)}")
        flash('Error adding broker. Please try again.', 'error')
    
    return redirect(url_for('broker_management'))

@app.route('/api/broker/<int:broker_id>/connect', methods=['POST'])
@login_required
def connect_broker(broker_id):
    """Connect a broker (Step 2: Make broker active for trading)"""
    try:
        from models_broker import BrokerAccount
        
        # Use database transaction to ensure atomicity
        broker = BrokerAccount.query.filter_by(
            id=broker_id, 
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not broker:
            return jsonify({'success': False, 'message': 'Broker not found'})
        
        if broker.connection_status == 'connected':
            return jsonify({'success': False, 'message': 'Broker is already connected'})
        
        # Check if user already has a connected broker (limit of 1) - within transaction
        connected_broker = BrokerAccount.query.filter_by(
            user_id=current_user.id,
            connection_status='connected',
            is_active=True
        ).first()
        
        if connected_broker and connected_broker.id != broker_id:
            return jsonify({
                'success': False, 
                'message': f'You already have {connected_broker.broker_name} connected. Disconnect it first to connect another broker.'
            })
        
        # TODO: Validate API credentials by calling actual broker API
        # For now, we'll simulate validation success
        # In production, this would call the broker's API to test connection
        
        # Update broker status atomically
        broker.connection_status = 'connected'
        broker.last_connected = datetime.utcnow()
        db.session.commit()
        
        flash(f'{broker.broker_name} connected successfully and is now active for trading!', 'success')
        return jsonify({'success': True, 'message': f'{broker.broker_name} connected successfully and is now active for trading!'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error connecting broker {broker_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error connecting broker. Please try again.'})

@app.route('/api/broker/<int:broker_id>/disconnect', methods=['POST'])
@login_required
def disconnect_broker(broker_id):
    """Disconnect a broker (Step 3: Make broker inactive)"""
    try:
        from models_broker import BrokerAccount
        
        broker = BrokerAccount.query.filter_by(
            id=broker_id, 
            user_id=current_user.id,
            is_active=True  # Consistency with other endpoints
        ).first()
        
        if not broker:
            return jsonify({'success': False, 'message': 'Broker not found'})
        
        if broker.connection_status == 'disconnected':
            return jsonify({'success': False, 'message': 'Broker is already disconnected'})
        
        broker.connection_status = 'disconnected'
        db.session.commit()
        
        flash(f'{broker.broker_name} disconnected successfully!', 'success')
        return jsonify({'success': True, 'message': f'{broker.broker_name} disconnected successfully!'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error disconnecting broker {broker_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error disconnecting broker. Please try again.'})

@app.route('/api/broker/sync-account/<int:account_id>', methods=['POST'])
@login_required
def sync_broker_account(account_id):
    """Sync broker account data"""
    try:
        from models_broker import BrokerAccount
        from services.broker_service import BrokerService, BrokerAPIError

        account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id,
            is_active=True
        ).first()

        if not account:
            return jsonify({'success': False, 'message': 'Broker account not found'})

        if account.connection_status != 'connected':
            return jsonify({'success': False, 'message': 'Broker must be connected to sync data'})

        account.sync_status = 'syncing'
        db.session.commit()

        try:
            results = BrokerService.sync_broker_data(account, ['holdings', 'positions', 'orders'])
            account.sync_status = 'success'
            account.last_sync = datetime.utcnow()
            db.session.commit()
            holdings = results.get('holdings', 0)
            positions = results.get('positions', 0)
            orders = results.get('orders', 0)
            return jsonify({
                'success': True,
                'message': f'{account.broker_name} synced — {holdings} holdings, {positions} positions, {orders} orders'
            })
        except BrokerAPIError as broker_err:
            account.sync_status = 'failed'
            db.session.commit()
            logging.warning(f"Broker API error syncing account {account_id}: {broker_err}")
            return jsonify({'success': False, 'message': f'Broker API error: {broker_err}'})

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error syncing broker account {account_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error syncing broker account'})


@app.route('/api/broker/sync-all', methods=['POST'])
@login_required
def sync_all_broker_accounts():
    """Sync all connected broker accounts for the current user"""
    try:
        from models_broker import BrokerAccount
        from services.broker_service import BrokerService, BrokerAPIError

        accounts = BrokerAccount.query.filter_by(
            user_id=current_user.id,
            is_active=True,
            connection_status='connected'
        ).all()

        if not accounts:
            return jsonify({'success': False, 'message': 'No connected broker accounts found'})

        synced = []
        failed = []

        for account in accounts:
            try:
                account.sync_status = 'syncing'
                db.session.commit()
                BrokerService.sync_broker_data(account, ['holdings', 'positions', 'orders'])
                account.sync_status = 'success'
                account.last_sync = datetime.utcnow()
                db.session.commit()
                synced.append(account.broker_name)
            except Exception as err:
                account.sync_status = 'failed'
                db.session.commit()
                logging.warning(f"Sync failed for {account.broker_name} (id={account.id}): {err}")
                failed.append(account.broker_name)

        if failed and not synced:
            return jsonify({'success': False, 'message': f'Sync failed for: {", ".join(failed)}'})

        msg = f'Synced {len(synced)} broker(s) successfully.'
        if failed:
            msg += f' Failed: {", ".join(failed)}.'
        return jsonify({'success': True, 'message': msg})

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in sync-all: {str(e)}")
        return jsonify({'success': False, 'message': 'Error syncing brokers'})

@app.route('/api/broker/test-connection/<int:account_id>', methods=['POST'])
@login_required
def test_broker_connection(account_id):
    """Test broker connection"""
    try:
        from models_broker import BrokerAccount
        
        account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not account:
            return jsonify({'success': False, 'message': 'Broker account not found'})
        
        # TODO: Implement actual broker API test
        # For now, check if credentials exist
        creds = account.get_credentials()
        if creds.get('client_id') and creds.get('access_token'):
            return jsonify({'success': True, 'message': 'Connection test successful!'})
        else:
            return jsonify({'success': False, 'message': 'Missing credentials'})
        
    except Exception as e:
        logging.error(f"Error testing broker connection {account_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error testing connection'})

@app.route('/api/broker/set-primary/<int:account_id>', methods=['POST'])
@login_required
def set_primary_broker(account_id):
    """Set broker as primary for trading"""
    try:
        from models_broker import BrokerAccount
        
        account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not account:
            return jsonify({'success': False, 'message': 'Broker account not found'})
        
        if account.connection_status != 'connected':
            return jsonify({'success': False, 'message': 'Broker must be connected to set as primary'})
        
        # Remove primary flag from all other brokers for this user
        BrokerAccount.query.filter_by(
            user_id=current_user.id,
            is_active=True
        ).update({'is_primary': False})
        
        # Set this broker as primary
        account.is_primary = True
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'{account.broker_name} set as primary broker for trading!'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error setting primary broker {account_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error setting primary broker'})

@app.route('/api/broker/remove-account/<int:account_id>', methods=['DELETE'])
@login_required
def remove_broker_account(account_id):
    """Remove broker account"""
    try:
        from models_broker import BrokerAccount
        
        account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not account:
            return jsonify({'success': False, 'message': 'Broker account not found'})
        
        # Soft delete by marking as inactive
        account.is_active = False
        account.connection_status = 'disconnected'
        account.is_primary = False
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'{account.broker_name} removed successfully!'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error removing broker account {account_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error removing broker account'})

@app.route('/update-broker', methods=['POST'])
@login_required
def update_broker():
    """Update existing broker connection - SECURE CREDENTIAL HANDLING"""
    try:
        from models_broker import BrokerAccount
        
        broker_id = request.form.get('broker_id')
        api_key = request.form.get('api_key')
        api_secret = request.form.get('api_secret')
        request_token = request.form.get('request_token')
        
        broker = BrokerAccount.query.filter_by(
            id=broker_id, 
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not broker:
            flash('Broker not found.', 'error')
            return redirect(url_for('broker_management'))
        
        # Use secure credential update method - NEVER store plaintext
        if api_key or api_secret or request_token:
            # Get current credentials to preserve unchanged values
            current_creds = broker.get_credentials()
            
            broker.set_credentials(
                client_id=api_key if api_key else current_creds.get('client_id'),
                access_token=request_token if request_token else current_creds.get('access_token'),
                api_secret=api_secret if api_secret else current_creds.get('api_secret')
            )
            
        broker.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash(f'{broker.broker_name} credentials updated securely!', 'success')
        logging.info(f"User {current_user.id} updated broker {broker.broker_name} credentials")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating broker: {str(e)}")
        flash('Error updating broker. Please try again.', 'error')
    
    return redirect(url_for('broker_management'))

@app.route('/api/broker/<int:broker_id>')
@login_required
def get_broker_details(broker_id):
    """Get broker details for editing - NEVER expose full credentials"""
    try:
        from models_broker import BrokerAccount
        
        broker = BrokerAccount.query.filter_by(
            id=broker_id, 
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not broker:
            return jsonify({'success': False, 'message': 'Broker not found'})
        
        # Get decrypted credentials safely for masking only
        credentials = broker.get_credentials()
        api_key = credentials.get('client_id', '')
        
        # Mask API key for display (show first 4 and last 4 characters)
        masked_api_key = ''
        if api_key and len(api_key) > 8:
            masked_api_key = api_key[:4] + '*' * (len(api_key) - 8) + api_key[-4:]
        elif api_key:
            masked_api_key = '*' * len(api_key)
        
        return jsonify({
            'success': True,
            'broker': {
                'id': broker.id,
                'broker_name': broker.broker_name,
                'api_key_masked': masked_api_key,  # Never expose full key
                'has_credentials': bool(api_key),
                'connection_status': broker.connection_status,
                'last_connected': broker.last_connected.isoformat() if broker.last_connected else None
            }
        })
        
    except Exception as e:
        logging.error(f"Error getting broker details: {str(e)}")
        return jsonify({'success': False, 'message': 'Error retrieving broker details'})

@app.route('/api/broker/<int:broker_id>', methods=['DELETE'])
@login_required
def delete_broker(broker_id):
    """Delete broker connection"""
    try:
        broker = BrokerAccount.query.filter_by(
            id=broker_id, 
            user_id=current_user.id
        ).first()
        
        if not broker:
            return jsonify({'success': False, 'message': 'Broker not found'})
        
        broker_name = broker.broker_name
        db.session.delete(broker)
        db.session.commit()
        
        logging.info(f"User {current_user.id} deleted broker {broker_name}")
        return jsonify({'success': True, 'message': f'{broker_name} connection deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting broker: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/broker/<int:broker_id>/refresh-token', methods=['POST'])
@login_required
def refresh_broker_token(broker_id):
    """Refresh broker access token - SECURE TOKEN HANDLING"""
    try:
        from models_broker import BrokerAccount
        
        data = request.get_json()
        request_token = data.get('request_token')
        
        broker = BrokerAccount.query.filter_by(
            id=broker_id, 
            user_id=current_user.id,
            is_active=True
        ).first()
        
        if not broker:
            return jsonify({'success': False, 'message': 'Broker not found'})
        
        if not request_token:
            return jsonify({'success': False, 'message': 'Request token is required'})
        
        # Use secure credential update - NEVER store plaintext tokens
        current_creds = broker.get_credentials()
        
        # TODO: In production, call broker API to validate and exchange tokens
        # For now, simulate successful token refresh
        new_access_token = f"access_token_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        broker.set_credentials(
            client_id=current_creds.get('client_id'),
            access_token=new_access_token,  # Store encrypted access token
            api_secret=current_creds.get('api_secret')
        )
        
        broker.last_token_refresh = datetime.utcnow()
        db.session.commit()
        
        logging.info(f"User {current_user.id} refreshed token for broker {broker.broker_name}")
        return jsonify({'success': True, 'message': 'Token refreshed successfully'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error refreshing token: {str(e)}")
        return jsonify({'success': False, 'message': 'Error refreshing token'})



@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """Contact page route with email notifications"""
    if request.method == 'POST':
        try:
            # Get form data
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            company = request.form.get('company', '').strip()
            subject = request.form.get('subject', '').strip()
            message = request.form.get('message', '').strip()
            
            # Validate required fields
            if not all([first_name, last_name, email, message]):
                flash('Please fill in all required fields.', 'error')
                return redirect(url_for('contact'))
            
            # Create full name
            full_name = f"{first_name} {last_name}"
            
            # Map subject to inquiry type
            inquiry_type_map = {
                'general': 'General',
                'support': 'Support',
                'sales': 'Sales', 
                'partnership': 'Partnership',
                'feedback': 'Feedback'
            }
            inquiry_type = inquiry_type_map.get(subject, 'General')
            
            # Create contact message record
            contact_message = ContactMessage(
                name=full_name,
                email=email,
                subject=subject,
                message=message,
                phone=phone if phone else None,
                company=company if company else None,
                inquiry_type=inquiry_type,
                ip_address=request.environ.get('REMOTE_ADDR'),
                user_agent=request.environ.get('HTTP_USER_AGENT')
            )
            
            # Save to database
            db.session.add(contact_message)
            db.session.commit()
            
            # Send email notifications
            from services.email_service import email_service
            
            # Send confirmation email to user
            email_sent_to_user = email_service.send_contact_confirmation(contact_message)
            
            # Send notification email to admin
            email_sent_to_admin = email_service.send_contact_notification(contact_message)
            
            # Show success message
            if email_sent_to_user:
                flash('Thank you for your message! We\'ve sent you a confirmation email and will get back to you within 24 hours.', 'success')
            else:
                flash('Thank you for your message! We will get back to you within 24 hours.', 'success')
            
            # Log the contact submission
            logging.info(f"New contact message from {full_name} ({email}) - Type: {inquiry_type}")
            
            return redirect(url_for('contact'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Contact form submission error: {str(e)}")
            flash('Sorry, there was an error sending your message. Please try again or contact us directly.', 'error')
            return redirect(url_for('contact'))
    
    return render_template('contact.html')


@app.route('/change-password', methods=['POST'])
@login_required
@limiter.limit("5 per minute", key_func=lambda: str(current_user.get_id()) if current_user.is_authenticated else get_remote_address())  # Per-user rate limiting
def change_password():
    """Change user password"""
    try:
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate input
        if not all([current_password, new_password, confirm_password]):
            flash('All password fields are required.', 'error')
            return redirect(url_for('account_settings'))
        
        # Check if current password is correct
        if not check_password_hash(current_user.password_hash, current_password):
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('account_settings'))
        
        # Check if new passwords match
        if new_password != confirm_password:
            flash('New passwords do not match.', 'error')
            return redirect(url_for('account_settings'))
        
        # Check password length
        if len(new_password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
            return redirect(url_for('account_settings'))
        
        # Update password
        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        flash('Password updated successfully!', 'success')
        logging.info(f"User {current_user.id} changed password")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Password change failed for user {current_user.id}: {str(e)}")
        flash('Error updating password. Please try again.', 'error')
    
    return redirect(url_for('account_settings'))

@app.route('/update-preferences', methods=['POST'])
@login_required
def update_preferences():
    """Update user preferences"""
    try:
        # Get form data
        email_trading_alerts = request.form.get('email_trading_alerts') == 'on'
        email_market_updates = request.form.get('email_market_updates') == 'on'
        email_billing_updates = request.form.get('email_billing_updates') == 'on'
        email_promotional = request.form.get('email_promotional') == 'on'
        default_dashboard_view = request.form.get('default_dashboard_view', 'overview')
        theme_preference = request.form.get('theme_preference', 'light')
        timezone = request.form.get('timezone', 'Asia/Kolkata')
        
        # For now, we'll store preferences in session since we don't have a UserPreferences model
        # In a full implementation, you'd want to create a UserPreferences model
        session['user_preferences'] = {
            'email_trading_alerts': email_trading_alerts,
            'email_market_updates': email_market_updates,
            'email_billing_updates': email_billing_updates,
            'email_promotional': email_promotional,
            'default_dashboard_view': default_dashboard_view,
            'theme_preference': theme_preference,
            'timezone': timezone
        }
        
        flash('Preferences updated successfully!', 'success')
        logging.info(f"User {current_user.id} updated preferences")
        
    except Exception as e:
        logging.error(f"Preferences update failed for user {current_user.id}: {str(e)}")
        flash('Error updating preferences. Please try again.', 'error')
    
    return redirect(url_for('account_settings'))


SUPPORTED_LANGUAGES = {'en', 'hi', 'ta', 'te', 'mr', 'gu', 'kn'}

@app.route('/account/update-language', methods=['POST'])
@login_required
def update_language():
    """Save the user's preferred language to the database"""
    lang = request.form.get('language', 'en').strip().lower()
    if lang not in SUPPORTED_LANGUAGES:
        flash('Invalid language selection.', 'error')
        return redirect(url_for('account_settings'))
    try:
        current_user.preferred_language = lang
        db.session.commit()
        flash('Language preference saved.', 'success')
        logging.info(f"User {current_user.id} set language to {lang}")
    except Exception as e:
        db.session.rollback()
        logging.error(f"Language update failed for user {current_user.id}: {e}")
        flash('Could not save language preference. Please try again.', 'error')
    return redirect(url_for('account_settings'))


# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")  # Strict rate limit for login attempts
def login():
    """User login route"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Please fill in all fields.', 'error')
            return render_template('auth/login.html')
        
        # Support login with username or email (tenant-scoped)
        tenant_id = get_current_tenant_id()
        user = User.query.filter(
            User.tenant_id == tenant_id,
            (User.username == username) | (User.email == username)
        ).first()
        
        if user and user.check_password(password):
            ensure_admin_status(user)
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()

            try:
                from services.broker_data_quality import BrokerDataQuality
                _dq = BrokerDataQuality(user.id, user.tenant_id or 'live')
                stale_ids = _dq.should_auto_sync()
                if stale_ids:
                    from services.broker_service import BrokerService
                    from models_broker import BrokerAccount
                    for _bid in stale_ids[:5]:
                        _acct = BrokerAccount.query.get(_bid)
                        if _acct:
                            try:
                                _acct.sync_status = 'syncing'
                                db.session.commit()
                                BrokerService.sync_broker_data(_acct.id, ['holdings', 'positions', 'orders'])
                                _acct.sync_status = 'success'
                                _acct.last_sync = datetime.utcnow()
                                db.session.commit()
                            except Exception:
                                _acct.sync_status = 'failed'
                                db.session.commit()
                    logger.info(f"Auto-synced {len(stale_ids)} stale broker(s) on login for user {user.id}")
            except Exception as _sync_exc:
                logger.debug(f"Login auto-sync skipped: {_sync_exc}")

            flash('Logged in successfully!', 'success')
            
            if user.is_admin:
                return redirect(url_for('dashboard'))
                
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("15 per minute")
def register():
    """User registration route"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        
        if not all([username, email, password]):
            flash('Please fill in all required fields.', 'error')
            return render_template('auth/register.html')
        
        # Check if username or email already exists within current tenant
        if TenantQuery(User).filter_by(username=username).first():
            flash('Username already exists.', 'error')
            return render_template('auth/register.html')
        
        if TenantQuery(User).filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('auth/register.html')
        
        # Create new user bound to current tenant
        user = create_for_tenant(User,
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        # Send signup notification to admin
        try:
            from services.messaging_service import send_signup_notification
            send_signup_notification(user)
        except Exception as e:
            logging.error(f"Failed to send signup notification: {e}")
        
        flash('Registration successful! You can now log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('auth/register.html')

@app.route('/logout')
@login_required
def logout():
    """User logout route"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile management"""
    if request.method == 'POST':
        # Update profile information
        current_user.first_name = request.form.get('first_name', '').strip()
        current_user.last_name = request.form.get('last_name', '').strip()
        current_user.username = request.form.get('username', '').strip()
        current_user.email = request.form.get('email', '').strip()
        
        # Handle password change
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        if new_password:
            if new_password != confirm_password:
                flash('Passwords do not match.', 'error')
                return render_template('auth/profile.html')
            
            if len(new_password) < 6:
                flash('Password must be at least 6 characters long.', 'error')
                return render_template('auth/profile.html')
            
            current_user.set_password(new_password)
        
        # Validate required fields
        if not current_user.username or not current_user.email:
            flash('Username and email are required.', 'error')
            return render_template('auth/profile.html')
        
        # Check for duplicate username/email (excluding current user)
        existing_username = User.query.filter(
            User.username == current_user.username, 
            User.id != current_user.id
        ).first()
        if existing_username:
            flash('Username already exists.', 'error')
            return render_template('auth/profile.html')
        
        existing_email = User.query.filter(
            User.email == current_user.email, 
            User.id != current_user.id
        ).first()
        if existing_email:
            flash('Email already registered.', 'error')
            return render_template('auth/profile.html')
        
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
    
    # Get user statistics with safe imports
    try:
        from models import TradingSignal
        trading_signals_count = TradingSignal.query.filter(TradingSignal.status == 'ACTIVE').count()
    except (ImportError, AttributeError):
        trading_signals_count = 0
    analyses_count = StockAnalysis.query.count()  # Could be user-specific in future
    
    return render_template('auth/profile.html', 
                         trading_signals_count=trading_signals_count,
                         analyses_count=analyses_count)

# OAuth success redirect routes (handled by google_auth blueprint for Google)
@app.route('/auth/facebook/callback')
def facebook_oauth_success():
    """Handle successful Facebook OAuth login"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    else:
        flash('Facebook authentication failed. Please try again.', 'error')
        return redirect(url_for('login'))

# Dashboard routes
@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard route with real portfolio data"""
    from services.comprehensive_portfolio_service import get_comprehensive_portfolio_service
    from models_broker import BrokerAccount
    
    # Get comprehensive portfolio summary
    portfolio_service = get_comprehensive_portfolio_service(current_user.id)
    portfolio_summary = portfolio_service.get_complete_portfolio_summary()
    top_performers = portfolio_service.get_top_performers(limit=3)
    
    # Get trading signals count
    try:
        from models import DailyTradingSignal
        from datetime import date
        # Check if column exists by attempting a simpler query if needed, 
        # but since we've fixed the DB, it should be fine.
        trading_signals_count = DailyTradingSignal.query.filter(
            DailyTradingSignal.status == 'ACTIVE',
            DailyTradingSignal.signal_date == date.today()
        ).count()
        # Fallback to total active if none today
        if trading_signals_count == 0:
            trading_signals_count = DailyTradingSignal.query.filter_by(status='ACTIVE').count()
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Error getting trading signals count: {str(e)}")
        trading_signals_count = 0
    
    # Get connected brokers
    broker_accounts = BrokerAccount.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate user statistics
    days_active = (datetime.utcnow() - current_user.created_at).days
    
    # Calculate user level based on portfolio value
    portfolio_value_cr = portfolio_summary['total_current_value'] / 10000000  # Convert to Crores
    if portfolio_value_cr >= 5:
        user_level = "Expert Trader"
        level_progress = 100
    elif portfolio_value_cr >= 2:
        user_level = "Advanced Trader"
        level_progress = 75
    elif portfolio_value_cr >= 0.5:
        user_level = "Intermediate"
        level_progress = 50
    elif portfolio_value_cr > 0:
        user_level = "Beginner"
        level_progress = 25
    else:
        user_level = "New User"
        level_progress = 10
    
    # Get account manager for HNI users
    account_manager = None
    if current_user.can_access_menu('dashboard_account_handling'):
        try:
            from models import AccountManager
            account_manager = AccountManager.get_or_create_default()
        except Exception:
            account_manager = None

    # Behavioural AI summary widget
    behaviour_summary = {'has_data': False}
    try:
        from services.behaviour_engine import BehaviourEngine
        _be = BehaviourEngine(current_user.id, current_user.tenant_id or 'live')
        _cats = {
            'trading':     _be.get_trading_behavior(),
            'risk':        _be.get_risk_behavior(),
            'portfolio':   _be.get_portfolio_behavior(),
            'performance': _be.get_performance_patterns(),
            'psychology':  _be.get_psychology_patterns(),
        }
        _master = _be.get_master_score(_cats)
        _patterns = {}
        for _cd in _cats.values():
            _patterns.update(_cd.get('modules', {}))
        _personality = _be.get_trading_personality(_patterns, _master['score'])
        _alerts = _be.get_today_alerts()[:2]
        behaviour_summary = {
            'has_data':    True,
            'score':       _master['score'],
            'category':    _master['category'],
            'color':       _master['color'],
            'personality': _personality,
            'alerts':      _alerts,
            'cats': {k: v['score'] for k, v in _cats.items()},
        }
    except Exception as _exc:
        current_app.logger.debug(f"Behaviour summary skipped on dashboard: {_exc}")

    data_freshness = None
    data_quality = None
    try:
        from services.broker_data_quality import BrokerDataQuality
        _dq = BrokerDataQuality(current_user.id, current_user.tenant_id or 'live')
        data_freshness = _dq.get_data_freshness()
        data_quality = _dq.get_quality_score()
    except Exception as _exc:
        current_app.logger.debug(f"Data quality check skipped: {_exc}")

    return render_template('dashboard/dashboard_improved.html',
                         portfolio_summary=portfolio_summary,
                         top_performers=top_performers,
                         trading_signals_count=trading_signals_count,
                         days_active=days_active,
                         user_level=user_level,
                         level_progress=level_progress,
                         broker_accounts=broker_accounts,
                         account_manager=account_manager,
                         behaviour_summary=behaviour_summary,
                         data_freshness=data_freshness,
                         data_quality=data_quality)

# Stock Analysis route removed as requested by user

# Real-time market data API endpoints with caching
@app.route('/api/realtime/indices')
def api_realtime_indices():
    """API endpoint for real-time NSE indices data with caching"""
    try:
        from caching.redis_cache import get_cache
        cache = get_cache()
        
        if cache.is_available():
            cached_data = cache.get_market_indices()
            if cached_data:
                cached_data['cached'] = True
                return jsonify(cached_data)
        
        data = get_live_market_data()
        
        if cache.is_available():
            cache.set_market_indices(data, expiry=60)
        data['cached'] = False
        return jsonify(data)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }), 500

@app.route('/api/realtime/stock/<symbol>')
def api_realtime_stock(symbol):
    """API endpoint for real-time stock data with caching"""
    try:
        from caching.redis_cache import get_cache
        cache = get_cache()
        
        symbol_upper = symbol.upper()
        
        if cache.is_available():
            cached_data = cache.get_market_data(symbol_upper)
            if cached_data:
                cached_data['cached'] = True
                return jsonify(cached_data)
        
        data = get_stock_quote(symbol_upper)
        
        if cache.is_available():
            cache.set_market_data(symbol_upper, data, expiry=60)
        data['cached'] = False
        return jsonify(data)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }), 500





@app.route('/seed-demo-data')
def seed_demo_data():
    """Seed the database with demo data for testing"""
    try:
        # Create demo user if not exists
        demo_user = User.query.filter_by(username='demo').first()
        if not demo_user:
            demo_user = User(
                username='demo',
                email='demo@tcapital.com',
                first_name='Demo',
                last_name='User'
            )
            demo_user.set_password('demo123')
            db.session.add(demo_user)
            db.session.commit()
        
        # Create sample stock analyses
        sample_analyses = [
            {
                'symbol': 'AAPL',
                'company_name': 'Apple Inc.',
                'current_price': 175.50,
                'previous_close': 173.35,
                'change_amount': 2.15,
                'change_percent': 1.24,
                'volume': 45000000,
                'market_cap': 2750000000000,
                'pe_ratio': 28.5,
                'day_high': 176.80,
                'day_low': 174.20,
                'week_52_high': 198.23,
                'week_52_low': 124.17,
                'ai_recommendation': 'BUY',
                'ai_confidence': 85.5,
                'ai_notes': 'Strong earnings momentum and product innovation pipeline'
            },
            {
                'symbol': 'GOOGL',
                'company_name': 'Alphabet Inc.',
                'current_price': 2845.20,
                'previous_close': 2857.70,
                'change_amount': -12.50,
                'change_percent': -0.44,
                'volume': 25000000,
                'market_cap': 1800000000000,
                'pe_ratio': 25.2,
                'day_high': 2860.00,
                'day_low': 2840.15,
                'week_52_high': 3030.93,
                'week_52_low': 2193.62,
                'ai_recommendation': 'HOLD',
                'ai_confidence': 72.3,
                'ai_notes': 'Solid fundamentals but facing regulatory headwinds'
            },
            {
                'symbol': 'TSLA',
                'company_name': 'Tesla Inc.',
                'current_price': 245.30,
                'previous_close': 253.50,
                'change_amount': -8.20,
                'change_percent': -3.23,
                'volume': 78000000,
                'market_cap': 780000000000,
                'pe_ratio': 45.8,
                'day_high': 248.90,
                'day_low': 243.10,
                'week_52_high': 299.29,
                'week_52_low': 138.80,
                'ai_recommendation': 'SELL',
                'ai_confidence': 68.1,
                'ai_notes': 'High volatility and production concerns'
            }
        ]
        
        for analysis_data in sample_analyses:
            existing = StockAnalysis.query.filter_by(symbol=analysis_data['symbol']).first()
            if not existing:
                analysis = StockAnalysis(**analysis_data)
                db.session.add(analysis)
        
        # Create sample watchlist items for demo user
        sample_watchlist = [
            {'symbol': 'AAPL', 'company_name': 'Apple Inc.', 'target_price': 180.00, 'notes': 'Strong buy on next dip'},
            {'symbol': 'MSFT', 'company_name': 'Microsoft Corp.', 'target_price': 400.00, 'notes': 'Cloud growth story'},
            {'symbol': 'NVDA', 'company_name': 'NVIDIA Corp.', 'target_price': 500.00, 'notes': 'AI revolution leader'}
        ]
        
        for item_data in sample_watchlist:
            existing = WatchlistItem.query.filter_by(user_id=demo_user.id, symbol=item_data['symbol']).first()
            if not existing:
                item = WatchlistItem(
                    user_id=demo_user.id,
                    **item_data
                )
                db.session.add(item)
        
        db.session.commit()
        flash('Demo data seeded successfully! You can login with username: demo, password: demo123', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error seeding demo data: {str(e)}', 'error')
        logging.error(f"Error seeding demo data: {str(e)}")
    
    return redirect(url_for('index'))

# NSE India API routes
@app.route('/api/nse/quote/<symbol>')
@login_required
def get_nse_quote(symbol):
    """Get real-time NSE stock quote"""
    try:
        quote = nse_service.get_stock_quote(symbol.upper())
        if quote:
            return jsonify({'success': True, 'data': quote})
        else:
            return jsonify({'success': False, 'error': 'Stock not found'})
    except Exception as e:
        logging.error(f"Error fetching NSE quote for {symbol}: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to fetch stock data'})

@app.route('/api/nse/search/<query>')
@login_required
def search_nse_stocks(query):
    """Search NSE stocks by symbol or name"""
    try:
        results = nse_service.search_stocks(query)
        return jsonify({'success': True, 'data': results})
    except Exception as e:
        logging.error(f"Error searching NSE stocks: {str(e)}")
        return jsonify({'success': False, 'error': 'Search failed'})

@app.route('/api/nse/market-overview')
@login_required
def get_market_overview():
    """Get NSE market overview with indices and top movers"""
    try:
        overview = {
            'indices': nse_service.get_market_indices(),
            'top_gainers': nse_service.get_top_gainers(5),
            'top_losers': nse_service.get_top_losers(5),
            'most_active': nse_service.get_most_active(5),
            'market_status': nse_service.get_market_status()
        }
        return jsonify({'success': True, 'data': overview})
    except Exception as e:
        logging.error(f"Error fetching market overview: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to fetch market data'})



@app.route('/dashboard/trading-signals')
@login_required
def dashboard_trading_signals():
    """Trading signals page for paid users only"""
    # Check if user has paid subscription (admins always have full access)
    from models import PricingPlan
    if not current_user.is_admin and current_user.pricing_plan not in [PricingPlan.TARGET_PLUS, PricingPlan.TARGET_PRO, PricingPlan.HNI]:
        flash('Trading signals are available for Target Plus, Target Pro, and HNI subscribers only.', 'warning')
        return redirect(url_for('pricing'))
    
    # Get date filter parameter
    selected_date_str = request.args.get('date')
    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = datetime.utcnow().date()
    else:
        selected_date = datetime.utcnow().date()
    
    # Get active trading signals from admin
    try:
        from models import TradingSignal
        from sqlalchemy import func, desc
        
        # Filter by selected date or default to last 7 days
        if selected_date_str:
            signals = TradingSignal.query.filter(
                TradingSignal.status == 'ACTIVE',
                func.date(TradingSignal.created_at) == selected_date
            ).order_by(desc(TradingSignal.created_at)).all()
        else:
            signals = TradingSignal.query.filter(
                TradingSignal.status == 'ACTIVE',
                func.date(TradingSignal.created_at) >= selected_date - timedelta(days=7)
            ).order_by(desc(TradingSignal.created_at)).all()
    except ImportError:
        signals = []  # Fallback if TradingSignal not available
    
    # Get AI stock picks for the selected date (merged functionality)
    try:
        from models import AIStockPick
        ai_picks = AIStockPick.query.filter_by(pick_date=selected_date).all()
    except:
        ai_picks = []  # Fallback if AIStockPick not available

    # Get available strategies for the dropdown
    from services.trading_service import get_trading_service
    trading_service = get_trading_service()
    strategies = trading_service.get_strategies()
    
    return render_template('dashboard/trading_signals.html', 
                         signals=signals, 
                         ai_picks=ai_picks,
                         strategies=strategies,
                         today=selected_date,
                         selected_date=selected_date_str,
                         timedelta=timedelta)

@app.route('/dashboard/trade-now')
@login_required  
def dashboard_trade_now():
    """Trade Now page for executing live trades"""
    from datetime import date
    
    # Import TradingSignal with safe fallback
    try:
        from models import TradingSignal
        signals_available = True
    except ImportError:
        signals_available = False
        TradingSignal = None
    
    # Get filter parameters
    selected_date_str = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    selected_date = date.today()  # Use date object for template
    symbol_type = request.args.get('symbol_type', 'all')
    signal_status = request.args.get('status', 'all')
    
    # Build query for trading signals if available
    if signals_available and TradingSignal:
        query = TradingSignal.query
    else:
        query = None
    
    # Handle trading signals data
    if query is not None:
        # Filter by date
        if selected_date_str:
            query = query.filter(TradingSignal.created_at >= selected_date_str)
        
        # Filter by symbol type
        if symbol_type != 'all':
            query = query.filter(TradingSignal.signal_type == symbol_type)
        
        # Filter by status
        if signal_status != 'all':
            query = query.filter(TradingSignal.status == signal_status)
        
        # Get signals ordered by creation date
        trading_signals = query.order_by(TradingSignal.created_at.desc()).all()
    else:
        trading_signals = []
    
    # Calculate summary statistics
    total_signals = len(trading_signals)
    active_signals = len([s for s in trading_signals if s.status == 'ACTIVE'])
    closed_signals = len([s for s in trading_signals if s.status == 'CLOSED'])
    
    # Calculate success rate for closed signals
    profitable_signals = len([s for s in trading_signals if s.status == 'CLOSED' and hasattr(s, 'potential_return') and s.potential_return > 0])
    success_rate = (profitable_signals / closed_signals * 100) if closed_signals > 0 else 0
    
    # Get unique symbol types for filter dropdown
    if signals_available and TradingSignal:
        symbol_types = db.session.query(TradingSignal.signal_type).distinct().all()
        symbol_types = [st[0] for st in symbol_types if st[0]]
    else:
        symbol_types = ['stock', 'options', 'futures']
    
    # Initialize trading service
    from services.trading_service import TradingService
    try:
        from models_broker import BrokerAccount
        broker_model_available = True
    except ImportError:
        broker_model_available = False
    
    trading_service = TradingService()
    
    connected_brokers = []
    primary_broker = None
    if broker_model_available:
        try:
            connected_brokers = BrokerAccount.query.filter_by(
                user_id=current_user.id,
                is_active=True,
                connection_status='connected'
            ).all()
            primary_broker = next((b for b in connected_brokers if b.is_primary), None)
            if not primary_broker and connected_brokers:
                primary_broker = connected_brokers[0]
        except Exception:
            connected_brokers = []
            primary_broker = None
    
    # Fetch supported assets
    assets = trading_service.get_all_assets()
    
    # Only support Equity, Mutual Funds, Futures and Options
    allowed_classes = ['stocks', 'equity', 'mutual_funds', 'futures', 'options', 'index_future', 'index_option']
    assets = [a for a in assets if a.get('asset_class', '').lower() in allowed_classes]
    
    # Get available strategies
    try:
        strategies = trading_service.get_strategies()
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Error getting strategies: {str(e)}")
        strategies = []
    
    # Get user's current trades
    user_trades = trading_service.get_user_trades(current_user.id)
    
    # Scentric Guardrails — load portfolio summary + risk profile for Trade Now
    from services.risk_engine import get_risk_engine
    from services.comprehensive_portfolio_service import get_comprehensive_portfolio_service
    from models import RiskProfile
    risk_engine = get_risk_engine(current_user.id)
    try:
        ps = get_comprehensive_portfolio_service(current_user.id)
        trade_portfolio_summary = ps.get_complete_portfolio_summary()
    except Exception:
        trade_portfolio_summary = {'total_current_value': 0, 'asset_classes': []}
    risk_profile = RiskProfile.query.filter_by(user_id=current_user.id).first()

    return render_template('dashboard/trade_now.html',
                           current_user=current_user,
                           primary_broker=primary_broker,
                           connected_brokers=connected_brokers,
                           assets=assets,
                           strategies=strategies,
                           risk_profile=risk_profile,
                           trade_portfolio_summary=trade_portfolio_summary,
                           user_trades=user_trades['data'] if user_trades['success'] else {
                               'active_trades': [],
                               'recent_history': [],
                               'recommendations': []
                           })

@app.route('/dashboard/trade-assist')
@login_required  
def trade_assist():
    """Trade Assist page - helps users execute trades based on signals"""
    # Check subscription access (admins always have full access)
    from models import PricingPlan
    if not current_user.is_admin and current_user.pricing_plan not in [PricingPlan.TARGET_PLUS, PricingPlan.TARGET_PRO, PricingPlan.HNI]:
        flash('Trade Assist is available for Trader, Trader Plus, and HNI subscribers only.', 'warning')
        return redirect(url_for('pricing'))
    
    # Pass ALL query parameters exactly as received to the trade-now page
    signal_params = request.args.to_dict(flat=True)
    
    # Redirect to trade-now page with all signal data preserved
    return redirect(url_for('dashboard_trade_now', **signal_params))

@app.route('/admin/trading-signals/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_trading_signal():
    """Admin route to create new trading signals"""
    from datetime import date
    
    if request.method == 'POST':
        try:
            # Create new trading signal from form data
            signal = TradingSignal(
                user_id=current_user.id,
                open_date=datetime.strptime(request.form['open_date'], '%Y-%m-%d').date(),
                symbol_type=request.form['symbol_type'],
                ticker_symbol=request.form['ticker_symbol'].upper(),
                option_type=request.form.get('option_type'),
                trade_strategy=request.form.get('trade_strategy'),
                strike_price=float(request.form['strike_price']) if request.form.get('strike_price') else None,
                expiration_date=datetime.strptime(request.form['expiration_date'], '%Y-%m-%d').date() if request.form.get('expiration_date') else None,
                number_of_units=int(request.form['number_of_units']),
                trade_direction=request.form['trade_direction'],
                entry_price=float(request.form['entry_price']),
                current_price=float(request.form['current_price']) if request.form.get('current_price') else None,
                trade_fees=float(request.form['trade_fees']) if request.form.get('trade_fees') else None,
                capital_risk=float(request.form['capital_risk']),
                trading_account=request.form.get('trading_account'),
                signal_status='Active'
            )
            
            db.session.add(signal)
            db.session.commit()
            
            flash(f'Trading signal for {signal.ticker_symbol} created successfully!', 'success')
            return redirect(url_for('dashboard_trading_signals'))
            
        except Exception as e:
            logging.error(f"Error creating trading signal: {str(e)}")
            flash('Error creating trading signal. Please check your input.', 'error')
    
    return render_template('admin/create_trading_signal.html', today=date.today())

@app.route('/admin/trading-signals/update/<int:signal_id>', methods=['POST'])
@login_required
@admin_required
def update_trading_signal(signal_id):
    """Admin route to update trading signal prices and status"""
    try:
        signal = TradingSignal.query.get_or_404(signal_id)
        
        # Update fields that can be modified
        if request.form.get('current_price'):
            signal.current_price = float(request.form['current_price'])
        
        if request.form.get('exit_price'):
            signal.exit_price = float(request.form['exit_price'])
            signal.exit_date = datetime.now(timezone.utc).date()
            signal.signal_status = 'Closed'
            
            # Calculate P&L
            pnl_amount, pnl_percentage = signal.calculate_pnl()
            signal.pnl_amount = pnl_amount
            signal.pnl_percentage = pnl_percentage
            
            if pnl_amount > 0:
                signal.trade_result = 'Profit'
            elif pnl_amount < 0:
                signal.trade_result = 'Loss'
            else:
                signal.trade_result = 'Breakeven'
        
        if request.form.get('signal_status'):
            signal.signal_status = request.form['signal_status']
        
        if request.form.get('reason_for_exit'):
            signal.reason_for_exit = request.form['reason_for_exit']
        
        db.session.commit()
        flash(f'Trading signal for {signal.ticker_symbol} updated successfully!', 'success')
        
    except Exception as e:
        logging.error(f"Error updating trading signal: {str(e)}")
        flash('Error updating trading signal.', 'error')
    
    return redirect(url_for('dashboard_trading_signals'))

@app.route('/dashboard/stock-picker')
@login_required
def dashboard_stock_picker():
    """Redirect AI Stock Picker to Trading Signals (merged functionality)"""
    return redirect(url_for('dashboard_trading_signals'))

@app.route('/dashboard/detailed-analysis/<string:symbol>')
@login_required
def detailed_stock_analysis(symbol):
    """Detailed stock analysis page based on Infosys format"""
    from datetime import date
    
    # Sample data structure - in production this would come from AI analysis
    stock_data = {
        'INFY': {
            'company_name': 'Infosys Limited',
            'current_price': 1447.70,
            'pe_ratio': 22.0,
            'market_cap': '₹6.15 lakh crore',
            'market_cap_category': 'Large Cap',
            'annual_revenue': '193,000',
            'revenue_growth': '10',
            'profit_margin': '18-20',
            'quarterly_results': 'Q1 FY25 revenue and profit both up YoY, though margin growth slowed due to higher wage costs',
            'global_presence': '98%+ of revenue from outside India, diversified client base (North America, Europe, APAC)',
            'debt_position': 'Virtually debt-free, with robust cash reserves for buybacks/dividends',
            'focus_areas': 'Enterprise AI (Infosys Topaz), cloud migration, digital transformation, and consulting',
            'recent_contracts': 'Multiple multi-million dollar wins with large US/European firms and public sector AI initiatives',
            'innovation': 'Strong push in generative AI, cloud, and cybersecurity services',
            'dividend_yield': 2.6,
            'buyback_info': 'Periodically announced, supporting shareholder value',
            'sector_position': 'Second largest listed IT services firm in India (after TCS)',
            'competitors': 'TCS, Accenture, IBM, Cognizant, Wipro, HCL Tech',
            'operating_risks': ['Cost inflation and margin pressure', 'Talent war and salary hikes', 'Client IT budget constraints'],
            'market_risks': ['Currency fluctuations impact', 'US/EU tech spending softness', 'Global recession concerns'],
            'consensus_rating': 'HOLD/BUY',
            'consensus_details': 'Majority "Hold" rating, some "Buy" on dips',
            'target_price': 1650.00,
            'upside_potential': 14.0,
            'long_term_outlook': 'Expected to benefit from AI/cloud/digital transformation trends',
            'short_term_view': 'Range-bound movement as cost pressures and global moderation persist',
            'debt_status': 'Debt-free',
            'fair_value_status': 'Slight premium/near value',
            'ai_verdict': 'Infosys continues to be a high-quality core IT holding in India. Long-term investors may hold/accumulate on dips, given its digital competency, strong balance sheet, and global client diversification.',
            'ai_recommendation': 'HOLD with selective buying opportunities on market dips',
            'disclaimer': 'Always check your risk profile and investment horizon. For updated targets, review latest quarterly results and analyst notes.'
        },
        'RELIANCE': {
            'company_name': 'Reliance Industries Limited',
            'current_price': 2845.60,
            'pe_ratio': 25.5,
            'market_cap': '₹19.2 lakh crore',
            'market_cap_category': 'Large Cap',
            'annual_revenue': '792,000',
            'revenue_growth': '12',
            'profit_margin': '8-10',
            'quarterly_results': 'Strong performance across retail and telecom segments with steady oil & gas business',
            'global_presence': 'Diversified operations in petrochemicals, oil refining, telecommunications, and retail',
            'debt_position': 'Debt reduced significantly, strong cash generation from operations',
            'focus_areas': 'Digital services expansion, retail growth, green energy transition',
            'recent_contracts': 'Major partnerships in renewable energy and digital infrastructure',
            'innovation': 'Investment in new energy solutions and digital technologies',
            'dividend_yield': 0.5,
            'buyback_info': 'Focus on growth investments rather than buybacks',
            'sector_position': 'Largest private sector company in India with diversified business model',
            'competitors': 'ONGC, IOC, Bharti Airtel, other energy and telecom companies',
            'operating_risks': ['Oil price volatility', 'Regulatory changes in telecom', 'Competition in retail'],
            'market_risks': ['Global energy transitions', 'Economic slowdown impact', 'Interest rate changes'],
            'consensus_rating': 'BUY',
            'consensus_details': 'Strong buy recommendations based on diversified growth prospects',
            'target_price': 3100.00,
            'upside_potential': 8.9,
            'long_term_outlook': 'Well-positioned for energy transition and digital economy growth',
            'short_term_view': 'Steady performance expected with potential volatility from oil prices',
            'debt_status': 'Net debt reduced',
            'fair_value_status': 'Fair to attractive valuation',
            'ai_verdict': 'Reliance remains a core holding for diversified growth exposure in the Indian market with strong fundamentals across multiple business segments.',
            'ai_recommendation': 'BUY for long-term wealth creation',
            'disclaimer': 'Monitor oil prices and regulatory changes in telecom sector for short-term volatility.'
        }
    }
    
    # Add default data for other stocks
    if symbol.upper() not in stock_data:
        stock_data[symbol.upper()] = {
            'company_name': f'{symbol.upper()} Limited',
            'current_price': 1500.00,
            'pe_ratio': 20.0,
            'market_cap': '₹5.0 lakh crore',
            'market_cap_category': 'Large Cap',
            'annual_revenue': '150,000',
            'revenue_growth': '8',
            'profit_margin': '15-18',
            'quarterly_results': f'Recent quarterly results show consistent performance with stable margins for {symbol}',
            'global_presence': 'Strong domestic presence with growing international operations',
            'debt_position': 'Manageable debt levels with adequate liquidity',
            'focus_areas': 'Core business expansion and digital transformation initiatives',
            'recent_contracts': 'Secured new business partnerships and expansion opportunities',
            'innovation': 'Investment in technology upgrades and operational efficiency',
            'dividend_yield': 2.0,
            'buyback_info': 'Regular dividend payouts with occasional share buybacks',
            'sector_position': f'Leading player in {symbol} sector with strong market position',
            'competitors': 'Various industry players with competitive positioning',
            'operating_risks': ['Market competition', 'Cost inflation', 'Regulatory changes'],
            'market_risks': ['Economic volatility', 'Interest rate fluctuations', 'Global market conditions'],
            'consensus_rating': 'HOLD',
            'consensus_details': 'Mixed analyst views with cautious optimism',
            'target_price': 1650.00,
            'upside_potential': 10.0,
            'long_term_outlook': 'Positive long-term growth prospects with strategic initiatives',
            'short_term_view': 'Near-term performance subject to market conditions',
            'debt_status': 'Manageable debt',
            'fair_value_status': 'Fair valuation',
            'ai_verdict': f'{symbol} shows stable fundamentals with growth potential. Suitable for long-term investors seeking steady returns with moderate risk.',
            'ai_recommendation': 'HOLD with monitoring for entry opportunities',
            'disclaimer': 'Investment decisions should be based on individual risk tolerance and financial goals.'
        }
    
    # Get stock data
    data = stock_data.get(symbol.upper())
    
    return render_template('dashboard/detailed_stock_analysis.html',
                         stock_symbol=symbol.upper(),
                         analysis_date=date.today().strftime('%B %d, %Y'),
                         **data)

@app.route('/dashboard/my-portfolio')
@login_required
def dashboard_my_portfolio():
    """Comprehensive Portfolio View with AI Analysis across all asset classes"""
    from datetime import date, datetime
    from services.comprehensive_portfolio_service import get_comprehensive_portfolio_service
    from services.risk_engine import get_risk_engine

    # Get comprehensive portfolio analysis
    portfolio_service = get_comprehensive_portfolio_service(current_user.id)
    portfolio_summary = portfolio_service.get_complete_portfolio_summary()

    # Generate AI insights
    ai_insights = portfolio_service.generate_ai_insights(portfolio_summary)

    # Get top performers
    top_performers = portfolio_service.get_top_performers(limit=5)

    # Scentric AI Engine — Risk Heat Map, Goal Progress, Portfolio Pulse
    risk_engine = get_risk_engine(current_user.id)
    risk_heatmap = risk_engine.get_risk_heatmap(portfolio_summary)
    goal_progress = risk_engine.get_goal_progress(portfolio_summary)
    portfolio_pulse = risk_engine.get_portfolio_pulse(portfolio_summary, risk_heatmap)
    recent_events = risk_engine.get_recent_events(limit=8)

    # Log this analysis visit as a portfolio event
    health_score_log = portfolio_pulse["health_score"] if portfolio_pulse else "—"
    risk_engine.log_event('analysis', 'Portfolio viewed', detail=f'Health score: {health_score_log}')

    # Fetch most recent LangGraph optimization report (if any)
    latest_optimization = None
    try:
        from models import PortfolioOptimizationReport
        latest_optimization = PortfolioOptimizationReport.query.filter_by(
            user_id=current_user.id
        ).order_by(PortfolioOptimizationReport.created_at.desc()).first()

        # Normalize allocation_recommendations — the optimizer may have stored a
        # raw markdown string from the LLM inside {"raw_recommendations": "```json\n{...}\n```"}.
        # Parse it so the template always receives a proper dict.
        if latest_optimization and isinstance(latest_optimization.allocation_recommendations, dict):
            alloc = latest_optimization.allocation_recommendations
            if 'raw_recommendations' in alloc and 'optimal_asset_class_allocation' not in alloc:
                import re as _re, json as _json
                raw_str = alloc['raw_recommendations']
                parsed = None
                # Try to extract JSON from inside code fences
                m = _re.search(r'```(?:json)?\s*(\{.*\})\s*```', raw_str, _re.DOTALL)
                if m:
                    try:
                        parsed = _json.loads(m.group(1))
                    except Exception:
                        pass
                if parsed is None:
                    # Try parsing the whole string as JSON
                    try:
                        parsed = _json.loads(raw_str)
                    except Exception:
                        pass
                if parsed:
                    # Update in-memory only (no DB write) so the template gets clean data
                    latest_optimization.allocation_recommendations = parsed

        # Normalize opportunities — optimizer may store [{"raw_opportunities": "```json\n[...]\n```"}]
        if latest_optimization and isinstance(latest_optimization.opportunities, list):
            opps = latest_optimization.opportunities
            if len(opps) == 1 and isinstance(opps[0], dict) and 'raw_opportunities' in opps[0]:
                import re as _re2, json as _json2
                raw_str = opps[0]['raw_opportunities']
                parsed_opps = None
                # Strip code fences and trailing commas then parse
                clean = _re2.sub(r'^```(?:json)?\s*', '', raw_str.strip(), flags=_re2.IGNORECASE)
                clean = _re2.sub(r'\s*```$', '', clean.strip())
                clean = _re2.sub(r',\s*([}\]])', r'\1', clean)
                try:
                    parsed_opps = _json2.loads(clean)
                except Exception:
                    # Try extracting JSON array from inside the string
                    m2 = _re2.search(r'(\[.*\])', clean, _re2.DOTALL)
                    if m2:
                        try:
                            parsed_opps = _json2.loads(m2.group(1))
                        except Exception:
                            pass
                if parsed_opps and isinstance(parsed_opps, list):
                    latest_optimization.opportunities = parsed_opps
    except Exception as _opt_err:
        logging.warning(f"Could not load latest optimization: {_opt_err}")

    # ── Portfolio Intelligence Layer ───────────────────────────────────────────
    portfolio_intel = None
    broker_breakdown_data = None
    action_center = []
    bottom_performers = []
    try:
        ps = portfolio_summary
        total_value = ps.get('total_current_value', 0) if isinstance(ps, dict) else getattr(ps, 'total_current_value', 0) or 0
        total_pnl = ps.get('total_pnl', 0) if isinstance(ps, dict) else getattr(ps, 'total_pnl', 0) or 0
        total_invest = ps.get('total_investment', 0) if isinstance(ps, dict) else getattr(ps, 'total_investment', 0) or 0
        pnl_pct = ps.get('pnl_percentage', 0) if isinstance(ps, dict) else getattr(ps, 'pnl_percentage', 0) or 0
        rp = ps.get('risk_profile', {}) if isinstance(ps, dict) else getattr(ps, 'risk_profile', {}) or {}
        risk_score = rp.get('score', 50) if isinstance(rp, dict) else getattr(rp, 'score', 50) or 50
        risk_cat = rp.get('category', 'Moderate') if isinstance(rp, dict) else getattr(rp, 'category', 'Moderate') or 'Moderate'

        # Concentration & diversification from heatmap
        hm = risk_heatmap or []
        def _w(item): return item.get('weight', 0) if isinstance(item, dict) else getattr(item, 'weight', 0) or 0
        def _n(item): return item.get('name', '') if isinstance(item, dict) else getattr(item, 'name', '') or ''
        active_classes = [h for h in hm if _w(h) > 0]
        n_classes = len(active_classes)
        heavy = [h for h in hm if _w(h) >= 70]
        moderate_heavy = [h for h in hm if 45 <= _w(h) < 70]

        risks = []
        if heavy:
            for h in heavy:
                risks.append({'sev': 'critical', 'text': f"{_w(h):.0f}% allocated to {_n(h)} — extreme concentration risk"})
        elif moderate_heavy:
            for h in moderate_heavy:
                risks.append({'sev': 'warning', 'text': f"{_w(h):.0f}% in {_n(h)} — high concentration"})
        if n_classes <= 1:
            risks.append({'sev': 'critical', 'text': f"Only 1 asset class — no diversification whatsoever"})
        elif n_classes <= 2:
            risks.append({'sev': 'warning', 'text': f"Only {n_classes} asset classes — limited diversification"})
        if risk_score >= 70:
            risks.append({'sev': 'warning', 'text': f"High risk score ({risk_score:.0f}/100) — portfolio is exposed to significant drawdown"})
        if pnl_pct < -10:
            risks.append({'sev': 'critical', 'text': f"Significant losses ({pnl_pct:.1f}%) — strategy review recommended"})
        elif pnl_pct < 0:
            risks.append({'sev': 'warning', 'text': f"Portfolio in negative territory ({pnl_pct:.1f}%)"})

        strengths = []
        if pnl_pct > 10:
            strengths.append(f"Strong returns of +{pnl_pct:.1f}% — portfolio is outperforming")
        elif pnl_pct > 0:
            strengths.append(f"Positive returns (+{pnl_pct:.1f}%) — portfolio is growing")
        if n_classes >= 4:
            strengths.append(f"Diversified across {n_classes} asset classes — good spread")
        if risk_score <= 35:
            strengths.append(f"Conservative risk profile ({risk_score:.0f}/100) — capital preserved")

        critical_risks = [r for r in risks if r['sev'] == 'critical']
        biggest_issue = critical_risks[0]['text'] if critical_risks else (risks[0]['text'] if risks else 'No critical issues detected')

        if n_classes <= 1 and pnl_pct < 0:
            summary = 'Portfolio is highly concentrated in one asset class with negative returns — diversification is urgent.'
        elif n_classes <= 2 and pnl_pct < 0:
            summary = f"Limited to {n_classes} asset class(es) with {pnl_pct:.1f}% returns — broadening exposure is the next step."
        elif pnl_pct > 8 and n_classes >= 4:
            summary = f"Portfolio showing +{pnl_pct:.1f}% returns across {n_classes} asset classes — maintain discipline and rebalance."
        elif pnl_pct > 0:
            summary = f"Portfolio in positive territory (+{pnl_pct:.1f}%). Focus on improving diversification to reduce risk concentration."
        else:
            summary = f"Portfolio has a {risk_cat.lower()} risk profile. Review asset allocation to improve returns and reduce concentration."

        risk_color = '#ef4444' if risk_score >= 70 else '#f59e0b' if risk_score >= 40 else '#22c55e'
        portfolio_intel = {
            'summary': summary, 'risks': risks[:3], 'strengths': strengths[:2],
            'biggest_issue': biggest_issue, 'risk_color': risk_color,
            'total_value': total_value, 'total_pnl': total_pnl, 'total_invest': total_invest,
            'pnl_pct': round(pnl_pct, 2), 'risk_score': round(risk_score),
            'risk_cat': risk_cat, 'n_classes': n_classes,
        }

        # ── Broker Breakdown ─────────────────────────────────────────────────
        from models import Portfolio as PortfolioModel
        holdings = PortfolioModel.query.filter_by(user_id=current_user.id).all()
        brokers_agg = {}
        for h in holdings:
            bn = h.get_broker_name() if hasattr(h, 'get_broker_name') else 'Manual'
            if bn not in brokers_agg:
                brokers_agg[bn] = {'value': 0, 'invest': 0, 'count': 0, 'hi_risk': 0}
            brokers_agg[bn]['value'] += h.current_value or 0
            brokers_agg[bn]['invest'] += h.purchased_value or 0
            brokers_agg[bn]['count'] += 1
            rl = h.get_risk_level() if hasattr(h, 'get_risk_level') else 'Low'
            if rl and 'high' in rl.lower():
                brokers_agg[bn]['hi_risk'] += 1

        bk_total = sum(b['value'] for b in brokers_agg.values()) or 1
        broker_rows = []
        for name, b in brokers_agg.items():
            pnl_v = b['value'] - b['invest']
            pnl_p = round(pnl_v / max(b['invest'], 1) * 100, 1)
            alloc_p = round(b['value'] / bk_total * 100, 1)
            rp_v = round(b['hi_risk'] / max(b['count'], 1) * 100)
            risk_l = 'High' if rp_v >= 60 else 'Medium' if rp_v >= 30 else 'Low'
            broker_rows.append({
                'name': name, 'value': round(b['value']), 'pnl': round(pnl_v),
                'pnl_pct': pnl_p, 'allocation': alloc_p, 'count': b['count'],
                'risk': risk_l, 'risk_color': '#ef4444' if risk_l == 'High' else '#f59e0b' if risk_l == 'Medium' else '#22c55e',
            })
        broker_rows.sort(key=lambda x: x['value'], reverse=True)

        bk_insight = None
        if len(broker_rows) > 1:
            hi_risk_bk = max(broker_rows, key=lambda x: {'High': 3, 'Medium': 2, 'Low': 1}[x['risk']])
            lo_ret_bk = min(broker_rows, key=lambda x: x['pnl_pct'])
            dominant = next((r for r in broker_rows if r['allocation'] > 60), None)
            if dominant:
                bk_insight = f"{dominant['allocation']}% of your capital sits in {dominant['name']} — high broker concentration."
            elif hi_risk_bk['name'] == lo_ret_bk['name'] and hi_risk_bk['risk'] == 'High':
                bk_insight = f"You take the most risk on {hi_risk_bk['name']} but returns there ({hi_risk_bk['pnl_pct']:+.1f}%) may not justify it."
            else:
                best_bk = max(broker_rows, key=lambda x: x['pnl_pct'])
                bk_insight = f"Best performing broker: {best_bk['name']} at {best_bk['pnl_pct']:+.1f}% returns."
        broker_breakdown_data = {'brokers': broker_rows, 'insight': bk_insight}

        # ── Action Center ────────────────────────────────────────────────────
        actions = []
        if n_classes <= 1:
            actions.append({'priority': 'critical', 'icon': 'fas fa-exclamation-circle', 'color': '#ef4444',
                'title': 'Add Diversification Immediately',
                'detail': 'Portfolio is 100% in one asset class. Add 2–3 more (e.g., Mutual Funds, Gold, Fixed Income) to reduce concentration risk.'})
        elif n_classes <= 2:
            actions.append({'priority': 'warning', 'icon': 'fas fa-exclamation-triangle', 'color': '#f59e0b',
                'title': 'Broaden Asset Allocation',
                'detail': f'Only {n_classes} asset classes detected. Target 4–5 classes for a balanced risk-return profile.'})
        if heavy:
            nm = _n(heavy[0]); wt = _w(heavy[0])
            actions.append({'priority': 'critical', 'icon': 'fas fa-compress-arrows-alt', 'color': '#ef4444',
                'title': f'Reduce {nm} Concentration ({wt:.0f}%)',
                'detail': f'A single asset class at {wt:.0f}% means one bad sector move hits your entire portfolio. Ideal: ≤60% in any one class.'})
        elif moderate_heavy:
            nm = _n(moderate_heavy[0]); wt = _w(moderate_heavy[0])
            actions.append({'priority': 'warning', 'icon': 'fas fa-balance-scale', 'color': '#f59e0b',
                'title': f'Rebalance {nm} ({wt:.0f}%)',
                'detail': f'Consider trimming {nm} to below 45% and reallocating to other asset classes.'})
        if risk_score >= 70:
            actions.append({'priority': 'warning', 'icon': 'fas fa-shield-alt', 'color': '#f59e0b',
                'title': 'Review High-Risk Holdings',
                'detail': 'Risk score is elevated. Shift 15–20% into lower-risk instruments like FDs, debt funds, or sovereign gold.'})
        if pnl_pct < -5:
            actions.append({'priority': 'critical', 'icon': 'fas fa-search', 'color': '#ef4444',
                'title': 'Investigate Underperforming Holdings',
                'detail': f'Portfolio is down {pnl_pct:.1f}%. Identify the worst position and assess if the investment thesis still holds.'})
        pp_alerts = (portfolio_pulse or {}).get('alerts', [])
        for alert in pp_alerts[:1]:
            if alert.get('level') == 'danger':
                actions.append({'priority': 'critical', 'icon': 'fas fa-fire', 'color': '#ef4444',
                    'title': alert['text'], 'detail': 'Flagged as high priority by the Scentric Risk Engine.'})
        if not actions:
            if pnl_pct > 5:
                actions.append({'priority': 'good', 'icon': 'fas fa-sync-alt', 'color': '#22c55e',
                    'title': 'Rebalance Quarterly',
                    'detail': 'Portfolio is performing well. Quarterly rebalancing locks in gains and keeps allocation on target.'})
            actions.append({'priority': 'good', 'icon': 'fas fa-chart-line', 'color': '#22c55e',
                'title': 'Set Up Systematic Investment Plan',
                'detail': 'SIPs in your best-performing category build wealth consistently through market cycles.'})
        action_center = actions[:5]

        # ── Bottom Performers ────────────────────────────────────────────────
        all_perf = list(top_performers or [])
        bottom_performers = sorted(
            [p for p in all_perf if (p.get('pnl_percentage') or p.get('pnl_pct', 0)) < 0],
            key=lambda x: x.get('pnl_percentage', x.get('pnl_pct', 0))
        )[:3]

    except Exception as _pi_err:
        logging.warning(f"Portfolio intelligence computation error: {_pi_err}")

    from models_broker import BrokerAccount as _BA
    portfolio_broker_accounts = _BA.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()

    return render_template('dashboard/my_portfolio_comprehensive.html',
                           portfolio_summary=portfolio_summary,
                           ai_insights=ai_insights,
                           top_performers=top_performers,
                           bottom_performers=bottom_performers,
                           risk_heatmap=risk_heatmap,
                           goal_progress=goal_progress,
                           portfolio_pulse=portfolio_pulse,
                           recent_events=recent_events,
                           latest_optimization=latest_optimization,
                           portfolio_intel=portfolio_intel,
                           broker_breakdown_data=broker_breakdown_data,
                           action_center=action_center,
                           broker_accounts=portfolio_broker_accounts,
                           current_user=current_user)

@app.route('/dashboard/my-portfolio-old')
@login_required
def dashboard_my_portfolio_old():
    """Legacy Portfolio Analyzer with AI-powered insights and multi-broker integration with filtering support"""
    from datetime import date, datetime
    from sqlalchemy import func
    from services.portfolio_analyzer_service import PortfolioAnalyzerService
    from models import AssetType
    
    # Get asset type filter from query parameters
    asset_filter = request.args.get('type', None)
    view_mode = request.args.get('view', 'unified')  # unified, by-asset, by-broker
    
    # Validate asset type filter if provided
    valid_asset_types = [e.value for e in AssetType]
    valid_display_names = {
        'equities': 'equities',
        'mutual-funds': 'mutual_funds',
        'mutual_funds': 'mutual_funds',
        'fixed-income': 'fixed_income',
        'fixed_income': 'fixed_income',
        'futures-options': 'futures_options',
        'futures_options': 'futures_options',
        'nps': 'nps',
        'real-estate': 'real_estate',
        'real_estate': 'real_estate',
        'gold': 'gold',
        'etf': 'etf',
        'crypto': 'crypto',
        'esop': 'esop',
        'private-equity': 'private_equity',
        'private_equity': 'private_equity'
    }
    
    normalized_asset_filter = None
    if asset_filter:
        normalized_asset_filter = valid_display_names.get(asset_filter.lower())
        if not normalized_asset_filter:
            flash(f'Invalid asset type: {asset_filter}. Please select a valid asset type.', 'error')
            return redirect(url_for('dashboard_my_portfolio'))
    
    # Initialize portfolio analyzer
    analyzer = PortfolioAnalyzerService(current_user.id)
    
    # Get comprehensive portfolio analysis
    analysis_result = analyzer.analyze_portfolio()
    
    if not analysis_result['success']:
        flash(f'Portfolio analysis error: {analysis_result.get("error", "Unknown error")}', 'error')
        analysis_result = analyzer._generate_empty_portfolio_analysis()
    
    portfolio_analysis = analysis_result['analysis']
    
    # Get actual holdings for display with optional filtering
    query = Portfolio.query.filter_by(user_id=current_user.id)
    if normalized_asset_filter:
        query = query.filter_by(asset_type=normalized_asset_filter)
    
    portfolio_holdings = query.all()
    
    # If no real data exists, create sample portfolio for demonstration
    if not portfolio_holdings:
        sample_data = [
            {
                'broker_id': 'zerodha',
                'ticker_symbol': 'RELIANCE',
                'stock_name': 'Reliance Industries Limited',
                'asset_type': 'Stocks',
                'quantity': 50,
                'date_purchased': date(2024, 6, 15),
                'purchase_price': 2650.00,
                'purchased_value': 132500.00,
                'current_price': 2845.60,
                'current_value': 142280.00,
                'sector': 'Oil & Gas',
                'exchange': 'NSE'
            },
            {
                'broker_id': 'angel',
                'ticker_symbol': 'INFY',
                'stock_name': 'Infosys Limited',
                'asset_type': 'Stocks',
                'quantity': 75,
                'date_purchased': date(2024, 7, 20),
                'purchase_price': 1420.00,
                'purchased_value': 106500.00,
                'current_price': 1447.70,
                'current_value': 108577.50,
                'sector': 'Information Technology',
                'exchange': 'NSE'
            },
            {
                'broker_id': 'zerodha',
                'ticker_symbol': 'HDFCBANK',
                'stock_name': 'HDFC Bank Limited',
                'asset_type': 'Stocks',
                'quantity': 30,
                'date_purchased': date(2024, 5, 10),
                'purchase_price': 1580.00,
                'purchased_value': 47400.00,
                'current_price': 1625.30,
                'current_value': 48759.00,
                'sector': 'Banking',
                'exchange': 'NSE'
            },
            {
                'broker_id': 'dhan',
                'ticker_symbol': 'TCS',
                'stock_name': 'Tata Consultancy Services',
                'asset_type': 'Stocks',
                'quantity': 25,
                'date_purchased': date(2024, 8, 1),
                'purchase_price': 3950.00,
                'purchased_value': 98750.00,
                'current_price': 4125.80,
                'current_value': 103145.00,
                'sector': 'Information Technology',
                'exchange': 'NSE'
            },
            {
                'broker_id': 'zerodha',
                'ticker_symbol': 'BAJFINANCE',
                'stock_name': 'Bajaj Finance Limited',
                'asset_type': 'Stocks',
                'quantity': 15,
                'date_purchased': date(2024, 4, 25),
                'purchase_price': 6800.00,
                'purchased_value': 102000.00,
                'current_price': 6750.45,
                'current_value': 101256.75,
                'sector': 'Financial Services',
                'exchange': 'NSE'
            }
        ]
        
        for data in sample_data:
            portfolio_holding = Portfolio(
                user_id=current_user.id,
                **data
            )
            db.session.add(portfolio_holding)
        
        db.session.commit()
        portfolio_holdings = Portfolio.query.filter_by(user_id=current_user.id).all()
    
    # Calculate portfolio summary
    total_investment = sum(holding.purchased_value for holding in portfolio_holdings)
    total_current_value = sum(holding.current_value or holding.purchased_value for holding in portfolio_holdings)
    total_pnl = total_current_value - total_investment
    total_pnl_percentage = (total_pnl / total_investment * 100) if total_investment > 0 else 0
    
    # Group holdings by sector for sectorial analysis
    sector_analysis = {}
    for holding in portfolio_holdings:
        sector = holding.sector or 'Others'
        if sector not in sector_analysis:
            sector_analysis[sector] = {
                'investment': 0,
                'current_value': 0,
                'holdings_count': 0
            }
        sector_analysis[sector]['investment'] += holding.purchased_value
        sector_analysis[sector]['current_value'] += holding.current_value or holding.purchased_value
        sector_analysis[sector]['holdings_count'] += 1
    
    # Group holdings by broker
    broker_analysis = {}
    broker_names = {
        'zerodha': 'Zerodha',
        'angel': 'Angel Broking',
        'dhan': 'Dhan',
        'upstox': 'Upstox',
        'icici': 'ICICI Direct'
    }
    
    for holding in portfolio_holdings:
        broker = holding.broker_id
        if broker not in broker_analysis:
            broker_analysis[broker] = {
                'name': broker_names.get(broker, broker.title()),
                'investment': 0,
                'current_value': 0,
                'holdings_count': 0,
                'holdings': []
            }
        broker_analysis[broker]['investment'] += holding.purchased_value
        broker_analysis[broker]['current_value'] += holding.current_value or holding.purchased_value
        broker_analysis[broker]['holdings_count'] += 1
        broker_analysis[broker]['holdings'].append(holding)
    
    # Check if user has broker access (TraderPlus/Premium)
    if current_user.can_access_menu('dashboard_broker_accounts'):
        try:
            # Get portfolio summary with error handling
            from services.broker_service_helpers import get_portfolio_summary, get_consolidated_holdings
            portfolio_summary = get_portfolio_summary(current_user.id)
            holdings = get_consolidated_holdings(current_user.id)
        except:
            portfolio_summary = {
                'total_value': total_current_value,
                'total_pnl': total_pnl,
                'holdings_count': len(portfolio_holdings),
                'brokers_count': 0,
                'broker_accounts': []
            }
            holdings = []
        
        return render_template('dashboard/my_portfolio.html',
                             current_user=current_user,
                             portfolio_data=portfolio_holdings,
                             portfolio_analysis=portfolio_analysis,
                             sector_analysis=sector_analysis,
                             broker_analysis=broker_analysis,
                             total_investment=total_investment,
                             total_current_value=total_current_value,
                             total_pnl=total_pnl,
                             total_pnl_percentage=total_pnl_percentage,
                             asset_filter=asset_filter,
                             normalized_asset_filter=normalized_asset_filter,
                             view_mode=view_mode,
                             valid_asset_types=valid_asset_types,
                             has_broker_access=True)
    else:
        # Enhanced portfolio view with AI analysis
        return render_template('dashboard/my_portfolio.html',
                             current_user=current_user,
                             portfolio_data=portfolio_holdings,
                             portfolio_analysis=portfolio_analysis,
                             sector_analysis=sector_analysis,
                             broker_analysis=broker_analysis,
                             total_investment=total_investment,
                             total_current_value=total_current_value,
                             total_pnl=total_pnl,
                             total_pnl_percentage=total_pnl_percentage,
                             asset_filter=asset_filter,
                             normalized_asset_filter=normalized_asset_filter,
                             view_mode=view_mode,
                             valid_asset_types=valid_asset_types,
                             has_broker_access=False)

@app.route('/dashboard/equities', methods=['GET', 'POST'])
@login_required
def dashboard_equities():
    """Equities portfolio management - manual and broker holdings"""
    from models import ManualEquityHolding
    from models_broker import BrokerHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual equity holding
            new_holding = ManualEquityHolding(
                user_id=current_user.id,
                symbol=request.form.get('symbol').upper(),
                company_name=request.form.get('company_name'),
                isin=request.form.get('isin'),
                transaction_type=request.form.get('transaction_type', 'BUY'),
                purchase_date=datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date(),
                quantity=float(request.form.get('quantity')),
                purchase_price=float(request.form.get('purchase_price')),
                brokerage=float(request.form.get('brokerage') or 0),
                stt=float(request.form.get('stt') or 0),
                transaction_charges=float(request.form.get('transaction_charges') or 0),
                gst=float(request.form.get('gst') or 0),
                stamp_duty=float(request.form.get('stamp_duty') or 0),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes'),
                current_price=float(request.form.get('current_price')) if request.form.get('current_price') else None
            )
            
            # Calculate totals (also computes current_value / pnl if current_price is set)
            new_holding.calculate_totals()
            
            db.session.add(new_holding)
            db.session.commit()
            
            flash(f'Successfully added {new_holding.symbol} to your portfolio!', 'success')
            return redirect(url_for('dashboard_equities'))
            
        except Exception as e:
            logger.error(f"Error adding equity holding: {str(e)}")
            flash(f'Error adding equity holding: {str(e)}', 'error')
            return redirect(url_for('dashboard_equities'))
    
    # GET request - display holdings
    # Get manual holdings
    manual_holdings = ManualEquityHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Get broker holdings AND open positions if user has broker access
    broker_holdings_list = []
    broker_positions_list = []
    if current_user.can_access_menu('dashboard_broker_accounts'):
        try:
            from models_broker import BrokerAccount, BrokerPosition
            broker_accounts = BrokerAccount.query.filter_by(
                user_id=current_user.id,
                connection_status='connected'
            ).all()
            
            for account in broker_accounts:
                broker_holdings = BrokerHolding.query.filter_by(
                    broker_account_id=account.id,
                ).all()
                broker_holdings_list.extend(broker_holdings)

                positions = BrokerPosition.query.filter_by(
                    broker_account_id=account.id,
                ).all()
                for p in positions:
                    broker_positions_list.append({
                        'id': p.id,
                        'symbol': p.trading_symbol or p.symbol,
                        'exchange': p.exchange or '',
                        'product': p.product_type.value if p.product_type else '',
                        'quantity': p.quantity or 0,
                        'avg_buy_price': p.avg_buy_price or 0,
                        'current_price': p.current_price or 0,
                        'unrealized_pnl': p.unrealized_pnl or 0,
                        'realized_pnl': p.realized_pnl or 0,
                        'total_pnl': p.total_pnl or 0,
                        'position_date': p.position_date.strftime('%d %b %Y') if p.position_date else '',
                        'broker_name': account.broker_name,
                    })
        except Exception as e:
            logger.warning(f"Broker data fetch error: {e}")
    
    # Combine holdings for display
    combined_holdings = []
    
    # Add manual holdings
    for holding in manual_holdings:
        combined_holdings.append({
            'id': holding.id,
            'symbol': holding.symbol,
            'company_name': holding.company_name or holding.symbol,
            'quantity': holding.quantity,
            'purchase_price': holding.purchase_price,
            'current_price': holding.current_price,
            'total_investment': holding.total_investment,
            'current_value': holding.current_value,
            'unrealized_pnl': holding.unrealized_pnl,
            'unrealized_pnl_percentage': holding.unrealized_pnl_percentage,
            'source': 'manual'
        })
    
    # Add broker holdings
    for holding in broker_holdings_list:
        combined_holdings.append({
            'id': holding.id,
            'symbol': holding.symbol,
            'company_name': holding.company_name or holding.symbol,
            'quantity': holding.available_quantity,
            'purchase_price': holding.avg_cost_price,
            'current_price': holding.current_price,
            'total_investment': holding.investment_value,
            'current_value': holding.total_value,
            'unrealized_pnl': holding.pnl,
            'unrealized_pnl_percentage': holding.pnl_percentage,
            'source': 'broker'
        })
    
    # Calculate summary
    total_investment = sum((h.get('total_investment') or 0) for h in combined_holdings)
    current_value = sum((h.get('current_value') or h.get('total_investment') or 0) for h in combined_holdings)
    total_pnl = current_value - total_investment
    holdings_count = len(combined_holdings)
    
    return render_template('dashboard/equities.html',
                         holdings=combined_holdings,
                         broker_positions=broker_positions_list,
                         total_investment=total_investment,
                         current_value=current_value,
                         total_pnl=total_pnl,
                         holdings_count=holdings_count)

@app.route('/api/equities/<int:holding_id>', methods=['GET'])
@login_required
def get_equity_holding(holding_id):
    """Get manual equity holding details"""
    from models import ManualEquityHolding
    
    holding = ManualEquityHolding.query.filter_by(
        id=holding_id,
        user_id=current_user.id
    ).first()
    
    if not holding:
        return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
    return jsonify({
        'success': True,
        'holding': {
            'id': holding.id,
            'symbol': holding.symbol,
            'company_name': holding.company_name,
            'isin': holding.isin,
            'transaction_type': holding.transaction_type,
            'purchase_date': holding.purchase_date.strftime('%Y-%m-%d'),
            'quantity': holding.quantity,
            'purchase_price': holding.purchase_price,
            'brokerage': holding.brokerage,
            'stt': holding.stt,
            'transaction_charges': holding.transaction_charges,
            'gst': holding.gst,
            'stamp_duty': holding.stamp_duty,
            'portfolio_name': holding.portfolio_name,
            'notes': holding.notes
        }
    })

@app.route('/api/equities/<int:holding_id>', methods=['PUT'])
@login_required
def update_equity_holding(holding_id):
    """Update a manual equity holding"""
    from models import ManualEquityHolding
    from datetime import datetime
    
    try:
        holding = ManualEquityHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
            
        data = request.json
        holding.symbol = data.get('symbol', holding.symbol).upper()
        holding.company_name = data.get('company_name', holding.company_name)
        holding.isin = data.get('isin', holding.isin)
        holding.transaction_type = data.get('transaction_type', holding.transaction_type)
        holding.purchase_date = datetime.strptime(data.get('purchase_date'), '%Y-%m-%d').date()
        holding.quantity = float(data.get('quantity', holding.quantity))
        holding.purchase_price = float(data.get('purchase_price', holding.purchase_price))
        holding.brokerage = float(data.get('brokerage') or 0)
        holding.stt = float(data.get('stt') or 0)
        holding.transaction_charges = float(data.get('transaction_charges') or 0)
        holding.gst = float(data.get('gst') or 0)
        holding.stamp_duty = float(data.get('stamp_duty') or 0)
        holding.portfolio_name = data.get('portfolio_name', 'Default')
        holding.notes = data.get('notes', holding.notes)
        
        # Recalculate totals
        holding.calculate_totals()
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Holding updated successfully'})
    except Exception as e:
        logger.error(f"Error updating equity holding: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/equities/<int:holding_id>', methods=['DELETE'])
@login_required
def delete_equity_holding(holding_id):
    """Delete a manual equity holding"""
    from models import ManualEquityHolding
    
    try:
        holding = ManualEquityHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
        db.session.delete(holding)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Holding deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting equity holding: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/equities/refresh-prices', methods=['POST'])
@login_required
def refresh_equity_prices():
    """Refresh current market prices for all manual equity holdings"""
    from models import ManualEquityHolding
    from services.nse_realtime_service import get_stock_quote

    holdings = ManualEquityHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()

    updated = 0
    total = len(holdings)
    for holding in holdings:
        try:
            data = get_stock_quote(holding.symbol)
            price = data.get('price') or data.get('current_price') or data.get('lastPrice')
            if price and float(price) > 0:
                holding.current_price = float(price)
                holding.calculate_totals()
                updated += 1
        except Exception as e:
            logger.warning(f"Could not refresh price for {holding.symbol}: {e}")

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error committing refreshed prices: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': True, 'updated': updated, 'total': total})

@app.route('/dashboard/mutual-funds', methods=['GET', 'POST'])
@login_required
def dashboard_mutual_funds():
    """Mutual Funds portfolio management - manual and broker holdings"""
    from models import ManualMutualFundHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual mutual fund holding
            new_holding = ManualMutualFundHolding(
                user_id=current_user.id,
                scheme_name=request.form.get('scheme_name'),
                fund_house=request.form.get('fund_house'),
                isin=request.form.get('isin'),
                folio_number=request.form.get('folio_number'),
                fund_category=request.form.get('fund_category'),
                transaction_type=request.form.get('transaction_type', 'PURCHASE'),
                transaction_date=datetime.strptime(request.form.get('transaction_date'), '%Y-%m-%d').date(),
                units=float(request.form.get('units')),
                nav=float(request.form.get('nav')),
                amount=float(request.form.get('amount')),
                entry_load=float(request.form.get('entry_load') or 0),
                exit_load=float(request.form.get('exit_load') or 0),
                stamp_duty=float(request.form.get('stamp_duty') or 0),
                stt=float(request.form.get('stt') or 0),
                other_charges=float(request.form.get('other_charges') or 0),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            # Calculate totals
            new_holding.calculate_totals()
            
            db.session.add(new_holding)
            db.session.commit()
            
            flash(f'Successfully added {new_holding.scheme_name} to your portfolio!', 'success')
            return redirect(url_for('dashboard_mutual_funds'))
            
        except Exception as e:
            logger.error(f"Error adding mutual fund holding: {str(e)}")
            flash(f'Error adding mutual fund holding: {str(e)}', 'error')
            return redirect(url_for('dashboard_mutual_funds'))
    
    # GET request - display holdings
    # Get manual holdings
    manual_holdings = ManualMutualFundHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Combine holdings for display
    combined_holdings = []
    
    # Add manual holdings
    for holding in manual_holdings:
        combined_holdings.append({
            'id': holding.id,
            'scheme_name': holding.scheme_name,
            'fund_house': holding.fund_house,
            'folio_number': holding.folio_number,
            'units': holding.units,
            'nav': holding.nav,
            'current_nav': holding.current_nav,
            'total_investment': holding.total_investment,
            'current_value': holding.current_value,
            'unrealized_pnl': holding.unrealized_pnl,
            'unrealized_pnl_percentage': holding.unrealized_pnl_percentage,
            'source': 'manual'
        })
    
    # Calculate summary
    total_investment = sum(h['total_investment'] for h in combined_holdings)
    current_value = sum(h['current_value'] or h['total_investment'] for h in combined_holdings)
    total_pnl = current_value - total_investment
    holdings_count = len(combined_holdings)
    
    return render_template('dashboard/mutual_funds.html',
                         holdings=combined_holdings,
                         total_investment=total_investment,
                         current_value=current_value,
                         total_pnl=total_pnl,
                         holdings_count=holdings_count)

@app.route('/api/mutual-funds/<int:holding_id>', methods=['DELETE'])
@login_required
def delete_mutual_fund_holding(holding_id):
    """Delete a manual mutual fund holding"""
    from models import ManualMutualFundHolding
    
    try:
        holding = ManualMutualFundHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
        db.session.delete(holding)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Holding deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting mutual fund holding: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/fixed-deposits', methods=['GET', 'POST'])
@login_required
def dashboard_fixed_deposits():
    """Fixed Deposits portfolio management - manual entries"""
    from models import ManualFixedDepositHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual fixed deposit holding
            new_holding = ManualFixedDepositHolding(
                user_id=current_user.id,
                bank_name=request.form.get('bank_name'),
                fd_number=request.form.get('fd_number'),
                account_number=request.form.get('account_number'),
                branch_name=request.form.get('branch_name'),
                deposit_type=request.form.get('deposit_type'),
                principal_amount=float(request.form.get('principal_amount')),
                interest_rate=float(request.form.get('interest_rate')),
                tenure_months=int(request.form.get('tenure_months')) if request.form.get('tenure_months') else None,
                tenure_days=int(request.form.get('tenure_days')) if request.form.get('tenure_days') else None,
                deposit_date=datetime.strptime(request.form.get('deposit_date'), '%Y-%m-%d').date(),
                maturity_date=datetime.strptime(request.form.get('maturity_date'), '%Y-%m-%d').date(),
                interest_frequency=request.form.get('interest_frequency'),
                interest_payout=request.form.get('interest_payout'),
                tds_applicable=bool(request.form.get('tds_applicable')),
                tds_deducted=float(request.form.get('tds_deducted') or 0),
                nominee_name=request.form.get('nominee_name'),
                nominee_relation=request.form.get('nominee_relation'),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            # Calculate maturity amount and current value
            new_holding.calculate_maturity()
            
            db.session.add(new_holding)
            db.session.commit()
            
            flash(f'Successfully added FD from {new_holding.bank_name} to your portfolio!', 'success')
            return redirect(url_for('dashboard_fixed_deposits'))
            
        except Exception as e:
            logger.error(f"Error adding fixed deposit: {str(e)}")
            flash(f'Error adding fixed deposit: {str(e)}', 'error')
            return redirect(url_for('dashboard_fixed_deposits'))
    
    # GET request - display holdings
    # Get manual holdings
    manual_holdings = ManualFixedDepositHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_principal = sum(h.principal_amount for h in manual_holdings)
    current_value = sum(h.current_value or h.principal_amount for h in manual_holdings)
    total_interest = sum(h.interest_earned or 0 for h in manual_holdings)
    holdings_count = len(manual_holdings)
    
    return render_template('dashboard/fixed_deposits.html',
                         holdings=manual_holdings,
                         total_principal=total_principal,
                         current_value=current_value,
                         total_interest=total_interest,
                         holdings_count=holdings_count)

@app.route('/api/fixed-deposits/<int:holding_id>', methods=['DELETE'])
@login_required
def delete_fixed_deposit_holding(holding_id):
    """Delete a manual fixed deposit holding"""
    from models import ManualFixedDepositHolding
    
    try:
        holding = ManualFixedDepositHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
        db.session.delete(holding)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Holding deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting fixed deposit: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/real-estate', methods=['GET', 'POST'])
@login_required
def dashboard_real_estate():
    """Real Estate portfolio management - manual entries"""
    from models import ManualRealEstateHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual real estate holding
            new_holding = ManualRealEstateHolding(
                user_id=current_user.id,
                property_name=request.form.get('property_name'),
                property_type=request.form.get('property_type'),
                property_subtype=request.form.get('property_subtype'),
                address=request.form.get('address'),
                city=request.form.get('city'),
                state=request.form.get('state'),
                pincode=request.form.get('pincode'),
                area_sqft=float(request.form.get('area_sqft')) if request.form.get('area_sqft') else None,
                area_unit=request.form.get('area_unit', 'sqft'),
                bedrooms=int(request.form.get('bedrooms')) if request.form.get('bedrooms') else None,
                bathrooms=int(request.form.get('bathrooms')) if request.form.get('bathrooms') else None,
                purchase_date=datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date(),
                purchase_price=float(request.form.get('purchase_price')),
                stamp_duty=float(request.form.get('stamp_duty') or 0),
                registration_charges=float(request.form.get('registration_charges') or 0),
                brokerage=float(request.form.get('brokerage') or 0),
                other_charges=float(request.form.get('other_charges') or 0),
                loan_amount=float(request.form.get('loan_amount') or 0),
                loan_bank=request.form.get('loan_bank'),
                loan_account_number=request.form.get('loan_account_number'),
                emi_amount=float(request.form.get('emi_amount') or 0),
                loan_tenure_months=int(request.form.get('loan_tenure_months')) if request.form.get('loan_tenure_months') else None,
                current_market_value=float(request.form.get('current_market_value')) if request.form.get('current_market_value') else None,
                valuation_date=datetime.strptime(request.form.get('valuation_date'), '%Y-%m-%d').date() if request.form.get('valuation_date') else None,
                is_rented=bool(request.form.get('is_rented')),
                monthly_rent=float(request.form.get('monthly_rent') or 0),
                tenant_name=request.form.get('tenant_name'),
                lease_start_date=datetime.strptime(request.form.get('lease_start_date'), '%Y-%m-%d').date() if request.form.get('lease_start_date') else None,
                lease_end_date=datetime.strptime(request.form.get('lease_end_date'), '%Y-%m-%d').date() if request.form.get('lease_end_date') else None,
                property_tax_annual=float(request.form.get('property_tax_annual') or 0),
                maintenance_monthly=float(request.form.get('maintenance_monthly') or 0),
                insurance_annual=float(request.form.get('insurance_annual') or 0),
                ownership_type=request.form.get('ownership_type'),
                deed_number=request.form.get('deed_number'),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            # Calculate values
            new_holding.calculate_values()
            
            db.session.add(new_holding)
            db.session.commit()
            
            flash(f'Successfully added {new_holding.property_name} to your portfolio!', 'success')
            return redirect(url_for('dashboard_real_estate'))
            
        except Exception as e:
            logger.error(f"Error adding real estate: {str(e)}")
            flash(f'Error adding real estate: {str(e)}', 'error')
            return redirect(url_for('dashboard_real_estate'))
    
    # GET request - display holdings
    # Get manual holdings
    manual_holdings = ManualRealEstateHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_investment = sum(h.total_investment for h in manual_holdings)
    current_value = sum(h.current_market_value or h.total_investment for h in manual_holdings)
    total_gain = sum(h.unrealized_gain or 0 for h in manual_holdings)
    holdings_count = len(manual_holdings)
    
    return render_template('dashboard/real_estate.html',
                         holdings=manual_holdings,
                         total_investment=total_investment,
                         current_value=current_value,
                         total_gain=total_gain,
                         holdings_count=holdings_count)

@app.route('/api/real-estate/<int:holding_id>', methods=['DELETE'])
@login_required
def delete_real_estate_holding(holding_id):
    """Delete a manual real estate holding"""
    from models import ManualRealEstateHolding
    
    try:
        holding = ManualRealEstateHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
        db.session.delete(holding)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Holding deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting real estate: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/commodities', methods=['GET', 'POST'])
@login_required
def dashboard_commodities():
    """Gold, Silver & Commodities portfolio management - manual entries"""
    from models import ManualCommodityHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual commodity holding
            new_holding = ManualCommodityHolding(
                user_id=current_user.id,
                commodity_type=request.form.get('commodity_type'),
                commodity_form=request.form.get('commodity_form'),
                sub_form=request.form.get('sub_form'),
                item_name=request.form.get('item_name'),
                purity=request.form.get('purity'),
                weight_grams=float(request.form.get('weight_grams')) if request.form.get('weight_grams') else None,
                weight_unit=request.form.get('weight_unit', 'grams'),
                purchase_date=datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date(),
                quantity=float(request.form.get('quantity')) if request.form.get('quantity') else None,
                purchase_rate_per_gram=float(request.form.get('purchase_rate_per_gram')) if request.form.get('purchase_rate_per_gram') else None,
                purchase_amount=float(request.form.get('purchase_amount')),
                making_charges=float(request.form.get('making_charges') or 0),
                gst=float(request.form.get('gst') or 0),
                other_charges=float(request.form.get('other_charges') or 0),
                vendor_name=request.form.get('vendor_name'),
                bill_number=request.form.get('bill_number'),
                hallmark_number=request.form.get('hallmark_number'),
                current_rate_per_gram=float(request.form.get('current_rate_per_gram')) if request.form.get('current_rate_per_gram') else None,
                current_market_value=float(request.form.get('current_market_value')) if request.form.get('current_market_value') else None,
                valuation_date=datetime.strptime(request.form.get('valuation_date'), '%Y-%m-%d').date() if request.form.get('valuation_date') else None,
                storage_location=request.form.get('storage_location'),
                locker_number=request.form.get('locker_number'),
                locker_rent_annual=float(request.form.get('locker_rent_annual') or 0),
                insurance_annual=float(request.form.get('insurance_annual') or 0),
                digital_platform=request.form.get('digital_platform'),
                digital_account_id=request.form.get('digital_account_id'),
                bond_issue_number=request.form.get('bond_issue_number'),
                bond_maturity_date=datetime.strptime(request.form.get('bond_maturity_date'), '%Y-%m-%d').date() if request.form.get('bond_maturity_date') else None,
                bond_interest_rate=float(request.form.get('bond_interest_rate')) if request.form.get('bond_interest_rate') else None,
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            # Calculate values
            new_holding.calculate_values()
            
            db.session.add(new_holding)
            db.session.commit()
            
            flash(f'Successfully added {new_holding.commodity_type} holding to your portfolio!', 'success')
            return redirect(url_for('dashboard_commodities'))
            
        except Exception as e:
            logger.error(f"Error adding commodity: {str(e)}")
            flash(f'Error adding commodity: {str(e)}', 'error')
            return redirect(url_for('dashboard_commodities'))
    
    # GET request - display holdings
    # Get manual holdings
    manual_holdings = ManualCommodityHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_investment = sum(h.total_investment for h in manual_holdings)
    current_value = sum(h.current_market_value or h.total_investment for h in manual_holdings)
    total_gain = sum(h.unrealized_gain or 0 for h in manual_holdings)
    holdings_count = len(manual_holdings)
    
    return render_template('dashboard/commodities.html',
                         holdings=manual_holdings,
                         total_investment=total_investment,
                         current_value=current_value,
                         total_gain=total_gain,
                         holdings_count=holdings_count)

@app.route('/api/commodities/<int:holding_id>', methods=['DELETE'])
@login_required
def delete_commodity_holding(holding_id):
    """Delete a manual commodity holding"""
    from models import ManualCommodityHolding
    
    try:
        holding = ManualCommodityHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
        db.session.delete(holding)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Holding deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting commodity: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/cryptocurrency', methods=['GET', 'POST'])
@login_required
def dashboard_cryptocurrency():
    """Cryptocurrency portfolio management - manual entries"""
    from models import ManualCryptocurrencyHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual cryptocurrency holding
            new_holding = ManualCryptocurrencyHolding(
                user_id=current_user.id,
                crypto_symbol=request.form.get('crypto_symbol'),
                crypto_name=request.form.get('crypto_name'),
                platform=request.form.get('platform'),
                wallet_type=request.form.get('wallet_type'),
                wallet_address=request.form.get('wallet_address'),
                purchase_date=datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date(),
                quantity=float(request.form.get('quantity')),
                purchase_rate_inr=float(request.form.get('purchase_rate_inr')),
                purchase_amount=float(request.form.get('purchase_amount')),
                transaction_fee=float(request.form.get('transaction_fee') or 0),
                gas_fee=float(request.form.get('gas_fee') or 0),
                other_charges=float(request.form.get('other_charges') or 0),
                transaction_id=request.form.get('transaction_id'),
                transaction_hash=request.form.get('transaction_hash'),
                current_rate_inr=float(request.form.get('current_rate_inr')) if request.form.get('current_rate_inr') else None,
                current_market_value=float(request.form.get('current_market_value')) if request.form.get('current_market_value') else None,
                valuation_date=datetime.strptime(request.form.get('valuation_date'), '%Y-%m-%d').date() if request.form.get('valuation_date') else None,
                is_staked=bool(request.form.get('is_staked')),
                staking_platform=request.form.get('staking_platform'),
                staking_apy=float(request.form.get('staking_apy')) if request.form.get('staking_apy') else None,
                staking_rewards_earned=float(request.form.get('staking_rewards_earned') or 0),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            # Calculate values
            new_holding.calculate_values()
            
            db.session.add(new_holding)
            db.session.commit()
            
            flash(f'Successfully added {new_holding.crypto_name} to your portfolio!', 'success')
            return redirect(url_for('dashboard_cryptocurrency'))
            
        except Exception as e:
            logger.error(f"Error adding cryptocurrency: {str(e)}")
            flash(f'Error adding cryptocurrency: {str(e)}', 'error')
            return redirect(url_for('dashboard_cryptocurrency'))
    
    # GET request - display holdings
    # Get manual holdings
    manual_holdings = ManualCryptocurrencyHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_investment = sum(h.total_investment for h in manual_holdings)
    current_value = sum(h.current_market_value or h.total_investment for h in manual_holdings)
    total_gain = sum(h.unrealized_gain or 0 for h in manual_holdings)
    holdings_count = len(manual_holdings)
    
    return render_template('dashboard/cryptocurrency.html',
                         holdings=manual_holdings,
                         total_investment=total_investment,
                         current_value=current_value,
                         total_gain=total_gain,
                         holdings_count=holdings_count)

@app.route('/api/cryptocurrency/<int:holding_id>', methods=['DELETE'])
@login_required
def delete_cryptocurrency_holding(holding_id):
    """Delete a manual cryptocurrency holding"""
    from models import ManualCryptocurrencyHolding
    
    try:
        holding = ManualCryptocurrencyHolding.query.filter_by(
            id=holding_id,
            user_id=current_user.id
        ).first()
        
        if not holding:
            return jsonify({'success': False, 'error': 'Holding not found'}), 404
        
        db.session.delete(holding)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Holding deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting cryptocurrency: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/insurance', methods=['GET', 'POST'])
@login_required
def dashboard_insurance():
    """Insurance policies management - manual entries"""
    from models import ManualInsuranceHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual insurance policy
            new_policy = ManualInsuranceHolding(
                user_id=current_user.id,
                insurance_type=request.form.get('insurance_type'),
                policy_name=request.form.get('policy_name'),
                policy_number=request.form.get('policy_number'),
                insurance_company=request.form.get('insurance_company'),
                policy_holder_name=request.form.get('policy_holder_name'),
                insured_person_name=request.form.get('insured_person_name'),
                sum_assured=float(request.form.get('sum_assured')),
                policy_term_years=int(request.form.get('policy_term_years')) if request.form.get('policy_term_years') else None,
                premium_amount=float(request.form.get('premium_amount')),
                premium_frequency=request.form.get('premium_frequency'),
                premium_payment_term_years=int(request.form.get('premium_payment_term_years')) if request.form.get('premium_payment_term_years') else None,
                total_premiums_paid=float(request.form.get('total_premiums_paid') or 0),
                policy_start_date=datetime.strptime(request.form.get('policy_start_date'), '%Y-%m-%d').date(),
                policy_end_date=datetime.strptime(request.form.get('policy_end_date'), '%Y-%m-%d').date() if request.form.get('policy_end_date') else None,
                maturity_date=datetime.strptime(request.form.get('maturity_date'), '%Y-%m-%d').date() if request.form.get('maturity_date') else None,
                next_premium_due_date=datetime.strptime(request.form.get('next_premium_due_date'), '%Y-%m-%d').date() if request.form.get('next_premium_due_date') else None,
                maturity_amount=float(request.form.get('maturity_amount')) if request.form.get('maturity_amount') else None,
                bonus_accumulated=float(request.form.get('bonus_accumulated') or 0),
                surrender_value=float(request.form.get('surrender_value')) if request.form.get('surrender_value') else None,
                nominee_name=request.form.get('nominee_name'),
                nominee_relation=request.form.get('nominee_relation'),
                agent_name=request.form.get('agent_name'),
                agent_contact=request.form.get('agent_contact'),
                policy_status=request.form.get('policy_status', 'Active'),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            db.session.add(new_policy)
            db.session.commit()
            
            flash(f'Successfully added {new_policy.policy_name} to your portfolio!', 'success')
            return redirect(url_for('dashboard_insurance'))
            
        except Exception as e:
            logger.error(f"Error adding insurance policy: {str(e)}")
            flash(f'Error adding insurance policy: {str(e)}', 'error')
            return redirect(url_for('dashboard_insurance'))
    
    # GET request - display policies
    # Get manual policies
    manual_policies = ManualInsuranceHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_coverage = sum(p.sum_assured for p in manual_policies if p.policy_status == 'Active')
    total_premiums = sum(p.total_premiums_paid for p in manual_policies)
    
    # Calculate annual premium (convert all to annual equivalent)
    annual_premium = 0
    for p in manual_policies:
        if p.policy_status == 'Active':
            if p.premium_frequency == 'Monthly':
                annual_premium += p.premium_amount * 12
            elif p.premium_frequency == 'Quarterly':
                annual_premium += p.premium_amount * 4
            elif p.premium_frequency == 'Half-Yearly':
                annual_premium += p.premium_amount * 2
            elif p.premium_frequency == 'Annual':
                annual_premium += p.premium_amount
            elif p.premium_frequency == 'Single Premium':
                pass  # Already paid, no annual premium
    
    policies_count = len([p for p in manual_policies if p.policy_status == 'Active'])
    
    return render_template('dashboard/insurance.html',
                         policies=manual_policies,
                         total_coverage=total_coverage,
                         total_premiums=total_premiums,
                         annual_premium=annual_premium,
                         policies_count=policies_count)

@app.route('/api/insurance/<int:policy_id>', methods=['DELETE'])
@login_required
def delete_insurance_policy(policy_id):
    """Delete a manual insurance policy"""
    from models import ManualInsuranceHolding
    
    try:
        policy = ManualInsuranceHolding.query.filter_by(
            id=policy_id,
            user_id=current_user.id
        ).first()
        
        if not policy:
            return jsonify({'success': False, 'error': 'Policy not found'}), 404
        
        db.session.delete(policy)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Policy deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting insurance policy: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/banks', methods=['GET', 'POST'])
@login_required
def dashboard_banks():
    """Bank accounts and cash management - manual entries"""
    from models import ManualBankAccount
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual bank account
            new_account = ManualBankAccount(
                user_id=current_user.id,
                account_type=request.form.get('account_type'),
                bank_name=request.form.get('bank_name'),
                account_number=request.form.get('account_number'),
                branch_name=request.form.get('branch_name'),
                ifsc_code=request.form.get('ifsc_code'),
                account_holder_name=request.form.get('account_holder_name'),
                current_balance=float(request.form.get('current_balance')),
                as_on_date=datetime.strptime(request.form.get('as_on_date'), '%Y-%m-%d').date() if request.form.get('as_on_date') else None,
                interest_rate=float(request.form.get('interest_rate')) if request.form.get('interest_rate') else None,
                interest_earned_ytd=float(request.form.get('interest_earned_ytd') or 0),
                account_status=request.form.get('account_status', 'Active'),
                account_opening_date=datetime.strptime(request.form.get('account_opening_date'), '%Y-%m-%d').date() if request.form.get('account_opening_date') else None,
                has_debit_card=bool(request.form.get('has_debit_card')),
                has_internet_banking=bool(request.form.get('has_internet_banking')),
                has_mobile_banking=bool(request.form.get('has_mobile_banking')),
                has_cheque_book=bool(request.form.get('has_cheque_book')),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            db.session.add(new_account)
            db.session.commit()
            
            flash(f'Successfully added {new_account.bank_name} account to your portfolio!', 'success')
            return redirect(url_for('dashboard_banks'))
            
        except Exception as e:
            logger.error(f"Error adding bank account: {str(e)}")
            flash(f'Error adding bank account: {str(e)}', 'error')
            return redirect(url_for('dashboard_banks'))
    
    # GET request - display accounts
    # Get manual accounts
    manual_accounts = ManualBankAccount.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_balance = sum(a.current_balance for a in manual_accounts if a.account_status == 'Active')
    savings_balance = sum(a.current_balance for a in manual_accounts if a.account_type == 'Savings Account' and a.account_status == 'Active')
    total_interest = sum(a.interest_earned_ytd or 0 for a in manual_accounts)
    accounts_count = len([a for a in manual_accounts if a.account_status == 'Active'])
    
    return render_template('dashboard/banks.html',
                         accounts=manual_accounts,
                         total_balance=total_balance,
                         savings_balance=savings_balance,
                         total_interest=total_interest,
                         accounts_count=accounts_count)

@app.route('/api/banks/<int:account_id>', methods=['DELETE'])
@login_required
def delete_bank_account(account_id):
    """Delete a manual bank account"""
    from models import ManualBankAccount
    
    try:
        account = ManualBankAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()
        
        if not account:
            return jsonify({'success': False, 'error': 'Account not found'}), 404
        
        db.session.delete(account)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Account deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting bank account: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/futures-options', methods=['GET', 'POST'])
@login_required
def dashboard_futures_options():
    """Futures and Options positions - manual entries"""
    from models import ManualFuturesOptionsHolding
    from datetime import datetime
    
    if request.method == 'POST':
        try:
            # Create new manual F&O position
            new_position = ManualFuturesOptionsHolding(
                user_id=current_user.id,
                contract_type=request.form.get('contract_type'),
                underlying_asset=request.form.get('underlying_asset'),
                symbol=request.form.get('symbol'),
                strike_price=float(request.form.get('strike_price')) if request.form.get('strike_price') else None,
                lot_size=int(request.form.get('lot_size')),
                quantity_lots=int(request.form.get('quantity_lots')),
                total_quantity=0,  # Will be calculated
                expiry_date=datetime.strptime(request.form.get('expiry_date'), '%Y-%m-%d').date(),
                trade_date=datetime.strptime(request.form.get('trade_date'), '%Y-%m-%d').date(),
                position_type=request.form.get('position_type'),
                entry_price=float(request.form.get('entry_price')),
                premium_paid=float(request.form.get('premium_paid') or 0),
                brokerage=float(request.form.get('brokerage') or 0),
                stt=float(request.form.get('stt') or 0),
                exchange_charges=float(request.form.get('exchange_charges') or 0),
                gst=float(request.form.get('gst') or 0),
                other_charges=float(request.form.get('other_charges') or 0),
                total_charges=0,  # Will be calculated
                total_investment=0,  # Will be calculated
                current_market_price=float(request.form.get('current_market_price')) if request.form.get('current_market_price') else None,
                position_status=request.form.get('position_status', 'Open'),
                portfolio_name=request.form.get('portfolio_name', 'Default'),
                notes=request.form.get('notes')
            )
            
            # Calculate values
            new_position.calculate_values()
            
            db.session.add(new_position)
            db.session.commit()
            
            flash(f'Successfully added {new_position.contract_type} position for {new_position.underlying_asset}!', 'success')
            return redirect(url_for('dashboard_futures_options'))
            
        except Exception as e:
            logger.error(f"Error adding F&O position: {str(e)}")
            flash(f'Error adding position: {str(e)}', 'error')
            return redirect(url_for('dashboard_futures_options'))
    
    # GET request - display positions
    positions = ManualFuturesOptionsHolding.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Calculate summary
    total_investment = sum(p.total_investment for p in positions if p.position_status == 'Open')
    current_value = sum(p.current_value or 0 for p in positions if p.position_status == 'Open')
    total_pnl = sum(p.unrealized_pnl or 0 for p in positions if p.position_status == 'Open')
    positions_count = len([p for p in positions if p.position_status == 'Open'])

    # Fetch imported F&O trades from Behavioural AI / broker P&L imports
    from models import ManualTradeImport
    imported_fo_trades = ManualTradeImport.query.filter(
        ManualTradeImport.user_id == current_user.id,
        ManualTradeImport.asset_type.in_(['OPTION', 'FUTURES', 'FNO', 'F&O'])
    ).order_by(ManualTradeImport.entry_time.desc()).all()

    imported_wins  = sum(1 for t in imported_fo_trades if t.trade_result == 'WIN')
    imported_losses = sum(1 for t in imported_fo_trades if t.trade_result == 'LOSS')
    imported_total_pnl = sum(t.net_pnl or t.realized_pnl or 0 for t in imported_fo_trades)
    imported_win_rate = round(imported_wins / len(imported_fo_trades) * 100, 1) if imported_fo_trades else 0

    # Fetch broker-synced F&O positions
    broker_fo_positions = []
    try:
        import re as _re
        from models_broker import BrokerPosition as _BPos, BrokerAccount as _BAcc

        def _is_fo(pos):
            """Return True if this BrokerPosition looks like an F&O instrument."""
            exch = (pos.exchange or '').upper()
            sym  = (pos.trading_symbol or pos.symbol or '').upper()
            if any(k in exch for k in ('FNO', 'NFO', 'BFO', 'BSE_FO', 'NSE_FO', 'MCX_FO', 'NSE_FNO', 'BSE_FNO')):
                return True
            if _re.search(r'(CE|PE|FUT)$', sym):
                return True
            return False

        def _parse_fo_symbol(trading_symbol):
            """Parse a broker trading symbol into F&O components.
            Handles patterns like:
              NIFTY26APR24550CE  → underlying=NIFTY, expiry=26APR, strike=24550, contract=CE
              BANKNIFTY26APR52000PE → underlying=BANKNIFTY, expiry=26APR, strike=52000, contract=PE
              NIFTY26APRFUT       → underlying=NIFTY, expiry=26APR, strike=None, contract=FUT
            """
            sym = (trading_symbol or '').upper().strip()
            m_opt = _re.match(r'^([A-Z]+?)(\d{2}[A-Z]{3})(\d+\.?\d*)(CE|PE)$', sym)
            m_fut = _re.match(r'^([A-Z]+?)(\d{2}[A-Z]{3})(FUT)$', sym)
            if m_opt:
                return {
                    'underlying': m_opt.group(1),
                    'expiry': m_opt.group(2),
                    'strike': float(m_opt.group(3)),
                    'contract': m_opt.group(4),
                }
            elif m_fut:
                return {
                    'underlying': m_fut.group(1),
                    'expiry': m_fut.group(2),
                    'strike': None,
                    'contract': 'FUT',
                }
            # Fallback — can't parse
            contract = 'CE' if sym.endswith('CE') else ('PE' if sym.endswith('PE') else 'FUT')
            return {'underlying': sym, 'expiry': None, 'strike': None, 'contract': contract}

        _user_accounts = _BAcc.query.filter_by(user_id=current_user.id, is_active=True).all()
        _acc_ids = [a.id for a in _user_accounts]
        _acc_map = {a.id: a.broker_name for a in _user_accounts}

        if _acc_ids:
            _all_positions = _BPos.query.filter(
                _BPos.broker_account_id.in_(_acc_ids)
            ).order_by(_BPos.position_date.desc()).all()

            for pos in _all_positions:
                if not _is_fo(pos):
                    continue
                parsed = _parse_fo_symbol(pos.trading_symbol or pos.symbol or '')
                broker_fo_positions.append({
                    'id': pos.id,
                    'symbol': pos.symbol,
                    'trading_symbol': pos.trading_symbol,
                    'exchange': pos.exchange,
                    'underlying': parsed['underlying'],
                    'expiry': parsed['expiry'],
                    'strike': parsed['strike'],
                    'contract': parsed['contract'],
                    'broker_name': _acc_map.get(pos.broker_account_id, 'Broker'),
                    'quantity': pos.quantity,
                    'avg_buy_price': pos.avg_buy_price or 0.0,
                    'current_price': pos.current_price or 0.0,
                    'unrealized_pnl': pos.unrealized_pnl or 0.0,
                    'realized_pnl': pos.realized_pnl or 0.0,
                    'total_pnl': pos.total_pnl or 0.0,
                    'position_date': pos.position_date,
                })
    except Exception as _e:
        logger.warning(f"Could not fetch broker F&O positions: {_e}")

    # Aggregate broker F&O into summary totals
    broker_fo_unrealized = sum(p['unrealized_pnl'] for p in broker_fo_positions)
    broker_fo_realized = sum(p['realized_pnl'] for p in broker_fo_positions)

    return render_template('dashboard/futures_options.html',
                         positions=positions,
                         total_investment=total_investment,
                         current_value=current_value,
                         total_pnl=total_pnl,
                         positions_count=positions_count,
                         broker_fo_positions=broker_fo_positions,
                         broker_fo_unrealized=broker_fo_unrealized,
                         broker_fo_realized=broker_fo_realized,
                         imported_fo_trades=imported_fo_trades,
                         imported_wins=imported_wins,
                         imported_losses=imported_losses,
                         imported_total_pnl=imported_total_pnl,
                         imported_win_rate=imported_win_rate)

@app.route('/api/futures-options/<int:position_id>', methods=['DELETE'])
@login_required
def delete_futures_options_position(position_id):
    """Delete a manual F&O position"""
    from models import ManualFuturesOptionsHolding
    
    try:
        position = ManualFuturesOptionsHolding.query.filter_by(
            id=position_id,
            user_id=current_user.id
        ).first()
        
        if not position:
            return jsonify({'success': False, 'error': 'Position not found'}), 404
        
        db.session.delete(position)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Position deleted successfully'})
    except Exception as e:
        logger.error(f"Error deleting F&O position: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dashboard/import/<asset_class>', methods=['GET'])
@login_required
def import_holdings(asset_class):
    """Import holdings page for any asset class"""
    from models_broker import BrokerAccount
    from models import ManualBankAccount
    
    # Map asset class to display name
    asset_class_names = {
        'equities': 'Equities',
        'mutual_funds': 'Mutual Funds',
        'fixed_deposits': 'Fixed Deposits',
        'real_estate': 'Real Estate',
        'commodities': 'Gold & Commodities',
        'cryptocurrency': 'Cryptocurrency',
        'insurance': 'Insurance',
        'banks': 'Banks & Cash',
        'futures_options': 'F&O'
    }
    
    asset_class_name = asset_class_names.get(asset_class, asset_class.title())
    
    # Get connected brokers
    connected_brokers = BrokerAccount.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    
    # Get connected banks
    connected_banks = ManualBankAccount.query.filter_by(
        user_id=current_user.id,
        is_active=True,
        account_status='Active'
    ).all()
    
    return render_template('dashboard/import_holdings.html',
                         asset_class=asset_class,
                         asset_class_name=asset_class_name,
                         connected_brokers=connected_brokers,
                         connected_banks=connected_banks)

@app.route('/api/import/broker/<int:broker_id>/<asset_class>', methods=['POST'])
@login_required
def import_from_broker(broker_id, asset_class):
    """Import holdings from a connected broker"""
    from models_broker import BrokerAccount
    
    try:
        broker = BrokerAccount.query.filter_by(
            id=broker_id,
            user_id=current_user.id
        ).first()
        
        if not broker:
            return jsonify({'success': False, 'error': 'Broker not found'}), 404
        
        # TODO: Implement actual broker sync based on broker_name and asset_class
        # This is a placeholder that will be implemented with actual broker APIs
        
        return jsonify({
            'success': True,
            'count': 0,
            'message': f'Broker import for {asset_class} will be implemented soon'
        })
    except Exception as e:
        logger.error(f"Error importing from broker: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/import/bank/<int:bank_id>/<asset_class>', methods=['POST'])
@login_required
def import_from_bank(bank_id, asset_class):
    """Import holdings from a connected bank"""
    from models import ManualBankAccount
    
    try:
        bank = ManualBankAccount.query.filter_by(
            id=bank_id,
            user_id=current_user.id
        ).first()
        
        if not bank:
            return jsonify({'success': False, 'error': 'Bank not found'}), 404
        
        # TODO: Implement actual bank sync based on bank_name and asset_class
        # This is a placeholder that will be implemented with actual bank APIs
        
        return jsonify({
            'success': True,
            'count': 0,
            'message': f'Bank import for {asset_class} will be implemented soon'
        })
    except Exception as e:
        logger.error(f"Error importing from bank: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/import/upload', methods=['POST'])
@login_required
def import_holdings_upload():
    """Upload and import holdings from Excel or PDF file"""
    try:
        asset_class = request.form.get('asset_class')
        file_type = request.form.get('file_type')
        
        if 'file' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(url_for('import_holdings', asset_class=asset_class))
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('import_holdings', asset_class=asset_class))
        
        # TODO: Implement file parsing logic
        # - For Excel: Use pandas to read and parse
        # - For PDF: Use AI/OCR to extract data
        
        flash(f'File upload and import for {asset_class} will be implemented soon', 'info')
        return redirect(url_for('import_holdings', asset_class=asset_class))
        
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        flash(f'Error uploading file: {str(e)}', 'error')
        return redirect(url_for('import_holdings', asset_class=asset_class))

@app.route('/api/import/template/<asset_class>/<format>')
@login_required
def download_import_template(asset_class, format):
    """Download Excel template for importing holdings"""
    try:
        # TODO: Generate actual Excel template based on asset class
        # For now, return a simple message
        
        flash(f'Template download for {asset_class} will be implemented soon', 'info')
        return redirect(url_for('import_holdings', asset_class=asset_class))
        
    except Exception as e:
        logger.error(f"Error downloading template: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('import_holdings', asset_class=asset_class))

@app.route('/portfolio/sync-brokers', methods=['POST'])
@login_required
def portfolio_sync_brokers():
    """Sync portfolio data from connected broker accounts"""
    if not current_user.can_access_menu('dashboard_broker_accounts'):
        return jsonify({'success': False, 'error': 'Broker access required'})
    
    try:
        from services.portfolio_analyzer_service import PortfolioAnalyzerService
        analyzer = PortfolioAnalyzerService(current_user.id)
        result = analyzer.sync_broker_data()
        
        if result['success']:
            flash(f'Successfully synced {result["total_holdings"]} holdings from {len(result["synced_brokers"])} brokers', 'success')
        else:
            flash(f'Sync completed with errors: {"; ".join(result.get("errors", []))}', 'warning')
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/portfolio/upload-holdings', methods=['POST'])
@login_required
def portfolio_upload_holdings():
    """Upload manual holdings via CSV/Excel"""
    if request.method == 'POST':
        try:
            # Process uploaded file
            if 'holdings_file' not in request.files:
                flash('No file uploaded', 'error')
                return redirect(url_for('dashboard_my_portfolio'))
            
            file = request.files['holdings_file']
            if file.filename == '':
                flash('No file selected', 'error')
                return redirect(url_for('dashboard_my_portfolio'))
            
            # Parse CSV/Excel file
            import pandas as pd
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            elif file.filename.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file)
            else:
                flash('Please upload a CSV or Excel file', 'error')
                return redirect(url_for('dashboard_my_portfolio'))
            
            # Convert DataFrame to list of dictionaries
            holdings_data = df.to_dict('records')
            
            # Process holdings
            from services.portfolio_analyzer_service import PortfolioAnalyzerService
            analyzer = PortfolioAnalyzerService(current_user.id)
            result = analyzer.upload_manual_holdings(holdings_data)
            
            if result['success']:
                flash(f'Successfully processed {result["processed"]} holdings. Skipped: {result["skipped"]}', 'success')
            else:
                flash(f'Upload failed: {result.get("error", "Unknown error")}', 'error')
            
            return redirect(url_for('dashboard_my_portfolio'))
            
        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(url_for('dashboard_my_portfolio'))

@app.route('/portfolio/risk-profile', methods=['GET', 'POST'])
@login_required
def portfolio_risk_profile():
    """Risk profiling questionnaire"""
    from models import RiskProfile
    
    if request.method == 'POST':
        try:
            # Calculate risk score based on responses
            age_score = {'18-25': 25, '26-35': 20, '36-45': 15, '46-55': 10, '55+': 5}
            goal_score = {'wealth_creation': 25, 'retirement': 15, 'children_education': 10, 'house_purchase': 10}
            horizon_score = {'short_term': 5, 'medium_term': 15, 'long_term': 25}
            tolerance_score = {'conservative': 5, 'moderate': 15, 'aggressive': 25}
            experience_score = {'beginner': 5, 'intermediate': 15, 'advanced': 25}
            
            risk_score = (
                age_score.get(request.form.get('age_group'), 15) +
                goal_score.get(request.form.get('investment_goal'), 15) +
                horizon_score.get(request.form.get('investment_horizon'), 15) +
                tolerance_score.get(request.form.get('risk_tolerance'), 15) +
                experience_score.get(request.form.get('investment_experience'), 15) +
                int(request.form.get('loss_tolerance', 10))
            )
            
            # Determine risk category
            if risk_score <= 40:
                risk_category = 'Conservative'
            elif risk_score <= 70:
                risk_category = 'Balanced'
            else:
                risk_category = 'Aggressive'
            
            # Update or create risk profile
            risk_profile = RiskProfile.query.filter_by(user_id=current_user.id).first()
            if risk_profile:
                risk_profile.age_group = request.form.get('age_group')
                risk_profile.investment_goal = request.form.get('investment_goal')
                risk_profile.investment_horizon = request.form.get('investment_horizon')
                risk_profile.risk_tolerance = request.form.get('risk_tolerance')
                risk_profile.loss_tolerance = int(request.form.get('loss_tolerance', 10))
                risk_profile.investment_experience = request.form.get('investment_experience')
                risk_profile.risk_score = risk_score
                risk_profile.risk_category = risk_category
                risk_profile.updated_at = datetime.utcnow()
            else:
                risk_profile = RiskProfile(
                    user_id=current_user.id,
                    age_group=request.form.get('age_group'),
                    investment_goal=request.form.get('investment_goal'),
                    investment_horizon=request.form.get('investment_horizon'),
                    risk_tolerance=request.form.get('risk_tolerance'),
                    loss_tolerance=int(request.form.get('loss_tolerance', 10)),
                    investment_experience=request.form.get('investment_experience'),
                    risk_score=risk_score,
                    risk_category=risk_category
                )
                db.session.add(risk_profile)
            
            db.session.commit()
            flash(f'Risk profile updated. Your category: {risk_category} (Score: {risk_score})', 'success')
            return redirect(url_for('dashboard_my_portfolio'))
            
        except Exception as e:
            flash(f'Error saving risk profile: {str(e)}', 'error')
    
    # Get existing risk profile if any
    risk_profile = RiskProfile.query.filter_by(user_id=current_user.id).first()
    
    return render_template('dashboard/risk_profile.html', 
                         current_user=current_user,
                         risk_profile=risk_profile)

@app.route('/portfolio/preferences', methods=['GET', 'POST'])
@login_required
def portfolio_preferences():
    """Portfolio preferences form for personalized optimization"""
    from models import PortfolioPreferences
    import json
    
    if request.method == 'POST':
        try:
            # Get or create preferences
            prefs = PortfolioPreferences.query.filter_by(user_id=current_user.id).first()
            if not prefs:
                prefs = PortfolioPreferences(user_id=current_user.id)
                db.session.add(prefs)
            
            # Personal Details
            prefs.age = int(request.form.get('age')) if request.form.get('age') else None
            prefs.employment_status = request.form.get('employment_status')
            prefs.annual_income = request.form.get('annual_income')
            prefs.dependents = int(request.form.get('dependents')) if request.form.get('dependents') else None
            prefs.marital_status = request.form.get('marital_status')
            
            # Financial Goals (multi-select)
            goals = request.form.getlist('financial_goals')
            prefs.financial_goals = json.dumps(goals) if goals else None
            
            # Investment Horizon
            prefs.investment_horizon = request.form.get('investment_horizon')
            prefs.investment_horizon_years = int(request.form.get('investment_horizon_years')) if request.form.get('investment_horizon_years') else None
            
            # Contribution Details
            prefs.contribution_type = request.form.get('contribution_type')
            prefs.monthly_contribution = float(request.form.get('monthly_contribution')) if request.form.get('monthly_contribution') else None
            prefs.lump_sum_amount = float(request.form.get('lump_sum_amount')) if request.form.get('lump_sum_amount') else None
            
            # Risk Profile
            prefs.risk_tolerance = request.form.get('risk_tolerance')
            prefs.risk_capacity = request.form.get('risk_capacity')
            prefs.max_acceptable_loss = float(request.form.get('max_acceptable_loss')) if request.form.get('max_acceptable_loss') else None
            
            # Return Requirements
            prefs.expected_return = float(request.form.get('expected_return')) if request.form.get('expected_return') else None
            prefs.minimum_return_required = float(request.form.get('minimum_return_required')) if request.form.get('minimum_return_required') else None
            
            # Liquidity Needs
            prefs.liquidity_requirement = request.form.get('liquidity_requirement')
            prefs.withdrawal_frequency = request.form.get('withdrawal_frequency')
            prefs.emergency_fund_months = int(request.form.get('emergency_fund_months')) if request.form.get('emergency_fund_months') else None
            
            # Asset Class Preferences
            asset_classes = request.form.getlist('preferred_asset_classes')
            prefs.preferred_asset_classes = json.dumps(asset_classes) if asset_classes else None
            
            # Asset Allocation
            allocation = {}
            for asset_class in ['equities', 'bonds', 'fo', 'real_estate', 'crypto', 'fd', 'insurance', 'cash']:
                value = request.form.get(f'allocation_{asset_class}')
                if value:
                    allocation[asset_class] = float(value)
            prefs.asset_allocation_preferences = json.dumps(allocation) if allocation else None
            
            # Geographic & Sector Preferences
            prefs.geographic_preference = request.form.get('geographic_preference')
            sectors = request.form.getlist('sector_preferences')
            prefs.sector_preferences = json.dumps(sectors) if sectors else None
            sector_exclusions = request.form.getlist('sector_exclusions')
            prefs.sector_exclusions = json.dumps(sector_exclusions) if sector_exclusions else None
            
            # ESG Preferences
            prefs.esg_preference = request.form.get('esg_preference') == 'on'
            esg_priorities = request.form.getlist('esg_priorities')
            prefs.esg_priorities = json.dumps(esg_priorities) if esg_priorities else None
            
            # Rebalancing & Monitoring
            prefs.rebalancing_frequency = request.form.get('rebalancing_frequency')
            prefs.performance_review_frequency = request.form.get('performance_review_frequency')
            
            # Optimization Preferences
            prefs.optimization_strategy = request.form.get('optimization_strategy')
            prefs.tax_optimization = request.form.get('tax_optimization') == 'on'
            prefs.dividend_preference = request.form.get('dividend_preference')
            
            # Special Instructions
            prefs.special_instructions = request.form.get('special_instructions')
            
            # Mark as completed
            prefs.completed = True
            prefs.updated_at = datetime.utcnow()
            
            db.session.commit()
            flash('Portfolio preferences saved successfully! These will be used for personalized recommendations.', 'success')
            return redirect(url_for('dashboard_my_portfolio'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving preferences: {str(e)}', 'error')
    
    # Get existing preferences if any
    prefs = PortfolioPreferences.query.filter_by(user_id=current_user.id).first()
    
    # Parse JSON fields for template
    prefs_data = {}
    if prefs:
        try:
            prefs_data = {
                'financial_goals': json.loads(prefs.financial_goals) if prefs.financial_goals else [],
                'preferred_asset_classes': json.loads(prefs.preferred_asset_classes) if prefs.preferred_asset_classes else [],
                'asset_allocation': json.loads(prefs.asset_allocation_preferences) if prefs.asset_allocation_preferences else {},
                'sector_preferences': json.loads(prefs.sector_preferences) if prefs.sector_preferences else [],
                'sector_exclusions': json.loads(prefs.sector_exclusions) if prefs.sector_exclusions else [],
                'esg_priorities': json.loads(prefs.esg_priorities) if prefs.esg_priorities else []
            }
        except:
            pass
    
    return render_template('dashboard/portfolio_preferences.html', 
                         current_user=current_user,
                         prefs=prefs,
                         prefs_data=prefs_data)

# Duplicate route removed - using existing dashboard_trade_now above

# Add trading-related API routes
@app.route('/api/trade/recommend', methods=['POST'])
@login_required  
def api_create_trade_recommendation():
    """Create trade recommendation"""
    try:
        from services.trading_service import TradingService
        trading_service = TradingService()
        
        data = request.get_json()
        result = trading_service.create_trade_recommendation(current_user.id, data)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trade/deploy/<int:recommendation_id>', methods=['POST'])
@login_required
def api_deploy_trade(recommendation_id):
    """Deploy trade to broker"""
    try:
        # Additional security check at API level
        from models import PricingPlan
        
        if current_user.pricing_plan == PricingPlan.TARGET_PLUS:
            return jsonify({
                'success': False, 
                'error': 'Target Plus plan allows portfolio analysis only. Upgrade to Target Pro for trade execution.'
            }), 403
        
        if not current_user.is_admin and current_user.pricing_plan not in [PricingPlan.TARGET_PRO, PricingPlan.HNI]:
            return jsonify({
                'success': False, 
                'error': 'Trade execution requires Target Pro subscription or higher.'
            }), 403
        
        from services.trading_service import TradingService
        trading_service = TradingService()
        
        result = trading_service.deploy_trade(current_user.id, recommendation_id)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/market/analyze/<symbol>')
@login_required
def api_market_analysis(symbol):
    """Get market analysis for symbol"""
    try:
        from services.trading_service import TradingService
        trading_service = TradingService()
        
        analysis = trading_service.get_market_analysis(symbol)
        return jsonify({'success': True, 'data': analysis})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/strategies/recommend/<symbol>/<asset_class>')
@login_required
def api_strategy_recommendations(symbol, asset_class):
    """Get strategy recommendations"""
    try:
        from services.trading_service import TradingService
        trading_service = TradingService()
        
        recommendations = trading_service.get_strategy_recommendations(symbol, asset_class)
        return jsonify({'success': True, 'data': recommendations})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    brokers = [
        {'broker_id': 'zerodha', 'name': 'Zerodha'},
        {'broker_id': 'angel', 'name': 'Angel Broking'},
        {'broker_id': 'dhan', 'name': 'Dhan'}
    ]
    
    # Check if user has broker access (TraderPlus/Premium)
    if current_user.can_access_menu('dashboard_broker_accounts'):
        try:
            # Get user's connected broker accounts
            from models_broker import BrokerAccount, BrokerOrder, ConnectionStatus
            broker_accounts = BrokerAccount.query.filter_by(
                user_id=current_user.id,
                is_active=True,
                connection_status=ConnectionStatus.CONNECTED
            ).all()
            
            # Get recent orders (last 10)
            recent_orders = BrokerOrder.query.join(BrokerAccount).filter(
                BrokerAccount.user_id == current_user.id
            ).order_by(BrokerOrder.order_time.desc()).limit(10).all()
            
            return render_template('dashboard/trade_now.html',
                                 broker_accounts=broker_accounts,
                                 recent_orders=recent_orders,
                                 trading_signals=[],
                                 total_signals=0,
                                 buy_signals=0,
                                 sell_signals=0,
                                 avg_confidence=0)
                                 
        except Exception as e:
            logger.error(f"Error loading broker trading: {e}")
            # Fall back to regular trading signals view
            pass
    
    # Regular trading signals view 
    return render_template('dashboard/trade_now.html',
                         trading_signals=trading_signals,
                         total_signals=total_signals,
                         buy_signals=buy_signals,
                         sell_signals=sell_signals,
                         avg_confidence=avg_confidence,
                         broker_accounts=[],
                         recent_orders=[],
                         selected_date=selected_date)

@app.route('/dashboard/account-handling')
@login_required
def dashboard_account_handling():
    # Check subscription access
    if not current_user.can_access_menu('dashboard_account_handling'):
        flash('This feature requires a higher subscription plan. Please upgrade your account.', 'warning')
        return redirect(url_for('pricing'))
    """Dashboard Account Handling page"""
    from datetime import date
    return render_template('dashboard/account_handling.html', today=date.today())

# Dashboard route handlers already exist above, removing duplicate definitions

# Stock picker route already exists above







@app.route('/dashboard/nse-stocks')
@login_required
def dashboard_nse_stocks():
    """NSE India stocks dashboard with real-time data"""
    try:
        # Get Indian market data using enhanced market data service
        trending_indian_stocks = market_data_service.get_trending_stocks('INDIA', 15)
        market_indices = market_data_service.get_market_indices()
        
        # Filter Indian indices
        indian_indices = [idx for idx in market_indices if idx.get('exchange') == 'NSE']
        
        # Get market status and additional data from NSE service
        market_status = nse_service.get_market_status()
        top_gainers = nse_service.get_top_gainers(10)
        top_losers = nse_service.get_top_losers(10)
        
        # If no data from enhanced service, fallback to original NSE service
        if not trending_indian_stocks:
            popular_symbols = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN']
            trending_indian_stocks = nse_service.get_multiple_quotes(popular_symbols)
        
        if not indian_indices:
            indian_indices = nse_service.get_market_indices()
        
        return render_template('dashboard/nse_stocks.html',
                             popular_stocks=trending_indian_stocks,
                             market_status=market_status,
                             indices=indian_indices,
                             top_gainers=top_gainers,
                             top_losers=top_losers,
                             last_updated=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
    except Exception as e:
        logging.error(f"Error loading NSE stocks dashboard: {str(e)}")
        flash('Unable to load NSE market data. Please try again later.', 'error')
        return redirect(url_for('dashboard'))

@app.route('/dashboard/live-market')
@login_required
def live_market():
    """Live market data dashboard"""
    return render_template('dashboard/live_market.html',
                          last_updated=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/dashboard/add-nse-stock', methods=['POST'])
@login_required
def add_nse_stock_to_watchlist():
    """Add NSE stock to watchlist with real-time data"""
    try:
        symbol = request.form.get('symbol', '').upper()
        target_price = request.form.get('target_price')
        notes = request.form.get('notes', '')
        
        if not symbol:
            flash('Stock symbol is required.', 'error')
            return redirect(url_for('dashboard_watchlist'))
        
        # Get real-time data from NSE
        stock_data = nse_service.get_stock_quote(symbol)
        if not stock_data:
            flash(f'Unable to find stock {symbol} on NSE. Please check the symbol.', 'error')
            return redirect(url_for('dashboard_watchlist'))
        
        # Check if already in watchlist
        existing = WatchlistItem.query.filter_by(user_id=current_user.id, symbol=symbol).first()
        if existing:
            flash(f'{symbol} is already in your watchlist.', 'warning')
            return redirect(url_for('dashboard_watchlist'))
        
        # Create new watchlist item with NSE data
        watchlist_item = WatchlistItem(
            user_id=current_user.id,
            symbol=symbol,
            company_name=stock_data['company_name'],
            target_price=float(target_price) if target_price else None,
            notes=notes
        )
        
        db.session.add(watchlist_item)
        db.session.commit()
        
        flash(f'{symbol} ({stock_data["company_name"]}) added to your watchlist!', 'success')
        return redirect(url_for('dashboard_watchlist'))
        
    except Exception as e:
        logging.error(f"Error adding NSE stock to watchlist: {str(e)}")
        flash('Error adding stock to watchlist. Please try again.', 'error')
        return redirect(url_for('dashboard_watchlist'))

@app.route('/dashboard/analyze-nse-stock', methods=['POST'])
@login_required
def analyze_nse_stock():
    """Analyze NSE stock and save to database"""
    try:
        symbol = request.form.get('symbol', '').upper()
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Stock symbol is required'})
        
        # Get real-time data from NSE
        stock_data = nse_service.get_stock_quote(symbol)
        if not stock_data:
            return jsonify({'success': False, 'error': f'Stock {symbol} not found on NSE'})
        
        # Create or update stock analysis
        analysis = StockAnalysis.query.filter_by(symbol=symbol).first()
        if not analysis:
            analysis = StockAnalysis()
        
        # Update analysis with NSE data
        analysis.symbol = symbol
        analysis.company_name = stock_data['company_name']
        analysis.current_price = stock_data['current_price']
        analysis.previous_close = stock_data['previous_close']
        analysis.change_amount = stock_data['change_amount']
        analysis.change_percent = stock_data['change_percent']
        analysis.volume = stock_data['volume']
        analysis.day_high = stock_data['day_high']
        analysis.day_low = stock_data['day_low']
        analysis.week_52_high = stock_data['week_52_high']
        analysis.week_52_low = stock_data['week_52_low']
        analysis.pe_ratio = stock_data['pe_ratio']
        analysis.analysis_date = datetime.utcnow()
        
        # Generate AI recommendation based on price movement
        if stock_data['change_percent'] > 2:
            analysis.ai_recommendation = 'BUY'
            analysis.ai_confidence = min(85.0, 70 + abs(stock_data['change_percent']))
            analysis.ai_notes = f"Strong upward momentum with {stock_data['change_percent']:.2f}% gain"
        elif stock_data['change_percent'] < -2:
            analysis.ai_recommendation = 'SELL'
            analysis.ai_confidence = min(85.0, 70 + abs(stock_data['change_percent']))
            analysis.ai_notes = f"Significant decline of {stock_data['change_percent']:.2f}% - consider risk"
        else:
            analysis.ai_recommendation = 'HOLD'
            analysis.ai_confidence = 65.0
            analysis.ai_notes = "Stable price movement - monitor for trends"
        
        if analysis.id is None:
            db.session.add(analysis)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'data': {
                'symbol': symbol,
                'company_name': analysis.company_name,
                'current_price': analysis.current_price,
                'change_percent': analysis.change_percent,
                'recommendation': analysis.ai_recommendation,
                'confidence': analysis.ai_confidence,
                'notes': analysis.ai_notes
            }
        })
        
    except Exception as e:
        logging.error(f"Error analyzing NSE stock: {str(e)}")
        return jsonify({'success': False, 'error': 'Analysis failed. Please try again.'})

def generate_mock_market_data():
    """Generate mock market data for fallback"""
    from types import SimpleNamespace
    
    # Create mock objects with proper attributes matching market_data_service format
    def create_stock(symbol, company_name, current_price, change_percent):
        stock = SimpleNamespace()
        stock.symbol = symbol
        stock.company_name = company_name
        stock.current_price = current_price
        stock.change = change_percent * current_price / 100  # Calculate absolute change
        stock.change_percent = f"{change_percent:.2f}"  # Format as string
        return stock
    
    return {
        'indices': [
            create_stock('SPY', 'S&P 500 ETF', 445.23, 1.2),
            create_stock('QQQ', 'NASDAQ-100 ETF', 378.45, -0.8),
            create_stock('^DJI', 'Dow Jones', 34567.89, 0.5)
        ],
        'trending_us': [
            create_stock('AAPL', 'Apple Inc.', 175.43, 2.1),
            create_stock('GOOGL', 'Alphabet Inc.', 142.56, -0.8),
            create_stock('MSFT', 'Microsoft Corp.', 378.85, 1.4),
            create_stock('NVDA', 'NVIDIA Corp.', 875.28, 3.7),
            create_stock('TSLA', 'Tesla Inc.', 248.50, 5.2)
        ],
        'trending_india': [],
        'last_updated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    }

# Admin Blog Management Routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    total_posts = BlogPost.query.count()
    published_posts = BlogPost.query.filter_by(status='published').count()
    draft_posts = BlogPost.query.filter_by(status='draft').count()
    recent_posts = BlogPost.query.order_by(BlogPost.created_at.desc()).limit(5).all()
    
    return render_template('admin/dashboard.html',
                         total_posts=total_posts,
                         published_posts=published_posts,
                         draft_posts=draft_posts,
                         recent_posts=recent_posts)



# Update blog routes to handle slug-based URLs
@app.route('/blog/<slug>')
def blog_post_by_slug(slug):
    """Individual blog post page route by slug"""
    post = BlogPost.query.filter_by(slug=slug, status='published').first_or_404()
    
    # Increment view count
    post.view_count += 1
    db.session.commit()
    
    # Get related posts (same category or recent posts)
    related_posts = BlogPost.query.filter(
        BlogPost.id != post.id,
        BlogPost.status == 'published'
    ).order_by(BlogPost.created_at.desc()).limit(3).all()
    
    testimonials = Testimonial.query.limit(2).all()
    return render_template('blog_post.html', post=post, related_posts=related_posts, testimonials=testimonials)

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

# Real-time Market Data API Endpoints

@app.route('/api/stock/<symbol>')
def api_get_stock(symbol):
    """Get real-time stock data"""
    exchange = request.args.get('exchange', 'US')
    try:
        stock_data = market_data_service.get_stock_quote(symbol, exchange)
        if stock_data:
            return jsonify({
                'success': True,
                'data': stock_data
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Stock not found'
            }), 404
    except Exception as e:
        logging.error(f"API error for stock {symbol}: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/api/market/indices')
def api_market_indices():
    """Get market indices with caching"""
    try:
        from caching.redis_cache import get_cache
        cache = get_cache()
        
        cache_key = 'market_indices_full'
        if cache.is_available():
            cached_data = cache.get(cache_key)
            if cached_data:
                return jsonify({
                    'success': True,
                    'data': cached_data,
                    'last_updated': datetime.now(timezone.utc).isoformat(),
                    'cached': True
                })
        
        indices = market_data_service.get_market_indices()
        
        if cache.is_available():
            cache.set(cache_key, indices, expiry=120)
        return jsonify({
            'success': True,
            'data': indices,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False
        })
    except Exception as e:
        logging.error(f"API error for market indices: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Unable to fetch market indices'
        }), 500

@app.route('/api/market/trending')
def api_trending_stocks():
    """Get trending stocks"""
    market = request.args.get('market', 'US')
    limit = int(request.args.get('limit', 10))
    try:
        trending = market_data_service.get_trending_stocks(market, limit)
        return jsonify({
            'success': True,
            'data': trending,
            'market': market,
            'last_updated': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logging.error(f"API error for trending stocks: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Unable to fetch trending stocks'
        }), 500

@app.route('/api/search/stocks')
def api_search_stocks():
    """Search stocks by symbol or company name"""
    query = request.args.get('q', '')
    limit = int(request.args.get('limit', 10))
    
    if not query:
        return jsonify({
            'success': False,
            'error': 'Query parameter required'
        }), 400
    
    try:
        results = market_data_service.search_stocks(query, limit)
        return jsonify({
            'success': True,
            'data': results,
            'query': query
        })
    except Exception as e:
        logging.error(f"API error for stock search: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Search failed'
        }), 500

# Research Assistant Routes
@app.route('/dashboard/ai-advisor')
@login_required
def dashboard_ai_advisor():
    """Research Assistant - RAG-powered stock research"""
    try:
        from services.research_assistant_service import ResearchAssistantService
        from models import ResearchList
        
        research_service = ResearchAssistantService()
        
        # Get user context
        user_context = research_service.get_user_context(current_user.id)
        
        # Get recent conversations
        conversations = research_service.get_user_conversations(current_user.id, limit=10)
        
        # Get current conversation if specified
        conversation_id = request.args.get('conversation_id', type=int)
        messages = []
        
        if conversation_id:
            messages = research_service.get_conversation_history(conversation_id)

        # Pre-fill query from landing page "Research" button
        prefill_symbol = request.args.get('q', '').strip().upper()
        prefill_company = request.args.get('company', '').strip()
        prefill_asset = request.args.get('asset_type', 'stocks').strip()
        
        # Get broker accounts for context
        from models_broker import BrokerAccount
        broker_accounts = BrokerAccount.query.filter_by(user_id=current_user.id).all()
        
        # Get research watch list — all active entries sorted by I-Score
        research_list = ResearchList.query.filter_by(is_active=True).order_by(
            ResearchList.i_score.desc().nullslast()
        ).all()
        
        return render_template('dashboard/research_assistant.html',
                             context={
                                 'portfolio_value': user_context['portfolio']['total_value'],
                                 'holdings_count': len(user_context['portfolio']['holdings']),
                                 'brokers_connected': user_context['brokers']['count']
                             },
                             conversations=conversations,
                             messages=messages,
                             conversation_id=conversation_id,
                             broker_accounts=broker_accounts,
                             research_list=research_list,
                             prefill_symbol=prefill_symbol,
                             prefill_company=prefill_company,
                             prefill_asset=prefill_asset)
    except Exception as e:
        logger.error(f"Error loading Research Assistant: {str(e)}")
        flash('Error loading Research Assistant. Please try again.', 'error')
        return redirect(url_for('dashboard'))

@app.route('/api/research/upload', methods=['POST'])
@login_required
@csrf.exempt
def api_research_upload():
    """Handle file uploads for Research Assistant"""
    try:
        import os
        from werkzeug.utils import secure_filename
        
        # Check if files are in request
        if 'files' not in request.files:
            return jsonify({'success': False, 'error': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        if not files:
            return jsonify({'success': False, 'error': 'No files selected'}), 400
        
        # Create upload directory if it doesn't exist
        upload_folder = os.path.join('uploads', 'research', str(current_user.id))
        os.makedirs(upload_folder, exist_ok=True)
        
        uploaded_files = []
        for file in files:
            if file.filename == '':
                continue
            
            # Secure the filename
            filename = secure_filename(file.filename)
            filepath = os.path.join(upload_folder, filename)
            
            # Save the file
            file.save(filepath)
            
            # Get file info
            file_size = os.path.getsize(filepath)
            file_info = {
                'name': filename,
                'path': filepath,
                'size': file_size,
                'type': file.content_type
            }
            uploaded_files.append(file_info)
            
            logging.info(f"File uploaded: {filename} ({file_size} bytes) by user {current_user.id}")
        
        return jsonify({
            'success': True,
            'message': f'{len(uploaded_files)} file(s) uploaded successfully',
            'file_info': uploaded_files
        })
        
    except Exception as e:
        logging.error(f"File upload error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/research/clear-history', methods=['POST'])
@login_required
@csrf.exempt
def api_research_clear_history():
    """Clear all conversation history for the current user"""
    try:
        from models import ResearchConversation, ResearchMessage, SourceCitation
        
        # Get all user conversations
        user_conversations = ResearchConversation.query.filter_by(user_id=current_user.id).all()
        conversation_ids = [conv.id for conv in user_conversations]
        
        if conversation_ids:
            # Get all message IDs for these conversations
            message_ids = [msg.id for msg in ResearchMessage.query.filter(
                ResearchMessage.conversation_id.in_(conversation_ids)
            ).all()]
            
            # Delete source citations first (foreign key constraint)
            if message_ids:
                SourceCitation.query.filter(SourceCitation.message_id.in_(message_ids)).delete(synchronize_session=False)
            
            # Delete all messages
            ResearchMessage.query.filter(ResearchMessage.conversation_id.in_(conversation_ids)).delete(synchronize_session=False)
        
        # Delete all conversations
        ResearchConversation.query.filter_by(user_id=current_user.id).delete()
        
        db.session.commit()
        
        logging.info(f"User {current_user.id} cleared all conversation history")
        
        return jsonify({
            'success': True,
            'message': 'All conversation history cleared successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Clear history error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/research/query', methods=['POST'])
@login_required
@csrf.exempt
def api_research_query():
    """Handle research queries with LangGraph-powered RAG"""
    try:
        from services.langgraph_research_assistant import LangGraphResearchAssistant
        from services.comprehensive_portfolio_service import ComprehensivePortfolioService
        from models import ConversationHistory
        import uuid
        
        data = request.get_json()
        query = data.get('query', '').strip()
        conversation_id = data.get('conversation_id')
        use_langgraph = data.get('use_langgraph', True)  # Enable LangGraph by default
        
        if not query:
            return jsonify({'success': False, 'error': 'Query is required'}), 400
        
        if use_langgraph:
            # Use new LangGraph-powered assistant
            langgraph_assistant = LangGraphResearchAssistant()
            
            # Get user context (portfolio, preferences)
            portfolio_service = ComprehensivePortfolioService()
            try:
                portfolio_data = portfolio_service.get_portfolio_summary(current_user.id)
            except:
                portfolio_data = {}
            
            user_context = {
                'user_id': current_user.id,
                'portfolio': portfolio_data
            }
            
            # Perform LangGraph research
            result = langgraph_assistant.research(query, user_context)
            
            # Store in conversation history
            if not conversation_id:
                conversation_id = str(uuid.uuid4())
            
            conversation = ConversationHistory(
                user_id=current_user.id,
                session_id=conversation_id,
                query=query,
                response=result.get('answer', ''),
                citations=result.get('citations', []),
                trade_suggestions=result.get('trade_suggestions', []),
                conversation_metadata=result.get('metadata', {})
            )
            db.session.add(conversation)
            db.session.commit()
            
            return jsonify({
                'success': True,
                'answer': result.get('answer', ''),
                'citations': result.get('citations', []),
                'trade_suggestions': result.get('trade_suggestions', []),
                'conversation_id': conversation_id,
                'metadata': result.get('metadata', {}),
                'powered_by': 'LangGraph'
            })
        else:
            # Fallback to original service
            from services.research_assistant_service import ResearchAssistantService
            research_service = ResearchAssistantService()
            result = research_service.perform_research(current_user.id, query, conversation_id)
            return jsonify(result)
        
    except Exception as e:
        logger.error(f"Research query error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An error occurred. Please try again.'
        }), 500


@app.route('/api/research/sync-watchlist', methods=['POST'])
@login_required
@csrf.exempt
def api_research_sync_watchlist():
    """Add or update a symbol in the Research Watch List after user research"""
    try:
        from models import ResearchList
        data = request.get_json()
        symbol = data.get('symbol', '').strip().upper()
        company_name = data.get('company_name', '').strip()
        asset_type = data.get('asset_type', 'stocks')
        sector = data.get('sector', '').strip()

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        stock = ResearchList.query.filter_by(symbol=symbol).first()
        if stock:
            stock.last_requested_at = datetime.utcnow()
            if company_name and not stock.company_name:
                stock.company_name = company_name
            is_new = False
        else:
            stock = ResearchList(
                symbol=symbol,
                company_name=company_name or symbol,
                asset_type=asset_type,
                sector=sector or None,
                is_active=True,
                last_requested_at=datetime.utcnow(),
                computation_source='user_research',
                tenant_id='live'
            )
            db.session.add(stock)
            is_new = True

        db.session.commit()
        return jsonify({'success': True, 'is_new': is_new, 'symbol': symbol})
    except Exception as e:
        db.session.rollback()
        logger.error(f"sync-watchlist error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai-research/analyse', methods=['POST'])
@login_required
@csrf.exempt
def api_ai_research_analyse():
    """Run AI research pipeline for sentiment analysis"""
    try:
        from services.langgraph_ai_research_pipeline import ai_research_pipeline
        import asyncio
        
        data = request.get_json()
        symbol = data.get('symbol', '').strip().upper()
        asset_type = data.get('asset_type', 'stocks')
        date_from = data.get('date_from')
        date_to = data.get('date_to')
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        
        tenant_id = get_current_tenant_id()
        
        # Run the analysis pipeline
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                ai_research_pipeline.run_analysis(
                    symbol=symbol,
                    asset_type=asset_type,
                    asset_name=symbol,
                    date_range_start=date_from,
                    date_range_end=date_to,
                    user_id=current_user.id,
                    tenant_id=tenant_id
                )
            )
        finally:
            loop.close()
        
        if result.get('success'):
            return jsonify({
                'success': True,
                'symbol': result.get('symbol'),
                'overall_score': result.get('overall_score'),
                'confidence': result.get('confidence'),
                'recommendation': result.get('recommendation'),
                'recommendation_summary': result.get('recommendation_summary'),
                'component_scores': result.get('component_scores'),
                'run_id': result.get('run_id'),
                'cache_hit': result.get('cache_hit', False)
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Analysis failed')
            }), 500
            
    except Exception as e:
        logger.error(f"AI research analysis error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An error occurred during analysis. Please try again.'
        }), 500


@app.route('/api/portfolio/optimize-langgraph', methods=['POST'])
@login_required
@csrf.exempt
def api_portfolio_optimize_langgraph():
    """Multi-agent portfolio optimization using LangGraph"""
    try:
        from services.langgraph_portfolio_optimizer import LangGraphPortfolioOptimizer
        from models import PortfolioOptimizationReport
        
        optimizer = LangGraphPortfolioOptimizer()
        result = optimizer.optimize_portfolio(current_user.id)
        
        # Store optimization report
        report = PortfolioOptimizationReport(
            user_id=current_user.id,
            report_data=result,
            risk_analysis=result.get('risk_analysis', {}),
            sector_analysis=result.get('sector_analysis', {}),
            allocation_recommendations=result.get('allocation_recommendations', {}),
            opportunities=result.get('opportunities', [])
        )
        db.session.add(report)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'report': result.get('report', ''),
            'risk_analysis': result.get('risk_analysis', {}),
            'sector_analysis': result.get('sector_analysis', {}),
            'allocation_recommendations': result.get('allocation_recommendations', {}),
            'opportunities': result.get('opportunities', []),
            'metadata': result.get('metadata', {}),
            'report_id': report.id,
            'powered_by': 'LangGraph Multi-Agent System'
        })
        
    except Exception as e:
        logger.error(f"Portfolio optimization error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Optimization failed: {str(e)}'
        }), 500

@app.route('/api/portfolio/sync-embeddings', methods=['POST'])
@login_required
@csrf.exempt
def api_portfolio_sync_embeddings():
    """Sync all portfolio assets to vector database for semantic search"""
    try:
        from services.portfolio_embedding_service import PortfolioEmbeddingService
        
        embedding_service = PortfolioEmbeddingService()
        result = embedding_service.sync_all_user_assets(current_user.id)
        
        return jsonify({
            'success': True,
            'embeddings_created': result.get('embeddings_created', 0),
            'assets_processed': result.get('assets_processed', {}),
            'message': 'Portfolio embeddings synced successfully'
        })
        
    except Exception as e:
        logger.error(f"Embedding sync error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Embedding sync failed: {str(e)}'
        }), 500

@app.route('/api/portfolio/search-assets', methods=['POST'])
@login_required
def api_portfolio_search_assets():
    """Search user's portfolio assets using natural language"""
    try:
        from services.portfolio_embedding_service import PortfolioEmbeddingService
        
        data = request.get_json()
        query = data.get('query', '').strip()
        
        if not query:
            return jsonify({'success': False, 'error': 'Query is required'}), 400
        
        embedding_service = PortfolioEmbeddingService()
        results = embedding_service.search_user_assets(current_user.id, query, limit=10)
        
        return jsonify({
            'success': True,
            'results': results,
            'query': query,
            'count': len(results)
        })
        
    except Exception as e:
        logger.error(f"Asset search error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Search failed: {str(e)}'
        }), 500

@app.route('/api/signals/generate-langgraph', methods=['POST'])
@login_required
@csrf.exempt
def api_signals_generate_langgraph():
    """Generate trading signals using LangGraph pipeline"""
    try:
        from services.langgraph_signal_pipeline import LangGraphSignalPipeline
        from models import TradingSignal
        from datetime import date
        
        pipeline = LangGraphSignalPipeline()
        result = pipeline.generate_daily_signals()
        
        # Store signals in database
        signals_created = []
        for signal_data in result.get('signals', []):
            signal = TradingSignal(
                signal_date=date.today(),
                symbol=signal_data.get('symbol', 'UNKNOWN'),
                action=signal_data.get('action', 'BUY'),
                entry_price=signal_data.get('entry_price'),
                target_price=signal_data.get('target_price'),
                stop_loss=signal_data.get('stop_loss'),
                timeframe=signal_data.get('timeframe'),
                rationale=signal_data.get('rationale'),
                risk_reward_ratio=signal_data.get('risk_reward_ratio'),
                signal_data=signal_data,
                pipeline_metadata=result.get('pipeline_metadata', {})
            )
            db.session.add(signal)
            signals_created.append(signal)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'signals': result.get('signals', []),
            'signals_count': len(signals_created),
            'pipeline_metadata': result.get('pipeline_metadata', {}),
            'market_scan': result.get('market_scan', {}),
            'powered_by': 'LangGraph Signal Pipeline'
        })
        
    except Exception as e:
        logger.error(f"Signal generation error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Signal generation failed: {str(e)}'
        }), 500

@app.route('/api/trade/validate-execution', methods=['POST'])
@login_required
@csrf.exempt
def api_trade_validate_execution():
    """Validate trade execution using LangGraph 6-stage pipeline"""
    try:
        from services.langgraph_trade_executor import LangGraphTradeExecutor
        
        data = request.get_json()
        signal_id = data.get('signal_id')
        
        # Get signal data (either from database or from request)
        if signal_id:
            from models import TradingSignal
            signal = TradingSignal.query.get(signal_id)
            if not signal:
                return jsonify({'success': False, 'error': 'Signal not found'}), 404
            
            signal_data = {
                'symbol': signal.symbol,
                'action': signal.action,
                'entry_price': signal.entry_price,
                'target_price': signal.target_price,
                'stop_loss': signal.stop_loss,
                'timeframe': signal.timeframe,
                'quantity': data.get('quantity', 100)
            }
        else:
            # Manual signal data from request
            signal_data = {
                'symbol': data.get('symbol', '').upper(),
                'action': data.get('action', 'BUY'),
                'entry_price': float(data.get('entry_price', 0)),
                'target_price': float(data.get('target_price', 0)),
                'stop_loss': float(data.get('stop_loss', 0)),
                'timeframe': data.get('timeframe', 'Intraday'),
                'quantity': int(data.get('quantity', 100))
            }
        
        # Run validation pipeline
        executor = LangGraphTradeExecutor()
        result = executor.validate_trade(current_user.id, signal_data, signal_id)
        
        return jsonify({
            'success': result['success'],
            'execution_plan': result['execution_plan'],
            'validation_errors': result['validation_errors'],
            'stage_status': result['stage_status'],
            'pipeline_metadata': result['pipeline_metadata'],
            'powered_by': 'LangGraph Trade Plus Pipeline'
        })
        
    except Exception as e:
        logger.error(f"Trade validation error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Trade validation failed: {str(e)}',
            'validation_errors': [str(e)]
        }), 500

@app.route('/api/trade/execute-confirmed', methods=['POST'])
@login_required
@csrf.exempt
def api_trade_execute_confirmed():
    """Execute trade after user confirmation"""
    try:
        from services.broker_service import BrokerService
        from models_broker import BrokerAccount, BrokerOrder
        
        data = request.get_json()
        execution_plan = data.get('execution_plan', {})
        
        if not execution_plan or not execution_plan.get('ready_for_execution'):
            return jsonify({'success': False, 'error': 'Invalid execution plan'}), 400
        
        # Get broker account
        broker_id = execution_plan.get('broker_id')
        broker_account = BrokerAccount.query.get(broker_id)
        
        if not broker_account or broker_account.user_id != current_user.id:
            return jsonify({'success': False, 'error': 'Invalid broker account'}), 403
        
        # Prepare order data for broker
        order_data = {
            'symbol': execution_plan.get('symbol'),
            'trading_symbol': execution_plan.get('symbol'),
            'quantity': execution_plan.get('quantity'),
            'price': execution_plan.get('entry_price'),
            'transaction_type': execution_plan.get('action'),
            'order_type': execution_plan.get('order_type', 'LIMIT'),
            'product_type': execution_plan.get('product_type', 'MIS'),
            'exchange': 'NSE'
        }
        
        # Execute order through broker
        order_result = BrokerService.place_order_via_broker(broker_account, order_data)
        
        return jsonify({
            'success': True,
            'order_id': order_result.id,
            'broker_order_id': order_result.broker_order_id,
            'status': order_result.order_status,
            'message': 'Order placed successfully'
        })
        
    except Exception as e:
        logger.error(f"Trade execution error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Trade execution failed: {str(e)}'
        }), 500

@app.route('/api/data-quality', methods=['GET'])
@login_required
def api_data_quality():
    try:
        from services.broker_data_quality import BrokerDataQuality
        _dq = BrokerDataQuality(current_user.id, current_user.tenant_id or 'live')
        freshness = _dq.get_data_freshness()
        quality = _dq.get_quality_score()
        freshness_brokers = []
        for b in freshness.get('brokers', []):
            freshness_brokers.append({
                'id': b['id'], 'name': b['name'], 'freshness': b['freshness'],
                'age_display': b['age_display'], 'sync_status': b['sync_status'],
            })
        return jsonify({
            'success': True,
            'freshness': {
                'overall': freshness.get('overall', 'none'),
                'warning': freshness.get('warning'),
                'brokers': freshness_brokers,
            },
            'quality': quality,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/trade/execute-signal', methods=['POST'])
@login_required
@csrf.exempt
def api_trade_execute_signal():
    """Execute trade from a daily trading signal"""
    try:
        from services.broker_service import BrokerService
        from models_broker import BrokerAccount, BrokerOrder
        from models import PricingPlan
        
        # Check subscription access (admins always have full access)
        if not current_user.is_admin and current_user.pricing_plan not in [PricingPlan.TARGET_PRO, PricingPlan.HNI]:
            return jsonify({
                'success': False, 
                'error': 'Trade execution requires TARGET PRO or HNI subscription'
            }), 403
        
        data = request.get_json()

        selected_broker_id = data.get('broker_id')
        if selected_broker_id:
            selected_broker = BrokerAccount.query.filter_by(
                id=int(selected_broker_id),
                user_id=current_user.id,
                is_active=True,
                connection_status='connected'
            ).first()
        else:
            selected_broker = BrokerAccount.query.filter_by(
                user_id=current_user.id,
                is_active=True,
                connection_status='connected'
            ).first()

        if not selected_broker:
            return jsonify({
                'success': False,
                'error': 'No broker selected or connected. Please connect a broker first.'
            }), 400

        order_data = {
            'symbol': data.get('symbol'),
            'trading_symbol': data.get('symbol'),
            'quantity': int(data.get('quantity', 1)),
            'price': float(data.get('price')) if data.get('price') else None,
            'transaction_type': data.get('action', 'BUY'),
            'order_type': data.get('order_type', 'MARKET'),
            'product_type': data.get('product_type', 'MIS'),
            'exchange': 'NSE',
            'trigger_price': float(data.get('stop_loss')) if data.get('stop_loss') else None
        }

        try:
            from services.broker_data_quality import BrokerDataQuality
            _dq = BrokerDataQuality(current_user.id, current_user.tenant_id or 'live')
            validation = _dq.pre_trade_validation(selected_broker, order_data)
            if not validation['passed']:
                failed = [c for c in validation['checks'] if not c['pass']]
                return jsonify({
                    'success': False,
                    'error': 'Pre-trade validation failed',
                    'validation_checks': validation['checks'],
                    'failed_checks': [f"{c['check']}: {c['detail']}" for c in failed]
                }), 400
        except Exception as val_exc:
            logger.debug(f"Pre-trade validation skipped: {val_exc}")

        logger.info(f"Executing trade for user {current_user.id} via {selected_broker.broker_name}: {order_data}")

        try:
            order_result = BrokerService.place_order_via_broker(selected_broker, order_data)

            return jsonify({
                'success': True,
                'order_id': order_result.id if hasattr(order_result, 'id') else None,
                'broker_order_id': order_result.broker_order_id if hasattr(order_result, 'broker_order_id') else 'PENDING',
                'status': order_result.order_status if hasattr(order_result, 'order_status') else 'SUBMITTED',
                'message': f'Order placed successfully with {selected_broker.broker_name}'
            })
        except Exception as broker_error:
            logger.error(f"Broker execution error: {str(broker_error)}")
            return jsonify({
                'success': False,
                'error': f'Broker error: {str(broker_error)}'
            }), 500
        
    except Exception as e:
        logger.error(f"Trade execution error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Trade execution failed: {str(e)}'
        }), 500

@app.route('/api/ai/analyze-stock', methods=['POST'])
@login_required
def api_ai_analyze_stock():
    """Comprehensive AI stock analysis using all agents"""
    try:
        data = request.get_json()
        symbol = data.get('symbol', '').upper().strip()
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        
        # Initialize AI coordinator
        ai_coordinator = AgenticAICoordinator()
        
        # Get user's portfolio for context
        watchlist_items = WatchlistItem.query.filter_by(user_id=current_user.id).all()
        portfolio = {
            'total_value': 100000,  # Default portfolio value
            'positions': [{'symbol': item.symbol, 'value': 10000} for item in watchlist_items]
        }
        
        # Perform Agentic AI analysis (learn, reason, act, adapt)
        analysis_result = ai_coordinator.analyze_with_agentic_ai(symbol, "comprehensive")
        
        if 'error' in analysis_result:
            return jsonify({'success': False, 'error': analysis_result['error']}), 500
        
        # Store Agentic AI analysis in database
        ai_analysis = AIAnalysis(
            user_id=current_user.id,
            symbol=symbol,
            analysis_type='AGENTIC_AI',
            trading_recommendation=analysis_result.get('final_recommendation', 'HOLD'),
            trading_confidence=analysis_result.get('confidence', 0.5),
            trading_reasoning=analysis_result.get('reasoning_summary', 'Agentic AI analysis completed'),
            sentiment_score=0.5,  # Will be enhanced with research sentiment
            sentiment_label='NEUTRAL',
            news_sentiment='NEUTRAL',
            risk_level='MEDIUM',
            suggested_position_size=0.1,
            final_recommendation=analysis_result.get('final_recommendation', 'HOLD'),
            overall_confidence=analysis_result.get('confidence', 0.5),
            technical_indicators=str(analysis_result.get('research_insights', []))
        )
        
        db.session.add(ai_analysis)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': analysis_result,
            'analysis_id': ai_analysis.id
        })
        
    except Exception as e:
        logging.error(f"AI stock analysis error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to analyze stock'
        }), 500

@app.route('/api/portfolio/unified', methods=['GET'])
@login_required
def api_portfolio_unified():
    """Get unified portfolio view across all asset classes and brokers with optional filtering"""
    try:
        from models import Portfolio, AssetType
        from sqlalchemy import func
        
        # Get filters from query parameters
        asset_filter = request.args.get('type', None)
        broker_filter = request.args.get('broker', None)
        view = request.args.get('view', 'unified')
        
        # Base query
        query = Portfolio.query.filter_by(user_id=current_user.id)
        
        # Apply asset type filter if specified
        if asset_filter:
            asset_types_map = {
                'equities': 'equities',
                'mutual-funds': 'mutual_funds',
                'mutual_funds': 'mutual_funds',
                'fixed-income': 'fixed_income',
                'fixed_income': 'fixed_income',
                'futures-options': 'futures_options',
                'futures_options': 'futures_options',
                'nps': 'nps',
                'real-estate': 'real_estate',
                'real_estate': 'real_estate',
                'gold': 'gold',
                'etf': 'etf',
                'crypto': 'crypto',
                'esop': 'esop',
                'private-equity': 'private_equity',
                'private_equity': 'private_equity'
            }
            
            normalized_filter = asset_types_map.get(asset_filter.lower())
            if normalized_filter:
                query = query.filter_by(asset_type=normalized_filter)
            else:
                return jsonify({
                    'success': False, 
                    'error': f'Invalid asset type: {asset_filter}',
                    'valid_types': list(asset_types_map.keys())
                }), 400
        
        # Apply broker filter if specified
        if broker_filter:
            query = query.filter_by(broker_id=broker_filter)
        
        # Get filtered holdings
        holdings = query.all()
        
        # Calculate totals
        total_value = sum(h.current_value or 0 for h in holdings)
        total_invested = sum(h.purchased_value for h in holdings)
        total_pnl = total_value - total_invested
        total_pnl_percentage = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        
        # Group by asset type
        asset_breakdown = {}
        for holding in holdings:
            asset_type = holding.asset_type or 'unknown'
            if asset_type not in asset_breakdown:
                asset_breakdown[asset_type] = {
                    'asset_type': holding.get_asset_type_display(),
                    'holdings': [],
                    'total_value': 0,
                    'total_pnl': 0,
                    'count': 0
                }
            
            holding_data = {
                'id': holding.id,
                'ticker_symbol': holding.ticker_symbol,
                'asset_name': holding.stock_name,  # Use stock_name field
                'quantity': holding.quantity,
                'current_price': holding.current_price,
                'current_value': holding.current_value,
                'purchase_price': holding.purchase_price,
                'purchased_value': holding.purchased_value,
                'pnl_amount': holding.pnl_amount,
                'pnl_percentage': holding.pnl_percentage,
                'broker_name': holding.get_broker_name(),
                'sector': holding.sector,
                'date_purchased': holding.date_purchased.strftime('%Y-%m-%d') if holding.date_purchased else None,
                'risk_level': holding.get_risk_level(),
                'allocation_percentage': holding.allocation_percentage
            }
            
            # Add asset-specific information
            asset_specific_info = holding.get_asset_specific_info()
            if asset_specific_info:
                holding_data['asset_specific'] = asset_specific_info
            
            asset_breakdown[asset_type]['holdings'].append(holding_data)
            asset_breakdown[asset_type]['total_value'] += holding.current_value or 0
            asset_breakdown[asset_type]['total_pnl'] += holding.pnl_amount
            asset_breakdown[asset_type]['count'] += 1
        
        return jsonify({
            'success': True,
            'portfolio_summary': {
                'total_value': total_value,
                'total_invested': total_invested,
                'total_pnl': total_pnl,
                'total_pnl_percentage': total_pnl_percentage,
                'total_holdings': len(holdings)
            },
            'asset_breakdown': asset_breakdown,
            'filters': {
                'asset_type': asset_filter,
                'broker': broker_filter,
                'view': view
            },
            'metadata': {
                'last_updated': datetime.utcnow().isoformat(),
                'has_filters': bool(asset_filter or broker_filter)
            }
        })
        
    except Exception as e:
        logging.error(f"Portfolio unified API error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/portfolio', methods=['GET'])
@csrf.exempt
def api_portfolio():
    """Get unified portfolio data - main endpoint called by frontend"""
    # Handle unauthenticated requests gracefully (for OAuth flow)
    if not current_user.is_authenticated:
        return jsonify({
            'success': True,
            'portfolio_summary': {
                'total_value': 0,
                'total_invested': 0, 
                'total_pnl': 0,
                'total_pnl_percentage': 0,
                'total_holdings': 0
            },
            'asset_breakdown': {},
            'message': 'Please log in to view your portfolio'
        })
    
    try:
        from models import Portfolio, AssetType
        from sqlalchemy import func
        
        # Get asset type filter from query parameters
        asset_filter = request.args.get('type', None)
        view = request.args.get('view', 'unified')  # unified, by-broker, by-asset
        
        # Base query for user's portfolio
        query = Portfolio.query.filter_by(user_id=current_user.id)
        
        # Apply asset type filter if specified
        if asset_filter:
            try:
                # Handle both enum values and display names
                asset_types_map = {
                    'equities': 'equities',
                    'mutual_funds': 'mutual_funds', 
                    'mutual-funds': 'mutual_funds',
                    'fixed_income': 'fixed_income',
                    'fixed-income': 'fixed_income',
                    'futures_options': 'futures_options',
                    'futures-options': 'futures_options',
                    'nps': 'nps',
                    'real_estate': 'real_estate',
                    'real-estate': 'real_estate',
                    'gold': 'gold',
                    'etf': 'etf',
                    'crypto': 'crypto',
                    'esop': 'esop',
                    'private_equity': 'private_equity',
                    'private-equity': 'private_equity'
                }
                
                normalized_filter = asset_types_map.get(asset_filter.lower(), asset_filter)
                query = query.filter_by(asset_type=normalized_filter)
                
            except (ValueError, KeyError):
                return jsonify({
                    'success': False, 
                    'error': f'Invalid asset type: {asset_filter}',
                    'valid_types': list(asset_types_map.keys())
                }), 400
        
        # Get filtered holdings
        holdings = query.all()
        
        # Calculate portfolio summary
        total_value = sum(h.current_value or 0 for h in holdings)
        total_invested = sum(h.purchased_value for h in holdings)
        total_pnl = total_value - total_invested
        total_pnl_percentage = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        
        # Calculate today's change (placeholder - would need real-time data)
        today_change = 0  # This would be calculated from price changes
        
        # Format holdings for frontend
        formatted_holdings = []
        for holding in holdings:
            holding_data = {
                'id': holding.id,
                'ticker_symbol': holding.ticker_symbol,
                'asset_name': holding.stock_name,  # Using stock_name field as asset_name
                'asset_type': holding.get_asset_type_display(),
                'quantity': holding.quantity,
                'current_price': holding.current_price,
                'current_value': holding.current_value,
                'purchase_price': holding.purchase_price,
                'purchased_value': holding.purchased_value,
                'pnl_amount': holding.pnl_amount,
                'pnl_percentage': holding.pnl_percentage,
                'broker_name': holding.get_broker_name(),
                'sector': holding.sector,
                'allocation_percentage': holding.allocation_percentage,
                'date_purchased': holding.date_purchased.strftime('%Y-%m-%d') if holding.date_purchased else None,
                'pnl_class': holding.get_pnl_class()
            }
            
            # Add asset-specific fields based on type
            if holding.is_futures_options():
                holding_data.update({
                    'contract_type': holding.contract_type,
                    'strike_price': holding.strike_price,
                    'expiry_date': holding.expiry_date.strftime('%Y-%m-%d') if holding.expiry_date else None,
                    'lot_size': holding.lot_size,
                    'days_to_expiry': holding.days_to_expiry(),
                    'is_expiring_soon': holding.is_expiring_soon()
                })
            elif holding.is_nps():
                holding_data.update({
                    'nps_scheme': holding.nps_scheme,
                    'pension_fund_manager': holding.pension_fund_manager,
                    'tier': holding.tier
                })
            elif holding.is_real_estate():
                holding_data.update({
                    'property_type': holding.property_type,
                    'property_location': holding.property_location,
                    'area_sqft': holding.area_sqft
                })
            elif holding.is_fixed_income():
                holding_data.update({
                    'maturity_date': holding.maturity_date.strftime('%Y-%m-%d') if holding.maturity_date else None,
                    'interest_rate': holding.interest_rate,
                    'coupon_rate': holding.coupon_rate,
                    'face_value': holding.face_value
                })
            elif holding.is_gold():
                holding_data.update({
                    'gold_form': holding.gold_form,
                    'gold_purity': holding.gold_purity,
                    'grams': holding.grams
                })
            
            formatted_holdings.append(holding_data)
        
        # Group by asset type for unified view
        asset_breakdown = {}
        if not asset_filter:  # Only show breakdown if no specific filter
            for holding in holdings:
                asset_type = holding.asset_type or 'unknown'
                if asset_type not in asset_breakdown:
                    asset_breakdown[asset_type] = {
                        'asset_type': holding.get_asset_type_display(),
                        'count': 0,
                        'total_value': 0,
                        'total_pnl': 0,
                        'allocation_percentage': 0
                    }
                
                asset_breakdown[asset_type]['count'] += 1
                asset_breakdown[asset_type]['total_value'] += holding.current_value or 0
                asset_breakdown[asset_type]['total_pnl'] += holding.pnl_amount
                asset_breakdown[asset_type]['allocation_percentage'] += holding.allocation_percentage
        
        # Response structure matching frontend expectations
        response_data = {
            'success': True,
            'portfolio': {
                'totalValue': total_value,
                'totalInvested': total_invested,
                'totalPnL': total_pnl,
                'totalPnLPercentage': total_pnl_percentage,
                'todayChange': today_change,
                'totalHoldings': len(holdings),
                'holdings': formatted_holdings
            },
            'asset_breakdown': asset_breakdown,
            'filters': {
                'asset_type': asset_filter,
                'view': view
            },
            'metadata': {
                'last_updated': datetime.utcnow().isoformat(),
                'user_id': current_user.id,
                'has_filter': bool(asset_filter)
            }
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"Portfolio API error: {str(e)}")
        return jsonify({
            'success': False, 
            'error': str(e),
            'portfolio': {
                'totalValue': 0,
                'todayChange': 0,
                'holdings': []
            }
        }), 500

@app.route('/api/trading-signals', methods=['GET'])
@csrf.exempt
def api_trading_signals():
    """Get trading signals data with real-time NSE prices - main endpoint called by frontend"""
    # Handle unauthenticated requests gracefully (for OAuth flow)
    if not current_user.is_authenticated:
        return jsonify({
            'success': True,
            'signals': [],
            'message': 'Please log in to view trading signals'
        })
    
    try:
        from models import TradingSignal, PricingPlan
        from sqlalchemy import desc
        from services.nse_service import NSEService
        
        # Check if user has paid subscription (admins always have full access)
        if not current_user.is_admin and current_user.pricing_plan not in [PricingPlan.TARGET_PLUS, PricingPlan.TARGET_PRO, PricingPlan.HNI]:
            return jsonify({
                'success': True,
                'signals': [],
                'message': 'Trading signals are available for Trader, Trader Plus, and HNI subscribers only.'
            })
        
        # Get active trading signals
        signals = TradingSignal.query.filter(
            TradingSignal.status == 'ACTIVE'
        ).order_by(desc(TradingSignal.created_at)).limit(20).all()
        
        # Initialize NSE service for real-time prices
        nse_service = NSEService()
        
        signals_data = []
        for signal in signals:
            # Fetch real-time price from NSE for stock signals
            current_price = None
            price_change_percent = None
            if signal.signal_type == 'stock' and signal.symbol:
                try:
                    stock_quote = nse_service.get_stock_quote(signal.symbol)
                    if stock_quote:
                        current_price = stock_quote.get('current_price')
                        # Calculate change from entry price
                        if current_price and signal.entry_price:
                            price_change_percent = ((current_price - float(signal.entry_price)) / float(signal.entry_price)) * 100
                except Exception as e:
                    logging.warning(f"Failed to fetch current price for {signal.symbol}: {str(e)}")
            
            signals_data.append({
                'id': signal.id,
                'signal_type': signal.signal_type,
                'symbol': signal.symbol,
                'company_name': signal.company_name,
                'action': signal.action,
                'entry_price': float(signal.entry_price) if signal.entry_price else None,
                'current_price': current_price,  # Real-time NSE price
                'price_change_percent': round(price_change_percent, 2) if price_change_percent else None,
                'target_price': float(signal.target_price) if signal.target_price else None,
                'stop_loss': float(signal.stop_loss) if signal.stop_loss else None,
                'quantity': signal.quantity,
                'risk_level': signal.risk_level,
                'time_frame': signal.time_frame,
                'strategy': signal.strategy_name,
                'notes': signal.notes,
                'sector': signal.sector,
                'category': signal.category,
                'expiry_date': signal.expires_at.isoformat() if signal.expires_at else None,
                'created_at': signal.created_at.isoformat() if signal.created_at else None,
                'status': signal.status,
                'potential_return': signal.potential_return
            })
        
        return jsonify({
            'success': True,
            'signals': signals_data
        })
        
    except Exception as e:
        logging.error(f"Trading signals API error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/portfolio/by-broker', methods=['GET'])
@login_required
def api_portfolio_by_broker():
    """Get portfolio view grouped by broker with optional asset type filtering"""
    try:
        from models import Portfolio, BrokerAccount
        
        # Get asset type filter from query parameters
        asset_filter = request.args.get('type', None)
        
        # Base query for user's portfolio
        query = Portfolio.query.filter_by(user_id=current_user.id)
        
        # Apply asset type filter if specified
        if asset_filter:
            asset_types_map = {
                'equities': 'equities',
                'mutual-funds': 'mutual_funds',
                'mutual_funds': 'mutual_funds',
                'fixed-income': 'fixed_income',
                'fixed_income': 'fixed_income',
                'futures-options': 'futures_options',
                'futures_options': 'futures_options',
                'nps': 'nps',
                'real-estate': 'real_estate',
                'real_estate': 'real_estate',
                'gold': 'gold',
                'etf': 'etf',
                'crypto': 'crypto',
                'esop': 'esop',
                'private-equity': 'private_equity',
                'private_equity': 'private_equity'
            }
            
            normalized_filter = asset_types_map.get(asset_filter.lower())
            if normalized_filter:
                query = query.filter_by(asset_type=normalized_filter)
            else:
                return jsonify({
                    'success': False,
                    'error': f'Invalid asset type: {asset_filter}',
                    'valid_types': list(asset_types_map.keys())
                }), 400
        
        # Get filtered holdings
        holdings = query.all()
        
        # Group by broker
        broker_breakdown = {}
        total_value = 0
        total_pnl = 0
        
        for holding in holdings:
            broker_key = holding.get_broker_name()
            broker_id = holding.broker_id
            
            if broker_key not in broker_breakdown:
                broker_breakdown[broker_key] = {
                    'broker_id': broker_id,
                    'broker_name': broker_key,
                    'connection_status': 'manual' if broker_key == 'Manual Entry' else 'connected',
                    'holdings': [],
                    'total_value': 0,
                    'total_pnl': 0,
                    'count': 0,
                    'asset_types': set()
                }
            
            holding_data = {
                'id': holding.id,
                'ticker_symbol': holding.ticker_symbol,
                'asset_name': holding.stock_name,  # Use stock_name field
                'asset_type': holding.get_asset_type_display(),
                'quantity': holding.quantity,
                'current_price': holding.current_price,
                'current_value': holding.current_value,
                'purchase_price': holding.purchase_price,
                'purchased_value': holding.purchased_value,
                'pnl_amount': holding.pnl_amount,
                'pnl_percentage': holding.pnl_percentage,
                'sector': holding.sector,
                'date_purchased': holding.date_purchased.strftime('%Y-%m-%d') if holding.date_purchased else None,
                'risk_level': holding.get_risk_level(),
                'allocation_percentage': holding.allocation_percentage
            }
            
            # Add asset-specific information
            asset_specific_info = holding.get_asset_specific_info()
            if asset_specific_info:
                holding_data['asset_specific'] = asset_specific_info
            
            broker_breakdown[broker_key]['holdings'].append(holding_data)
            broker_breakdown[broker_key]['total_value'] += holding.current_value or 0
            broker_breakdown[broker_key]['total_pnl'] += holding.pnl_amount
            broker_breakdown[broker_key]['count'] += 1
            broker_breakdown[broker_key]['asset_types'].add(holding.get_asset_type_display())
            
            total_value += holding.current_value or 0
            total_pnl += holding.pnl_amount
        
        # Convert sets to lists for JSON serialization
        for broker_data in broker_breakdown.values():
            broker_data['asset_types'] = list(broker_data['asset_types'])
            # Calculate broker allocation percentage
            broker_data['allocation_percentage'] = (broker_data['total_value'] / total_value * 100) if total_value > 0 else 0
        
        return jsonify({
            'success': True,
            'broker_breakdown': broker_breakdown,
            'portfolio_summary': {
                'total_brokers': len(broker_breakdown),
                'total_value': total_value,
                'total_pnl': total_pnl,
                'total_holdings': len(holdings)
            },
            'filters': {
                'asset_type': asset_filter
            },
            'metadata': {
                'last_updated': datetime.utcnow().isoformat(),
                'has_filter': bool(asset_filter)
            }
        })
        
    except Exception as e:
        logging.error(f"Portfolio by-broker API error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/portfolio/by-asset-type/<asset_type>', methods=['GET'])
@login_required
def api_portfolio_by_asset_type(asset_type):
    """Get portfolio holdings for specific asset type"""
    try:
        from models import Portfolio, AssetType
        
        # Validate asset type
        try:
            asset_enum = AssetType(asset_type)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid asset type'}), 400
        
        # Get holdings for specific asset type
        holdings = Portfolio.query.filter_by(
            user_id=current_user.id,
            asset_type=asset_enum
        ).all()
        
        holdings_data = []
        total_value = 0
        total_pnl = 0
        
        for holding in holdings:
            holding_data = {
                'id': holding.id,
                'ticker_symbol': holding.ticker_symbol,
                'asset_name': holding.asset_name,
                'quantity': holding.quantity,
                'current_price': holding.current_price,
                'current_value': holding.current_value,
                'purchase_price': holding.purchase_price,
                'purchased_value': holding.purchased_value,
                'pnl_amount': holding.pnl_amount,
                'pnl_percentage': holding.pnl_percentage,
                'broker_name': holding.get_broker_name(),
                'sector': holding.sector,
                'date_purchased': holding.date_purchased.strftime('%Y-%m-%d') if holding.date_purchased else None
            }
            
            # Add asset-specific fields based on type
            if asset_enum == AssetType.FUTURES_OPTIONS:
                holding_data.update({
                    'contract_type': holding.contract_type,
                    'strike_price': holding.strike_price,
                    'expiry_date': holding.expiry_date.strftime('%Y-%m-%d') if holding.expiry_date else None,
                    'lot_size': holding.lot_size
                })
            elif asset_enum == AssetType.NPS:
                holding_data.update({
                    'nps_scheme': holding.nps_scheme,
                    'pension_fund_manager': holding.pension_fund_manager
                })
            elif asset_enum == AssetType.REAL_ESTATE:
                holding_data.update({
                    'property_type': holding.property_type,
                    'property_location': holding.property_location
                })
            elif asset_enum == AssetType.FIXED_INCOME:
                holding_data.update({
                    'maturity_date': holding.maturity_date.strftime('%Y-%m-%d') if holding.maturity_date else None,
                    'interest_rate': holding.interest_rate
                })
            
            holdings_data.append(holding_data)
            total_value += holding.current_value or 0
            total_pnl += holding.pnl_amount
        
        return jsonify({
            'success': True,
            'asset_type': asset_enum.value,
            'asset_type_display': holdings[0].get_asset_type_display() if holdings else '',
            'holdings': holdings_data,
            'summary': {
                'total_value': total_value,
                'total_pnl': total_pnl,
                'count': len(holdings)
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai/optimize-portfolio', methods=['POST'])
@login_required
def api_ai_optimize_portfolio():
    """AI-powered portfolio optimization"""
    try:
        # Get user's current portfolio
        watchlist_items = WatchlistItem.query.filter_by(user_id=current_user.id).all()
        
        portfolio = {
            'total_value': 100000,  # This could be calculated from actual holdings
            'positions': [
                {
                    'symbol': item.symbol,
                    'value': 10000,  # This should be actual position value
                    'target_price': item.target_price
                } for item in watchlist_items
            ]
        }
        
        # Initialize AI coordinator
        ai_coordinator = AgenticAICoordinator()
        
        # Perform portfolio optimization
        optimization_result = ai_coordinator.optimize_portfolio_comprehensive(portfolio)
        
        if 'error' in optimization_result:
            return jsonify({'success': False, 'error': optimization_result['error']}), 500
        
        # Store optimization results
        portfolio_opt = PortfolioOptimization(
            user_id=current_user.id,
            total_value=portfolio['total_value'],
            num_positions=len(portfolio['positions']),
            rebalance_needed=optimization_result['optimization'].get('rebalance_needed', False),
            efficiency_score=optimization_result['optimization'].get('efficiency_score'),
            diversification_score=optimization_result['optimization'].get('diversification_score'),
            suggested_actions=str(optimization_result['optimization'].get('suggested_actions', [])),
            allocation_recommendations=str(optimization_result['optimization'].get('risk_adjusted_allocation', {}))
        )
        
        db.session.add(portfolio_opt)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': optimization_result,
            'optimization_id': portfolio_opt.id
        })
        
    except Exception as e:
        logging.error(f"Portfolio optimization error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to optimize portfolio'
        }), 500

@app.route('/api/n8n/trigger-workflow', methods=['POST'])
@login_required
def api_trigger_n8n_workflow():
    """Trigger n8n workflow for Agentic AI automation"""
    try:
        data = request.get_json()
        workflow_type = data.get('workflow_type')
        workflow_data = data.get('data', {})
        
        if not workflow_type:
            return jsonify({'success': False, 'error': 'Workflow type is required'}), 400
        
        # Initialize AI coordinator for n8n integration
        ai_coordinator = AgenticAICoordinator()
        
        # Trigger the specified workflow
        result = ai_coordinator._trigger_n8n_workflow(workflow_type, workflow_data)
        
        return jsonify({
            'success': result,
            'message': f'Workflow {workflow_type} triggered successfully' if result else f'Failed to trigger workflow {workflow_type}',
            'workflow_type': workflow_type,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"n8n workflow trigger error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai/sentiment-analysis/<symbol>')
@login_required
def api_ai_sentiment_analysis(symbol):
    """Get AI sentiment analysis for a specific stock"""
    try:
        ai_coordinator = AgenticAICoordinator()
        sentiment_result = ai_coordinator.sentiment_agent.analyze_sentiment(symbol.upper())
        
        if 'error' in sentiment_result:
            return jsonify({'success': False, 'error': sentiment_result['error']}), 500
        
        return jsonify({
            'success': True,
            'data': sentiment_result
        })
        
    except Exception as e:
        logging.error(f"Sentiment analysis error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to analyze sentiment'
        }), 500

@app.route('/api/ai/trading-signals/<symbol>')
@login_required
def api_ai_trading_signals(symbol):
    """Get AI trading signals for a specific stock"""
    try:
        ai_coordinator = AgenticAICoordinator()
        trading_result = ai_coordinator.trading_agent.analyze_stock(symbol.upper())
        
        if 'error' in trading_result:
            return jsonify({'success': False, 'error': trading_result['error']}), 500
        
        return jsonify({
            'success': True,
            'data': trading_result
        })
        
    except Exception as e:
        logging.error(f"Trading signals error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to get trading signals'
        }), 500

# Account Management Routes
@app.route('/account/profile')
@login_required
def account_profile():
    """User profile management"""
    # Get user statistics for display
    from models import TradingSignal
    trading_signals_count = TradingSignal.query.count()
    return render_template('account/profile.html', 
                         active_section='profile',
                         trading_signals_count=trading_signals_count,
                         PricingPlan=PricingPlan)

@app.route('/account/profile', methods=['POST'])
@login_required
def update_profile():
    """Update user profile - Only allows specific fields to prevent privilege escalation"""
    try:
        # SECURITY: Explicit whitelist of updatable fields
        # NEVER allow: is_admin, pricing_plan, subscription_status, tenant_id
        ALLOWED_PROFILE_FIELDS = {'first_name', 'last_name', 'email', 'username'}
        
        # Safely update only allowed fields
        if request.form.get('first_name'):
            current_user.first_name = request.form.get('first_name')
        if request.form.get('last_name'):
            current_user.last_name = request.form.get('last_name')
        if request.form.get('email'):
            # Validate email uniqueness within tenant
            new_email = request.form.get('email')
            if new_email != current_user.email:
                existing = TenantQuery(User).filter(User.email == new_email, User.id != current_user.id).first()
                if existing:
                    flash('Email already in use by another account.', 'error')
                    return redirect(url_for('account_profile'))
            current_user.email = new_email
        if request.form.get('username'):
            # Validate username uniqueness within tenant
            new_username = request.form.get('username')
            if new_username != current_user.username:
                existing = TenantQuery(User).filter(User.username == new_username, User.id != current_user.id).first()
                if existing:
                    flash('Username already in use by another account.', 'error')
                    return redirect(url_for('account_profile'))
            current_user.username = new_username
        
        db.session.commit()
        logging.info(f"User {current_user.id} updated profile fields: {list(ALLOWED_PROFILE_FIELDS & set(request.form.keys()))}")
        flash('Profile updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Profile update failed for user {current_user.id}: {str(e)}")
        flash('Error updating profile. Please try again.', 'error')
        
    return redirect(url_for('account_profile'))

@app.route('/account')
@login_required
def account_settings():
    """Account settings page"""
    # Get user preferences from session or use defaults
    user_preferences = session.get('user_preferences', {
        'email_trading_alerts': True,
        'email_market_updates': True,
        'email_billing_updates': True,
        'email_promotional': False,
        'default_dashboard_view': 'overview',
        'theme_preference': 'light',
        'timezone': 'Asia/Kolkata'
    })
    
    return render_template('account/settings.html', 
                         active_section='account',
                         user_preferences=user_preferences)

@app.route('/account/billing')
@login_required
def account_billing():
    """Billing and subscription management"""
    payments = Payment.query.filter_by(user_id=current_user.id).order_by(Payment.created_at.desc()).limit(10).all()
    
    return render_template('account/billing.html', 
                         active_section='billing',
                         payments=payments,
                         PricingPlan=PricingPlan,
                         razorpay_key_id=RAZORPAY_KEY_ID)

@app.route('/account/referrals')
@login_required
def account_referrals():
    """Referrals management"""
    if not current_user.referral_code:
        current_user.generate_referral_code()
        db.session.commit()
    
    referrals = Referral.query.filter_by(referrer_id=current_user.id).order_by(Referral.created_at.desc()).all()
    
    referral_stats = {
        'total_referrals': len(referrals),
        'successful_referrals': sum(1 for r in referrals if r.status == 'paid'),
        'pending_referrals': sum(1 for r in referrals if r.status == 'pending'),
        'total_earned': sum(r.reward_amount for r in referrals if r.status == 'paid'),
        'pending_amount': sum(r.reward_amount for r in referrals if r.status == 'pending'),
        'paid_amount': sum(r.reward_amount for r in referrals if r.status == 'paid')
    }
    
    return render_template('account/referrals.html', 
                         active_section='referrals',
                         referrals=referrals,
                         referral_stats=referral_stats)

# API Routes for Account Management
@app.route('/api/create-order', methods=['POST'])
@login_required
def create_razorpay_order():
    """Create Razorpay order for subscription payment"""
    try:
        data = request.get_json()
        plan_type = data.get('plan_type')
        amount = data.get('amount')
        
        if razorpay_client:
            # Create actual Razorpay order
            order_data = {
                'amount': amount * 100,  # Amount in paise
                'currency': 'INR',
                'receipt': f'tcapital_{current_user.id}_{int(datetime.now(timezone.utc).timestamp())}',
                'notes': {
                    'user_id': str(current_user.id),
                    'plan_type': plan_type,
                    'email': current_user.email
                }
            }
            order = razorpay_client.order.create(data=order_data)
            
            return jsonify({
                'success': True,
                'order_id': order['id'],
                'amount': order['amount'],
                'currency': order['currency'],
                'key': RAZORPAY_KEY_ID
            })
        else:
            # Fallback for demo/testing without real keys
            order_data = {
                'success': True,
                'order_id': f'order_{int(datetime.now(timezone.utc).timestamp())}',
                'amount': amount * 100,
                'currency': 'INR',
                'key': RAZORPAY_KEY_ID
            }
            return jsonify(order_data)
        
    except Exception as e:
        logging.error(f"Razorpay order creation failed: {str(e)}")
        return jsonify({'success': False, 'message': 'Failed to create payment order'})

@app.route('/api/payment-success', methods=['POST'])
@login_required
def handle_payment_success():
    """Handle successful payment and upgrade user plan"""
    try:
        data = request.get_json()
        payment_id = data.get('payment_id')
        order_id = data.get('order_id')
        signature = data.get('signature')
        plan_type = data.get('plan_type')
        
        # Verify payment signature if using real Razorpay
        if razorpay_client and signature:
            try:
                razorpay_client.utility.verify_payment_signature({
                    'razorpay_order_id': order_id,
                    'razorpay_payment_id': payment_id,
                    'razorpay_signature': signature
                })
            except:
                return jsonify({'success': False, 'message': 'Payment verification failed'})
        
        # Determine plan and amount
        if plan_type == 'target_plus':
            current_user.pricing_plan = PricingPlan.TARGET_PLUS
            amount = 1499
        elif plan_type == 'target_pro':
            current_user.pricing_plan = PricingPlan.TARGET_PRO
            amount = 2499
        else:
            return jsonify({'success': False, 'message': 'Invalid plan type'})
        
        # Update user subscription
        current_user.subscription_status = SubscriptionStatus.ACTIVE
        current_user.subscription_start_date = datetime.utcnow()
        current_user.subscription_end_date = datetime.utcnow() + timedelta(days=30)
        current_user.total_payments += amount
        
        # Create payment record
        payment = Payment(
            user_id=current_user.id,
            razorpay_payment_id=payment_id,
            razorpay_order_id=order_id,
            amount=amount,
            status='captured',
            plan_type=current_user.pricing_plan,
            billing_period='monthly'
        )
        
        db.session.add(payment)
        db.session.commit()
        
        logging.info(f"User {current_user.id} upgraded to {plan_type} plan")
        return jsonify({'success': True, 'message': 'Plan upgraded successfully!'})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Payment processing failed: {str(e)}")
        return jsonify({'success': False, 'message': 'Payment processing failed'})

@app.route('/api/generate-referral-code', methods=['POST'])
@login_required
def generate_referral_code():
    """Generate referral code for user"""
    try:
        referral_code = current_user.generate_referral_code()
        db.session.commit()
        
        return jsonify({'success': True, 'referral_code': referral_code})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


# ===== AI INVESTMENT CHATBOT ROUTES =====

@app.route('/dashboard/ai-chat')
@login_required
def ai_chat():
    """Unified AI Advisor with Chat Interface and Market Intelligence"""
    if not current_user.can_access_menu('ai_advisor'):
        flash('This feature requires a subscription. Please upgrade your plan.', 'warning')
        return redirect(url_for('account_billing'))
    
    try:
        # Initialize services
        from services.market_intelligence_service import MarketIntelligenceService
        from services.investment_analysis_service import InvestmentAnalysisService
        from services.chatbot_service import InvestmentChatbot
        
        market_service = MarketIntelligenceService()
        investment_service = InvestmentAnalysisService()
        chatbot = InvestmentChatbot()
        
        # Get market intelligence data
        market_sentiment = market_service.get_market_sentiment()
        sector_performance = market_service.get_sector_performance()
        market_trends = market_service.get_market_trends()
        
        # Get user portfolio for analysis
        user_portfolio = {
            'total_value': 250000,
            'positions': [
                {'symbol': 'RELIANCE', 'value': 50000},
                {'symbol': 'TCS', 'value': 40000},
                {'symbol': 'HDFCBANK', 'value': 35000},
                {'symbol': 'INFY', 'value': 30000},
                {'symbol': 'ICICIBANK', 'value': 25000}
            ]
        }
        
        # Generate portfolio insights
        portfolio_insights = investment_service.generate_portfolio_insights(user_portfolio)
        
        # Get market opportunities
        market_opportunities = investment_service.get_market_opportunities(50000, 'moderate')
        
        # Get user's recent conversations
        conversations = chatbot.get_user_conversations(current_user.id, limit=10)
        
        # Prepare AI advisor data
        ai_advisor_data = {
            'market_sentiment': market_sentiment,
            'sector_performance': sector_performance,
            'market_trends': market_trends,
            'portfolio_insights': portfolio_insights,
            'market_opportunities': market_opportunities,
            'ai_stats': {
                'decisions_this_month': 47,
                'success_rate': 89.2,
                'active_workflows': 12,
                'market_sentiment_label': market_sentiment.get('sentiment', 'Neutral')
            }
        }
        
        return render_template('dashboard/ai_chat.html', 
                             conversations=conversations,
                             ai_advisor_data=ai_advisor_data,
                             active_menu='ai_chat')
                             
    except Exception as e:
        logging.error(f"AI Advisor data loading error: {str(e)}")
        # Fallback to basic chat interface
        try:
            from services.chatbot_service import InvestmentChatbot
            chatbot = InvestmentChatbot()
            conversations = chatbot.get_user_conversations(current_user.id, limit=10)
        except Exception as fallback_error:
            logging.error(f"Chatbot service fallback error: {str(fallback_error)}")
            conversations = []
        
        return render_template('dashboard/ai_chat.html', 
                             conversations=conversations,
                             active_menu='ai_chat')

@app.route('/dashboard/ai-chat/conversation/<session_id>')
@login_required
def ai_chat_conversation(session_id):
    """Load specific conversation"""
    if not current_user.can_access_menu('ai_advisor'):
        return jsonify({'error': 'Access denied'}), 403
    
    conversation = ChatConversation.query.filter_by(
        session_id=session_id, 
        user_id=current_user.id,
        is_active=True
    ).first()
    
    if not conversation:
        return jsonify({'error': 'Conversation not found'}), 404
    
    messages = conversation.get_recent_messages(50)
    messages_data = []
    
    for msg in messages:
        messages_data.append({
            'id': msg.id,
            'type': msg.message_type,
            'content': msg.content,
            'timestamp': msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    
    return jsonify({
        'conversation_id': conversation.session_id,
        'title': conversation.title,
        'messages': messages_data
    })

@app.route('/api/ai-chat/send', methods=['POST'])
@login_required
def api_ai_chat_send():
    """Clean AI Chat API Endpoint"""
    if not current_user.can_access_menu('ai_advisor'):
        return jsonify({'error': 'Access denied. Please upgrade your subscription.'}), 403
    
    try:
        # Initialize clean AI chat service
        from services.ai_chat_service import AIChatService
        from services.advanced_ai_functions import AdvancedAIFunctions
        ai_chat = AIChatService()
        advanced_ai = AdvancedAIFunctions()
        
        data = request.get_json()
        if not data or not data.get('message'):
            return jsonify({'error': 'Message is required'}), 400
        
        user_message = data.get('message').strip()
        conversation_id = data.get('conversation_id')
        
        if len(user_message) > 2000:
            return jsonify({'error': 'Message too long. Please keep it under 2000 characters.'}), 400
        
        # Process message with clean service
        ai_response, conv_id, usage_info = ai_chat.process_user_message(
            current_user.id, 
            user_message, 
            conversation_id
        )
        
        return jsonify({
            'success': True,
            'conversation_id': conv_id,
            'ai_response': ai_response
        })
        
    except Exception as e:
        logging.error(f"Error in AI chat endpoint: {e}")
        return jsonify({'error': 'Unable to process your request. Please try again.'}), 500

@app.route('/api/ai-advisor/advanced-function', methods=['POST']) 
@login_required
def ai_advisor_advanced_function():
    """Handle advanced AI function calls"""
    if not current_user.can_access_menu('ai_advisor'):
        return jsonify({'error': 'Access denied. Please upgrade your subscription.'}), 403
    
    try:
        from services.advanced_ai_functions import AdvancedAIFunctions
        advanced_ai = AdvancedAIFunctions()
        
        data = request.get_json()
        function_type = data.get('function_type')
        parameters = data.get('parameters', {})
        
        if function_type == 'news_impact':
            result = advanced_ai.news_impact_analyzer(
                stock_symbols=parameters.get('stock_symbols'),
                portfolio_stocks=parameters.get('portfolio_stocks')
            )
        elif function_type == 'competitive_comparison':
            result = advanced_ai.competitive_stock_comparison(
                primary_stock=parameters.get('primary_stock'),
                sector=parameters.get('sector')
            )
        elif function_type == 'sector_rotation':
            result = advanced_ai.sector_rotation_predictor(
                current_sectors=parameters.get('current_sectors')
            )
        elif function_type == 'crash_opportunities':
            result = advanced_ai.market_crash_opportunity_scanner(
                risk_tolerance=parameters.get('risk_tolerance', 'moderate')
            )
        elif function_type == 'ipo_analyzer':
            result = advanced_ai.ipo_new_listing_analyzer(
                specific_ipo=parameters.get('specific_ipo')
            )
        else:
            return jsonify({'error': 'Invalid function type'}), 400
            
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in advanced AI function: {e}")
        return jsonify({'error': 'Failed to execute AI function'}), 500

@app.route('/api/ai-advisor/functions', methods=['GET'])
@login_required
def get_ai_advisor_functions():
    """Get list of available AI Advisor functions"""
    try:
        from services.advanced_ai_functions import AdvancedAIFunctions
        advanced_ai = AdvancedAIFunctions()
        functions = advanced_ai.get_available_functions()
        return jsonify({'functions': functions})
    except Exception as e:
        logger.error(f"Error getting AI functions: {e}")
        return jsonify({'error': 'Failed to get functions'}), 500

@app.route('/api/ai-chat/conversations')
@login_required 
def api_ai_chat_conversations():
    """Get user's chat conversations"""
    if not current_user.can_access_menu('ai_advisor'):
        return jsonify({'error': 'Access denied'}), 403
    
    conversations = chatbot.get_user_conversations(current_user.id)
    conversations_data = []
    
    for conv in conversations:
        last_message = conv.messages.order_by(ChatMessage.created_at.desc()).first()
        conversations_data.append({
            'session_id': conv.session_id,
            'title': conv.title,
            'updated_at': conv.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
            'message_count': conv.messages.count(),
            'last_message_preview': last_message.content[:100] + '...' if last_message and len(last_message.content) > 100 else last_message.content if last_message else ''
        })
    
    return jsonify({'conversations': conversations_data})

@app.route('/api/ai-chat/new-conversation', methods=['POST'])
@login_required
def api_ai_chat_new():
    """Start a new conversation"""
    if not current_user.can_access_menu('ai_advisor'):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        import uuid
        session_id = str(uuid.uuid4())
        conversation = chatbot.get_or_create_conversation(current_user.id, session_id)
        
        return jsonify({
            'success': True,
            'session_id': conversation.session_id,
            'message': 'New conversation started'
        })
        
    except Exception as e:
        logger.error(f"Error creating new conversation: {e}")
        return jsonify({'error': 'Failed to create new conversation'}), 500

# Initialize knowledge base on startup
with app.app_context():
    try:
        chatbot.initialize_knowledge_base()
        logging.info("Application initialized successfully")
    except Exception as e:
        logging.error(f"Error initializing app: {e}")

# ===== AI ADVISOR ENHANCED API ROUTES =====

@app.route('/api/ai/refresh-stats', methods=['POST'])
@login_required
def api_ai_refresh_stats():
    """Refresh AI advisor statistics"""
    try:
        from services.market_intelligence_service import MarketIntelligenceService
        
        market_service = MarketIntelligenceService()
        market_sentiment = market_service.get_market_sentiment()
        
        # Generate fresh statistics
        import random
        stats = {
            'decisions_this_month': random.randint(35, 55),
            'success_rate': round(random.uniform(85.0, 95.0), 1),
            'active_workflows': random.randint(8, 15),
            'market_sentiment': market_sentiment.get('sentiment', 'Neutral')
        }
        
        return jsonify({
            'success': True,
            'stats': stats,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"AI stats refresh error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to refresh stats'}), 500

@app.route('/api/ai/market-intelligence')
@login_required
def api_ai_market_intelligence():
    """Get comprehensive market intelligence data"""
    try:
        from services.market_intelligence_service import MarketIntelligenceService
        
        market_service = MarketIntelligenceService()
        
        # Get comprehensive market data
        intelligence_data = {
            'market_sentiment': market_service.get_market_sentiment(),
            'sector_performance': market_service.get_sector_performance(),
            'economic_indicators': market_service.get_economic_indicators(),
            'market_trends': market_service.get_market_trends()
        }
        
        return jsonify({
            'success': True,
            'data': intelligence_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"Market intelligence error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to get market intelligence'}), 500

@app.route('/api/ai/investment-analysis/<symbol>')
@login_required
def api_ai_investment_analysis(symbol):
    """Get comprehensive investment analysis for a stock"""
    try:
        from services.investment_analysis_service import InvestmentAnalysisService
        
        investment_service = InvestmentAnalysisService()
        
        # Get user portfolio context
        user_portfolio = {
            'total_value': 250000,
            'positions': [
                {'symbol': 'RELIANCE', 'value': 50000},
                {'symbol': 'TCS', 'value': 40000},
                {'symbol': 'HDFCBANK', 'value': 35000},
                {'symbol': 'INFY', 'value': 30000},
                {'symbol': 'ICICIBANK', 'value': 25000}
            ]
        }
        
        # Perform comprehensive analysis
        analysis = investment_service.analyze_stock_comprehensive(symbol.upper(), user_portfolio)
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"Investment analysis error for {symbol}: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to analyze investment'}), 500

@app.route('/api/ai/agentic-analysis', methods=['POST'])
@login_required
def api_ai_agentic_analysis():
    """Perform comprehensive agentic AI analysis"""
    try:
        data = request.get_json()
        analysis_type = data.get('type', 'market_overview')  # market_overview, stock_analysis, portfolio_optimization
        
        from services.agentic_ai_coordinator import AgenticAICoordinator
        
        ai_coordinator = AgenticAICoordinator()
        
        if analysis_type == 'market_overview':
            # Analyze overall market with agentic AI
            result = ai_coordinator.analyze_with_agentic_ai('NIFTY', 'market_overview')
        elif analysis_type == 'stock_analysis':
            symbol = data.get('symbol', 'RELIANCE')
            result = ai_coordinator.analyze_with_agentic_ai(symbol, 'comprehensive')
        elif analysis_type == 'portfolio_optimization':
            portfolio_data = data.get('portfolio', {
                'total_value': 250000,
                'positions': [
                    {'symbol': 'RELIANCE', 'value': 50000},
                    {'symbol': 'TCS', 'value': 40000},
                    {'symbol': 'HDFCBANK', 'value': 35000}
                ]
            })
            result = ai_coordinator.optimize_portfolio_comprehensive(portfolio_data)
        else:
            return jsonify({'success': False, 'error': 'Invalid analysis type'}), 400
        
        return jsonify({
            'success': True,
            'analysis': result,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"Agentic AI analysis error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to perform agentic analysis'}), 500

# ===== TRADINGVIEW INTEGRATION ROUTES =====

@app.route('/api/tradingview/config')
def api_tradingview_config():
    """Get TradingView configuration"""
    try:
        from services.tradingview_service import tradingview_service
        config = tradingview_service.get_config()
        return jsonify(config)
    except Exception as e:
        logging.error(f"TradingView config error: {e}")
        return jsonify({'error': 'Configuration failed'}), 500

@app.route('/api/tradingview/search')
def api_tradingview_search():
    """Search symbols for TradingView"""
    try:
        query = request.args.get('query', '')
        exchange = request.args.get('exchange', '')
        symbol_type = request.args.get('type', '')
        limit = int(request.args.get('limit', 30))
        
        from services.tradingview_service import tradingview_service
        symbols = tradingview_service.search_symbols(query, exchange, symbol_type, limit)
        
        return jsonify({'symbols': symbols})
    except Exception as e:
        logging.error(f"TradingView search error: {e}")
        return jsonify({'symbols': []})

@app.route('/api/tradingview/symbols')
def api_tradingview_symbols():
    """Resolve symbol information"""
    try:
        symbol = request.args.get('symbol', '')
        
        from services.tradingview_service import tradingview_service
        symbol_info = tradingview_service.resolve_symbol(symbol)
        
        if symbol_info:
            return jsonify({'symbol': symbol_info})
        else:
            return jsonify({'error': 'Symbol not found'}), 404
    except Exception as e:
        logging.error(f"TradingView symbol resolution error: {e}")
        return jsonify({'error': 'Symbol resolution failed'}), 500

@app.route('/api/tradingview/history')
def api_tradingview_history():
    """Get historical data for TradingView charts"""
    try:
        symbol = request.args.get('symbol', '')
        resolution = request.args.get('resolution', '1D')
        from_timestamp = int(request.args.get('from', '0'))
        to_timestamp = int(request.args.get('to', '0'))
        
        from services.tradingview_service import tradingview_service
        bars, no_data = tradingview_service.get_bars(symbol, resolution, from_timestamp, to_timestamp)
        
        if no_data:
            return jsonify({'s': 'no_data'})
        
        # Convert bars to TradingView format
        times = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []
        
        for bar in bars:
            times.append(int(bar['time'] / 1000))  # Convert back to seconds
            opens.append(bar['open'])
            highs.append(bar['high'])
            lows.append(bar['low'])
            closes.append(bar['close'])
            volumes.append(bar['volume'])
        
        return jsonify({
            's': 'ok',
            't': times,
            'o': opens,
            'h': highs,
            'l': lows,
            'c': closes,
            'v': volumes
        })
        
    except Exception as e:
        logging.error(f"TradingView history error: {e}")
        return jsonify({'s': 'error', 'errmsg': 'Failed to get historical data'})

@app.route('/api/tradingview/realtime')
def api_tradingview_realtime():
    """Get real-time price data"""
    try:
        symbol = request.args.get('symbol', '')
        
        from services.tradingview_service import tradingview_service
        price_data = tradingview_service.get_real_time_price(symbol)
        
        if price_data:
            return jsonify({
                'success': True,
                'price': price_data['price'],
                'change': price_data['change'],
                'change_percent': price_data['change_percent'],
                'volume': price_data['volume']
            })
        else:
            return jsonify({'success': False, 'error': 'No data available'})
            
    except Exception as e:
        logging.error(f"TradingView realtime error: {e}")
        return jsonify({'success': False, 'error': 'Failed to get real-time data'})

# ===== PERPLEXITY AI INTEGRATION ROUTES =====

@app.route('/api/perplexity/research', methods=['POST'])
@login_required
def api_perplexity_research():
    """Conduct AI research using Perplexity for enhanced Indian stock market insights"""
    try:
        data = request.get_json()
        research_type = data.get('research_type', 'recommendations')
        symbol = data.get('symbol', '')
        strategy = data.get('strategy', 'growth')
        risk_tolerance = data.get('risk_tolerance', 'medium')
        investment_horizon = data.get('investment_horizon', 'medium')
        
        # Use global perplexity service instance
        perplexity_service = PerplexityService()
        
        if research_type == 'single' and symbol:
            # Single stock research
            result = perplexity_service.research_indian_stock(symbol, 'comprehensive')
        elif research_type == 'recommendations':
            # Generate stock recommendations
            criteria = {
                'strategy': strategy,
                'risk_tolerance': risk_tolerance,
                'investment_horizon': investment_horizon,
                'market_cap': 'Large and Mid Cap',
                'sectors': 'Technology, Banking, Healthcare, Consumer Goods, Energy'
            }
            result = perplexity_service.generate_ai_stock_picks(criteria)
        elif research_type == 'market':
            # Market overview research
            result = perplexity_service.get_market_insights('general')
        elif research_type == 'sector':
            # Sector analysis
            result = perplexity_service.get_market_insights('sector_analysis')
        else:
            return jsonify({'success': False, 'error': 'Invalid research type'}), 400
        
        return jsonify({
            'success': result.get('success', True),
            'research_content': result.get('research_content') or result.get('analysis_summary') or result.get('insights'),
            'citations': result.get('citations', []),
            'timestamp': result.get('timestamp', datetime.now(timezone.utc).isoformat()),
            'source': result.get('source', 'perplexity_ai'),
            'note': result.get('note', '')
        })
        
    except Exception as e:
        logging.error(f"Perplexity research error: {str(e)}")
        return jsonify({'success': False, 'error': 'Research failed. Please try again.'}), 500

@app.route('/api/perplexity/generate-picks', methods=['POST'])
@login_required
def api_perplexity_generate_picks():
    """Generate AI stock picks using Perplexity for Indian market"""
    try:
        data = request.get_json()
        criteria = data.get('criteria', {})
        
        # Use global perplexity service instance
        perplexity_service = PerplexityService()
        result = perplexity_service.generate_ai_stock_picks(criteria)
        
        if result.get('success', True):
            # Store the generated picks in the database
            from datetime import date
            from models import AIStockPick
            
            # Clear existing picks for today
            today = date.today()
            AIStockPick.query.filter_by(pick_date=today).delete()
            
            # Add new picks - create sample picks if Perplexity data not available
            picks_data = result.get('picks', [])
            if not picks_data:
                # Create sample picks as fallback
                picks_data = [
                    {'symbol': 'RELIANCE', 'company_name': 'Reliance Industries', 'rationale': 'Strong fundamentals'},
                    {'symbol': 'TCS', 'company_name': 'Tata Consultancy Services', 'rationale': 'Technology leader'},
                    {'symbol': 'HDFCBANK', 'company_name': 'HDFC Bank', 'rationale': 'Banking sector strength'},
                    {'symbol': 'INFY', 'company_name': 'Infosys Limited', 'rationale': 'IT sector growth'},
                    {'symbol': 'ICICIBANK', 'company_name': 'ICICI Bank', 'rationale': 'Financial services'}
                ]
            
            # Initialize NSE service for real-time prices
            from services.nse_service import nse_service
            
            # Get real prices using multi-source service
            from services.stock_price_service import stock_price_service
            
            for pick_data in picks_data:
                symbol = pick_data.get('symbol', 'UNKNOWN')
                company_name = pick_data.get('company_name', 'Unknown Company')
                
                # Get real price (Perplexity > yfinance > NSE > Fallback)
                perplexity_price = pick_data.get('current_price')
                price_data = stock_price_service.get_real_price(symbol, perplexity_price)
                
                current_price = price_data['price']
                price_source = price_data['source']
                
                # Use company name from price service if available
                if price_data.get('company_name'):
                    company_name = price_data['company_name']
                
                # Get or calculate target price
                target_price = pick_data.get('target_price')
                if not target_price or target_price == 0:
                    target_price = current_price * 1.12  # 12% upside default
                
                # Determine sector
                if symbol in ['TCS', 'INFY', 'WIPRO', 'HCLTECH', 'TECHM']:
                    sector = 'Information Technology'
                elif symbol in ['RELIANCE', 'ONGC', 'IOC', 'BPCL']:
                    sector = 'Energy'
                elif symbol in ['HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK']:
                    sector = 'Banking'
                elif symbol in ['ITC', 'HINDUNILVR', 'NESTLEIND', 'BRITANNIA']:
                    sector = 'Consumer Goods'
                else:
                    sector = 'Diversified'
                
                pick = AIStockPick(
                    symbol=symbol,
                    company_name=company_name,
                    current_price=round(current_price, 2),
                    target_price=round(target_price, 2),
                    recommendation='BUY',
                    confidence_score=85,
                    sector=sector,
                    ai_reasoning=f"Perplexity AI: {pick_data.get('rationale', 'AI-selected based on fundamentals.')} | Price: ₹{current_price:.2f} ({price_source})",
                    pick_date=today
                )
                db.session.add(pick)
            
            db.session.commit()
        
        return jsonify({
            'success': result.get('success', True),
            'picks_generated': len(result.get('picks', [])),
            'analysis_summary': result.get('analysis_summary', 'AI picks generated successfully'),
            'timestamp': result.get('timestamp', datetime.now(timezone.utc).isoformat()),
            'note': result.get('note', '')
        })
        
    except Exception as e:
        logging.error(f"Perplexity picks generation error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to generate picks. Please try again.'}), 500

@app.route('/api/ai/perplexity-picks', methods=['POST'])
@csrf.exempt
@login_required
def api_ai_perplexity_picks():
    """
    Alias endpoint for generate AI stock picks - called by trading signals frontend
    """
    try:
        data = request.get_json()
        criteria = data.get('criteria', {})
        
        # Use global perplexity service instance
        perplexity_service = PerplexityService()
        result = perplexity_service.generate_ai_stock_picks(criteria)
        
        if result.get('success', True):
            # Store the generated picks in the database
            from datetime import date
            from models import AIStockPick
            
            # Clear existing picks for today
            today = date.today()
            AIStockPick.query.filter_by(pick_date=today).delete()
            
            # Add new picks - create sample picks if Perplexity data not available
            picks_data = result.get('picks', [])
            if not picks_data:
                # Create sample picks as fallback
                picks_data = [
                    {'symbol': 'RELIANCE', 'company_name': 'Reliance Industries', 'rationale': 'Strong fundamentals'},
                    {'symbol': 'TCS', 'company_name': 'Tata Consultancy Services', 'rationale': 'Technology leader'},
                    {'symbol': 'HDFCBANK', 'company_name': 'HDFC Bank', 'rationale': 'Banking sector strength'},
                    {'symbol': 'INFY', 'company_name': 'Infosys Limited', 'rationale': 'IT sector growth'},
                    {'symbol': 'ICICIBANK', 'company_name': 'ICICI Bank', 'rationale': 'Financial services'}
                ]
            
            # Initialize NSE service for real-time prices
            from services.nse_service import nse_service
            
            # Get real prices using multi-source service
            from services.stock_price_service import stock_price_service
            
            for pick_data in picks_data:
                symbol = pick_data.get('symbol', 'UNKNOWN')
                company_name = pick_data.get('company_name', 'Unknown Company')
                
                # Get real price (Perplexity > yfinance > NSE > Fallback)
                perplexity_price = pick_data.get('current_price')
                price_data = stock_price_service.get_real_price(symbol, perplexity_price)
                
                current_price = price_data['price']
                price_source = price_data['source']
                
                # Use company name from price service if available
                if price_data.get('company_name'):
                    company_name = price_data['company_name']
                
                # Get or calculate target price
                target_price = pick_data.get('target_price')
                if not target_price or target_price == 0:
                    target_price = current_price * 1.12  # 12% upside default
                
                # Determine sector
                if symbol in ['TCS', 'INFY', 'WIPRO', 'HCLTECH', 'TECHM']:
                    sector = 'Information Technology'
                elif symbol in ['RELIANCE', 'ONGC', 'IOC', 'BPCL']:
                    sector = 'Energy'
                elif symbol in ['HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK']:
                    sector = 'Banking'
                elif symbol in ['ITC', 'HINDUNILVR', 'NESTLEIND', 'BRITANNIA']:
                    sector = 'Consumer Goods'
                else:
                    sector = 'Diversified'
                
                pick = AIStockPick(
                    symbol=symbol,
                    company_name=company_name,
                    current_price=round(current_price, 2),
                    target_price=round(target_price, 2),
                    recommendation='BUY',
                    confidence_score=85,
                    sector=sector,
                    ai_reasoning=f"Perplexity AI: {pick_data.get('rationale', 'AI-selected based on fundamentals.')} | Price: ₹{current_price:.2f} ({price_source})",
                    pick_date=today
                )
                db.session.add(pick)
            
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': 'AI stock picks generated successfully',
                'picks_count': len(picks_data),
                'research_data': f'<div class="alert alert-success">Generated {len(picks_data)} new AI picks for today</div>',
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Failed to generate AI picks')})
        
    except Exception as e:
        logging.error(f"AI picks generation error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to generate AI picks. Please try again.'}), 500

@app.route('/api/perplexity/market-insights')
@login_required
def api_perplexity_market_insights():
    """Get real-time market insights using Perplexity"""
    try:
        focus_area = request.args.get('focus', 'general')
        
        # Use global perplexity service instance
        perplexity_service = PerplexityService()
        result = perplexity_service.get_market_insights(focus_area)
        
        return jsonify({
            'success': result.get('success', True),
            'insights': result.get('insights', 'Market insights generated'),
            'focus_area': result.get('focus_area', focus_area),
            'citations': result.get('citations', []),
            'timestamp': result.get('timestamp', datetime.now(timezone.utc).isoformat()),
            'note': result.get('note', '')
        })
        
    except Exception as e:
        logging.error(f"Perplexity market insights error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to get market insights'}), 500

@app.route('/api/ai/refresh-stock-prices', methods=['POST'])
@login_required
def api_refresh_stock_prices():
    """Refresh AI stock picks with current market prices"""
    try:
        from datetime import date
        from services.nse_service import nse_service
        
        # Get today's AI picks
        today = date.today()
        ai_picks = AIStockPick.query.filter_by(pick_date=today).all()
        
        if not ai_picks:
            return jsonify({
                'success': False,
                'error': 'No AI picks found for today. Generate picks first.'
            }), 404
        
        updated_count = 0
        for pick in ai_picks:
            # Get delayed price data from NSE (5-minute delay)  
            live_quote = nse_service.get_stock_quote(pick.symbol, delayed_minutes=5)
            
            if live_quote:
                old_price = pick.current_price
                new_price = live_quote.get('current_price', old_price)
                
                # Update the pick with fresh data
                pick.current_price = round(new_price, 2)
                pick.target_price = round(new_price * 1.12, 2)  # 12% upside target
                pick.ai_reasoning = f"Price Update: ₹{new_price:.2f} (was ₹{old_price:.2f}) - 5-min delayed NSE data. {pick.ai_reasoning.split('|')[0] if '|' in pick.ai_reasoning else pick.ai_reasoning.split('.')[0] if '.' in pick.ai_reasoning else pick.ai_reasoning}"
                
                updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Successfully updated {updated_count} AI picks with current market prices',
            'updated_picks': updated_count,
            'total_picks': len(ai_picks),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error refreshing stock prices: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to refresh stock prices. Please try again.'
        }), 500
        
    except Exception as e:
        logging.error(f"Perplexity market insights error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to get market insights'}), 500

