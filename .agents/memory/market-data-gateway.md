---
name: Market Data Gateway
description: Uniform market data fallback chain — one entry point, 5 canonical source labels, consistent across all platform areas.
---

# Market Data Gateway

## Rule
All four areas (Market Intelligence, Stock Research, F&O Analysis, Trade Execution) must fetch market data through `services/market_data_gateway.py`. Never add a new direct Dhan/NSE/yfinance call in a route or service — go through the gateway.

## Canonical fallback chain
1. **Admin Broker Pool** — runway-aware selection from admin-configured brokers
2. **TrueData** — only when `plan_type='nse_truedata'`; supports LTP + option chain only (no batch equity or OHLCV)
3. **System Dhan** — any connected DataApiBroker; all data types
4. **NSEPython** — LTP and option chain OI only; impractical for batch
5. **yfinance** — universal last resort for all data types

## Canonical source labels (exactly 5)
`admin_broker` | `truedata` | `nse` | `yfinance` | `estimated`

**Why:** All broker-derived data (admin pool, system Dhan, user broker) emits `SRC_ADMIN`. TrueData is its own label. Finer detail goes in `source_detail` field, not `source`.

**How to apply:** Every `return` in the gateway must use one of the 5 SRC_* constants. Never return `"dhan_system"`, `"user_broker"`, or `"yfinance:prev_close"` as the `source` value — those are legacy aliases kept only in `source_badge()` for backward compat.

## Dominant-source bug prevention
When computing `_source` in `get_index_prices()`, iterate only dict values and check for `SRC_ADMIN` first (covers all broker sub-sources now that they're normalized).

## Trade Execution integration
`broker_data_quality.pre_trade_validation()` calls `gateway.get_price()` for a "Live Price Check" that warns when a user's limit price deviates >10% from the market price. Non-critical — never blocks the trade.

## AI routing (separate module: services/ai_router.py)
- **Perplexity** → real-time commentary, market movers, stock sentiment
- **OpenAI** → RAG embeddings, Research Assistant
- **Claude** → portfolio analysis, workflow hub, deep reports
