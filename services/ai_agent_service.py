"""
Agentic AI Service for Capulse Platform
Implements autonomous AI system with n8n workflow integration for stock trading, portfolio management, and market analysis.
Features OpenAI and Perplexity API integration for advanced reasoning and real-time research capabilities.
"""

import pandas as pd
import numpy as np
import yfinance as yf
import os
import requests
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AgenticAICoordinator:
    """
    Agentic AI Coordinator that orchestrates autonomous learning, reasoning, acting, and adapting
    using OpenAI, Perplexity, and n8n workflow integration.
    """
    
    def __init__(self):
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        # n8n is optional — enable only if explicitly configured. The previous
        # localhost default silently failed in production.
        self.n8n_webhook_url = os.environ.get("N8N_WEBHOOK_URL") or None
        if not self.n8n_webhook_url:
            logger.info("N8N_WEBHOOK_URL not set — n8n integration disabled")
        self.learning_history = []
        self.adaptation_metrics = {}
        
    def analyze_with_agentic_ai(self, symbol: str, analysis_type: str = "comprehensive") -> Dict[str, Any]:
        """
        Main entry point for Agentic AI analysis that coordinates all AI capabilities:
        - Learning from historical patterns
        - Reasoning through market scenarios
        - Acting on opportunities
        - Adapting strategies based on outcomes
        """
        try:
            # Step 1: LEARN - Gather and learn from data
            market_data = self._learn_from_market_data(symbol)
            historical_patterns = self._learn_from_historical_patterns(symbol)
            
            # Step 2: REASON - Use OpenAI for complex reasoning
            reasoning_analysis = self._reason_with_openai(symbol, market_data, historical_patterns)
            
            # Step 3: ACT - Use Perplexity for real-time research and action planning
            current_research = self._act_with_perplexity_research(symbol)
            
            # Step 4: ADAPT - Combine insights and adapt strategy
            final_recommendation = self._adapt_strategy(symbol, reasoning_analysis, current_research)
            
            # Trigger n8n workflow for continuous monitoring
            self._trigger_n8n_workflow("stock_analysis", {
                "symbol": symbol,
                "recommendation": final_recommendation,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            return final_recommendation
            
        except Exception as e:
            logger.error(f"Agentic AI analysis error for {symbol}: {str(e)}")
            return {"error": str(e), "symbol": symbol}
    
    def _learn_from_market_data(self, symbol: str) -> Dict[str, Any]:
        """Learn from current market data and patterns"""
        try:
            stock = yf.Ticker(symbol)
            data = stock.history(period="1y")
            info = stock.info
            
            if data.empty:
                return {"error": "No market data available"}
            
            # Calculate learning metrics
            volatility = data['Close'].pct_change().std() * np.sqrt(252)
            trend_strength = (data['Close'].iloc[-1] / data['Close'].iloc[0] - 1) * 100
            volume_trend = data['Volume'].rolling(20).mean().iloc[-1] / data['Volume'].rolling(60).mean().iloc[-1]
            
            return {
                "current_price": float(data['Close'].iloc[-1]),
                "volatility": float(volatility),
                "trend_strength": float(trend_strength),
                "volume_trend": float(volume_trend),
                "market_cap": info.get("marketCap", 0),
                "pe_ratio": info.get("trailingPE", 0),
                "data_quality": "high" if len(data) > 200 else "medium"
            }
            
        except Exception as e:
            logger.error(f"Learning from market data failed: {str(e)}")
            return {"error": str(e)}
    
    def _learn_from_historical_patterns(self, symbol: str) -> Dict[str, Any]:
        """Analyze historical patterns to learn long-term behaviors"""
        try:
            stock = yf.Ticker(symbol)
            data = stock.history(period="2y")
            
            if len(data) < 50:
                return {"pattern_confidence": "low", "learnings": []}
            
            # Pattern recognition learning
            learnings = []
            
            # Seasonal patterns
            monthly_returns = data.groupby(data.index.month)['Close'].pct_change().mean()
            best_month = monthly_returns.idxmax()
            worst_month = monthly_returns.idxmin()
            
            learnings.append(f"Historically performs best in month {best_month}")
            learnings.append(f"Typically weaker in month {worst_month}")
            
            # Support and resistance learning
            resistance = data['High'].rolling(20).max().iloc[-1]
            support = data['Low'].rolling(20).min().iloc[-1]
            current_price = data['Close'].iloc[-1]
            
            if current_price > resistance * 0.95:
                learnings.append("Near resistance level - potential pullback")
            elif current_price < support * 1.05:
                learnings.append("Near support level - potential bounce")
            
            return {
                "pattern_confidence": "high",
                "learnings": learnings,
                "resistance_level": float(resistance),
                "support_level": float(support),
                "historical_volatility": float(data['Close'].pct_change().std() * np.sqrt(252))
            }
            
        except Exception as e:
            logger.error(f"Historical pattern learning failed: {str(e)}")
            return {"pattern_confidence": "low", "learnings": []}
    
    def _reason_with_openai(self, symbol: str, market_data: Dict[str, Any], historical_patterns: Dict[str, Any]) -> Dict[str, Any]:
        """Use OpenAI GPT-4 for complex reasoning and scenario analysis"""
        if not self.openai_api_key:
            logger.warning("OpenAI API key not available, using fallback reasoning")
            return self._fallback_reasoning(market_data, historical_patterns)
        
        try:
            headers = {
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json"
            }
            
            # Construct reasoning prompt
            prompt = self._build_reasoning_prompt(symbol, market_data, historical_patterns)
            
            payload = {
                "model": "gpt-4o",  # the newest OpenAI model is "gpt-4o" which was released May 13, 2024. do not change this unless explicitly requested by the user
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert financial analyst with Agentic AI capabilities. You learn from patterns, reason through scenarios, act on insights, and adapt strategies. Provide detailed analysis in JSON format."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 1000,
                "temperature": 0.3
            }
            
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60  # Increased for OpenAI API
            )
            
            if response.status_code == 200:
                result = response.json()
                content = json.loads(result['choices'][0]['message']['content'])
                content['reasoning_source'] = 'openai_gpt4'
                return content
            else:
                logger.error(f"OpenAI API error: {response.status_code}")
                return self._fallback_reasoning(market_data, historical_patterns)
                
        except Exception as e:
            logger.error(f"OpenAI reasoning failed: {str(e)}")
            return self._fallback_reasoning(market_data, historical_patterns)
    
    def _act_with_perplexity_research(self, symbol: str) -> Dict[str, Any]:
        """Research stock using Claude (Anthropic) — previously used Perplexity."""
        try:
            import anthropic
            api_key = os.environ.get('ANTHROPIC_API_KEY', '')
            if not api_key:
                logger.warning("ANTHROPIC_API_KEY not available, using fallback research")
                return self._fallback_research(symbol)

            # Inject live price from Dhan/NSE so Claude doesn't guess
            live_price_str = ""
            try:
                from services.market_data_gateway import get_price as _gp
                pr = _gp(symbol.upper())
                if pr.get('success') and pr.get('value', 0) > 0:
                    src = pr.get('source_detail') or pr.get('source', 'broker')
                    live_price_str = (
                        f"\n\n⚡ LIVE MARKET DATA (from {src}): "
                        f"Current price of {symbol.upper()} = ₹{pr['value']:,.2f}. "
                        f"Use this exact price — do NOT estimate."
                    )
            except Exception:
                pass

            query = (
                f"Provide a research summary for {symbol} (Indian stock, NSE). "
                f"Cover: recent price movements, key financial metrics (P/E, ROE, revenue growth), "
                f"business developments, analyst outlook, and main risks. "
                f"Be concise and use ₹ for currency."
                f"{live_price_str}"
            )
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model='claude-3-5-sonnet-20241022',
                max_tokens=800,
                system=(
                    "You are a market research specialist covering Indian equities (NSE/BSE). "
                    "Provide factual, data-driven analysis relevant to trading decisions."
                ),
                messages=[{'role': 'user', 'content': query}],
            )
            research_content = msg.content[0].text if msg.content else ''
            if research_content:
                return {
                    "research_summary": research_content,
                    "citations": [],
                    "research_source": "claude_ai",
                    "research_timestamp": datetime.now(timezone.utc).isoformat(),
                    "key_findings": self._extract_key_findings(research_content)
                }
            return self._fallback_research(symbol)

        except Exception as e:
            logger.error(f"Claude research failed for {symbol}: {str(e)}")
            return self._fallback_research(symbol)
    
    def _adapt_strategy(self, symbol: str, reasoning_analysis: Dict[str, Any], current_research: Dict[str, Any]) -> Dict[str, Any]:
        """Adapt and combine insights from learning, reasoning, and research to form final recommendation"""
        try:
            # Extract key metrics
            confidence_scores = []
            recommendations = []
            risk_factors = []
            
            # From reasoning analysis
            if 'confidence' in reasoning_analysis:
                confidence_scores.append(reasoning_analysis['confidence'])
            if 'recommendation' in reasoning_analysis:
                recommendations.append(reasoning_analysis['recommendation'])
            if 'risks' in reasoning_analysis:
                risk_factors.extend(reasoning_analysis['risks'])
            
            # From current research
            research_sentiment = self._analyze_research_sentiment(current_research.get('research_summary', ''))
            if research_sentiment['confidence'] > 0.6:
                confidence_scores.append(research_sentiment['confidence'])
                recommendations.append(research_sentiment['recommendation'])
            
            # Adaptive strategy formation
            final_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.5
            final_recommendation = self._consolidate_recommendations(recommendations)
            
            # Adaptation metrics for future learning
            adaptation_key = f"{symbol}_{datetime.now(timezone.utc).strftime('%Y%m')}"
            self.adaptation_metrics[adaptation_key] = {
                "recommendation": final_recommendation,
                "confidence": final_confidence,
                "factors_considered": len(confidence_scores),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            return {
                "symbol": symbol,
                "final_recommendation": final_recommendation,
                "confidence": final_confidence,
                "reasoning_summary": reasoning_analysis.get('summary', 'Analysis completed'),
                "research_insights": current_research.get('key_findings', []),
                "risk_assessment": risk_factors,
                "adaptation_notes": f"Strategy adapted based on {len(confidence_scores)} analysis sources",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agentic_ai_version": "1.0"
            }
            
        except Exception as e:
            logger.error(f"Strategy adaptation failed: {str(e)}")
            return {
                "symbol": symbol,
                "final_recommendation": "HOLD",
                "confidence": 0.5,
                "error": "Strategy adaptation failed",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _trigger_n8n_workflow(self, workflow_type: str, data: Dict[str, Any]) -> bool:
        """Trigger n8n workflow for continuous monitoring and automation"""
        try:
            webhook_url = f"{self.n8n_webhook_url}/{workflow_type}"
            
            payload = {
                "workflow_type": workflow_type,
                "data": data,
                "triggered_by": "agentic_ai",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            response = requests.post(webhook_url, json=payload, timeout=10)
            
            if response.status_code in [200, 201]:
                logger.info(f"n8n workflow '{workflow_type}' triggered successfully")
                return True
            else:
                logger.warning(f"n8n workflow trigger failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"n8n workflow trigger error: {str(e)}")
            return False
    
    def _build_reasoning_prompt(self, symbol: str, market_data: Dict[str, Any], historical_patterns: Dict[str, Any]) -> str:
        """Build comprehensive prompt for OpenAI reasoning"""
        prompt = f"""
        Analyze {symbol} stock using Agentic AI approach (learn, reason, act, adapt):

        LEARNING DATA:
        - Current Price: ${market_data.get('current_price', 'N/A')}
        - Volatility: {market_data.get('volatility', 'N/A')}%
        - Trend Strength: {market_data.get('trend_strength', 'N/A')}%
        - Volume Trend: {market_data.get('volume_trend', 'N/A')}
        - P/E Ratio: {market_data.get('pe_ratio', 'N/A')}
        
        HISTORICAL PATTERNS:
        - Pattern Confidence: {historical_patterns.get('pattern_confidence', 'N/A')}
        - Key Learnings: {historical_patterns.get('learnings', [])}
        - Support Level: ${historical_patterns.get('support_level', 'N/A')}
        - Resistance Level: ${historical_patterns.get('resistance_level', 'N/A')}

        REASONING REQUIREMENTS:
        1. Learn from the provided data patterns
        2. Reason through multiple market scenarios (bull, bear, sideways)
        3. Act by providing specific recommendation with rationale
        4. Adapt by suggesting strategy adjustments for different conditions

        Provide response in JSON format with:
        {{
            "recommendation": "BUY/HOLD/SELL",
            "confidence": 0.0-1.0,
            "summary": "Brief analysis summary",
            "scenarios": {{"bull": "strategy", "bear": "strategy", "sideways": "strategy"}},
            "risks": ["risk1", "risk2"],
            "adaptation_triggers": ["condition1", "condition2"]
        }}
        """
        return prompt
    
    def _fallback_reasoning(self, market_data: Dict[str, Any], historical_patterns: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback reasoning when OpenAI is not available"""
        try:
            # Simple rule-based reasoning
            confidence = 0.6
            recommendation = "HOLD"
            
            # Basic trend analysis
            trend_strength = market_data.get('trend_strength', 0)
            volatility = market_data.get('volatility', 0.2)
            
            if trend_strength > 10 and volatility < 0.3:
                recommendation = "BUY"
                confidence = 0.7
            elif trend_strength < -10 and volatility > 0.4:
                recommendation = "SELL"
                confidence = 0.7
            
            return {
                "recommendation": recommendation,
                "confidence": confidence,
                "summary": "Fallback analysis based on trend and volatility",
                "reasoning_source": "fallback_rules"
            }
            
        except Exception as e:
            logger.error(f"Fallback reasoning failed: {str(e)}")
            return {
                "recommendation": "HOLD",
                "confidence": 0.5,
                "summary": "Unable to complete analysis",
                "reasoning_source": "error_fallback"
            }
    
    def _fallback_research(self, symbol: str) -> Dict[str, Any]:
        """Fallback research when Perplexity is not available"""
        return {
            "research_summary": f"Research data for {symbol} not available - external API required",
            "key_findings": ["Real-time research requires API access"],
            "research_source": "fallback",
            "research_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def _extract_key_findings(self, research_content: str) -> List[str]:
        """Extract key findings from research content"""
        findings = []
        
        # Simple keyword-based extraction
        keywords = ['upgrade', 'downgrade', 'earnings', 'revenue', 'profit', 'loss', 'acquisition', 'merger', 'dividend']
        
        sentences = research_content.split('.')
        for sentence in sentences:
            for keyword in keywords:
                if keyword.lower() in sentence.lower() and len(sentence.strip()) > 10:
                    findings.append(sentence.strip())
                    break
        
        return findings[:5]  # Return top 5 findings
    
    def _analyze_research_sentiment(self, research_content: str) -> Dict[str, Any]:
        """Analyze sentiment from research content"""
        positive_words = ['upgrade', 'buy', 'strong', 'growth', 'profit', 'beat', 'exceed', 'positive', 'bullish']
        negative_words = ['downgrade', 'sell', 'weak', 'decline', 'loss', 'miss', 'below', 'negative', 'bearish']
        
        content_lower = research_content.lower()
        positive_count = sum(1 for word in positive_words if word in content_lower)
        negative_count = sum(1 for word in negative_words if word in content_lower)
        
        if positive_count > negative_count:
            return {"recommendation": "BUY", "confidence": min(0.8, 0.5 + (positive_count - negative_count) * 0.1)}
        elif negative_count > positive_count:
            return {"recommendation": "SELL", "confidence": min(0.8, 0.5 + (negative_count - positive_count) * 0.1)}
        else:
            return {"recommendation": "HOLD", "confidence": 0.5}
    
    def _consolidate_recommendations(self, recommendations: List[str]) -> str:
        """Consolidate multiple recommendations into final decision"""
        if not recommendations:
            return "HOLD"
        
        buy_count = recommendations.count("BUY")
        sell_count = recommendations.count("SELL")
        hold_count = recommendations.count("HOLD")
        
        if buy_count > sell_count and buy_count > hold_count:
            return "BUY"
        elif sell_count > buy_count and sell_count > hold_count:
            return "SELL"
        else:
            return "HOLD"

class TradingAgent:
    """Core trading agent that makes buy/sell/hold decisions based on technical analysis"""
    
    def __init__(self):
        self.name = "Trading Agent"
        self.confidence_threshold = 0.7
        
    def analyze_stock(self, symbol: str, period: str = "3mo") -> Dict:
        """Analyze stock and provide trading recommendation"""
        try:
            # Fetch stock data
            stock = yf.Ticker(symbol)
            data = stock.history(period=period)
            
            if data.empty:
                return {"error": "No data available for symbol"}
            
            # Calculate technical indicators
            signals = self._calculate_technical_signals(data)
            
            # Make trading decision
            decision = self._make_trading_decision(signals)
            
            return {
                "symbol": symbol,
                "recommendation": decision["action"],
                "confidence": decision["confidence"],
                "reasoning": decision["reasoning"],
                "current_price": float(data['Close'].iloc[-1]),
                "signals": signals,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Trading Agent error for {symbol}: {str(e)}")
            return {"error": str(e)}
    
    def _calculate_technical_signals(self, data: pd.DataFrame) -> Dict:
        """Calculate various technical indicators"""
        signals = {}
        
        # Moving averages
        data['SMA_20'] = data['Close'].rolling(window=20).mean()
        data['SMA_50'] = data['Close'].rolling(window=50).mean()
        data['EMA_12'] = data['Close'].ewm(span=12).mean()
        data['EMA_26'] = data['Close'].ewm(span=26).mean()
        
        # RSI
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        data['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        data['MACD'] = data['EMA_12'] - data['EMA_26']
        data['MACD_Signal'] = data['MACD'].ewm(span=9).mean()
        
        # Bollinger Bands
        data['BB_Middle'] = data['Close'].rolling(window=20).mean()
        data['BB_Std'] = data['Close'].rolling(window=20).std()
        data['BB_Upper'] = data['BB_Middle'] + (data['BB_Std'] * 2)
        data['BB_Lower'] = data['BB_Middle'] - (data['BB_Std'] * 2)
        
        # Get latest values safely
        try:
            latest = data.iloc[-1] if len(data) > 0 else {}
            volume_avg = data['Volume'].rolling(20).mean().iloc[-1] if len(data) >= 20 else data['Volume'].mean()
            
            signals = {
                "sma_trend": "bullish" if latest['Close'] > latest['SMA_20'] else "bearish",
                "golden_cross": latest['SMA_20'] > latest['SMA_50'],
                "rsi": float(latest['RSI']) if not pd.isna(latest['RSI']) else 50.0,
                "rsi_signal": "oversold" if latest['RSI'] < 30 else "overbought" if latest['RSI'] > 70 else "neutral",
                "macd_signal": "bullish" if latest['MACD'] > latest['MACD_Signal'] else "bearish",
                "bb_position": "upper" if latest['Close'] > latest['BB_Upper'] else "lower" if latest['Close'] < latest['BB_Lower'] else "middle",
                "volume_trend": "high" if latest['Volume'] > volume_avg else "normal"
            }
        except Exception as e:
            logger.error(f"Error calculating signals: {str(e)}")
            signals = {
                "sma_trend": "neutral",
                "golden_cross": False,
                "rsi": 50.0,
                "rsi_signal": "neutral",
                "macd_signal": "neutral",
                "bb_position": "middle",
                "volume_trend": "normal"
            }
        
        return signals
    
    def _make_trading_decision(self, signals: Dict) -> Dict:
        """Make trading decision based on signals"""
        bullish_signals = 0
        bearish_signals = 0
        reasoning = []
        
        # Evaluate signals
        if signals["sma_trend"] == "bullish":
            bullish_signals += 1
            reasoning.append("Price above 20-day SMA")
        else:
            bearish_signals += 1
            reasoning.append("Price below 20-day SMA")
            
        if signals["golden_cross"]:
            bullish_signals += 1
            reasoning.append("Golden cross pattern")
            
        if signals["rsi_signal"] == "oversold":
            bullish_signals += 1
            reasoning.append("RSI indicates oversold condition")
        elif signals["rsi_signal"] == "overbought":
            bearish_signals += 1
            reasoning.append("RSI indicates overbought condition")
            
        if signals["macd_signal"] == "bullish":
            bullish_signals += 1
            reasoning.append("MACD bullish crossover")
        else:
            bearish_signals += 1
            reasoning.append("MACD bearish signal")
            
        if signals["volume_trend"] == "high":
            bullish_signals += 0.5
            reasoning.append("Above average volume")
            
        # Calculate confidence and make decision
        total_signals = bullish_signals + bearish_signals
        confidence = abs(bullish_signals - bearish_signals) / total_signals if total_signals > 0 else 0
        
        if bullish_signals > bearish_signals and confidence >= self.confidence_threshold:
            action = "BUY"
        elif bearish_signals > bullish_signals and confidence >= self.confidence_threshold:
            action = "SELL"
        else:
            action = "HOLD"
            
        return {
            "action": action,
            "confidence": round(confidence, 3),
            "reasoning": reasoning,
            "bullish_signals": bullish_signals,
            "bearish_signals": bearish_signals
        }


class RiskAgent:
    """Risk management agent that evaluates portfolio risk and position sizing"""
    
    def __init__(self):
        self.name = "Risk Agent"
        self.max_position_size = 0.1  # 10% max position size
        self.max_portfolio_risk = 0.02  # 2% max portfolio risk
        
    def evaluate_risk(self, portfolio: Dict, new_position: Dict) -> Dict:
        """Evaluate risk for a new position"""
        try:
            total_value = portfolio.get('total_value', 100000)
            current_positions = portfolio.get('positions', [])
            
            # Calculate position size
            suggested_position_size = self._calculate_position_size(
                total_value, new_position.get('confidence', 0.5)
            )
            
            # Risk metrics
            risk_metrics = {
                "suggested_position_size": suggested_position_size,
                "position_risk": suggested_position_size / total_value,
                "diversification_score": self._calculate_diversification(current_positions),
                "risk_level": "LOW",
                "warnings": []
            }
            
            # Risk warnings
            if risk_metrics["position_risk"] > self.max_position_size:
                risk_metrics["warnings"].append("Position size exceeds maximum limit")
                risk_metrics["risk_level"] = "HIGH"
                
            if len(current_positions) < 5:
                risk_metrics["warnings"].append("Portfolio lacks diversification")
                
            return risk_metrics
            
        except Exception as e:
            logger.error(f"Risk Agent error: {str(e)}")
            return {"error": str(e)}
    
    def _calculate_position_size(self, total_value: float, confidence: float) -> float:
        """Calculate optimal position size based on Kelly Criterion principles"""
        base_position = total_value * 0.05  # 5% base position
        confidence_multiplier = min(confidence * 2, 2.0)  # Max 2x multiplier
        return min(base_position * confidence_multiplier, total_value * self.max_position_size)
    
    def _calculate_diversification(self, positions: List) -> float:
        """Calculate portfolio diversification score"""
        if not positions:
            return 0.0
        
        # Simple diversification score based on number of positions
        num_positions = len(positions)
        if num_positions >= 10:
            return 1.0
        elif num_positions >= 5:
            return 0.7
        elif num_positions >= 3:
            return 0.5
        else:
            return 0.3


class SentimentAgent:
    """Sentiment analysis agent that analyzes market sentiment and news"""
    
    def __init__(self):
        self.name = "Sentiment Agent"
        self.alpha_vantage_key = os.environ.get('ALPHA_VANTAGE_API_KEY')
        
    def analyze_sentiment(self, symbol: str) -> Dict:
        """Analyze market sentiment for a symbol"""
        try:
            sentiment_data = {
                "symbol": symbol,
                "overall_sentiment": "NEUTRAL",
                "sentiment_score": 0.0,
                "news_sentiment": "NEUTRAL",
                "social_sentiment": "NEUTRAL",
                "confidence": 0.5,
                "factors": []
            }
            
            # Try to get news sentiment from Alpha Vantage
            if self.alpha_vantage_key:
                news_sentiment = self._get_news_sentiment(symbol)
                sentiment_data.update(news_sentiment)
            
            # Market sentiment indicators
            market_sentiment = self._analyze_market_sentiment(symbol)
            sentiment_data.update(market_sentiment)
            
            return sentiment_data
            
        except Exception as e:
            logger.error(f"Sentiment Agent error for {symbol}: {str(e)}")
            return {"error": str(e)}
    
    def _get_news_sentiment(self, symbol: str) -> Dict:
        """Get news sentiment from Alpha Vantage"""
        try:
            url = f"https://www.alphavantage.co/query"
            params = {
                'function': 'NEWS_SENTIMENT',
                'tickers': symbol,
                'apikey': self.alpha_vantage_key,
                'limit': 50
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if 'feed' in data:
                sentiments = []
                for item in data['feed'][:10]:  # Analyze top 10 news items
                    if 'ticker_sentiment' in item:
                        for ticker_data in item['ticker_sentiment']:
                            if ticker_data['ticker'] == symbol:
                                sentiments.append(float(ticker_data['ticker_sentiment_score']))
                
                if sentiments:
                    avg_sentiment = np.mean(sentiments)
                    sentiment_label = "POSITIVE" if avg_sentiment > 0.1 else "NEGATIVE" if avg_sentiment < -0.1 else "NEUTRAL"
                    
                    return {
                        "news_sentiment": sentiment_label,
                        "sentiment_score": round(avg_sentiment, 3),
                        "news_count": len(sentiments),
                        "factors": [f"News sentiment: {sentiment_label}"]
                    }
            
            return {"news_sentiment": "NEUTRAL", "sentiment_score": 0.0}
            
        except Exception as e:
            logger.error(f"News sentiment error: {str(e)}")
            return {"news_sentiment": "NEUTRAL", "sentiment_score": 0.0}
    
    def _analyze_market_sentiment(self, symbol: str) -> Dict:
        """Analyze overall market sentiment indicators"""
        try:
            # Get stock data for sentiment analysis
            stock = yf.Ticker(symbol)
            data = stock.history(period="1mo")
            
            if data.empty:
                return {"overall_sentiment": "NEUTRAL"}
            
            # Price momentum
            price_change = (data['Close'].iloc[-1] - data['Close'].iloc[0]) / data['Close'].iloc[0]
            
            # Volume analysis
            avg_volume = data['Volume'].mean()
            recent_volume = data['Volume'].iloc[-5:].mean()
            volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1
            
            factors = []
            sentiment_score = 0
            
            if price_change > 0.05:
                sentiment_score += 0.3
                factors.append("Strong positive price momentum")
            elif price_change < -0.05:
                sentiment_score -= 0.3
                factors.append("Negative price momentum")
                
            if volume_ratio > 1.2:
                sentiment_score += 0.2
                factors.append("Higher than average volume")
                
            # Determine overall sentiment
            if sentiment_score > 0.2:
                overall_sentiment = "POSITIVE"
            elif sentiment_score < -0.2:
                overall_sentiment = "NEGATIVE"
            else:
                overall_sentiment = "NEUTRAL"
                
            return {
                "overall_sentiment": overall_sentiment,
                "market_momentum": round(price_change * 100, 2),
                "volume_ratio": round(volume_ratio, 2),
                "factors": factors
            }
            
        except Exception as e:
            logger.error(f"Market sentiment error: {str(e)}")
            return {"overall_sentiment": "NEUTRAL"}


class PortfolioAgent:
    """Portfolio optimization agent that manages asset allocation and rebalancing"""
    
    def __init__(self):
        self.name = "Portfolio Agent"
        self.target_allocations = {
            "large_cap": 0.4,
            "mid_cap": 0.3,
            "small_cap": 0.2,
            "international": 0.1
        }
        
    def optimize_portfolio(self, current_portfolio: Dict, market_conditions: Dict) -> Dict:
        """Optimize portfolio allocation based on current conditions"""
        try:
            recommendations = {
                "rebalance_needed": False,
                "suggested_actions": [],
                "risk_adjusted_allocation": self.target_allocations.copy(),
                "efficiency_score": 0.8,
                "diversification_gaps": []
            }
            
            # Analyze current allocation
            current_allocation = self._analyze_current_allocation(current_portfolio)
            
            # Check for rebalancing needs
            rebalance_needed = self._check_rebalancing_needs(current_allocation)
            recommendations["rebalance_needed"] = rebalance_needed
            
            if rebalance_needed:
                recommendations["suggested_actions"] = self._generate_rebalancing_actions(
                    current_allocation, market_conditions
                )
                
            return recommendations
            
        except Exception as e:
            logger.error(f"Portfolio Agent error: {str(e)}")
            return {"error": str(e)}
    
    def _analyze_current_allocation(self, portfolio: Dict) -> Dict:
        """Analyze current portfolio allocation"""
        positions = portfolio.get('positions', [])
        total_value = portfolio.get('total_value', 0)
        
        allocation = {
            "large_cap": 0,
            "mid_cap": 0,
            "small_cap": 0,
            "international": 0
        }
        
        # Categorize positions (simplified categorization)
        for position in positions:
            symbol = position.get('symbol', '')
            value = position.get('value', 0)
            weight = value / total_value if total_value > 0 else 0
            
            # Simple categorization logic (in real implementation, use market cap data)
            if symbol in ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']:
                allocation["large_cap"] += weight
            elif symbol.endswith('.NS'):  # NSE stocks as international
                allocation["international"] += weight
            else:
                allocation["mid_cap"] += weight
                
        return allocation
    
    def _check_rebalancing_needs(self, current_allocation: Dict) -> bool:
        """Check if portfolio needs rebalancing"""
        threshold = 0.05  # 5% threshold
        
        for category, target in self.target_allocations.items():
            current = current_allocation.get(category, 0)
            if abs(current - target) > threshold:
                return True
                
        return False
    
    def _generate_rebalancing_actions(self, current_allocation: Dict, market_conditions: Dict) -> List[str]:
        """Generate specific rebalancing actions"""
        actions = []
        
        for category, target in self.target_allocations.items():
            current = current_allocation.get(category, 0)
            difference = target - current
            
            if abs(difference) > 0.05:  # 5% threshold
                if difference > 0:
                    actions.append(f"Increase {category} allocation by {difference:.1%}")
                else:
                    actions.append(f"Reduce {category} allocation by {abs(difference):.1%}")
                    
        return actions


class AgenticAICoordinator:
    """Main coordinator that orchestrates all AI agents"""
    
    def __init__(self):
        self.trading_agent = TradingAgent()
        self.risk_agent = RiskAgent()
        self.sentiment_agent = SentimentAgent()
        self.portfolio_agent = PortfolioAgent()
        
    def analyze_stock_comprehensive(self, symbol: str, portfolio: Dict = None) -> Dict:
        """Comprehensive stock analysis using all agents"""
        try:
            results = {
                "symbol": symbol,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agents_analysis": {}
            }
            
            # Trading Agent Analysis
            trading_analysis = self.trading_agent.analyze_stock(symbol)
            results["agents_analysis"]["trading"] = trading_analysis
            
            # Sentiment Agent Analysis
            sentiment_analysis = self.sentiment_agent.analyze_sentiment(symbol)
            results["agents_analysis"]["sentiment"] = sentiment_analysis
            
            # Risk Agent Analysis (if portfolio provided)
            if portfolio:
                risk_analysis = self.risk_agent.evaluate_risk(portfolio, trading_analysis)
                results["agents_analysis"]["risk"] = risk_analysis
            else:
                results["agents_analysis"]["risk"] = {"risk_level": "MEDIUM"}
            
            # Final Recommendation
            final_recommendation = self._synthesize_recommendations(results["agents_analysis"])
            results["final_recommendation"] = final_recommendation
            
            return results
            
        except Exception as e:
            logger.error(f"Agentic AI Coordinator error: {str(e)}")
            return {"error": str(e)}
    
    def optimize_portfolio_comprehensive(self, portfolio: Dict) -> Dict:
        """Comprehensive portfolio optimization"""
        try:
            market_conditions = {"volatility": "MEDIUM", "trend": "NEUTRAL"}  # Simplified
            
            portfolio_optimization = self.portfolio_agent.optimize_portfolio(
                portfolio, market_conditions
            )
            
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "optimization": portfolio_optimization,
                "market_conditions": market_conditions
            }
            
        except Exception as e:
            logger.error(f"Portfolio optimization error: {str(e)}")
            return {"error": str(e)}
    
    def _synthesize_recommendations(self, agents_analysis: Dict) -> Dict:
        """Synthesize recommendations from all agents"""
        trading = agents_analysis.get("trading", {})
        sentiment = agents_analysis.get("sentiment", {})
        risk = agents_analysis.get("risk", {})
        
        # Weight different factors
        trading_weight = 0.4
        sentiment_weight = 0.3
        risk_weight = 0.3
        
        # Calculate overall confidence
        trading_confidence = trading.get("confidence", 0.5)
        sentiment_confidence = sentiment.get("confidence", 0.5)
        
        overall_confidence = (
            trading_confidence * trading_weight +
            sentiment_confidence * sentiment_weight
        )
        
        # Risk adjustment
        risk_level = risk.get("risk_level", "MEDIUM")
        if risk_level == "HIGH":
            overall_confidence *= 0.7
            
        # Final recommendation
        trading_action = trading.get("recommendation", "HOLD")
        sentiment_trend = sentiment.get("overall_sentiment", "NEUTRAL")
        
        if trading_action == "BUY" and sentiment_trend in ["POSITIVE", "NEUTRAL"] and overall_confidence > 0.6:
            final_action = "STRONG_BUY"
        elif trading_action == "BUY":
            final_action = "BUY"
        elif trading_action == "SELL" and sentiment_trend in ["NEGATIVE", "NEUTRAL"] and overall_confidence > 0.6:
            final_action = "STRONG_SELL"
        elif trading_action == "SELL":
            final_action = "SELL"
        else:
            final_action = "HOLD"
            
        return {
            "action": final_action,
            "confidence": round(overall_confidence, 3),
            "reasoning": f"Trading: {trading_action}, Sentiment: {sentiment_trend}, Risk: {risk_level}",
            "risk_level": risk_level
        }