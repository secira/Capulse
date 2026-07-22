"""
FastAPI Application for High-Performance API Endpoints
Handles async operations, advanced caching, and input validation
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBearer
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator, EmailStr
import redis.asyncio as redis
import asyncpg
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Security configuration
class SecurityConfig:
    """Enhanced security configuration"""
    
    @staticmethod
    def get_session_secret() -> str:
        """Get secure session secret"""
        secret = os.environ.get("SESSION_SECRET")
        if not secret or secret == "dev-secret-key-change-in-production":
            if os.environ.get("ENVIRONMENT") == "production":
                raise ValueError("Production requires secure SESSION_SECRET")
            # Generate secure development key
            import secrets
            secret = secrets.token_urlsafe(32)
            logger.warning(f"Using generated session secret for development: {secret}")
        return secret
    
    @staticmethod
    def get_encryption_key() -> bytes:
        """Get secure encryption key"""
        key = os.environ.get("BROKER_ENCRYPTION_KEY")
        if not key:
            if os.environ.get("ENVIRONMENT") == "production":
                raise ValueError("Production requires BROKER_ENCRYPTION_KEY")
            # Generate secure development key
            key = Fernet.generate_key()
            logger.warning("Using generated encryption key for development")
        return key if isinstance(key, bytes) else key.encode()

# Database configuration with advanced connection pooling
class DatabaseConfig:
    """Advanced database configuration with read replicas"""
    
    def __init__(self):
        self.primary_url = os.environ.get("DATABASE_URL", "sqlite:///stock_trading.db")
        self.read_replica_urls = []
        
        # Check for read replicas
        replica_count = int(os.environ.get("DB_READ_REPLICAS", "0"))
        for i in range(replica_count):
            replica_url = os.environ.get(f"DATABASE_READ_REPLICA_{i+1}")
            if replica_url:
                self.read_replica_urls.append(replica_url)
        
        # Convert PostgreSQL URLs for async
        if self.primary_url.startswith('postgresql://'):
            self.primary_url = self.primary_url.replace('postgresql://', 'postgresql+asyncpg://')
            self.read_replica_urls = [
                url.replace('postgresql://', 'postgresql+asyncpg://') 
                for url in self.read_replica_urls
            ]
    
    def get_engine_options(self):
        """Get optimized engine options"""
        return {
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "20")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "30")),
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "300")),
            "pool_pre_ping": True,
            "connect_args": {
                "server_settings": {
                    "jit": "off",
                    "application_name": "Capulse-FastAPI"
                }
            }
        }

# Pydantic models for input validation
class UserProfileRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    
    @validator('first_name', 'last_name')
    def validate_names(cls, v):
        if v and (len(v) < 2 or len(v) > 50):
            raise ValueError('Name must be between 2 and 50 characters')
        return v

class BrokerAccountRequest(BaseModel):
    broker_type: str
    client_id: str
    access_token: str
    api_secret: Optional[str] = None
    totp_secret: Optional[str] = None
    
    @validator('broker_type')
    def validate_broker_type(cls, v):
        allowed_brokers = ['ZERODHA', 'DHAN', 'ANGEL_ONE', 'UPSTOX', 'FYERS', 
                          'GROWW', 'ICICIDIRECT', 'HDFC', 'KOTAK', 'FIVEPAISA', 
                          'CHOICE', 'GOODWILL']
        if v not in allowed_brokers:
            raise ValueError(f'Invalid broker type. Must be one of: {allowed_brokers}')
        return v
    
    @validator('client_id', 'access_token')
    def validate_credentials(cls, v):
        if not v or len(v.strip()) < 3:
            raise ValueError('Invalid credentials provided')
        return v.strip()

class OrderRequest(BaseModel):
    symbol: str
    quantity: int
    price: Optional[float] = None
    order_type: str = "MARKET"
    transaction_type: str
    product_type: str = "MIS"
    
    @validator('symbol')
    def validate_symbol(cls, v):
        if not v or len(v) < 2:
            raise ValueError('Invalid symbol')
        return v.upper()
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError('Quantity must be positive')
        return v
    
    @validator('transaction_type')
    def validate_transaction_type(cls, v):
        if v not in ['BUY', 'SELL']:
            raise ValueError('Transaction type must be BUY or SELL')
        return v.upper()

# Cache configuration
class CacheConfig:
    """Redis cache configuration"""
    
    def __init__(self):
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.redis_pool = None
    
    async def get_redis(self) -> redis.Redis:
        """Get Redis connection with connection pooling"""
        if not self.redis_pool:
            self.redis_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                max_connections=50,
                retry_on_timeout=True
            )
        return redis.Redis(connection_pool=self.redis_pool)

# Global configuration instances
security_config = SecurityConfig()
db_config = DatabaseConfig()
cache_config = CacheConfig()

# Database setup
async_engine = create_async_engine(
    db_config.primary_url,
    **db_config.get_engine_options()
)

AsyncSessionLocal = sessionmaker(
    async_engine, 
    class_=AsyncSession,
    expire_on_commit=False
)

# Read replica engines if available
read_engines = []
for replica_url in db_config.read_replica_urls:
    read_engine = create_async_engine(replica_url, **db_config.get_engine_options())
    read_engines.append(read_engine)

async def get_db_session():
    """Get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def get_read_db_session():
    """Get read-only database session (uses replica if available)"""
    if read_engines:
        engine = read_engines[0]  # Simple load balancing
        session = AsyncSession(engine)
    else:
        session = AsyncSessionLocal()
    
    try:
        yield session
    finally:
        await session.close()

