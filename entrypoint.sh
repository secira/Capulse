#!/bin/bash
# Note: intentionally NOT using 'set -e' — seed/migration failures should not
# abort the startup. Gunicorn must start even if seeds fail, so the healthcheck
# can pass and Railway marks the deployment healthy.

echo "======================================"
echo "Target Capital — Deployment Startup"
echo "======================================"

# ── Environment variable health check ──────────────────────────────────────
echo ""
echo "[0/5] Checking required environment variables..."

MISSING_VARS=0

check_var() {
  local name="$1"
  local required="$2"
  if [ -z "${!name}" ]; then
    if [ "$required" = "required" ]; then
      echo "  ❌  $name — NOT SET (REQUIRED)"
      MISSING_VARS=$((MISSING_VARS + 1))
    else
      echo "  ⚠️   $name — not set (optional but recommended)"
    fi
  else
    echo "  ✅  $name — set"
  fi
}

check_var DATABASE_URL      required
check_var SESSION_SECRET    required
check_var BROKER_ENCRYPTION_KEY optional
check_var ANTHROPIC_API_KEY optional
check_var GOOGLE_OAUTH_CLIENT_ID optional
check_var GOOGLE_OAUTH_CLIENT_SECRET optional
check_var TELEGRAM_BOT_TOKEN optional
check_var TELEGRAM_CHAT_ID  optional

if [ -z "$BROKER_ENCRYPTION_KEY" ]; then
  echo ""
  echo "  ℹ️   BROKER_ENCRYPTION_KEY not set — encryption key will be derived"
  echo "       from SESSION_SECRET (stable, works across restarts)."
  echo "       For maximum security, copy BROKER_ENCRYPTION_KEY from Replit"
  echo "       Secrets into Railway → Variables."
fi

if [ "$MISSING_VARS" -gt 0 ]; then
  echo ""
  echo "❌ $MISSING_VARS required variable(s) are missing. Aborting startup."
  exit 1
fi

echo "Environment check passed."
# ───────────────────────────────────────────────────────────────────────────

# Step 1: Run database migrations (creates tables, tenant, etc.)
echo ""
echo "[1/5] Running database migrations..."
python railway_migrate.py || echo "⚠️  Migrations exited non-zero — app will attempt incremental migrations on startup."
echo "Migrations step done."

# Step 2: Seed research list stocks (501 stocks)
echo ""
echo "[2/5] Seeding research list..."
python seed_research_list.py || echo "⚠️  Research list seed failed — continuing anyway."
echo "Research list seed step done."

# Step 3: Seed pre-computed I-Score data
echo ""
echo "[3/5] Seeding I-Score data..."
python seed_iscore_data.py || echo "⚠️  I-Score seed failed — continuing anyway."
echo "I-Score seed step done."

# Step 4: Seed blog posts (5 articles)
echo ""
echo "[4/5] Seeding blog posts..."
python seed_blog_posts.py || echo "⚠️  Blog post seed failed — continuing anyway."
echo "Blog seed step done."

# Step 5: Start the app
# --preload loads app.py ONCE in the master process before forking workers.
# Workers are then forked from the pre-loaded master (fast, via copy-on-write),
# so they are ready to serve /health almost immediately.
# Without --preload each worker independently loads the full app which takes
# 60-120 s, causing Railway's healthcheck to time out before they are ready.
echo ""
echo "[5/5] Starting gunicorn (preload enabled for fast worker readiness)..."
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --timeout 120 \
    --preload \
    main:app
