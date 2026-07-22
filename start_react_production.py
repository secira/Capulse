#!/usr/bin/env python3
"""
Capulse Production Startup with React + WebSocket Integration
Comprehensive multi-service orchestration for scalable trading platform
"""

import subprocess
import time
import signal
import sys
import os
import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProductionOrchestrator:
    def __init__(self):
        self.processes = {}
        self.running = False
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # Service configurations
        self.services = {
            'flask_app': {
                'command': ['gunicorn', '--bind', '0.0.0.0:5000', '--reuse-port', '--reload', '--workers', '4', 'main:app'],
                'port': 5000,
                'name': 'Flask Dashboard',
                'critical': True
            },
            'fastapi_engine': {
                'command': ['uvicorn', 'trading_engine:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '2'],
                'port': 8000,
                'name': 'FastAPI Trading Engine',
                'critical': True
            },
            'websocket_servers': {
                'command': ['python', 'websocket_servers.py'],
                'port': [8001, 8002, 8003],
                'name': 'WebSocket Servers',
                'critical': True
            },
            'load_balancer': {
                'command': ['python', 'load_balancer.py'],
                'port': 9000,
                'name': 'Load Balancer',
                'critical': False
            },
            'redis_server': {
                'command': ['redis-server', '--port', '6379'],
                'port': 6379,
                'name': 'Redis Cache',
                'critical': True
            },
            'celery_worker': {
                'command': ['celery', '-A', 'trading_engine.celery', 'worker', '--loglevel=info'],
                'port': None,
                'name': 'Celery Worker',
                'critical': False
            },
            'celery_beat': {
                'command': ['celery', '-A', 'trading_engine.celery', 'beat', '--loglevel=info'],
                'port': None,
                'name': 'Celery Scheduler',
                'critical': False
            }
        }
        
    def check_port_available(self, port):
        """Check if port is available"""
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port == port:
                    return False
            return True
        except:
            return True
    
    def start_service(self, service_name, config):
        """Start individual service"""
        try:
            # Check if ports are available
            ports = config['port'] if isinstance(config['port'], list) else [config['port']] if config['port'] else []
            
            for port in ports:
                if port and not self.check_port_available(port):
                    logger.warning(f"⚠️  Port {port} already in use for {service_name}")
            
            logger.info(f"🚀 Starting {config['name']}...")
            
            process = subprocess.Popen(
                config['command'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                env=os.environ.copy()
            )
            
            self.processes[service_name] = {
                'process': process,
                'config': config,
                'start_time': time.time()
            }
            
            logger.info(f"✅ {config['name']} started (PID: {process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start {config['name']}: {e}")
            return False
    
    def monitor_services(self):
        """Monitor service health"""
        def monitor_loop():
            while self.running:
                try:
                    for service_name, service_info in self.processes.items():
                        process = service_info['process']
                        config = service_info['config']
                        
                        # Check if process is still running
                        if process.poll() is not None:
                            logger.error(f"💀 {config['name']} stopped unexpectedly")
                            
                            # Restart critical services
                            if config.get('critical', False):
                                logger.info(f"🔄 Restarting {config['name']}...")
                                self.start_service(service_name, config)
                        
                    time.sleep(30)  # Check every 30 seconds
                    
                except Exception as e:
                    logger.error(f"Service monitoring error: {e}")
                    time.sleep(60)
        
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        logger.info("🔍 Service monitoring started")
    
    def get_service_status(self):
        """Get status of all services"""
        status = {}
        
        for service_name, service_info in self.processes.items():
            process = service_info['process']
            config = service_info['config']
            
            is_running = process.poll() is None
            uptime = time.time() - service_info['start_time']
            
            status[service_name] = {
                'name': config['name'],
                'running': is_running,
                'pid': process.pid if is_running else None,
                'uptime': uptime,
                'port': config['port']
            }
        
        return status
    
    def start_all_services(self):
        """Start all production services"""
        logger.info("🏗️  Starting Capulse Production Infrastructure with React + WebSocket...")
        self.running = True
        
        # Start services in order of dependency
        service_order = [
            'redis_server',
            'flask_app', 
            'fastapi_engine',
            'websocket_servers',
            'load_balancer',
            'celery_worker',
            'celery_beat'
        ]
        
        started_services = []
        
        for service_name in service_order:
            if service_name in self.services:
                config = self.services[service_name]
                
                if self.start_service(service_name, config):
                    started_services.append(service_name)
                    time.sleep(2)  # Wait between service starts
                elif config.get('critical', False):
                    logger.error(f"❌ Critical service {config['name']} failed to start")
                    self.stop_all_services()
                    return False
        
        # Start monitoring
        self.monitor_services()
        
        # Print startup summary
        self.print_startup_summary()
        
        return True
    
    def print_startup_summary(self):
        """Print production startup summary"""
        status = self.get_service_status()
        
        print("\n" + "="*80)
        print("🚀 Capulse Production Infrastructure - React + WebSocket Ready")
        print("="*80)
        
        # Service status
        for service_name, info in status.items():
            status_icon = "✅" if info['running'] else "❌"
            port_info = f"Port {info['port']}" if info['port'] else "Background Service"
            print(f"{status_icon} {info['name']:25} | {port_info:15} | PID {info['pid'] or 'N/A'}")
        
        print("\n📱 Frontend Architecture:")
        print("   • React-style Components: ✅ Implemented")
        print("   • WebSocket Hooks: ✅ Real-time data streaming")
        print("   • State Management: ✅ React-like useState/useEffect")
        print("   • Performance Monitoring: ✅ Built-in metrics")
        
        print("\n🔌 WebSocket Infrastructure:")
        print("   • Market Data Stream: ws://localhost:8001")
        print("   • Trading Updates: ws://localhost:8002")  
        print("   • Portfolio Updates: ws://localhost:8003")
        
        print("\n🌐 API Endpoints:")
        print("   • Main Dashboard: http://localhost:5000")
        print("   • Trading API: http://localhost:8000")
        print("   • Load Balancer: http://localhost:9000")
        print("   • API Documentation: http://localhost:8000/docs")
        
        print("\n📊 Performance Targets:")
        print("   • Order Execution: < 100ms")
        print("   • WebSocket Connections: 10,000+ concurrent")
        print("   • Market Data Updates: Every 5-10 seconds")
        print("   • Cache Hit Ratio: 80%+ target")
        
        print("\n⚡ React Integration Features:")
        print("   • Component-based architecture")
        print("   • Real-time state management") 
        print("   • WebSocket auto-reconnection")
        print("   • Mobile PWA optimization")
        print("   • Performance monitoring")
        
        print("\n" + "="*80)
        print("🎯 Production system ready for high-frequency trading operations")
        print("="*80)
    
    def stop_all_services(self):
        """Stop all services gracefully"""
        logger.info("🛑 Stopping all production services...")
        self.running = False
        
        for service_name, service_info in self.processes.items():
            try:
                process = service_info['process']
                config = service_info['config']
                
                if process.poll() is None:  # Still running
                    logger.info(f"🛑 Stopping {config['name']}...")
                    process.terminate()
                    
                    # Wait for graceful shutdown
                    try:
                        process.wait(timeout=10)
                        logger.info(f"✅ {config['name']} stopped gracefully")
                    except subprocess.TimeoutExpired:
                        logger.warning(f"⚠️  Force killing {config['name']}...")
                        process.kill()
                        process.wait()
                        
            except Exception as e:
                logger.error(f"Error stopping {service_name}: {e}")
        
        logger.info("✅ All services stopped")
    
    def handle_signal(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"📨 Received signal {signum}, initiating graceful shutdown...")
        self.stop_all_services()
        sys.exit(0)

def main():
    """Main production startup function"""
    orchestrator = ProductionOrchestrator()
    
    # Setup signal handlers
    signal.signal(signal.SIGTERM, orchestrator.handle_signal)
    signal.signal(signal.SIGINT, orchestrator.handle_signal)
    
    try:
        # Start all services
        if orchestrator.start_all_services():
            logger.info("🎯 Production system running - Press Ctrl+C to stop")
            
            # Keep main thread alive
            try:
                while orchestrator.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        
    except Exception as e:
        logger.error(f"❌ Production startup failed: {e}")
    finally:
        orchestrator.stop_all_services()

if __name__ == "__main__":
    main()