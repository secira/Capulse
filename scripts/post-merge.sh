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

# Apply any new database columns / tables (Flask-SQLAlchemy create_all is safe/idempotent)
python3 - <<'PYEOF'
import os, sys
os.environ.setdefault('DATABASE_URL', os.environ.get('DATABASE_URL', ''))
from app import app, db
with app.app_context():
    db.create_all()
    print("✅ Database tables synced")
PYEOF

echo "=== Post-merge setup complete ==="
