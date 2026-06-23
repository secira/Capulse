---
name: TC Execution Engine credential architecture
description: How the execution engine at 54.225.202.78:8080 gets broker credentials, and why it can return DH-901 even when TC's own broker connection is live.
---

## The engine has its own credential store

The TC Execution Engine (`http://54.225.202.78:8080`) maintains its **own** copy of broker credentials — separate from TC's encrypted `user_brokers` table. It is NOT a pure stateless proxy.

**PlaceOrderRequest schema**: sends `user_id`, `user_broker_id`, `broker_type`, `client_id`, `access_token` — but the engine **ignores** the credential fields and uses its own DB lookup by `user_broker_id`. DH-901 still fires with stale stored creds even when fresh creds are in payload.

**The mismatch problem**: When a user regenerates a Dhan token in TC, TC updates its own `user_brokers` table. But the engine's credential store is NOT automatically updated.

## IP whitelist (Dhan)

Only `54.225.202.78` (TC Engine) needs to be whitelisted in Dhan. Replit's outbound IPs (`34.73.12.59` etc.) must NOT call Dhan's order API — Dhan silently drops packets from non-whitelisted IPs (no HTTP response, TCP stall = worker hangs). The 12-second `ThreadPoolExecutor` timeout in `DhanBroker.place_order` is the safety net against this hang.

## Credential sync (implemented)

`services/execution_proxy.push_broker_credentials(broker_account)` pushes decrypted creds to engine:
1. `PUT /admin/api/broker-accounts/{bid}` (with `X-TC-Admin-Token` if env var set)
2. `POST /admin/api/broker-accounts` (fallback)

Called automatically from `routes.update_broker()` after `db.session.commit()` when `USE_REMOTE_EXEC=true`. User triggers it by going to Broker Settings → Edit Dhan → Save.

## In-process fallback rules (routes.py, both trade routes)

- `_secid_miss` (validation_error + "security" in message): falls through to in-process. OK only when instrument master disk cache is missing.
- Auth errors (`expired_token`, DH-901): **do NOT fall through** — engine error is surfaced directly. In-process Dhan from Replit's IP always fails (IP not whitelisted).

## Engine admin API

- `GET /admin/api/broker-accounts`, `POST /admin/api/broker-accounts`, `PUT /admin/api/broker-accounts/{id}` — require `X-TC-Admin-Token` header (set via `EXECUTION_ADMIN_TOKEN` env var; different from `EXECUTION_HMAC_SECRET`)
- `POST /v1/orders`, `POST /v1/orders/{id}/cancel`, `GET /v1/orders/{id}`
- `GET|PUT /v1/halt`, `GET /healthz`, `GET /version`
