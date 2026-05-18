from app import db
from datetime import datetime
from flask_login import UserMixin
from enum import Enum
import json
from cryptography.fernet import Fernet
import os

# Broker Types
class BrokerType(Enum):
    DHAN = "dhan"
    ZERODHA = "zerodha"
    ANGEL_BROKING = "angel_broking"
    UPSTOX = "upstox"
    FIVE_PAISA = "5paisa"
    ALICE_BLUE = "alice_blue"
    FYERS = "fyers"
    SHOONYA = "shoonya"
    # Legacy values kept so existing DB rows are not broken
    GROWW = "groww"
    ICICIDIRECT = "icicidirect"
    HDFC_SECURITIES = "hdfc_securities"
    KOTAK_SECURITIES = "kotak_securities"
    CHOICE_INDIA = "choice_india"
    GOODWILL = "goodwill"

class ConnectionStatus(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    PENDING = "pending"
    EXPIRED = "expired"  # Stored creds present but access token rejected by broker

class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

class TransactionType(Enum):
    BUY = "buy"
    SELL = "sell"

class ProductType(Enum):
    INTRADAY = "intraday"
    DELIVERY = "delivery"
    CNC = "cnc"
    MIS = "mis"
    NRML = "nrml"   # F&O carry-forward / overnight
    MTF  = "mtf"    # Margin Trading Facility (Pay Later)

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    SL = "sl"
    SL_M = "sl_m"

class BrokerAccount(db.Model):
    """User's broker account connections"""
    __tablename__ = 'user_brokers'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(255), db.ForeignKey('tenants.id'), nullable=True, default='live', index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    broker_type = db.Column(db.String(50), nullable=False)  # Store as string, not enum
    broker_name = db.Column(db.String(50), nullable=False)  # Display name
    
    # Encrypted credentials - using Text type to handle large encrypted values
    api_key = db.Column(db.Text, nullable=True)  # Encrypted client_id
    access_token = db.Column(db.Text, nullable=True)  # Encrypted access token (can be very long when encrypted)
    api_secret = db.Column(db.Text, nullable=True)  # Encrypted API secret
    
    # Connection details (match existing table structure)
    connection_status = db.Column(db.String(20), default='disconnected', index=True)
    is_primary = db.Column(db.Boolean, default=False, index=True)  # Primary broker for trading
    is_data_broker = db.Column(db.Boolean, default=False, index=True)  # Use this broker's Data API for market data
    last_connected = db.Column(db.DateTime, nullable=True)
    
    # Account information
    account_balance = db.Column(db.Float, default=0.0)
    margin_available = db.Column(db.Float, default=0.0)  # Match existing column name
    
    # Settings
    is_active = db.Column(db.Boolean, default=True, index=True)
    
    # Other existing columns
    request_token = db.Column(db.Text, nullable=True)
    redirect_url = db.Column(db.Text, nullable=True)
    last_token_refresh = db.Column(db.DateTime, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_sync = db.Column(db.DateTime, nullable=True, index=True)
    sync_status = db.Column(db.String(20), default='pending')  # success, failed, pending, syncing
    
    # Relationships - only basic user relationship for now
    user = db.relationship('User', backref='broker_accounts')
    
    def __init__(self, **kwargs):
        super(BrokerAccount, self).__init__(**kwargs)
        # Initialize encryption key only when needed
    
    @property
    def _encryption_key(self):
        """Get or create encryption key for sensitive data"""
        return getattr(self, '_key', None)
    
    @_encryption_key.setter
    def _encryption_key(self, value):
        self._key = value
    
    def _get_encryption_key(self):
        """Get encryption key from secure environment configuration"""
        try:
            # Try to get from secure config first
            from security.environment_config import setup_secure_environment
            secure_config = setup_secure_environment()
            return secure_config["encryption_key"]
        except (ImportError, KeyError):
            # Fallback to environment variable
            key = os.environ.get('BROKER_ENCRYPTION_KEY')
            if not key:
                environment = os.environ.get("ENVIRONMENT", "development")
                if environment == "production":
                    raise ValueError("BROKER_ENCRYPTION_KEY is required in production")
                # Use a fixed development key for testing (NEVER use in production)
                key = "Target Capital_Dev_Key_32_Chars_Long_123="
                # Convert to proper Fernet key format
                import base64
                key = base64.urlsafe_b64encode(key.encode()[:32].ljust(32, b'0'))
            return key.encode() if isinstance(key, str) else key
    
    def encrypt_data(self, data):
        """Encrypt sensitive data"""
        if not data:
            return None
        fernet = Fernet(self._get_encryption_key())
        return fernet.encrypt(data.encode()).decode()
    
    def decrypt_data(self, encrypted_data):
        """Decrypt sensitive data"""
        if not encrypted_data:
            return None
        try:
            fernet = Fernet(self._get_encryption_key())
            return fernet.decrypt(encrypted_data.encode()).decode()
        except Exception:
            return None

    def set_credentials(self, client_id, access_token=None, api_secret=None, totp_secret=None):
        """Set encrypted credentials (temporary compatibility method)"""
        self.api_key = self.encrypt_data(client_id)  # Store client_id in api_key for compatibility
        if access_token:
            self.access_token = self.encrypt_data(access_token)
        if api_secret:
            # Store TOTP secret in api_secret field for Angel One (separated by |)
            if totp_secret:
                combined_secret = f"{api_secret}|{totp_secret}"
                self.api_secret = self.encrypt_data(combined_secret)
            else:
                self.api_secret = self.encrypt_data(api_secret)
    
    def get_credentials(self):
        """Get decrypted credentials (temporary compatibility method)"""
        try:
            api_secret = self.decrypt_data(self.api_secret)
            totp_secret = None

            # Extract TOTP secret for Angel One (stored as secret|totp)
            if api_secret and '|' in api_secret:
                api_secret, totp_secret = api_secret.split('|', 1)

            return {
                'client_id': self.decrypt_data(self.api_key),
                'access_token': self.decrypt_data(self.access_token),
                'api_secret': api_secret,
                'totp_secret': totp_secret,
                'credentials_valid': True
            }
        except Exception:
            return {
                'client_id': None,
                'access_token': None,
                'api_secret': None,
                'totp_secret': None,
                'credentials_valid': False
            }
    
    def update_connection_status(self, status, error_message=None):
        """Update connection status"""
        self.connection_status = status
        if status == ConnectionStatus.CONNECTED:
            self.last_connected = datetime.utcnow()
            self.connection_error = None
        elif status == ConnectionStatus.ERROR:
            self.connection_error = error_message

    # ─── UI helpers (so templates can show "Saved value" hints) ────────────
    def has_stored_credentials(self) -> bool:
        """True when this account already has saved API key + secret on file."""
        creds = self.get_credentials()
        return bool(creds.get('client_id')) and bool(creds.get('api_secret'))

    def cred_preview(self, field: str = 'client_id') -> str:
        """Return a masked preview of a stored credential, e.g. 'abcd••••wxyz'.

        Safe to render in templates — never returns the full value. Always
        returns an empty string if nothing is stored.
        """
        creds = self.get_credentials()
        raw = creds.get(field) or ''
        if not raw:
            return ''
        if len(raw) <= 6:
            return '••••' + raw[-2:] if len(raw) >= 2 else '••••'
        return raw[:3] + '••••' + raw[-3:]

    def is_token_expired_state(self) -> bool:
        """True when the broker has rejected the saved access token."""
        return (self.connection_status or '').lower() == 'expired'
    
    def set_as_primary(self):
        """Set this account as primary broker"""
        # Remove primary flag from other accounts for this user
        BrokerAccount.query.filter_by(user_id=self.user_id, is_primary=True).update({'is_primary': False})
        self.is_primary = True
        db.session.commit()

class BrokerHolding(db.Model):
    """User's holdings from broker account"""
    __tablename__ = 'broker_holdings'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(255), db.ForeignKey('tenants.id'), nullable=True, default='live', index=True)
    broker_account_id = db.Column(db.Integer, db.ForeignKey('user_brokers.id'), nullable=False, index=True)  # Critical performance index
    
    # Stock details
    symbol = db.Column(db.String(20), nullable=False, index=True)  # Added index for performance
    trading_symbol = db.Column(db.String(50), nullable=False, index=True)
    company_name = db.Column(db.String(200), nullable=True)
    exchange = db.Column(db.String(10), nullable=False, index=True)
    security_id = db.Column(db.String(20), nullable=True)
    isin = db.Column(db.String(20), nullable=True)
    
    # Quantity details
    total_quantity = db.Column(db.Integer, default=0)
    available_quantity = db.Column(db.Integer, default=0)
    t1_quantity = db.Column(db.Integer, default=0)  # T+1 holdings
    dp_quantity = db.Column(db.Integer, default=0)  # Demat quantity
    collateral_quantity = db.Column(db.Integer, default=0)
    
    # Price details
    avg_cost_price = db.Column(db.Float, default=0.0)
    current_price = db.Column(db.Float, default=0.0)
    last_trade_price = db.Column(db.Float, default=0.0)
    
    # P&L calculations
    total_value = db.Column(db.Float, default=0.0)
    investment_value = db.Column(db.Float, default=0.0)
    pnl = db.Column(db.Float, default=0.0)
    pnl_percentage = db.Column(db.Float, default=0.0)
    
    # Metadata
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def calculate_pnl(self):
        """Calculate P&L for this holding"""
        if self.current_price and self.avg_cost_price and self.available_quantity:
            self.total_value = self.current_price * self.available_quantity
            self.investment_value = self.avg_cost_price * self.available_quantity
            self.pnl = self.total_value - self.investment_value
            self.pnl_percentage = (self.pnl / self.investment_value) * 100 if self.investment_value > 0 else 0

class BrokerPosition(db.Model):
    """User's positions from broker account"""
    __tablename__ = 'broker_positions'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(255), db.ForeignKey('tenants.id'), nullable=True, default='live', index=True)
    broker_account_id = db.Column(db.Integer, db.ForeignKey('user_brokers.id'), nullable=False, index=True)  # Critical performance index
    
    # Position details  
    symbol = db.Column(db.String(20), nullable=False, index=True)  # Performance index
    trading_symbol = db.Column(db.String(50), nullable=False, index=True)
    exchange = db.Column(db.String(10), nullable=False, index=True)
    security_id = db.Column(db.String(20), nullable=True)
    product_type = db.Column(db.Enum(ProductType), nullable=False, index=True)  # Frequent filter
    
    # Quantity and price
    quantity = db.Column(db.Integer, default=0)
    buy_quantity = db.Column(db.Integer, default=0)
    sell_quantity = db.Column(db.Integer, default=0)
    avg_buy_price = db.Column(db.Float, default=0.0)
    avg_sell_price = db.Column(db.Float, default=0.0)
    current_price = db.Column(db.Float, default=0.0)
    
    # P&L calculations
    realized_pnl = db.Column(db.Float, default=0.0)
    unrealized_pnl = db.Column(db.Float, default=0.0)
    total_pnl = db.Column(db.Float, default=0.0)
    
    # Metadata
    position_date = db.Column(db.Date, default=datetime.utcnow().date, index=True)  # Date-based queries
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)  # Performance index

