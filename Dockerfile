FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (libpq-dev for psycopg2, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying app code
# (separate layer — only rebuilt when requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Create a non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

# Railway injects PORT at runtime; 8080 is its default
EXPOSE 8080

CMD ["./entrypoint.sh"]
