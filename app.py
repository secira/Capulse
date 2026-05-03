import os
import logging
import threading
import asyncio
import atexit
from datetime import datetime, timezone, timedelta
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
except Exception as _sec_err:
    # Never crash at startup due to missing/invalid env vars.
    # Log clearly and use the best available fallback.
    logging.warning(f"⚠️ Secure config error ({_sec_err}). Using direct env var fallback.")
    _session_secret = os.environ.get("SESSION_SECRET", "")
    if not _session_secret:
        import secrets as _secrets
        _session_secret = _secrets.token_urlsafe(32)
        logging.error(
            "❌ SESSION_SECRET not set — generated a one-time secret. "
            "Set SESSION_SECRET in Railway Variables to keep sessions stable across restarts."
        )
    app.secret_key = _session_secret
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

IST = timezone(timedelta(hours=5, minutes=30))


@app.template_filter("ist_datetime")
def ist_datetime(value, fmt="%d %b %Y, %I:%M %p"):
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime(fmt)

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
    
    # Normalise Railway/Heroku postgres:// → postgresql://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    # Inject psycopg2 driver and SSL
    if database_url.startswith('postgresql://') and '+psycopg2' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
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

    # Normalise Railway/Heroku postgres:// → postgresql://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    # Inject psycopg2 driver and SSL
    if database_url.startswith('postgresql://') and '+psycopg2' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
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
    import models_partner_api  # B2B partner API models (ApiPartner / ApiSubscription / ApiAlertLog)
    import routes_mobile  # Import mobile OTP routes
    
    # In production, all tables already exist and are populated — DO NOT call
    # db.create_all().  The incremental column-migration block below handles
    # any additive schema changes (ADD COLUMN IF NOT EXISTS, CREATE TABLE IF
    # NOT EXISTS) safely on every startup.
    # In development, db.create_all() bootstraps the local DB.
    if not is_production:
        try:
            db.create_all()
            logging.info("✅ Database tables created (development mode)")
        except Exception as _e:
            logging.error(f"db.create_all() failed: {_e}", exc_info=True)
    else:
        logging.info("⏭️  Production: skipping db.create_all() — only additive migrations will run")

    # ── Incremental column migrations (safe to run on every startup) ──────────
    # ADD COLUMN IF NOT EXISTS is idempotent — no-op when column already exists.
    _pending_migrations = [
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT \'en\'',
        'ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS asset_type VARCHAR(20) DEFAULT \'STOCK\'',
        'ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS instrument_detail VARCHAR(100) DEFAULT \'\'',
        'ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS sync_status VARCHAR(20) DEFAULT \'pending\'',
        # Fix FK constraints that were created pointing to old table name 'broker_accounts' instead of 'user_brokers'
        '''DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name='broker_holdings_broker_account_id_fkey' AND constraint_type='FOREIGN KEY') THEN
                ALTER TABLE broker_holdings DROP CONSTRAINT broker_holdings_broker_account_id_fkey;
                ALTER TABLE broker_holdings ADD CONSTRAINT broker_holdings_broker_account_id_fkey FOREIGN KEY (broker_account_id) REFERENCES user_brokers(id) ON DELETE CASCADE;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name='broker_positions_broker_account_id_fkey' AND constraint_type='FOREIGN KEY') THEN
                ALTER TABLE broker_positions DROP CONSTRAINT broker_positions_broker_account_id_fkey;
                ALTER TABLE broker_positions ADD CONSTRAINT broker_positions_broker_account_id_fkey FOREIGN KEY (broker_account_id) REFERENCES user_brokers(id) ON DELETE CASCADE;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name='broker_orders_broker_account_id_fkey' AND constraint_type='FOREIGN KEY') THEN
                ALTER TABLE broker_orders DROP CONSTRAINT broker_orders_broker_account_id_fkey;
                ALTER TABLE broker_orders ADD CONSTRAINT broker_orders_broker_account_id_fkey FOREIGN KEY (broker_account_id) REFERENCES user_brokers(id) ON DELETE CASCADE;
            END IF;
        END $$''',
        '''CREATE TABLE IF NOT EXISTS behavioural_alerts (
            id SERIAL PRIMARY KEY,
            tenant_id VARCHAR(255) DEFAULT \'live\',
            user_id INTEGER NOT NULL REFERENCES "user"(id),
            alert_type VARCHAR(50) NOT NULL,
            severity VARCHAR(10) NOT NULL,
            title VARCHAR(200),
            description TEXT,
            advice TEXT,
            acknowledged BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            acknowledged_at TIMESTAMP
        )''',
        'ALTER TABLE manual_mutual_fund_holdings ADD COLUMN IF NOT EXISTS platform_name VARCHAR(100)',
        'ALTER TABLE manual_commodity_holdings ADD COLUMN IF NOT EXISTS platform_name VARCHAR(100)',
        'ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS external_trade_id VARCHAR(100)',
        'ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS transaction_type VARCHAR(10)',
        'CREATE INDEX IF NOT EXISTS ix_manual_trade_imports_external_trade_id ON manual_trade_imports (external_trade_id)',
        '''CREATE TABLE IF NOT EXISTS data_source_config (
            id SERIAL PRIMARY KEY,
            source_key VARCHAR(50) NOT NULL UNIQUE,
            display_name VARCHAR(100) NOT NULL,
            description TEXT,
            icon VARCHAR(50) DEFAULT 'fa-database',
            is_active BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )''',
        '''INSERT INTO data_source_config (source_key, display_name, description, icon, is_active)
           VALUES ('nse_python', 'NSE Python (Default)', 'Uses NSEPython, yfinance, and NSE official API for option chain and market data. Free, no API key required.', 'fa-code', true)
           ON CONFLICT (source_key) DO NOTHING''',
        '''INSERT INTO data_source_config (source_key, display_name, description, icon, is_active)
           VALUES ('truedata', 'TrueData API', 'Professional real-time data feed with sub-second latency. Requires TrueData subscription and API key.', 'fa-bolt', false)
           ON CONFLICT (source_key) DO NOTHING''',
        '''INSERT INTO data_source_config (source_key, display_name, description, icon, is_active)
           VALUES ('user_custom', 'User Data Source', 'Manual CSV upload or custom data input for backtesting and historical analysis.', 'fa-upload', false)
           ON CONFLICT (source_key) DO NOTHING''',
        '''CREATE TABLE IF NOT EXISTS fno_signal_history (
            id SERIAL PRIMARY KEY,
            signal_type VARCHAR(20) DEFAULT 'SCAN',
            direction VARCHAR(20),
            confidence INTEGER DEFAULT 0,
            confidence_grade VARCHAR(20),
            entry_mode VARCHAR(20),
            spot_price FLOAT,
            atm_strike INTEGER,
            trades_json TEXT,
            layers_json TEXT,
            alert_sent BOOLEAN DEFAULT FALSE,
            data_source VARCHAR(50) DEFAULT 'nse_python',
            created_at TIMESTAMP DEFAULT NOW()
        )''',
        'ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS is_data_broker BOOLEAN DEFAULT FALSE',
        '''CREATE TABLE IF NOT EXISTS data_api_broker (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES "user"(id),
            broker_type VARCHAR(50) NOT NULL,
            broker_name VARCHAR(100) NOT NULL,
            api_key TEXT,
            access_token TEXT,
            api_secret TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            connection_status VARCHAR(20) DEFAULT 'disconnected',
            last_connected TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )''',
        '''CREATE TABLE IF NOT EXISTS data_api_plan (
            id SERIAL PRIMARY KEY,
            plan_type VARCHAR(30) NOT NULL DEFAULT 'user_data',
            truedata_api_key TEXT,
            truedata_api_secret TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT NOW(),
            updated_by VARCHAR(100)
        )''',
        '''INSERT INTO data_api_plan (plan_type, is_active) SELECT 'user_data', true
            WHERE NOT EXISTS (SELECT 1 FROM data_api_plan)''',
        'ALTER TABLE research_list ADD COLUMN IF NOT EXISTS hist_data_source VARCHAR(50)',
        # fno_signal_history — columns added for multi-index support (BANKNIFTY, FINNIFTY, SENSEX)
        # and trade-lifecycle tracking (trade_code, outcome, exit_spot, exit_time).
        # The original CREATE TABLE migration only had the base set of columns.
        "ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS index_id VARCHAR(20) DEFAULT 'NIFTY'",
        'ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS trade_code VARCHAR(20)',
        'ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS outcome VARCHAR(50)',
        'ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS exit_spot FLOAT',
        'ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP',
        # ── B2B Partner API tables (api_partner / api_subscription / api_alert_log) ──
        # Production skips db.create_all(), so these new tables must be created
        # explicitly here for Railway deployments.
        '''CREATE TABLE IF NOT EXISTS api_partner (
            id SERIAL PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            contact_email VARCHAR(180) NOT NULL,
            organisation VARCHAR(180),
            api_key_prefix VARCHAR(16) NOT NULL,
            api_key_hash VARCHAR(256) NOT NULL,
            webhook_url VARCHAR(512),
            webhook_secret VARCHAR(128),
            plan VARCHAR(32) NOT NULL DEFAULT 'basic',
            rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            tenant_id VARCHAR(255) DEFAULT 'live',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMP
        )''',
        'CREATE INDEX IF NOT EXISTS ix_api_partner_contact_email ON api_partner (contact_email)',
        'CREATE INDEX IF NOT EXISTS ix_api_partner_api_key_prefix ON api_partner (api_key_prefix)',
        'CREATE INDEX IF NOT EXISTS ix_api_partner_tenant_id ON api_partner (tenant_id)',
        '''CREATE TABLE IF NOT EXISTS api_subscription (
            id SERIAL PRIMARY KEY,
            partner_id INTEGER NOT NULL REFERENCES api_partner(id) ON DELETE CASCADE,
            engine VARCHAR(16) NOT NULL,
            symbol VARCHAR(64) NOT NULL,
            min_confidence INTEGER NOT NULL DEFAULT 75,
            delta_threshold INTEGER NOT NULL DEFAULT 5,
            channels VARCHAR(64) NOT NULL DEFAULT 'webhook',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_score FLOAT,
            last_tier VARCHAR(32),
            last_alert_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_partner_engine_symbol UNIQUE (partner_id, engine, symbol)
        )''',
        'CREATE INDEX IF NOT EXISTS ix_api_subscription_partner_id ON api_subscription (partner_id)',
        'CREATE INDEX IF NOT EXISTS ix_api_subscription_engine ON api_subscription (engine)',
        'CREATE INDEX IF NOT EXISTS ix_api_subscription_symbol ON api_subscription (symbol)',
        '''CREATE TABLE IF NOT EXISTS api_alert_log (
            id SERIAL PRIMARY KEY,
            partner_id INTEGER NOT NULL REFERENCES api_partner(id) ON DELETE CASCADE,
            subscription_id INTEGER REFERENCES api_subscription(id) ON DELETE SET NULL,
            engine VARCHAR(16) NOT NULL,
            symbol VARCHAR(64) NOT NULL,
            score FLOAT,
            tier VARCHAR(32),
            channel VARCHAR(32) NOT NULL DEFAULT 'webhook',
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            http_status INTEGER,
            error VARCHAR(512),
            payload_json TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            delivered_at TIMESTAMP
        )''',
        'CREATE INDEX IF NOT EXISTS ix_api_alert_log_partner_id ON api_alert_log (partner_id)',
        'CREATE INDEX IF NOT EXISTS ix_api_alert_log_subscription_id ON api_alert_log (subscription_id)',
        'CREATE INDEX IF NOT EXISTS ix_api_alert_log_symbol ON api_alert_log (symbol)',
        'CREATE INDEX IF NOT EXISTS ix_api_alert_log_created_at ON api_alert_log (created_at)',
    ]
    # In production, column migrations are GATED behind RUN_MIGRATIONS=1.
    # Reason: with gunicorn --preload, this block runs in the master process
    # during app load.  An ALTER TABLE that needs to wait for a lock (held by
    # any other live connection) will hang the master forever — workers never
    # get forked, /health never responds, Railway healthcheck times out.
    # On Railway: set RUN_MIGRATIONS=1 for ONE deploy when you ship a new
    # column or table, watch it complete, then unset it.  Normal redeploys
    # skip this entire block and boot in seconds.
    # In development we always run them so the local DB stays in sync.
    _should_run_migrations = (not is_production) or (os.environ.get("RUN_MIGRATIONS") == "1")
    if _should_run_migrations:
        try:
            with db.engine.connect() as _conn:
                for _i, _sql in enumerate(_pending_migrations, 1):
                    logging.info(f"  [migration {_i}/{len(_pending_migrations)}] running…")
                    _conn.execute(db.text(_sql))
                _conn.commit()
            logging.info("✅ Incremental column migrations applied")
        except Exception as _e:
            logging.warning(f"⚠️ Column migration skipped (table may not exist yet): {_e}")
    else:
        logging.info("⏭️  Production: skipping column migrations (set RUN_MIGRATIONS=1 to apply)")
    # ─────────────────────────────────────────────────────────────────────────

    # Initialize default 'live' tenant (Target Capital) - only if tables exist
    logging.info("→ initializing default tenant…")
    try:
        models.Tenant.get_or_create_default()
        logging.info("✅ Default tenant ready")
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize default tenant (tables may not exist yet): {e}")

    # Initialize tenant-aware SQLAlchemy infrastructure
    logging.info("→ setting up tenant-aware SQLAlchemy…")
    try:
        from middleware.tenant_sqlalchemy import setup_tenant_sqlalchemy, init_tenant_scoped_models
        setup_tenant_sqlalchemy(db)
        init_tenant_scoped_models()
        logging.info("✅ Tenant-aware SQLAlchemy infrastructure initialized")
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize tenant SQLAlchemy: {e}")

