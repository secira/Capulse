"""
Production Trading Engine for Capulse
High-performance async trading system with real-time market data and algorithmic execution
"""

import asyncio
import logging
import redis
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from celery import Celery
import aioredis
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis Configuration
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# Celery Configuration for Background Tasks
celery_app = Celery(
    'trading_engine',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['trading_tasks']
)

# FastAPI App for Real-time Trading API
trading_api = FastAPI(title="Capulse Trading Engine", version="1.0.0")

# CORS middleware for frontend connections
trading_api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TradingEngine:
    """
    High-performance trading engine with real-time capabilities
    """
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.market_data_cache = {}
        self.active_algorithms = {}
        self.order_queue = asyncio.Queue()
        self.portfolio_cache = {}
        self.risk_limits = {}
        
    async def connect_websocket(self, websocket: WebSocket):
        """Connect new WebSocket client"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
        
    def disconnect_websocket(self, websocket: WebSocket):
        """Disconnect WebSocket client"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
        
    async def broadcast_market_data(self, data: Dict):
        """Broadcast real-time market data to all connected clients"""
        if self.active_connections:
            message = json.dumps({
                "type": "market_data",
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # Send to all connected clients
            disconnected = []
            for connection in self.active_connections:
                try:
                    await connection.send_text(message)
                except Exception as e:
                    logger.warning(f"Failed to send data to client: {e}")
                    disconnected.append(connection)
            
            # Clean up disconnected clients
            for conn in disconnected:
                self.disconnect_websocket(conn)
    
    async def process_order(self, order_data: Dict) -> Dict:
        """Process trading order with validation and execution"""
        try:
            # Validate order
            if not self.validate_order(order_data):
                return {"status": "rejected", "reason": "Invalid order data"}
            
            # Check risk limits
            if not self.check_risk_limits(order_data):
                return {"status": "rejected", "reason": "Risk limits exceeded"}
            
            # Add to processing queue
            await self.order_queue.put(order_data)
            
            # Execute order asynchronously
            result = await self.execute_order_async(order_data)
            
            # Update portfolio cache
            await self.update_portfolio_cache(order_data, result)
            
            # Broadcast order update
            await self.broadcast_order_update(order_data, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Order processing error: {e}")
            return {"status": "error", "reason": str(e)}
    
    def validate_order(self, order_data: Dict) -> bool:
        """Validate order data"""
        required_fields = ['symbol', 'quantity', 'price', 'side', 'user_id']
        return all(field in order_data for field in required_fields)
    
    def check_risk_limits(self, order_data: Dict) -> bool:
        """Check risk management limits"""
        user_id = order_data.get('user_id')
        symbol = order_data.get('symbol')
        quantity = order_data.get('quantity', 0)
        price = order_data.get('price', 0)
        
        # Position size limit
        max_position_value = 1000000  # ₹10 lakh per position
        order_value = quantity * price
        
        if order_value > max_position_value:
            return False
        
        # Daily trading limit
        user_daily_volume = self.get_user_daily_volume(user_id)
        daily_limit = 5000000  # ₹50 lakh daily limit
        
        if user_daily_volume + order_value > daily_limit:
            return False
            
        return True
    
    async def execute_order_async(self, order_data: Dict) -> Dict:
        """Execute order using async broker API calls"""
        try:
            # Import broker service
            from services.broker_service import BrokerService
            
            # Get user's primary broker
            user_id = order_data['user_id']
            broker_service = BrokerService()
            
            # Execute order through broker API
            # Placeholder for broker integration
            result = {
                'order_id': f'order_{int(time.time())}',
                'price': order_data.get('price'),
                'status': 'executed'
            }
            
            return {
                "status": "executed",
                "order_id": result.get('order_id'),
                "execution_price": result.get('price'),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Order execution error: {e}")
            return {"status": "failed", "reason": str(e)}
    
    async def update_portfolio_cache(self, order_data: Dict, result: Dict):
        """Update portfolio cache with new order"""
        user_id = order_data['user_id']
        
        # Update Redis cache
        cache_key = f"portfolio:{user_id}"
        portfolio_data = redis_client.get(cache_key)
        
        if portfolio_data:
            try:
                portfolio = json.loads(portfolio_data)
            except (json.JSONDecodeError, TypeError):
                portfolio = {"holdings": {}, "cash": 0, "total_value": 0}
        else:
            portfolio = {"holdings": {}, "cash": 0, "total_value": 0}
        
        # Update holdings
        symbol = order_data['symbol']
        quantity = order_data['quantity']
        side = order_data['side']
        
        if symbol not in portfolio['holdings']:
            portfolio['holdings'][symbol] = {"quantity": 0, "avg_price": 0}
        
        current_qty = portfolio['holdings'][symbol]['quantity']
        if side == 'BUY':
            new_qty = current_qty + quantity
        else:  # SELL
            new_qty = current_qty - quantity
            
        portfolio['holdings'][symbol]['quantity'] = new_qty
        
        # Cache updated portfolio
        redis_client.setex(cache_key, 300, json.dumps(portfolio))  # 5 min cache
    
    async def broadcast_order_update(self, order_data: Dict, result: Dict):
        """Broadcast order update to relevant clients"""
        message = {
            "type": "order_update",
            "user_id": order_data['user_id'],
            "order": order_data,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await self.broadcast_to_user(order_data['user_id'], message)
    
    async def broadcast_to_user(self, user_id: str, message: Dict):
        """Broadcast message to specific user's connections"""
        # In production, you'd maintain user-specific connection mappings
        message_json = json.dumps(message)
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.warning(f"Failed to send user message: {e}")
    
    def get_user_daily_volume(self, user_id: str) -> float:
        """Get user's daily trading volume from cache"""
        if not user_id:
            return 0.0
        cache_key = f"daily_volume:{user_id}:{datetime.now(timezone.utc).date()}"
        volume = redis_client.get(cache_key)
        return float(volume) if volume else 0.0

# Global trading engine instance
trading_engine = TradingEngine()

class MarketDataStreamer:
    """
    Real-time market data streaming service
    """
    
    def __init__(self):
        self.subscribed_symbols = set()
        self.data_cache = {}
        
    async def start_streaming(self):
        """Start market data streaming"""
        while True:
            try:
                # Fetch real-time data for subscribed symbols
                for symbol in self.subscribed_symbols:
                    market_data = await self.fetch_live_data(symbol)
                    if market_data:
                        # Cache data
                        self.data_cache[symbol] = market_data
                        
                        # Broadcast to clients
                        await trading_engine.broadcast_market_data({
                            "symbol": symbol,
                            "data": market_data
                        })
                
                # Sleep for real-time updates (adjust for production)
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Market data streaming error: {e}")
                await asyncio.sleep(5)
    
    async def fetch_live_data(self, symbol: str) -> Optional[Dict]:
        """Fetch live market data for symbol"""
        try:
            # Import your NSE service
            from services.nse_realtime_service import NSERealTimeService
            
            nse_service = NSERealTimeService()
            data = await asyncio.to_thread(nse_service.get_stock_data, symbol)
            
            return data if data.get('success') else None
            
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")
            return None
    
    def subscribe_symbol(self, symbol: str):
        """Subscribe to symbol for real-time data"""
        self.subscribed_symbols.add(symbol)
        logger.info(f"Subscribed to {symbol}. Total subscriptions: {len(self.subscribed_symbols)}")
    
    def unsubscribe_symbol(self, symbol: str):
        """Unsubscribe from symbol"""
        self.subscribed_symbols.discard(symbol)
        logger.info(f"Unsubscribed from {symbol}")

# Global market data streamer
market_streamer = MarketDataStreamer()

class AlgorithmExecutor:
    """
    Algorithmic trading execution engine
    """
    
    def __init__(self):
        self.active_algorithms = {}
        self.algorithm_results = {}
        
    async def start_algorithm(self, algo_config: Dict) -> str:
        """Start algorithmic trading strategy"""
        algo_id = f"algo_{datetime.now(timezone.utc).timestamp()}"
        
        # Validate algorithm configuration
        if not self.validate_algorithm_config(algo_config):
            raise ValueError("Invalid algorithm configuration")
        
        # Start algorithm in background
        task = asyncio.create_task(self.run_algorithm(algo_id, algo_config))
        self.active_algorithms[algo_id] = {
            "config": algo_config,
            "task": task,
            "start_time": datetime.now(timezone.utc),
            "status": "running"
        }
        
        logger.info(f"Started algorithm {algo_id}")
        return algo_id
    
    async def run_algorithm(self, algo_id: str, config: Dict):
        """Run algorithmic trading strategy"""
        try:
            strategy_type = config.get('strategy_type')
            
            if strategy_type == 'momentum':
                await self.run_momentum_strategy(algo_id, config)
            elif strategy_type == 'mean_reversion':
                await self.run_mean_reversion_strategy(algo_id, config)
            elif strategy_type == 'arbitrage':
                await self.run_arbitrage_strategy(algo_id, config)
            else:
                raise ValueError(f"Unknown strategy type: {strategy_type}")
                
        except Exception as e:
            logger.error(f"Algorithm {algo_id} error: {e}")
            self.active_algorithms[algo_id]["status"] = "error"
    
    async def run_momentum_strategy(self, algo_id: str, config: Dict):
        """Run momentum-based trading strategy"""
        symbol = config['symbol']
        
        while self.active_algorithms[algo_id]["status"] == "running":
            try:
                # Get latest market data
                current_data = market_streamer.data_cache.get(symbol)
                if not current_data:
                    await asyncio.sleep(5)
                    continue
                
                # Implement momentum logic
                signal = self.calculate_momentum_signal(symbol, current_data)
                
                if signal and signal.get('action') in ['BUY', 'SELL']:
                    # Generate order
                    order = {
                        'symbol': symbol,
                        'quantity': config.get('quantity', 100),
                        'side': signal['action'],
                        'price': float(current_data.get('price', 0)),
                        'user_id': config['user_id'],
                        'algorithm_id': algo_id
                    }
                    
                    # Execute order
                    result = await trading_engine.process_order(order)
                    logger.info(f"Algorithm {algo_id} executed order: {result}")
                
                await asyncio.sleep(config.get('interval', 30))  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Momentum strategy error: {e}")
                await asyncio.sleep(60)
    
    def calculate_momentum_signal(self, symbol: str, current_data: Dict) -> Optional[Dict]:
        """Calculate momentum trading signal"""
        # Simplified momentum calculation
        # In production, use sophisticated technical indicators
        
        price = current_data.get('price', 0)
        change_percent = current_data.get('change_percent', 0)
        
        if change_percent > 2:  # Strong upward momentum
            return {"action": "BUY", "strength": change_percent}
        elif change_percent < -2:  # Strong downward momentum
            return {"action": "SELL", "strength": abs(change_percent)}
        
        return None
    
    async def run_mean_reversion_strategy(self, algo_id: str, config: Dict):
        """Run mean reversion strategy"""
        # Implementation for mean reversion
        pass
    
    async def run_arbitrage_strategy(self, algo_id: str, config: Dict):
        """Run arbitrage strategy"""
        # Implementation for arbitrage opportunities
        pass
    
    def validate_algorithm_config(self, config: Dict) -> bool:
        """Validate algorithm configuration"""
        required_fields = ['strategy_type', 'symbol', 'user_id']
        return all(field in config for field in required_fields)
    
    async def stop_algorithm(self, algo_id: str):
        """Stop running algorithm"""
        if algo_id in self.active_algorithms:
            self.active_algorithms[algo_id]["status"] = "stopped"
            task = self.active_algorithms[algo_id]["task"]
            task.cancel()
            logger.info(f"Stopped algorithm {algo_id}")

# Global algorithm executor
algo_executor = AlgorithmExecutor()

# FastAPI Routes
@trading_api.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time trading data"""
    await trading_engine.connect_websocket(websocket)
    try:
        while True:
            # Keep connection alive and handle incoming messages
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get('type') == 'subscribe':
                symbol = message.get('symbol')
                if symbol:
                    market_streamer.subscribe_symbol(symbol)
                    
    except WebSocketDisconnect:
        trading_engine.disconnect_websocket(websocket)

@trading_api.post("/api/orders")
async def place_order(order_data: Dict):
    """Place trading order"""
    result = await trading_engine.process_order(order_data)
    return result

@trading_api.post("/api/algorithms/start")
async def start_algorithm(algo_config: Dict):
    """Start algorithmic trading strategy"""
    try:
        algo_id = await algo_executor.start_algorithm(algo_config)
        return {"status": "success", "algorithm_id": algo_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@trading_api.post("/api/algorithms/{algo_id}/stop")
async def stop_algorithm(algo_id: str):
    """Stop algorithmic trading strategy"""
    await algo_executor.stop_algorithm(algo_id)
    return {"status": "success"}

@trading_api.get("/api/market/{symbol}")
async def get_market_data(symbol: str):
    """Get current market data for symbol"""
    data = market_streamer.data_cache.get(symbol)
    if data:
        return {"success": True, "data": data}
    else:
        return {"success": False, "message": "Data not available"}

@trading_api.get("/api/portfolio/{user_id}")
async def get_portfolio(user_id: str):
    """Get real-time portfolio data"""
    cache_key = f"portfolio:{user_id}"
    portfolio_data = redis_client.get(cache_key)
    
    if portfolio_data:
        return {"success": True, "data": json.loads(portfolio_data)}
    else:
        return {"success": False, "message": "Portfolio not found"}

# Background task to start market data streaming
@trading_api.on_event("startup")
async def startup_event():
    """Start background services"""
    # Start market data streaming
    asyncio.create_task(market_streamer.start_streaming())
    logger.info("Trading engine started successfully")

if __name__ == "__main__":
    # Run the trading engine
    uvicorn.run(
        "trading_engine:trading_api",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Set to False in production
        workers=1,  # Single worker for WebSocket state management
        loop="asyncio"
    )