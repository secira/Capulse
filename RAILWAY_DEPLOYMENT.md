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
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | F&O, I-Score, and Daily Signal alert channel. Verify after deploy at **`/admin/telegram`** — the page runs live diagnostics (token format, bot reachability, chat ID presence) and lets admins send test broadcasts and re-share recent signals. New "Our Signal" entries auto-broadcast on create unless the admin unticks the box. |

### Auth
| Variable | Description |
|----------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google sign-in |
| `APP_DOMAIN` | Your Railway/custom domain (e.g. `targetcapital.ai`). Shown in `/oauth-check` so you know exactly which URI to register in Google Console. Not required for OAuth to work — the app uses `request.base_url` dynamically — but recommended for clarity. |

### Admin
| Variable | Description |
|----------|-------------|
| `ADMIN_EMAILS` | Comma-separated list of email addresses that are auto-promoted to admin on first Google/email login (e.g. `you@targetcapital.ai,partner@example.com`). Admins can also be promoted manually from `/admin/users`. |
| `TELEGRAM_ADMIN_CHAT_ID` | Secondary Telegram chat ID for critical admin-only alerts (separate from the public signal channel in `TELEGRAM_CHAT_ID`). Optional. |

### TC Execution Engine (EC2)

Target Capital routes live trade execution to a dedicated stateless TC-Engine
running on EC2.  Three variables control the integration:

| Variable | Value | Description |
|----------|-------|-------------|
| `EXECUTION_ENGINE_URL` | `http://54.225.202.78:8080` | Base URL of the TC-Engine on EC2 |
| `EXECUTION_HMAC_SECRET` | *(shared secret — see below)* | HMAC-SHA256 signing key — **must match** the value set on the EC2 engine |
| `USE_REMOTE_EXEC` | `true` | Enables engine routing at the env level. Per-user opt-in (`User.use_remote_execution`) is a second gate |

**Generate a shared HMAC secret (do this once):**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Set the **same value** in:
- Railway → Variables → `EXECUTION_HMAC_SECRET`
- EC2 instance → environment / `.env` → `EXECUTION_HMAC_SECRET`

**EC2 Security Group — allow Railway's outbound IPs on port 8080:**

Railway's static egress IPs (add all as `/32` inbound rules for TCP port 8080):
```
52.15.224.211
18.218.184.132
18.222.27.208
```
> If you are on Railway Hobby (no static IPs), temporarily open `0.0.0.0/0` on port 8080.
> The HMAC signature on every request means only requests signed with the correct
> `EXECUTION_HMAC_SECRET` will be accepted by the engine — random internet traffic is rejected.

**Verify after deploy:**
Go to **Admin → Execution Engine** (`/admin/execution-engine`).
The page shows a live `/healthz` ping, engine version, halt state, and which users
have the per-user opt-in enabled.

### Tuning
| Variable | Default | Description |
|----------|---------|-------------|
| `GUNICORN_WORKERS` | 2 (entrypoint) | Increase to `(2*vCPU)+1` on bigger plans |
| `GUNICORN_THREADS` | 4 (entrypoint) | Threads per worker |
| `MESSAGING_HTTP_TIMEOUT` | 8 | Telegram/WhatsApp HTTP timeout (seconds) |
| `DISABLE_SCHEDULERS` | unset | Set to `1` on **non-web** Railway services (e.g. a separate worker) to prevent duplicate F&O / I-Score schedulers |

---

## Post-deploy verification — `/admin/notifications`

After every Railway deploy, sign in as admin and open **`/admin/notifications`**.
It runs live health probes (5 s timeout each) against every external
dependency the platform uses and renders one card per check, grouped by
category:

| Category                | What it covers |
|-------------------------|----------------|
| Messaging & Alerts      | Telegram bot (`getMe`), Twilio account |
| Market Data Sources     | Primary & Secondary Admin Data Brokers, yfinance fallback |
| Partner Broker Connections | One card per broker (Dhan / Zerodha / Angel / …) showing connected vs auth-rejected vs sync-failed counts. Surfaces real licensing problems (e.g. Dhan DH-901 "invalid access token"). |
| AI / LLM Providers      | Anthropic `/v1/models`, OpenAI `/v1/models`, Perplexity key presence |
| Billing & Payments      | Razorpay `/v1/payments` auth |
| TC Execution Engine     | `$EXECUTION_ENGINE_URL/healthz` + version |
| Scheduled / Nightly Jobs| APScheduler liveness + one card per `alert_schedule` row |

