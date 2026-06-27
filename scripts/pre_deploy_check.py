#!/usr/bin/env python3
"""
Target Capital — Pre-Deployment Validation Script
===================================================
Run this BEFORE deploying to Railway to verify every required piece is in place.

Usage (on Replit, with your Railway env vars loaded):
    python scripts/pre_deploy_check.py

Usage (on Railway shell after first deploy):
    python scripts/pre_deploy_check.py

Exit codes:
    0  — all REQUIRED checks pass (WARNINGS are fine)
    1  — one or more REQUIRED checks failed
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── colour helpers ──────────────────────────────────────────────────────────
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    GREEN  = Fore.GREEN  + Style.BRIGHT
    RED    = Fore.RED    + Style.BRIGHT
    YELLOW = Fore.YELLOW + Style.BRIGHT
    CYAN   = Fore.CYAN   + Style.BRIGHT
    RESET  = Style.RESET_ALL
except ImportError:
    GREEN = RED = YELLOW = CYAN = RESET = ""

PASS    = f"{GREEN}✓ PASS{RESET}"
FAIL    = f"{RED}✗ FAIL{RESET}"
WARN    = f"{YELLOW}⚠ WARN{RESET}"
INFO    = f"{CYAN}ℹ INFO{RESET}"

results = {"pass": 0, "fail": 0, "warn": 0}

def check(label: str, ok: bool, message: str = "", required: bool = True):
    tag = PASS if ok else (FAIL if required else WARN)
    key = "pass" if ok else ("fail" if required else "warn")
    results[key] += 1
    suffix = f"  →  {message}" if message else ""
    print(f"  {tag}  {label}{suffix}")
    return ok


def section(title: str):
    print()
    print(f"{CYAN}{'─' * 60}{RESET}")
    print(f"{CYAN}  {title}{RESET}")
    print(f"{CYAN}{'─' * 60}{RESET}")


# ── 1. Required environment variables ──────────────────────────────────────

section("1 / Required environment variables")

REQUIRED_VARS = [
    ("DATABASE_URL",           "PostgreSQL connection string (Railway auto-sets this)"),
    ("SESSION_SECRET",         "≥32 random chars — signs Flask sessions"),
    ("BROKER_ENCRYPTION_KEY",  "44-char Fernet key — encrypts broker tokens at rest"),
    ("ENCRYPTION_MASTER_KEY",  "≥32 chars — per-tenant field encryption root"),
    ("ENVIRONMENT",            "Must equal 'production'"),
    ("CORS_ORIGINS",           "Comma-separated allowed origins, e.g. https://targetcapital.ai"),
]

for var, desc in REQUIRED_VARS:
    val = os.environ.get(var, "")
    check(var, bool(val), f"MISSING — {desc}" if not val else "")

check(
    "ENVIRONMENT value",
    os.environ.get("ENVIRONMENT") == "production",
    f"currently '{os.environ.get('ENVIRONMENT')}' — must be 'production'",
)

# Fernet key format validation
fernet_key = os.environ.get("BROKER_ENCRYPTION_KEY", "")
if fernet_key:
    try:
        from cryptography.fernet import Fernet
        Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
        check("BROKER_ENCRYPTION_KEY format", True, "valid Fernet key")
    except Exception as e:
        check("BROKER_ENCRYPTION_KEY format", False, f"invalid Fernet key: {e}")

session_secret = os.environ.get("SESSION_SECRET", "")
check(
    "SESSION_SECRET length",
    len(session_secret) >= 32,
    f"length {len(session_secret)} — needs ≥32 chars",
)


# ── 2. Optional but important env vars ─────────────────────────────────────

section("2 / Optional environment variables (feature-gating)")

OPTIONAL_VARS = [
    ("REDIS_URL",                "Rate limits + AI cache + F&O dedup — highly recommended"),
    ("OPENAI_API_KEY",           "LangGraph pipelines (Research, Portfolio, Signals)"),
    ("ANTHROPIC_API_KEY",        "Claude — I-Score, Behavioural narratives, Research"),
    ("PERPLEXITY_API_KEY",       "Real-time market research in Co-Pilot"),
    ("GOOGLE_OAUTH_CLIENT_ID",   "Google sign-in"),
    ("GOOGLE_OAUTH_CLIENT_SECRET","Google sign-in"),
    ("RAZORPAY_KEY_ID",          "Payments / subscriptions"),
    ("RAZORPAY_KEY_SECRET",      "Payments / subscriptions"),
    ("TWILIO_ACCOUNT_SID",       "SMS / WhatsApp OTP"),
    ("TWILIO_AUTH_TOKEN",        "SMS / WhatsApp OTP"),
    ("TWILIO_PHONE_NUMBER",      "SMS / WhatsApp OTP"),
    ("TELEGRAM_BOT_TOKEN",       "F&O alerts + deployment pings"),
    ("TELEGRAM_CHAT_ID",         "F&O alerts channel"),
    ("ADMIN_EMAILS",             "Comma-separated emails auto-promoted to admin on login"),
    ("APP_DOMAIN",               "Railway production domain (e.g. targetcapital.ai) — used in OAuth debug page"),
    ("EXECUTION_ENGINE_URL",     "TC Engine on EC2 — live trade routing"),
]

for var, desc in OPTIONAL_VARS:
    val = os.environ.get(var, "")
    check(var, bool(val), f"not set — {desc}", required=False)


# ── 3. Database connectivity ────────────────────────────────────────────────

section("3 / Database connectivity")

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    check("Database connection", False, "DATABASE_URL not set — skipping connection test")
else:
    try:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif db_url.startswith("postgresql://") and "+psycopg2" not in db_url:
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

        from sqlalchemy import create_engine, text
        engine = create_engine(db_url, connect_args={"connect_timeout": 10})
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0].split(",")[0]
        check("Database connection", True, version)

        # Check pgvector extension (needed for RAG)
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT 1 FROM pg_extension WHERE extname='vector'"
            ))
            has_vector = result.fetchone() is not None
        check("pgvector extension", has_vector,
              "not installed — run: CREATE EXTENSION vector;" if not has_vector else "installed",
              required=False)

        # Check key tables exist
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            ))
            existing = {row[0] for row in result}

        KEY_TABLES = ["user", "tenants", "user_brokers", "subscription",
                      "research_list", "fno_signal_history"]
        missing_tables = [t for t in KEY_TABLES if t not in existing]
        check("Key tables exist", not missing_tables,
              f"missing: {missing_tables} — run railway_migrate.py" if missing_tables else
              f"{len(existing)} tables found")

        # research_list row count
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM research_list"))
            rl_count = result.fetchone()[0] if "research_list" in existing else 0
        check("Research list (NSE stocks)", rl_count >= 2167,
              f"{rl_count} rows — expect 2167; run: python seed_research_list.py" if rl_count < 2167 else f"{rl_count} stocks",
              required=False)

    except Exception as e:
        check("Database connection", False, str(e)[:120])


# ── 4. Redis connectivity ───────────────────────────────────────────────────

section("4 / Redis connectivity")

redis_url = os.environ.get("REDIS_URL", "")
if not redis_url:
    check("Redis connection", False,
          "REDIS_URL not set — rate limits and caches will be in-process only",
          required=False)
else:
    try:
        import redis as redis_lib
        r = redis_lib.from_url(redis_url, socket_connect_timeout=5)
        pong = r.ping()
        check("Redis connection", pong, "PONG received")
    except ImportError:
        check("Redis connection", False, "redis package not installed", required=False)
    except Exception as e:
        check("Redis connection", False, str(e)[:100], required=False)


# ── 5. AI / external services ──────────────────────────────────────────────

section("5 / AI and external service credentials")

import requests as _req

def _head(url, headers=None, timeout=8):
    try:
        r = _req.get(url, headers=headers or {}, timeout=timeout)
        return r.status_code
    except Exception as e:
        return str(e)[:60]

anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
if anthropic_key:
    status = _head("https://api.anthropic.com/v1/models",
                   {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"})
    check("Anthropic API key", status == 200,
          f"HTTP {status}" if status != 200 else "reachable", required=False)
else:
    check("Anthropic API key", False, "not set", required=False)

openai_key = os.environ.get("OPENAI_API_KEY", "")
if openai_key:
    status = _head("https://api.openai.com/v1/models",
                   {"Authorization": f"Bearer {openai_key}"})
    check("OpenAI API key", status == 200,
          f"HTTP {status}" if status != 200 else "reachable", required=False)
else:
    check("OpenAI API key", False, "not set", required=False)

telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if telegram_token:
    status = _head(f"https://api.telegram.org/bot{telegram_token}/getMe")
    check("Telegram bot token", status == 200,
          f"HTTP {status}" if status != 200 else "bot reachable", required=False)
else:
    check("Telegram bot token", False, "not set", required=False)

razorpay_id  = os.environ.get("RAZORPAY_KEY_ID", "")
razorpay_sec = os.environ.get("RAZORPAY_KEY_SECRET", "")
if razorpay_id and razorpay_sec:
    try:
        r = _req.get("https://api.razorpay.com/v1/payments?count=1",
                     auth=(razorpay_id, razorpay_sec), timeout=8)
        check("Razorpay credentials", r.status_code in (200, 400),
              "authenticated" if r.status_code in (200, 400) else f"HTTP {r.status_code}",
              required=False)
    except Exception as e:
        check("Razorpay credentials", False, str(e)[:80], required=False)
else:
    check("Razorpay credentials", False, "not set", required=False)


# ── 6. Google OAuth ─────────────────────────────────────────────────────────

section("6 / Google OAuth")

gclient_id  = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
gclient_sec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
app_domain  = os.environ.get("APP_DOMAIN", "")

check("GOOGLE_OAUTH_CLIENT_ID",     bool(gclient_id),  required=False)
check("GOOGLE_OAUTH_CLIENT_SECRET", bool(gclient_sec), required=False)
check("APP_DOMAIN",                 bool(app_domain),
      "Set this to your Railway/custom domain so the redirect URI is correct in logs",
      required=False)

if gclient_id and app_domain:
    expected_uri = f"https://{app_domain}/google_login/callback"
    print()
    print(f"  {INFO}  Expected redirect URI: {CYAN}{expected_uri}{RESET}")
    print(f"  {INFO}  This URI must be in Google Cloud Console →")
    print(f"  {INFO}  APIs & Services → Credentials → OAuth 2.0 Client → Authorized redirect URIs")
    print()
elif gclient_id:
    print()
    print(f"  {WARN}  Set APP_DOMAIN to see the exact redirect URI you need to add to Google Console.")
    print()


# ── 7. Security checks ──────────────────────────────────────────────────────

section("7 / Security configuration")

check("SESSION_COOKIE_SECURE",
      os.environ.get("ENVIRONMENT") == "production",
      "Will be forced True in production mode — OK", required=False)

cors = os.environ.get("CORS_ORIGINS", "")
if cors:
    origins = [o.strip() for o in cors.split(",") if o.strip()]
    has_wildcard = "*" in origins
    check("CORS_ORIGINS no wildcard", not has_wildcard,
          "contains '*' — this allows any origin to call your APIs in production",
          required=False)
    check("CORS_ORIGINS uses HTTPS", all(o.startswith("https://") for o in origins),
          "all origins should start with https:// in production", required=False)

admin_emails = os.environ.get("ADMIN_EMAILS", "")
check("ADMIN_EMAILS set", bool(admin_emails),
      "Not set — you can promote admins manually from /admin; or set ADMIN_EMAILS=your@email.com",
      required=False)


# ── 8. Health endpoint check (if running) ──────────────────────────────────

section("8 / App health endpoint (optional — only if app is already running)")

port = os.environ.get("PORT", "8080")
try:
    r = _req.get(f"http://localhost:{port}/health", timeout=5)
    check("GET /health", r.status_code == 200,
          f"HTTP {r.status_code}" if r.status_code != 200 else "200 OK", required=False)
except Exception:
    check("GET /health", False,
          "App not reachable on localhost — run this check after starting gunicorn",
          required=False)


# ── Summary ─────────────────────────────────────────────────────────────────

section("Summary")
print()
total = sum(results.values())
print(f"  Total checks : {total}")
print(f"  {GREEN}{results['pass']:2d} passed{RESET}")
print(f"  {YELLOW}{results['warn']:2d} warnings{RESET}  (optional features)")
print(f"  {RED}{results['fail']:2d} failed{RESET}   (required — must fix before deploying)")
print()

if results["fail"] == 0:
    print(f"  {GREEN}✓ GO — all required checks passed.{RESET}")
    print()
    print("  Next steps:")
    print("   1. If Railway DB is fresh: set RUN_SEEDS=1, deploy, then remove it.")
    print("   2. After first deploy open /admin/notifications to verify all integrations.")
    print("   3. Add your Railway domain to Google Cloud Console → Authorized redirect URIs.")
else:
    print(f"  {RED}✗ NO-GO — {results['fail']} required check(s) failed.{RESET}")
    print("  Fix the FAIL items above, then re-run this script.")

print()
sys.exit(0 if results["fail"] == 0 else 1)
