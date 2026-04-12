# Target Capital - Flask Web Application

### Overview
Target Capital is an AI-powered trading support platform for the Indian market. It aims to reduce losses for individual F&O traders by offering a Portfolio Hub for multi-broker dashboards and risk analytics, an AI Research Assistant for RAG-powered insights, and Trade Now with transparent, experience-gated signals and behavioural guardrails. The platform is branded as the **Scentric AI Decision Engine** and targets over 15 million Indian investors with a vision for multi-regional expansion.

### User Preferences
Preferred communication style: Simple, everyday language.
Brand name: Target Capital
Design preference: Clean white backgrounds instead of blue gradients
Navigation bar: Custom dark navy background color #00091a
Typography: Modern Poppins font throughout the website
Manual changes accepted for project integration and navigation structure
Do not make changes to the file `replit.nix`
Do not make changes to the file `pt-app/pt_app.py`
Do not make changes to the file `pt-app/templates/index.html`
Do not make changes to the folder `pt-app/static/`

### System Architecture
The platform features a multi-service backend with Flask for the web interface and FastAPI for high-performance trading operations. It uses WebSockets for real-time data, Celery with Redis for background tasks, and PostgreSQL with Redis caching for data storage. A multi-tenant architecture ensures tenant isolation via `tenant_id` and middleware for resolution. The frontend uses Jinja2, Bootstrap 5.3.0, Font Awesome 6.4.0, Google Fonts (Inter), and vanilla JavaScript, focusing on a mobile-first, responsive design with clean white backgrounds and modern typography.

**AI Architecture**:
Target Capital employs a dual AI engine approach:
-   **Anthropic Workflow Engine** (fully implemented): Uses `claude-sonnet-4-20250514` as primary model and `claude-3-5-haiku-20241022` as fallback. Implementation files:
    - `services/anthropic_service.py` — Claude API wrapper with retry logic, structured output, and tool-use support
    - `services/workflow_engine.py` — Base framework: `WorkflowNode`, `WorkflowPipeline`, `WorkflowState`, audit trails
    - `services/data_connectors.py` — Pluggable `B2CConnector`, `B2BConnector`, `DatabaseConnector` with `ConnectorRegistry`; shared `_fetch_market_quote()` helper deduplicates market data logic
    - `services/workflow_iscore.py` — 7-node I-Score pipeline (data collection → qualitative → quantitative → sentiment → trend → aggregation → storage)
    - `services/workflow_research.py` — 5-step research pipeline (query understanding → context → market analysis → response → trade suggestions) with `save_research_results()` for tenant-scoped persistence to `ResearchCache`
    - `services/workflow_portfolio.py` — 6-step portfolio analysis (fetch → risk → sector → allocation → opportunities → report)
    - `routes_workflow.py` — REST endpoints with input validation (symbol regex, query length cap), per-user rate limiting (10/hour), and tenant-scoped queries: `/api/workflow/iscore`, `/api/workflow/research`, `/api/workflow/portfolio`, `/api/workflow/executions`, `/api/workflow/connectors`
    - `templates/dashboard/workflows/workflow_hub.html` — Visual pipeline execution UI with step-by-step progress, results display, and execution history
    - Database models: `WorkflowExecution`, `WorkflowStep`, `DataConnectorConfig` in `models.py`
    - Blueprint `workflow_bp` registered in `main.py`
    - Sidebar nav: accessible via Research Co-Pilot (no separate menu item)
-   **LangGraph Engine**: The original OpenAI-based system, featuring a `LangGraph Research Assistant`, a `Multi-Agent Portfolio Optimizer`, a `Smart Trading Signal Pipeline`, and a `Trade Plus Pipeline`. Visualizations are provided through a `Visual Agent Workflow System`, with state persistence via PostgreSQL models.

**B2B/B2C Multi-Tenant Data Architecture**: Supports B2C user-connected brokers (Dhan, Zerodha, Angel) via `BrokerService` and B2B partner broker APIs with configurable `B2BConnector`. A database fallback reads from the local `Portfolio` model. Each B2B partner operates as a distinct tenant.

