"""
Separate Admin Module for Capulse
Provides independent admin interface for managing trading signals, users, and payments
"""
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timedelta, timezone
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app for admin
admin_app = Flask(__name__, template_folder='templates/admin')
admin_app.secret_key = os.environ.get("SESSION_SECRET")
admin_app.wsgi_app = ProxyFix(admin_app.wsgi_app, x_proto=1, x_host=1)

# Database configuration - use same database as main app
admin_app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
admin_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
admin_app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    'pool_pre_ping': True,
    "pool_recycle": 300,
}

# Import models from main app
from db_instance import db
from models import User, TradingSignal, BrokerAccount, ExecutedTrade
from models_broker import BrokerHolding, BrokerPosition, BrokerOrder
from models.payment_models import PaymentOrder, Subscription

# Initialize database with admin app
db.init_app(admin_app)

# Admin authentication
ADMIN_CREDENTIALS = {
    'admin': os.environ.get('ADMIN_PASSWORD', 'admin123'),  # Change in production
    'tcapital_admin': os.environ.get('ADMIN_PASSWORD', 'tcapital2025')
}

def admin_required(f):
    """Decorator to require admin authentication"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@admin_app.route('/admin')
def admin_login():
    """Admin login page"""
    return render_template('admin_login.html')

@admin_app.route('/admin/auth', methods=['POST'])
def admin_authenticate():
    """Process admin login"""
    username = request.form.get('username')
    password = request.form.get('password')
    
    if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
        session['admin_logged_in'] = True
        session['admin_username'] = username
        flash('Successfully logged in as admin', 'success')
        return redirect(url_for('admin_dashboard'))
    else:
        flash('Invalid admin credentials', 'error')
        return redirect(url_for('admin_login'))

@admin_app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('admin_login'))

@admin_app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard with overview"""
    # Get summary statistics
    total_users = User.query.count()
    active_signals = TradingSignal.query.filter(TradingSignal.expires_at > datetime.now(timezone.utc)).count()
    total_payments = PaymentOrder.query.filter_by(status='completed').count()
    connected_brokers = BrokerAccount.query.filter_by(is_connected=True).count()
    
    # Recent activity
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_signals = TradingSignal.query.order_by(TradingSignal.created_at.desc()).limit(5).all()
    recent_payments = PaymentOrder.query.filter_by(status='completed').order_by(PaymentOrder.created_at.desc()).limit(5).all()
    
    return render_template('admin_dashboard.html',
                         total_users=total_users,
                         active_signals=active_signals,
                         total_payments=total_payments,
                         connected_brokers=connected_brokers,
                         recent_users=recent_users,
                         recent_signals=recent_signals,
                         recent_payments=recent_payments)

