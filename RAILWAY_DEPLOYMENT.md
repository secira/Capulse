# Railway Deployment Guide for Target Capital

This guide explains how to deploy Target Capital to Railway.

## Prerequisites

1. A Railway account at https://railway.app
2. A PostgreSQL database (can be provisioned on Railway)
3. Required environment variables

## Quick Start

1. **Connect your repository to Railway**
   - Go to Railway Dashboard
   - Click "New Project" > "Deploy from GitHub repo"
   - Select your repository

2. **Add a PostgreSQL database**
   - In your Railway project, click "New" > "Database" > "PostgreSQL"
   - Railway will automatically set the `DATABASE_URL` environment variable

3. **Set Required Environment Variables**
   
   Go to your service's "Variables" tab and add:

   ```
   # Required
   SESSION_SECRET=your-secure-secret-key-min-32-chars
   ENVIRONMENT=production
   
   # AI Features (optional - app works without these)
   OPENAI_API_KEY=your-openai-api-key
   PERPLEXITY_API_KEY=your-perplexity-api-key
   
   # Payment (optional)
   RAZORPAY_KEY_ID=your-razorpay-key
   RAZORPAY_KEY_SECRET=your-razorpay-secret
   
   # Notifications (optional)
   TWILIO_ACCOUNT_SID=your-twilio-sid
   TWILIO_AUTH_TOKEN=your-twilio-token
   TWILIO_PHONE_NUMBER=your-twilio-number
   TELEGRAM_BOT_TOKEN=your-telegram-bot-token
   TELEGRAM_CHAT_ID=your-telegram-chat-id
   
   # Google OAuth (optional)
   GOOGLE_OAUTH_CLIENT_ID=your-google-client-id
   GOOGLE_OAUTH_CLIENT_SECRET=your-google-client-secret
   
   # Redis for caching (optional but recommended)
   REDIS_URL=redis://your-redis-url
   ```

4. **Deploy**
   - Railway will automatically detect the Procfile and deploy
   - The migration script runs automatically on first deployment

## Environment Variables Reference

### Required Variables

| Variable | Description |
|----------|-------------|
| `SESSION_SECRET` | Secret key for session management (min 32 chars) |
| `DATABASE_URL` | PostgreSQL connection string (auto-set by Railway) |
| `ENVIRONMENT` | Set to `production` for Railway deployment |

### Optional AI Features

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key for AI analysis features |
| `PERPLEXITY_API_KEY` | Perplexity API key for real-time market research |

### Optional Payment & Notifications

| Variable | Description |
|----------|-------------|
| `RAZORPAY_KEY_ID` | Razorpay key for payments |
| `RAZORPAY_KEY_SECRET` | Razorpay secret for payments |
| `TWILIO_ACCOUNT_SID` | Twilio SID for SMS notifications |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Twilio phone number |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for alerts |

## Health Checks

Railway uses the following health check endpoints:

- `/health` - Basic health check (returns 200 if app is running)
- `/health/ready` - Readiness check (verifies database connectivity)
- `/health/live` - Liveness check for container orchestrators

## Database Setup & Data Migration

### Step 1: Export Data from Replit (Before Deploying)

Run the export script on Replit to create a backup of your critical data:

```bash
python scripts/db_export.py
```

This creates `database_export.sql` containing:
- Users and authentication data
- Broker configurations
- Portfolios and manual holdings
- Subscriptions and payments
- Knowledge base articles
- AI stock picks

### Step 2: Deploy to Railway

1. **Add PostgreSQL Database**
   - In Railway project, click "New" > "Database" > "PostgreSQL"
   - Railway automatically sets `DATABASE_URL`

2. **Deploy Application**
   - Railway auto-detects Procfile and deploys
   - `railway_migrate.py` runs automatically to create tables

### Step 3: Import Data to Railway

After the first deployment creates the tables, import your data:

**Option A: Using Railway CLI**
```bash
# Install Railway CLI if not already installed
npm install -g @railway/cli

# Login to Railway
railway login

# Connect to your project
railway link

# Run the import
railway run python scripts/db_import.py
```

