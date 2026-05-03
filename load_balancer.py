"""
Production Load Balancer and API Gateway for Target Capital
Handles traffic distribution, rate limiting, and service orchestration
"""

import asyncio
import aiohttp
from aiohttp import web, WSMsgType
import logging
import json
import time
import redis
from typing import Dict, List, Optional
import os
from datetime import datetime, timedelta, timezone
import random

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LoadBalancer:
    """
    Production load balancer with intelligent traffic distribution
    """
    
    def __init__(self):
        # Backend instance URLs are env-driven so we don't ship localhost
        # defaults to production. Comma-separated list per service.
        flask_instances = [
            u.strip() for u in os.environ.get(
                "LB_FLASK_INSTANCES", "http://localhost:5000"
            ).split(",") if u.strip()
        ]
        trading_instances = [
            u.strip() for u in os.environ.get(
                "LB_TRADING_INSTANCES", "http://localhost:8000"
            ).split(",") if u.strip()
        ]
        self.services = {
            'flask_app': {
                'instances': flask_instances,
                'health_endpoint': '/health',
                'current_index': 0,
                'healthy_instances': []
            },
            'trading_engine': {
                'instances': trading_instances,
                'health_endpoint': '/docs',
                'current_index': 0,
                'healthy_instances': []
            }
        }

        self.websocket_targets = [
            u.strip() for u in os.environ.get(
                "LB_WEBSOCKET_TARGETS", "ws://localhost:8001"
            ).split(",") if u.strip()
        ]
        self.redis_client = redis.Redis.from_url(
            os.environ.get('REDIS_URL', 'redis://localhost:6379'),
            decode_responses=True
        )
        
        # Rate limiting
        self.rate_limits = {
            'api_calls': {'limit': 1000, 'window': 60},  # 1000 requests per minute
            'trading_orders': {'limit': 100, 'window': 60},  # 100 orders per minute
            'market_data': {'limit': 10000, 'window': 60}  # 10k market data requests per minute
        }
        
        # Load balancing metrics
        self.metrics = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'average_response_time': 0,
            'service_health': {}
        }
    
    async def start_load_balancer(self):
        """Start the production load balancer"""
        logger.info("🚀 Starting Target Capital Production Load Balancer")
        
        # Start health checking
        asyncio.create_task(self.health_check_loop())
        
        # Start metrics collection
        asyncio.create_task(self.metrics_collection_loop())
        
        # Setup web application
        app = web.Application(middlewares=[
            self.rate_limiting_middleware,
            self.cors_middleware,
            self.logging_middleware,
            self.load_balancing_middleware
        ])
        
        # API routes
        app.router.add_route('*', '/api/trading/{path:.*}', self.handle_trading_api)
        app.router.add_route('*', '/api/{path:.*}', self.handle_api_request)
        app.router.add_get('/ws', self.handle_websocket)
        app.router.add_get('/lb/health', self.load_balancer_health)
        app.router.add_get('/lb/metrics', self.load_balancer_metrics)
        app.router.add_get('/lb/status', self.load_balancer_status)
        
        # Static file serving and web app routes
        app.router.add_route('*', '/{path:.*}', self.handle_web_request)
        
        # Start server
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', 9000)
        await site.start()
        
        logger.info("✅ Load balancer started on port 9000")
        logger.info("🌐 Main URL: http://localhost:9000")
        logger.info("📊 Metrics: http://localhost:9000/lb/metrics")
        logger.info("🏥 Health: http://localhost:9000/lb/health")
    
    async def health_check_loop(self):
        """Continuously check service health"""
        while True:
            try:
                for service_name, service in self.services.items():
                    healthy_instances = []
                    
                    for instance_url in service['instances']:
                        if await self.check_instance_health(instance_url, service['health_endpoint']):
                            healthy_instances.append(instance_url)
                    
                    service['healthy_instances'] = healthy_instances
                    self.metrics['service_health'][service_name] = {
                        'total': len(service['instances']),
                        'healthy': len(healthy_instances),
                        'ratio': len(healthy_instances) / len(service['instances']) if service['instances'] else 0
                    }
                
                # Log health status
                total_healthy = sum(len(s['healthy_instances']) for s in self.services.values())
                total_instances = sum(len(s['instances']) for s in self.services.values())
                
                if total_healthy == total_instances:
                    logger.debug(f"✅ All {total_instances} service instances healthy")
                else:
                    logger.warning(f"⚠️  {total_healthy}/{total_instances} service instances healthy")
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(60)
    
    async def check_instance_health(self, instance_url: str, health_endpoint: str) -> bool:
        """Check if a service instance is healthy"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{instance_url}{health_endpoint}") as response:
                    return response.status == 200
        except:
            return False
    
    async def metrics_collection_loop(self):
        """Collect and update performance metrics"""
        while True:
            try:
                # Update Redis-based metrics
                current_time = datetime.now(timezone.utc)
                
                # Store metrics in Redis for persistence
                metrics_data = {
                    'timestamp': current_time.isoformat(),
                    'metrics': self.metrics
                }
                
                self.redis_client.setex(
                    'lb:metrics:latest',
                    300,  # 5 minutes
                    json.dumps(metrics_data)
                )
                
                # Clean old metrics
                hour_ago = current_time - timedelta(hours=1)
                self.redis_client.zremrangebyscore(
                    'lb:metrics:history',
                    0,
                    hour_ago.timestamp()
                )
                
                await asyncio.sleep(60)  # Update every minute
                
            except Exception as e:
                logger.error(f"Metrics collection error: {e}")
                await asyncio.sleep(60)
    
    @web.middleware
    async def rate_limiting_middleware(self, request, handler):
        """Rate limiting middleware"""
        client_ip = request.remote
        current_time = time.time()
        
        # Determine rate limit type based on path
        if '/api/trading/' in request.path:
            limit_key = 'trading_orders'
        elif '/api/market/' in request.path:
            limit_key = 'market_data'
        else:
            limit_key = 'api_calls'
        
        rate_limit = self.rate_limits[limit_key]
        
        # Check rate limit in Redis
        redis_key = f"rate_limit:{limit_key}:{client_ip}"
        current_count = self.redis_client.get(redis_key)
        
        if current_count is None:
            # First request in window
            self.redis_client.setex(redis_key, rate_limit['window'], 1)
        else:
            current_count = int(current_count)
            if current_count >= rate_limit['limit']:
                # Rate limit exceeded
                return web.json_response(
                    {
                        'error': 'Rate limit exceeded',
                        'limit': rate_limit['limit'],
                        'window': rate_limit['window'],
                        'retry_after': rate_limit['window']
                    },
                    status=429
                )
            else:
                # Increment counter
                self.redis_client.incr(redis_key)
        
        return await handler(request)
    
    @web.middleware
    async def cors_middleware(self, request, handler):
        """CORS middleware"""
        response = await handler(request)
        
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        
        return response
    
    @web.middleware
    async def logging_middleware(self, request, handler):
        """Request logging middleware"""
        start_time = time.time()
        
        try:
            response = await handler(request)
            
            # Update metrics
            self.metrics['total_requests'] += 1
            self.metrics['successful_requests'] += 1
            
            response_time = time.time() - start_time
            self.update_average_response_time(response_time)
            
            logger.debug(f"{request.method} {request.path} - {response.status} - {response_time:.3f}s")
            
            return response
            
        except Exception as e:
            self.metrics['total_requests'] += 1
            self.metrics['failed_requests'] += 1
            
            response_time = time.time() - start_time
            logger.error(f"{request.method} {request.path} - ERROR - {response_time:.3f}s: {e}")
            
            raise
    
    @web.middleware
    async def load_balancing_middleware(self, request, handler):
        """Load balancing middleware"""
        # Add load balancing headers
        request['lb_start_time'] = time.time()
        request['lb_request_id'] = f"req_{int(time.time() * 1000)}"
        
        return await handler(request)
    
    def update_average_response_time(self, response_time: float):
        """Update average response time with exponential moving average"""
        alpha = 0.1  # Smoothing factor
        if self.metrics['average_response_time'] == 0:
            self.metrics['average_response_time'] = response_time
        else:
            self.metrics['average_response_time'] = (
                alpha * response_time + 
                (1 - alpha) * self.metrics['average_response_time']
            )
    
    def get_next_instance(self, service_name: str) -> Optional[str]:
        """Get next healthy instance using round-robin"""
        service = self.services.get(service_name)
        if not service or not service['healthy_instances']:
            return None
        
        # Round-robin selection
        healthy_instances = service['healthy_instances']
        instance = healthy_instances[service['current_index'] % len(healthy_instances)]
        service['current_index'] += 1
        
        return instance
    
    async def handle_api_request(self, request):
        """Handle API requests with load balancing"""
        # Determine target service
        if request.path.startswith('/api/trading/'):
            target_instance = self.get_next_instance('trading_engine')
            target_path = request.path.replace('/api/trading/', '/api/')
        else:
            target_instance = self.get_next_instance('flask_app')
            target_path = request.path
        
        if not target_instance:
            return web.json_response(
                {'error': 'Service temporarily unavailable'},
                status=503
            )
        
        return await self.proxy_request(request, target_instance, target_path)
    
    async def handle_trading_api(self, request):
        """Handle trading API requests specifically"""
        target_instance = self.get_next_instance('trading_engine')
        
        if not target_instance:
            return web.json_response(
                {'error': 'Trading service temporarily unavailable'},
                status=503
            )
        
        # Remove /api/trading prefix and add /api prefix
        target_path = '/api/' + request.match_info['path']
        
        return await self.proxy_request(request, target_instance, target_path)
    
    async def handle_web_request(self, request):
        """Handle web requests (HTML pages)"""
        target_instance = self.get_next_instance('flask_app')
        
        if not target_instance:
            return web.Response(
                text='Service temporarily unavailable',
                status=503,
                content_type='text/html'
            )
        
        return await self.proxy_request(request, target_instance, request.path)
    
    async def proxy_request(self, request, target_instance: str, target_path: str):
        """Proxy request to target instance"""
        try:
            target_url = f"{target_instance}{target_path}"
            
            # Prepare headers (remove host header to avoid conflicts)
            headers = dict(request.headers)
            headers.pop('Host', None)
            
            # Get request body
            body = None
            if request.method in ['POST', 'PUT', 'PATCH']:
                body = await request.read()
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=request.query,
                    data=body
                ) as response:
                    
                    # Read response
                    response_body = await response.read()
                    
                    # Create response
                    result = web.Response(
                        body=response_body,
                        status=int(response.status),
                        headers=response.headers
                    )
                    
                    return result
                    
        except Exception as e:
            logger.error(f"Proxy request failed: {e}")
            return web.json_response(
                {'error': 'Request failed', 'details': str(e)},
                status=502
            )
    
    async def handle_websocket(self, request):
        """Handle WebSocket connections with load balancing"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        # Connect to backend WebSocket
        backend_ws = None
        try:
            # Simple round-robin for WebSocket targets
            target_url = random.choice(self.websocket_targets)
            
            async with aiohttp.ClientSession() as session:
                backend_ws = await session.ws_connect(target_url)
                
                # Create bidirectional proxy
                async def forward_to_backend():
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            await backend_ws.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await backend_ws.send_bytes(msg.data)
                        elif msg.type == WSMsgType.ERROR:
                            break
                
                async def forward_to_client():
                    async for msg in backend_ws:
                        if msg.type == WSMsgType.TEXT:
                            await ws.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await ws.send_bytes(msg.data)
                        elif msg.type == WSMsgType.ERROR:
                            break
                
                # Run both directions concurrently
                await asyncio.gather(
                    forward_to_backend(),
                    forward_to_client(),
                    return_exceptions=True
                )
                
        except Exception as e:
            logger.error(f"WebSocket proxy error: {e}")
        finally:
            if backend_ws:
                await backend_ws.close()
            if not ws.closed:
                await ws.close()
        
        return ws
    
    async def load_balancer_health(self, request):
        """Load balancer health endpoint"""
        health_data = {
            'status': 'healthy',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'services': self.metrics['service_health'],
            'total_requests': self.metrics['total_requests'],
            'success_rate': (
                self.metrics['successful_requests'] / self.metrics['total_requests']
                if self.metrics['total_requests'] > 0 else 0
            ),
            'average_response_time': self.metrics['average_response_time']
        }
        
        return web.json_response(health_data)
    
    async def load_balancer_metrics(self, request):
        """Load balancer metrics endpoint"""
        return web.json_response(self.metrics)
    
    async def load_balancer_status(self, request):
        """Load balancer status endpoint"""
        status_data = {
            'services': {},
            'load_balancer': {
                'uptime': time.time(),
                'version': '1.0.0',
                'features': [
                    'Round-robin load balancing',
                    'Health checking',
                    'Rate limiting',
                    'WebSocket proxying',
                    'CORS support',
                    'Request logging',
                    'Metrics collection'
                ]
            }
        }
        
        for service_name, service in self.services.items():
            status_data['services'][service_name] = {
                'total_instances': len(service['instances']),
                'healthy_instances': len(service['healthy_instances']),
                'instances': service['instances'],
                'health_ratio': (
                    len(service['healthy_instances']) / len(service['instances'])
                    if service['instances'] else 0
                )
            }
        
        return web.json_response(status_data)

# Global load balancer instance
load_balancer = LoadBalancer()

async def start_load_balancer():
    """Start the production load balancer"""
    await load_balancer.start_load_balancer()

if __name__ == "__main__":
    try:
        asyncio.run(start_load_balancer())
        
        # Keep running
        while True:
            time.sleep(60)
            logger.info("💓 Load balancer heartbeat")
            
    except KeyboardInterrupt:
        logger.info("🛑 Load balancer shutting down...")
    except Exception as e:
        logger.error(f"❌ Load balancer error: {e}")