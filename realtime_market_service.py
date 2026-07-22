"""
Real-time Market Data Service for Capulse
High-performance market data streaming with WebSocket support and caching
"""

import asyncio
import websockets
import json
import logging
import redis
import aioredis
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set, Optional
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RealTimeMarketService:
    """
    High-performance real-time market data service
    """
    
    def __init__(self):
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.redis_client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        self.subscribers = {}  # WebSocket connections by user
        self.subscribed_symbols = set()
        self.market_data_cache = {}
        self.is_market_open = False
        self.executor = ThreadPoolExecutor(max_workers=4)
        
    async def start_service(self):
        """Start the real-time market data service"""
        logger.info("Starting Real-time Market Data Service")
        
        # Start market hours monitoring
        asyncio.create_task(self.monitor_market_hours())
        
        # Start data fetching loops
        asyncio.create_task(self.fetch_indices_data())
        asyncio.create_task(self.fetch_stock_data())
        
        # Start WebSocket server
        await self.start_websocket_server()
    
    async def monitor_market_hours(self):
        """Monitor Indian market hours (9:15 AM - 3:30 PM IST)"""
        # IST is UTC+5:30
        IST = timezone(timedelta(hours=5, minutes=30))
        
        while True:
            try:
                # Get current time in IST
                current_time_utc = datetime.now(timezone.utc)
                current_time_ist = current_time_utc.astimezone(IST)
                
                # Check if current time is within market hours (IST)
                market_start = current_time_ist.replace(hour=9, minute=15, second=0, microsecond=0)
                market_end = current_time_ist.replace(hour=15, minute=30, second=0, microsecond=0)
                
                # Skip weekends
                is_weekday = current_time_ist.weekday() < 5
                is_market_time = market_start <= current_time_ist <= market_end
                
                self.is_market_open = is_weekday and is_market_time
                
                # Broadcast market status
                await self.broadcast_market_status()
                
                # Check every minute
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"Market hours monitoring error: {e}")
                await asyncio.sleep(60)
    
    async def fetch_indices_data(self):
        """Fetch real-time NSE indices data"""
        while True:
            try:
                if self.is_market_open:
                    # Fetch indices data
                    indices_data = await self.get_live_indices_data()
                    
                    if indices_data:
                        # Update cache
                        self.market_data_cache['indices'] = indices_data
                        
                        # Store in Redis
                        await self.cache_data('market:indices', indices_data)
                        
                        # Broadcast to subscribers
                        await self.broadcast_indices_data(indices_data)
                    
                    # Update every 10 seconds during market hours
                    await asyncio.sleep(10)
                else:
                    # Update every 5 minutes outside market hours
                    await asyncio.sleep(300)
                    
            except Exception as e:
                logger.error(f"Indices data fetch error: {e}")
                await asyncio.sleep(30)
    
    async def fetch_stock_data(self):
        """Fetch real-time stock data for subscribed symbols"""
        while True:
            try:
                if self.subscribed_symbols and self.is_market_open:
                    # Fetch data for all subscribed symbols
                    tasks = []
                    for symbol in list(self.subscribed_symbols):
                        task = self.get_live_stock_data(symbol)
                        tasks.append(task)
                    
                    # Execute all tasks concurrently
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Process results
                    for i, result in enumerate(results):
                        if isinstance(result, Exception):
                            logger.warning(f"Stock data fetch failed: {result}")
                            continue
                        
                        if result and isinstance(result, dict) and result.get('success'):
                            symbol = result.get('symbol')
                            if symbol:
                                # Update cache
                                self.market_data_cache[f'stock:{symbol}'] = result
                                
                                # Store in Redis
                                await self.cache_data(f'market:stock:{symbol}', result)
                                
                                # Broadcast to subscribers
                                await self.broadcast_stock_data(symbol, result)
                    
                    # Update every 5 seconds during market hours
                    await asyncio.sleep(5)
                else:
                    # Wait if no subscriptions or market closed
                    await asyncio.sleep(30)
                    
            except Exception as e:
                logger.error(f"Stock data fetch error: {e}")
                await asyncio.sleep(30)
    
    async def get_live_indices_data(self) -> Optional[Dict]:
        """Get live NSE indices data"""
        try:
            from services.nse_realtime_service import NSERealTimeService
            
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            nse_service = NSERealTimeService()
            
            data = await loop.run_in_executor(
                self.executor,
                nse_service.get_nse_indices
            )
            
            if data.get('success'):
                data['timestamp'] = datetime.now(timezone.utc).isoformat()
                return data
            
            return None
            
        except Exception as e:
            logger.error(f"Live indices data fetch failed: {e}")
            return None
    
    async def get_live_stock_data(self, symbol: str) -> Optional[Dict]:
        """Get live stock data for symbol"""
        try:
            from services.nse_realtime_service import NSERealTimeService
            
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            nse_service = NSERealTimeService()
            
            data = await loop.run_in_executor(
                self.executor,
                nse_service.get_stock_data,
                symbol
            )
            
            if data.get('success'):
                data['timestamp'] = datetime.now(timezone.utc).isoformat()
                return data
            
            return None
            
        except Exception as e:
            logger.error(f"Live stock data fetch failed for {symbol}: {e}")
            return None
    
    async def cache_data(self, key: str, data: Dict, expiry: int = 300):
        """Cache data in Redis with expiry"""
        try:
            self.redis_client.setex(key, expiry, json.dumps(data))
        except Exception as e:
            logger.error(f"Redis cache error: {e}")
    
    async def start_websocket_server(self):
        """Start WebSocket server for real-time data streaming"""
        try:
            async def handle_client(websocket):
                """Handle WebSocket client connections"""
                client_id = f"client_{datetime.now(timezone.utc).timestamp()}"
                self.subscribers[client_id] = websocket
                
                logger.info(f"Client {client_id} connected. Total clients: {len(self.subscribers)}")
                
                try:
                    async for message in websocket:
                        await self.handle_client_message(client_id, message)
                        
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.error(f"Client {client_id} error: {e}")
                finally:
                    # Clean up
                    if client_id in self.subscribers:
                        del self.subscribers[client_id]
                    logger.info(f"Client {client_id} disconnected. Total clients: {len(self.subscribers)}")
            
            # Start WebSocket server
            server = await websockets.serve(
                handle_client,
                "0.0.0.0",
                8001,  # WebSocket port
                ping_interval=20,
                ping_timeout=10
            )
            
            logger.info("WebSocket server started on port 8001")
            await server.wait_closed()
            
        except Exception as e:
            logger.error(f"WebSocket server error: {e}")
    
    async def handle_client_message(self, client_id: str, message: str):
        """Handle incoming WebSocket messages from clients"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            
            if msg_type == 'subscribe':
                symbol = data.get('symbol')
                if symbol:
                    self.subscribed_symbols.add(symbol)
                    logger.info(f"Client {client_id} subscribed to {symbol}")
                    
                    # Send cached data if available
                    cached_data = self.market_data_cache.get(f'stock:{symbol}')
                    if cached_data:
                        await self.send_to_client(client_id, {
                            'type': 'stock_data',
                            'data': cached_data
                        })
            
            elif msg_type == 'unsubscribe':
                symbol = data.get('symbol')
                if symbol:
                    self.subscribed_symbols.discard(symbol)
                    logger.info(f"Client {client_id} unsubscribed from {symbol}")
            
            elif msg_type == 'get_indices':
                # Send current indices data
                indices_data = self.market_data_cache.get('indices')
                if indices_data:
                    await self.send_to_client(client_id, {
                        'type': 'indices_data',
                        'data': indices_data
                    })
                    
        except Exception as e:
            logger.error(f"Message handling error for client {client_id}: {e}")
    
    async def send_to_client(self, client_id: str, data: Dict):
        """Send data to specific client"""
        if client_id in self.subscribers:
            try:
                websocket = self.subscribers[client_id]
                await websocket.send(json.dumps(data))
            except Exception as e:
                logger.warning(f"Failed to send data to client {client_id}: {e}")
                # Remove disconnected client
                if client_id in self.subscribers:
                    del self.subscribers[client_id]
    
    async def broadcast_to_all(self, data: Dict):
        """Broadcast data to all connected clients"""
        if self.subscribers:
            message = json.dumps(data)
            disconnected_clients = []
            
            for client_id, websocket in self.subscribers.items():
                try:
                    await websocket.send(message)
                except Exception as e:
                    logger.warning(f"Failed to send to client {client_id}: {e}")
                    disconnected_clients.append(client_id)
            
            # Clean up disconnected clients
            for client_id in disconnected_clients:
                if client_id in self.subscribers:
                    del self.subscribers[client_id]
    
    async def broadcast_market_status(self):
        """Broadcast market open/close status"""
        await self.broadcast_to_all({
            'type': 'market_status',
            'is_open': self.is_market_open,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def broadcast_indices_data(self, data: Dict):
        """Broadcast indices data to all clients"""
        await self.broadcast_to_all({
            'type': 'indices_data',
            'data': data
        })
    
    async def broadcast_stock_data(self, symbol: str, data: Dict):
        """Broadcast stock data to subscribed clients"""
        await self.broadcast_to_all({
            'type': 'stock_data',
            'symbol': symbol,
            'data': data
        })
    
    def get_cached_data(self, key: str) -> Optional[Dict]:
        """Get cached data from Redis"""
        try:
            data = self.redis_client.get(f'market:{key}')
            if data:
                return json.loads(str(data))
            return None
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
            return None
    
    def get_subscribed_symbols(self) -> Set[str]:
        """Get currently subscribed symbols"""
        return self.subscribed_symbols.copy()
    
    def get_active_connections(self) -> int:
        """Get number of active WebSocket connections"""
        return len(self.subscribers)

# Global service instance
market_service = RealTimeMarketService()

async def start_market_service():
    """Start the market service"""
    await market_service.start_service()

if __name__ == "__main__":
    # Run the real-time market service
    try:
        asyncio.run(start_market_service())
    except KeyboardInterrupt:
        logger.info("Market service stopped")
    except Exception as e:
        logger.error(f"Market service error: {e}")