"""
LangGraph AI Research Pipeline for Capulse
Implements comprehensive sentiment analysis with weighted scoring:
- Qualitative Sentiments (15%): News, social media, annual reports
- Quantitative Sentiments (50%): Technical indicators (RSI, SuperTrend, EMA)
- Search Sentiments (10%): Google Trends, Perplexity search
- Trend Analysis (25%): Open Interest, PCR, VIX
"""

import os
import logging
import hashlib
from typing import Dict, List, TypedDict, Annotated, Optional
from datetime import datetime, timedelta
from decimal import Decimal
import operator
import json

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from services.perplexity_service import PerplexityService
from services.market_data_service import MarketDataService

logger = logging.getLogger(__name__)


class ResearchState(TypedDict):
    """State for the AI research pipeline"""
    messages: Annotated[List, operator.add]
    
    # Input parameters
    asset_type: str
    symbol: str
    asset_name: str
    date_range_start: str
    date_range_end: str
    user_id: int
    tenant_id: str
    
    # Configuration
    weight_config: Dict
    threshold_config: Dict
    
    # Component results
    qualitative_result: Dict
    quantitative_result: Dict
    search_result: Dict
    trend_result: Dict
    
    # Aggregated results
    component_scores: List[Dict]
    overall_score: float
    confidence: float
    recommendation: str
    recommendation_summary: str
    
    # Evidence collected
    evidence: List[Dict]
    
    # Pipeline status
    pipeline_stage: str
    cache_hit: bool
    cached_result: Optional[Dict]
    run_id: Optional[int]
    error: Optional[str]


