"""
Optimized Gunicorn Configuration for Production Performance
"""

import multiprocessing
import os

# Server socket — PORT is set by Railway automatically; default 8080 matches Dockerfile EXPOSE
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 8)))
worker_class = "gthread"  # Thread workers compatible with Twilio client
threads = int(os.environ.get("GUNICORN_THREADS", "8"))  # Thread count per worker
max_requests = 10000
max_requests_jitter = 1000

# Worker timeout and keep-alive
timeout = 300  # Increased from 120 to handle complex operations
keepalive = 5
graceful_timeout = 60

# Performance optimizations
preload_app = True  # Load application code before forking workers
enable_stdio_inheritance = True
reuse_port = True

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'target-capital-gunicorn'

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

def post_fork(server, worker):
    """Dispose the inherited SQLAlchemy connection pool in each worker after fork.

    With preload_app=True the master process loads the app (and may open DB
    connections during startup migrations). Without this hook, forked workers
    inherit those connections and share them — leading to 'connection already
    closed' / 'SSL connection has been closed unexpectedly' errors under load.
    Calling dispose() forces each worker to open fresh connections from its own
    pool on first use.
    """
    try:
        from db_instance import db
        db.engine.dispose()
    except Exception:
        pass


def when_ready(server):
    """Called just after server is started"""
    server.log.info("Capulse server ready to accept connections")

def worker_abort(worker):
    """Called when a worker received the SIGABRT signal"""
    worker.log.info("Worker received SIGABRT signal")

def on_exit(server):
    """Called just before exiting"""
    server.log.info("Capulse server shutting down")