"""
AI Service Router — canonical assignment of AI providers across the platform.

Each provider is used for what it does best:

  Perplexity  → Real-time web-searched market commentary, sentiment, news.
                Activated by search_recency='day' so responses always
                reflect today's market.
                Used by: Market Intelligence (AI commentary, Scentric query),
                         Stock Research (search sentiment sub-score in I-Score).

  OpenAI      → RAG embeddings and retrieval-augmented report generation
                from stored vector knowledge (pgvector).
                Used by: Research Assistant (semantic Q&A over Knowledge Base),
                         I-Score research phase (LangGraph pipeline).

  Claude      → Deep structured analysis requiring long context and careful
                reasoning: portfolio narratives, workflow hub reports, risk
                heat-map narration, trade plan generation.
                Model: claude-sonnet-4-20250514 (primary),
                       claude-3-5-haiku-20241022 (fallback for speed).
                Used by: Workflow Hub (Chief Portfolio Strategist),
                         Portfolio analysis pages, Risk Engine narration.

This module provides thin call helpers so callers don't re-implement
auth/error handling, and a lane map for documentation and routing checks.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Provider identifiers ──────────────────────────────────────────────────────
PERPLEXITY = "perplexity"
OPENAI     = "openai"
CLAUDE     = "claude"

# ── Lane map: area → provider ─────────────────────────────────────────────────
# This is the single source of truth for which AI handles which product area.
AREA_AI_MAP: dict = {
    # Market Intelligence
    "market_commentary":          PERPLEXITY,
    "scentric_ai_query":          PERPLEXITY,
    "market_movers_data":         PERPLEXITY,
    "market_news_sentiment":      PERPLEXITY,
    # Stock Research
    "iscore_search_sentiment":    PERPLEXITY,
    "iscore_research_phase":      OPENAI,
    # Research Assistant
    "research_assistant_rag":     OPENAI,
    "knowledge_base_qa":          OPENAI,
    # Portfolio & Workflow
    "portfolio_analysis":         CLAUDE,
    "workflow_hub":               CLAUDE,
    "risk_heatmap_narration":     CLAUDE,
    "trade_plan_generation":      CLAUDE,
    "behaviour_insights":         CLAUDE,
}


# ── Call helpers ──────────────────────────────────────────────────────────────

def call_perplexity(
    prompt: str,
    *,
    system: Optional[str] = None,
    timeout: int = 25,
    search_recency: str = "day",
    max_tokens: int = 1000,
    temperature: float = 0.2,
) -> Optional[str]:
    """
    Make a Perplexity sonar-pro call with live web-search grounding.

    Returns the response text, or None on failure.
    Use for: real-time market commentary, sentiment, movers/news.

    :param prompt:         User prompt (the question / instruction)
    :param system:         Optional system message override
    :param timeout:        HTTP timeout in seconds (default 25)
    :param search_recency: Perplexity recency filter — 'day' | 'week' | 'month'
    :param max_tokens:     Max tokens in the response
    :param temperature:    Sampling temperature (low = more factual)
    """
    api_key = os.environ.get('PERPLEXITY_API_KEY', '')
    if not api_key:
        logger.warning("ai_router.call_perplexity: PERPLEXITY_API_KEY not set")
        return None
    try:
        import requests
        sys_msg = system or (
            "You are a real-time Indian financial market analyst. "
            "Be concise, factual, and use today's live data."
        )
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar-pro",
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user",   "content": prompt},
                ],
                "max_tokens":            max_tokens,
                "temperature":           temperature,
                "search_recency_filter": search_recency,
                "stream":                False,
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        logger.warning(f"ai_router.call_perplexity: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"ai_router.call_perplexity: {e}")
    return None


def call_claude(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 1500,
    timeout: int = 60,
) -> Optional[str]:
    """
    Make an Anthropic Claude call.

    Primary model:  claude-sonnet-4-20250514
    Fallback model: claude-3-5-haiku-20241022

    Returns the response text, or None on failure.
    Use for: portfolio analysis, deep structured reports, workflow hub.

    :param prompt:     User / task prompt
    :param system:     Optional system prompt override
    :param max_tokens: Max tokens in the response
    :param timeout:    HTTP timeout in seconds
    """
    try:
        from services.anthropic_service import AnthropicService
        svc = AnthropicService()
        result = svc.generate_analysis(
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
        )
        if result:
            return result.get('content') or result.get('text') or str(result)
    except Exception as e:
        logger.warning(f"ai_router.call_claude: {e}")
    return None


def provider_for(area: str) -> Optional[str]:
    """
    Return the canonical provider name for a product area key.

    >>> provider_for("market_commentary")
    'perplexity'
    >>> provider_for("portfolio_analysis")
    'claude'
    """
    return AREA_AI_MAP.get(area)
