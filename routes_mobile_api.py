"""
Mobile API Routes v1
Versioned REST API for mobile app integration with JWT authentication
"""
import logging
from flask import Blueprint, request, jsonify, g
from werkzeug.security import check_password_hash
from db_instance import db
from models import User, Portfolio, TradingSignal
from models_broker import BrokerAccount
from services.jwt_service import jwt_service, jwt_required, jwt_optional
from services.otp_service import otp_service
from services.sms_service import sms_service

logger = logging.getLogger(__name__)

mobile_api = Blueprint('mobile_api', __name__, url_prefix='/api/v1/mobile')

@mobile_api.route('/auth/login', methods=['POST'])
def mobile_login():
    """
    Login with email/password and receive JWT tokens
    
    Request Body:
    {
        "email": "user@example.com",
        "password": "password123"
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body required',
            'code': 'INVALID_REQUEST'
        }), 400
    
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({
            'success': False,
            'error': 'Email and password are required',
            'code': 'MISSING_CREDENTIALS'
        }), 400
    
    user = User.query.filter_by(email=email).first()
    
    if not user or not user.password_hash:
        return jsonify({
            'success': False,
            'error': 'Invalid email or password',
            'code': 'INVALID_CREDENTIALS'
        }), 401
    
    if not check_password_hash(user.password_hash, password):
        return jsonify({
            'success': False,
            'error': 'Invalid email or password',
            'code': 'INVALID_CREDENTIALS'
        }), 401
    
    tokens = jwt_service.generate_tokens(user.id, getattr(user, 'tenant_id', 'live'))
    
    logger.info(f"Mobile login successful for user {user.id}")
    
    return jsonify({
        'success': True,
        'tokens': tokens,
        'user': {
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': getattr(user, 'first_name', None),
            'last_name': getattr(user, 'last_name', None),
            'subscription_tier': getattr(user, 'subscription_tier', 'FREE'),
            'profile_picture': getattr(user, 'profile_picture', None)
        }
    })

@mobile_api.route('/auth/otp/send', methods=['POST'])
def send_otp():
    """
    Send OTP to mobile number for login/registration
    
    Request Body:
    {
        "mobile_number": "9876543210"
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body required',
            'code': 'INVALID_REQUEST'
        }), 400
    
    mobile_number = data.get('mobile_number', '').strip()
    
    if not mobile_number:
        return jsonify({
            'success': False,
            'error': 'Mobile number is required',
            'code': 'MISSING_MOBILE'
        }), 400
    
    if not sms_service.validate_mobile_number(mobile_number):
        return jsonify({
            'success': False,
            'error': 'Please enter a valid Indian mobile number',
            'code': 'INVALID_MOBILE'
        }), 400
    
    formatted_mobile = sms_service.format_mobile_number(mobile_number)
    user = User.query.filter_by(mobile_number=formatted_mobile).first()
    
    success, message = otp_service.send_otp_to_mobile(mobile_number, "login")
    
    if success:
        return jsonify({
            'success': True,
            'message': 'OTP sent successfully',
            'is_new_user': user is None,
            'mobile_number': formatted_mobile[-4:]
        })
    else:
        return jsonify({
            'success': False,
            'error': message,
            'code': 'OTP_SEND_FAILED'
        }), 500

@mobile_api.route('/auth/otp/verify', methods=['POST'])
def verify_otp():
    """
    Verify OTP and receive JWT tokens
    
    Request Body:
    {
        "mobile_number": "9876543210",
        "otp": "123456"
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body required',
            'code': 'INVALID_REQUEST'
        }), 400
    
    mobile_number = data.get('mobile_number', '').strip()
    otp = data.get('otp', '').strip()
    
    if not mobile_number or not otp:
        return jsonify({
            'success': False,
            'error': 'Mobile number and OTP are required',
            'code': 'MISSING_FIELDS'
        }), 400
    
    formatted_mobile = sms_service.format_mobile_number(mobile_number)
    success, message, user = otp_service.verify_otp(formatted_mobile, otp)
    
    if success and user:
        tokens = jwt_service.generate_tokens(user.id, getattr(user, 'tenant_id', 'live'))
        
        is_new_user = not user.email or not user.password_hash
        
        logger.info(f"Mobile OTP login successful for user {user.id}")
        
        return jsonify({
            'success': True,
            'tokens': tokens,
            'is_new_user': is_new_user,
            'user': {
                'id': user.id,
                'email': user.email,
                'username': user.username,
                'first_name': getattr(user, 'first_name', None),
                'last_name': getattr(user, 'last_name', None),
                'mobile_number': formatted_mobile[-4:],
                'subscription_tier': getattr(user, 'subscription_tier', 'FREE'),
                'profile_picture': getattr(user, 'profile_picture', None)
            }
        })
    else:
        return jsonify({
            'success': False,
            'error': message,
            'code': 'OTP_VERIFICATION_FAILED'
        }), 401

@mobile_api.route('/auth/refresh', methods=['POST'])
def refresh_token():
    """
    Refresh access token using refresh token
    
    Request Body:
    {
        "refresh_token": "..."
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body required',
            'code': 'INVALID_REQUEST'
        }), 400
    
    refresh_token = data.get('refresh_token', '')
    
    if not refresh_token:
        return jsonify({
            'success': False,
            'error': 'Refresh token is required',
            'code': 'MISSING_TOKEN'
        }), 400
    
    result, error = jwt_service.refresh_access_token(refresh_token)
    
    if error:
        return jsonify({
            'success': False,
            'error': error,
            'code': 'REFRESH_FAILED'
        }), 401
    
    return jsonify({
        'success': True,
        'tokens': result
    })