A red **FAIL** card means real user impact — fix critical/high-severity items
first. Disabled cards just indicate the corresponding env var isn't set.

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

## Google OAuth — production setup

The app dynamically uses `request.base_url` for the redirect URI, so it works
on any domain without code changes. You only need to register the right URI in
Google Console once.

**Steps:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **Credentials**.
2. Open your **OAuth 2.0 Client ID**.
3. Under **Authorized redirect URIs** add:
   ```
   https://<your-railway-domain>/google_login/callback
   ```
   Replace `<your-railway-domain>` with your actual Railway-generated URL
   (e.g. `target-capital-production.up.railway.app`) **and** your custom domain
   if you have one (e.g. `targetcapital.ai`). Add both as separate entries.
4. Make sure **Authorized JavaScript origins** includes `https://<your-domain>`.
5. Under **OAuth consent screen**, set **User Type** to **External** (not Internal) so users outside your Google Workspace can sign in.
6. Save. Changes take effect within a few minutes.

**Verify:** Open `/oauth-check` after deploying — it shows the exact redirect URI
the app will send to Google. Paste it into Console if it isn't there yet.

**Popup mode (Replit preview):** The app automatically detects when it's loaded
inside an iframe and opens a popup for Google sign-in. On Railway (standalone
browser tab) it navigates directly — no popup, no extra setup needed.

---

## First-time deploy checklist

1. **Provision plugins**
   - Add **PostgreSQL** plugin → `DATABASE_URL` is auto-set.
   - Add **Redis** plugin → `REDIS_URL` is auto-set.

2. **Set the required env vars** (the 5 secrets + `ENVIRONMENT=production` + `CORS_ORIGINS`).
   Also set `ADMIN_EMAILS=your@email.com` so your first login auto-promotes you to admin.

3. **Run the pre-deploy check** (optional but recommended — catches issues before deploying):
   ```bash
   # On Replit shell, with RAILWAY env vars loaded:
   DATABASE_URL=<railway-db-url> ENVIRONMENT=production SESSION_SECRET=<your-secret> \
     BROKER_ENCRYPTION_KEY=<your-key> ENCRYPTION_MASTER_KEY=<your-key> \
     CORS_ORIGINS=https://targetcapital.ai \
     python scripts/pre_deploy_check.py
   ```
   All REQUIRED checks must pass before continuing.

4. **Export data from Replit** (if migrating existing users/data):
   ```bash
   python scripts/db_export.py
   # Creates database_export.sql in project root
   ```

5. **First deploy:** also set `RUN_MIGRATIONS=1` and `RUN_SEEDS=1`.

6. Trigger the deploy. Watch logs for:
   - `✅ Environment configuration validated successfully`
   - `Target Capital server ready to accept connections`
   - `singleton worker` next to each scheduler line

7. **Import data** (if migrating):
   ```bash
   # Option A — Railway CLI
   railway run python scripts/db_import.py

   # Option B — direct psql
   psql $RAILWAY_DATABASE_URL < database_export.sql
   ```

8. Once the deploy is green, **remove `RUN_MIGRATIONS` and `RUN_SEEDS`** from variables.

9. **Add your Railway domain to Google Console** — see Google OAuth section above.

10. Configure your custom domain and TLS in Railway → Settings.

11. Open `/admin/notifications` to verify all integrations are live.

---

## Subsequent deploys

After the first deploy you only push code. The boot path is:

1. `entrypoint.sh` syncs the NSE stock universe (idempotent, <1 s when complete).
2. Gunicorn loads `app.py` once with `--preload`.
3. Workers fork and start serving immediately.
4. Schedulers (F&O monitor + I-Score dispatcher) acquire a Postgres advisory lock so only **one** worker runs each scheduler — no duplicate alerts.

When you ship code that adds a new column or index to `_pending_migrations`, set `RUN_MIGRATIONS=1` for that one deploy.

**New tables that must exist on every boot** (e.g. `trader_profile`, `trader_answer`, `trader_progression` for the Trader Intelligence wizard) are placed in `app.py`'s `_always_create` list and in `railway_migrate.py::ensure_raw_tables`, not in `_pending_migrations`. They are created with `CREATE TABLE IF NOT EXISTS` on every startup, so a fresh Railway deploy does **not** need `RUN_MIGRATIONS=1` to avoid `relation "trader_profile" does not exist` errors.

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
