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

    # ─── Optional notification / integration channel checks (non-blocking) ──
    # These mirror the probes shown on /admin/notifications. Missing values
    # don't block boot — they just degrade specific features.
    _warn_if_missing() {
        # $1=label  $2..=env var names (any missing → warn)
        local label="$1"; shift
        local miss=""
        for v in "$@"; do
            if [ -z "$(eval echo \$$v)" ]; then miss="$miss $v"; fi
        done
        if [ -n "$miss" ]; then
            echo "  ℹ️  $label DISABLED — missing:$miss"
        else
            echo "  ✓ $label credentials present"
        fi
    }
    _warn_if_missing "Telegram alerts"        TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
    _warn_if_missing "Anthropic (Claude)"     ANTHROPIC_API_KEY
    _warn_if_missing "OpenAI (GPT)"           OPENAI_API_KEY
    _warn_if_missing "Perplexity (Sonar)"     PERPLEXITY_API_KEY
    _warn_if_missing "Razorpay (billing)"     RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET
    _warn_if_missing "Twilio (SMS/WhatsApp)"  TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_PHONE_NUMBER
    _warn_if_missing "Google OAuth"           GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET
    # ─── TC Execution Engine ─────────────────────────────────────────────
    if [ -n "${EXECUTION_ENGINE_URL}" ]; then
        echo "  ✓ TC Execution Engine URL   : ${EXECUTION_ENGINE_URL}"
        if [ -n "${EXECUTION_HMAC_SECRET}" ]; then
            echo "  ✓ EXECUTION_HMAC_SECRET     : SET"
        else
            echo "  ⚠️  EXECUTION_HMAC_SECRET NOT SET — engine calls will be rejected (401)"
        fi
        if [ "${USE_REMOTE_EXEC}" = "true" ] || [ "${USE_REMOTE_EXEC}" = "1" ]; then
            echo "  ✓ USE_REMOTE_EXEC           : ON — trades route to engine"
        else
            echo "  ℹ️  USE_REMOTE_EXEC          : OFF — trades use in-process path"
        fi
    else
        echo "  ℹ️  TC Execution Engine routing OFF — set EXECUTION_ENGINE_URL to enable."
    fi
    echo "  → Verify everything live at /admin/notifications after boot."
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

# ─── Always: seed the admin account (idempotent, hash-only) ──────────────
# seed_admin.py creates/updates the admin login on every deploy so the
# account always exists in the production database. It stores only a
# password hash, never a plaintext password.
echo ""
echo "Seeding admin account..."
SKIP_SCHEDULER=1 python seed_admin.py \
    || echo "⚠️  Admin seed exited non-zero; continuing to gunicorn."

# ─── Optional: create / reset admin user (only when ADMIN_EMAIL is set) ──
# Set ADMIN_EMAIL + ADMIN_PASSWORD in Railway Variables for the first deploy,
# then delete both variables — the script is idempotent and safe to re-run.
if [ -n "${ADMIN_EMAIL}" ] && [ -n "${ADMIN_PASSWORD}" ]; then
    echo ""
    echo "ADMIN_EMAIL set — creating/resetting admin user (${ADMIN_EMAIL})..."
    SKIP_SCHEDULER=1 python reset_admin.py --env \
        && echo "  ✓ Admin user ready. Remove ADMIN_EMAIL + ADMIN_PASSWORD from Railway Variables." \
        || echo "  ⚠️  reset_admin.py exited non-zero; continuing to gunicorn."
fi

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

# ─── Railway deployment notification ────────────────────────────────────
# Sends a Telegram ping when this is a production Railway deploy so the
# admin knows exactly when a new build went live.  Non-blocking: a failed
# send (missing token, network hiccup) is logged as a warning but never
# prevents gunicorn from starting.
if [ "${ENVIRONMENT}" = "production" ] && \
   [ -n "${TELEGRAM_BOT_TOKEN}" ] && \
   [ -n "${TELEGRAM_CHAT_ID}" ]; then
    echo ""
    echo "Sending Railway deployment notification to Telegram..."
    python - <<'PYEOF'
import os, datetime, requests
token   = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]
now     = datetime.datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
railway_env  = os.environ.get("RAILWAY_ENVIRONMENT", "production")
railway_svc  = os.environ.get("RAILWAY_SERVICE_NAME", "target-capital")
railway_dep  = os.environ.get("RAILWAY_DEPLOYMENT_ID", "—")
msg = (
    "🚀 <b>Target Capital — Deployed on Railway</b>\n\n"
    f"🌐 Environment : <code>{railway_env}</code>\n"
    f"🔧 Service     : <code>{railway_svc}</code>\n"
    f"🆔 Deploy ID   : <code>{railway_dep}</code>\n"
    f"🕒 Time        : <code>{now}</code>\n\n"
    "Server is starting. <b>/health</b> will be live within seconds."
)
try:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
        timeout=8,
    )
    if r.ok:
        print("  ✓ Deployment notification sent to Telegram")
    else:
        print(f"  ⚠️  Telegram responded {r.status_code}: {r.text[:120]}")
except Exception as e:
    print(f"  ⚠️  Telegram notification skipped: {e}")
PYEOF
fi

# ─── Start the app ───────────────────────────────────────────────────────
# gunicorn.conf.py drives all tuning (workers, threads, timeouts, logging).
# PORT is read inside gunicorn.conf.py from the environment — Railway always
# sets it; the default there is 8080 to match the Dockerfile EXPOSE.
echo ""
echo "Starting gunicorn (config: gunicorn.conf.py)..."
exec gunicorn -c gunicorn.conf.py main:app