class BrokerOrder(db.Model):
    """Orders placed through broker accounts"""
    __tablename__ = 'broker_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(255), db.ForeignKey('tenants.id'), nullable=True, default='live', index=True)
    broker_account_id = db.Column(db.Integer, db.ForeignKey('user_brokers.id'), nullable=False, index=True)  # Critical performance index
    
    # Order identification
    broker_order_id = db.Column(db.String(50), nullable=True)  # Order ID from broker
    correlation_id = db.Column(db.String(50), nullable=True)  # Our internal correlation ID
    
    # Order details
    symbol = db.Column(db.String(20), nullable=False, index=True)  # Performance index
    trading_symbol = db.Column(db.String(50), nullable=False, index=True)  # Frequent filter
    exchange = db.Column(db.String(10), nullable=False, index=True)
    security_id = db.Column(db.String(20), nullable=True)
    
    # Transaction details
    transaction_type = db.Column(db.Enum(TransactionType), nullable=False)
    order_type = db.Column(db.Enum(OrderType), nullable=False)
    product_type = db.Column(db.Enum(ProductType), nullable=False)
    
    # Quantity and price
    quantity = db.Column(db.Integer, nullable=False)
    filled_quantity = db.Column(db.Integer, default=0)
    pending_quantity = db.Column(db.Integer, default=0)
    price = db.Column(db.Float, default=0.0)
    trigger_price = db.Column(db.Float, default=0.0)
    disclosed_quantity = db.Column(db.Integer, default=0)
    
    # Order status and execution
    order_status = db.Column(db.Enum(OrderStatus), default=OrderStatus.PENDING, index=True)  # Frequent filter
    status_message = db.Column(db.String(200), nullable=True)
    avg_execution_price = db.Column(db.Float, default=0.0)
    
    # Trading signal reference (if from signal)
    trading_signal_id = db.Column(db.Integer, nullable=True)  # Reference to trading signal if available
    
    # Timestamps
    order_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)  # Time-based queries
    execution_time = db.Column(db.DateTime, nullable=True, index=True)  # Execution analysis
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Note: trading_signal relationship handled in main models.py if needed
    
    def calculate_total_value(self):
        """Calculate total order value"""
        return self.quantity * self.price if self.price else 0
    
    def update_status(self, status, message=None, filled_qty=None, avg_price=None):
        """Update order status"""
        self.order_status = status
        if message:
            self.status_message = message
        if filled_qty is not None:
            self.filled_quantity = filled_qty
            self.pending_quantity = self.quantity - filled_qty
        if avg_price:
            self.avg_execution_price = avg_price
        if status == OrderStatus.COMPLETE:
            self.execution_time = datetime.utcnow()
        self.last_updated = datetime.utcnow()

