"""
Broker Routes - Handle broker connections, portfolio sync, and trading
"""

from flask import request, jsonify, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from app import app, db, csrf
from models_broker import (
    BrokerAccount, BrokerHolding, BrokerPosition, BrokerOrder,
    BrokerType, ConnectionStatus, OrderStatus, TransactionType,
    ProductType, OrderType
)
from services.broker_service import BrokerService, BrokerAPIError
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Broker Catalog - All supported brokers
BROKER_CATALOG = [
    {
        'type': BrokerType.DHAN,
        'name': 'Dhan',
        'logo': 'https://dhan.co/logo.png',
        'status': 'active',
        'description': 'Low brokerage with advanced trading tools',
        'fields': ['client_id', 'access_token']
    },
    {
        'type': BrokerType.ZERODHA,
        'name': 'Zerodha',
        'logo': 'https://zerodha.com/static/images/logo.svg',
        'status': 'active',
        'description': 'India\'s largest broker by active clients',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.UPSTOX,
        'name': 'Upstox',
        'logo': 'https://upstox.com/logo.png',
        'status': 'active',
        'description': 'Technology-driven discount broker',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.ANGEL_BROKING,
        'name': 'Angel One',
        'logo': 'https://angelone.in/logo.png',
        'status': 'active',
        'description': 'Full-service broker with research',
        'fields': ['client_id', 'access_token', 'totp_secret']
    },
    {
        'type': BrokerType.GROWW,
        'name': 'Groww',
        'logo': 'https://groww.in/logo.png',
        'status': 'coming_soon',
        'description': 'Simple and intuitive trading platform',
        'fields': ['client_id', 'access_token']
    },
    {
        'type': BrokerType.FYERS,
        'name': 'Fyers',
        'logo': 'https://fyers.in/logo.png',
        'status': 'coming_soon',
        'description': 'Advanced charting and trading tools',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.ICICIDIRECT,
        'name': 'ICICI Direct',
        'logo': 'https://icicidirect.com/logo.png',
        'status': 'active',
        'description': 'Full-service broker with Breeze Connect',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.HDFC_SECURITIES,
        'name': 'HDFC Securities',
        'logo': 'https://hdfcsec.com/logo.png',
        'status': 'coming_soon',
        'description': 'Trusted full-service broker',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.KOTAK_SECURITIES,
        'name': 'Kotak Securities',
        'logo': 'https://kotaksecurities.com/logo.png',
        'status': 'coming_soon',
        'description': 'Comprehensive trading platform',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.FIVE_PAISA,
        'name': '5paisa',
        'logo': 'https://5paisa.com/logo.png',
        'status': 'coming_soon',
        'description': 'Affordable brokerage plans',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.CHOICE_INDIA,
        'name': 'Choice India',
        'logo': 'https://choiceindia.com/logo.png',
        'status': 'coming_soon',
        'description': 'Full-service broking house',
        'fields': ['client_id', 'access_token', 'api_secret']
    },
    {
        'type': BrokerType.GOODWILL,
        'name': 'Goodwill',
        'logo': 'https://goodwill.in/logo.png',
        'status': 'coming_soon',
        'description': 'South India based broker',
        'fields': ['client_id', 'access_token', 'api_secret']
    }
]

# Main broker routes - integrated into existing dashboard pages

@app.route('/dashboard/broker-accounts')
@login_required
def dashboard_broker_accounts():
    """Broker accounts management page - Available to all users"""
    
    # Get user's existing broker accounts
    user_brokers = BrokerAccount.query.filter_by(user_id=current_user.id).all()
    
    # Create a mapping of broker_type to account for quick lookup
    broker_accounts_map = {acc.broker_type: acc for acc in user_brokers}
    
    # Enrich broker catalog with user's account data
    enriched_catalog = []
    for broker in BROKER_CATALOG:
        broker_data = broker.copy()
        broker_type_value = broker['type'].value
        
        # Add user's account if exists
        if broker_type_value in broker_accounts_map:
            broker_data['account'] = broker_accounts_map[broker_type_value]
        else:
            broker_data['account'] = None
            
        enriched_catalog.append(broker_data)
    
    return render_template('dashboard/broker_accounts.html',
                         broker_catalog=enriched_catalog,
                         broker_types=BrokerType)