class LangGraphAIResearchPipeline:
    """
    AI Research Pipeline with multi-component sentiment analysis.
    
    Pipeline stages:
    1. prepare_context - Load config, check cache
    2. qualitative_sentiment - Analyze news, social media, reports (15%)
    3. quantitative_sentiment - Calculate RSI, SuperTrend, EMA (50%)
    4. search_sentiment - Google Trends, Perplexity search (10%)
    5. trend_analysis - OI, PCR, VIX (25%)
    6. aggregate_scores - Apply weights, compute overall score
    7. generate_recommendation - Create recommendation based on thresholds
    8. persist_results - Save to database
    """
    
    def __init__(self):
        self._llm = None
        self._perplexity_service = None
        self._market_service = None
        self._graph = None
    
    @property
    def llm(self):
        """Lazy-load the LLM"""
        if self._llm is None:
            api_key = os.environ.get("OPENAI_API_KEY", "")
            self._llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.2,
                api_key=api_key
            )
        return self._llm
    
    @property
    def perplexity_service(self):
        """Lazy-load perplexity service"""
        if self._perplexity_service is None:
            self._perplexity_service = PerplexityService()
        return self._perplexity_service
    
    @property
    def market_service(self):
        """Lazy-load market service"""
        if self._market_service is None:
            self._market_service = MarketDataService()
        return self._market_service
    
    @property
    def graph(self):
        """Lazy-load the graph"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph
    
    def _build_graph(self):
        """Build the research pipeline state machine"""
        workflow = StateGraph(ResearchState)
        
        # Add pipeline stages
        workflow.add_node("prepare_context", self.prepare_context)
        workflow.add_node("qualitative_sentiment", self.analyze_qualitative_sentiment)
        workflow.add_node("quantitative_sentiment", self.analyze_quantitative_sentiment)
        workflow.add_node("search_sentiment", self.analyze_search_sentiment)
        workflow.add_node("trend_analysis", self.analyze_trend)
        workflow.add_node("aggregate_scores", self.aggregate_scores)
        workflow.add_node("generate_recommendation", self.generate_recommendation)
        workflow.add_node("persist_results", self.persist_results)
        
        # Define the flow
        workflow.set_entry_point("prepare_context")
        
        # Check cache first, skip to end if cache hit
        workflow.add_conditional_edges(
            "prepare_context",
            self.check_cache,
            {
                "cache_hit": "persist_results",
                "continue": "qualitative_sentiment"
            }
        )
        
        # Sequential sentiment analysis
        workflow.add_edge("qualitative_sentiment", "quantitative_sentiment")
        workflow.add_edge("quantitative_sentiment", "search_sentiment")
        workflow.add_edge("search_sentiment", "trend_analysis")
        workflow.add_edge("trend_analysis", "aggregate_scores")
        workflow.add_edge("aggregate_scores", "generate_recommendation")
        workflow.add_edge("generate_recommendation", "persist_results")
        workflow.add_edge("persist_results", END)
        
        return workflow.compile()
    
    def check_cache(self, state: ResearchState) -> str:
        """Conditional check for cache hit"""
        if state.get("cache_hit"):
            return "cache_hit"
        return "continue"
    
    def prepare_context(self, state: ResearchState) -> Dict:
        """Stage 1: Load configuration and check cache"""
        logger.info(f"Stage 1: Preparing context for {state.get('symbol', 'unknown')}")
        
        from app import db
        from models import ResearchWeightConfig, ResearchThresholdConfig, ResearchCache, ResearchRun
        
        tenant_id = state.get("tenant_id", "live")
        symbol = state.get("symbol", "")
        asset_type = state.get("asset_type", "stocks")
        
        # Load weight configuration
        weight_config = ResearchWeightConfig.get_active_config(tenant_id)
        if not weight_config:
            # Create default config
            weight_config = {
                "qualitative_pct": 15,
                "quantitative_pct": 50,
                "search_pct": 10,
                "trend_pct": 25,
                "tech_params": {
                    "rsi_period": 14,
                    "rsi_overbought": 70,
                    "rsi_oversold": 30,
                    "ema_short": 9,
                    "ema_long": 20
                }
            }
        else:
            weight_config = {
                "qualitative_pct": weight_config.qualitative_pct,
                "quantitative_pct": weight_config.quantitative_pct,
                "search_pct": weight_config.search_pct,
                "trend_pct": weight_config.trend_pct,
                "tech_params": weight_config.tech_params or {},
                "qualitative_sources": weight_config.qualitative_sources or {}
            }
        
        # Load threshold configuration
        threshold_config = ResearchThresholdConfig.get_active_config(tenant_id)
        if not threshold_config:
            threshold_config = {
                "strong_buy_threshold": 70,
                "buy_threshold": 56,
                "hold_low": 40,
                "hold_high": 55,
                "sell_threshold": 28,
                "min_confidence": 0.38
            }
        else:
            threshold_config = {
                "strong_buy_threshold": threshold_config.strong_buy_threshold,
                "buy_threshold": threshold_config.buy_threshold,
                "hold_low": threshold_config.hold_low,
                "hold_high": threshold_config.hold_high,
                "sell_threshold": threshold_config.sell_threshold,
                "min_confidence": float(threshold_config.min_confidence)
            }
        
        # Generate cache key
        cache_key = hashlib.md5(
            f"{tenant_id}:{asset_type}:{symbol}:{state.get('date_range_end', '')}".encode()
        ).hexdigest()
        
        # Check cache
        cached = ResearchCache.get_valid_cache(cache_key, tenant_id)
        if cached:
            logger.info(f"Cache hit for {symbol}")
            cached_payload = cached.result_payload or {}
            return {
                "cache_hit": True,
                "cached_result": cached_payload,
                "overall_score": float(cached.overall_score) if cached.overall_score else cached_payload.get("overall_score", 0),
                "confidence": cached_payload.get("confidence", 0),
                "recommendation": cached.recommendation or cached_payload.get("recommendation", ""),
                "recommendation_summary": cached_payload.get("recommendation_summary", ""),
                "component_scores": cached_payload.get("component_scores", []),
                "weight_config": weight_config,
                "threshold_config": threshold_config,
                "pipeline_stage": "cache_hit",
                "messages": [AIMessage(content=f"Retrieved cached analysis for {symbol}")]
            }
        
        # Create research run record
        research_run = ResearchRun(
            tenant_id=tenant_id,
            user_id=state.get("user_id"),
            asset_type=asset_type,
            symbol=symbol,
            asset_name=state.get("asset_name", symbol),
            analysis_date=datetime.utcnow().date(),
            status="processing",
            cache_key=cache_key,
            run_started_at=datetime.utcnow()
        )
        db.session.add(research_run)
        db.session.commit()
        
        return {
            "cache_hit": False,
            "weight_config": weight_config,
            "threshold_config": threshold_config,
            "run_id": research_run.id,
            "pipeline_stage": "prepare_context",
            "messages": [AIMessage(content=f"Prepared context for {symbol} analysis")]
        }
    
    def analyze_qualitative_sentiment(self, state: ResearchState) -> Dict:
        """Stage 2: Analyze qualitative sentiments (15% weight)
        Sources: Annual reports, Twitter, news, forums
        """
        logger.info(f"Stage 2: Analyzing qualitative sentiment for {state.get('symbol')}")
        
        symbol = state.get("symbol", "")
        asset_name = state.get("asset_name", symbol)
        evidence = state.get("evidence", [])
        
        # Query Perplexity for news and social sentiment
        query = f"""Analyze the sentiment for {asset_name} ({symbol}) stock in Indian market:

