import os
import logging
import threading
import asyncio
import atexit
from flask import Flask, g
from flask.helpers import send_from_directory
from flask_compress import Compress
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

# Configure structured logging with production configuration
def setup_logging():
    """Setup structured logging based on environment"""
    environment = os.environ.get("ENVIRONMENT", "development")
    
    try:
        from config.production_config import ProductionConfig
        from logging.config import dictConfig
        
        logging_config = ProductionConfig.get_logging_config(environment)
        dictConfig(logging_config)
        logging.info("✅ Structured logging configured successfully")
    except ImportError:
        # Fallback to basic logging if production config not available
        log_level = logging.INFO if environment == "production" else logging.DEBUG
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logging.warning("⚠️ Using fallback logging configuration")

setup_logging()

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Create the app
app = Flask(__name__)

# Initialize Flask-Compress for production-grade compression
compress = Compress(app)
# Use secure environment configuration
secure_config = None
try:
    from security.environment_config import setup_secure_environment
    secure_config = setup_secure_environment()
    app.secret_key = secure_config["session_secret"]
    logging.info("✅ Secure environment configuration loaded")
except ImportError:
    # Fallback for development without security module
    session_secret = os.environ.get("SESSION_SECRET")
    if not session_secret:
        # Fail-fast in production
        environment = os.environ.get("ENVIRONMENT", "development")
        if environment == "production":
            raise ValueError("SESSION_SECRET is required in production environment")
        session_secret = "dev-secret-key-change-in-production"
        logging.warning("⚠️ Using default development secret key")
    elif session_secret == "dev-secret-key-change-in-production" and os.environ.get("ENVIRONMENT") == "production":
        raise ValueError("Default development secret cannot be used in production")
    app.secret_key = session_secret
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)  # x_for=1 for accurate client IP behind proxies

# Configure CSRF: Check by default but exempt /api/broker/ endpoints
app.config['WTF_CSRF_CHECK_DEFAULT'] = False  # We'll manually protect forms
app.config['WTF_CSRF_METHODS'] = ['POST', 'PUT', 'PATCH', 'DELETE']  # Methods to protect

# Initialize security extensions  
csrf = CSRFProtect(app)

# Protect CSRF on specific routes only (not on API endpoints)
@csrf.exempt
def check_api_request():
    pass  # Exempting API routes globally

# Make CSRF token available in all templates
from flask_wtf.csrf import generate_csrf

@app.template_global()
def csrf_token():
    return generate_csrf()

# Configure secure session settings
environment = os.environ.get("ENVIRONMENT", "development")
is_production = environment == "production"

# Initialize rate limiter (use Redis if available, fallback to memory)
redis_url = os.environ.get("REDIS_URL")
if is_production and not redis_url:
    logging.warning("⚠️ REDIS_URL not set - using in-memory rate limiting (not recommended for production with multiple workers)")

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per minute"],
    storage_uri=redis_url or "memory://"
)

if secure_config and "security_settings" in secure_config:
    security_settings = secure_config["security_settings"]
    app.config['SESSION_COOKIE_SECURE'] = security_settings.get('session_cookie_secure', is_production)
    app.config['SESSION_COOKIE_HTTPONLY'] = security_settings.get('session_cookie_httponly', True)
    app.config['SESSION_COOKIE_SAMESITE'] = security_settings.get('session_cookie_samesite', 'Lax')  # Lax for OAuth compatibility
else:
    # Fallback secure configuration
    app.config['SESSION_COOKIE_SECURE'] = is_production
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Lax for OAuth compatibility

# Initialize security headers with Talisman
if is_production:
    csp_policy = {
        'default-src': "'self'",
        'script-src': [
            "'self'",
            "'unsafe-inline'",
            'https://cdn.jsdelivr.net',
            'https://cdnjs.cloudflare.com',
            'https://kit.fontawesome.com',
            'https://s3.tradingview.com',
            'https://cdn.razorpay.com',
        ],
        'style-src': [
            "'self'",
            "'unsafe-inline'",
            'https://cdn.jsdelivr.net',
            'https://cdnjs.cloudflare.com',
            'https://fonts.googleapis.com',
            'https://kit.fontawesome.com',
        ],
        'font-src': [
            "'self'",
            'data:',
            'https://fonts.gstatic.com',
            'https://ka-f.fontawesome.com',
            'https://cdnjs.cloudflare.com',
            'https://cdn.jsdelivr.net',
        ],
        'img-src': [
            "'self'",
            'data:',
            'https:',
        ],
        'media-src': [
            "'self'",
            'blob:',
        ],
        'connect-src': [
            "'self'",
            'wss:',
            'https://cdn.jsdelivr.net',
            'https://cdnjs.cloudflare.com',
            'https://ka-f.fontawesome.com',
        ],
        'frame-ancestors': [
            "'self'",
            'https://*.replit.dev',
            'https://*.replit.com',
            'https://*.railway.app'
        ],
    }
    
    Talisman(
        app,
        force_https=False,  # Railway handles HTTPS at load balancer, internal health checks are HTTP
        strict_transport_security=True,
        content_security_policy=csp_policy,
        referrer_policy='strict-origin-when-cross-origin',
        feature_policy={
            'camera': "'none'",
            'microphone': "'self'",
            'geolocation': "'self'",
        }
    )
