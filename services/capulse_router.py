"""
Capulse Router — Claude-based intent classifier and engine dispatcher.
Routes natural-language chat messages to the appropriate analysis engine.
"""
import os
import json
import logging
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

INTENTS = {
    'ISCORE': 'i-Score lookup for a specific stock or company',
    'FNO_SIGNAL': 'F&O signals, NIFTY/BANKNIFTY levels, options probability, futures analysis',
    'MUTUAL_FUND': 'Mutual fund analysis, NAV, fund comparison, scheme lookup',
    'PORTFOLIO': 'Portfolio analysis, holdings review, sector concentration, rebalancing',
    'BEHAVIOUR': 'Behavioural coaching, trading patterns, discipline score, emotional trading',
    'GENERAL': 'General market questions, concepts, education, anything else',
}

INTENT_CLASSIFIER_PROMPT = """You are an intent classifier for Capulse, an AI stock research chat for Indian markets.

Classify the user's message into exactly ONE of these intents:
- ISCORE: User wants an i-Score for a specific stock/company (e.g. "i-Score for Reliance", "score of TCS", "rate HDFC Bank")
- FNO_SIGNAL: User wants F&O signals, NIFTY/BANKNIFTY analysis, options probability (e.g. "NIFTY signals", "probability of 24000", "F&O outlook")
- MUTUAL_FUND: User wants mutual fund info, NAV, fund analysis (e.g. "analyse Parag Parikh fund", "MF suggestions")
- PORTFOLIO: User wants portfolio analysis, holding review, diversification check
- BEHAVIOUR: User wants behavioural analysis, trading psychology, discipline review
- GENERAL: Everything else — explanations, education, market concepts, news

Also extract entities:
- symbol: NSE ticker if a specific stock was mentioned (e.g. "RELIANCE", "TCS", "HDFCBANK")
- index: "NIFTY" or "BANKNIFTY" if mentioned (default "NIFTY" for FNO_SIGNAL)
- level: Numeric level mentioned for F&O (e.g. 24000)

Respond with ONLY valid JSON in this exact format:
{"intent": "INTENT_NAME", "symbol": "SYMBOL_OR_NULL", "index": "INDEX_OR_NULL", "level": null_or_number, "confidence": 0.0_to_1.0}"""


def classify_intent(message: str, conversation_history: list = None) -> Dict[str, Any]:
    """
    Classify user message intent using Claude haiku.
    Falls back to GENERAL if classification fails.
    """
    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return {'intent': 'GENERAL', 'symbol': None, 'index': None, 'level': None, 'confidence': 0.5}

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Build context from recent history
        context = ''
        if conversation_history:
            recent = conversation_history[-4:]
            context = '\n'.join([f"{m['role'].upper()}: {m['content'][:200]}" for m in recent])
            context = f"\n\nRecent conversation:\n{context}"

        response = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=200,
            messages=[{
                'role': 'user',
                'content': f"{INTENT_CLASSIFIER_PROMPT}{context}\n\nUser message: {message}"
            }]
        )
        text = response.content[0].text.strip()
        result = json.loads(text)
        if result.get('confidence', 1.0) < 0.6:
            result['intent'] = 'GENERAL'
        return result

    except Exception as e:
        logger.warning(f"Intent classification failed: {e}")
        return {'intent': 'GENERAL', 'symbol': None, 'index': None, 'level': None, 'confidence': 0.5}


def handle_iscore(symbol: str, user_id: int) -> Dict[str, Any]:
    """Get i-Score for a stock symbol."""
    try:
        if not symbol:
            return {'card_type': 'prose', 'content': "Which stock would you like an i-Score for? Try asking: **i-Score for Reliance** or **rate HDFC Bank**."}

        from services.iscore import IScoreService
        service = IScoreService()
        result = service.get_iscore(symbol.upper())

        if result and result.get('score') is not None:
            return {
                'card_type': 'iscore',
                'content': f"Here's the current i-Score for **{symbol.upper()}**:",
                'card_data': {
                    'symbol': symbol.upper(),
                    'score': result.get('score', 0),
                    'components': result.get('components', {}),
                    'recommendation': result.get('recommendation', ''),
                    'summary': result.get('summary', ''),
                }
            }
        else:
            return {'card_type': 'prose', 'content': f"I couldn't fetch an i-Score for **{symbol.upper()}** right now. The data service may be temporarily unavailable. Please try again in a moment, or check that the ticker symbol is correct (e.g. RELIANCE, TCS, HDFCBANK)."}

    except ImportError:
        return _iscore_fallback(symbol)
    except Exception as e:
        logger.error(f"i-Score error for {symbol}: {e}")
        return _iscore_fallback(symbol)


def _iscore_fallback(symbol: str) -> Dict[str, Any]:
    return {
        'card_type': 'prose',
        'content': f"The i-Score engine for **{symbol.upper()}** requires the ANTHROPIC_API_KEY to be configured. Once set up, I'll analyse fundamentals, momentum, valuation, sentiment, and risk — and return a composite 0–100 score with a full breakdown.\n\nTo enable this, please configure the AI service keys in your settings."
    }


def handle_fno_signal(index: str, level: Optional[float], user_id: int) -> Dict[str, Any]:
    """Get F&O signals for NIFTY/BANKNIFTY."""
    try:
        idx = (index or 'NIFTY').upper()
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine()
        signals = engine.generate_signals(idx)

        if signals and isinstance(signals, list) and len(signals) > 0:
            return {
                'card_type': 'fno_signals',
                'content': f"Here are today's F&O signals for **{idx}**:",
                'card_data': {
                    'index': idx,
                    'signals': signals[:3],
                }
            }
        else:
            return {'card_type': 'prose', 'content': f"No signals are available for {idx} right now. The F&O engine runs during market hours (9:15 AM – 3:30 PM IST). Outside market hours, signals from the last session may not be available. Try again when the market is open."}

    except ImportError:
        return _fno_fallback(index)
    except Exception as e:
        logger.error(f"F&O signal error: {e}")
        return _fno_fallback(index)


