"""
Simple WebSocket Test Server for React Integration
Minimal implementation to test React WebSocket components
"""

import asyncio
import websockets
import json
import logging
from datetime import datetime, timezone
import threading
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SimpleWebSocketServer:
    def __init__(self):
        self.clients = set()
        self.running = False
    
    async def register_client(self, websocket):
        self.clients.add(websocket)
        logger.info(f"Client connected. Total clients: {len(self.clients)}")
    
    async def unregister_client(self, websocket):
        self.clients.discard(websocket)
        logger.info(f"Client disconnected. Total clients: {len(self.clients)}")
    
    async def broadcast_market_data(self):
        """Send simulated market data to all clients"""
        if not self.clients:
            return
        
        # Simulated market data
        market_data = {
            'type': 'market_data',
            'data': {
                'indices': {
                    'NIFTY': {
                        'value': '25,041.10',
                        'change': 0.50
                    },
                    'BANKNIFTY': {
                        'value': '51,234.80', 
                        'change': -0.30
                    },
                    'SENSEX': {
                        'value': '81,523.45',
                        'change': 0.75
                    }
                },
                'stocks': {
                    'RELIANCE': {
                        'price': 2845.60,
                        'change': 2.3,
                        'volume': '2.5M'
                    },
                    'TCS': {
                        'price': 4125.30,
                        'change': 1.8,
                        'volume': '1.8M'
                    },
                    'INFY': {
                        'price': 1756.25,
                        'change': -0.5,
                        'volume': '3.2M'
                    }
                }
            },
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        message = json.dumps(market_data)
        disconnected = set()
        
        for client in self.clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
            except Exception as e:
                logger.error(f"Broadcast error: {e}")
                disconnected.add(client)
        
        # Clean up disconnected clients
        for client in disconnected:
            await self.unregister_client(client)
    
    async def handle_client(self, websocket, path):
        """Handle individual client connections"""
        await self.register_client(websocket)
        
        try:
            # Send initial data
            await self.send_initial_data(websocket)
            
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_message(websocket, data)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Invalid JSON'
                    }))
                except Exception as e:
                    logger.error(f"Message handling error: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister_client(websocket)
    
    async def send_initial_data(self, websocket):
        """Send initial market data to new client"""
        initial_data = {
            'type': 'welcome',
            'message': 'Connected to Capulse WebSocket',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        await websocket.send(json.dumps(initial_data))
        
        # Send current market data
        await self.broadcast_market_data()
    
    async def handle_message(self, websocket, data):
        """Handle incoming messages"""
        message_type = data.get('type')
        
        if message_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))
        elif message_type == 'subscribe':
            symbols = data.get('symbols', [])
            logger.info(f"Client subscribed to: {symbols}")
        elif message_type == 'refresh_data':
            await self.broadcast_market_data()
    
    def start_data_updates(self):
        """Start background data updates"""
        def update_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            while self.running:
                try:
                    loop.run_until_complete(self.broadcast_market_data())
                    time.sleep(10)  # Update every 10 seconds
                except Exception as e:
                    logger.error(f"Update error: {e}")
                    time.sleep(30)
        
        thread = threading.Thread(target=update_loop, daemon=True)
        thread.start()
        logger.info("Market data update thread started")

async def main():
    server = SimpleWebSocketServer()
    server.running = True
    
    # Start data updates
    server.start_data_updates()
    
    logger.info("🚀 Starting Simple WebSocket Server on port 8001...")
    
    async with websockets.serve(
        server.handle_client,
        "0.0.0.0", 
        8001,
        ping_interval=30,
        ping_timeout=10
    ):
        logger.info("✅ WebSocket server started successfully on ws://localhost:8001")
        logger.info("Ready for React component connections")
        
        # Keep server running
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 WebSocket server stopped")
    except Exception as e:
        logger.error(f"❌ Server error: {e}")