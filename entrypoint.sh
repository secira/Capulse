#!/bin/bash
set -e

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
python railway_migrate.py
echo "Migrations done."

# Step 2: Seed research list stocks (501 stocks)
echo ""
echo "[2/5] Seeding research list..."
python seed_research_list.py
echo "Research list seed done."

# Step 3: Seed pre-computed I-Score data
echo ""
echo "[3/5] Seeding I-Score data..."
python seed_iscore_data.py
echo "I-Score seed done."

# Step 4: Seed blog posts (5 articles)
echo ""
echo "[4/5] Seeding blog posts..."
python seed_blog_posts.py
echo "Blog posts seed done."

# Step 5: Start the app
echo ""
echo "[5/5] Starting gunicorn..."
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 1 \
    --threads 8 \
    --worker-class gthread \
    --timeout 120 \
    main:app
