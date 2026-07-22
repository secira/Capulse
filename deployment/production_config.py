"""
Production Deployment Configuration
Optimized settings for production deployment with security and performance
"""

import os
import logging
from typing import Dict, Any

# Configure production logging
def setup_production_logging():
    """Setup production logging configuration"""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_file = os.environ.get("LOG_FILE", "/var/log/tcapital/app.log")
    
    # Create log directory if it doesn't exist
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()  # Keep console output
        ]
    )

class ProductionConfig:
    """Production configuration settings"""
    
    @staticmethod
    def get_gunicorn_config() -> Dict[str, Any]:
        """Get optimized Gunicorn configuration for production"""
        
        # Calculate workers based on CPU cores
        import multiprocessing
        workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
        
        return {
            "bind": f"0.0.0.0:{os.environ.get('PORT', '5000')}",
            "workers": workers,
            "worker_class": "gevent",  # Async worker for better performance
            "worker_connections": int(os.environ.get("GUNICORN_WORKER_CONNECTIONS", "1000")),
            "max_requests": int(os.environ.get("GUNICORN_MAX_REQUESTS", "10000")),
            "max_requests_jitter": int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "1000")),
            "timeout": int(os.environ.get("GUNICORN_TIMEOUT", "120")),
            "keepalive": int(os.environ.get("GUNICORN_KEEPALIVE", "5")),
            "preload_app": True,
            "enable_stdio_inheritance": True,
            "access_log_format": '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s',
            "errorlog": "/var/log/tcapital/gunicorn_error.log",
            "accesslog": "/var/log/tcapital/gunicorn_access.log",
            "loglevel": os.environ.get("GUNICORN_LOG_LEVEL", "info")
        }
    
    @staticmethod
    def get_uvicorn_config() -> Dict[str, Any]:
        """Get optimized Uvicorn configuration for FastAPI"""
        
        return {
            "host": "0.0.0.0",
            "port": int(os.environ.get("FASTAPI_PORT", "8000")),
            "workers": int(os.environ.get("UVICORN_WORKERS", "4")),
            "loop": "uvloop",
            "http": "httptools",
            "log_level": os.environ.get("UVICORN_LOG_LEVEL", "info"),
            "access_log": True,
            "use_colors": False,
            "proxy_headers": True,
            "forwarded_allow_ips": "*"
        }
    
    @staticmethod
    def get_nginx_config() -> str:
        """Generate optimized Nginx configuration"""
        
        return """
server {
    listen 80;
    server_name _;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    # SSL Configuration
    ssl_certificate /path/to/ssl/certificate.crt;
    ssl_certificate_key /path/to/ssl/private.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    
    # Security Headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin";
    
    # Gzip Compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/xml+rss application/json;
    
    # Static Files with Long Cache
    location /static/ {
        alias /path/to/static/files/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }
    
    # API Routes (FastAPI)
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket Support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Flask Routes (Web Interface)
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Health Check
    location /health {
        access_log off;
        proxy_pass http://127.0.0.1:8000/api/health;
    }
}
"""

class SystemdService:
    """Systemd service configuration"""
    
    @staticmethod
    def get_flask_service() -> str:
        """Get systemd service for Flask app"""
        
        return """[Unit]
Description=Capulse Flask Application
After=network.target postgresql.service redis.service
Requires=postgresql.service redis.service

[Service]
Type=notify
User=tcapital
Group=tcapital
WorkingDirectory=/opt/tcapital
Environment=PATH=/opt/tcapital/venv/bin
Environment=ENVIRONMENT=production
EnvironmentFile=/etc/tcapital/environment
ExecStart=/opt/tcapital/venv/bin/gunicorn --config /opt/tcapital/gunicorn.conf.py app:app
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
RestartSec=5
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
    
    @staticmethod
    def get_fastapi_service() -> str:
        """Get systemd service for FastAPI app"""
        
        return """[Unit]
Description=Capulse FastAPI Application
After=network.target postgresql.service redis.service
Requires=postgresql.service redis.service

[Service]
Type=simple
User=tcapital
Group=tcapital
WorkingDirectory=/opt/tcapital
Environment=PATH=/opt/tcapital/venv/bin
Environment=ENVIRONMENT=production
EnvironmentFile=/etc/tcapital/environment
ExecStart=/opt/tcapital/venv/bin/uvicorn hybrid_app:app --host 0.0.0.0 --port 8000 --workers 4
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
RestartSec=5
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

def create_deployment_files():
    """Create all production deployment files"""
    
    deployment_files = {
        "/opt/tcapital/gunicorn.conf.py": generate_gunicorn_config(),
        "/etc/nginx/sites-available/tcapital": ProductionConfig.get_nginx_config(),
        "/etc/systemd/system/tcapital-flask.service": SystemdService.get_flask_service(),
        "/etc/systemd/system/tcapital-fastapi.service": SystemdService.get_fastapi_service(),
    }
    
    for file_path, content in deployment_files.items():
        print(f"=== {file_path} ===")
        print(content)
        print()

def generate_gunicorn_config():
    """Generate Gunicorn configuration file"""
    config = ProductionConfig.get_gunicorn_config()
    
    return f"""# Gunicorn Configuration for Capulse Production

bind = "{config['bind']}"
workers = {config['workers']}
worker_class = "{config['worker_class']}"
worker_connections = {config['worker_connections']}
max_requests = {config['max_requests']}
max_requests_jitter = {config['max_requests_jitter']}
timeout = {config['timeout']}
keepalive = {config['keepalive']}
preload_app = {config['preload_app']}

# Logging
errorlog = "{config['errorlog']}"
accesslog = "{config['accesslog']}"
loglevel = "{config['loglevel']}"
access_log_format = '{config['access_log_format']}'

# Security
limit_request_line = 8192
limit_request_fields = 100
limit_request_field_size = 8190

def when_ready(server):
    server.log.info("Capulse Flask application ready to serve requests")

def on_exit(server):
    server.log.info("Capulse Flask application shutting down")
"""

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "create-files":
        create_deployment_files()
    else:
        print("Production configuration module loaded")
        print("Run with 'create-files' to generate deployment configurations")