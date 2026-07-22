"""
WebSocket Servers for Real-time Data Streaming
Supports React frontend with high-performance real-time updates
"""

import asyncio
import websockets
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Set, Dict, Any
import threading
import time
from services.nse_service import NSEService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebSocketServer:
    def __init__(self, port: int, name: str):
        self.port = port
        self.name = name
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.subscriptions: Dict[websockets.WebSocketServerProtocol, Set[str]] = {}
        self.server = None
        self.running = False
        
    async def register(self, websocket):
        """Register a new client"""
        self.clients.add(websocket)
        self.subscriptions[websocket] = set()
        logger.info(f"🔌 Client connected to {self.name} server - Total: {len(self.clients)}")
        
    async def unregister(self, websocket):
        """Unregister a client"""
        self.clients.discard(websocket)
        self.subscriptions.pop(websocket, None)
        logger.info(f"🔌 Client disconnected from {self.name} server - Total: {len(self.clients)}")
        
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        if not self.clients:
            return
            
        message_str = json.dumps(message)
        disconnected = set()
        
        for client in self.clients:
            try:
                await client.send(message_str)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
            except Exception as e:
                logger.error(f"Broadcast error: {e}")
                disconnected.add(client)
        
        # Clean up disconnected clients
        for client in disconnected:
            await self.unregister(client)
    
    async def send_to_client(self, websocket, message: dict):
        """Send message to specific client"""
        try:
            await websocket.send(json.dumps(message))
        except websockets.exceptions.ConnectionClosed:
            await self.unregister(websocket)
        except Exception as e:
            logger.error(f"Send to client error: {e}")

