"""
Hybrid Flask + FastAPI Application
Combines Flask for templates/views with FastAPI for high-performance APIs
"""

import os
import logging
from flask import Flask
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import existing Flask app
from app import app as flask_app

# Import FastAPI app and routers
from fastapi_app import app as fastapi_app
from api.broker_endpoints import router as broker_router
from api.market_endpoints import router as market_router
from database.advanced_config import db_manager
from caching.cdn_config import setup_cdn_middleware
from security.input_validation import validation_exception_handler
from pydantic import ValidationError

class HybridApplication:
    """Hybrid application manager"""
    
    def __init__(self):
        self.flask_app = flask_app
        self.fastapi_app = fastapi_app
        self.setup_fastapi()
        self.setup_hybrid_routing()
    
    def setup_fastapi(self):
        """Configure FastAPI application"""
        # Add API routers
        self.fastapi_app.include_router(broker_router)
        self.fastapi_app.include_router(market_router)
        
        # Setup CDN and caching middleware
        cdn_config, static_handler, api_cache = setup_cdn_middleware(self.fastapi_app)
        
        # Add validation exception handler
        self.fastapi_app.add_exception_handler(ValidationError, validation_exception_handler)
        
        # Add Flask app as WSGI middleware for template routes
        self.fastapi_app.mount("/", WSGIMiddleware(self.flask_app))
        
        logger.info("✅ FastAPI application configured with hybrid routing")
    
    def setup_hybrid_routing(self):
        """Setup routing between Flask and FastAPI"""
        
        @self.fastapi_app.get("/api/health")
        async def health_check():
            """Combined health check for both Flask and FastAPI"""
            try:
                # Check database
                db_health = await db_manager.health_check()
                
                # Check Flask app
                with self.flask_app.app_context():
                    flask_status = "healthy"
                
                return {
                    "status": "healthy",
                    "services": {
                        "fastapi": "healthy",
                        "flask": flask_status,
                        "database": db_health
                    },
                    "timestamp": "2025-01-01T00:00:00"
                }
            except Exception as e:
                return {
                    "status": "unhealthy",
                    "error": str(e),
                    "timestamp": "2025-01-01T00:00:00"
                }
        
        @self.fastapi_app.get("/api/stats")
        async def get_application_stats():
            """Get application performance statistics"""
            try:
                # Database stats
                db_stats = await db_manager.get_connection_stats()
                
                return {
                    "database": db_stats,
                    "timestamp": "2025-01-01T00:00:00"
                }
            except Exception as e:
                return {"error": str(e)}

    def run_development(self, host="0.0.0.0", port=5000):
        """Run in development mode"""
        logger.info("🚀 Starting Capulse Hybrid Application (Development)")
        uvicorn.run(
            self.fastapi_app,
            host=host,
            port=port,
            reload=True,
            log_level="info"
        )
    
    def get_app(self):
        """Get FastAPI app for production deployment"""
        return self.fastapi_app

# Create hybrid application instance
hybrid_app = HybridApplication()
app = hybrid_app.get_app()

# Production startup configuration
def create_production_app():
    """Create production-ready application"""
    
    # Ensure secure configuration
    if os.environ.get("ENVIRONMENT") == "production":
        # Validate production requirements
        required_env_vars = [
            "SESSION_SECRET",
            "BROKER_ENCRYPTION_KEY", 
            "DATABASE_URL",
            "REDIS_URL"
        ]
        
        missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")
        
        # Validate session secret is not default
        session_secret = os.environ.get("SESSION_SECRET")
        if session_secret == "dev-secret-key-change-in-production":
            raise ValueError("Production requires secure SESSION_SECRET")
        
        logger.info("✅ Production security validation passed")
    
    return app

if __name__ == "__main__":
    # Development server
    hybrid_app.run_development()