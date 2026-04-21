"""
Secure Environment Configuration
Ensures production security standards and proper key management
"""

import os
import logging
import secrets
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
import base64

logger = logging.getLogger(__name__)

class SecureEnvironmentConfig:
    """Secure environment configuration manager"""
    
    def __init__(self):
        self.environment = os.environ.get("ENVIRONMENT", "development")
        self.is_production = self.environment == "production"
        self._validated_config = {}
        
    def validate_and_get_config(self) -> Dict[str, Any]:
        """Validate and return secure configuration"""
        
        config = {
            "session_secret": self._get_session_secret(),
            "encryption_key": self._get_encryption_key(),
            "database_config": self._get_database_config(),
            "redis_config": self._get_redis_config(),
            "api_keys": self._get_api_keys(),
            "security_settings": self._get_security_settings()
        }
        
        # Validate configuration
        self._validate_production_config(config)
        
        return config
    
    def _get_session_secret(self) -> str:
        """Get secure session secret"""
        session_secret = os.environ.get("SESSION_SECRET")
        
        if not session_secret:
            if self.is_production:
                raise ValueError("SESSION_SECRET is required in production")
            # Generate secure development secret
            session_secret = secrets.token_urlsafe(32)
            logger.warning(f"Generated session secret for development: {session_secret[:10]}...")
        
        # Validate production secret
        if self.is_production:
            if session_secret == "dev-secret-key-change-in-production":
                raise ValueError("Default development secret cannot be used in production")
            if len(session_secret) < 32:
                raise ValueError("Session secret must be at least 32 characters in production")
        
        return session_secret
    
    def _get_encryption_key(self) -> bytes:
        """Get secure encryption key for broker credentials.

        Priority:
          1. BROKER_ENCRYPTION_KEY env var  (best — copy same value to all envs)
          2. Derived from SESSION_SECRET    (stable across restarts/deployments)
          3. Random key                     (last-resort dev only — never survives restart)
        """
        import hashlib

        encryption_key = os.environ.get("BROKER_ENCRYPTION_KEY")

        if encryption_key:
            # Handle string keys properly for Fernet
            if isinstance(encryption_key, str):
                encryption_key = encryption_key.encode()
            try:
                Fernet(encryption_key)
                return encryption_key
            except Exception as e:
                raise ValueError(f"Invalid BROKER_ENCRYPTION_KEY format: {e}")

        # Fallback 1: derive a stable 32-byte key from SESSION_SECRET.
        # Same SESSION_SECRET → same encryption key on every startup and every
        # server, so credentials encrypted on Replit are readable on Railway.
        session_secret = os.environ.get("SESSION_SECRET", "")
        if session_secret:
            derived = hashlib.sha256(
                (session_secret + "_broker_key_v1").encode()
            ).digest()                              # 32 raw bytes
            stable_key = base64.urlsafe_b64encode(derived)  # Fernet needs URL-safe b64
            if self.is_production:
                logger.warning(
                    "⚠️  BROKER_ENCRYPTION_KEY not set — deriving encryption key from "
                    "SESSION_SECRET. Add BROKER_ENCRYPTION_KEY to Railway Variables "
                    "(copy from Replit Secrets) for explicit key management."
                )
            return stable_key

        # Fallback 2: random key — credentials won't survive restarts. Dev only.
        logger.error(
            "❌ Neither BROKER_ENCRYPTION_KEY nor SESSION_SECRET is set. "
            "Broker credentials will be unreadable after restart. "
            "Set BROKER_ENCRYPTION_KEY in your environment variables."
        )
        return Fernet.generate_key()
    
    def _get_database_config(self) -> Dict[str, Any]:
        """Get secure database configuration"""
        database_url = os.environ.get("DATABASE_URL")
        
        if not database_url:
            if self.is_production:
                raise ValueError("DATABASE_URL is required in production")
            database_url = "sqlite:///stock_trading.db"
        
        # Validate PostgreSQL SSL in production
        if self.is_production and database_url.startswith("postgresql"):
            if "sslmode=require" not in database_url and "sslmode=disable" not in database_url:
                logger.warning("PostgreSQL SSL not explicitly configured")
        
        config = {
            "url": database_url,
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "20")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "30")),
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "300")),
            "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
            "echo": os.environ.get("DB_ECHO", "false").lower() == "true"
        }
        
        # Read replicas
        replica_count = int(os.environ.get("DB_READ_REPLICAS", "0"))
        replicas = []
        for i in range(replica_count):
            replica_url = os.environ.get(f"DATABASE_READ_REPLICA_{i+1}")
            if replica_url:
                replicas.append(replica_url)
        config["read_replicas"] = replicas
        
        return config
    
    def _get_redis_config(self) -> Dict[str, Any]:
        """Get Redis configuration"""
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        
        # Validate Redis SSL in production
        if self.is_production and not redis_url.startswith("rediss://"):
            logger.warning("Redis SSL connection recommended for production")
        
        return {
            "url": redis_url,
            "max_connections": int(os.environ.get("REDIS_MAX_CONNECTIONS", "50")),
            "socket_keepalive": True,
            "socket_keepalive_options": {},
            "health_check_interval": int(os.environ.get("REDIS_HEALTH_CHECK_INTERVAL", "30"))
        }
    
    def _get_api_keys(self) -> Dict[str, str]:
        """Get API keys and secrets"""
        api_keys = {}
        
        # OpenAI API Key
        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            api_keys["openai"] = openai_key
        elif self.is_production:
            logger.warning("OPENAI_API_KEY not configured")
        
        # Perplexity API Key
        perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
        if perplexity_key:
            api_keys["perplexity"] = perplexity_key
        elif self.is_production:
            logger.warning("PERPLEXITY_API_KEY not configured")
        
        # Razorpay Keys
        razorpay_key_id = os.environ.get("RAZORPAY_KEY_ID")
        razorpay_key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if razorpay_key_id and razorpay_key_secret:
            api_keys["razorpay_key_id"] = razorpay_key_id
            api_keys["razorpay_key_secret"] = razorpay_key_secret
        elif self.is_production:
            logger.warning("Razorpay API keys not configured")
        
        return api_keys
    
    def _get_security_settings(self) -> Dict[str, Any]:
        """Get security settings"""
        return {
            # Rate limiting
            "rate_limiting_enabled": os.environ.get("RATE_LIMITING_ENABLED", "true").lower() == "true",
            "rate_limit_per_minute": int(os.environ.get("RATE_LIMIT_PER_MINUTE", "300")),
            
            # CORS
            "cors_origins": os.environ.get("CORS_ORIGINS", "*").split(","),
            "cors_credentials": os.environ.get("CORS_CREDENTIALS", "true").lower() == "true",
            
            # Session security
            "session_cookie_secure": self.is_production,
            "session_cookie_httponly": True,
            "session_cookie_samesite": "strict" if self.is_production else "lax",
            
            # Content Security Policy
            "csp_enabled": self.is_production,
            "csp_policy": self._get_csp_policy(),
            
            # Logging
            "security_logging_enabled": True,
            "log_sensitive_data": not self.is_production
        }
    
    def _get_csp_policy(self) -> str:
        """Get Content Security Policy"""
        if self.is_production:
            return (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "connect-src 'self' wss: https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://ka-f.fontawesome.com; "
                "frame-ancestors 'none';"
            )
        else:
            return "default-src 'self' 'unsafe-inline' 'unsafe-eval' *;"
    
    def _validate_production_config(self, config: Dict[str, Any]):
        """Validate configuration for production"""
        if not self.is_production:
            return
        
        # Critical validation checks
        validations = [
            (len(config["session_secret"]) >= 32, "Session secret must be at least 32 characters"),
            (config["encryption_key"] is not None, "Encryption key is required"),
            (config["database_config"]["url"] != "sqlite:///stock_trading.db", "SQLite not recommended for production"),
            (config["security_settings"]["session_cookie_secure"], "Secure cookies required in production"),
        ]
        
        for check, message in validations:
            if not check:
                raise ValueError(f"Production validation failed: {message}")
        
        logger.info("✅ Production security validation passed")

