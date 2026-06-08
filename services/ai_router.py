"""
AI Service Router — canonical assignment of AI providers across the platform.

Each provider is used for what it does best:

  Claude      → Market commentary, Scentric AI queries, sentiment analysis.
                Also handles all areas previously routed to Perplexity.
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
                       claude-haiku-4-5 (fallback for speed).
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
    "market_commentary":          CLAUDE,
    "scentric_ai_query":          CLAUDE,
    "market_movers_data":         CLAUDE,
    "market_news_sentiment":      CLAUDE,
    # Stock Research
    "iscore_search_sentiment":    CLAUDE,
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
    Previously called Perplexity sonar-pro. Now routes to Claude directly.
    Signature kept unchanged so all existing callers work without modification.
    """
    return call_claude(prompt, system=system, max_tokens=max_tokens, timeout=timeout)


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