class MarketDataServer(WebSocketServer):
    def __init__(self):
        super().__init__(8001, "Market Data")
        self.nse_service = NSEService()
        self.market_data = {}
        self.last_update = None
        
    async def handle_client(self, websocket, path):
        """Handle individual client connections"""
        await self.register(websocket)
        
        try:
            # Send initial market data
            await self.send_initial_data(websocket)
            
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_message(websocket, data)
                except json.JSONDecodeError:
                    await self.send_error(websocket, "Invalid JSON")
                except Exception as e:
                    logger.error(f"Message handling error: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)
    
    async def send_initial_data(self, websocket):
        """Send current market data to newly connected client"""
        if self.market_data:
            await self.send_to_client(websocket, {
                'type': 'market_data',
                'data': self.market_data,
                'timestamp': self.last_update
            })
    
    async def handle_message(self, websocket, data):
        """Handle incoming messages from clients"""
        message_type = data.get('type')
        
        if message_type == 'subscribe':
            symbols = data.get('symbols', [])
            self.subscriptions[websocket].update(symbols)
            logger.info(f"Client subscribed to: {symbols}")
            
        elif message_type == 'unsubscribe':
            symbols = data.get('symbols', [])
            self.subscriptions[websocket].difference_update(symbols)
            logger.info(f"Client unsubscribed from: {symbols}")
            
        elif message_type == 'ping':
            await self.send_to_client(websocket, {'type': 'pong'})
            
        elif message_type == 'refresh_data':
            await self.send_initial_data(websocket)
    
    async def send_error(self, websocket, error_message):
        """Send error message to client"""
        await self.send_to_client(websocket, {
            'type': 'error',
            'message': error_message
        })
    
    def start_data_updates(self):
        """Start background data updates"""
        def update_loop():
            while self.running:
                try:
                    asyncio.run(self.update_market_data())
                    time.sleep(10)  # Update every 10 seconds
                except Exception as e:
                    logger.error(f"Market data update error: {e}")
                    time.sleep(30)  # Wait longer on error
        
        thread = threading.Thread(target=update_loop, daemon=True)
        thread.start()
        logger.info("Market data update thread started")
    
    async def update_market_data(self):
        """Update market data from NSE"""
        try:
            # Get indices data
            indices_result = self.nse_service.get_market_indices()
            
            # Get popular stocks
            popular_stocks = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK']
            stocks_data = {}
            
            for symbol in popular_stocks:
                try:
                    stock_result = self.nse_service.get_stock_quote(symbol)
                    if stock_result.get('success'):
                        stocks_data[symbol] = stock_result['data']
                except Exception as e:
                    logger.warning(f"Failed to fetch {symbol}: {e}")
            
            # Update market data
            self.market_data = {
                'indices': indices_result.get('data', {}) if indices_result.get('success') else {},
                'stocks': stocks_data,
                'market_status': 'open' if self.is_market_open() else 'closed'
            }
            
            self.last_update = datetime.now(timezone.utc).isoformat()
            
            # Broadcast to all clients
            await self.broadcast({
                'type': 'market_data',
                'data': self.market_data,
                'timestamp': self.last_update
            })
            
        except Exception as e:
            logger.error(f"Market data update failed: {e}")
    
    def is_market_open(self):
        """Check if market is currently open"""
        now = datetime.now(timezone.utc)
        hour = now.hour
        minute = now.minute
        day = now.weekday()
        
        # Indian market hours: 9:15 AM to 3:30 PM, Monday to Friday
        is_weekday = day < 5
        is_market_hours = (hour > 9 or (hour == 9 and minute >= 15)) and \
                         (hour < 15 or (hour == 15 and minute <= 30))
        
        return is_weekday and is_market_hours

class TradingUpdatesServer(WebSocketServer):
    def __init__(self):
        super().__init__(8002, "Trading Updates")
        
    async def handle_client(self, websocket, path):
        """Handle trading update connections"""
        await self.register(websocket)
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_trading_message(websocket, data)
                except json.JSONDecodeError:
                    await self.send_to_client(websocket, {
                        'type': 'error',
                        'message': 'Invalid JSON'
                    })
                except Exception as e:
                    logger.error(f"Trading message error: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)
    
    async def handle_trading_message(self, websocket, data):
        """Handle trading-related messages"""
        message_type = data.get('type')
        
        if message_type == 'ping':
            await self.send_to_client(websocket, {'type': 'pong'})
            
        elif message_type == 'subscribe_user':
            user_id = data.get('user_id')
            if user_id:
                # Store user subscription
                self.subscriptions[websocket].add(f"user_{user_id}")
                logger.info(f"Client subscribed to user updates: {user_id}")

class PortfolioUpdatesServer(WebSocketServer):
    def __init__(self):
        super().__init__(8003, "Portfolio Updates")
        
    async def handle_client(self, websocket, path):
        """Handle portfolio update connections"""
        await self.register(websocket)
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_portfolio_message(websocket, data)
                except json.JSONDecodeError:
                    await self.send_to_client(websocket, {
                        'type': 'error',
                        'message': 'Invalid JSON'
                    })
                except Exception as e:
                    logger.error(f"Portfolio message error: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)
    
    async def handle_portfolio_message(self, websocket, data):
        """Handle portfolio-related messages"""
        message_type = data.get('type')
        
        if message_type == 'ping':
            await self.send_to_client(websocket, {'type': 'pong'})
            
        elif message_type == 'sync_portfolio':
            user_id = data.get('user_id')
            if user_id:
                # Trigger portfolio sync (implement as needed)
                await self.send_to_client(websocket, {
                    'type': 'portfolio_sync_started',
                    'user_id': user_id
                })

# Global server instances
market_server = MarketDataServer()
trading_server = TradingUpdatesServer()
portfolio_server = PortfolioUpdatesServer()

async def start_market_data_server():
    """Start market data WebSocket server"""
    try:
        market_server.running = True
        market_server.start_data_updates()
        
        logger.info(f"🚀 Starting {market_server.name} server on port {market_server.port}")
        
        server = await websockets.serve(
            market_server.handle_client,
            "0.0.0.0",
            market_server.port,
            ping_interval=30,
            ping_timeout=10
        )
        
        market_server.server = server
        logger.info(f"✅ {market_server.name} server started successfully")
        
        return server
        
    except Exception as e:
        logger.error(f"❌ Failed to start {market_server.name} server: {e}")
        raise

async def start_trading_updates_server():
    """Start trading updates WebSocket server"""
    try:
        trading_server.running = True
        
        logger.info(f"🚀 Starting {trading_server.name} server on port {trading_server.port}")
        
        server = await websockets.serve(
            trading_server.handle_client,
            "0.0.0.0",
            trading_server.port,
            ping_interval=30,
            ping_timeout=10
        )
        
        trading_server.server = server
        logger.info(f"✅ {trading_server.name} server started successfully")
        
        return server
        
    except Exception as e:
        logger.error(f"❌ Failed to start {trading_server.name} server: {e}")
        raise

async def start_portfolio_updates_server():
    """Start portfolio updates WebSocket server"""
    try:
        portfolio_server.running = True
        
        logger.info(f"🚀 Starting {portfolio_server.name} server on port {portfolio_server.port}")
        
        server = await websockets.serve(
            portfolio_server.handle_client,
            "0.0.0.0",
            portfolio_server.port,
            ping_interval=30,
            ping_timeout=10
        )
        
        portfolio_server.server = server
        logger.info(f"✅ {portfolio_server.name} server started successfully")
        
        return server
        
    except Exception as e:
        logger.error(f"❌ Failed to start {portfolio_server.name} server: {e}")
        raise

async def start_all_websocket_servers():
    """Start all WebSocket servers concurrently"""
    try:
        servers = await asyncio.gather(
            start_market_data_server(),
            start_trading_updates_server(),
            start_portfolio_updates_server(),
            return_exceptions=True
        )
        
        logger.info("🌐 All WebSocket servers started successfully")
        
        # Setup graceful shutdown
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda s, f: asyncio.create_task(shutdown_servers()))
        
        # Keep servers running
        await asyncio.Future()  # Run forever
        
    except Exception as e:
        logger.error(f"❌ Failed to start WebSocket servers: {e}")
        raise

async def shutdown_servers():
    """Gracefully shutdown all servers"""
    logger.info("🛑 Shutting down WebSocket servers...")
    
    # Stop all servers
    for server_obj in [market_server, trading_server, portfolio_server]:
        server_obj.running = False
        if server_obj.server:
            server_obj.server.close()
            await server_obj.server.wait_closed()
    
    logger.info("✅ All WebSocket servers shut down successfully")
    sys.exit(0)

def run_websocket_servers():
    """Main function to run WebSocket servers"""
    try:
        logger.info("🚀 Starting Capulse WebSocket Infrastructure...")
        asyncio.run(start_all_websocket_servers())
    except KeyboardInterrupt:
        logger.info("🛑 WebSocket servers stopped by user")
    except Exception as e:
        logger.error(f"❌ WebSocket servers failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_websocket_servers()