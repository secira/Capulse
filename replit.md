# Target Capital - Flask Web Application

### Overview
Target Capital is an AI-powered trading support platform for the Indian market, aiming to reduce losses for F&O traders. It offers a Portfolio Hub with multi-broker dashboards and risk analytics, an AI Research Assistant for RAG-powered insights, and a Trade Now feature with transparent signals and behavioral guardrails. Branded as the **Scentric AI Decision Engine**, it targets over 15 million Indian investors with aspirations for multi-regional expansion.

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
The platform utilizes a multi-service backend with Flask for the web interface and FastAPI for high-performance trading operations, using WebSockets for real-time data. Celery with Redis handles background tasks, and PostgreSQL with Redis caching manages data storage. A multi-tenant architecture ensures tenant isolation via `tenant_id` and middleware. The frontend uses Jinja2, Bootstrap 5.3.0, Font Awesome 6.4.0, Google Fonts, and vanilla JavaScript, focusing on mobile-first, responsive design with clean aesthetics.

**AI Architecture**:
Target Capital employs a dual AI engine approach:
-   **Anthropic Workflow Engine**: Uses `claude-sonnet-4-20250514` as primary and `claude-3-5-haiku-20241022` as fallback, implementing workflows for I-Score, Research, and Portfolio analysis with robust API wrappers, data connectors, and persistence.
-   **LangGraph Engine**: An OpenAI-based system for a Research Assistant, Multi-Agent Portfolio Optimizer, Smart Trading Signal Pipeline, and Trade Plus Pipeline, with visual workflow management and PostgreSQL persistence.

**B2B/B2C Multi-Tenant Data Architecture**: Supports B2C user-connected brokers (Dhan, Zerodha, Angel) via `BrokerService` and B2B partner broker APIs with `B2BConnector`. A database fallback reads from the local `Portfolio` model, with each B2B partner as a distinct tenant.

**Broker Integration Architecture (Multi-Broker Intelligence Model)**:
-   **No "active/primary broker" for trading**: All connected brokers sync independently. User selects broker per-trade via dropdown.
-   **8 fully implemented brokers**: Dhan, Zerodha, Angel One, Upstox, Fyers, Shoonya (Finvasia), Alice Blue, 5 Paisa, all integrating via SDKs or REST APIs.
-   **Broker Data API Layer** (`brokers/` package): Separate adapter layer for market data (option chain, prices). Each adapter implements `BrokerBase` (get_price, get_option_chain, get_quotes). Factory in `services/broker_factory.py` with `get_data_broker_for_user()`.
-   **Data API Broker (separate from Trading Brokers)**: Dedicated `DataApiBroker` model (`data_api_broker` table) stores credentials for one broker used exclusively for market data. User configures via Dashboard → Data API Broker page. Completely separate from trading broker connections.
-   **Admin Data API Plan** (`data_api_plan` table): Two modes controlled from Admin → Data API Plan: (1) **User Data API** (default) — each user connects their own broker Data API; (2) **NSE + TrueData** — admin configures TrueData API key, all users get data from TrueData.
-   **Data Fallback Chain**: F&O engine checks admin plan first. If `nse_truedata`: TrueData API → NSE → estimated. If `user_data`: User's Data API broker → NSE → estimated. Data source shown in UI banner (green for live broker/TrueData, yellow for estimated).
-   **`BaseBrokerClient`**: An abstract interface ensuring consistent functionality across all brokers for operations like `connect()`, `get_holdings()`, and `place_order()`.
-   **OAuth + Auth flows**: Implemented for various brokers, handling connection and authentication securely.
-   **Broker Data Sync**: `BrokerService.sync_broker_data()` synchronizes holdings, positions, orders, and trade history per broker. Each `BrokerAccount` tracks `sync_status` (success/failed/pending/syncing) and `last_sync` timestamp. "Sync All" button triggers all connected brokers.
-   **Trade Router**: `api_trade_execute_signal` accepts `broker_id` from the UI dropdown and routes the order to the selected broker's API. Fallback: if no broker selected, uses first connected broker.
-   **Portfolio & Behaviour Engines**: Aggregate data across ALL connected brokers for unified portfolio view and cross-broker behavioural intelligence.

**Data Quality & Safety Layer** (`services/broker_data_quality.py`):
-   **Data Freshness Scoring**: Per-broker freshness (high/medium/low/stale/never) based on `last_sync` age thresholds (5m/30m/2h). Dashboard shows freshness warning banner when any broker is outdated.
-   **Quality Score (0–100)**: Checks sync recency, failure status, duplicate trades, and missing exit data. Grades: Excellent/Good/Fair/Poor.
-   **Event-Driven Sync**: Login auto-syncs stale brokers (>30 min old). Syncs holdings + positions for up to 5 stale accounts.
-   **Pre-Trade Validation**: 5-point check (broker connected, sync health, symbol valid, quantity valid, stop-loss logic, margin check) runs before every trade execution. Blocks trade if any critical check fails.
-   **API**: `GET /api/data-quality` returns freshness + quality data for client-side consumption.

