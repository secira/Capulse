# Capulse — AI Stock Research Chat for Indian Markets

### Overview
Capulse is a chat-first AI research platform for Indian retail traders and investors. Users interact via a ChatGPT-style interface to get i-Scores for any listed stock, F&O signals for NIFTY/BANKNIFTY, mutual fund NAVs and returns, portfolio analysis (manual holdings), and behavioural coaching — all under SEBI Research Analyst oversight. No broker connection, no order execution, no trading UI on the user-facing side.

The codebase is a fork of **Target Capital** — the TC broker/trade execution code is preserved in `brokers/`, `models_broker.py`, `routes_partner.py`, etc. but hidden from the Capulse UI. Admin panel at `/admin/*` is kept fully functional.

### Architecture
```
User types question in chat
  → POST /chat/message
  → CapulseRouter (services/capulse_router.py) — Claude intent classifier
      ├── ISCORE      → IScoreService
      ├── FNO_SIGNAL  → NiftyOptionsEngine
      ├── MUTUAL_FUND → MFApiService
      ├── PORTFOLIO   → PortfolioAnalyzerService (ManualEquityHolding)
      ├── BEHAVIOUR   → BehaviourEngine
      └── GENERAL     → Claude prose answer
  → Structured JSON → Chat card rendered in bubble
```

### User Preferences
- Do not make changes to the file `replit.nix`
- Do not make changes to the file `pt-app/pt_app.py`
- Do not make changes to the file `pt-app/templates/index.html`
- Do not make changes to the folder `pt-app/static/`
- Admin panel stays as-is (full feature set, `/admin/*`)
- User dashboard (`/dashboard/*`) is inactive — all routes redirect to `/chat`
- Preferred communication style: Simple, everyday language

### Capulse Design System
- **Background:** `#0B0D10` (deep ink)
- **Accent:** `#E9A23B` (amber)
- **Typography:** Fraunces (serif headings) + IBM Plex Sans (body) + IBM Plex Mono (labels/code)
- **Shell:** 264px fixed sidebar + main content area (full-height, no page scroll)
- **Base template:** `templates/capulse_base.html` — all Capulse pages extend this

### Capulse Pages (active)
| URL | Template | Auth |
|-----|----------|------|
| `/` | → redirects to `/chat` (auth) or `/login` (unauth) | — |
| `/chat` | `templates/chat.html` | Public (send requires login) |
| `/chat?q=...` | Pre-fills + auto-submits a query | Public |
| `/login` | `templates/auth/login.html` | Public |
| `/mobile-login` | `templates/auth/mobile_login.html` | Public |
| `/register` | `templates/auth/register.html` | Public |
| `/profile` | `templates/auth/profile.html` | Login |
| `/pricing` | `templates/capulse_pricing.html` | Public |
| `/about` | `templates/capulse_about.html` | Public |
| `/compliance` | `templates/capulse_compliance.html` | Public |
| `/how-it-works` | `templates/capulse_how_it_works.html` | Public |
| `/admin/*` | Admin panel templates | Admin |

### Sidebar Navigation (capulse_base.html)
**Research section** (quick-action links — open chat with pre-filled query):
- Chat → `/chat`
- F&O Signals → `/chat?q=What are today's NIFTY F&O signals?` (auto-submits)
- I-Score Lookup → `/chat?q=I-Score for ` (pre-fills, user types stock name)
- Mutual Funds → `/chat?q=Analyse this mutual fund: ` (pre-fills)
- Portfolio Review → `/chat?q=Analyse my portfolio...` (auto-submits)
- Behavioural Coach → `/chat?q=How is my trading behaviour...` (auto-submits)

**Pages section:** Pricing, How it works, Compliance

### Plans
| Plan | Daily questions | Price |
|------|----------------|-------|
| Free | 5 | ₹0 |
| Plus | 200 | ₹999/mo |

### Key Services
| File | Purpose |
|------|---------|
| `services/capulse_router.py` | Claude intent classifier + engine dispatcher |
| `services/iscore/` | i-Score composite scoring engine |
| `services/nifty_options_engine.py` | F&O signal generation |
| `services/mfapi_service.py` | Mutual fund NAV + returns (MFApi.in) |
| `services/portfolio_analyzer_service.py` | Portfolio analysis (ManualEquityHolding) |
| `services/behaviour_engine.py` | Behavioural coaching + discipline score |
| `services/market_data_gateway.py` | Unified market data fallback chain |
| `services/market_calendar.py` | NSE holiday + trading day helpers |

### Preserved (not used in Capulse UI, kept for future)
- `brokers/` — all broker adapters (Dhan, Zerodha, Angel, Upstox, etc.)
- `models_broker.py` — BrokerAccount, BrokerOrder, BrokerHolding
- `routes_partner.py`, `routes_partner_api.py` — B2B partner API
- `routes_websocket.py` — live order streaming
- `templates/dashboard/` — full TC dashboard (inactive, redirects to /chat)

### Outstanding Tasks (proposed)
- Task #3: Keep-alive / prevent Replit sleep
- Task #8: Harden admin password (remove env-var fallback credentials)
- Task #9: Google OAuth — set User Type to External + publish in Google Cloud Console
- Task #10: Residual "Target Capital" strings in OTP screens
- Task #11: Connect public Redis (Upstash) for rate limits before go-live
