---
name: Market Data Gateway
description: Uniform market data fallback chain and AI routing — single entry point at services/market_data_gateway.py.
---

# Market Data Gateway

## Rule
All four areas (Market Intelligence, Stock Research, F&O Analysis, Trade Execution) must fetch market data through `services/market_data_gateway.py`. Never add a new direct Dhan/NSE/yfinance call in a route or service — go through the gateway.

## Canonical fallback chain (same for all functions)
1. **Admin Broker Pool** — `get_admin_data_brokers()` in `broker_factory.py`; runway-aware selection from `AdminDataBroker` model
2. **TrueData** — only when `data_api_plan.plan_type == 'nse_truedata'` and credentials set; supports LTP + option chain but NOT batch equity quotes or OHLCV history
3. **System Dhan** — any connected `DataApiBroker` (via `dhan_service`); supports LTP, batch quotes, OHLCV, and option chain
4. **NSEPython** — `nse_quote()` direct NSE scrape (LTP and option chain OI only, not batch or OHLCV)
5. **yfinance** — universal last resort for LTP, batch quotes, and OHLCV

## Gateway public API
- `get_price(symbol, user_id)` → `{value, source, success}`
- `get_ohlcv(symbol, days, user_id)` → `{df, source, success}` — TrueData skipped (no OHLCV endpoint)
- `get_quotes(symbols, user_id)` → `{quotes: {sym: {price, change_percent, source}}, source, success}` — TrueData skipped (no batch endpoint)
- `get_option_chain(symbol, expiry, user_id)` → `{option_chain, spot_price, expiry, source, success}`
- `get_index_prices(symbols, user_id)` → `{NIFTY: {ltp, change, pct_change, source}, _source, _success}`
- `source_badge(source)` → `{label, css_class, color}` — for UI badges
- `invalidate_cache(symbol=None)` — evict TTL cache entries

## Caches
- Prices: 60s TTL (`_PRICE_CACHE`)
- OHLCV: 300s TTL (`_OHLCV_CACHE`)

## AI routing (separate module: services/ai_router.py)
- **Perplexity** → real-time commentary, market movers, stock sentiment search
- **OpenAI** → RAG embeddings, Research Assistant, I-Score research phase
- **Claude** → portfolio analysis, workflow hub, risk narration, trade plans

## Where each area is wired to the gateway
- `services/nse_realtime_service.py` — `get_nse_indices()` and `get_stock_data()` call gateway as P0
- `services/nifty_options_engine.py` — `get_market_indices()` and `_get_option_chain_data()` call gateway as P0
- `routes_daily_signals.py` — `_fetch_nse_nifty50_stocks()` calls `gateway.get_quotes()` as P0
- `services/iscore/data_fetcher.py` — `fetch_historical_ohlcv_with_source()` calls `gateway.get_ohlcv()` as P0

**Why:** Before this change, each area had its own bespoke Dhan → yfinance chain, bypassing the admin broker pool entirely. The gateway ensures the admin's configured broker always gets priority, and TrueData is properly respected when configured.
