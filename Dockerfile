FROM python:3.11-slim

WORKDIR /app

# System dependencies
# libpq-dev + gcc: psycopg2 build
# curl: health-check probe during entrypoint
# tzdata: zoneinfo support on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (separate layer — only rebuilt when requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure entrypoint is executable
RUN chmod +x entrypoint.sh

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser \
    && chown -R appuser:appgroup /app
USER appuser

# Railway injects PORT at runtime (default 8080 in gunicorn.conf.py)
EXPOSE 8080

CMD ["./entrypoint.sh"]