class BrokerSyncLog(db.Model):
    """Log of broker data synchronization"""
    __tablename__ = 'broker_sync_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(255), db.ForeignKey('tenants.id'), nullable=True, default='live', index=True)
    broker_account_id = db.Column(db.Integer, db.ForeignKey('user_brokers.id'), nullable=False, index=True)  # Critical performance index
    
    sync_type = db.Column(db.String(50), nullable=False)  # holdings, positions, orders, profile
    sync_status = db.Column(db.String(20), nullable=False)  # success, error, partial
    records_synced = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text, nullable=True)
    sync_duration = db.Column(db.Float, default=0.0)  # in seconds
    
    sync_time = db.Column(db.DateTime, default=datetime.utcnow)


class DataApiBroker(db.Model):
    __tablename__ = 'data_api_broker'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    broker_type = db.Column(db.String(50), nullable=False)
    broker_name = db.Column(db.String(100), nullable=False)

    api_key = db.Column(db.Text, nullable=True)
    access_token = db.Column(db.Text, nullable=True)
    api_secret = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    connection_status = db.Column(db.String(20), default='disconnected')
    last_connected = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref='data_api_broker')

    def _get_encryption_key(self):
        try:
            from security.environment_config import setup_secure_environment
            secure_config = setup_secure_environment()
            return secure_config["encryption_key"]
        except (ImportError, KeyError):
            key = os.environ.get('BROKER_ENCRYPTION_KEY')
            if not key:
                environment = os.environ.get("ENVIRONMENT", "development")
                if environment == "production":
                    raise ValueError("BROKER_ENCRYPTION_KEY is required in production")
                key = "Target Capital_Dev_Key_32_Chars_Long_123="
                import base64
                key = base64.urlsafe_b64encode(key.encode()[:32].ljust(32, b'0'))
            return key.encode() if isinstance(key, str) else key

    def encrypt_data(self, data):
        if not data:
            return None
        fernet = Fernet(self._get_encryption_key())
        return fernet.encrypt(data.encode()).decode()

    def decrypt_data(self, encrypted_data):
        if not encrypted_data:
            return None
        try:
            fernet = Fernet(self._get_encryption_key())
            return fernet.decrypt(encrypted_data.encode()).decode()
        except Exception:
            return None

    def set_credentials(self, client_id, access_token=None, api_secret=None):
        self.api_key = self.encrypt_data(client_id)
        if access_token:
            self.access_token = self.encrypt_data(access_token)
        if api_secret:
            self.api_secret = self.encrypt_data(api_secret)

    def get_credentials(self):
        try:
            return {
                'client_id': self.decrypt_data(self.api_key),
                'access_token': self.decrypt_data(self.access_token),
                'api_secret': self.decrypt_data(self.api_secret),
                'credentials_valid': True,
            }
        except Exception:
            return {
                'client_id': None,
                'access_token': None,
                'api_secret': None,
                'credentials_valid': False,
            }