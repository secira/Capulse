# Railway Deployment Guide for Target Capital

This guide explains how to deploy Target Capital to Railway.

## Prerequisites

1. A Railway account at https://railway.app
2. A PostgreSQL database (provision on Railway)
3. A Redis instance (provision on Railway — required for rate limits + shared cache)
4. The required environment variables listed below

---

## Required Environment Variables

These **must** be set in the Railway service Variables tab or the app
will refuse to boot.

| Variable | Description |
|----------|-------------|
| `ENVIRONMENT` | Must be `production` |
| `DATABASE_URL` | Postgres URL (auto-set by Railway when you attach the DB plugin) |
| `REDIS_URL` | Redis URL (auto-set when you attach the Redis plugin) — used for rate limits, AI insight cache, and F&O alert dedup |
| `SESSION_SECRET` | ≥32 random chars (used to sign Flask sessions) |
| `BROKER_ENCRYPTION_KEY` | Fernet key (44-char base64) used to encrypt stored broker tokens. **Generate once and never rotate** — rotating bricks every stored broker connection. |
| `ENCRYPTION_MASTER_KEY` | Master key for per-tenant field encryption (≥32 chars) |
| `CORS_ORIGINS` | Comma-separated allowed origins, e.g. `https://targetcapital.ai,https://www.targetcapital.ai`. **In production an empty value blocks all cross-origin requests.** |

### Generate the encryption keys

```bash
# BROKER_ENCRYPTION_KEY (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ENCRYPTION_MASTER_KEY (any high-entropy 32+ char string)
python -c "import secrets; print(secrets.token_urlsafe(48))"

# SESSION_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Optional Environment Variables

### AI features
| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI for LangGraph pipelines |
| `ANTHROPIC_API_KEY` | Claude for I-Score, Research, Behavioural narratives |
| `PERPLEXITY_API_KEY` | Real-time market research |

### Payments
| Variable | Description |
|----------|-------------|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Razorpay credentials |

### Notifications
| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` | SMS / WhatsApp |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | F&O & I-Score alert channel |

### Auth
| Variable | Description |
|----------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google sign-in |

### Tuning
| Variable | Default | Description |
|----------|---------|-------------|
| `GUNICORN_WORKERS` | 2 (entrypoint) | Increase to `(2*vCPU)+1` on bigger plans |
| `GUNICORN_THREADS` | 4 (entrypoint) | Threads per worker |
| `MESSAGING_HTTP_TIMEOUT` | 8 | Telegram/WhatsApp HTTP timeout (seconds) |
| `DISABLE_SCHEDULERS` | unset | Set to `1` on **non-web** Railway services (e.g. a separate worker) to prevent duplicate F&O / I-Score schedulers |

---

## One-time setup flags

These environment variables run heavy operations on the **next deploy only**.
Set them, deploy, watch logs, then **remove them** so subsequent deploys are fast.

| Variable | When to set | What it does |
|----------|-------------|--------------|
| `RUN_MIGRATIONS=1` | Anytime new columns / indexes ship in `_pending_migrations` (e.g. this release added 9 performance indexes and the encryption hardening) | Runs all `CREATE INDEX / ALTER TABLE IF NOT EXISTS` statements during boot. Skipped by default in prod because an `ALTER TABLE` waiting on a lock would hang the gunicorn master and fail Railway's health check. |
| `RUN_SEEDS=1` | First deploy of an empty database | Runs `railway_migrate.py` (I-Score data, blog posts, etc.) |

After each successful boot, **delete these vars** in the Railway dashboard.

---

## First-time deploy checklist

1. **Provision plugins**
   - Add **PostgreSQL** plugin → `DATABASE_URL` is auto-set.
   - Add **Redis** plugin → `REDIS_URL` is auto-set.

2. **Set the 7 required env vars** listed above (the 5 secrets + `ENVIRONMENT=production` + `CORS_ORIGINS`).

3. **First deploy:** also set `RUN_MIGRATIONS=1` and `RUN_SEEDS=1`.

4. Trigger the deploy. Watch logs for:
   - `✅ Environment configuration validated successfully`
   - `[migration N/N] running…` (only when `RUN_MIGRATIONS=1`)
   - `Target Capital server ready to accept connections`
   - `singleton worker` next to each scheduler line

5. Once the deploy is green, **remove `RUN_MIGRATIONS` and `RUN_SEEDS`** from variables.

