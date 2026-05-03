#!/bin/bash
# Railway entrypoint for Target Capital.
#
# Schema migrations: app.py runs db.create_all() and ADD COLUMN IF NOT EXISTS
# on every startup — the schema is always correct when gunicorn loads.
#
# Research list: seed_research_list.py runs on EVERY deploy.  It checks the
# current row count first and exits in <1 s when the list is already complete
# (2167 stocks).  Only on a fresh or partial database does it insert the
# missing rows — so normal redeploys are not slowed down.
#
# Heavy seeds (I-Score data, blog posts): opt-in via RUN_SEEDS=1.
# Set it once on a fresh database, then unset it.

echo "======================================"
echo "Target Capital — Deployment Startup"
echo "======================================"

# ─── Pre-flight: warn (don't fail) if required prod env vars are missing ─
# The app's own validators will raise on boot if anything is truly missing,
# but printing a clear list here makes Railway logs much easier to read.
if [ "${ENVIRONMENT}" = "production" ]; then
    echo ""
    echo "Pre-flight check (production mode)…"
    _missing=""
    for v in DATABASE_URL REDIS_URL SESSION_SECRET BROKER_ENCRYPTION_KEY ENCRYPTION_MASTER_KEY CORS_ORIGINS; do
        if [ -z "$(eval echo \$$v)" ]; then
            _missing="$_missing $v"
        fi
    done
    if [ -n "$_missing" ]; then
        echo "  ⚠️  Missing required env vars:$_missing"
        echo "  ⚠️  See RAILWAY_DEPLOYMENT.md → Required Environment Variables"
    else
        echo "  ✓ All required production env vars present"
    fi
    if [ "${RUN_MIGRATIONS}" = "1" ]; then
        echo "  ℹ️  RUN_MIGRATIONS=1 — schema/index migrations WILL run"
        echo "     (remove this flag after a successful deploy)"
    fi
fi

# ─── Always: ensure all 2167 NSE stocks are in research_list ─────────────
# seed_research_list.py is idempotent and fast-paths out (<1 s) when the
# table is already fully populated.  This fixes any Railway DB that received
# a partial seed on the first deploy.
echo ""
echo "Syncing NSE stock universe (research_list)..."
SKIP_SCHEDULER=1 python seed_research_list.py \
    || echo "⚠️  Research list seed exited non-zero; continuing to gunicorn."

# ─── Optional: heavy one-shot seeding (only when RUN_SEEDS=1) ────────────
# Covers: I-Score data, blog posts, and full railway_migrate.py flow.
# Set RUN_SEEDS=1 on first deploy of a blank database, then unset it.
if [ "${RUN_SEEDS}" = "1" ]; then
    echo ""
    echo "RUN_SEEDS=1 detected — running railway_migrate.py once."
    echo "After this deploy succeeds, unset RUN_SEEDS in Railway variables."
    SKIP_SCHEDULER=1 python railway_migrate.py \
        || echo "⚠️  Seed step exited non-zero; continuing to gunicorn."
fi

# ─── Start the app ───────────────────────────────────────────────────────
# --preload loads app.py ONCE in the master process.  Workers are then
# forked (copy-on-write, <1 s) and ready to serve /health immediately.
echo ""
echo "Starting gunicorn..."
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --timeout 120 \
    --preload \
    --access-logfile - \
    --error-logfile - \
    main:app
