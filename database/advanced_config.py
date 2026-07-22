"""
Advanced Database Configuration
Implements read replicas, connection pooling, and performance optimizations
"""

import os
import logging
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
from sqlalchemy import create_engine, event, pool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool, QueuePool
import asyncpg
import asyncio

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Advanced database manager with read/write splitting"""
    
    def __init__(self):
        self.write_engine = None
        self.read_engines: List[Any] = []
        self.write_session_factory = None
        self.read_session_factory = None
        self._read_engine_index = 0
        
        # Configuration
        self.config = self._load_config()
        
        # Initialize engines
        self._initialize_engines()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load database configuration from environment"""
        return {
            # Primary database (write operations)
            "primary_url": os.environ.get("DATABASE_URL", "sqlite:///stock_trading.db"),
            
            # Read replica URLs
            "read_replicas": self._get_read_replica_urls(),
            
            # Connection pool settings
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "20")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "30")),
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "300")),
            "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
            "pool_pre_ping": True,
            
            # Performance settings
            "echo": os.environ.get("DB_ECHO", "false").lower() == "true",
            "echo_pool": os.environ.get("DB_ECHO_POOL", "false").lower() == "true",
            
            # Connection arguments
            "connect_args": self._get_connection_args()
        }
    
    def _get_read_replica_urls(self) -> List[str]:
        """Get read replica URLs from environment"""
        replicas = []
        replica_count = int(os.environ.get("DB_READ_REPLICAS", "0"))
        
        for i in range(replica_count):
            replica_url = os.environ.get(f"DATABASE_READ_REPLICA_{i+1}")
            if replica_url:
                replicas.append(replica_url)
        
        return replicas
    
    def _get_connection_args(self) -> Dict[str, Any]:
        """Get database-specific connection arguments"""
        if self.config["primary_url"].startswith("postgresql"):
            return {
                "server_settings": {
                    "jit": "off",  # Disable JIT for better performance on small queries
                    "application_name": "Capulse-API",
                    "tcp_keepalives_idle": "300",
                    "tcp_keepalives_interval": "30",
                    "tcp_keepalives_count": "3"
                },
                "command_timeout": 60
            }
        return {}
    
    def _prepare_url_for_async(self, url: str) -> str:
        """Convert database URL for async usage"""
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://")
        elif url.startswith("postgresql+psycopg2://"):
            return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        return url
    
    def _initialize_engines(self):
        """Initialize database engines"""
        try:
            # Primary (write) engine
            primary_url = self._prepare_url_for_async(self.config["primary_url"])
            
            self.write_engine = create_async_engine(
                primary_url,
                poolclass=QueuePool,
                pool_size=self.config["pool_size"],
                max_overflow=self.config["max_overflow"],
                pool_recycle=self.config["pool_recycle"],
                pool_timeout=self.config["pool_timeout"],
                pool_pre_ping=self.config["pool_pre_ping"],
                echo=self.config["echo"],
                echo_pool=self.config["echo_pool"],
                connect_args=self.config["connect_args"]
            )
            
            # Read replica engines
            for replica_url in self.config["read_replicas"]:
                replica_url_async = self._prepare_url_for_async(replica_url)
                read_engine = create_async_engine(
                    replica_url_async,
                    poolclass=QueuePool,
                    pool_size=max(self.config["pool_size"] // 2, 5),  # Smaller pool for replicas
                    max_overflow=max(self.config["max_overflow"] // 2, 10),
                    pool_recycle=self.config["pool_recycle"],
                    pool_timeout=self.config["pool_timeout"],
                    pool_pre_ping=self.config["pool_pre_ping"],
                    echo=self.config["echo"],
                    connect_args=self.config["connect_args"]
                )
                self.read_engines.append(read_engine)
            
            # Session factories
            self.write_session_factory = async_sessionmaker(
                self.write_engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            if self.read_engines:
                # Use first read replica for read sessions
                # In production, you might want to implement load balancing
                self.read_session_factory = async_sessionmaker(
                    self.read_engines[0],
                    class_=AsyncSession,
                    expire_on_commit=False
                )
            else:
                # Fallback to write engine for reads
                self.read_session_factory = self.write_session_factory
            
            logger.info(f"✅ Database initialized - Write: 1 engine, Read: {len(self.read_engines)} engines")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize database engines: {e}")
            raise
    
    def get_write_session(self):
        """Get write session (primary database)"""
        return self.write_session_factory()
    
    def get_read_session(self):
        """Get read session (replica if available, otherwise primary)"""
        if self.read_engines:
            # Simple round-robin load balancing for read replicas
            engine_index = self._read_engine_index % len(self.read_engines)
            self._read_engine_index += 1
            
            selected_engine = self.read_engines[engine_index]
            return async_sessionmaker(
                selected_engine,
                class_=AsyncSession,
                expire_on_commit=False
            )()
        
        return self.read_session_factory()
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check for all database connections"""
        health_status = {
            "write_engine": {"status": "unknown", "latency_ms": None},
            "read_engines": []
        }
        
        try:
            # Check write engine
            start_time = asyncio.get_event_loop().time()
            async with self.write_engine.begin() as conn:
                await conn.execute("SELECT 1")
            end_time = asyncio.get_event_loop().time()
            
            health_status["write_engine"] = {
                "status": "healthy",
                "latency_ms": round((end_time - start_time) * 1000, 2)
            }
            
        except Exception as e:
            health_status["write_engine"] = {
                "status": "unhealthy",
                "error": str(e)
            }
        
        # Check read engines
        for i, read_engine in enumerate(self.read_engines):
            try:
                start_time = asyncio.get_event_loop().time()
                async with read_engine.begin() as conn:
                    await conn.execute("SELECT 1")
                end_time = asyncio.get_event_loop().time()
                
                health_status["read_engines"].append({
                    "replica_id": i + 1,
                    "status": "healthy",
                    "latency_ms": round((end_time - start_time) * 1000, 2)
                })
                
            except Exception as e:
                health_status["read_engines"].append({
                    "replica_id": i + 1,
                    "status": "unhealthy",
                    "error": str(e)
                })
        
        return health_status
    
    async def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        stats = {
            "write_engine": {},
            "read_engines": []
        }
        
        # Write engine stats
        if hasattr(self.write_engine.pool, 'size'):
            stats["write_engine"] = {
                "pool_size": self.write_engine.pool.size(),
                "checked_in": self.write_engine.pool.checkedin(),
                "checked_out": self.write_engine.pool.checkedout(),
                "overflow": self.write_engine.pool.overflow(),
                "invalid": self.write_engine.pool.invalid()
            }
        
        # Read engines stats
        for i, read_engine in enumerate(self.read_engines):
            if hasattr(read_engine.pool, 'size'):
                stats["read_engines"].append({
                    "replica_id": i + 1,
                    "pool_size": read_engine.pool.size(),
                    "checked_in": read_engine.pool.checkedin(),
                    "checked_out": read_engine.pool.checkedout(),
                    "overflow": read_engine.pool.overflow(),
                    "invalid": read_engine.pool.invalid()
                })
        
        return stats
    
    async def close(self):
        """Close all database connections"""
        try:
            if self.write_engine:
                await self.write_engine.dispose()
                logger.info("✅ Write engine closed")
            
            for i, read_engine in enumerate(self.read_engines):
                await read_engine.dispose()
                logger.info(f"✅ Read engine {i+1} closed")
                
        except Exception as e:
            logger.error(f"❌ Error closing database connections: {e}")

# Global database manager instance
db_manager = DatabaseManager()

# Dependency functions for FastAPI
async def get_write_db():
    """FastAPI dependency for write database session"""
    async with db_manager.get_write_session() as session:
        try:
            yield session
        finally:
            await session.close()

async def get_read_db():
    """FastAPI dependency for read database session"""
    async with db_manager.get_read_session() as session:
        try:
            yield session
        finally:
            await session.close()

# Context manager for transaction handling
@asynccontextmanager
async def database_transaction():
    """Context manager for database transactions"""
    async with db_manager.get_write_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()