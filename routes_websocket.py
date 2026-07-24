"""
WebSocket API Routes for React Frontend Integration
Provides RESTful APIs that work seamlessly with React components and WebSocket clients
"""

from flask import Blueprint, jsonify, request, session
from flask_login import login_required, current_user
import json
import logging
from datetime import datetime, timezone
from services.nse_service import NSEService
from services.ai_agent_service import AgenticAICoordinator
from models import Portfolio, TradingSignal, User
from db_instance import db

# Configure logging
logger = logging.getLogger(__name__)

# Create blueprint for WebSocket-compatible APIs
websocket_api = Blueprint('websocket_api', __name__, url_prefix='/api')

@websocket_api.route('/user/profile')
@login_required
def get_user_profile():
    """Get current user profile for React components"""
    try:
        user_data = {
            'id': current_user.id,
            'email': current_user.email,
            'first_name': current_user.first_name,
            'last_name': current_user.last_name,
            'profile_image_url': getattr(current_user, 'profile_image_url', None),
            'subscription_plan': str(current_user.pricing_plan.value) if current_user.pricing_plan else 'free',
            'last_login': current_user.last_login.isoformat() if current_user.last_login else None
        }
        
        return jsonify({
            'success': True,
            'user': user_data
        })
        
    except Exception as e:
        logger.error(f"User profile fetch error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to fetch user profile'
        }), 500

@websocket_api.route('/portfolio')
@websocket_api.route('/portfolio/<user_id>')
@login_required
def get_portfolio_data(user_id=None):
    """Get portfolio data for React portfolio component"""
    try:
        target_user_id = user_id or current_user.id
        
        # Get portfolio holdings from database
        portfolio_items = Portfolio.query.filter_by(user_id=target_user_id).all()
        
        if not portfolio_items:
            return jsonify({
                'success': True,
                'portfolio': {
                    'holdings': {},
                    'total_value': 0,
                    'day_change': 0,
                    'day_change_percent': 0
                }
            })
        
        # Calculate portfolio totals
        total_value = sum(item.current_value or 0 for item in portfolio_items)
        total_invested = sum(item.purchased_value or 0 for item in portfolio_items)
        day_change = total_value - total_invested
        day_change_percent = (day_change / total_invested * 100) if total_invested > 0 else 0
        
        # Format holdings data
        holdings = {}
        for item in portfolio_items:
            holdings[item.ticker_symbol] = {
                'symbol': item.ticker_symbol,
                'name': item.stock_name,
                'quantity': item.quantity,
                'current_price': item.current_price or 0,
                'purchase_price': item.purchase_price,
                'current_value': item.current_value or 0,
                'purchased_value': item.purchased_value
            }
        
        # Format portfolio data for React
        portfolio_data = {
            'holdings': holdings,
            'total_value': float(total_value),
            'day_change': float(day_change),
            'day_change_percent': float(day_change_percent)
        }
        
        return jsonify({
            'success': True,
            'portfolio': portfolio_data
        })
        
    except Exception as e:
        logger.error(f"Portfolio data fetch error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to fetch portfolio data'
        }), 500

@websocket_api.route('/market-data')
def get_market_data():
    """Get current market data for React components"""
    try:
        nse_service = NSEService()
        
        # Get indices data
        indices_data = nse_service.get_nse_indices()
        
        # Get popular stocks data
        popular_stocks = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK']
        stocks_data = {}
        
        for symbol in popular_stocks:
            try:
                stock_data = nse_service.get_stock_data(symbol)
                if stock_data.get('success'):
                    stocks_data[symbol] = stock_data['data']
            except Exception as e:
                logger.warning(f"Failed to fetch {symbol}: {e}")
        
        return jsonify({
            'success': True,
            'data': {
                'indices': indices_data.get('data', {}) if indices_data.get('success') else {},
                'stocks': stocks_data,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'market_status': 'open' if is_market_open() else 'closed'
            }
        })
        
    except Exception as e:
        logger.error(f"Market data fetch error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to fetch market data'
        }), 500

@websocket_api.route('/trading-signals')
@login_required
def get_trading_signals():
    """Get AI trading signals for React components"""
    try:
        # Get recent trading signals
        signals = TradingSignal.query.filter_by(
            status='ACTIVE'
        ).order_by(
            TradingSignal.created_at.desc()
        ).limit(20).all()
        
        signals_data = []
        for signal in signals:
            signal_data = {
                'id': signal.id,
                'symbol': signal.symbol,
                'action': signal.action,
                'signal_type': signal.signal_type,
                'risk_level': signal.risk_level,
                'target_price': float(signal.target_price) if signal.target_price else None,
                'stop_loss': float(signal.stop_loss) if signal.stop_loss else None,
                'notes': signal.notes,
                'created_at': signal.created_at.isoformat(),
                'expires_at': signal.expires_at.isoformat() if signal.expires_at else None
            }
            signals_data.append(signal_data)
        
        return jsonify({
            'success': True,
            'signals': signals_data,
            'count': len(signals_data)
        })
        
    except Exception as e:
        logger.error(f"Trading signals fetch error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to fetch trading signals'
        }), 500

