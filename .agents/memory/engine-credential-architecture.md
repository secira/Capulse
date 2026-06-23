---
name: TC Execution Engine credential architecture
description: How the execution engine at 54.225.202.78:8080 gets broker credentials, and why it can return DH-901 even when TC's own broker connection is live.
---

## The engine has its own credential store

The TC Execution Engine (`http://54.225.202.78:8080`) maintains its **own** copy of broker credentials — separate from TC's encrypted `user_brokers` table. It is NOT a pure stateless proxy (despite the old docstring in `execution_proxy.py` claiming it is).

**PlaceOrderRequest schema**: only sends `user_id` + `user_broker_id` (no raw credentials). Engine looks these up from its own DB by `user_broker_id`.

**The mismatch problem**: When a user reconnects a broker in TC (getting a fresh token), TC updates its own `user_brokers` table. But the engine's credential store is NOT automatically updated. The engine continues to use its stale token → Dhan returns DH-901.

## How to detect the mismatch

- TC broker health check → LIVE ✓ (TC's own live credentials)
- Engine order call → DH-901 (engine's stale credentials)
- The `connection_status` on the `BrokerAccount` row stays `connected`

## Fallback solution (implemented)

In `api_trade_execute_signal` and `api_trade_execute_confirmed` in `routes.py`:

When the engine returns `expired_token` / `invalid_credentials` / `auth_error` bucket AND TC's own `BrokerAccount.connection_status == 'connected'`, TC falls through to the **in-process Dhan path** instead of returning an error. The in-process path decrypts TC's live token and calls Dhan directly.

**Why:** If the broker is connected in TC but the engine rejects, it's an engine credential staleness issue, not an actual expired token. The in-process path always has the live credentials.

## Long-term fix needed (engine-side)

The engine admin token (`X-TC-Admin-Token`) is NOT the same as `EXECUTION_HMAC_SECRET`. The admin endpoint `GET /admin/api/broker-accounts` requires this token. To update engine credentials, TC needs:
1. The engine's admin token, OR
2. A credential sync endpoint added to the engine API

Until then, the fallback keeps orders working.

## Engine API endpoints

- `GET /healthz`, `GET /version`
- `POST /v1/orders` — PlaceOrderRequest (user_id, user_broker_id, symbol, exchange, security_id, transaction_type, quantity, order_type, product_type)
- `POST /v1/orders/{id}/cancel`
- `GET /v1/orders/{id}`
- `GET|PUT /v1/halt`
- `GET /admin/api/status`, `GET /admin/api/broker-accounts`, `GET /admin/api/trades`, `POST /admin/api/halt`, `POST /admin/api/test-order` — all require `X-TC-Admin-Token` header
