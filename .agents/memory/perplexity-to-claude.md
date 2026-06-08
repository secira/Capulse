---
name: Perplexity replaced by Claude
description: Perplexity API key returns 401 (no credits). Claude is now the primary AI provider across all services. Perplexity is kept as an optional path if the key is later funded.
---

## Rule
Do NOT assume Perplexity is functional. Claude (`claude-sonnet-4-20250514`) is the primary AI provider. All Perplexity call sites now fall back to Claude automatically.

**Why:** PERPLEXITY_API_KEY returns HTTP 401 — no credits on the account. User cannot top up at this time.

## How to apply
- Any new AI feature should use `services/ai_router.py` → `call_claude()` or import `anthropic` directly.
- `PerplexityService._call_perplexity_api()` already wraps Claude automatically — callers get a Perplexity-compatible dict regardless of which provider actually ran.
- `PerplexityAPI` (perplexity_api.py) no longer raises `ValueError` on missing key — it uses Claude instead.
- `services/ai_router.py` AREA_AI_MAP: all market intelligence areas now point to `CLAUDE`.
- If Perplexity is ever re-funded, no code changes needed — the key being present automatically re-enables it as the first attempt.

## Files updated
- routes_daily_signals.py (`_call_perplexity_structured` → direct Claude)
- services/ai_router.py (AREA_AI_MAP + call_perplexity wrapper)
- services/perplexity_service.py (_call_claude_api added; _call_perplexity_api auto-falls back)
- services/perplexity_api.py (no more ValueError on missing key)
- services/ai_agent_service.py (_act_with_perplexity_research uses Claude)
- services/chatbot_service.py (generate_response uses Claude)
- services/research_assistant_service.py (research_with_perplexity falls back to Claude)
- LangGraph pipelines auto-fixed via _call_perplexity_api fallback — no code changes needed there