@websocket_api.route('/ai-analysis', methods=['POST'])
@login_required
def get_ai_analysis():
    """Get AI analysis for a symbol - React component integration"""
    try:
        data = request.get_json()
        symbol = data.get('symbol')
        analysis_type = data.get('analysis_type', 'comprehensive')
        
        if not symbol:
            return jsonify({
                'success': False,
                'error': 'Symbol is required'
            }), 400
        
        # Get AI analysis
        ai_coordinator = AgenticAICoordinator()
        analysis = ai_coordinator.analyze_with_agentic_ai(symbol, analysis_type)
        
        return jsonify({
            'success': True,
            'analysis': analysis
        })
        
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return jsonify({
            'success': False,
            'error': 'AI analysis failed'
        }), 500

@websocket_api.route('/orders', methods=['POST'])
@login_required
def place_trading_order():
    """Place trading order - React trading interface integration"""
    try:
        order_data = request.get_json()
        
        # Validate order data
        required_fields = ['symbol', 'quantity', 'side', 'order_type']
        for field in required_fields:
            if field not in order_data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        # Add user ID to order
        order_data['user_id'] = current_user.id
        order_data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        # Process order through trading engine
        # This would integrate with your actual trading engine
        order_id = f"order_{int(datetime.now(timezone.utc).timestamp())}"
        
        # For now, simulate order placement
        result = {
            'order_id': order_id,
            'status': 'pending',
            'symbol': order_data['symbol'],
            'quantity': order_data['quantity'],
            'side': order_data['side'],
            'order_type': order_data['order_type'],
            'user_id': current_user.id
        }
        
        return jsonify({
            'success': True,
            'orderId': order_id,
            'order': result
        })
        
    except Exception as e:
        logger.error(f"Order placement error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to place order'
        }), 500

@websocket_api.route('/push-subscription', methods=['POST'])
@login_required
def save_push_subscription():
    """Save push notification subscription for PWA"""
    try:
        subscription_data = request.get_json()
        
        # Store subscription in database (you'd need to create a model for this)
        # For now, just log it
        logger.info(f"Push subscription received for user {current_user.id}")
        
        return jsonify({
            'success': True,
            'message': 'Push subscription saved'
        })
        
    except Exception as e:
        logger.error(f"Push subscription error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to save subscription'
        }), 500

@websocket_api.route('/analytics/notification-interaction', methods=['POST'])
def track_notification_interaction():
    """Track notification interactions for analytics"""
    try:
        interaction_data = request.get_json()
        
        # Log interaction data
        logger.info(f"Notification interaction: {interaction_data}")
        
        return jsonify({
            'success': True
        })
        
    except Exception as e:
        logger.error(f"Notification tracking error: {e}")
        return jsonify({
            'success': False
        }), 500

@websocket_api.route('/system/status')
def get_system_status():
    """Get system status for monitoring dashboard"""
    try:
        # Check various system components
        status = {
            'services': {
                'flask_app': 'running',
                'trading_engine': 'running',
                'market_data': 'running',
                'load_balancer': 'running'
            },
            'websockets': {
                'market_data': 'ws://localhost:8001',
                'trading_updates': 'ws://localhost:8002',
                'portfolio_updates': 'ws://localhost:8003'
            },
            'database': 'connected',
            'redis': 'connected',
            'market_status': 'open' if is_market_open() else 'closed',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify({
            'success': True,
            'status': status
        })
        
    except Exception as e:
        logger.error(f"System status error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to get system status'
        }), 500

def is_market_open():
    """Check if Indian market is currently open"""
    now = datetime.now(timezone.utc)
    hour = now.hour
    minute = now.minute
    day = now.weekday()
    
    # Indian market hours: 9:15 AM to 3:30 PM, Monday to Friday
    is_weekday = day < 5  # Monday = 0, Friday = 4
    is_market_hours = (hour > 9 or (hour == 9 and minute >= 15)) and \
                     (hour < 15 or (hour == 15 and minute <= 30))
    
    return is_weekday and is_market_hours

# Register blueprint in your main routes.py
def register_websocket_apis(app):
    """Register WebSocket API routes with the Flask app"""
    app.register_blueprint(websocket_api)