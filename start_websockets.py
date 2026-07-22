#!/usr/bin/env python3
"""
WebSocket Server Startup Script for Capulse
Starts all WebSocket servers alongside the Flask application
"""

import asyncio
import logging
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WebSocketLauncher:
    """Manages WebSocket server lifecycle"""
    
    def __init__(self):
        self.servers = []
        self.running = True
        self.executor = ThreadPoolExecutor(max_workers=3)
        
    async def start_websocket_server(self, server_func, server_name):
        """Start a single WebSocket server"""
        try:
            logger.info(f"🚀 Starting {server_name} WebSocket server...")
            server = await server_func()
            self.servers.append(server)
            logger.info(f"✅ {server_name} WebSocket server started successfully")
            return server
        except Exception as e:
            logger.error(f"❌ Failed to start {server_name} WebSocket server: {e}")
            raise
    
    async def start_all_servers(self):
        """Start all WebSocket servers concurrently"""
        try:
            from websocket_servers import (
                start_market_data_server,
                start_trading_updates_server,
                start_portfolio_updates_server
            )
            
            logger.info("🌐 Starting Capulse WebSocket Infrastructure...")
            
            # Start all servers concurrently
            await asyncio.gather(
                self.start_websocket_server(start_market_data_server, "MarketData"),
                self.start_websocket_server(start_trading_updates_server, "TradingUpdates"),
                self.start_websocket_server(start_portfolio_updates_server, "PortfolioUpdates"),
                return_exceptions=True
            )
            
            logger.info("✅ All WebSocket servers started successfully")
            logger.info("📡 WebSocket servers listening on:")
            logger.info("   📊 Market Data: ws://localhost:8001")
            logger.info("   📈 Trading Updates: ws://localhost:8002")
            logger.info("   💼 Portfolio Updates: ws://localhost:8003")
            
            # Setup graceful shutdown
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, self.signal_handler)
            
            # Keep servers running
            while self.running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Failed to start WebSocket infrastructure: {e}")
            raise
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"🛑 Received signal {signum}, shutting down...")
        self.running = False
    
    async def shutdown(self):
        """Gracefully shutdown all servers"""
        logger.info("🛑 Shutting down WebSocket servers...")
        
        for server in self.servers:
            if server:
                server.close()
                await server.wait_closed()
        
        logger.info("✅ All WebSocket servers shut down successfully")

def start_websockets_in_background():
    """Start WebSocket servers in a background thread"""
    def run_servers():
        try:
            launcher = WebSocketLauncher()
            asyncio.run(launcher.start_all_servers())
        except KeyboardInterrupt:
            logger.info("🛑 WebSocket servers stopped by user")
        except Exception as e:
            logger.error(f"❌ WebSocket servers failed: {e}")
    
    thread = threading.Thread(target=run_servers, daemon=True, name="websocket-launcher")
    thread.start()
    logger.info("🚀 WebSocket servers started in background thread")
    return thread

def main():
    """Main function to run WebSocket servers"""
    try:
        logger.info("🚀 Starting Capulse WebSocket Server Launcher...")
        
        launcher = WebSocketLauncher()
        asyncio.run(launcher.start_all_servers())
        
    except KeyboardInterrupt:
        logger.info("🛑 WebSocket servers stopped by user")
    except Exception as e:
        logger.error(f"❌ WebSocket launcher failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()