**Option B: Direct psql import**
```bash
# Get DATABASE_URL from Railway dashboard
psql $RAILWAY_DATABASE_URL < database_export.sql
```

**Option C: Using Railway Shell**
1. Go to your service in Railway dashboard
2. Click "Shell" tab
3. Run: `python scripts/db_import.py`

### What Gets Exported

| Table | Description |
|-------|-------------|
| `tenants` | Multi-tenant configuration |
| `user` | User accounts and profiles |
| `admins` | Admin accounts |
| `user_brokers` | Broker connections |
| `portfolio` | Portfolio holdings |
| `subscriptions` | Active subscriptions |
| `payments` | Payment history |
| `blog_posts` | Knowledge base articles |
| `ai_stock_picks` | AI recommendations |
| `watchlist_item` | User watchlists |
| `user_risk_profile` | Risk assessments |
| `manual_*_holdings` | All manual asset holdings |

### What Gets Skipped

Temporary/regeneratable data is excluded:
- Research cache (regenerated on demand)
- Vector embeddings (regenerated automatically)
- Chat history (optional, can be included if needed)
- Sync logs (operational data)

### Important Warnings

1. **Stop the Replit app before exporting** to ensure data consistency
2. **Backup Railway database first** if importing to an existing database
3. **ON CONFLICT DO NOTHING** - existing records with matching IDs will NOT be overwritten
4. **Data merge behavior** - the import adds new records but won't update existing ones
5. **Run migrations first** - tables must exist before importing data

## Automatic Database Migrations

The `railway_migrate.py` script runs automatically before the app starts:

1. Connects to the PostgreSQL database
2. Runs Alembic migrations if available
3. Falls back to direct table creation if migrations fail
4. Initializes the default tenant ('live')

## Troubleshooting

### App fails to start

1. Check that `DATABASE_URL` is set correctly
2. Verify `SESSION_SECRET` is at least 32 characters
3. Check Railway logs for specific error messages

### Database connection issues

1. Ensure PostgreSQL service is running on Railway
2. Check that the database URL uses `postgresql://` or `postgres://` prefix
3. The migration script automatically handles URL format conversion

### AI features not working

1. Verify `OPENAI_API_KEY` and `PERPLEXITY_API_KEY` are set
2. AI features degrade gracefully - app works without these keys
3. Check logs for API-specific error messages

## Schema Changes — Important

This deploy ships **3 new tables** for the B2B Partner API (`api_partner`,
`api_subscription`, `api_alert_log`) plus a new Python dependency (`logzero`,
required by Angel One's SmartApi SDK).

To apply the new tables on Railway:

1. In your Railway service Variables, set `RUN_MIGRATIONS=1`.
2. Trigger a redeploy (push to your branch or click "Redeploy").
3. Watch the logs — you should see `[migration N/39] running…` lines and
   `✅ Incremental column migrations applied`.
4. **Unset `RUN_MIGRATIONS`** after the deploy succeeds so future redeploys
   stay fast (the migration block is gated to avoid `--preload` lock waits).

`requirements.txt` already pins `logzero` (line 66), so the dependency is
installed automatically on the next image build — no extra action needed.

## Architecture Notes

### Lazy Loading

All AI/LLM services use lazy loading:
- API clients are initialized on first use, not at startup
- This prevents deployment failures when API keys are not yet configured
- Services gracefully degrade when keys are missing

### Port Configuration

Railway automatically sets the `PORT` environment variable. The app binds to `0.0.0.0:$PORT`.

### Worker Configuration

The Procfile configures:
- 2 workers with 4 threads each (gthread worker class)
- 120 second timeout for long-running requests
- Request recycling to prevent memory leaks

## Files Created for Railway

- `Procfile` - Railway process definition
- `railway.json` - Railway configuration
- `railway_migrate.py` - Database migration script
- `requirements.txt` - Python dependencies
- `runtime.txt` - Python version specification
- `nixpacks.toml` - Nixpacks build configuration