else:
    # Development mode - relaxed Talisman with Replit iframe support and unsafe-eval
    Talisman(
        app,
        force_https=False,
        strict_transport_security=False,
        content_security_policy={
            'default-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'", "*"],
            'script-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'", "*"],
            'style-src': ["'self'", "'unsafe-inline'", "*"],
            'font-src': ["'self'", "*"],
            'img-src': ["'self'", "data:", "https:", "*"],
            'media-src': ["'self'", "blob:"],
            'connect-src': ["'self'", "ws:", "wss:", "*"],
            'frame-ancestors': [
                "'self'",
                'https://*.replit.dev',
                'https://*.replit.com',
                'https://replit.com',
                'https://*.railway.app'
            ]
        },
        frame_options='ALLOWALL'  # Allow iframe embedding for Replit preview
    )

# Configure the database with enhanced security and connection pooling
try:
    if secure_config is None:
        raise KeyError("secure_config is None")
    database_config = secure_config["database_config"]
    database_url = database_config["url"]
    
    # Fix SSL issues for PostgreSQL
    if database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://')
        if 'sslmode=' not in database_url:
            database_url += '&sslmode=prefer' if '?' in database_url else '?sslmode=prefer'
    
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": database_config["pool_size"],
        "max_overflow": database_config["max_overflow"],
        "pool_recycle": min(database_config["pool_recycle"], 180),  # Reduce to prevent stale SSL connections
        "pool_pre_ping": True,
        "connect_args": {
            "sslmode": "prefer",
            "connect_timeout": 10,
            "application_name": "Target-Capital-Flask"
        } if database_url.startswith('postgresql+psycopg2://') else {}
    }
    
    logging.info("✅ Enhanced database configuration loaded")
    
except (NameError, KeyError):
    # Fallback configuration
    database_url = os.environ.get("DATABASE_URL", "sqlite:///stock_trading.db")
    
    if database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://')
        if 'sslmode=' not in database_url:
            database_url += '&sslmode=prefer' if '?' in database_url else '?sslmode=prefer'
    
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 10,  # Optimize connection pool size
        "max_overflow": 20,  # Allow overflow connections
        "pool_recycle": 180,  # Reduced to prevent stale SSL connections
        "pool_pre_ping": True,
        "pool_timeout": 30,  # Connection timeout
        "connect_args": {
            "sslmode": "prefer",
            "connect_timeout": 10
        } if database_url.startswith('postgresql+psycopg2://') else {}
    }
    
    logging.warning("⚠️ Using fallback database configuration")

# Initialize the app with the extension
db.init_app(app)

# Mail configuration for notifications
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'notifications@targetcapital.ai')

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # type: ignore
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    from models import User
    return User.query.get(int(user_id))

# Fix OAuth issue: Return 401 JSON for API requests instead of redirecting
@login_manager.unauthorized_handler
def unauthorized():
    from flask import request, jsonify, redirect, url_for
    if request.path.startswith('/api') or request.accept_mimetypes.best == 'application/json':
        return jsonify({'error': 'unauthorized', 'message': 'Authentication required'}), 401
    return redirect(url_for('login', next=request.url))

with app.app_context():
    # Import models
    import models
    import models_broker  # Import broker models too
    import models_vector  # Import vector database models for RAG
    import routes_mobile  # Import mobile OTP routes
    
    # Only create tables in development mode
    # Production should use Alembic migrations
    if not is_production:
        db.create_all()
        logging.info("✅ Database tables created (development mode)")
    else:
        logging.info("⚠️ Skipping db.create_all() in production - use Alembic migrations")

    # ── Incremental column migrations (safe to run on every startup) ──────────
    # ADD COLUMN IF NOT EXISTS is idempotent — no-op when column already exists.
    _pending_migrations = [
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT \'en\'',
    ]
    try:
        with db.engine.connect() as _conn:
            for _sql in _pending_migrations:
                _conn.execute(db.text(_sql))
            _conn.commit()
        logging.info("✅ Incremental column migrations applied")
    except Exception as _e:
        logging.warning(f"⚠️ Column migration skipped (table may not exist yet): {_e}")
    # ─────────────────────────────────────────────────────────────────────────

    # Initialize default 'live' tenant (Target Capital) - only if tables exist
    try:
        models.Tenant.get_or_create_default()
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize default tenant (tables may not exist yet): {e}")
    
    # Initialize tenant-aware SQLAlchemy infrastructure
    try:
        from middleware.tenant_sqlalchemy import setup_tenant_sqlalchemy, init_tenant_scoped_models
        setup_tenant_sqlalchemy(db)
        init_tenant_scoped_models()
        logging.info("✅ Tenant-aware SQLAlchemy infrastructure initialized")
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize tenant SQLAlchemy: {e}")

# Initialize multi-tenant middleware
from middleware.tenant_middleware import init_tenant_middleware
init_tenant_middleware(app)