**Broker Integration Architecture** (`services/broker_service.py`):
- **8 fully implemented brokers**: Dhan (dhanhq SDK), Zerodha (kiteconnect SDK), Angel One (SmartAPI SDK), Upstox (REST API v2, no external SDK), ICICI Direct (breeze-connect SDK), Groww (Partner REST API, access token), Alice Blue (ANT API v2, SHA-256 checksum auth), 5 Paisa (OpenAPI REST, TOTP direct)
- **BaseBrokerClient** abstract interface — all brokers implement: `connect()`, `get_holdings()`, `get_positions()`, `get_orders()`, `get_trade_history()`, `place_order()`, `cancel_order()`, `get_profile()`
- **`get_trade_history()`**: Fetches executed trade book from broker (Dhan: `get_trade_book()`, Zerodha: `trades()`, Angel: `tradeBook()`). All normalized to unified schema: symbol, quantity, price, trade_date, trade_id, broker_name.
- **Broker Registry** (`_BROKER_REGISTRY` dict): Adding a new broker = implement `BaseBrokerClient` + add one line. Future: Fyers, HDFC Securities.
- **OAuth + Auth flows** (`routes_broker_oauth.py`): Zerodha (KiteConnect OAuth redirect → request_token exchange), Upstox (v2 OAuth redirect → authorization code exchange), ICICI Direct (Breeze Connect OAuth redirect → apisession exchange), Angel One (TOTP direct connect, no redirect). Blueprint `broker_oauth` registered in `main.py`.
- **Sensibull-style "Connect Broker" page** (`/dashboard/broker-connect`): Clean UI with primary Zerodha card + grid for Upstox/Angel One/ICICI Direct. Modals collect credentials, then redirect to broker OAuth. Sidebar nav updated with "Connect Broker" and "Manage Brokers" links under Portfolio Hub.
- **`BrokerService.sync_broker_data()`**: Syncs all 5 data types (holdings, positions, orders, trade_history, profile). Trade history stored in `BrokerOrder` table with idempotency key (trade_id/order_id) — safe to re-sync.
- **`BrokerService.place_order_via_broker()`**: Places order via broker SDK, records in DB. User monitors execution on broker's own platform.
- **Behavioural AI feed**: Completed trades from `BrokerOrder` feed the Behaviour Engine's pattern detection.

**Key Features & Design Patterns**:
-   **Agentic AI Tools**: Leverages OpenAI, Perplexity, and LangGraph for advanced analytics.
-   **Multi-Broker Integration**: Unified API support for 12 major Indian brokers.
-   **Authentication**: Google OAuth, Mobile OTP, and Email/Password.
-   **RAG-Powered Research Assistant**: Semantic search with pgvector, LLM responses with citations.
-   **Multi-Asset Portfolio System**: Supports 11 asset classes across multiple brokers with real-time data and asset-specific filtering.
-   **Portfolio Asset Vector Embeddings**: Automatic generation for semantic search and AI analysis.
-   **Unified Portfolio Analyzer System**: AI-powered analysis with multi-agent LangGraph optimization and risk profiling.
-   **Scentric Risk Engine** (`services/risk_engine.py`): Portfolio Pulse (health score, alerts), Risk Heat Map (per-asset-class risk/weight/PnL grid), Goal-Based Monitoring (progress bars vs financial goals). Displayed on the Portfolio page. `PortfolioEvent` model tracks all events. Includes 5-minute TTL cache with portfolio-fingerprinted keys and automatic expired-entry eviction.
-   **Behavioural Guardrails** (Trade Now page): Live client-side guardrail checks trigger when selecting assets — warns against risk profile violations (conservative user picking derivatives) and concentration risk (>20% portfolio in one trade).
-   **Behavioural AI Engine** (`services/behaviour_engine.py`): Deep trading psychology analysis — the platform's core USP. Detects 6 harmful patterns from `TradeHistory`: Revenge Trading (loss → bigger trade within 30 mins), Overtrading (5+ trades in 4hrs), Position Size Tilt (martingale after consecutive losses), Loss Aversion (holding losers longer than winners), Panic Selling (manual exit within 2hrs at a loss), Overconfidence Bias (size increase after winning streaks). Produces a **Trading Discipline Score (0–100)**, Trading Personality profile (Disciplined/Developing/Emotional/Fearful Gambler/Revenge/Impulse), win rate analysis by hour/day/symbol, avg win vs loss comparison, and a **pre-trade real-time check** API. Dashboard at `/dashboard/behavioural-insights` renders all insights server-side with Bootstrap. Pre-trade API at `/api/behaviour/pre-trade-check`. Routes in `routes_behaviour.py`. Model: `BehaviouralAlert` in `models.py`. Table auto-migrated via incremental migrations in `app.py`.
-   **Live Market Pulse** (`/dashboard/daily-signals`): Replaced "Daily Signals" with a comprehensive market intelligence page. Features: live Nifty 50 D3.js treemap heatmap (colour-coded by day-change %, sized by index weight), sector performance grid (weighted avg), Top Movers tabs (gainers/losers/most active), AI market commentary (Claude Haiku, AJAX-loaded), market indices strip (Nifty 50, Bank Nifty, Sensex, IT), **interactive Scentric AI query input** with voice support (Web Speech API). Users can type or speak any market question and receive AI-powered responses via `/api/market-pulse/query` (Claude Haiku with live market context). Suggestion chips provide quick query shortcuts. Data: `_get_live_nifty50_data()` fetches top 20 quotes from NSE with day-seeded deterministic fallback; `_get_sector_summary()` aggregates to 18 sectors. API endpoints: `/api/market-pulse/commentary` (auto-generated brief), `/api/market-pulse/query` (user queries). Signals table retained at bottom.
-   **Research Watch List in Co-Pilot**: `dashboard_ai_advisor` (`/dashboard/ai-advisor`) now renders all active `ResearchList` entries below the search box. Users can filter by asset type/recommendation, search by symbol or company name, and click "Research" to auto-submit a deep-dive query. After each research, `POST /api/research/sync-watchlist` upserts the symbol into the list (updates `last_requested_at` or creates a new entry for new symbols). Admin I-Score refresh route (`admin_routes.py`) fixed to use `LangGraphIScoreEngine.analyze()` with proper result mapping.
-   **Comprehensive Trading Signal System**: LangGraph-powered signal pipeline with validation and execution planning.
-   **Subscription Model**: Tiered access (FREE, TARGET PLUS, TARGET PRO, HNI).
-   **Knowledge Base**: Educational trading articles.