@admin_app.route('/admin/users')
@admin_required
def admin_users():
    """User management page"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    users = User.query.paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('admin_users.html', users=users)

@admin_app.route('/admin/trading-signals')
@admin_required
def admin_trading_signals():
    """Trading signals management page"""
    signals = TradingSignal.query.order_by(TradingSignal.created_at.desc()).all()
    return render_template('admin_trading_signals.html', signals=signals)

@admin_app.route('/admin/trading-signals/add', methods=['GET', 'POST'])
@admin_required
def admin_add_signal():
    """Add new trading signal"""
    if request.method == 'POST':
        try:
            signal = TradingSignal(
                symbol=request.form.get('symbol').upper(),
                company_name=request.form.get('company_name'),
                signal_type=request.form.get('signal_type'),
                action=request.form.get('action'),
                entry_price=float(request.form.get('entry_price')) if request.form.get('entry_price') else None,
                target_price=float(request.form.get('target_price')) if request.form.get('target_price') else None,
                stop_loss=float(request.form.get('stop_loss')) if request.form.get('stop_loss') else None,
                quantity=int(request.form.get('quantity')) if request.form.get('quantity') else None,
                time_frame=request.form.get('time_frame'),
                risk_level=request.form.get('risk_level'),
                strategy_name=request.form.get('strategy_name'),
                notes=request.form.get('notes'),
                expires_at=datetime.strptime(request.form.get('expires_at'), '%Y-%m-%d') if request.form.get('expires_at') else None,
                created_by='admin',
                is_active=True
            )
            
            # Calculate potential return
            if signal.entry_price and signal.target_price:
                if signal.action == 'BUY':
                    signal.potential_return = ((signal.target_price - signal.entry_price) / signal.entry_price) * 100
                elif signal.action == 'SELL':
                    signal.potential_return = ((signal.entry_price - signal.target_price) / signal.entry_price) * 100
            
            db.session.add(signal)
            db.session.commit()
            
            # Send to WhatsApp and Telegram (implement later)
            send_signal_to_messaging(signal)
            
            flash('Trading signal added successfully', 'success')
            return redirect(url_for('admin_trading_signals'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding signal: {str(e)}', 'error')
            logging.error(f"Error adding trading signal: {e}")
    
    return render_template('admin_add_signal.html')

@admin_app.route('/admin/trading-signals/delete/<int:signal_id>', methods=['POST'])
@admin_required
def admin_delete_signal(signal_id):
    """Delete trading signal"""
    signal = TradingSignal.query.get_or_404(signal_id)
    db.session.delete(signal)
    db.session.commit()
    flash('Trading signal deleted successfully', 'success')
    return redirect(url_for('admin_trading_signals'))

@admin_app.route('/admin/payments')
@admin_required
def admin_payments():
    """Payment management page"""
    view_type = request.args.get('view', 'daily')
    
    if view_type == 'daily':
        start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
    elif view_type == 'weekly':
        start_date = datetime.now(timezone.utc) - timedelta(days=7)
        end_date = datetime.now(timezone.utc)
    elif view_type == 'monthly':
        start_date = datetime.now(timezone.utc) - timedelta(days=30)
        end_date = datetime.now(timezone.utc)
    
    payments = PaymentOrder.query.filter(
        PaymentOrder.created_at >= start_date,
        PaymentOrder.created_at < end_date,
        PaymentOrder.status == 'completed'
    ).order_by(PaymentOrder.created_at.desc()).all()
    
    total_revenue = sum(p.amount for p in payments)
    
    return render_template('admin_payments.html', 
                         payments=payments, 
                         view_type=view_type,
                         total_revenue=total_revenue,
                         start_date=start_date,
                         end_date=end_date)

@admin_app.route('/admin/account-handling')
@admin_required
def admin_account_handling():
    """Account handling for premium users"""
    premium_users = User.query.filter(
        User.pricing_plan.in_(['target_pro', 'hni'])
    ).all()
    
    # Get trading activity for premium users
    user_activities = []
    for user in premium_users:
        executed_trades = ExecutedTrade.query.filter_by(user_id=user.id).count()
        broker_accounts = BrokerAccount.query.filter_by(user_id=user.id).count()
        
        # Calculate P&L (simplified)
        total_pnl = 0
        user_trades = ExecutedTrade.query.filter_by(user_id=user.id).all()
        for trade in user_trades:
            if hasattr(trade, 'pnl') and trade.pnl:
                total_pnl += trade.pnl
        
        user_activities.append({
            'user': user,
            'executed_trades': executed_trades,
            'broker_accounts': broker_accounts,
            'total_pnl': total_pnl
        })
    
    return render_template('admin_account_handling.html', user_activities=user_activities)

def send_signal_to_messaging(signal):
    """Send trading signal to WhatsApp and Telegram groups"""
    try:
        # Format signal message
        message = f"""
🚨 NEW TRADING SIGNAL 🚨

Symbol: {signal.symbol}
Action: {signal.action}
Type: {signal.signal_type.title()}

💰 Entry: ₹{signal.entry_price}
🎯 Target: ₹{signal.target_price}
🛑 Stop Loss: ₹{signal.stop_loss}

Strategy: {signal.strategy_name}
Risk Level: {signal.risk_level}

⚠️ Trade at your own risk. This is for educational purposes only.

- Capulse Team
        """
        
        # Import messaging services
        try:
            from services.messaging_service import send_whatsapp_message, send_telegram_message
            
            # Send to WhatsApp group
            send_whatsapp_message(message)
            
            # Send to Telegram group
            send_telegram_message(message)
            
            logging.info(f"Trading signal sent to messaging groups: {signal.symbol}")
            
        except ImportError:
            logging.warning("Messaging services not available")
        except Exception as e:
            logging.error(f"Error sending signal to messaging: {e}")
            
    except Exception as e:
        logging.error(f"Error in send_signal_to_messaging: {e}")

if __name__ == '__main__':
    with admin_app.app_context():
        db.create_all()
    admin_app.run(host='0.0.0.0', port=5001, debug=True)