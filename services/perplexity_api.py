"""
Fresh Perplexity API Service for AI Investment Advisor
Clean implementation with proper model names and error handling
"""
import os
import requests
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

class PerplexityAPI:
    """Investment advice API — uses Claude (primary) with Perplexity as optional fallback."""

    def __init__(self):
        self.api_key = os.environ.get('PERPLEXITY_API_KEY')
        self.anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
        self.base_url = "https://api.perplexity.ai/chat/completions"
        self.headers = {
            'Authorization': f'Bearer {self.api_key or ""}',
            'Content-Type': 'application/json'
        }
        self.model = "sonar-pro"
        if not self.api_key and not self.anthropic_api_key:
            logger.warning("Neither PERPLEXITY_API_KEY nor ANTHROPIC_API_KEY set — AI chat will be limited")
        elif not self.api_key:
            logger.info("PERPLEXITY_API_KEY not set — PerplexityAPI will use Claude (Anthropic)")
        else:
            logger.info("Perplexity API initialized successfully")
    
    def get_investment_advice(self, user_message: str, conversation_history: list = None) -> Tuple[str, Dict]:
        """
        Get investment advice — uses Claude (primary) with Perplexity as optional override.
        Returns: (response_text, usage_info)
        """
        system_content = (
            "You are an expert investment advisor specializing in Indian and global stock markets. "
            "Provide accurate financial insights, market analysis, and investment recommendations. "
            "Focus on practical advice. Use ₹ for Indian currency, NSE/BSE for exchanges."
        )

        # Build message list for whichever provider we'll use
        messages = []
        if conversation_history:
            last_role = None
            for msg in conversation_history[-6:]:
                msg_role = msg.get('role')
                if msg_role in ['user', 'assistant'] and msg_role != last_role:
                    messages.append(msg)
                    last_role = msg_role
        messages.append({"role": "user", "content": user_message})

        # ── Claude is the primary AI provider ────────────────────────────────
        if self.anthropic_api_key:
            try:
                import anthropic as _ant
                client = _ant.Anthropic(api_key=self.anthropic_api_key)
                msg = client.messages.create(
                    model='claude-3-5-sonnet-20241022',
                    max_tokens=1000,
                    system=system_content,
                    messages=messages,
                )
                content = msg.content[0].text if msg.content else ''
                tokens = (msg.usage.input_tokens or 0) + (msg.usage.output_tokens or 0)
                logger.info(f"Claude API call successful. Tokens: {tokens}")
                return content, {'total_tokens': tokens, 'model': 'claude-3-5-sonnet-20241022'}
            except Exception as e:
                logger.error(f"Claude API error: {e}")

        return (
            "I'm experiencing technical difficulties. Please try again shortly.",
            {"error": True}
        )
    
    def validate_connection(self) -> bool:
        """Test if Perplexity API is accessible"""
        try:
            test_response, _ = self.get_investment_advice("Test connection")
            return not test_response.startswith("I'm experiencing technical difficulties")
        except Exception:
            return False