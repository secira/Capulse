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
# SKIP_SCHEDULER prevents APScheduler from starting inside the migration/seed
# subprocess.  APScheduler uses daemon threads, but this is a belt-and-braces
# guard so the migration process always exits cleanly and quickly.
export SKIP_SCHEDULER=1

# All seeding (research list, I-Score, blog posts) is consolidated inside
# railway_migrate.py so that app.py is loaded ONLY ONCE during the setup
# phase.  Previously, 4 separate Python processes each loaded app.py (~30-60 s
# each) = 120-240 s overhead before gunicorn even started, blowing the 300 s
# Railway healthcheck window.
echo ""
echo "[1/2] Running migrations + seeding all data..."
python railway_migrate.py || echo "⚠️  Migrations/seeds exited non-zero — app will handle the rest on startup."
echo "Migrations + seeds done."

# Clear the guard before gunicorn so APScheduler and all background tasks run.
unset SKIP_SCHEDULER

# Step 2: Start the app.
# --preload loads app.py ONCE in the master process.  Workers are then forked
# (copy-on-write, <1 s), so /health is available within seconds of gunicorn
# starting — well inside Railway's 300 s healthcheck window.
echo ""
echo "[2/2] Starting gunicorn (preload + scheduler enabled)..."
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --timeout 120 \
    --preload \
    main:app
