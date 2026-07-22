#!/usr/bin/env python3
"""
Production Startup Script for Capulse Trading Platform
One-command deployment of the complete scalable trading system
"""

import subprocess
import time
import logging
import os
import sys
from typing import List, Dict
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProductionStarter:
    """
    Production startup orchestrator for Capulse trading system
    """
    
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.services = [
            {
                'name': 'flask_app',
                'command': ['gunicorn', '--bind', '0.0.0.0:5000', '--workers', '4', '--reload', 'main:app'],
                'port': 5000,
                'description': 'Main Flask web application'
            },
            {
                'name': 'trading_engine',
                'command': ['uvicorn', 'trading_engine:trading_api', '--host', '0.0.0.0', '--port', '8000', '--workers', '1'],
                'port': 8000,
                'description': 'FastAPI trading engine'
            },
            {
                'name': 'realtime_market',
                'command': ['python', 'realtime_market_service.py'],
                'port': 8001,
                'description': 'Real-time market data service'
            },
            {
                'name': 'load_balancer',
                'command': ['python', 'load_balancer.py'],
                'port': 9000,
                'description': 'Production load balancer'
            },
            {
                'name': 'celery_worker',
                'command': ['celery', '-A', 'trading_tasks', 'worker', '--loglevel=info', '--concurrency=4'],
                'port': None,
                'description': 'Celery background worker'
            },
            {
                'name': 'celery_beat',
                'command': ['celery', '-A', 'trading_tasks', 'beat', '--loglevel=info'],
                'port': None,
                'description': 'Celery periodic task scheduler'
            }
        ]
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"🛑 Received signal {signum}, shutting down...")
        self.stop_all_services()
        sys.exit(0)
    
    def validate_environment(self) -> bool:
        """Validate environment before starting"""
        logger.info("🔍 Validating production environment...")
        
        # Check Python version
        if sys.version_info < (3, 8):
            logger.error("❌ Python 3.8+ required")
            return False
        
        # Check required packages
        required_packages = [
            'flask', 'fastapi', 'uvicorn', 'celery', 'redis', 
            'websockets', 'aiohttp', 'gunicorn'
        ]
        
        for package in required_packages:
            try:
                __import__(package)
            except ImportError:
                logger.error(f"❌ Required package missing: {package}")
                return False
        
        # Check environment variables
        required_env_vars = ['DATABASE_URL', 'SESSION_SECRET']
        missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
        
        if missing_vars:
            logger.warning(f"⚠️  Missing environment variables: {missing_vars}")
        
        # Set Redis URL if not set
        if not os.environ.get('REDIS_URL'):
            os.environ['REDIS_URL'] = 'redis://localhost:6379'
        
        logger.info("✅ Environment validation passed")
        return True
    
    def start_service(self, service: Dict) -> bool:
        """Start a single service"""
        name = service['name']
        command = service['command']
        
        try:
            logger.info(f"🚀 Starting {name}: {service['description']}")
            
            # Start process
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )
            
            # Store process
            self.processes[name] = process
            
            # Give service time to start
            time.sleep(2)
            
            # Check if process is still running
            if process.poll() is None:
                port_info = f" on port {service['port']}" if service['port'] else ""
                logger.info(f"✅ {name} started successfully{port_info}")
                return True
            else:
                stdout, stderr = process.communicate()
                logger.error(f"❌ {name} failed to start: {stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error starting {name}: {e}")
            return False
    
    def stop_service(self, name: str):
        """Stop a single service"""
        if name in self.processes:
            process = self.processes[name]
            
            try:
                # Try graceful shutdown first
                if hasattr(os, 'killpg'):
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                else:
                    process.terminate()
                
                # Wait for graceful shutdown
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill if necessary
                    if hasattr(os, 'killpg'):
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                
                logger.info(f"🛑 Stopped {name}")
                
            except Exception as e:
                logger.warning(f"⚠️  Error stopping {name}: {e}")
            
            del self.processes[name]
    
    def start_all_services(self):
        """Start all services in the correct order"""
        logger.info("🎯 Starting all production services...")
        
        # Start services with dependencies first
        success_count = 0
        
        for service in self.services:
            if self.start_service(service):
                success_count += 1
            else:
                logger.warning(f"⚠️  Failed to start {service['name']}, continuing...")
            
            # Small delay between service starts
            time.sleep(1)
        
        logger.info(f"✅ Started {success_count}/{len(self.services)} services")
        
        if success_count > 0:
            self.display_system_info()
        
        return success_count > 0
    
    def stop_all_services(self):
        """Stop all services"""
        logger.info("🛑 Stopping all services...")
        
        # Stop services in reverse order
        service_names = [s['name'] for s in reversed(self.services)]
        
        for name in service_names:
            self.stop_service(name)
        
        logger.info("✅ All services stopped")
    
    def monitor_services(self):
        """Monitor service health"""
        logger.info("👀 Monitoring service health...")
        
        while True:
            try:
                running_count = 0
                
                for name, process in list(self.processes.items()):
                    if process.poll() is None:
                        running_count += 1
                    else:
                        logger.warning(f"⚠️  Service {name} has stopped")
                        # Could implement auto-restart here
                
                if running_count == 0:
                    logger.error("❌ All services have stopped")
                    break
                
                # Health check every 30 seconds
                time.sleep(30)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"❌ Monitoring error: {e}")
                time.sleep(60)
    
    def display_system_info(self):
        """Display system information and URLs"""
        logger.info("\n" + "="*70)
        logger.info("🎯 Capulse Production Trading System")
        logger.info("="*70)
        
        logger.info("\n🌐 System URLs:")
        logger.info("  📊 Main Dashboard: http://localhost:5000")
        logger.info("  🔄 Load Balancer: http://localhost:9000")
        logger.info("  ⚡ Trading API: http://localhost:8000")
        logger.info("  📈 API Documentation: http://localhost:8000/docs")
        logger.info("  🔌 WebSocket: ws://localhost:8001")
        logger.info("  📊 Load Balancer Metrics: http://localhost:9000/lb/metrics")
        logger.info("  🏥 System Health: http://localhost:9000/lb/health")
        
        logger.info("\n📱 Services Status:")
        for name, process in self.processes.items():
            service_info = next((s for s in self.services if s['name'] == name), {})
            status = "✅ Running" if process.poll() is None else "❌ Stopped"
            port = f" (port {service_info.get('port')})" if service_info.get('port') else ""
            logger.info(f"  {status} {name}{port}")
        
        logger.info("\n🚀 Production Features:")
        logger.info("  📈 Real-time market data streaming")
        logger.info("  🤖 Algorithmic trading execution")
        logger.info("  📊 Live portfolio analysis")
        logger.info("  🔗 12-broker integration")
        logger.info("  🧠 AI-powered recommendations")
        logger.info("  ⚡ WebSocket real-time updates")
        logger.info("  🔄 Load balancing & rate limiting")
        logger.info("  📊 Background task processing")
        logger.info("  🏥 Health monitoring")
        
        logger.info("\n💡 Performance Optimizations:")
        logger.info("  ⚡ Async/await for trading operations")
        logger.info("  💾 Redis caching for market data")
        logger.info("  🔄 Background processing with Celery")
        logger.info("  📊 Real-time data streaming")
        logger.info("  🎯 Load balancing across services")
        
        logger.info("\n" + "="*70)
        logger.info("🎉 Production system is ready for high-frequency trading!")
        logger.info("="*70 + "\n")
    
    def run_production_system(self):
        """Run the complete production system"""
        try:
            logger.info("🚀 Capulse Production Trading System Startup")
            logger.info("=" * 50)
            
            # Validate environment
            if not self.validate_environment():
                logger.error("❌ Environment validation failed")
                return False
            
            # Start all services
            if not self.start_all_services():
                logger.error("❌ Failed to start services")
                return False
            
            logger.info("🏃 Production system is running. Press Ctrl+C to stop.")
            
            # Monitor services
            self.monitor_services()
            
        except KeyboardInterrupt:
            logger.info("\n🛑 Shutdown requested by user")
        except Exception as e:
            logger.error(f"❌ Production system error: {e}")
        finally:
            self.stop_all_services()
        
        return True

def main():
    """Main entry point"""
    starter = ProductionStarter()
    
    # Print banner
    print("""
    ██████╗  ██████╗ █████╗ ██████╗ ██╗████████╗ █████╗ ██╗     
    ╚══██╔══╝██╔════╝██╔══██╗██╔══██╗██║╚══██╔══╝██╔══██╗██║     
       ██║   ██║     ███████║██████╔╝██║   ██║   ███████║██║     
       ██║   ██║     ██╔══██║██╔═══╝ ██║   ██║   ██╔══██║██║     
       ██║   ╚██████╗██║  ██║██║     ██║   ██║   ██║  ██║███████╗
       ╚═╝    ╚═════╝╚═╝  ╚═╝╚═╝     ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝
                                                                  
    🚀 Production Trading Platform - High-Performance Architecture
    """)
    
    # Run the system
    success = starter.run_production_system()
    
    if success:
        logger.info("✅ Production system shutdown completed")
    else:
        logger.error("❌ Production system encountered errors")
        sys.exit(1)

if __name__ == "__main__":
    main()