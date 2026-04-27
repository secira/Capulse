#!/bin/bash
set -e

# Target Capital – post-merge setup script
# Runs automatically after every task branch is merged.
# Must be idempotent, non-interactive, and fast.

echo "=== Post-merge setup starting ==="

# Install / sync Python dependencies (pip, no prompts)
if [ -f requirements.txt ]; then
    pip install -q --no-input -r requirements.txt
    echo "✅ Python dependencies installed"
fi

# Apply new tables and all incremental column migrations.
# RUN_MIGRATIONS=1  — forces the ALTER TABLE block in app.py to run even when
#                     ENVIRONMENT=production is set (safe: all statements use
#                     IF NOT EXISTS / ON CONFLICT DO NOTHING).
# SKIP_SCHEDULER=1  — prevents APScheduler from starting in this non-serving run.
echo "--- Running schema migrations ---"
SKIP_SCHEDULER=1 RUN_MIGRATIONS=1 python3 - <<'PYEOF'
import os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

os.environ.setdefault('DATABASE_URL', os.environ.get('DATABASE_URL', ''))
os.environ.setdefault('SESSION_SECRET', 'post-merge-temp-secret')
os.environ['RUN_MIGRATIONS'] = '1'   # ensure the ALTER TABLE block runs

# Importing app triggers db.create_all() and the _pending_migrations block
# (which covers every ADD COLUMN IF NOT EXISTS, CREATE TABLE IF NOT EXISTS,
# and INSERT … ON CONFLICT DO NOTHING statement in the repo).
from app import app, db   # noqa: F401 — side-effects are the goal here
logging.info("✅ All schema migrations applied via app startup")
PYEOF

echo "=== Post-merge setup complete ==="
