# Capulse — AI Stock Research Chat for Indian Markets

### Overview
Capulse is a chat-first AI research platform for Indian retail traders and investors. Users interact via a ChatGPT-style interface to get i-Scores for any listed stock, F&O signals for NIFTY/BANKNIFTY, portfolio analysis, and behavioural coaching — all under SEBI Research Analyst oversight. No broker connection, no order execution, no trading UI on the user-facing side.

The codebase is a fork of **Capulse** (Capulse.in) — the TC broker/trade execution code is preserved in place but hidden from the Capulse UI. The admin panel at `/admin/*` remains unchanged (TC branding + full feature set).

### User Preferences
- Do not make changes to the file `replit.nix`
- Do not make changes to the file `pt-app/pt_app.py`
- Do not make changes to the file `pt-app/templates/index.html`
- Do not make changes to the folder `pt-app/static/`
- Admin panel stays as-is (Capulse branding, full feature set)
- Preferred communication style: Simple, everyday language

### Capulse Design System
- **Background:** `#0B0D10` (deep ink)
- **Accent:** `#E9A23B` (amber)
- **Typography:** Fraunces (serif headings) + IBM Plex Sans (body) + IBM Plex Mono (labels/code)
- **Shell:** 264px fixed sidebar + main content area (full-height, no page scroll)
- **Base template:** `templates/capulse_base.html` — all Capulse pages extend this

### Capulse Pages
| URL | Template | Auth |
|-----|----------|------|
| `/` | → redirects to `/chat` (auth) or `/login` (unauth) | — |
| `/chat` | `templates/chat.html` | Required |
| `/about` | `templates/capulse_about.html` | Public |
| `/pricing` | `templates/capulse_pricing.html` | Public |
| `/compliance` | `templates/capulse_compliance.html` | Public |
| `/how-it-works` | `templates/capulse_how_it_works.html` | Public |
| `/login` | `templates/auth/login.html` | Public |
| `/register` | `templates/auth/register.html` | Public |
| `/admin/*` | unchanged (TC admin) | Admin |

### Backend Architecture
- **Framework:** Flask + Gunicorn (8 workers, gthread)
- **Database:** Replit PostgreSQL (`DATABASE_URL` runtime-managed)
- **Chat models:** `ChatConversation` + `ChatMessage` in `models.py`
- **Chat blueprint:** `routes_chat.py` → registered in `main.py`
- **Intent router:** `services/capulse_router.py` — Claude haiku classifies to ISCORE | FNO_SIGNAL | MUTUAL_FUND | PORTFOLIO | BEHAVIOUR | GENERAL
- **AI:** Anthropic Claude (`ANTHROPIC_API_KEY`) — primary across all AI service files
- **Daily limits:** Free = 20 questions/day, Plus = 200 questions/day

### Required Environment Variables
| Key | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | Claude (chat router + all AI engines) |
| `SESSION_SECRET` | Flask session signing (set) |
| `DATABASE_URL` | PostgreSQL (auto-managed by Replit) |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Payments (optional) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` | OTP (optional) |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google login (optional) |

### System Architecture Notes
- Multi-tenant via `tenant_id` middleware (default tenant: `live`)
- APScheduler runs alert jobs, nightly i-Score scans, and broker health checks
- Broker/trade code (Dhan, Zerodha, Angel etc.) preserved in place — not exposed in Capulse UI
- fno_config table bootstrapped by inline SQL in app.py (not by services/fno_config.py)
- Market data gateway: Admin Pool → TrueData → System Dhan → NSEPython → yfinance
- ThreadPoolExecutor: never use `with` context manager for broker SDK calls (blocks forever on hung threads)

### Run Command
```
gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app
```