@mobile_api.route('/auth/logout', methods=['POST'])
@jwt_required
def mobile_logout():
    """Logout user (invalidate token on client side)"""
    logger.info(f"Mobile logout for user {g.current_user.id}")
    
    return jsonify({
        'success': True,
        'message': 'Logged out successfully'
    })

@mobile_api.route('/user/profile', methods=['GET'])
@jwt_required
def get_profile():
    """Get current user profile"""
    user = g.current_user
    
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': getattr(user, 'first_name', None),
            'last_name': getattr(user, 'last_name', None),
            'mobile_number': getattr(user, 'mobile_number', None),
            'subscription_tier': getattr(user, 'subscription_tier', 'FREE'),
            'profile_picture': getattr(user, 'profile_picture', None),
            'created_at': user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None
        }
    })

@mobile_api.route('/user/profile', methods=['PUT'])
@jwt_required
def update_profile():
    """Update user profile"""
    data = request.get_json()
    
    if not data or not isinstance(data, dict):
        return jsonify({
            'success': False,
            'error': 'Invalid request body',
            'code': 'INVALID_REQUEST'
        }), 400
    
    user = g.current_user
    
    if 'first_name' in data:
        user.first_name = data['first_name']
    if 'last_name' in data:
        user.last_name = data['last_name']
    if 'email' in data:
        existing = User.query.filter_by(email=data['email']).first()
        if existing and existing.id != user.id:
            return jsonify({
                'success': False,
                'error': 'Email already in use',
                'code': 'EMAIL_EXISTS'
            }), 400
        user.email = data['email']
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Profile updated successfully',
        'user': {
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': getattr(user, 'first_name', None),
            'last_name': getattr(user, 'last_name', None),
            'subscription_tier': getattr(user, 'subscription_tier', 'FREE')
        }
    })

@mobile_api.route('/portfolio', methods=['GET'])
@jwt_required
def get_portfolio():
    """Get user's portfolio"""
    user = g.current_user
    
    portfolios = Portfolio.query.filter_by(user_id=user.id).all()
    
    portfolio_data = []
    for p in portfolios:
        portfolio_data.append({
            'id': p.id,
            'symbol': p.symbol,
            'asset_type': getattr(p, 'asset_type', 'stock'),
            'quantity': float(p.quantity) if p.quantity else 0,
            'avg_price': float(p.avg_price) if hasattr(p, 'avg_price') and p.avg_price else 0,
            'current_price': float(p.current_price) if hasattr(p, 'current_price') and p.current_price else 0,
            'broker_name': getattr(p, 'broker_name', None),
            'created_at': p.created_at.isoformat() if hasattr(p, 'created_at') and p.created_at else None
        })
    
    return jsonify({
        'success': True,
        'portfolio': portfolio_data,
        'total_count': len(portfolio_data)
    })

@mobile_api.route('/trading-signals', methods=['GET'])
@jwt_required
def get_trading_signals():
    """Get active trading signals"""
    user = g.current_user
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    signal_type = request.args.get('type', None)
    
    query = TradingSignal.query.filter_by(user_id=user.id)
    
    if signal_type:
        query = query.filter_by(signal_type=signal_type)
    
    query = query.order_by(TradingSignal.created_at.desc())
    
    pagination = query.paginate(page=page, per_page=min(per_page, 50), error_out=False)
    
    signals = []
    for s in pagination.items:
        signals.append({
            'id': s.id,
            'symbol': s.symbol,
            'signal_type': s.signal_type,
            'action': getattr(s, 'action', None),
            'entry_price': float(s.entry_price) if hasattr(s, 'entry_price') and s.entry_price else None,
            'target_price': float(s.target_price) if hasattr(s, 'target_price') and s.target_price else None,
            'stop_loss': float(s.stop_loss) if hasattr(s, 'stop_loss') and s.stop_loss else None,
            'status': getattr(s, 'status', 'active'),
            'confidence': getattr(s, 'confidence', None),
            'created_at': s.created_at.isoformat() if s.created_at else None
        })
    
    return jsonify({
        'success': True,
        'signals': signals,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        }
    })

@mobile_api.route('/brokers', methods=['GET'])
@jwt_required
def get_connected_brokers():
    """Get user's connected brokers"""
    user = g.current_user
    
    brokers = BrokerAccount.query.filter_by(user_id=user.id).all()
    
    broker_data = []
    for b in brokers:
        broker_data.append({
            'id': b.id,
            'broker_name': getattr(b, 'broker_name', None) or getattr(b, 'broker_type', None),
            'is_active': getattr(b, 'is_active', True),
            'is_primary': getattr(b, 'is_primary', False),
            'connected_at': b.created_at.isoformat() if hasattr(b, 'created_at') and b.created_at else None
        })
    
    return jsonify({
        'success': True,
        'brokers': broker_data,
        'total_count': len(broker_data)
    })

@mobile_api.route('/market/indices', methods=['GET'])
@jwt_optional
def get_market_indices():
    """Get market indices data (public endpoint)"""
    try:
        from services.nse_service import nse_service
        indices = nse_service.get_market_indices()
        
        return jsonify({
            'success': True,
            'indices': indices
        })
    except Exception as e:
        logger.error(f"Error fetching market indices: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to fetch market data',
            'code': 'MARKET_DATA_ERROR'
        }), 500

@mobile_api.route('/health', methods=['GET'])
def health_check():
    """API health check endpoint"""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'version': 'v1',
        'platform': 'mobile'
    })