def _fno_fallback(index: str) -> Dict[str, Any]:
    idx = (index or 'NIFTY').upper()
    return {
        'card_type': 'prose',
        'content': f"The F&O signal engine for **{idx}** analyses options chain data, implied volatility, VWAP, and momentum indicators to generate ranked trade signals.\n\nThe engine is configured and ready — signals are generated during market hours (9:15 AM – 3:30 PM IST). Check back when the market is open."
    }


def handle_portfolio(user_id: int) -> Dict[str, Any]:
    """Analyse user's manual portfolio holdings."""
    try:
        from models import ManualHolding
        holdings = ManualHolding.query.filter_by(user_id=user_id).all()

        if not holdings:
            return {
                'card_type': 'prose',
                'content': "You haven't added any holdings yet. To get a portfolio analysis, go to **Portfolio → Manual Holdings** and add your stocks.\n\nOnce you've added your holdings, I can analyse sector concentration, risk, diversification gaps, and suggest rebalancing."
            }

        from services.portfolio_analyzer_service import PortfolioAnalyzerService
        service = PortfolioAnalyzerService()
        result = service.analyse(user_id=user_id)

        return {
            'card_type': 'portfolio',
            'content': "Here's your portfolio analysis:",
            'card_data': result
        }

    except ImportError:
        return {'card_type': 'prose', 'content': "Portfolio analysis is available once you add your holdings under **Portfolio → Manual Holdings**. The engine analyses sector concentration, volatility, and rebalancing opportunities."}
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        return {'card_type': 'prose', 'content': "There was an issue loading your portfolio. Please make sure you've added holdings under Portfolio → Manual Holdings."}


def handle_behaviour(user_id: int) -> Dict[str, Any]:
    """Get behavioural coaching analysis."""
    try:
        from services.behaviour_engine import BehaviourEngine
        engine = BehaviourEngine()
        result = engine.analyse(user_id=user_id)

        if result:
            return {
                'card_type': 'behaviour',
                'content': "Here's your behavioural trading analysis:",
                'card_data': result
            }
        return {'card_type': 'prose', 'content': "I need some trading history to give you a behavioural analysis. Add trades via the Trade Now section, then come back for a full pattern breakdown — including revenge trading detection, overtrading flags, and your discipline score."}

    except Exception as e:
        logger.error(f"Behaviour error: {e}")
        return {'card_type': 'prose', 'content': "The behavioural coach analyses your trading patterns to detect emotional biases — revenge trading, overtrading, and poor risk discipline. Add some trade history first, then ask me again."}


def handle_general(message: str, conversation_history: list = None) -> Dict[str, Any]:
    """Handle general questions using Claude for prose answers."""
    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return {
                'card_type': 'prose',
                'content': "I'm a research assistant for Indian markets — I can help with i-Scores, F&O signals, portfolio analysis, and trading concepts.\n\nTo enable AI-powered answers, the ANTHROPIC_API_KEY needs to be configured. Once set up, you can ask me anything about stocks, markets, or your portfolio."
            }

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        system = """You are Capulse, an AI research assistant for Indian retail traders and investors. 
You provide clear, factual information about Indian stocks (NSE/BSE), F&O markets, mutual funds, and trading concepts.
You do NOT give buy/sell recommendations or tips. You explain, analyse, and educate.
Keep answers concise and practical. Use markdown formatting sparingly — bold for key terms, short paragraphs.
Always note when something is research/education, not advice."""

        messages = []
        if conversation_history:
            for m in conversation_history[-6:]:
                messages.append({'role': m['role'], 'content': m['content']})
        messages.append({'role': 'user', 'content': message})

        response = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=800,
            system=system,
            messages=messages
        )
        return {
            'card_type': 'prose',
            'content': response.content[0].text
        }

    except Exception as e:
        logger.error(f"General handler error: {e}")
        return {
            'card_type': 'prose',
            'content': "I can help with i-Scores, F&O signals, portfolio analysis, and market education. What would you like to know?"
        }


def route_message(message: str, user_id: int, conversation_history: list = None) -> Dict[str, Any]:
    """
    Main entry point: classify intent and dispatch to appropriate engine.
    Returns a dict with card_type, content, and optionally card_data.
    """
    start = time.time()

    classification = classify_intent(message, conversation_history)
    intent = classification.get('intent', 'GENERAL')
    symbol = classification.get('symbol')
    index = classification.get('index', 'NIFTY')
    level = classification.get('level')

    logger.info(f"Capulse intent: {intent} symbol={symbol} index={index} user={user_id}")

    try:
        if intent == 'ISCORE':
            result = handle_iscore(symbol, user_id)
        elif intent == 'FNO_SIGNAL':
            result = handle_fno_signal(index, level, user_id)
        elif intent == 'MUTUAL_FUND':
            result = {
                'card_type': 'prose',
                'content': "Mutual fund analysis is on the roadmap for Capulse Plus. For now, you can ask me about a specific fund's category, historical NAV trend, or how to evaluate overlap in your MF portfolio."
            }
        elif intent == 'PORTFOLIO':
            result = handle_portfolio(user_id)
        elif intent == 'BEHAVIOUR':
            result = handle_behaviour(user_id)
        else:
            result = handle_general(message, conversation_history)
    except Exception as e:
        logger.error(f"Router dispatch error: {e}")
        result = handle_general(message, conversation_history)

    result['intent'] = intent
    result['processing_time'] = round(time.time() - start, 2)
    return result