6. Configure your custom domain and TLS in Railway → Settings.

---

## Subsequent deploys

After the first deploy you only push code. The boot path is:

1. `entrypoint.sh` syncs the NSE stock universe (idempotent, <1 s when complete).
2. Gunicorn loads `app.py` once with `--preload`.
3. Workers fork and start serving immediately.
4. Schedulers (F&O monitor + I-Score dispatcher) acquire a Postgres advisory lock so only **one** worker runs each scheduler — no duplicate alerts.

When you ship code that adds a new column or index to `_pending_migrations`, set `RUN_MIGRATIONS=1` for that one deploy.

---

## Health checks

Railway hits `/health` (configured in `railway.json`).

- `/health` — liveness, returns 200 if the app is running
- `/health/ready` — readiness, also pings Postgres + Redis (use this for k8s)
- `/health/live` — minimal liveness probe

`/health/ready` returns `degraded` if Redis is down — the app keeps serving but rate limits and shared caches fall back to per-worker memory.

---

## Database setup & data migration

### Step 1: Export from Replit (before deploying)
```bash
python scripts/db_export.py
```
Creates `database_export.sql` with users, brokers, portfolios, subscriptions,
blog posts, AI picks, watchlists, risk profiles, manual holdings.

### Step 2: First Railway deploy (see checklist above)

### Step 3: Import data into Railway

**Option A — Railway CLI:**
```bash
npm install -g @railway/cli
railway login
railway link
railway run python scripts/db_import.py
```

**Option B — direct psql:**
```bash
psql $RAILWAY_DATABASE_URL < database_export.sql
```

**Option C — Railway shell:**
1. Open the service in the Railway dashboard
2. Click "Shell"
3. Run: `python scripts/db_import.py`

### Skipped (regenerated automatically)
- `research_cache`, vector embeddings, sync logs, chat history.

### Important warnings
1. **Stop the Replit app before exporting** to ensure data consistency.
2. **Backup the Railway DB** before importing into a non-empty database.
3. The importer uses `ON CONFLICT DO NOTHING` — existing rows are not overwritten.
4. After importing broker data, **`BROKER_ENCRYPTION_KEY` must match** the one used on Replit, or the encrypted tokens will be unreadable.

---

## Operational notes

### Single-worker schedulers
Both `services/fno_monitor.py` and `services/iscore_alert_dispatcher.py`
acquire a Postgres advisory lock on startup. Only one gunicorn worker
ever runs them, so you can scale `GUNICORN_WORKERS` freely without
duplicating Telegram alerts or AI-cost. If you run a **separate**
Railway service (e.g. a worker), set `DISABLE_SCHEDULERS=1` on it.

### Shared state across workers
`services/state_store.py` provides a Redis-backed cache used by:
- Portfolio AI insights (2 h TTL per user)
- F&O alert dedup state (survives restarts so the daily cap isn't reset)

If `REDIS_URL` is unreachable, both fall back to per-worker memory and
log a warning.

### Rate limits
Per-user rate limits live on the expensive AI endpoints
(`/api/research/analyze`, `/api/behaviour/narrative`). They use Redis
storage when `REDIS_URL` is set.

### Encryption
- `BROKER_ENCRYPTION_KEY` encrypts broker access tokens at rest.
  Rotate **only** with a planned re-link of every account.
- `ENCRYPTION_MASTER_KEY` is the root for per-tenant field encryption
  (PII, etc.).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ENCRYPTION_MASTER_KEY is required in production` on boot | Missing required env var — see above |
| `BROKER_ENCRYPTION_KEY is required in production` on boot | Missing required env var |
| All cross-origin requests blocked from your frontend | `CORS_ORIGINS` not set or doesn't include your domain |
| Rate limits "leaking" between workers | `REDIS_URL` not set — flask-limiter falls back to in-memory per worker |
| Health check times out on first deploy | You set `RUN_MIGRATIONS=1` and an `ALTER TABLE` is waiting on a lock — check connection pool, retry once |
| Duplicate Telegram alerts | Two services running schedulers — set `DISABLE_SCHEDULERS=1` on the non-web one |
| Stored broker reconnect prompts after deploy | `BROKER_ENCRYPTION_KEY` changed (or wasn't set, so it derived from `SESSION_SECRET` which then changed) |