1. Recent news coverage and headlines (last 7 days)
2. Social media sentiment (Twitter/X discussions)
3. Analyst opinions and recommendations
4. Any corporate announcements or regulatory filings
5. Management guidance and outlook
6. Sector sentiment and peer comparison

Provide a sentiment score from -100 (very bearish) to +100 (very bullish) with reasoning."""
        
        try:
            response = self.perplexity_service._call_perplexity_api(query, model="sonar-pro")
            
            if response and response.get("choices"):
                content = response["choices"][0]["message"]["content"]
                citations = response.get("citations", [])
                
                # Parse sentiment from LLM response
                sentiment_score = self._extract_sentiment_score(content)
                
                # Collect evidence
                for i, citation in enumerate(citations[:5]):
                    evidence.append({
                        "source_name": f"Source {i+1}",
                        "source_type": "news",
                        "source_url": citation,
                        "snippet": f"Reference for qualitative analysis",
                        "component": "qualitative"
                    })
                
                result = {
                    "raw_score": max(0, min(100, (sentiment_score + 100) / 2)),  # Convert to 0-100
                    "sentiment_direction": "bullish" if sentiment_score > 20 else ("bearish" if sentiment_score < -20 else "neutral"),
                    "analysis": content,
                    "citations": citations,
                    "confidence": 0.75
                }
            else:
                result = {
                    "raw_score": 50,
                    "sentiment_direction": "neutral",
                    "analysis": "Unable to fetch qualitative data",
                    "confidence": 0.3
                }
                
        except Exception as e:
            logger.error(f"Qualitative analysis failed: {e}")
            result = {
                "raw_score": 50,
                "sentiment_direction": "neutral",
                "error": str(e),
                "confidence": 0.2
            }
        
        return {
            "qualitative_result": result,
            "evidence": evidence,
            "pipeline_stage": "qualitative_sentiment",
            "messages": [AIMessage(content=f"Qualitative analysis complete: {result.get('sentiment_direction', 'neutral')}")]
        }
    
    def analyze_quantitative_sentiment(self, state: ResearchState) -> Dict:
        """Stage 3: Analyze quantitative sentiments (50% weight)
        Indicators: RSI, SuperTrend, EMA 9/20
        """
        logger.info(f"Stage 3: Analyzing quantitative sentiment for {state.get('symbol')}")
        
        symbol = state.get("symbol", "")
        weight_config = state.get("weight_config", {})
        tech_params = weight_config.get("tech_params", {})
        
        try:
            # Get market data for technical analysis
            market_data = self.market_service.get_stock_data(symbol)
            
            if not market_data:
                # Fallback to Perplexity for technical analysis
                query = f"""Provide technical analysis for {symbol} on NSE:
                
1. RSI (14-period) value and interpretation
2. SuperTrend indicator status (bullish/bearish)
3. EMA 9 and EMA 20 crossover status
4. MACD signal
5. Volume analysis
6. Support and resistance levels