**Key Features & Design Patterns**:
-   **Agentic AI Tools**: Leverages OpenAI, Perplexity, and LangGraph.
-   **Multi-Broker Integration**: Unified API support for 12 major Indian brokers.
-   **Authentication**: Google OAuth, Mobile OTP, and Email/Password.
-   **RAG-Powered Research Assistant**: Semantic search with pgvector and LLM responses.
-   **Multi-Asset Portfolio System**: Supports 11 asset classes across brokers with real-time data and AI analysis.
-   **Scentric Risk Engine**: Provides Portfolio Pulse, Risk Heat Map, and Goal-Based Monitoring.
-   **Behavioural Guardrails**: Client-side checks on the Trade Now page to warn against risk profile violations and concentration risk.
-   **Behavioural AI Engine**: Analyzes trading psychology, detecting harmful patterns (e.g., Revenge Trading, Overtrading) to produce a **Trading Discipline Score**, personality profiles, and pre-trade checks. Includes new intelligence modules for timeline analysis, cross-broker insights, and real-time alerts.
-   **Live Market Pulse**: A comprehensive market intelligence page featuring live Nifty 50 treemap, sector performance, Top Movers, AI market commentary, and interactive Scentric AI query input with voice support.
-   **Research Watch List in Co-Pilot**: Displays active `ResearchList` entries for easy filtering, search, and deep-dive queries.
-   **F&O Analysis Engine (NIFTY Options)**: MVLA (Momentum-Validated, Loss-Averse) decision engine with 3-layer architecture: Time Filter, Direction Engine (VWAP + Supertrend + DMI), Strength & Momentum (ADX + ATR + OI). Generates 3-6 trade recommendations with confidence scoring (0-100), strike selection (ATM/ITM/OTM), and risk management rules. Connected to Trade Now for execution. Supports NIFTY 50 (active), Bank Nifty, FIN NIFTY, SENSEX (coming soon). Routes: `routes_fno.py` blueprint, Engine: `services/nifty_options_engine.py`.
-   **Admin Data Input Source**: Switchable market data sources (NSE Python default, TrueData API, User Custom). Stored in `data_source_config` table, manageable from Admin Panel → Data Input Source.
-   **F&O Continuous Monitor**: APScheduler-based background scanner (60s interval) in `services/fno_monitor.py`. Runs during market hours (9:15 AM–3:30 PM IST), saves signals to `fno_signal_history` table. Smart alert system: Telegram alerts only when confidence > 75, new direction (not duplicate), 10-min cooldown, max 3 alerts/day. Signal history viewable on the F&O page.
-   **Comprehensive Trading Signal System**: LangGraph-powered signal pipeline.
-   **Subscription Model**: Tiered access (FREE, TARGET PLUS, TARGET PRO, HNI).
-   **Knowledge Base**: Educational trading articles.

**Mobile App & PWA Support**:
-   **Mobile REST API (v1)**: Versioned API with JWT authentication.
-   **Progressive Web App (PWA)**: Full PWA support with service worker, offline caching, and push notifications.
-   **Mobile-First Design**: Responsive UI with touch optimization.

**Enterprise Multi-Tenant Security Architecture**:
-   **SQLAlchemy ORM Automatic Filtering**: Auto-injects tenant filters for data isolation.
-   **PostgreSQL Row-Level Security (RLS)**: Dynamic RLS policies for enhanced security.
-   **Per-Tenant Encryption Service**: Hierarchical key management and Fernet-based field-level encryption.

**Application Security Controls**:
-   **User Data Isolation**: Queries filtered by `user_id` and `tenant_id`, with resource ownership verification.
-   **API Keys & Secrets Protection**: Environment variables, encrypted broker credentials, and rate limiting.
-   **Privilege Escalation Prevention**: `is_admin` and `pricing_plan` fields are protected, with `@admin_required` decorator.

**I-Score Engine Implementation (v2)**:
-   **6-component model for stocks**: Quantitative, Trend, Risk, Qualitative, Search, Market Context, with hardcoded weights.
-   **Real technical indicators**: Incorporates Wilder RSI, EMA, ATR, SuperTrend, momentum, max drawdown, beta, and volume profiling from yfinance.
-   **Stocks pipeline**: A multi-step process from cache check to result storage.
-   **Nonlinear penalty system**: Adjusts scores based on volatility, trends, and drawdowns.
-   **Confidence scoring**: Rates score reliability based on component variance and data quality.
-   **Explainability factors**: Provides "Why this score?" explanations in the UI.
-   **Thresholds**: Defines Strong Buy, Buy, Hold, and Sell ranges.
-   **UI**: Visual display of component scores, confidence, penalties, and factors.

### External Dependencies
-   **Python Packages**: Flask, Flask-SQLAlchemy, Werkzeug, NSEPython, Pandas, Requests, LangGraph, LangChain, LangChain-OpenAI, LangChain-Community, cryptography.
-   **Frontend Libraries**: Bootstrap 5.3.0, Font Awesome 6.4.0, Google Fonts (Inter).
-   **Infrastructure Dependencies**: PostgreSQL (with pgvector extension), Redis.
-   **AI/ML Stack**: OpenAI API (GPT-4-turbo), Perplexity API (Sonar Pro).
-   **Third-Party Services**: n8n, Twilio, WhatsApp Business API, Telegram Bot API, Razorpay API, TradingView.
-   **Mutual Fund Data Sources**: MFapi.in, mftool Python Library, AMFI Data.