@app.route('/api/broker/add-account', methods=['POST'])
@login_required
def api_add_broker_account():
    """Add new broker account"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['broker_type', 'client_id', 'access_token']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'Missing {field}'}), 400
        
        # Check if broker type is valid
        try:
            broker_type = BrokerType(data['broker_type'])
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid broker type'}), 400
        
        # Prepare credentials based on broker type
        credentials = {
            'client_id': data['client_id'],
            'access_token': data['access_token'],
            'api_secret': data.get('api_secret'),
            'totp_secret': data.get('totp_secret')
        }
        
        # Check if user already has an account with this broker
        existing_account = BrokerAccount.query.filter_by(
            user_id=current_user.id, 
            broker_type=broker_type.value
        ).first()
        
        if existing_account:
            return jsonify({
                'success': False,
                'message': 'User can create only connection with each broker'
            }), 400
        
        # Check broker limits based on pricing plan
        from models import PricingPlan
        existing_brokers_count = BrokerAccount.query.filter_by(user_id=current_user.id).count()
        
        # All plans now allow only one broker connection
        if existing_brokers_count >= 1:
            plan_name = current_user.get_plan_display_name()
            return jsonify({
                'success': False,
                'message': f'{plan_name} plan allows only one broker connection.'
            }), 400
        
        # Step 1: Save broker credentials (DO NOT CONNECT YET)
        # Create broker account in 'disconnected' state
        broker_account = BrokerAccount(
            user_id=current_user.id,
            broker_type=broker_type.value,
            broker_name=broker_type.value.title(),
            connection_status='disconnected',  # Saved but not connected
            is_primary=False,
            is_active=True
        )
        
        # Securely store encrypted credentials
        broker_account.set_credentials(
            client_id=credentials['client_id'],
            access_token=credentials['access_token'],
            api_secret=credentials.get('api_secret'),
            totp_secret=credentials.get('totp_secret')
        )
        
        db.session.add(broker_account)
        db.session.commit()
        
        logger.info(f"User {current_user.id} added broker {broker_type.value} (Step 1: Credentials saved)")
        
        return jsonify({
            'success': True,
            'message': f'{broker_type.value.title()} credentials saved successfully! Now click "Connect" to activate the broker.',
            'account_id': broker_account.id
        })
            
    except BrokerAPIError as e:
        logger.error(f"Broker API error in add_broker_account: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding broker account: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Failed to add broker. Please try again.'}), 500

@app.route('/api/broker/<int:account_id>/connect', methods=['POST'])
@login_required
def api_connect_broker(account_id):
    """Connect a broker (Step 2: Activate broker for trading)"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        if broker_account.connection_status == 'connected':
            return jsonify({'success': False, 'message': 'Broker is already connected'}), 400
        
        # Update connection status to connected
        broker_account.connection_status = 'connected'
        broker_account.last_connected = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f"User {current_user.id} connected broker {broker_account.broker_name} (ID: {account_id})")
        
        return jsonify({
            'success': True,
            'message': f'{broker_account.broker_name} connected successfully! You can now use it for trading.'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error connecting broker account: {e}")
        return jsonify({'success': False, 'message': 'Failed to connect broker'}), 500

@app.route('/api/broker/<int:account_id>/disconnect', methods=['POST'])
@login_required
def api_disconnect_broker(account_id):
    """Disconnect a broker (deactivate but keep credentials)"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        if broker_account.connection_status == 'disconnected':
            return jsonify({'success': False, 'message': 'Broker is already disconnected'}), 400
        
        # If this was the primary broker, unset it
        if broker_account.is_primary:
            broker_account.is_primary = False
        
        # Update connection status to disconnected (credentials preserved)
        broker_account.connection_status = 'disconnected'
        
        db.session.commit()
        
        logger.info(f"User {current_user.id} disconnected broker {broker_account.broker_name} (ID: {account_id})")
        
        return jsonify({
            'success': True,
            'message': f'{broker_account.broker_name} disconnected successfully. Credentials are saved for quick reconnection.'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error disconnecting broker account: {e}")
        return jsonify({'success': False, 'message': 'Failed to disconnect broker'}), 500

@app.route('/api/broker/sync-account/<int:account_id>', methods=['POST'])
@login_required
def api_sync_broker_account(account_id):
    """Sync broker account data"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        # Check if broker is connected
        if broker_account.connection_status != 'connected':
            return jsonify({
                'success': False, 
                'message': 'Please connect the broker first before syncing'
            }), 400
        
        # Get sync types from request
        data = request.get_json() or {}
        sync_types = data.get('sync_types', ['holdings', 'positions', 'orders'])
        
        logger.info(f"Starting sync for broker {broker_account.broker_name} (ID: {account_id})")
        
        # Perform sync
        sync_results = BrokerService.sync_broker_data(broker_account, sync_types)
        
        logger.info(f"Sync completed for broker {broker_account.broker_name}: {sync_results}")
        
        return jsonify({
            'success': True,
            'message': 'Account synced successfully',
            'sync_results': sync_results
        })
        
    except BrokerAPIError as e:
        logger.error(f"BrokerAPIError during sync: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error syncing broker account: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': f'Sync failed: {str(e)}'}), 500

@app.route('/api/broker/remove-account/<int:account_id>', methods=['DELETE'])
@login_required
def api_remove_broker_account(account_id):
    """Remove broker account"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        broker_name = broker_account.broker_name
        
        # Delete account and all related data (cascade delete)
        db.session.delete(broker_account)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'{broker_name} account removed successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing broker account: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/broker/set-primary/<int:account_id>', methods=['POST'])
@login_required
def api_set_primary_broker(account_id):
    """Set primary broker account"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        broker_account.set_as_primary()
        
        return jsonify({
            'success': True,
            'message': f'{broker_account.broker_name} set as primary broker'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error setting primary broker: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/dashboard/live-portfolio')
@login_required
def dashboard_live_portfolio():
    """Live portfolio with real broker data"""
    # Check subscription access
    from models import PricingPlan
    if current_user.pricing_plan not in [PricingPlan.TARGET_PLUS, PricingPlan.TARGET_PRO, PricingPlan.HNI]:
        flash('Live portfolio access requires Target Plus subscription or higher.', 'warning')
        return redirect(url_for('pricing'))
    
    # Get portfolio summary with error handling
    try:
        from services.broker_service_helpers import get_portfolio_summary
        portfolio_summary = get_portfolio_summary(current_user.id)
    except:
        portfolio_summary = {
            'total_value': 0,
            'total_pnl': 0, 
            'holdings_count': 0,
            'brokers_count': 0,
            'broker_accounts': []
        }
    
    # Get all holdings across brokers
    broker_accounts = BrokerAccount.query.filter_by(user_id=current_user.id, is_active=True).all()
    all_holdings = []
    
    for account in broker_accounts:
        holdings = BrokerHolding.query.filter_by(broker_account_id=account.id).all()
        for holding in holdings:
            holding.broker_name = account.broker_name
            all_holdings.append(holding)
    
    # Sort by total value
    all_holdings.sort(key=lambda x: x.total_value, reverse=True)
    
    return render_template('dashboard/live_portfolio.html',
                         portfolio_summary=portfolio_summary,
                         holdings=all_holdings,
                         broker_accounts=broker_accounts)

@app.route('/api/broker/place-order', methods=['POST'])
@login_required
def api_place_broker_order():
    """Place order through broker"""
    try:
        # Check trading permissions based on pricing plan
        from models import PricingPlan
        
        if current_user.pricing_plan == PricingPlan.TARGET_PLUS:
            return jsonify({
                'success': False, 
                'message': 'Target Plus plan allows portfolio analysis only. Upgrade to Target Pro for trade execution.'
            }), 403
        
        if current_user.pricing_plan not in [PricingPlan.TARGET_PRO, PricingPlan.HNI]:
            return jsonify({
                'success': False, 
                'message': 'Trade execution requires Target Pro subscription or higher.'
            }), 403
        
        data = request.get_json()
        
        # Get broker account (use primary if not specified)
        account_id = data.get('broker_account_id')
        if account_id:
            broker_account = BrokerAccount.query.filter_by(
                id=account_id, 
                user_id=current_user.id
            ).first()
        else:
            broker_account = BrokerAccount.query.filter_by(
                user_id=current_user.id, 
                is_primary=True
            ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'No broker account found'}), 404
        
        # Ensure trading is only allowed with one broker (the primary one)
        if not broker_account.is_primary:
            return jsonify({
                'success': False, 
                'message': 'Trading is only allowed with your primary broker. Please set this broker as primary to trade.'
            }), 403
        
        # Validate order data
        required_fields = ['symbol', 'transaction_type', 'quantity', 'order_type', 'product_type']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'Missing {field}'}), 400
        
        # Prepare order data
        order_data = {
            'symbol': data['symbol'],
            'trading_symbol': data.get('trading_symbol', data['symbol']),
            'exchange': data.get('exchange', 'NSE'),
            'security_id': data.get('security_id'),
            'transaction_type': TransactionType(data['transaction_type']),
            'order_type': OrderType(data['order_type']),
            'product_type': ProductType(data['product_type']),
            'quantity': int(data['quantity']),
            'price': float(data.get('price', 0)),
            'trigger_price': float(data.get('trigger_price', 0)),
            'disclosed_quantity': int(data.get('disclosed_quantity', 0)),
            'correlation_id': data.get('correlation_id'),
            'trading_signal_id': data.get('trading_signal_id')
        }
        
        # Place order
        order = BrokerService.place_order_via_broker(broker_account, order_data)
        
        return jsonify({
            'success': True,
            'message': 'Order placed successfully',
            'order_id': order.id,
            'broker_order_id': order.broker_order_id
        })
        
    except BrokerAPIError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/broker/orders')
@login_required
def api_get_broker_orders():
    """Get user's broker orders"""
    try:
        # Get orders from all user's broker accounts
        broker_accounts = BrokerAccount.query.filter_by(user_id=current_user.id).all()
        account_ids = [account.id for account in broker_accounts]
        
        orders = BrokerOrder.query.filter(
            BrokerOrder.broker_account_id.in_(account_ids)
        ).order_by(BrokerOrder.order_time.desc()).limit(50).all()
        
        orders_data = []
        for order in orders:
            orders_data.append({
                'id': order.id,
                'broker_order_id': order.broker_order_id,
                'symbol': order.symbol,
                'transaction_type': order.transaction_type.value,
                'order_type': order.order_type.value,
                'product_type': order.product_type.value,
                'quantity': order.quantity,
                'filled_quantity': order.filled_quantity,
                'price': order.price,
                'order_status': order.order_status.value,
                'order_time': order.order_time.isoformat(),
                'broker_name': order.broker_account.broker_name,
                'avg_execution_price': order.avg_execution_price,
                'status_message': order.status_message
            })
        
        return jsonify({
            'success': True,
            'orders': orders_data
        })
        
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/dashboard/broker-trading')
@login_required
def dashboard_broker_trading():
    """Broker trading interface"""
    # Check subscription access
    from models import PricingPlan
    if current_user.pricing_plan not in [PricingPlan.TARGET_PRO, PricingPlan.HNI]:
        flash('Broker trading requires Target Pro or HNI subscription.', 'warning')
        return redirect(url_for('pricing'))
    
    broker_accounts = BrokerAccount.query.filter_by(
        user_id=current_user.id, 
        is_active=True,
        connection_status=ConnectionStatus.CONNECTED
    ).all()
    
    # Get recent orders
    if broker_accounts:
        account_ids = [account.id for account in broker_accounts]
        recent_orders = BrokerOrder.query.filter(
            BrokerOrder.broker_account_id.in_(account_ids)
        ).order_by(BrokerOrder.order_time.desc()).limit(10).all()
    else:
        recent_orders = []
    
    return render_template('dashboard/broker_trading.html',
                         broker_accounts=broker_accounts,
                         recent_orders=recent_orders)

@app.route('/api/broker/test-connection/<int:account_id>', methods=['POST'])
@login_required
def api_test_broker_connection(account_id):
    """Test broker connection"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        # Test connection
        client = BrokerService.get_broker_client(broker_account)
        if client.connect():
            return jsonify({
                'success': True,
                'message': 'Connection successful',
                'status': broker_account.connection_status.value,
                'last_connected': broker_account.last_connected.isoformat() if broker_account.last_connected else None
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Connection failed',
                'error': broker_account.connection_error
            })
            
    except Exception as e:
        logger.error(f"Error testing connection: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/broker/sync-all', methods=['POST'])
@login_required
def api_sync_all_brokers():
    """Sync all user's broker accounts"""
    try:
        broker_accounts = BrokerAccount.query.filter_by(
            user_id=current_user.id,
            is_active=True
        ).all()
        
        if not broker_accounts:
            return jsonify({'success': False, 'message': 'No broker accounts found'}), 404
        
        results = {}
        for account in broker_accounts:
            try:
                from services.broker_service_helpers import sync_broker_data
                sync_result = sync_broker_data(account)
                results[account.broker_name] = sync_result
            except Exception as e:
                results[account.broker_name] = {'error': str(e)}
        
        return jsonify({
            'success': True,
            'message': f'Synced {len(broker_accounts)} broker accounts',
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error syncing all brokers: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/broker/cancel-order/<order_id>', methods=['POST'])
@login_required
def api_cancel_broker_order(order_id):
    """Cancel order by broker order ID"""
    try:
        # Find the order
        order = BrokerOrder.query.join(BrokerAccount).filter(
            BrokerOrder.broker_order_id == order_id,
            BrokerAccount.user_id == current_user.id
        ).first()
        
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404
        
        # Get broker client and cancel order
        client = BrokerService.get_broker_client(order.broker_account)
        if not client.connect():
            return jsonify({'success': False, 'message': 'Failed to connect to broker'}), 400
        
        result = client.cancel_order(order_id)
        
        if result.get('status') == 'success':
            # Update order status in database
            order.update_status(OrderStatus.CANCELLED, 'Order cancelled by user')
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': 'Order cancelled successfully'
            })
        else:
            return jsonify({'success': False, 'message': result.get('message', 'Failed to cancel order')}), 400
            
    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500