# Authentication dependency
security = HTTPBearer()

async def get_current_user(token: str = Depends(security)):
    """Get current authenticated user"""
    # Implement JWT or session-based authentication
    # This is a placeholder - implement actual authentication
    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("🚀 Starting Capulse FastAPI Application")
    
    # Initialize Redis cache
    redis_client = await cache_config.get_redis()
    await redis_client.ping()
    logger.info("✅ Redis cache connected")
    
    # Initialize database
    try:
        # Test database connection
        async with async_engine.begin() as conn:
            await conn.execute("SELECT 1")
        logger.info("✅ Primary database connected")
        
        # Test read replicas
        for i, engine in enumerate(read_engines):
            async with engine.begin() as conn:
                await conn.execute("SELECT 1")
            logger.info(f"✅ Read replica {i+1} connected")
            
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("🔄 Shutting down FastAPI Application")
    await async_engine.dispose()
    for engine in read_engines:
        await engine.dispose()

# Create FastAPI application
app = FastAPI(
    title="Capulse Trading API",
    description="High-performance async trading platform API",
    version="2.0.0",
    lifespan=lifespan
)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# Cache decorator
def cache_response(expire_seconds: int = 300):
    """Cache decorator for API responses"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            request = kwargs.get('request')
            if request:
                cache_key = f"api_cache:{request.url.path}:{hash(str(kwargs))}"
                redis_client = await cache_config.get_redis()
                
                # Try to get from cache
                cached = await redis_client.get(cache_key)
                if cached:
                    return JSONResponse(content=eval(cached))
                
                # Execute function and cache result
                result = await func(*args, **kwargs)
                if isinstance(result, JSONResponse):
                    await redis_client.setex(
                        cache_key, 
                        expire_seconds, 
                        str(result.body.decode())
                    )
                return result
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Check database
        async with async_engine.begin() as conn:
            await conn.execute("SELECT 1")
        
        # Check Redis
        redis_client = await cache_config.get_redis()
        await redis_client.ping()
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected",
            "cache": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
        )

# Rate limiting middleware
@app.middleware("http")
async def rate_limiting_middleware(request: Request, call_next):
    """Rate limiting middleware"""
    client_ip = request.client.host
    redis_client = await cache_config.get_redis()
    
    # Define rate limits per endpoint type
    rate_limits = {
        "/api/trading/": {"limit": 100, "window": 60},  # 100 trades per minute
        "/api/market/": {"limit": 1000, "window": 60},  # 1000 market data requests per minute
        "/api/": {"limit": 300, "window": 60},  # 300 general API requests per minute
    }
    
    # Find applicable rate limit
    rate_limit = None
    for pattern, limit_config in rate_limits.items():
        if request.url.path.startswith(pattern):
            rate_limit = limit_config
            break
    
    if rate_limit:
        key = f"rate_limit:{client_ip}:{request.url.path.split('/')[1]}"
        current_requests = await redis_client.get(key)
        
        if current_requests and int(current_requests) >= rate_limit["limit"]:
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded", "retry_after": rate_limit["window"]}
            )
        
        # Increment counter
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, rate_limit["window"])
        await pipe.execute()
    
    response = await call_next(request)
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "fastapi_app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        workers=4
    )