# Initialize multi-tenant middleware
try:
    from middleware.tenant_middleware import init_tenant_middleware
    init_tenant_middleware(app)
except Exception as _e:
    logging.error(f"❌ Tenant middleware init failed: {_e}", exc_info=True)

# Import and register Google OAuth blueprint (from blueprint:flask_google_oauth)
try:
    from google_auth import google_auth
    app.register_blueprint(google_auth)
except Exception as _e:
    logging.error(f"❌ Google OAuth blueprint failed: {_e}", exc_info=True)

# Register admin blueprint (import after routes to avoid conflicts)
try:
    from admin_routes import admin_bp
    app.register_blueprint(admin_bp)
except Exception as e:
    logging.warning(f"Admin blueprint not available: {e}", exc_info=True)

try:
    from routes_fno import fno_bp
    app.register_blueprint(fno_bp)
except Exception as e:
    logging.warning(f"F&O blueprint not available: {e}", exc_info=True)

# Guard: seed/migration subprocesses set SKIP_SCHEDULER=1 so that APScheduler
# (which spawns non-daemon threads) never starts inside them.  Without this
# guard the seed process can never exit, entrypoint.sh hangs forever, and
# gunicorn never starts — causing the Railway healthcheck to time out.
if not os.environ.get("SKIP_SCHEDULER"):
    try:
        from services.fno_monitor import start_scheduler as start_fno_monitor
        start_fno_monitor(app)
    except Exception as e:
        logging.warning(f"F&O monitor not started: {e}")

    try:
        from services.iscore_alert_dispatcher import start_scheduler as start_iscore_partner_scheduler
        start_iscore_partner_scheduler(app)
    except Exception as e:
        logging.warning(f"I-Score partner scheduler not started: {e}")


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

