"""
LLM Client — provider-agnostic interface for all AI language model calls.
═══════════════════════════════════════════════════════════════════════════

Default provider: Claude (primary) → OpenAI GPT-4o (automatic fallback).

Set LLM_PROVIDER env var to switch providers:
  - 'anthropic'  Claude primary, OpenAI fallback (default)
  - 'openai'     OpenAI only, no fallback
  - 'capulse'    Capulse self-hosted model (register CapulseLLMBackend first)

Usage (same everywhere in the codebase):
──────────────────────────────────────────
    from services.llm_client import get_llm_client, Model

    llm = get_llm_client()

    # Plain text answer
    text = llm.chat(
        [{'role': 'user', 'content': 'Summarise the NIFTY 50 outlook.'}],
        system="You are a market analyst.",
        max_tokens=300,
    )

    # JSON answer (structured_output strips fences and parses automatically)
    data = llm.structured_output(
        [{'role': 'user', 'content': 'Classify: ' + message}],
        system="Return only JSON.",
        max_tokens=200,
    )

    # Speed-optimised tasks (classification, extraction, one-liners)
    text = llm.chat([...], model=Model.FAST)

    # Quality-optimised tasks (analysis, narrative, research)
    text = llm.chat([...], model=Model.SMART)

To add a new backend (e.g. CapulseLLM):
──────────────────────────────────────────
    class CapulseLLMBackend(LLMClient):
        def chat(self, messages, *, system=None, max_tokens=1024,
                 temperature=0.3, model=None) -> str:
            # call your inference endpoint here
            ...

    _BACKENDS['capulse'] = CapulseLLMBackend

Then set LLM_PROVIDER=capulse in the environment.
"""

import abc
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Model aliases ─────────────────────────────────────────────────────────────
# Use these constants instead of hard-coding provider model IDs in call sites.
# Each backend maps them to its own model identifiers.

class Model:
    FAST  = 'fast'   # Low-latency: classification, extraction, short answers
    SMART = 'smart'  # Full-quality: analysis, narrative, research, long output


# ── Abstract interface ────────────────────────────────────────────────────────

