import os
import json
import time
import logging
from typing import Dict, List, Optional, Any, Type
from datetime import datetime

import anthropic

logger = logging.getLogger(__name__)


class AnthropicService:
    PRIMARY_MODEL = "claude-sonnet-4-20250514"
    FALLBACK_MODEL = "claude-haiku-4-5"
    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 2.0

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self._api_key:
                logger.warning("ANTHROPIC_API_KEY not set - Claude API calls will fail")
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _call_with_retry(
        self,
        model: str,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Dict] = None,
    ) -> anthropic.types.Message:
        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.client.messages.create(**kwargs)
                return response
            except anthropic.RateLimitError as exc:
                last_exc = exc
                wait = self.RETRY_BACKOFF_BASE ** attempt
                logger.warning(f"Rate limited (attempt {attempt}/{self.MAX_RETRIES}), waiting {wait}s")
                time.sleep(wait)
            except anthropic.APIStatusError as exc:
                last_exc = exc
                if exc.status_code >= 500:
                    wait = self.RETRY_BACKOFF_BASE ** attempt
                    logger.warning(f"Server error {exc.status_code} (attempt {attempt}/{self.MAX_RETRIES}), waiting {wait}s")
                    time.sleep(wait)
                else:
                    raise
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                wait = self.RETRY_BACKOFF_BASE ** attempt
                logger.warning(f"Connection error (attempt {attempt}/{self.MAX_RETRIES}), waiting {wait}s")
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    def chat(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        model: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        target_model = model or self.PRIMARY_MODEL

        try:
            response = self._call_with_retry(
                model=target_model,
                messages=messages,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
            )
        except Exception as primary_exc:
            if target_model == self.PRIMARY_MODEL:
                logger.warning(f"Primary model failed ({primary_exc}), falling back to {self.FALLBACK_MODEL}")
                try:
                    response = self._call_with_retry(
                        model=self.FALLBACK_MODEL,
                        messages=messages,
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                except Exception as fallback_exc:
                    logger.error(f"Fallback model also failed: {fallback_exc}")
                    raise fallback_exc
            else:
                raise

        return self._format_response(response)

    def structured_output(
        self,
        messages: List[Dict[str, str]],
        output_schema: Dict[str, Any],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        schema_instruction = (
            f"\n\nYou MUST respond with valid JSON matching this schema:\n"
            f"```json\n{json.dumps(output_schema, indent=2)}\n```\n"
            f"Return ONLY the JSON object, no other text."
        )

        effective_system = (system or "") + schema_instruction

        result = self.chat(
            messages=messages,
            system=effective_system,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )

        text = result.get("text", "")
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
            result["parsed"] = parsed
        except json.JSONDecodeError:
            logger.warning("Failed to parse structured output as JSON")
            result["parsed"] = None
            result["parse_error"] = True

        return result

    def tool_use(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        tool_choice: Optional[Dict] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = self.chat(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
        )
        return result

    def _format_response(self, response: anthropic.types.Message) -> Dict[str, Any]:
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "model": response.model,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "stop_reason": response.stop_reason,
            "id": response.id,
        }

    def analyze_stock(self, symbol: str, context: str = "") -> Dict[str, Any]:
        system = (
            "You are a senior financial analyst at Target Capital specializing in Indian equity markets. "
            "Provide precise, data-driven analysis with specific scores and actionable recommendations."
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Analyze the stock {symbol} for investment.\n\n"
                    f"Context:\n{context}\n\n"
                    "Provide: sentiment score (0-100), confidence (0-1), key findings, risks, "
                    "recommendation (STRONG BUY/BUY/HOLD/SELL/STRONG SELL), and reasoning."
                ),
            }
        ]
        schema = {
            "sentiment_score": "number (0-100)",
            "confidence": "number (0-1)",
            "recommendation": "string",
            "key_findings": ["string"],
            "risks": ["string"],
            "reasoning": "string",
        }
        return self.structured_output(messages=messages, output_schema=schema, system=system)

    def analyze_portfolio(self, portfolio_data: Dict, user_preferences: Optional[Dict] = None) -> Dict[str, Any]:
        system = (
            "You are a Chief Portfolio Strategist at Target Capital. "
            "Analyze the portfolio and provide comprehensive optimization advice "
            "aligned with the user's preferences and goals."
        )
        prefs_section = ""
        if user_preferences:
            prefs_section = f"\n\nUser Preferences:\n{json.dumps(user_preferences, indent=2)}"

        messages = [
            {
                "role": "user",
                "content": (
                    f"Analyze this portfolio:\n{json.dumps(portfolio_data, indent=2)}"
                    f"{prefs_section}\n\n"
                    "Provide: risk score (1-10), diversification quality, sector analysis, "
                    "allocation recommendations, opportunities, and action plan."
                ),
            }
        ]
        schema = {
            "risk_score": "number (1-10)",
            "diversification_quality": "string",
            "sector_analysis": {},
            "allocation_recommendations": {},
            "opportunities": [],
            "action_plan": [],
            "executive_summary": "string",
        }
        return self.structured_output(messages=messages, output_schema=schema, system=system)

    def research_query(self, query: str, context: str = "", market_data: str = "") -> Dict[str, Any]:
        system = (
            "You are an expert financial research analyst at Target Capital. "
            "Provide comprehensive, well-cited research answers about Indian markets."
        )
        content = f"Research question: {query}"
        if context:
            content += f"\n\nHistorical context:\n{context}"
        if market_data:
            content += f"\n\nCurrent market data:\n{market_data}"
        content += "\n\nProvide a detailed research report in markdown format."

        messages = [{"role": "user", "content": content}]
        return self.chat(messages=messages, system=system, temperature=0.2)