**Mobile App & PWA Support**:
-   **Mobile REST API (v1)**: Versioned API `/api/v1/mobile/` with JWT authentication.
-   **JWT Authentication**: Stateless token-based authentication.
-   **Mobile Endpoints**: Covers authentication, portfolio, trading signals, brokers, and market data.
-   **Progressive Web App (PWA)**: Full PWA support including service worker, offline caching, and push notifications.
-   **Mobile-First Design**: Responsive UI with touch optimization.

**Enterprise Multi-Tenant Security Architecture**: Implements defense-in-depth through:
1.  **SQLAlchemy ORM Automatic Filtering**: Auto-injects tenant filters and validates `tenant_id` for data isolation.
2.  **PostgreSQL Row-Level Security (RLS)**: Dynamic RLS policy creation using `current_setting('app.tenant_id')`.
3.  **Per-Tenant Encryption Service**: Hierarchical key management and Fernet-based field-level encryption for sensitive data.

**Application Security Controls**:
-   **User Data Isolation**: Queries filtered by `user_id` and `tenant_id`, URL manipulation protection via `verify_resource_ownership()`.
-   **API Keys & Secrets Protection**: Environment variables for keys, encrypted broker credentials, Replit/Railway secrets management, rate limiting.
-   **Privilege Escalation Prevention**: `is_admin` and `pricing_plan` fields cannot be user-modified, `@admin_required` decorator, subscription changes via verified webhooks.

**I-Score Engine Implementation**: A fully functional 7-node LangGraph workflow using GPT-4 Turbo, Perplexity Sonar Pro, and NSE Service for weighted scoring based on Qualitative, Quantitative/Technical, Search/Sentiment, and Trend Analysis. Includes cache checks, real-time market data, detailed reasoning, and result storage.

### External Dependencies
-   **Python Packages**: Flask, Flask-SQLAlchemy, Werkzeug, NSEPython, Pandas, Requests, LangGraph, LangChain, LangChain-OpenAI, LangChain-Community, cryptography.
-   **Frontend Libraries**: Bootstrap 5.3.0, Font Awesome 6.4.0, Google Fonts (Inter).
-   **Infrastructure Dependencies**: PostgreSQL (with pgvector extension), Redis.
-   **AI/ML Stack**: OpenAI API (GPT-4-turbo), Perplexity API (Sonar Pro).
-   **Third-Party Services**: n8n, Twilio, WhatsApp Business API, Telegram Bot API, Razorpay API, TradingView (custom implementation).
-   **Mutual Fund Data Sources**: MFapi.in, mftool Python Library, AMFI Data for NAV and scheme information.