class EnvironmentVariableTemplate:
    """Generate template for environment variables"""
    
    @staticmethod
    def generate_template() -> str:
        """Generate environment variable template"""
        template = """
# Target Capital Environment Configuration Template
# Copy this to .env and fill in the values

# Environment
ENVIRONMENT=development  # development, staging, production

# Security Keys (REQUIRED FOR PRODUCTION)
SESSION_SECRET=your-secure-session-secret-at-least-32-characters-long
BROKER_ENCRYPTION_KEY=your-base64-encoded-fernet-encryption-key

# Database Configuration
DATABASE_URL=postgresql://user:password@localhost:5432/tcapital
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30
DB_POOL_RECYCLE=300
DB_ECHO=false

# Read Replicas (Optional)
DB_READ_REPLICAS=2
DATABASE_READ_REPLICA_1=postgresql://user:password@replica1:5432/tcapital
DATABASE_READ_REPLICA_2=postgresql://user:password@replica2:5432/tcapital

# Redis Configuration
REDIS_URL=redis://localhost:6379
REDIS_MAX_CONNECTIONS=50
REDIS_HEALTH_CHECK_INTERVAL=30

# API Keys
OPENAI_API_KEY=your-openai-api-key
PERPLEXITY_API_KEY=your-perplexity-api-key
RAZORPAY_KEY_ID=your-razorpay-key-id
RAZORPAY_KEY_SECRET=your-razorpay-secret-key

# CDN Configuration
CDN_ENABLED=false
CDN_URL=https://your-cdn-domain.com

# Rate Limiting
RATE_LIMITING_ENABLED=true
RATE_LIMIT_PER_MINUTE=300

# CORS Settings
CORS_ORIGINS=http://localhost:3000,https://yourdomain.com
CORS_CREDENTIALS=true

# Logging
LOG_LEVEL=INFO
LOG_FILE=/var/log/tcapital/app.log

# Monitoring
SENTRY_DSN=your-sentry-dsn-for-error-tracking
PROMETHEUS_PORT=9090
"""
        return template.strip()

def setup_secure_environment():
    """Setup secure environment configuration"""
    config_manager = SecureEnvironmentConfig()
    
    try:
        config = config_manager.validate_and_get_config()
        logger.info("✅ Environment configuration validated successfully")
        return config
    except ValueError as e:
        logger.error(f"❌ Environment configuration error: {e}")
        raise

# Helper function to generate secure keys
def generate_secure_keys():
    """Generate secure keys for development"""
    session_secret = secrets.token_urlsafe(32)
    encryption_key = Fernet.generate_key().decode()
    
    print("Generated secure keys for development:")
    print(f"SESSION_SECRET={session_secret}")
    print(f"BROKER_ENCRYPTION_KEY={encryption_key}")
    print("\nAdd these to your environment variables or .env file")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "generate-keys":
        generate_secure_keys()
    elif len(sys.argv) > 1 and sys.argv[1] == "generate-template":
        print(EnvironmentVariableTemplate.generate_template())
    else:
        # Test configuration
        setup_secure_environment()
        print("Environment configuration is valid!")