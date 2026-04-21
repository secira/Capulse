#!/bin/bash
# Minimal Railway entrypoint.
#
# We deliberately do NOT run a separate migration/seed Python process here.
# app.py itself runs db.create_all() and incremental column migrations on
# every startup (both are idempotent), so the database schema is always
# correct as soon as gunicorn loads the app.
#
# Seeding (research list, I-Score, blog posts) is opt-in via RUN_SEEDS=1.
# Set it once on a fresh database, then unset it.  This keeps normal
# deploys fast and ensures /health responds well within Railway's
# healthcheck window.

echo "======================================"
echo "Target Capital — Deployment Startup"
echo "======================================"

# ─── Optional one-shot seeding (only when RUN_SEEDS=1) ───────────────────
if [ "${RUN_SEEDS}" = "1" ]; then
    echo ""
    echo "RUN_SEEDS=1 detected — running railway_migrate.py once."
    echo "After this deploy succeeds, unset RUN_SEEDS in Railway variables."
    SKIP_SCHEDULER=1 python railway_migrate.py || echo "⚠️  Seed step exited non-zero; continuing to gunicorn."
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