# Warm up Dhan instrument master in a background daemon thread so that the
# first I-Score / OHLCV request after a server restart is not delayed.
def _warmup_dhan_instrument_master():
    try:
        from services.dhan_service import _load_security_id_map
        _load_security_id_map()
    except Exception as _e:
        logging.warning(f"Dhan instrument master warm-up failed: {_e}")

_warmup_thread = threading.Thread(
    target=_warmup_dhan_instrument_master,
    daemon=True,
    name="dhan-instrument-master-warmup",
)
_warmup_thread.start()

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
import traceback as _traceback
try:
    import routes
    logging.info("✅ Routes loaded successfully")
except Exception as _routes_err:
    logging.critical(
        f"❌ FATAL: Failed to import routes — app will have no routes!\n"
        f"Error: {_routes_err}\n"
        f"Traceback:\n{_traceback.format_exc()}"
    )
    # Do NOT re-raise — let gunicorn start so /health and Railway logs are visible

# ── 30-DAY TRIAL EXPIRY GUARD ─────────────────────────────────────────────────
# FREE-plan users get full access for 30 days after registration.
# After the trial ends every request (except the exempt set below) is
# redirected to /pricing so they can upgrade.  Admins are always exempt.
_TRIAL_EXEMPT_ENDPOINTS: frozenset = frozenset({
    # Infrastructure
    'static', 'health_check', 'liveness_check',
    # Public / marketing pages
    'index', 'about', 'services', 'algo_trading', 'algo_trading_service',
    'blog', 'blog_post', 'blog_post_by_slug', 'pricing', 'careers',
    'news', 'partners', 'for_brokers', 'contact',
    'trading_signals', 'daily_signals_feature', 'live_market',
    # Auth
    'login', 'register', 'logout',
    'google_auth.login', 'google_auth.callback', 'google_auth.logout',
    # OTP / mobile auth
    'send_otp', 'verify_otp', 'resend_otp', 'mobile_login',
    # Payments / upgrades (must be reachable so they can subscribe)
    'subscribe', 'verify_payment', 'payment_success', 'payment_failed',
    'upgrade_plan', 'razorpay_webhook',
    # Settings & account management
    'account_profile', 'update_profile', 'account_settings',
    'account_billing', 'change_password', 'update_notification_settings',
})

@app.before_request
def check_trial_expiry():
    """Redirect FREE users to pricing once their 30-day trial has expired."""
    from flask import request, redirect, url_for, flash
    from flask_login import current_user

    endpoint = request.endpoint
    if not endpoint or endpoint in _TRIAL_EXEMPT_ENDPOINTS:
        return

    if not current_user.is_authenticated:
        return

    if getattr(current_user, 'is_admin', False):
        return

    try:
        plan = current_user.pricing_plan.value
    except Exception:
        return

    if plan != 'FREE':
        return

    # Trial still active → full access, nothing to do
    if current_user.is_trial_active():
        return

    # Trial expired → send to pricing
    flash(
        'Your 30-day free trial has ended. '
        'Please upgrade to continue using all features of Target Capital.',
        'warning'
    )
    return redirect(url_for('pricing'))
# ── END TRIAL EXPIRY GUARD ────────────────────────────────────────────────────

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