class LLMClient(abc.ABC):
    """Provider-agnostic interface. Backends only need to implement chat()."""

    @abc.abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        model: Optional[str] = None,
    ) -> str:
        """Send a chat request; return the assistant's text reply.

        Args:
            messages:    List of {'role': 'user'|'assistant', 'content': str}.
            system:      Optional system prompt.
            max_tokens:  Maximum tokens in the response.
            temperature: Sampling temperature (lower = more deterministic).
            model:       Model alias ('fast', 'smart') or None for default.
                         Raw provider model IDs are passed through as-is for
                         legacy call sites; migrate them to Model.FAST/SMART.
        Returns:
            The assistant's text response as a plain string.
        """

    def structured_output(
        self,
        messages: List[Dict[str, str]],
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.1,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a chat request; parse and return the JSON reply as a dict.

        Default implementation calls chat() then strips markdown fences and
        parses JSON.  Backends with native structured-output APIs can override
        this for better reliability.

        Returns {} on parse failure (never raises).
        """
        text = self.chat(
            messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )
        text = text.strip()
        # Strip ```json ... ``` or ``` ... ``` wrappers
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text.strip())
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f'llm_client: structured_output JSON parse failed: {exc}')
            return {}


# ── Anthropic (Claude) backend ────────────────────────────────────────────────

class AnthropicBackend(LLMClient):
    """Claude backend — delegates to AnthropicService for retry / fallback logic."""

    _MODEL_MAP: Dict[str, str] = {
        Model.FAST:  'claude-haiku-4-5',
        Model.SMART: 'claude-sonnet-4-5',
    }

    def _resolve_model(self, model: Optional[str]) -> Optional[str]:
        """Translate a Model alias to an Anthropic model ID, or pass through."""
        if model is None:
            return None  # AnthropicService will use its PRIMARY_MODEL default
        return self._MODEL_MAP.get(model, model)  # unknown → pass through as-is

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        model: Optional[str] = None,
    ) -> str:
        from services.anthropic_service import AnthropicService
        svc  = AnthropicService()
        resp = svc.chat(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            model=self._resolve_model(model),
        )
        # AnthropicService._format_response uses key 'text'
        return resp.get('text', '') or ''


# ── OpenAI (ChatGPT) backend ──────────────────────────────────────────────────

class OpenAIBackend(LLMClient):
    """OpenAI GPT backend — secondary provider / explicit OpenAI-only mode."""

    _MODEL_MAP: Dict[str, str] = {
        Model.FAST:  'gpt-4o-mini',
        Model.SMART: 'gpt-4o',
    }

    def _resolve_model(self, model: Optional[str]) -> str:
        if model is None:
            return self._MODEL_MAP[Model.SMART]
        return self._MODEL_MAP.get(model, model)

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        model: Optional[str] = None,
    ) -> str:
        api_key = os.environ.get('OPENAI_API_KEY', '')
        if not api_key:
            raise RuntimeError('OPENAI_API_KEY is not set — cannot use OpenAI backend')
        import openai as _oai
        client = _oai.OpenAI(api_key=api_key)
        oai_messages: List[Dict[str, str]] = []
        if system:
            oai_messages.append({'role': 'system', 'content': system})
        oai_messages.extend(messages)
        resp = client.chat.completions.create(
            model=self._resolve_model(model),
            messages=oai_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ''


# ── Claude-primary with OpenAI fallback (default) ────────────────────────────

class ClaudeWithOpenAIFallback(LLMClient):
    """Primary: Claude (Anthropic).  Automatic fallback: OpenAI GPT-4o.

    On any exception from Claude (rate-limit, outage, missing key) the same
    request is retried once against OpenAI before propagating the error.
    Set OPENAI_API_KEY to enable the fallback; if the key is absent the
    fallback silently re-raises the Claude error.
    """

    def __init__(self):
        self._claude = AnthropicBackend()
        self._openai = OpenAIBackend()

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        model: Optional[str] = None,
    ) -> str:
        try:
            result = self._claude.chat(
                messages, system=system, max_tokens=max_tokens,
                temperature=temperature, model=model,
            )
            if result:
                return result
            raise ValueError('empty response from Claude')
        except Exception as claude_err:
            if not os.environ.get('OPENAI_API_KEY'):
                raise  # no point trying OpenAI without a key
            logger.warning(
                f'llm_client: Claude failed ({claude_err!r}); '
                f'retrying with OpenAI fallback.'
            )
            try:
                return self._openai.chat(
                    messages, system=system, max_tokens=max_tokens,
                    temperature=temperature, model=model,
                )
            except Exception as oai_err:
                logger.error(
                    f'llm_client: OpenAI fallback also failed ({oai_err!r}). '
                    f'Both providers unavailable.'
                )
                raise


# ── Registry & factory ────────────────────────────────────────────────────────

_BACKENDS: Dict[str, type] = {
    'anthropic': ClaudeWithOpenAIFallback,  # Claude primary, OpenAI auto-fallback
    'openai':    OpenAIBackend,             # OpenAI only (no Claude)
    # 'capulse': CapulseLLMBackend,         # register here when ready
}


def get_llm_client() -> LLMClient:
    """Return a ready-to-use LLMClient for the configured provider.

    Reads LLM_PROVIDER from the environment (default: 'anthropic').
    Falls back to ClaudeWithOpenAIFallback and logs a warning for unknown values.
    """
    provider = os.environ.get('LLM_PROVIDER', 'anthropic').lower().strip()
    backend_cls = _BACKENDS.get(provider)
    if backend_cls is None:
        logger.warning(
            f"llm_client: unknown LLM_PROVIDER={provider!r}; "
            f"falling back to 'anthropic' (Claude + OpenAI). "
            f"Known providers: {list(_BACKENDS)}"
        )
        backend_cls = ClaudeWithOpenAIFallback
    return backend_cls()