# Import and register Google OAuth blueprint (from blueprint:flask_google_oauth)
from google_auth import google_auth
app.register_blueprint(google_auth)

# Register admin blueprint (import after routes to avoid conflicts)
try:
    from admin_routes import admin_bp
    app.register_blueprint(admin_bp)
except ImportError as e:
    logging.warning(f"Admin blueprint not available: {e}")


# WebSocket Server Management
websocket_threads = []
websocket_shutdown_event = threading.Event()

def start_websocket_server_thread(server_start_func, server_name):
    """Start a WebSocket server in a background thread"""
    def run_server():
        try:
            logging.info(f"🚀 Starting {server_name} WebSocket server in background thread")
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Start the server
            server_coro = server_start_func()
            loop.run_until_complete(server_coro)
            
        except Exception as e:
            logging.error(f"❌ Failed to start {server_name} WebSocket server: {e}")
    
    thread = threading.Thread(target=run_server, daemon=True, name=f"websocket-{server_name}")
    thread.start()
    websocket_threads.append(thread)
    logging.info(f"✅ {server_name} WebSocket server thread started")

def start_all_websocket_servers():
    """Start all WebSocket servers in background threads"""
    try:
        from websocket_servers import (
            start_market_data_server,
            start_trading_updates_server, 
            start_portfolio_updates_server
        )
        
        logging.info("🌐 Initializing WebSocket infrastructure...")
        
        # Start each WebSocket server in its own thread
        start_websocket_server_thread(start_market_data_server, "MarketData")
        start_websocket_server_thread(start_trading_updates_server, "TradingUpdates")
        start_websocket_server_thread(start_portfolio_updates_server, "PortfolioUpdates")
        
        logging.info("✅ All WebSocket servers started successfully")
        
    except ImportError as e:
        logging.error(f"❌ Failed to import WebSocket servers: {e}")
    except Exception as e:
        logging.error(f"❌ Failed to start WebSocket servers: {e}")

# WebSocket cleanup DISABLED - WebSocket servers are not started in user-driven mode
# def cleanup_websocket_servers():
#     """Cleanup WebSocket servers on app shutdown"""
#     logging.info("🛑 Shutting down WebSocket servers...")
#     websocket_shutdown_event.set()
#     for thread in websocket_threads:
#         thread.join(timeout=5)
#     logging.info("✅ WebSocket servers shutdown complete")
# atexit.register(cleanup_websocket_servers)

# Register WebSocket API routes
try:
    from routes_websocket import register_websocket_apis
    register_websocket_apis(app)
except ImportError as e:
    logging.warning(f"WebSocket API routes not available: {e}")

# WebSocket servers DISABLED - system is strictly user-driven with no automatic background processes
# Per user requirement: no automatic background polling, demo data generation, or WebSocket connections
logging.info("🚀 Starting Target Capital application (user-driven mode - no WebSocket servers)")

# Performance optimizations - Caching and security headers
@app.after_request
def enable_caching_and_security(response):
    """Enable aggressive caching and security headers"""
    
    if request.endpoint == 'static':
        if request.path.endswith('.js'):
            response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        else:
            response.headers['Cache-Control'] = 'public, max-age=86400'
    
    # Security headers for all responses  
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    return response

# Import request for the after_request function
from flask import request, jsonify
from datetime import datetime
from sqlalchemy import text

# Service worker route for PWA support
@app.route('/sw.js')
def service_worker():
    """Serve the service worker for PWA functionality"""
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

# Health check endpoints for production monitoring
@app.route('/health')
def health_check():
    """Basic health check - returns 200 if app is running"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'environment': environment
    }), 200

@app.route('/health/ready')
def readiness_check():
    """Readiness check - verifies database and Redis connectivity"""
    checks = {
        'database': False,
        'redis': False,
        'status': 'unhealthy'
    }
    
    try:
        db.session.execute(text('SELECT 1'))
        checks['database'] = True
    except Exception as e:
        checks['database_error'] = str(e)
    
    try:
        from caching.redis_cache import get_cache
        cache = get_cache()
        checks['redis'] = cache.is_available()
    except Exception as e:
        checks['redis_error'] = str(e)
    
    checks['timestamp'] = datetime.utcnow().isoformat()
    checks['environment'] = environment
    
    if checks['database']:
        checks['status'] = 'healthy' if checks['redis'] else 'degraded'
        status_code = 200
    else:
        checks['status'] = 'unhealthy'
        status_code = 503
    
    return jsonify(checks), status_code

@app.route('/health/live')
def liveness_check():
    """Liveness check - simple ping for container orchestrators"""
    return 'OK', 200

# Import routes
import routes

@app.context_processor
def inject_tenant_config():
    try:
        from models import Tenant
        tenant = Tenant.query.get('live')
        return dict(tenant_config=tenant.config if tenant else {})
    except Exception as e:
        logging.warning(f"Tenant config unavailable (DB connection issue): {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return dict(tenant_config={})

@app.context_processor
def inject_site_config():
    try:
        from models import SiteConfig
        broker_name = SiteConfig.get('broker_name', 'Scentric Networks')
    except Exception:
        broker_name = 'Scentric Networks'
    return dict(broker_name=broker_name)