Provide a technical score from 0-100 (higher = more bullish)."""
                
                response = self.perplexity_service._call_perplexity_api(query, model="sonar")
                
                if response and response.get("choices"):
                    content = response["choices"][0]["message"]["content"]
                    tech_score = self._extract_technical_score(content)
                else:
                    tech_score = 50
                
                result = {
                    "raw_score": tech_score,
                    "signal": "bullish" if tech_score > 60 else ("bearish" if tech_score < 40 else "neutral"),
                    "analysis": content if response else "Technical data unavailable",
                    "indicators": {},
                    "confidence": 0.6
                }
            else:
                # Calculate actual technical indicators
                rsi = market_data.get("rsi", 50)
                ema_signal = market_data.get("ema_signal", "neutral")
                supertrend = market_data.get("supertrend_signal", "neutral")
                
                # Score calculation
                rsi_score = 100 - rsi if rsi > 70 else (rsi if rsi < 30 else 50 + (50 - rsi))
                ema_score = 70 if ema_signal == "bullish" else (30 if ema_signal == "bearish" else 50)
                st_score = 80 if supertrend == "bullish" else (20 if supertrend == "bearish" else 50)
                
                tech_score = (rsi_score * 0.4 + ema_score * 0.3 + st_score * 0.3)
                
                result = {
                    "raw_score": tech_score,
                    "signal": "bullish" if tech_score > 60 else ("bearish" if tech_score < 40 else "neutral"),
                    "indicators": {
                        "rsi": rsi,
                        "ema_signal": ema_signal,
                        "supertrend": supertrend,
                        "rsi_score": rsi_score,
                        "ema_score": ema_score,
                        "supertrend_score": st_score
                    },
                    "confidence": 0.85
                }
                
        except Exception as e:
            logger.error(f"Quantitative analysis failed: {e}")
            result = {
                "raw_score": 50,
                "signal": "neutral",
                "error": str(e),
                "confidence": 0.2
            }
        
        return {
            "quantitative_result": result,
            "pipeline_stage": "quantitative_sentiment",
            "messages": [AIMessage(content=f"Quantitative analysis complete: {result.get('signal', 'neutral')}")]
        }
    
    def analyze_search_sentiment(self, state: ResearchState) -> Dict:
        """Stage 4: Analyze search sentiments (10% weight)
        Sources: Google Trends, Perplexity search patterns
        """
        logger.info(f"Stage 4: Analyzing search sentiment for {state.get('symbol')}")
        
        symbol = state.get("symbol", "")
        asset_name = state.get("asset_name", symbol)
        evidence = state.get("evidence", [])
        
        query = f"""Analyze search and retail interest trends for {asset_name} ({symbol}):

1. Google Trends interest over last 30 days (rising/falling/stable)
2. Search volume compared to sector peers
3. Retail investor interest indicators
4. Social media discussion volume trends
5. Broker app search trends if available

Provide a search interest score from 0-100 (higher = more interest/bullish sentiment)."""
        
        try:
            response = self.perplexity_service._call_perplexity_api(query, model="sonar")
            
            if response and response.get("choices"):
                content = response["choices"][0]["message"]["content"]
                search_score = self._extract_search_score(content)
                
                result = {
                    "raw_score": search_score,
                    "trend": "rising" if search_score > 60 else ("falling" if search_score < 40 else "stable"),
                    "analysis": content,
                    "confidence": 0.65
                }
            else:
                result = {
                    "raw_score": 50,
                    "trend": "stable",
                    "analysis": "Search trend data unavailable",
                    "confidence": 0.3
                }
                
        except Exception as e:
            logger.error(f"Search sentiment analysis failed: {e}")
            result = {
                "raw_score": 50,
                "trend": "stable",
                "error": str(e),
                "confidence": 0.2
            }
        
        return {
            "search_result": result,
            "evidence": evidence,
            "pipeline_stage": "search_sentiment",
            "messages": [AIMessage(content=f"Search sentiment analysis complete: {result.get('trend', 'stable')}")]
        }
    
    def analyze_trend(self, state: ResearchState) -> Dict:
        """Stage 5: Analyze market trends (25% weight)
        Indicators: Open Interest, PCR, VIX
        """
        logger.info(f"Stage 5: Analyzing market trends for {state.get('symbol')}")
        
        symbol = state.get("symbol", "")
        
        query = f"""Analyze derivatives and market trend data for {symbol}:

1. Open Interest (OI) changes - bullish/bearish buildup
2. Put-Call Ratio (PCR) for the stock and index
3. India VIX level and its implication
4. FII/DII activity in the segment
5. Max Pain level for current expiry
6. Rollover data if available

