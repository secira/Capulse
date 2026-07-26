---
name: NSEPython blocked in Replit
description: NSEPython cannot reach NSE India's servers from Replit — DNS fails. All bare calls hang indefinitely.
---

**Rule:** Never call NSEPython functions directly. Always use the `_nsepy_call(fn, *args, timeout=5)` helper in `services/nse_service.py`.

**Why:** Replit (and most cloud hosts) cannot resolve NSE India's DNS names (e.g. `iislliveblob.niftyindices.com`). Bare calls to `nse_quote`, `nse_get_index_quote`, `indiavix`, `pcr`, `option_chain`, `nse_get_top_gainers/losers`, `nse_most_active`, `get_fao_participant_oi` block for 2–5 minutes before failing. The app appears frozen from the user's perspective.

**How to apply:** `_nsepy_call` wraps each call in a ThreadPoolExecutor with `shutdown(wait=False)` so callers are unblocked within 5 seconds. The same "no `with ThreadPoolExecutor`" rule (see threadpool-timeout-pattern.md) applies here too.

**Related:** The LangGraph i-score engine had the same issue in `search_sentiment` (used `with ThreadPoolExecutor` which blocks on `__exit__` after timeout) and `qualitative_analysis` (called Perplexity with no timeout). Both fixed with `shutdown(wait=False)`.