Provide a trend score from 0-100 (higher = more bullish positioning)."""
        
        try:
            response = self.perplexity_service._call_perplexity_api(query, model="sonar")
            
            if response and response.get("choices"):
                content = response["choices"][0]["message"]["content"]
                trend_score = self._extract_trend_score(content)
                
                result = {
                    "raw_score": trend_score,
                    "positioning": "bullish" if trend_score > 60 else ("bearish" if trend_score < 40 else "neutral"),
                    "analysis": content,
                    "confidence": 0.7
                }
            else:
                result = {
                    "raw_score": 50,
                    "positioning": "neutral",
                    "analysis": "Trend data unavailable",
                    "confidence": 0.3
                }
                
        except Exception as e:
            logger.error(f"Trend analysis failed: {e}")
            result = {
                "raw_score": 50,
                "positioning": "neutral",
                "error": str(e),
                "confidence": 0.2
            }
        
        return {
            "trend_result": result,
            "pipeline_stage": "trend_analysis",
            "messages": [AIMessage(content=f"Trend analysis complete: {result.get('positioning', 'neutral')}")]
        }
    
    def aggregate_scores(self, state: ResearchState) -> Dict:
        """Stage 6: Aggregate weighted scores"""
        logger.info("Stage 6: Aggregating scores")
        
        weight_config = state.get("weight_config", {})
        
        qualitative = state.get("qualitative_result", {})
        quantitative = state.get("quantitative_result", {})
        search = state.get("search_result", {})
        trend = state.get("trend_result", {})
        
        # Get weights
        qual_weight = weight_config.get("qualitative_pct", 15) / 100
        quant_weight = weight_config.get("quantitative_pct", 50) / 100
        search_weight = weight_config.get("search_pct", 10) / 100
        trend_weight = weight_config.get("trend_pct", 25) / 100
        
        # Calculate weighted scores
        qual_score = qualitative.get("raw_score", 50) * qual_weight
        quant_score = quantitative.get("raw_score", 50) * quant_weight
        search_score = search.get("raw_score", 50) * search_weight
        trend_score = trend.get("raw_score", 50) * trend_weight
        
        overall_score = qual_score + quant_score + search_score + trend_score
        
        # Calculate confidence (weighted average of component confidences)
        confidence = (
            qualitative.get("confidence", 0.5) * qual_weight +
            quantitative.get("confidence", 0.5) * quant_weight +
            search.get("confidence", 0.5) * search_weight +
            trend.get("confidence", 0.5) * trend_weight
        )
        
        component_scores = [
            {
                "type": "qualitative",
                "weight_pct": int(qual_weight * 100),
                "raw_score": qualitative.get("raw_score", 50),
                "weighted_score": qual_score,
                "signal": qualitative.get("sentiment_direction", "neutral"),
                "confidence": qualitative.get("confidence", 0.5)
            },
            {
                "type": "quantitative",
                "weight_pct": int(quant_weight * 100),
                "raw_score": quantitative.get("raw_score", 50),
                "weighted_score": quant_score,
                "signal": quantitative.get("signal", "neutral"),
                "confidence": quantitative.get("confidence", 0.5)
            },
            {
                "type": "search",
                "weight_pct": int(search_weight * 100),
                "raw_score": search.get("raw_score", 50),
                "weighted_score": search_score,
                "signal": search.get("trend", "stable"),
                "confidence": search.get("confidence", 0.5)
            },
            {
                "type": "trend",
                "weight_pct": int(trend_weight * 100),
                "raw_score": trend.get("raw_score", 50),
                "weighted_score": trend_score,
                "signal": trend.get("positioning", "neutral"),
                "confidence": trend.get("confidence", 0.5)
            }
        ]
        
        return {
            "component_scores": component_scores,
            "overall_score": round(overall_score, 2),
            "confidence": round(confidence, 2),
            "pipeline_stage": "aggregate_scores",
            "messages": [AIMessage(content=f"Aggregated score: {round(overall_score, 2)}/100")]
        }
    
    def generate_recommendation(self, state: ResearchState) -> Dict:
        """Stage 7: Generate recommendation based on thresholds"""
        logger.info("Stage 7: Generating recommendation")
        
        overall_score = state.get("overall_score", 50)
        confidence = state.get("confidence", 0.5)
        threshold_config = state.get("threshold_config", {})
        symbol = state.get("symbol", "")
        
        # Determine recommendation
        min_confidence = threshold_config.get("min_confidence", 0.6)
        
        if confidence < min_confidence:
            recommendation = "inconclusive"
            summary = f"Analysis for {symbol} shows insufficient confidence ({confidence:.0%}) to make a clear recommendation."
        elif overall_score >= threshold_config.get("strong_buy_threshold", 70):
            recommendation = "strong_buy"
            summary = f"{symbol} shows strong bullish signals across multiple indicators with a score of {overall_score:.1f}/100. Recommend aggressive accumulation."
        elif overall_score >= threshold_config.get("buy_threshold", 56):
            recommendation = "buy"
            summary = f"{symbol} demonstrates positive momentum with a score of {overall_score:.1f}/100. Consider adding to portfolio."
        elif overall_score >= threshold_config.get("hold_low", 40):
            recommendation = "hold"
            summary = f"{symbol} shows mixed signals with a score of {overall_score:.1f}/100. Maintain existing positions."
        elif overall_score >= threshold_config.get("sell_threshold", 28):
            recommendation = "cautionary_sell"
            summary = f"{symbol} displays weakening signals with a score of {overall_score:.1f}/100. Consider reducing exposure."
        else:
            recommendation = "strong_sell"
            summary = f"{symbol} shows significant bearish signals with a score of {overall_score:.1f}/100. Recommend exiting positions."
        
        return {
            "recommendation": recommendation,
            "recommendation_summary": summary,
            "pipeline_stage": "generate_recommendation",
            "messages": [AIMessage(content=f"Recommendation: {recommendation.upper()}")]
        }
    
    def persist_results(self, state: ResearchState) -> Dict:
        """Stage 8: Persist results to database"""
        logger.info("Stage 8: Persisting results")
        
        from app import db
        from models import (
            ResearchRun, ResearchSignalComponent, ResearchEvidence, ResearchCache
        )
        
        run_id = state.get("run_id")
        
        # If cache hit, just return cached results
        if state.get("cache_hit"):
            cached_result = state.get("cached_result", {})
            return {
                "pipeline_stage": "completed",
                "messages": [AIMessage(content="Returned cached analysis results")]
            }
        
        try:
            # Update research run
            if run_id:
                research_run = ResearchRun.query.get(run_id)
                if research_run:
                    research_run.overall_score = Decimal(str(state.get("overall_score", 0)))
                    research_run.confidence = Decimal(str(state.get("confidence", 0)))
                    research_run.recommendation = state.get("recommendation")
                    research_run.recommendation_summary = state.get("recommendation_summary")
                    research_run.status = "completed"
                    research_run.run_completed_at = datetime.utcnow()
                    
                    # Add component scores
                    for comp in state.get("component_scores", []):
                        component = ResearchSignalComponent(
                            run_id=run_id,
                            component_type=comp["type"],
                            weight_pct=comp["weight_pct"],
                            raw_score=Decimal(str(comp["raw_score"])),
                            weighted_score=Decimal(str(comp["weighted_score"])),
                            confidence=Decimal(str(comp["confidence"])),
                            signal=comp["signal"]
                        )
                        db.session.add(component)
                    
                    db.session.commit()
            
            # Cache the result (upsert to avoid unique constraint violations)
            cache_key = hashlib.md5(
                f"{state.get('tenant_id', 'live')}:{state.get('asset_type')}:{state.get('symbol')}:{state.get('date_range_end', '')}".encode()
            ).hexdigest()
            
            result_payload = {
                "overall_score": state.get("overall_score"),
                "recommendation": state.get("recommendation"),
                "recommendation_summary": state.get("recommendation_summary"),
                "component_scores": state.get("component_scores"),
                "confidence": state.get("confidence")
            }
            
            # Check for existing cache entry and update, or create new
            existing_cache = ResearchCache.query.filter_by(cache_key=cache_key).first()
            if existing_cache:
                existing_cache.result_payload = result_payload
                existing_cache.overall_score = Decimal(str(state.get("overall_score", 0)))
                existing_cache.recommendation = state.get("recommendation")
                existing_cache.computed_at = datetime.utcnow()
                existing_cache.expires_at = datetime.utcnow() + timedelta(hours=24)
                existing_cache.is_valid = True
            else:
                cache_entry = ResearchCache(
                    tenant_id=state.get("tenant_id", "live"),
                    cache_key=cache_key,
                    asset_type=state.get("asset_type"),
                    symbol=state.get("symbol"),
                    analysis_date=datetime.utcnow().date(),
                    result_payload=result_payload,
                    overall_score=Decimal(str(state.get("overall_score", 0))),
                    recommendation=state.get("recommendation"),
                    computed_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(hours=24)
                )
                db.session.add(cache_entry)
            db.session.commit()
            
            logger.info(f"Results persisted for run_id={run_id}")
            
        except Exception as e:
            logger.error(f"Failed to persist results: {e}")
        
        return {
            "pipeline_stage": "completed",
            "messages": [AIMessage(content="Analysis completed and saved")]
        }
    
    def _extract_sentiment_score(self, content: str) -> float:
        """Extract sentiment score from LLM response"""
        import re
        
        # Look for patterns like "score: 65" or "sentiment: +45"
        patterns = [
            r'score[:\s]+([+-]?\d+)',
            r'sentiment[:\s]+([+-]?\d+)',
            r'rating[:\s]+([+-]?\d+)',
            r'([+-]?\d+)\s*/\s*100',
            r'([+-]?\d+)%'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        
        # Fallback: Use LLM to extract
        return 0  # Neutral
    
    def _extract_technical_score(self, content: str) -> float:
        """Extract technical score from analysis"""
        import re
        
        patterns = [
            r'technical score[:\s]+(\d+)',
            r'score[:\s]+(\d+)',
            r'(\d+)\s*/\s*100',
            r'(\d+)%'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    score = float(match.group(1))
                    return min(100, max(0, score))
                except ValueError:
                    continue
        
        # Sentiment-based fallback
        bullish_keywords = ['bullish', 'buy', 'uptrend', 'breakout', 'accumulate']
        bearish_keywords = ['bearish', 'sell', 'downtrend', 'breakdown', 'distribute']
        
        content_lower = content.lower()
        bullish_count = sum(1 for k in bullish_keywords if k in content_lower)
        bearish_count = sum(1 for k in bearish_keywords if k in content_lower)
        
        if bullish_count > bearish_count:
            return 65
        elif bearish_count > bullish_count:
            return 35
        return 50
    
    def _extract_search_score(self, content: str) -> float:
        """Extract search trend score"""
        return self._extract_technical_score(content)  # Same logic
    
    def _extract_trend_score(self, content: str) -> float:
        """Extract trend analysis score"""
        return self._extract_technical_score(content)  # Same logic
    
    async def run_analysis(
        self,
        symbol: str,
        asset_type: str = "stocks",
        asset_name: str = None,
        date_range_start: str = None,
        date_range_end: str = None,
        user_id: int = None,
        tenant_id: str = "live"
    ) -> Dict:
        """Run the complete analysis pipeline"""
        
        initial_state = {
            "messages": [],
            "asset_type": asset_type,
            "symbol": symbol,
            "asset_name": asset_name or symbol,
            "date_range_start": date_range_start or (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
            "date_range_end": date_range_end or datetime.utcnow().strftime("%Y-%m-%d"),
            "user_id": user_id,
            "tenant_id": tenant_id,
            "weight_config": {},
            "threshold_config": {},
            "qualitative_result": {},
            "quantitative_result": {},
            "search_result": {},
            "trend_result": {},
            "component_scores": [],
            "overall_score": 0,
            "confidence": 0,
            "recommendation": "",
            "recommendation_summary": "",
            "evidence": [],
            "pipeline_stage": "init",
            "cache_hit": False,
            "cached_result": None,
            "run_id": None,
            "error": None
        }
        
        try:
            result = self.graph.invoke(initial_state)
            
            return {
                "success": True,
                "symbol": symbol,
                "overall_score": result.get("overall_score"),
                "confidence": result.get("confidence"),
                "recommendation": result.get("recommendation"),
                "recommendation_summary": result.get("recommendation_summary"),
                "component_scores": result.get("component_scores"),
                "run_id": result.get("run_id"),
                "cache_hit": result.get("cache_hit", False)
            }
            
        except Exception as e:
            logger.error(f"Analysis pipeline failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "symbol": symbol
            }


# Create singleton instance
ai_research_pipeline = LangGraphAIResearchPipeline()
