"""
LangGraph I-Score Engine for Target Capital
Implements the Intelli Score (I-Score) research workflow with multi-step analysis

I-Score Components (Stocks — 6-component model, weights sum to 100%):
- Quantitative   (30%): Real technical indicators (RSI Wilder, EMA, SuperTrend, momentum)
- Trend          (20%): Open Interest, PCR, VIX, derivative trend signals
- Risk           (20%): Volatility, max drawdown, beta vs Nifty
- Qualitative    (15%): News, social media, annual reports
- Search         (10%): Google Trends, Perplexity search popularity
- Market Context  (5%): Nifty regime, VIX regime, PCR regime

Non-stock assets (MF, bond, commodity, currency, options, futures) use a
4-component model: Qualitative 15% / Quantitative 50% / Search 10% / Trend 25%.
"""

import os
import logging
import hashlib
from typing import Dict, List, TypedDict, Annotated, Optional, Any
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
import operator
import json
import pandas as pd
import math

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from app import db

logger = logging.getLogger(__name__)


class IScoreState(TypedDict):
    """State for the I-Score analysis workflow"""
    asset_type: str
    symbol: str
    asset_name: str
    user_id: int
    
    current_price: float
    previous_close: float
    price_change_pct: float
    market_status: str
    data_timestamp: str
    
    cache_hit: bool
    cached_result: Optional[Dict]
    
    qualitative_score: float
    qualitative_details: Dict
    qualitative_confidence: float
    qualitative_sources: List[Dict]
    qualitative_reasoning: str
    
    quantitative_score: float
    quantitative_details: Dict
    quantitative_confidence: float
    quantitative_sources: List[Dict]
    quantitative_reasoning: str
    
    search_score: float
    search_details: Dict
    search_confidence: float
    search_sources: List[Dict]
    search_reasoning: str
    
    trend_score: float
    trend_details: Dict
    trend_confidence: float
    trend_sources: List[Dict]
    trend_reasoning: str
    
    risk_score: float
    risk_details: Dict
    risk_confidence: float
    
    market_context_score: float
    market_context_details: Dict
    market_context_confidence: float
    
    raw_indicators: Dict
    
    overall_score: float
    overall_confidence: float
    recommendation: str
    recommendation_summary: str
    
    penalty_applied: float
    penalty_reasons: List[str]
    confidence_level: str
    score_factors: List[Dict]
    
    config: Dict
    evidence: List[Dict]
    audit_trail: List[Dict]
    error: Optional[str]
    step: str
    hist_data_source: str


class LangGraphIScoreEngine:
    """
    LangGraph-powered I-Score engine.

    Stocks workflow (9 nodes, 6-component model):
      1. Cache Check          - Skip recompute if today's result is cached
      2. Qualitative Analysis - News, social, annual reports (15%)
      3. Quantitative Analysis- RSI, SuperTrend, EMA, momentum (30%)
      4. Search Sentiment     - Perplexity, Google Trends (10%)
      5. Trend Analysis       - Open Interest, PCR, VIX (20%)
      6. Risk & Volatility    - Volatility, max drawdown, beta (20%)
      7. Market Context       - Nifty regime, VIX regime, PCR regime (5%)
      8. Aggregate Scores     - Weighted sum + nonlinear penalties + confidence
      9. Store Results        - Persist to ResearchRun, components, and cache

    Non-stock assets (MF, bond, commodity, currency, options, futures) use a
    shorter 4-component pipeline (qualitative, quantitative, search, trend) and
    skip the risk + market_context nodes.
    """
    
    def __init__(self):
        self._llm = None
        self._graph = None
    
    @property
    def llm(self):
        """Lazy-load the LLM to avoid startup failures when API key is not set"""
        if self._llm is None:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                logger.warning("OPENAI_API_KEY not set - LLM features will be limited")
            self._llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.1,
                api_key=api_key
            )
        return self._llm
    
    @property
    def graph(self):
        """Lazy-load the graph"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph
    
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow for I-Score calculation"""
        workflow = StateGraph(IScoreState)
        
        workflow.add_node("check_cache", self.check_cache)
        workflow.add_node("route_asset_type", self.route_asset_type)
        workflow.add_node("qualitative_analysis", self.qualitative_analysis)
        workflow.add_node("quantitative_analysis", self.quantitative_analysis)
        workflow.add_node("search_sentiment", self.search_sentiment)
        workflow.add_node("trend_analysis", self.trend_analysis)
        workflow.add_node("qualitative_analysis_mf", self.qualitative_analysis_mf)
        workflow.add_node("quantitative_analysis_mf", self.quantitative_analysis_mf)
        workflow.add_node("search_sentiment_mf", self.search_sentiment)
        workflow.add_node("trend_analysis_mf", self.trend_analysis_mf)
        workflow.add_node("qualitative_analysis_bond", self.qualitative_analysis_bond)
        workflow.add_node("quantitative_analysis_bond", self.quantitative_analysis_bond)
        workflow.add_node("search_sentiment_bond", self.search_sentiment_bond)
        workflow.add_node("trend_analysis_bond", self.trend_analysis_bond)
        workflow.add_node("qualitative_analysis_commodity", self.qualitative_analysis_commodity)
        workflow.add_node("quantitative_analysis_commodity", self.quantitative_analysis_commodity)
        workflow.add_node("search_sentiment_commodity", self.search_sentiment_commodity)
        workflow.add_node("trend_analysis_commodity", self.trend_analysis_commodity)
        workflow.add_node("qualitative_analysis_currency", self.qualitative_analysis_currency)
        workflow.add_node("quantitative_analysis_currency", self.quantitative_analysis_currency)
        workflow.add_node("search_sentiment_currency", self.search_sentiment_currency)
        workflow.add_node("trend_analysis_currency", self.trend_analysis_currency)
        workflow.add_node("qualitative_analysis_options", self.qualitative_analysis_options)
        workflow.add_node("quantitative_analysis_options", self.quantitative_analysis_options)
        workflow.add_node("search_sentiment_options", self.search_sentiment_options)
        workflow.add_node("trend_analysis_options", self.trend_analysis_options)
        workflow.add_node("qualitative_analysis_futures", self.qualitative_analysis_futures)
        workflow.add_node("quantitative_analysis_futures", self.quantitative_analysis_futures)
        workflow.add_node("search_sentiment_futures", self.search_sentiment_futures)
        workflow.add_node("trend_analysis_futures", self.trend_analysis_futures)
        workflow.add_node("risk_volatility", self.risk_volatility)
        workflow.add_node("market_context", self.market_context)
        workflow.add_node("aggregate_scores", self.aggregate_scores)
        workflow.add_node("store_results", self.store_results)
        
        workflow.set_entry_point("check_cache")
        
        workflow.add_conditional_edges(
            "check_cache",
            self._should_compute,
            {
                "cached": "store_results",
                "compute": "route_asset_type"
            }
        )
        
        workflow.add_conditional_edges(
            "route_asset_type",
            self._get_asset_route,
            {
                "mutual_funds": "qualitative_analysis_mf",
                "bonds": "qualitative_analysis_bond",
                "commodities": "qualitative_analysis_commodity",
                "currency": "qualitative_analysis_currency",
                "options": "qualitative_analysis_options",
                "futures": "qualitative_analysis_futures",
                "stocks": "qualitative_analysis"
            }
        )
        
        workflow.add_edge("qualitative_analysis", "quantitative_analysis")
        workflow.add_edge("quantitative_analysis", "search_sentiment")
        workflow.add_edge("search_sentiment", "trend_analysis")
        workflow.add_edge("trend_analysis", "risk_volatility")
        workflow.add_edge("risk_volatility", "market_context")
        workflow.add_edge("market_context", "aggregate_scores")
        
        workflow.add_edge("qualitative_analysis_mf", "quantitative_analysis_mf")
        workflow.add_edge("quantitative_analysis_mf", "search_sentiment_mf")
        workflow.add_edge("search_sentiment_mf", "trend_analysis_mf")
        workflow.add_edge("trend_analysis_mf", "aggregate_scores")
        
        workflow.add_edge("qualitative_analysis_bond", "quantitative_analysis_bond")
        workflow.add_edge("quantitative_analysis_bond", "search_sentiment_bond")
        workflow.add_edge("search_sentiment_bond", "trend_analysis_bond")
        workflow.add_edge("trend_analysis_bond", "aggregate_scores")
        
        workflow.add_edge("qualitative_analysis_commodity", "quantitative_analysis_commodity")
        workflow.add_edge("quantitative_analysis_commodity", "search_sentiment_commodity")
        workflow.add_edge("search_sentiment_commodity", "trend_analysis_commodity")
        workflow.add_edge("trend_analysis_commodity", "aggregate_scores")
        
        workflow.add_edge("qualitative_analysis_currency", "quantitative_analysis_currency")
        workflow.add_edge("quantitative_analysis_currency", "search_sentiment_currency")
        workflow.add_edge("search_sentiment_currency", "trend_analysis_currency")
        workflow.add_edge("trend_analysis_currency", "aggregate_scores")
        
        workflow.add_edge("qualitative_analysis_options", "quantitative_analysis_options")
        workflow.add_edge("quantitative_analysis_options", "search_sentiment_options")
        workflow.add_edge("search_sentiment_options", "trend_analysis_options")
        workflow.add_edge("trend_analysis_options", "aggregate_scores")
        
        workflow.add_edge("qualitative_analysis_futures", "quantitative_analysis_futures")
        workflow.add_edge("quantitative_analysis_futures", "search_sentiment_futures")
        workflow.add_edge("search_sentiment_futures", "trend_analysis_futures")
        workflow.add_edge("trend_analysis_futures", "aggregate_scores")
        
        workflow.add_edge("aggregate_scores", "store_results")
        workflow.add_edge("store_results", END)
        
        return workflow.compile()
    
    def _should_compute(self, state: IScoreState) -> str:
        """Decide whether to use cached results or compute new"""
        if state.get("cache_hit") and state.get("cached_result"):
            return "cached"
        return "compute"
    
    def _get_asset_route(self, state: IScoreState) -> str:
        """Route to appropriate analysis based on asset type"""
        asset_type = state.get('asset_type', 'stocks')
        if asset_type in ['mutual_funds', 'mutual-funds', 'mf']:
            return "mutual_funds"
        if asset_type in ['bonds', 'bond']:
            return "bonds"
        if asset_type in ['commodities', 'commodity']:
            return "commodities"
        if asset_type in ['currency', 'forex', 'fx']:
            return "currency"
        if asset_type in ['options', 'option']:
            return "options"
        if asset_type in ['futures', 'future']:
            return "futures"
        return "stocks"
    
    def _get_trend_route(self, state: IScoreState) -> str:
        """Route to appropriate trend analysis based on asset type"""
        asset_type = state.get('asset_type', 'stocks')
        if asset_type in ['mutual_funds', 'mutual-funds', 'mf']:
            return "mutual_funds"
        return "stocks"
    
    def route_asset_type(self, state: IScoreState) -> Dict:
        """Node to route based on asset type"""
        asset_type = state.get('asset_type', 'stocks')
        logger.info(f"I-Score Router: Routing {state['symbol']} as {asset_type}")
        return {'step': f'routing_to_{asset_type}'}
    
    def _generate_cache_key(self, asset_type: str, symbol: str, analysis_date: date) -> str:
        """Generate cache key for this analysis"""
        key_parts = f"iscore:{asset_type}:{symbol}:{analysis_date.isoformat()}"
        return hashlib.md5(key_parts.encode()).hexdigest()
    
    def _calculate_trend_confidence(self, vix_source: str, pcr_source: str, oi_source: str) -> float:
        """
        Calculate trend confidence based on data sources
        Higher confidence when all data is from live sources
        """
        confidence = 0.40
        
        if vix_source == 'NSE Live':
            confidence += 0.25
        elif 'fallback' not in vix_source.lower():
            confidence += 0.15
        
        if pcr_source == 'NSE Option Chain' or pcr_source == 'Calculated from OI':
            confidence += 0.20
        elif 'fallback' not in pcr_source.lower():
            confidence += 0.10
        
        if oi_source == 'NSE Option Chain':
            confidence += 0.15
        elif 'fallback' not in oi_source.lower():
            confidence += 0.08
        
        return min(0.95, confidence)
    
    # ── Scentric Proprietary Model Weights (IP) ──────────────────────────────
    WEIGHTS = {
        'quantitative_pct': 30,
        'trend_pct': 20,
        'risk_pct': 20,
        'qualitative_pct': 15,
        'search_pct': 10,
        'market_context_pct': 5,
    }
    THRESHOLDS = {
        'strong_buy': 78,
        'buy': 63,
        'hold_low': 42,
        'hold_high': 62,
        'sell': 28,
        'min_confidence': 0.45,
    }

    def _get_config(self) -> Dict:
        """Return hardcoded proprietary model configuration (IP)."""
        return {
            'weights': {
                **self.WEIGHTS,
                'qualitative_sources': {
                    'annual_reports': True, 'twitter': True,
                    'moneycontrol': True, 'economic_times': True,
                    'nse_india': True, 'bse_india': True,
                    'screener': True, 'glassdoor': True,
                },
            },
            'thresholds': dict(self.THRESHOLDS),
        }
    
    def check_cache(self, state: IScoreState) -> Dict:
        """Node 1: Check if valid cached result exists"""
        logger.info(f"I-Score Node 1: Checking cache for {state['symbol']}")
        
        from models import ResearchCache
        
        cache_key = self._generate_cache_key(
            state['asset_type'], 
            state['symbol'], 
            date.today()
        )
        
        cached = ResearchCache.get_valid_cache(cache_key)
        
        if cached:
            logger.info(f"Cache HIT for {state['symbol']}, using cached I-Score: {cached.overall_score}")
            payload = cached.result_payload or {}
            
            qualitative = payload.get('qualitative', {})
            quantitative = payload.get('quantitative', {})
            search = payload.get('search', {})
            trend = payload.get('trend', {})
            
            return {
                'cache_hit': True,
                'cached_result': payload,
                'overall_score': float(cached.overall_score) if cached.overall_score else 0,
                'overall_confidence': payload.get('overall_confidence', 0),
                'recommendation': cached.recommendation,
                'recommendation_summary': payload.get('recommendation_summary', ''),
                'qualitative_score': qualitative.get('score', 0),
                'qualitative_details': qualitative.get('details', {}),
                'qualitative_confidence': qualitative.get('confidence', 0),
                'quantitative_score': quantitative.get('score', 0),
                'quantitative_details': quantitative.get('details', {}),
                'quantitative_confidence': quantitative.get('confidence', 0),
                'search_score': search.get('score', 0),
                'search_details': search.get('details', {}),
                'search_confidence': search.get('confidence', 0),
                'trend_score': trend.get('score', 0),
                'trend_details': trend.get('details', {}),
                'trend_confidence': trend.get('confidence', 0),
                'config': self._get_config(),
                'step': 'cache_hit'
            }
        
        logger.info(f"Cache MISS for {state['symbol']}, computing new I-Score")
        return {
            'cache_hit': False,
            'cached_result': None,
            'config': self._get_config(),
            'evidence': [],
            'step': 'cache_miss'
        }
    
    def qualitative_analysis(self, state: IScoreState) -> Dict:
        """Node 2: Qualitative Sentiment Analysis (15% weight)"""
        logger.info(f"I-Score Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        config = state.get('config', {})
        sources = config.get('weights', {}).get('qualitative_sources', {})
        
        try:
            from services.perplexity_service import PerplexityService
            perplexity = PerplexityService()
            
            search_query = f"""Analyze the qualitative sentiment for {symbol} stock in Indian market:
            1. Recent news from Moneycontrol, Economic Times, NSE/BSE announcements
            2. Social media sentiment from Twitter/X
            3. Company fundamentals and recent annual report highlights
            4. Employee sentiment from Glassdoor
            5. Analyst ratings and recommendations
            
            Provide a sentiment score from 0-100 where:
            - 0-30: Very Negative
            - 31-50: Negative
            - 51-70: Neutral to Positive
            - 71-85: Positive
            - 86-100: Very Positive
            
            Return JSON with: sentiment_score, confidence (0-1), key_findings (list), reasoning (string), sources (list)"""
            
            response = perplexity.research_indian_stock(symbol, 'news_sentiment')
            
            if response and response.get('research_content'):
                try:
                    parsed = self._parse_llm_response(response['research_content'], 'qualitative')
                    score = parsed.get('sentiment_score', 50)
                    confidence = parsed.get('confidence', 0.7)
                    findings = parsed.get('key_findings', [])
                    reasoning = parsed.get('reasoning', f"Analysis based on recent news, social media, and company fundamentals")
                except:
                    score = 50
                    confidence = 0.5
                    findings = ["Unable to parse qualitative data"]
                    reasoning = "Analysis unavailable"
                
                sources_list = [
                    {'name': 'Perplexity Sonar Pro', 'type': 'news_aggregation', 'coverage': 'News, social media, annual reports'},
                    {'name': 'NSE Announcements', 'type': 'regulatory', 'coverage': 'Corporate actions and disclosures'},
                    {'name': 'Financial News Networks', 'type': 'media', 'coverage': 'Moneycontrol, Economic Times, Bloomberg'}
                ]
                
                return {
                    'qualitative_score': min(100, max(0, score)),
                    'qualitative_details': {
                        'findings': findings,
                        'sources_analyzed': list(sources.keys()),
                        'citations': response.get('citations', [])
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': min(1.0, max(0, confidence)),
                    'step': 'qualitative_complete'
                }
        except Exception as e:
            logger.error(f"Qualitative analysis error: {e}")
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Analysis unavailable'},
            'qualitative_sources': [{'name': 'N/A', 'type': 'error', 'coverage': 'Analysis failed'}],
            'qualitative_reasoning': 'Qualitative analysis is currently unavailable',
            'qualitative_confidence': 0.3,
            'step': 'qualitative_fallback'
        }
    
    def quantitative_analysis(self, state: IScoreState) -> Dict:
        """Node 3: Quantitative Analysis (30% weight) — Real Technical Indicators"""
        logger.info(f"I-Score Node 3: Real quantitative analysis for {state['symbol']}")
        symbol = state['symbol']

        try:
            from services.iscore.data_fetcher import fetch_historical_ohlcv_with_source
            from services.iscore.indicators import compute_all_indicators
            from services.iscore.scoring import compute_quant_score

            df, hist_src = fetch_historical_ohlcv_with_source(symbol, days=120)
            if df is not None and len(df) >= 15:
                indicators = compute_all_indicators(df)
                if indicators:
                    quant = compute_quant_score(indicators)
                    price = indicators['price']
                    rsi_val = indicators['rsi']
                    rsi_signal = 'overbought' if rsi_val > 70 else ('oversold' if rsi_val < 30 else 'neutral')
                    ema_signal = 'bullish' if (indicators.get('ema9') and indicators.get('ema20') and indicators['ema9'] > indicators['ema20']) else 'bearish'

                    reasoning = (
                        f"Real technical analysis: RSI(14) = {rsi_val} ({rsi_signal}), "
                        f"EMA alignment = {ema_signal}, SuperTrend = {indicators['supertrend_direction']}, "
                        f"5d momentum = {indicators['momentum_5d']}%, "
                        f"Short/Medium/Long trend = {indicators['short_trend']}/{indicators['medium_trend']}/{indicators['long_trend']}."
                    )

                    return {
                        'current_price': price['current'],
                        'previous_close': price['prev_close'],
                        'price_change_pct': price['change_pct'],
                        'market_status': 'live',
                        'raw_indicators': indicators,
                        'hist_data_source': hist_src,
                        'quantitative_score': min(100, max(0, quant['composite'])),
                        'quantitative_details': {
                            'rsi': {'value': rsi_val, 'signal': rsi_signal, 'score': quant['rsi_score']},
                            'supertrend': {'direction': indicators['supertrend_direction'], 'score': quant['supertrend_score']},
                            'ema': {
                                'ema9': indicators.get('ema9'), 'ema20': indicators.get('ema20'),
                                'ema50': indicators.get('ema50'), 'signal': ema_signal,
                                'score': quant['ema_score'],
                            },
                            'momentum': {
                                '5d': indicators['momentum_5d'], '20d': indicators.get('momentum_20d', 0),
                                'score': quant['momentum_score'],
                            },
                            'multi_timeframe': {
                                'short': indicators['short_trend'],
                                'medium': indicators['medium_trend'],
                                'long': indicators['long_trend'],
                            },
                            'price_data': {
                                'current': price['current'], 'previous_close': price['prev_close'],
                                'change_pct': price['change_pct'],
                            },
                        },
                        'quantitative_sources': [
                            {'name': f'Historical OHLCV ({hist_src or "unknown"})', 'type': 'market_data', 'coverage': f'{len(df)} days'},
                            {'name': f'RSI(14) Wilder', 'type': 'technical', 'coverage': f'{rsi_val}'},
                            {'name': f'EMA(9/20/50)', 'type': 'technical', 'coverage': ema_signal},
                            {'name': 'SuperTrend (ATR)', 'type': 'trend_following', 'coverage': indicators['supertrend_direction']},
                        ],
                        'quantitative_reasoning': reasoning,
                        'quantitative_confidence': 0.88,
                        'step': 'quantitative_complete',
                    }
        except Exception as e:
            logger.error(f"Real quantitative analysis error: {e}")

        try:
            fallback = self._get_fallback_price_data(symbol)
            if fallback:
                return {
                    'current_price': fallback.get('current_price', 0),
                    'previous_close': fallback.get('previous_close', 0),
                    'price_change_pct': fallback.get('change_percent', 0),
                    'market_status': 'demo', 'raw_indicators': {},
                    'quantitative_score': 50,
                    'quantitative_details': {'error': 'Indicator data unavailable — using fallback'},
                    'quantitative_sources': [{'name': 'Fallback', 'type': 'demo', 'coverage': symbol}],
                    'quantitative_reasoning': 'Fallback data — real indicators unavailable',
                    'quantitative_confidence': 0.30, 'step': 'quantitative_fallback',
                }
        except:
            pass

        return {
            'current_price': 0, 'previous_close': 0, 'price_change_pct': 0,
            'market_status': 'unknown', 'raw_indicators': {},
            'quantitative_score': 50,
            'quantitative_details': {'error': 'Technical data unavailable'},
            'quantitative_sources': [{'name': 'None', 'type': 'error', 'coverage': 'N/A'}],
            'quantitative_reasoning': 'Fallback — no data available',
            'quantitative_confidence': 0.20, 'step': 'quantitative_fallback',
        }
    
    def search_sentiment(self, state: IScoreState) -> Dict:
        """Node 4: Search Sentiment Analysis (10% weight)"""
        logger.info(f"I-Score Node 4: Search sentiment for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.perplexity_service import PerplexityService
            perplexity = PerplexityService()
            
            search_query = f"""What is the current market sentiment and search interest for {symbol} stock in India?
            
            Analyze:
            1. Recent search trends
            2. Social media buzz
            3. Retail investor interest
            4. News mentions frequency
            
            Provide a sentiment score from 0-100 and key insights.
            Return JSON with: search_score, trend_direction (up/down/stable), buzz_level (high/medium/low)"""
            
            response = perplexity.research_indian_stock(symbol, 'news_sentiment')
            
            if response and response.get('research_content'):
                try:
                    parsed = self._parse_llm_response(response['research_content'], 'search')
                    score = parsed.get('search_score', 50)
                    trend = parsed.get('trend_direction', 'stable')
                    buzz = parsed.get('buzz_level', 'medium')
                except:
                    score = 50
                    trend = 'stable'
                    buzz = 'medium'
                
                evidence = state.get('evidence', [])
                evidence.append({
                    'source_name': 'Perplexity Search Trends',
                    'source_type': 'search',
                    'title': f'Search sentiment for {symbol}',
                    'sentiment_score': score
                })
                
                sources_list = [
                    {'name': 'Perplexity Search Index', 'type': 'search_trends', 'coverage': 'Web-wide search volume and trends'},
                    {'name': 'Social Media Analysis', 'type': 'sentiment', 'coverage': 'Twitter, Reddit, retail forums'},
                    {'name': 'News Mentions', 'type': 'media', 'coverage': f'Buzz Level: {buzz}'}
                ]
                
                reasoning = f"Search sentiment shows {trend} trend with {buzz} buzz level. Market interest analysis based on search volume, social media mentions, and news frequency."
                
                return {
                    'search_score': min(100, max(0, score)),
                    'search_details': {
                        'trend_direction': trend,
                        'buzz_level': buzz,
                        'analysis': response.get('answer', '')[:500]
                    },
                    'search_sources': sources_list,
                    'search_reasoning': reasoning,
                    'search_confidence': 0.7,
                    'step': 'search_complete'
                }
        except Exception as e:
            logger.error(f"Search sentiment error: {e}")
        
        return {
            'search_score': 50,
            'search_details': {'error': 'Search analysis unavailable'},
            'search_sources': [{'name': 'Perplexity API', 'type': 'error', 'coverage': 'Service temporarily unavailable'}],
            'search_reasoning': 'Search sentiment analysis currently unavailable',
            'search_confidence': 0.3,
            'step': 'search_fallback'
        }
    
    def trend_analysis(self, state: IScoreState) -> Dict:
        """Node 5: Stock-Specific Trend Analysis (20% weight)"""
        logger.info(f"I-Score Node 5: Stock-specific trend analysis for {state['symbol']}")

        indicators = state.get('raw_indicators', {})
        if indicators:
            from services.iscore.scoring import compute_trend_score_from_indicators
            trend = compute_trend_score_from_indicators(indicators)
            vol = indicators.get('volume', {})

            reasoning = (
                f"Stock-specific trend: Short={indicators.get('short_trend','?')}, "
                f"Medium={indicators.get('medium_trend','?')}, Long={indicators.get('long_trend','?')}. "
                f"Volume ratio {vol.get('volume_ratio', 1):.1f}x avg ({'spike' if vol.get('is_spike') else 'normal'})."
            )

            return {
                'trend_score': min(100, max(0, trend['composite'])),
                'trend_details': {
                    'multi_timeframe': {
                        'short': indicators.get('short_trend', 'neutral'),
                        'medium': indicators.get('medium_trend', 'neutral'),
                        'long': indicators.get('long_trend', 'neutral'),
                        'score': trend['multi_timeframe_score'],
                    },
                    'volume': {
                        'current': vol.get('current_volume', 0),
                        'avg_20d': vol.get('avg_volume_20d', 0),
                        'ratio': vol.get('volume_ratio', 1.0),
                        'is_spike': vol.get('is_spike', False),
                        'score': trend['volume_score'],
                    },
                },
                'trend_sources': [
                    {'name': 'Multi-Timeframe EMA', 'type': 'trend', 'coverage': f"S:{indicators.get('short_trend')} M:{indicators.get('medium_trend')} L:{indicators.get('long_trend')}"},
                    {'name': 'Volume Analysis', 'type': 'volume', 'coverage': f"{vol.get('volume_ratio', 1):.1f}x avg"},
                ],
                'trend_reasoning': reasoning,
                'trend_confidence': 0.80,
                'step': 'trend_complete',
            }

        return {
            'trend_score': 50,
            'trend_details': {'error': 'No stock-level indicators available'},
            'trend_sources': [],
            'trend_reasoning': 'Trend data unavailable — using neutral default',
            'trend_confidence': 0.30,
            'step': 'trend_fallback',
        }

    def risk_volatility(self, state: IScoreState) -> Dict:
        """Node: Risk & Volatility Assessment (20% weight)"""
        logger.info(f"I-Score Risk Node: Assessing risk for {state['symbol']}")

        indicators = state.get('raw_indicators', {})
        if not indicators:
            return {
                'risk_score': 50, 'risk_details': {'error': 'No indicator data'},
                'risk_confidence': 0.30, 'step': 'risk_fallback',
            }

        try:
            from services.iscore.scoring import compute_risk_score
            from services.iscore.data_fetcher import fetch_historical_ohlcv, fetch_market_index_history
            from services.iscore.indicators import compute_beta

            stock_df = fetch_historical_ohlcv(state['symbol'], days=60)
            market_df = fetch_market_index_history('^NSEI', days=60)
            beta = 1.0
            if stock_df is not None and market_df is not None and len(stock_df) >= 10 and len(market_df) >= 10:
                stock_ret = stock_df['close'].pct_change().dropna()
                mkt_ret = market_df['close'].pct_change().dropna()
                beta = compute_beta(stock_ret, mkt_ret)

            risk = compute_risk_score(indicators, beta)

            vol_label = 'high' if indicators.get('atr_pct', 0) > 4 else ('low' if indicators.get('atr_pct', 0) < 1.5 else 'medium')
            reasoning = (
                f"ATR volatility = {indicators.get('atr_pct', 0):.1f}% ({vol_label}), "
                f"Max drawdown = {indicators.get('max_drawdown', 0):.1f}%, Beta = {beta:.2f}."
            )

            return {
                'risk_score': min(100, max(0, risk['composite'])),
                'risk_details': {
                    'atr_pct': indicators.get('atr_pct', 0),
                    'volatility_label': vol_label,
                    'volatility_score': risk['volatility_score'],
                    'max_drawdown': indicators.get('max_drawdown', 0),
                    'drawdown_score': risk['drawdown_score'],
                    'beta': round(beta, 2),
                    'beta_score': risk['beta_score'],
                },
                'risk_confidence': 0.82,
                'step': 'risk_complete',
            }
        except Exception as e:
            logger.error(f"Risk analysis error: {e}")
            return {
                'risk_score': 50, 'risk_details': {'error': str(e)},
                'risk_confidence': 0.30, 'step': 'risk_fallback',
            }

    def market_context(self, state: IScoreState) -> Dict:
        """Node: Market Context (5% weight) — VIX, PCR, Nifty direction"""
        logger.info(f"I-Score Market Context Node for {state['symbol']}")

        # ---- helpers ----
        def _vix_score_regime(v):
            if v < 15:
                return 75, 'calm'
            elif v > 25:
                return 25, 'fearful'
            else:
                return 50 - (v - 15) * 2.5, 'cautious'

        def _pcr_score(p):
            if p > 1.3:
                return 70
            elif p < 0.7:
                return 35
            return 50 + (p - 1.0) * 20

        def _mkt_score(c):
            if c > 0.5:
                return 70
            elif c < -0.5:
                return 30
            return 50 + c * 15

        # Defaults (used when a data source fails)
        vix, vix_score, vix_regime = 15.0, 75.0, 'calm'
        pcr, pcr_score_val = 0.85, 47.0
        mkt_chg, mkt_score_val = 0.0, 50.0
        confidence = 0.30
        data_sources = []

        # ---- 0. Market Data Gateway (single canonical fallback chain) ----
        try:
            from services.market_data_gateway import get_index_prices as _gw_idx
            _gw_data = _gw_idx(['NIFTY', 'INDIA VIX'])
            if _gw_data.get('_success'):
                _nifty = _gw_data.get('NIFTY', {})
                _vix_d = _gw_data.get('INDIA VIX', {})
                if _vix_d.get('ltp', 0) > 0:
                    vix = float(_vix_d['ltp'])
                    vix_score, vix_regime = _vix_score_regime(vix)
                    data_sources.append(f"gateway-VIX[{_vix_d.get('source','?')}]")
                    confidence = max(confidence, 0.70)
                if _nifty.get('pct_change') is not None:
                    mkt_chg = round(float(_nifty['pct_change']), 2)
                    mkt_score_val = _mkt_score(mkt_chg)
                    data_sources.append(f"gateway-NIFTY[{_nifty.get('source','?')}]")
                    confidence = max(confidence, 0.70)
        except Exception as e:
            logger.debug(f"Gateway index prices failed: {e}")

        # ---- 1. Try Dhan for VIX + NIFTY if gateway didn't deliver ----
        _need_vix   = not any('VIX'   in s for s in data_sources)
        _need_nifty = not any('NIFTY' in s for s in data_sources)
        if _need_vix or _need_nifty:
            try:
                from services.dhan_service import get_index_quotes
                idx_data = get_index_quotes()   # {NIFTY:{ltp,open,...}, INDIA VIX:{...}, ...}
                if idx_data:
                    if _need_vix:
                        vix_raw = idx_data.get("INDIA VIX", {}).get("ltp", 0)
                        if vix_raw and float(vix_raw) > 0:
                            vix = float(vix_raw)
                            vix_score, vix_regime = _vix_score_regime(vix)
                            data_sources.append('Dhan-VIX')
                            confidence = max(confidence, 0.70)
                    if _need_nifty:
                        nifty_d = idx_data.get("NIFTY", {})
                        pct = nifty_d.get("pct_change", 0)
                        if pct is not None:
                            mkt_chg = round(float(pct), 2)
                            mkt_score_val = _mkt_score(mkt_chg)
                            data_sources.append('Dhan-NIFTY')
                            confidence = max(confidence, 0.70)
            except Exception as e:
                logger.debug(f"Dhan VIX/NIFTY fetch failed: {e}")

        # ---- 2. yfinance fallback for VIX (if Dhan didn't supply it) ----
        if 'Dhan-VIX' not in data_sources:
            try:
                import yfinance as yf
                vix_ticker = yf.Ticker('^INDIAVIX')
                vix_info = vix_ticker.history(period='2d')
                if not vix_info.empty:
                    vix = float(vix_info['Close'].iloc[-1])
                    vix_score, vix_regime = _vix_score_regime(vix)
                    data_sources.append('yfinance-VIX')
                    confidence = max(confidence, 0.60)
            except Exception as e:
                logger.debug(f"yfinance VIX fetch failed: {e}")

        # ---- 3. yfinance fallback for NIFTY change (if Dhan didn't supply it) ----
        if 'Dhan-NIFTY' not in data_sources:
            try:
                import yfinance as yf
                nifty_ticker = yf.Ticker('^NSEI')
                nifty_hist = nifty_ticker.history(period='2d')
                if len(nifty_hist) >= 2:
                    prev_close = float(nifty_hist['Close'].iloc[-2])
                    last_close = float(nifty_hist['Close'].iloc[-1])
                    mkt_chg = round((last_close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
                    mkt_score_val = _mkt_score(mkt_chg)
                    data_sources.append('yfinance-NIFTY')
                    confidence = max(confidence, 0.60)
            except Exception as e:
                logger.debug(f"yfinance Nifty fetch failed: {e}")

        # ---- 4. Try NSE service for PCR (may fail when geo-blocked) ----
        try:
            from services.nse_service import NSEService
            nse = NSEService()
            pcr_data = nse.get_pcr('NIFTY')
            pcr_raw = pcr_data.get('pcr_value', 0)
            if pcr_raw and pcr_raw > 0:
                pcr = pcr_raw
                data_sources.append('NSE-PCR')
                confidence = max(confidence, 0.70)
        except Exception as e:
            logger.debug(f"NSE PCR fetch failed (geo-blocked?): {e}")

        # ---- 5. Try NSE service for VIX (last resort — geo-blocked in cloud) ----
        if 'Dhan-VIX' not in data_sources and 'yfinance-VIX' not in data_sources:
            try:
                from services.nse_service import NSEService
                nse = NSEService()
                vix_data = nse.get_india_vix()
                vix_raw = vix_data.get('vix_value', 0)
                if vix_raw and vix_raw > 0:
                    vix = vix_raw
                    vix_score, vix_regime = _vix_score_regime(vix)
                    data_sources.append('NSE-VIX')
                    confidence = max(confidence, 0.70)
            except Exception as e:
                logger.debug(f"NSE VIX fetch also failed: {e}")

        pcr_score_val = _pcr_score(pcr)
        composite = 0.40 * vix_score + 0.30 * pcr_score_val + 0.30 * mkt_score_val
        reasoning = f"VIX={vix:.1f} ({vix_regime}), PCR={pcr:.2f}, Nifty {mkt_chg:+.1f}%. Sources: {', '.join(data_sources) or 'defaults'}."

        return {
            'market_context_score': round(min(100, max(0, composite)), 2),
            'market_context_details': {
                'vix': {'value': round(vix, 2), 'regime': vix_regime, 'score': round(vix_score, 2)},
                'pcr': {'value': round(pcr, 2), 'score': round(pcr_score_val, 2)},
                'nifty': {'change_pct': round(mkt_chg, 2), 'score': round(mkt_score_val, 2)},
            },
            'market_context_confidence': confidence,
            'market_context_reasoning': reasoning,
            'step': 'market_context_complete',
        }

    # ==================== MUTUAL FUND ANALYSIS METHODS ====================
    
    def qualitative_analysis_mf(self, state: IScoreState) -> Dict:
        """Mutual Fund Qualitative Analysis (15% weight) - Fund House, Manager, Expense Ratio"""
        logger.info(f"I-Score MF Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.mfapi_service import mfapi_service
            fund_data = mfapi_service.analyze_for_iscore(symbol)
            
            if fund_data.get('success'):
                fund_house = fund_data.get('fund_house', '')
                fund_age = fund_data.get('fund_age_years', 0)
                category = fund_data.get('scheme_category', '')
                
                score = 50
                
                top_amcs = ['HDFC', 'ICICI', 'SBI', 'Nippon', 'Axis', 'Kotak', 'Aditya Birla', 'UTI', 'Franklin', 'DSP']
                if any(amc.lower() in fund_house.lower() for amc in top_amcs):
                    score += 15
                
                if fund_age >= 10:
                    score += 20
                elif fund_age >= 5:
                    score += 15
                elif fund_age >= 3:
                    score += 10
                elif fund_age >= 1:
                    score += 5
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'AMFI Data', 'type': 'regulatory', 'coverage': f'Fund House: {fund_house}'},
                    {'name': 'Fund Age', 'type': 'track_record', 'coverage': f'Age: {fund_age} years'},
                    {'name': 'Category', 'type': 'classification', 'coverage': category}
                ]
                
                reasoning = f"Fund managed by {fund_house} with {fund_age} years track record. Category: {category}. Qualitative score based on AMC reputation and fund maturity."
                
                return {
                    'qualitative_score': score,
                    'qualitative_details': {
                        'fund_house': fund_house,
                        'fund_age_years': fund_age,
                        'category': category,
                        'scheme_name': fund_data.get('scheme_name', '')
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': 0.8,
                    'asset_name': fund_data.get('scheme_name', symbol),
                    'step': 'mf_qualitative_complete'
                }
        except Exception as e:
            logger.error(f"MF Qualitative analysis error: {e}")
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Fund data unavailable'},
            'qualitative_sources': [{'name': 'MFapi', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'qualitative_reasoning': 'Qualitative analysis unavailable for this fund',
            'qualitative_confidence': 0.3,
            'step': 'mf_qualitative_fallback'
        }
    
    def quantitative_analysis_mf(self, state: IScoreState) -> Dict:
        """Mutual Fund Quantitative Analysis (50% weight) - Returns, CAGR, NAV, Risk Metrics"""
        logger.info(f"I-Score MF Node 3: Quantitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.mfapi_service import mfapi_service
            fund_data = mfapi_service.analyze_for_iscore(symbol)
            
            if fund_data.get('success'):
                current_nav = fund_data.get('current_nav', 0)
                nav_date = fund_data.get('nav_date', '')
                returns = fund_data.get('returns', {})
                cagr = fund_data.get('cagr', {})
                risk_metrics = fund_data.get('risk_metrics', {})
                
                score = 50
                
                return_3y = returns.get('3y', 0) or 0
                cagr_3y = cagr.get('3y', 0) or 0
                
                if cagr_3y >= 25:
                    score += 30
                elif cagr_3y >= 20:
                    score += 25
                elif cagr_3y >= 15:
                    score += 20
                elif cagr_3y >= 10:
                    score += 10
                elif cagr_3y >= 5:
                    score += 5
                elif cagr_3y < 0:
                    score -= 15
                
                sharpe = risk_metrics.get('sharpe_ratio', 0) or 0
                if sharpe >= 1.5:
                    score += 15
                elif sharpe >= 1.0:
                    score += 10
                elif sharpe >= 0.5:
                    score += 5
                elif sharpe < 0:
                    score -= 10
                
                volatility = risk_metrics.get('volatility', 20) or 20
                if volatility < 15:
                    score += 5
                elif volatility > 25:
                    score -= 5
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'MFapi.in', 'type': 'nav_data', 'coverage': f'Current NAV: ₹{current_nav:.2f} ({nav_date})'},
                    {'name': 'Returns Analysis', 'type': 'performance', 'coverage': f'3Y CAGR: {cagr_3y:.1f}%'},
                    {'name': 'Risk Metrics', 'type': 'risk', 'coverage': f'Sharpe Ratio: {sharpe:.2f}, Volatility: {volatility:.1f}%'}
                ]
                
                reasoning = f"NAV at ₹{current_nav:.2f}. 3-Year CAGR of {cagr_3y:.1f}% indicates {'strong' if cagr_3y > 15 else 'moderate' if cagr_3y > 5 else 'weak'} performance. Sharpe ratio of {sharpe:.2f} shows {'excellent' if sharpe > 1 else 'good' if sharpe > 0.5 else 'poor'} risk-adjusted returns."
                
                return {
                    'current_price': current_nav,
                    'previous_close': current_nav * 0.999,
                    'price_change_pct': returns.get('1w', 0) or 0,
                    'market_status': 'live',
                    'quantitative_score': score,
                    'quantitative_details': {
                        'nav': {
                            'current': current_nav,
                            'date': nav_date
                        },
                        'returns': returns,
                        'cagr': cagr,
                        'risk_metrics': risk_metrics,
                        'price_data': {
                            'current': current_nav,
                            'previous_close': current_nav * 0.999,
                            'change_pct': returns.get('1w', 0) or 0
                        }
                    },
                    'quantitative_sources': sources_list,
                    'quantitative_reasoning': reasoning,
                    'quantitative_confidence': 0.85,
                    'step': 'mf_quantitative_complete'
                }
        except Exception as e:
            logger.error(f"MF Quantitative analysis error: {e}")
        
        return {
            'current_price': 0,
            'previous_close': 0,
            'price_change_pct': 0,
            'market_status': 'unknown',
            'quantitative_score': 50,
            'quantitative_details': {'error': 'NAV data unavailable'},
            'quantitative_sources': [{'name': 'MFapi', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'quantitative_reasoning': 'Quantitative analysis unavailable',
            'quantitative_confidence': 0.3,
            'step': 'mf_quantitative_fallback'
        }
    
    def trend_analysis_mf(self, state: IScoreState) -> Dict:
        """Mutual Fund Trend Analysis (25% weight) - Category Performance, Recent Trends"""
        logger.info(f"I-Score MF Node 5: Trend analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.mfapi_service import mfapi_service
            fund_data = mfapi_service.analyze_for_iscore(symbol)
            
            if fund_data.get('success'):
                returns = fund_data.get('returns', {})
                category = fund_data.get('scheme_category', '')
                
                score = 50
                
                return_1m = returns.get('1m', 0) or 0
                return_3m = returns.get('3m', 0) or 0
                return_6m = returns.get('6m', 0) or 0
                
                if return_1m > 5:
                    score += 20
                elif return_1m > 2:
                    score += 10
                elif return_1m > 0:
                    score += 5
                elif return_1m < -5:
                    score -= 15
                elif return_1m < -2:
                    score -= 10
                elif return_1m < 0:
                    score -= 5
                
                if return_3m > return_1m * 3:
                    trend_direction = 'accelerating'
                    score += 10
                elif return_3m < return_1m * 2:
                    trend_direction = 'decelerating'
                    score -= 5
                else:
                    trend_direction = 'stable'
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'Recent Performance', 'type': 'short_term', 'coverage': f'1M: {return_1m:.1f}%, 3M: {return_3m:.1f}%'},
                    {'name': 'Trend Direction', 'type': 'momentum', 'coverage': f'Trend: {trend_direction}'},
                    {'name': 'Category', 'type': 'benchmark', 'coverage': category}
                ]
                
                reasoning = f"Recent trend shows {return_1m:.1f}% in 1 month, {return_3m:.1f}% in 3 months. Trend is {trend_direction}. Category: {category}."
                
                return {
                    'trend_score': score,
                    'trend_details': {
                        'recent_returns': {
                            '1w': returns.get('1w', 0),
                            '1m': return_1m,
                            '3m': return_3m,
                            '6m': return_6m
                        },
                        'trend_direction': trend_direction,
                        'category': category
                    },
                    'trend_sources': sources_list,
                    'trend_reasoning': reasoning,
                    'trend_confidence': 0.75,
                    'step': 'mf_trend_complete'
                }
        except Exception as e:
            logger.error(f"MF Trend analysis error: {e}")
        
        return {
            'trend_score': 50,
            'trend_details': {'error': 'Trend data unavailable'},
            'trend_sources': [{'name': 'MFapi', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'trend_reasoning': 'Trend analysis unavailable',
            'trend_confidence': 0.3,
            'step': 'mf_trend_fallback'
        }
    
    # ==================== END MUTUAL FUND ANALYSIS METHODS ====================
    
    # ==================== BOND ANALYSIS METHODS ====================
    
    def qualitative_analysis_bond(self, state: IScoreState) -> Dict:
        """Bond Qualitative Analysis (15% weight) - Credit Rating, Issuer Quality"""
        logger.info(f"I-Score Bond Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.bond_service import bond_service
            bond_data = bond_service.analyze_for_iscore(symbol)
            
            if bond_data.get('success'):
                issuer = bond_data.get('issuer', '')
                issuer_type = bond_data.get('issuer_type', 'unknown')
                credit_rating = bond_data.get('credit_rating', 'NR')
                credit_score = bond_data.get('credit_score', 50)
                category = bond_data.get('category', '')
                
                score = credit_score
                
                if issuer_type == 'sovereign':
                    score = min(100, score + 5)
                elif issuer_type == 'psu':
                    score = min(100, score + 3)
                
                sources_list = [
                    {'name': 'NSDL BondInfo', 'type': 'regulatory', 'coverage': f'Issuer: {issuer}'},
                    {'name': 'Credit Rating', 'type': 'credit', 'coverage': f'Rating: {credit_rating}'},
                    {'name': 'SEBI Data', 'type': 'regulatory', 'coverage': f'Category: {category}'}
                ]
                
                reasoning = f"Bond issued by {issuer} with credit rating {credit_rating}. Issuer type: {issuer_type.upper()}. Higher ratings indicate lower default risk and higher qualitative score."
                
                return {
                    'qualitative_score': score,
                    'qualitative_details': {
                        'issuer': issuer,
                        'issuer_type': issuer_type,
                        'credit_rating': credit_rating,
                        'credit_score': credit_score,
                        'category': category,
                        'bond_name': bond_data.get('name', '')
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': 0.85,
                    'asset_name': bond_data.get('name', symbol),
                    'step': 'bond_qualitative_complete'
                }
        except Exception as e:
            logger.error(f"Bond Qualitative analysis error: {e}")
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Bond data unavailable'},
            'qualitative_sources': [{'name': 'NSDL', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'qualitative_reasoning': 'Qualitative analysis unavailable for this bond',
            'qualitative_confidence': 0.3,
            'step': 'bond_qualitative_fallback'
        }
    
    def quantitative_analysis_bond(self, state: IScoreState) -> Dict:
        """Bond Quantitative Analysis (50% weight) - Yield, Price, Duration"""
        logger.info(f"I-Score Bond Node 3: Quantitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.bond_service import bond_service
            bond_data = bond_service.analyze_for_iscore(symbol)
            
            if bond_data.get('success'):
                clean_price = bond_data.get('clean_price', 100)
                dirty_price = bond_data.get('dirty_price', 100)
                coupon_rate = bond_data.get('coupon_rate', 7.0)
                current_yield = bond_data.get('current_yield', 7.0)
                ytm = bond_data.get('ytm', 7.0)
                yield_spread = bond_data.get('yield_spread', 0.5)
                yield_score = bond_data.get('yield_score', 60)
                modified_duration = bond_data.get('modified_duration', 4.0)
                duration_score = bond_data.get('duration_score', 60)
                duration_risk = bond_data.get('duration_risk', 'Moderate')
                years_to_maturity = bond_data.get('years_to_maturity', 5.0)
                face_value = bond_data.get('face_value', 1000)
                
                score = (yield_score * 0.6) + (duration_score * 0.4)
                
                if clean_price < face_value * 0.95:
                    score += 10
                elif clean_price > face_value * 1.05:
                    score -= 5
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'NSE Bond Market', 'type': 'price_data', 'coverage': f'Clean Price: ₹{clean_price:.2f}'},
                    {'name': 'Yield Analysis', 'type': 'yield', 'coverage': f'YTM: {ytm:.2f}%, Current Yield: {current_yield:.2f}%'},
                    {'name': 'Duration Risk', 'type': 'risk', 'coverage': f'Modified Duration: {modified_duration:.1f} ({duration_risk})'}
                ]
                
                reasoning = f"Bond trading at ₹{clean_price:.2f} with YTM of {ytm:.2f}%. Yield spread of {yield_spread:.2f}% over benchmark. Modified duration of {modified_duration:.1f} years indicates {duration_risk.lower()} interest rate sensitivity."
                
                return {
                    'current_price': dirty_price,
                    'previous_close': dirty_price * 0.999,
                    'price_change_pct': 0.1,
                    'market_status': 'live',
                    'quantitative_score': score,
                    'quantitative_details': {
                        'price': {
                            'clean': clean_price,
                            'dirty': dirty_price,
                            'face_value': face_value,
                            'accrued_interest': bond_data.get('accrued_interest', 0)
                        },
                        'yield': {
                            'coupon_rate': coupon_rate,
                            'current_yield': current_yield,
                            'ytm': ytm,
                            'yield_spread': yield_spread,
                            'yield_score': yield_score
                        },
                        'duration': {
                            'modified_duration': modified_duration,
                            'years_to_maturity': years_to_maturity,
                            'duration_risk': duration_risk,
                            'duration_score': duration_score
                        },
                        'price_data': {
                            'current': dirty_price,
                            'previous_close': dirty_price * 0.999,
                            'change_pct': 0.1
                        }
                    },
                    'quantitative_sources': sources_list,
                    'quantitative_reasoning': reasoning,
                    'quantitative_confidence': 0.85,
                    'step': 'bond_quantitative_complete'
                }
        except Exception as e:
            logger.error(f"Bond Quantitative analysis error: {e}")
        
        return {
            'current_price': 0,
            'previous_close': 0,
            'price_change_pct': 0,
            'market_status': 'unknown',
            'quantitative_score': 50,
            'quantitative_details': {'error': 'Bond price data unavailable'},
            'quantitative_sources': [{'name': 'NSE Bond', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'quantitative_reasoning': 'Quantitative analysis unavailable',
            'quantitative_confidence': 0.3,
            'step': 'bond_quantitative_fallback'
        }
    
    def trend_analysis_bond(self, state: IScoreState) -> Dict:
        """Bond Trend Analysis (25% weight) - Yield Curve, Rate Environment"""
        logger.info(f"I-Score Bond Node 5: Trend analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.bond_service import bond_service
            bond_data = bond_service.analyze_for_iscore(symbol)
            yield_curve = bond_service.get_yield_curve_data()
            
            if bond_data.get('success'):
                ytm = bond_data.get('ytm', 7.0)
                modified_duration = bond_data.get('modified_duration', 4.0)
                years_to_maturity = bond_data.get('years_to_maturity', 5.0)
                trade_volume = bond_data.get('trade_volume', 0)
                
                score = 50
                
                curve_shape = yield_curve.get('curve_shape', 'normal')
                benchmark_10y = yield_curve.get('benchmark_10y', 7.18)
                
                if curve_shape == 'normal':
                    score += 10
                    rate_outlook = 'stable'
                elif curve_shape == 'steep':
                    score += 5
                    rate_outlook = 'rising'
                elif curve_shape == 'flat':
                    score -= 5
                    rate_outlook = 'uncertain'
                elif curve_shape == 'inverted':
                    score -= 10
                    rate_outlook = 'falling'
                else:
                    rate_outlook = 'neutral'
                
                if ytm > benchmark_10y + 0.5:
                    score += 15
                    yield_position = 'attractive'
                elif ytm > benchmark_10y:
                    score += 10
                    yield_position = 'fair'
                elif ytm > benchmark_10y - 0.3:
                    score += 5
                    yield_position = 'slightly below benchmark'
                else:
                    score -= 5
                    yield_position = 'below benchmark'
                
                if trade_volume > 10000:
                    liquidity = 'High'
                    score += 5
                elif trade_volume > 5000:
                    liquidity = 'Medium'
                    score += 2
                else:
                    liquidity = 'Low'
                    score -= 5
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'RBI Yield Curve', 'type': 'benchmark', 'coverage': f'Curve: {curve_shape}, 10Y: {benchmark_10y:.2f}%'},
                    {'name': 'Rate Outlook', 'type': 'macro', 'coverage': f'Rate environment: {rate_outlook}'},
                    {'name': 'Liquidity', 'type': 'trading', 'coverage': f'Trade volume: {trade_volume} units ({liquidity})'}
                ]
                
                reasoning = f"Yield curve is {curve_shape} with 10Y benchmark at {benchmark_10y:.2f}%. Bond YTM of {ytm:.2f}% is {yield_position}. Trading liquidity is {liquidity.lower()}. Rate outlook: {rate_outlook}."
                
                return {
                    'trend_score': score,
                    'trend_details': {
                        'yield_curve': {
                            'shape': curve_shape,
                            'benchmark_10y': benchmark_10y,
                            'rate_outlook': rate_outlook
                        },
                        'yield_position': yield_position,
                        'liquidity': {
                            'level': liquidity,
                            'trade_volume': trade_volume
                        },
                        'maturity_info': {
                            'years_to_maturity': years_to_maturity,
                            'modified_duration': modified_duration
                        }
                    },
                    'trend_sources': sources_list,
                    'trend_reasoning': reasoning,
                    'trend_confidence': 0.75,
                    'step': 'bond_trend_complete'
                }
        except Exception as e:
            logger.error(f"Bond Trend analysis error: {e}")
        
        return {
            'trend_score': 50,
            'trend_details': {'error': 'Trend data unavailable'},
            'trend_sources': [{'name': 'RBI', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'trend_reasoning': 'Trend analysis unavailable',
            'trend_confidence': 0.3,
            'step': 'bond_trend_fallback'
        }
    
    def search_sentiment_bond(self, state: IScoreState) -> Dict:
        """Bond Search Sentiment Analysis (10% weight) - Bond Market Sentiment"""
        logger.info(f"I-Score Bond Node 4: Search sentiment for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.perplexity_service import PerplexityService
            perplexity = PerplexityService()
            
            response = perplexity.research_indian_stock(symbol, 'news_sentiment')
            logger.debug(f"Perplexity response for {symbol}: {response}")
            
            if response and response.get('research_content'):
                try:
                    parsed = self._parse_llm_response(response['research_content'], 'search')
                    score = parsed.get('search_score', 50)
                    trend = parsed.get('trend_direction', 'stable')
                    buzz = parsed.get('buzz_level', 'medium')
                except Exception as parse_e:
                    logger.debug(f"Parse error: {parse_e}")
                    score = 50
                    trend = 'stable'
                    buzz = 'medium'
                
                sources_list = [
                    {'name': 'Perplexity Search Index', 'type': 'search_trends', 'coverage': 'Bond market sentiment and trends'},
                    {'name': 'News Analysis', 'type': 'media', 'coverage': f'Market Interest: {buzz}'},
                    {'name': 'Investor Sentiment', 'type': 'sentiment', 'coverage': 'Bond investor positioning'}
                ]
                
                reasoning = f"Bond market sentiment shows {trend} trend with {buzz} interest level. Analysis based on market data, investor positioning, and news sentiment."
                
                analysis = response.get('answer', f'Bond market sentiment is {trend}')[:500]
                
                return {
                    'search_score': min(100, max(0, score)),
                    'search_details': {
                        'trend_direction': trend,
                        'buzz_level': buzz,
                        'analysis': analysis
                    },
                    'search_sources': sources_list,
                    'search_reasoning': reasoning,
                    'search_confidence': 0.7,
                    'step': 'bond_search_complete'
                }
        except Exception as e:
            logger.error(f"Bond search sentiment error: {e}", exc_info=True)
        
        return {
            'search_score': 50,
            'search_details': {'trend_direction': 'stable', 'buzz_level': 'medium', 'analysis': 'Bond market sentiment is neutral with stable outlook.'},
            'search_sources': [{'name': 'Market Data', 'type': 'sentiment', 'coverage': 'Bond market analysis'}],
            'search_reasoning': 'Bond market sentiment is neutral',
            'search_confidence': 0.6,
            'step': 'bond_search_complete'
        }
    
    # ==================== END BOND ANALYSIS METHODS ====================
    
    # ==================== COMMODITY ANALYSIS METHODS ====================
    
    def qualitative_analysis_commodity(self, state: IScoreState) -> Dict:
        """Commodity Qualitative Analysis (15% weight) - Supply/Demand, Exchange Quality"""
        logger.info(f"I-Score Commodity Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.commodity_service import commodity_service
            commodity_data = commodity_service.analyze_for_iscore(symbol)
            
            if commodity_data.get('success'):
                exchange = commodity_data.get('exchange', 'MCX')
                category = commodity_data.get('category', 'Commodity')
                supply_outlook = commodity_data.get('supply_outlook', 'balanced')
                supply_score = commodity_data.get('supply_score', 50)
                global_benchmark = commodity_data.get('global_benchmark', '')
                seasonal_factor = commodity_data.get('seasonal_factor', '')
                
                score = supply_score
                
                if exchange == 'MCX':
                    score = min(100, score + 5)
                if category == 'Precious Metals':
                    score = min(100, score + 3)
                
                sources_list = [
                    {'name': 'MCX/APIdatafeed', 'type': 'exchange', 'coverage': f'Exchange: {exchange}'},
                    {'name': 'Supply Analysis', 'type': 'fundamental', 'coverage': f'Outlook: {supply_outlook}'},
                    {'name': 'Global Benchmark', 'type': 'reference', 'coverage': f'Benchmark: {global_benchmark}'}
                ]
                
                reasoning = f"Commodity traded on {exchange} with {supply_outlook} supply outlook. Category: {category}. Global benchmark: {global_benchmark}. Seasonal factor: {seasonal_factor}."
                
                return {
                    'qualitative_score': score,
                    'qualitative_details': {
                        'exchange': exchange,
                        'category': category,
                        'supply_outlook': supply_outlook,
                        'supply_score': supply_score,
                        'global_benchmark': global_benchmark,
                        'seasonal_factor': seasonal_factor,
                        'commodity_name': commodity_data.get('name', '')
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': 0.8,
                    'asset_name': commodity_data.get('name', symbol),
                    'step': 'commodity_qualitative_complete'
                }
        except Exception as e:
            logger.error(f"Commodity Qualitative analysis error: {e}")
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Commodity data unavailable'},
            'qualitative_sources': [{'name': 'MCX', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'qualitative_reasoning': 'Qualitative analysis unavailable for this commodity',
            'qualitative_confidence': 0.3,
            'step': 'commodity_qualitative_fallback'
        }
    
    def quantitative_analysis_commodity(self, state: IScoreState) -> Dict:
        """Commodity Quantitative Analysis (50% weight) - Price, Volatility, Volume"""
        logger.info(f"I-Score Commodity Node 3: Quantitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.commodity_service import commodity_service
            commodity_data = commodity_service.analyze_for_iscore(symbol)
            
            if commodity_data.get('success'):
                current_price = commodity_data.get('current_price', 0)
                previous_close = commodity_data.get('previous_close', 0)
                price_change_pct = commodity_data.get('price_change_pct', 0)
                volatility = commodity_data.get('volatility', 15)
                volatility_score = commodity_data.get('volatility_score', 50)
                volatility_risk = commodity_data.get('volatility_risk', 'Moderate')
                open_interest = commodity_data.get('open_interest', 0)
                volume = commodity_data.get('volume', 0)
                liquidity_score = commodity_data.get('liquidity_score', 50)
                margin_required = commodity_data.get('margin_required', 5)
                lot_size = commodity_data.get('lot_size', 100)
                unit = commodity_data.get('unit', '')
                
                score = (volatility_score * 0.4) + (liquidity_score * 0.35) + (55 * 0.25)
                
                if price_change_pct > 1:
                    score += 5
                elif price_change_pct < -1:
                    score -= 5
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'MCX/APIdatafeed', 'type': 'price_data', 'coverage': f'Price: ₹{current_price:,.2f} {unit}'},
                    {'name': 'Volatility Analysis', 'type': 'risk', 'coverage': f'Volatility: {volatility:.1f}% ({volatility_risk})'},
                    {'name': 'Volume Data', 'type': 'liquidity', 'coverage': f'Volume: {volume:,}, OI: {open_interest:,}'}
                ]
                
                reasoning = f"Commodity trading at ₹{current_price:,.2f} {unit} with {price_change_pct:.2f}% change. Volatility of {volatility:.1f}% indicates {volatility_risk.lower()} risk. Open Interest: {open_interest:,}, Volume: {volume:,}."
                
                return {
                    'current_price': current_price,
                    'previous_close': previous_close,
                    'price_change_pct': price_change_pct,
                    'market_status': 'live',
                    'quantitative_score': score,
                    'quantitative_details': {
                        'price': {
                            'current': current_price,
                            'previous_close': previous_close,
                            'change_pct': price_change_pct,
                            'unit': unit
                        },
                        'volatility': {
                            'value': volatility,
                            'score': volatility_score,
                            'risk': volatility_risk
                        },
                        'trading': {
                            'open_interest': open_interest,
                            'volume': volume,
                            'liquidity_score': liquidity_score,
                            'margin_required': margin_required,
                            'lot_size': lot_size
                        }
                    },
                    'quantitative_sources': sources_list,
                    'quantitative_reasoning': reasoning,
                    'quantitative_confidence': 0.8,
                    'step': 'commodity_quantitative_complete'
                }
        except Exception as e:
            logger.error(f"Commodity Quantitative analysis error: {e}")
        
        return {
            'quantitative_score': 50,
            'quantitative_details': {'error': 'Price data unavailable'},
            'quantitative_sources': [{'name': 'MCX', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'quantitative_reasoning': 'Quantitative analysis unavailable',
            'quantitative_confidence': 0.3,
            'step': 'commodity_quantitative_fallback'
        }
    
    def search_sentiment_commodity(self, state: IScoreState) -> Dict:
        """Commodity Search & Sentiment Analysis (10% weight)"""
        logger.info(f"I-Score Commodity Node 4: Search sentiment for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.commodity_service import commodity_service
            commodity_data = commodity_service.analyze_for_iscore(symbol)
            commodity_name = commodity_data.get('name', symbol) if commodity_data.get('success') else symbol
            category = commodity_data.get('category', 'Commodity') if commodity_data.get('success') else 'Commodity'
            
            if self.perplexity_service:
                query = f"Current market sentiment and outlook for {commodity_name} commodity in Indian market MCX. Include supply-demand dynamics, global price trends, and trading outlook."
                response = self.perplexity_service.ask_question(query)
                
                answer = response.get('answer', '').lower()
                
                if 'bullish' in answer or 'positive' in answer or 'strong demand' in answer:
                    trend = 'bullish'
                    score = 72
                elif 'bearish' in answer or 'negative' in answer or 'oversupply' in answer:
                    trend = 'bearish'
                    score = 38
                else:
                    trend = 'neutral'
                    score = 55
                
                if 'high demand' in answer:
                    buzz = 'high'
                    score += 8
                elif 'low demand' in answer:
                    buzz = 'low'
                    score -= 5
                else:
                    buzz = 'medium'
                
                sources_list = [
                    {'name': 'Perplexity Search', 'type': 'sentiment', 'coverage': f'Commodity: {commodity_name}'},
                    {'name': 'Global Markets', 'type': 'market_data', 'coverage': f'Category: {category}'},
                    {'name': 'MCX Analysis', 'type': 'exchange', 'coverage': 'Indian commodity market sentiment'}
                ]
                
                reasoning = f"Commodity sentiment for {commodity_name} shows {trend} outlook with {buzz} market interest based on supply-demand analysis and global price trends."
                
                analysis = response.get('answer', f'Commodity sentiment is {trend}')[:500]
                
                return {
                    'search_score': min(100, max(0, score)),
                    'search_details': {
                        'trend_direction': trend,
                        'buzz_level': buzz,
                        'analysis': analysis
                    },
                    'search_sources': sources_list,
                    'search_reasoning': reasoning,
                    'search_confidence': 0.7,
                    'step': 'commodity_search_complete'
                }
        except Exception as e:
            logger.error(f"Commodity search sentiment error: {e}", exc_info=True)
        
        return {
            'search_score': 50,
            'search_details': {'trend_direction': 'neutral', 'buzz_level': 'medium', 'analysis': 'Commodity sentiment analysis based on available market data.'},
            'search_sources': [{'name': 'Market Data', 'type': 'sentiment', 'coverage': 'Commodity market analysis'}],
            'search_reasoning': 'Commodity sentiment is neutral',
            'search_confidence': 0.6,
            'step': 'commodity_search_complete'
        }
    
    def trend_analysis_commodity(self, state: IScoreState) -> Dict:
        """Commodity Trend Analysis (25% weight) - Price trends, OI trends, Global correlation"""
        logger.info(f"I-Score Commodity Node 5: Trend analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.commodity_service import commodity_service
            commodity_data = commodity_service.analyze_for_iscore(symbol)
            
            if commodity_data.get('success'):
                trend = commodity_data.get('trend', 'neutral')
                trend_score = commodity_data.get('trend_score', 50)
                correlation_usd = commodity_data.get('correlation_usd', 0)
                volatility = commodity_data.get('volatility', 15)
                open_interest = commodity_data.get('open_interest', 5000)
                volume = commodity_data.get('volume', 2000)
                
                oi_trend = 'increasing' if open_interest > 5000 else 'stable' if open_interest > 2000 else 'decreasing'
                volume_trend = 'high' if volume > 5000 else 'moderate' if volume > 2000 else 'low'
                
                if trend == 'bullish' and oi_trend == 'increasing':
                    trend_score = min(100, trend_score + 8)
                elif trend == 'bearish' and oi_trend == 'increasing':
                    trend_score = max(0, trend_score - 5)
                
                if correlation_usd < -0.5:
                    usd_impact = 'Inverse USD correlation - benefits from weak dollar'
                elif correlation_usd > 0.3:
                    usd_impact = 'Positive USD correlation - follows dollar strength'
                else:
                    usd_impact = 'Low USD correlation - independent price action'
                
                sources_list = [
                    {'name': 'MCX/APIdatafeed', 'type': 'trend', 'coverage': f'Trend: {trend.capitalize()}'},
                    {'name': 'OI Analysis', 'type': 'positioning', 'coverage': f'OI Trend: {oi_trend}'},
                    {'name': 'USD Correlation', 'type': 'macro', 'coverage': f'Correlation: {correlation_usd:.2f}'}
                ]
                
                reasoning = f"Commodity shows {trend} trend with {oi_trend} open interest and {volume_trend} volume. {usd_impact}. Volatility: {volatility:.1f}%."
                
                return {
                    'trend_score': min(100, max(0, trend_score)),
                    'trend_details': {
                        'price_trend': trend,
                        'oi_trend': oi_trend,
                        'volume_trend': volume_trend,
                        'correlation_usd': correlation_usd,
                        'volatility': volatility,
                        'usd_impact': usd_impact
                    },
                    'trend_sources': sources_list,
                    'trend_reasoning': reasoning,
                    'trend_confidence': 0.75,
                    'step': 'commodity_trend_complete'
                }
        except Exception as e:
            logger.error(f"Commodity Trend analysis error: {e}")
        
        return {
            'trend_score': 50,
            'trend_details': {'error': 'Trend data unavailable'},
            'trend_sources': [{'name': 'MCX', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'trend_reasoning': 'Trend analysis unavailable',
            'trend_confidence': 0.3,
            'step': 'commodity_trend_fallback'
        }
    
    # ==================== END COMMODITY ANALYSIS METHODS ====================
    
    # ==================== CURRENCY ANALYSIS METHODS ====================
    
    def qualitative_analysis_currency(self, state: IScoreState) -> Dict:
        """Currency Qualitative Analysis (15% weight) - Central Bank Policy, Economic Fundamentals"""
        logger.info(f"I-Score Currency Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.currency_service import currency_service
            currency_data = currency_service.analyze_for_iscore(symbol)
            
            if currency_data.get('success'):
                exchange = currency_data.get('exchange', 'Global')
                category = currency_data.get('category', 'Major')
                base_currency = currency_data.get('base_currency', '')
                quote_currency = currency_data.get('quote_currency', '')
                central_bank_stance = currency_data.get('central_bank_stance', 'neutral')
                stance_score = currency_data.get('stance_score', 50)
                interest_rate_diff = currency_data.get('interest_rate_diff', 0)
                carry_trade_score = currency_data.get('carry_trade_score', 50)
                
                score = (stance_score * 0.4) + (carry_trade_score * 0.6)
                
                if exchange == 'NSE':
                    score = min(100, score + 3)
                if category == 'Major':
                    score = min(100, score + 2)
                
                sources_list = [
                    {'name': 'TraderMade/ExchangeRate-API', 'type': 'forex', 'coverage': f'Pair: {base_currency}/{quote_currency}'},
                    {'name': 'Central Bank Policy', 'type': 'monetary', 'coverage': f'Stance: {central_bank_stance.capitalize()}'},
                    {'name': 'Interest Rate Differential', 'type': 'carry', 'coverage': f'IRD: {interest_rate_diff:+.2f}%'}
                ]
                
                reasoning = f"Currency pair {symbol} with {central_bank_stance} central bank stance. Interest rate differential: {interest_rate_diff:+.2f}%. Carry trade attractiveness: {carry_trade_score}/100."
                
                return {
                    'qualitative_score': score,
                    'qualitative_details': {
                        'exchange': exchange,
                        'category': category,
                        'base_currency': base_currency,
                        'quote_currency': quote_currency,
                        'central_bank_stance': central_bank_stance,
                        'stance_score': stance_score,
                        'interest_rate_diff': interest_rate_diff,
                        'carry_trade_score': carry_trade_score
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': 0.8,
                    'asset_name': currency_data.get('name', symbol),
                    'step': 'currency_qualitative_complete'
                }
        except Exception as e:
            logger.error(f"Currency Qualitative analysis error: {e}")
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Currency data unavailable'},
            'qualitative_sources': [{'name': 'Forex', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'qualitative_reasoning': 'Qualitative analysis unavailable for this currency pair',
            'qualitative_confidence': 0.3,
            'step': 'currency_qualitative_fallback'
        }
    
    def quantitative_analysis_currency(self, state: IScoreState) -> Dict:
        """Currency Quantitative Analysis (50% weight) - Rate, Volatility, Spread, Liquidity"""
        logger.info(f"I-Score Currency Node 3: Quantitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.currency_service import currency_service
            currency_data = currency_service.analyze_for_iscore(symbol)
            
            if currency_data.get('success'):
                current_rate = currency_data.get('current_rate', 0)
                previous_close = currency_data.get('previous_close', 0)
                rate_change_pct = currency_data.get('rate_change_pct', 0)
                bid = currency_data.get('bid', 0)
                ask = currency_data.get('ask', 0)
                spread = currency_data.get('spread', 0)
                spread_pct = currency_data.get('spread_pct', 0)
                volatility = currency_data.get('volatility', 6)
                volatility_score = currency_data.get('volatility_score', 50)
                volatility_risk = currency_data.get('volatility_risk', 'Moderate')
                volume = currency_data.get('volume', 0)
                liquidity_score = currency_data.get('liquidity_score', 50)
                margin_required = currency_data.get('margin_required', 3)
                lot_size = currency_data.get('lot_size', 1000)
                
                score = (volatility_score * 0.35) + (liquidity_score * 0.45) + (55 * 0.20)
                
                if spread_pct < 0.02:
                    score += 8
                elif spread_pct > 0.1:
                    score -= 5
                
                score = min(100, max(0, score))
                
                sources_list = [
                    {'name': 'TraderMade/ExchangeRate-API', 'type': 'rate_data', 'coverage': f'Rate: {current_rate:.4f}'},
                    {'name': 'Spread Analysis', 'type': 'cost', 'coverage': f'Spread: {spread:.4f} ({spread_pct:.4f}%)'},
                    {'name': 'Volatility Analysis', 'type': 'risk', 'coverage': f'Volatility: {volatility:.1f}% ({volatility_risk})'}
                ]
                
                reasoning = f"Currency pair trading at {current_rate:.4f} with {rate_change_pct:+.2f}% change. Spread: {spread:.4f} ({spread_pct:.4f}%). Volatility: {volatility:.1f}% ({volatility_risk} risk)."
                
                return {
                    'current_price': current_rate,
                    'previous_close': previous_close,
                    'price_change_pct': rate_change_pct,
                    'market_status': 'live',
                    'quantitative_score': score,
                    'quantitative_details': {
                        'rate': {
                            'current': current_rate,
                            'previous_close': previous_close,
                            'change_pct': rate_change_pct,
                            'bid': bid,
                            'ask': ask
                        },
                        'spread': {
                            'value': spread,
                            'percentage': spread_pct
                        },
                        'volatility': {
                            'value': volatility,
                            'score': volatility_score,
                            'risk': volatility_risk
                        },
                        'trading': {
                            'volume': volume,
                            'liquidity_score': liquidity_score,
                            'margin_required': margin_required,
                            'lot_size': lot_size
                        }
                    },
                    'quantitative_sources': sources_list,
                    'quantitative_reasoning': reasoning,
                    'quantitative_confidence': 0.85,
                    'step': 'currency_quantitative_complete'
                }
        except Exception as e:
            logger.error(f"Currency Quantitative analysis error: {e}")
        
        return {
            'quantitative_score': 50,
            'quantitative_details': {'error': 'Rate data unavailable'},
            'quantitative_sources': [{'name': 'Forex', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'quantitative_reasoning': 'Quantitative analysis unavailable',
            'quantitative_confidence': 0.3,
            'step': 'currency_quantitative_fallback'
        }
    
    def search_sentiment_currency(self, state: IScoreState) -> Dict:
        """Currency Search & Sentiment Analysis (10% weight)"""
        logger.info(f"I-Score Currency Node 4: Search sentiment for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.currency_service import currency_service
            currency_data = currency_service.analyze_for_iscore(symbol)
            currency_name = currency_data.get('name', symbol) if currency_data.get('success') else symbol
            base_currency = currency_data.get('base_currency', '') if currency_data.get('success') else ''
            quote_currency = currency_data.get('quote_currency', '') if currency_data.get('success') else ''
            
            if self.perplexity_service:
                query = f"Current forex market outlook for {currency_name} ({symbol}). Include central bank policy impact, economic indicators, and short-term trading outlook."
                response = self.perplexity_service.ask_question(query)
                
                answer = response.get('answer', '').lower()
                
                if 'bullish' in answer or 'strengthen' in answer or 'appreciate' in answer:
                    trend = 'bullish'
                    score = 70
                elif 'bearish' in answer or 'weaken' in answer or 'depreciate' in answer:
                    trend = 'bearish'
                    score = 40
                else:
                    trend = 'neutral'
                    score = 55
                
                if 'rate hike' in answer or 'hawkish' in answer:
                    buzz = 'high'
                    score += 5
                elif 'rate cut' in answer or 'dovish' in answer:
                    buzz = 'high'
                    score -= 3
                else:
                    buzz = 'medium'
                
                sources_list = [
                    {'name': 'Perplexity Search', 'type': 'sentiment', 'coverage': f'Currency: {currency_name}'},
                    {'name': 'Central Bank Watch', 'type': 'policy', 'coverage': f'Base: {base_currency}'},
                    {'name': 'Forex Analysis', 'type': 'market_data', 'coverage': 'Currency market sentiment'}
                ]
                
                reasoning = f"Forex sentiment for {symbol} shows {trend} outlook with {buzz} market attention based on central bank policy and economic fundamentals."
                
                analysis = response.get('answer', f'Currency sentiment is {trend}')[:500]
                
                return {
                    'search_score': min(100, max(0, score)),
                    'search_details': {
                        'trend_direction': trend,
                        'buzz_level': buzz,
                        'analysis': analysis
                    },
                    'search_sources': sources_list,
                    'search_reasoning': reasoning,
                    'search_confidence': 0.7,
                    'step': 'currency_search_complete'
                }
        except Exception as e:
            logger.error(f"Currency search sentiment error: {e}", exc_info=True)
        
        return {
            'search_score': 50,
            'search_details': {'trend_direction': 'neutral', 'buzz_level': 'medium', 'analysis': 'Currency sentiment analysis based on available market data.'},
            'search_sources': [{'name': 'Forex Data', 'type': 'sentiment', 'coverage': 'Currency market analysis'}],
            'search_reasoning': 'Currency sentiment is neutral',
            'search_confidence': 0.6,
            'step': 'currency_search_complete'
        }
    
    def trend_analysis_currency(self, state: IScoreState) -> Dict:
        """Currency Trend Analysis (25% weight) - Rate trends, DXY correlation, Carry trade"""
        logger.info(f"I-Score Currency Node 5: Trend analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.currency_service import currency_service
            currency_data = currency_service.analyze_for_iscore(symbol)
            
            if currency_data.get('success'):
                trend = currency_data.get('trend', 'neutral')
                trend_score = currency_data.get('trend_score', 50)
                correlation_dxy = currency_data.get('correlation_dxy', 0)
                volatility = currency_data.get('volatility', 6)
                carry_trade_score = currency_data.get('carry_trade_score', 50)
                interest_rate_diff = currency_data.get('interest_rate_diff', 0)
                rbi_reference = currency_data.get('rbi_reference')
                
                if carry_trade_score > 65:
                    carry_outlook = 'Favorable carry trade opportunity'
                    trend_score = min(100, trend_score + 5)
                elif carry_trade_score < 45:
                    carry_outlook = 'Unfavorable carry trade conditions'
                    trend_score = max(0, trend_score - 3)
                else:
                    carry_outlook = 'Neutral carry trade conditions'
                
                if abs(correlation_dxy) > 0.7:
                    dxy_impact = f"Strong {'positive' if correlation_dxy > 0 else 'inverse'} DXY correlation"
                else:
                    dxy_impact = "Low DXY correlation - independent movement"
                
                sources_list = [
                    {'name': 'TraderMade/ExchangeRate-API', 'type': 'trend', 'coverage': f'Trend: {trend.capitalize()}'},
                    {'name': 'DXY Correlation', 'type': 'macro', 'coverage': f'Correlation: {correlation_dxy:.2f}'},
                    {'name': 'Carry Trade Analysis', 'type': 'strategy', 'coverage': f'IRD: {interest_rate_diff:+.2f}%'}
                ]
                
                if rbi_reference:
                    sources_list.append({'name': 'RBI Reference', 'type': 'official', 'coverage': f'RBI Rate: {rbi_reference}'})
                
                reasoning = f"Currency shows {trend} trend. {dxy_impact}. {carry_outlook}. Interest rate differential: {interest_rate_diff:+.2f}%."
                
                return {
                    'trend_score': min(100, max(0, trend_score)),
                    'trend_details': {
                        'price_trend': trend,
                        'correlation_dxy': correlation_dxy,
                        'dxy_impact': dxy_impact,
                        'carry_trade_score': carry_trade_score,
                        'carry_outlook': carry_outlook,
                        'interest_rate_diff': interest_rate_diff,
                        'volatility': volatility,
                        'rbi_reference': rbi_reference
                    },
                    'trend_sources': sources_list,
                    'trend_reasoning': reasoning,
                    'trend_confidence': 0.75,
                    'step': 'currency_trend_complete'
                }
        except Exception as e:
            logger.error(f"Currency Trend analysis error: {e}")
        
        return {
            'trend_score': 50,
            'trend_details': {'error': 'Trend data unavailable'},
            'trend_sources': [{'name': 'Forex', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'trend_reasoning': 'Trend analysis unavailable',
            'trend_confidence': 0.3,
            'step': 'currency_trend_fallback'
        }
    
    # ==================== END CURRENCY ANALYSIS METHODS ====================
    
    # ==================== OPTIONS ANALYSIS METHODS ====================
    
    def qualitative_analysis_options(self, state: IScoreState) -> Dict:
        """Options Qualitative Analysis (15% weight) - IV percentile, PCR sentiment, Max Pain"""
        logger.info(f"I-Score Options Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.options_service import options_service
            options_data = options_service.analyze_for_iscore(symbol)
            
            if options_data.get('success'):
                iv_percentile = options_data.get('iv_percentile', 50)
                pcr_oi = options_data.get('pcr_oi', 1.0)
                max_pain = options_data.get('max_pain', 0)
                spot = options_data.get('spot_price', 0)
                trend = options_data.get('trend', 'neutral')
                
                if pcr_oi > 1.2:
                    pcr_sentiment = 'bearish'
                    sentiment_adj = -5
                elif pcr_oi < 0.8:
                    pcr_sentiment = 'bullish'
                    sentiment_adj = 5
                else:
                    pcr_sentiment = 'neutral'
                    sentiment_adj = 0
                
                if iv_percentile > 80:
                    iv_view = 'overvalued'
                    iv_adj = -8
                elif iv_percentile < 20:
                    iv_view = 'undervalued'
                    iv_adj = 8
                else:
                    iv_view = 'fair'
                    iv_adj = 0
                
                max_pain_diff = ((spot - max_pain) / max_pain * 100) if max_pain else 0
                if abs(max_pain_diff) < 1:
                    mp_view = 'at_max_pain'
                    mp_adj = 0
                elif max_pain_diff > 2:
                    mp_view = 'above_max_pain'
                    mp_adj = -3
                else:
                    mp_view = 'below_max_pain'
                    mp_adj = 3
                
                base_score = 55 if trend == 'bullish' else (45 if trend == 'bearish' else 50)
                score = base_score + sentiment_adj + iv_adj + mp_adj
                
                sources_list = [
                    {'name': 'Option Chain', 'type': 'derivatives', 'coverage': f'PCR OI: {pcr_oi:.2f}'},
                    {'name': 'Implied Volatility', 'type': 'volatility', 'coverage': f'IV Percentile: {iv_percentile}%'},
                    {'name': 'Max Pain', 'type': 'analysis', 'coverage': f'Max Pain: {max_pain}'}
                ]
                
                reasoning = f"Options sentiment is {pcr_sentiment} (PCR: {pcr_oi:.2f}). IV is {iv_view} at {iv_percentile}th percentile. Spot is {mp_view.replace('_', ' ')}."
                
                return {
                    'qualitative_score': min(100, max(0, score)),
                    'qualitative_details': {
                        'pcr_oi': pcr_oi,
                        'pcr_sentiment': pcr_sentiment,
                        'iv_percentile': iv_percentile,
                        'iv_view': iv_view,
                        'max_pain': max_pain,
                        'max_pain_diff': round(max_pain_diff, 2),
                        'trend': trend
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': 0.8,
                    'step': 'options_qualitative_complete'
                }
        except Exception as e:
            logger.error(f"Options qualitative analysis error: {e}", exc_info=True)
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Options data unavailable'},
            'qualitative_sources': [{'name': 'Options Chain', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'qualitative_reasoning': 'Default score due to data unavailability',
            'qualitative_confidence': 0.3,
            'step': 'options_qualitative_fallback'
        }
    
    def quantitative_analysis_options(self, state: IScoreState) -> Dict:
        """Options Quantitative Analysis (50% weight) - Greeks, OI, Volume"""
        logger.info(f"I-Score Options Node 3: Quantitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.options_service import options_service
            options_data = options_service.analyze_for_iscore(symbol)
            
            if options_data.get('success'):
                atm_iv = options_data.get('atm_iv', 15)
                avg_iv = options_data.get('avg_iv', 15)
                total_call_oi = options_data.get('total_call_oi', 0)
                total_put_oi = options_data.get('total_put_oi', 0)
                pcr_volume = options_data.get('pcr_volume', 1.0)
                spot = options_data.get('spot_price', 0)
                change_pct = options_data.get('price_change_pct', 0)
                
                option_chain = options_data.get('option_chain', {})
                avg_delta = 0
                avg_gamma = 0
                avg_theta = 0
                avg_vega = 0
                count = 0
                
                for key, opt in option_chain.items():
                    avg_delta += abs(opt.get('delta', 0))
                    avg_gamma += opt.get('gamma', 0)
                    avg_theta += opt.get('theta', 0)
                    avg_vega += opt.get('vega', 0)
                    count += 1
                
                if count:
                    avg_delta /= count
                    avg_gamma /= count
                    avg_theta /= count
                    avg_vega /= count
                
                iv_score = 50
                if atm_iv < 12:
                    iv_score = 65
                elif atm_iv > 25:
                    iv_score = 35
                
                if avg_gamma > 0.0015:
                    gamma_signal = 'high_gamma'
                    gamma_adj = 5
                else:
                    gamma_signal = 'normal_gamma'
                    gamma_adj = 0
                
                momentum_score = 50 + (change_pct * 3)
                momentum_score = min(80, max(20, momentum_score))
                
                score = (iv_score * 0.4) + (momentum_score * 0.4) + (50 + gamma_adj) * 0.2
                
                sources_list = [
                    {'name': 'Greeks Analysis', 'type': 'technical', 'coverage': f'Delta: {avg_delta:.3f}, Gamma: {avg_gamma:.4f}'},
                    {'name': 'IV Analysis', 'type': 'volatility', 'coverage': f'ATM IV: {atm_iv}%, Avg IV: {avg_iv}%'},
                    {'name': 'OI Data', 'type': 'volume', 'coverage': f'Call OI: {total_call_oi:,}, Put OI: {total_put_oi:,}'}
                ]
                
                reasoning = f"ATM IV at {atm_iv}% with {gamma_signal}. Spot change {change_pct:+.2f}%. PCR Volume: {pcr_volume:.2f}."
                
                return {
                    'quantitative_score': min(100, max(0, score)),
                    'quantitative_details': {
                        'atm_iv': atm_iv,
                        'avg_iv': avg_iv,
                        'avg_delta': round(avg_delta, 4),
                        'avg_gamma': round(avg_gamma, 5),
                        'avg_theta': round(avg_theta, 2),
                        'avg_vega': round(avg_vega, 2),
                        'total_call_oi': total_call_oi,
                        'total_put_oi': total_put_oi,
                        'pcr_volume': pcr_volume,
                        'gamma_signal': gamma_signal
                    },
                    'quantitative_sources': sources_list,
                    'quantitative_reasoning': reasoning,
                    'quantitative_confidence': 0.85,
                    'step': 'options_quantitative_complete'
                }
        except Exception as e:
            logger.error(f"Options quantitative analysis error: {e}", exc_info=True)
        
        return {
            'quantitative_score': 50,
            'quantitative_details': {'error': 'Options analysis failed'},
            'quantitative_sources': [{'name': 'Options', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'quantitative_reasoning': 'Default score due to analysis failure',
            'quantitative_confidence': 0.3,
            'step': 'options_quantitative_fallback'
        }
    
    def search_sentiment_options(self, state: IScoreState) -> Dict:
        """Options Search Sentiment Analysis (10% weight) - Market sentiment from news/social"""
        logger.info(f"I-Score Options Node 4: Search sentiment for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.options_service import options_service
            options_data = options_service.analyze_for_iscore(symbol)
            
            if options_data.get('success'):
                pcr_oi = options_data.get('pcr_oi', 1.0)
                iv_percentile = options_data.get('iv_percentile', 50)
                trend = options_data.get('trend', 'neutral')
                
                if pcr_oi > 1.3:
                    sentiment = 'bearish'
                    score = 35
                elif pcr_oi < 0.7:
                    sentiment = 'bullish'
                    score = 65
                else:
                    sentiment = 'neutral'
                    score = 50
                
                if iv_percentile > 70:
                    fear_level = 'high'
                    score -= 5
                elif iv_percentile < 30:
                    fear_level = 'low'
                    score += 5
                else:
                    fear_level = 'normal'
                
                sources_list = [
                    {'name': 'PCR Sentiment', 'type': 'derivatives', 'coverage': f'Sentiment: {sentiment.capitalize()}'},
                    {'name': 'IV Fear Gauge', 'type': 'volatility', 'coverage': f'Fear Level: {fear_level.capitalize()}'}
                ]
                
                reasoning = f"Options market sentiment is {sentiment} based on PCR. Fear level is {fear_level} based on IV percentile."
                
                return {
                    'search_score': min(100, max(0, score)),
                    'search_details': {
                        'sentiment': sentiment,
                        'fear_level': fear_level,
                        'pcr_based_sentiment': pcr_oi
                    },
                    'search_sources': sources_list,
                    'search_reasoning': reasoning,
                    'search_confidence': 0.7,
                    'step': 'options_search_complete'
                }
        except Exception as e:
            logger.error(f"Options search sentiment error: {e}", exc_info=True)
        
        return {
            'search_score': 50,
            'search_details': {'sentiment': 'neutral', 'fear_level': 'normal'},
            'search_sources': [{'name': 'Options Data', 'type': 'sentiment', 'coverage': 'Market sentiment analysis'}],
            'search_reasoning': 'Options sentiment is neutral',
            'search_confidence': 0.6,
            'step': 'options_search_complete'
        }
    
    def trend_analysis_options(self, state: IScoreState) -> Dict:
        """Options Trend Analysis (25% weight) - OI trends, IV trends, Greeks trends"""
        logger.info(f"I-Score Options Node 5: Trend analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.options_service import options_service
            options_data = options_service.analyze_for_iscore(symbol)
            
            if options_data.get('success'):
                trend = options_data.get('trend', 'neutral')
                pcr_oi = options_data.get('pcr_oi', 1.0)
                iv_percentile = options_data.get('iv_percentile', 50)
                change_pct = options_data.get('price_change_pct', 0)
                
                if trend == 'bullish' and pcr_oi < 0.9:
                    trend_signal = 'strong_bullish'
                    score = 70
                elif trend == 'bearish' and pcr_oi > 1.1:
                    trend_signal = 'strong_bearish'
                    score = 30
                elif trend == 'bullish':
                    trend_signal = 'mild_bullish'
                    score = 60
                elif trend == 'bearish':
                    trend_signal = 'mild_bearish'
                    score = 40
                else:
                    trend_signal = 'neutral'
                    score = 50
                
                if iv_percentile > 60:
                    iv_trend = 'expanding'
                    iv_outlook = 'Elevated volatility suggests caution'
                elif iv_percentile < 40:
                    iv_trend = 'contracting'
                    iv_outlook = 'Low volatility may precede breakout'
                else:
                    iv_trend = 'stable'
                    iv_outlook = 'Volatility is within normal range'
                
                sources_list = [
                    {'name': 'Trend Analysis', 'type': 'trend', 'coverage': f'Signal: {trend_signal.replace("_", " ").title()}'},
                    {'name': 'IV Trend', 'type': 'volatility', 'coverage': f'IV Trend: {iv_trend.capitalize()}'},
                    {'name': 'Price Action', 'type': 'technical', 'coverage': f'Change: {change_pct:+.2f}%'}
                ]
                
                reasoning = f"Options trend is {trend_signal.replace('_', ' ')}. {iv_outlook}. Price change: {change_pct:+.2f}%."
                
                return {
                    'trend_score': min(100, max(0, score)),
                    'trend_details': {
                        'trend_signal': trend_signal,
                        'iv_trend': iv_trend,
                        'iv_outlook': iv_outlook,
                        'iv_percentile': iv_percentile,
                        'pcr_oi': pcr_oi
                    },
                    'trend_sources': sources_list,
                    'trend_reasoning': reasoning,
                    'trend_confidence': 0.75,
                    'step': 'options_trend_complete'
                }
        except Exception as e:
            logger.error(f"Options Trend analysis error: {e}")
        
        return {
            'trend_score': 50,
            'trend_details': {'error': 'Trend data unavailable'},
            'trend_sources': [{'name': 'Options', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'trend_reasoning': 'Trend analysis unavailable',
            'trend_confidence': 0.3,
            'step': 'options_trend_fallback'
        }
    
    # ==================== END OPTIONS ANALYSIS METHODS ====================
    
    # ==================== FUTURES ANALYSIS METHODS ====================
    
    def qualitative_analysis_futures(self, state: IScoreState) -> Dict:
        """Futures Qualitative Analysis (15% weight) - Basis, Rollover, COI Buildup"""
        logger.info(f"I-Score Futures Node 2: Qualitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.futures_service import futures_service
            futures_data = futures_service.analyze_for_iscore(symbol)
            
            if futures_data.get('success'):
                basis = futures_data.get('basis', 0)
                basis_pct = futures_data.get('basis_pct', 0)
                coi_buildup = futures_data.get('coi_buildup', 'neutral')
                rollover_cost = futures_data.get('rollover_cost', 0.1)
                margin_required = futures_data.get('margin_required', 15)
                trend = futures_data.get('trend', 'neutral')
                
                if basis_pct > 0.3:
                    basis_view = 'contango'
                    basis_adj = 5
                elif basis_pct < -0.1:
                    basis_view = 'backwardation'
                    basis_adj = -3
                else:
                    basis_view = 'normal'
                    basis_adj = 0
                
                if coi_buildup == 'long_buildup':
                    coi_adj = 10
                    coi_sentiment = 'bullish'
                elif coi_buildup == 'short_buildup':
                    coi_adj = -10
                    coi_sentiment = 'bearish'
                elif coi_buildup == 'short_covering':
                    coi_adj = 5
                    coi_sentiment = 'mildly_bullish'
                elif coi_buildup == 'long_unwinding':
                    coi_adj = -5
                    coi_sentiment = 'mildly_bearish'
                else:
                    coi_adj = 0
                    coi_sentiment = 'neutral'
                
                base_score = 55 if trend == 'bullish' else (45 if trend == 'bearish' else 50)
                score = base_score + basis_adj + coi_adj
                
                sources_list = [
                    {'name': 'Basis Analysis', 'type': 'derivatives', 'coverage': f'Basis: {basis:+.2f} ({basis_view})'},
                    {'name': 'COI Analysis', 'type': 'oi', 'coverage': f'Buildup: {coi_buildup.replace("_", " ").title()}'},
                    {'name': 'Rollover', 'type': 'cost', 'coverage': f'Rollover Cost: {rollover_cost}%'}
                ]
                
                reasoning = f"Futures showing {basis_view} with {basis:+.2f} basis. COI indicates {coi_sentiment.replace('_', ' ')} sentiment. Rollover cost at {rollover_cost}%."
                
                return {
                    'qualitative_score': min(100, max(0, score)),
                    'qualitative_details': {
                        'basis': basis,
                        'basis_pct': basis_pct,
                        'basis_view': basis_view,
                        'coi_buildup': coi_buildup,
                        'coi_sentiment': coi_sentiment,
                        'rollover_cost': rollover_cost,
                        'margin_required': margin_required
                    },
                    'qualitative_sources': sources_list,
                    'qualitative_reasoning': reasoning,
                    'qualitative_confidence': 0.8,
                    'step': 'futures_qualitative_complete'
                }
        except Exception as e:
            logger.error(f"Futures qualitative analysis error: {e}", exc_info=True)
        
        return {
            'qualitative_score': 50,
            'qualitative_details': {'error': 'Futures data unavailable'},
            'qualitative_sources': [{'name': 'Futures', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'qualitative_reasoning': 'Default score due to data unavailability',
            'qualitative_confidence': 0.3,
            'step': 'futures_qualitative_fallback'
        }
    
    def quantitative_analysis_futures(self, state: IScoreState) -> Dict:
        """Futures Quantitative Analysis (50% weight) - Price, OI, Volume, Technical Levels"""
        logger.info(f"I-Score Futures Node 3: Quantitative analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.futures_service import futures_service
            futures_data = futures_service.analyze_for_iscore(symbol)
            
            if futures_data.get('success'):
                current_price = futures_data.get('current_price', 0)
                change_pct = futures_data.get('price_change_pct', 0)
                oi = futures_data.get('open_interest', 0)
                oi_change_pct = futures_data.get('oi_change_pct', 0)
                volume = futures_data.get('volume', 0)
                support_levels = futures_data.get('support_levels', [])
                resistance_levels = futures_data.get('resistance_levels', [])
                day_high = futures_data.get('day_high', 0)
                day_low = futures_data.get('day_low', 0)
                
                momentum_score = 50 + (change_pct * 5)
                momentum_score = min(80, max(20, momentum_score))
                
                if oi_change_pct > 5:
                    oi_signal = 'significant_oi_addition'
                    oi_adj = 8
                elif oi_change_pct < -5:
                    oi_signal = 'significant_oi_reduction'
                    oi_adj = -5
                else:
                    oi_signal = 'normal_oi_change'
                    oi_adj = 0
                
                if support_levels and current_price:
                    nearest_support = min(support_levels, key=lambda x: abs(current_price - x))
                    support_distance = ((current_price - nearest_support) / current_price) * 100
                else:
                    support_distance = 0
                
                if resistance_levels and current_price:
                    nearest_resistance = min(resistance_levels, key=lambda x: abs(current_price - x))
                    resistance_distance = ((nearest_resistance - current_price) / current_price) * 100
                else:
                    resistance_distance = 0
                
                if support_distance < 0.5 and resistance_distance > 1:
                    level_position = 'near_support'
                    level_adj = 5
                elif resistance_distance < 0.5 and support_distance > 1:
                    level_position = 'near_resistance'
                    level_adj = -3
                else:
                    level_position = 'between_levels'
                    level_adj = 0
                
                score = (momentum_score * 0.5) + (50 + oi_adj) * 0.3 + (50 + level_adj) * 0.2
                
                sources_list = [
                    {'name': 'Price Action', 'type': 'technical', 'coverage': f'Price: {current_price:,.2f} ({change_pct:+.2f}%)'},
                    {'name': 'OI Analysis', 'type': 'oi', 'coverage': f'OI: {oi:,} ({oi_change_pct:+.1f}%)'},
                    {'name': 'Technical Levels', 'type': 'support_resistance', 'coverage': f'Position: {level_position.replace("_", " ").title()}'}
                ]
                
                reasoning = f"Price at {current_price:,.2f} with {change_pct:+.2f}% change. {oi_signal.replace('_', ' ').title()}. Currently {level_position.replace('_', ' ')}."
                
                return {
                    'quantitative_score': min(100, max(0, score)),
                    'quantitative_details': {
                        'current_price': current_price,
                        'price_change_pct': change_pct,
                        'day_high': day_high,
                        'day_low': day_low,
                        'open_interest': oi,
                        'oi_change_pct': oi_change_pct,
                        'oi_signal': oi_signal,
                        'volume': volume,
                        'support_levels': support_levels,
                        'resistance_levels': resistance_levels,
                        'level_position': level_position
                    },
                    'quantitative_sources': sources_list,
                    'quantitative_reasoning': reasoning,
                    'quantitative_confidence': 0.85,
                    'step': 'futures_quantitative_complete'
                }
        except Exception as e:
            logger.error(f"Futures quantitative analysis error: {e}", exc_info=True)
        
        return {
            'quantitative_score': 50,
            'quantitative_details': {'error': 'Futures analysis failed'},
            'quantitative_sources': [{'name': 'Futures', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'quantitative_reasoning': 'Default score due to analysis failure',
            'quantitative_confidence': 0.3,
            'step': 'futures_quantitative_fallback'
        }
    
    def search_sentiment_futures(self, state: IScoreState) -> Dict:
        """Futures Search Sentiment Analysis (10% weight) - Market sentiment"""
        logger.info(f"I-Score Futures Node 4: Search sentiment for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.futures_service import futures_service
            futures_data = futures_service.analyze_for_iscore(symbol)
            
            if futures_data.get('success'):
                coi_buildup = futures_data.get('coi_buildup', 'neutral')
                trend = futures_data.get('trend', 'neutral')
                oi_change_pct = futures_data.get('oi_change_pct', 0)
                
                if coi_buildup in ['long_buildup', 'short_covering'] and trend == 'bullish':
                    sentiment = 'bullish'
                    score = 65
                elif coi_buildup in ['short_buildup', 'long_unwinding'] and trend == 'bearish':
                    sentiment = 'bearish'
                    score = 35
                elif coi_buildup == 'long_buildup':
                    sentiment = 'mildly_bullish'
                    score = 58
                elif coi_buildup == 'short_buildup':
                    sentiment = 'mildly_bearish'
                    score = 42
                else:
                    sentiment = 'neutral'
                    score = 50
                
                if abs(oi_change_pct) > 10:
                    activity = 'high'
                elif abs(oi_change_pct) > 5:
                    activity = 'moderate'
                else:
                    activity = 'low'
                
                sources_list = [
                    {'name': 'COI Sentiment', 'type': 'derivatives', 'coverage': f'Sentiment: {sentiment.replace("_", " ").title()}'},
                    {'name': 'Activity Level', 'type': 'volume', 'coverage': f'Activity: {activity.capitalize()}'}
                ]
                
                reasoning = f"Futures market sentiment is {sentiment.replace('_', ' ')} based on COI buildup. Trading activity is {activity}."
                
                return {
                    'search_score': min(100, max(0, score)),
                    'search_details': {
                        'sentiment': sentiment,
                        'activity_level': activity,
                        'coi_buildup': coi_buildup
                    },
                    'search_sources': sources_list,
                    'search_reasoning': reasoning,
                    'search_confidence': 0.7,
                    'step': 'futures_search_complete'
                }
        except Exception as e:
            logger.error(f"Futures search sentiment error: {e}", exc_info=True)
        
        return {
            'search_score': 50,
            'search_details': {'sentiment': 'neutral', 'activity_level': 'moderate'},
            'search_sources': [{'name': 'Futures Data', 'type': 'sentiment', 'coverage': 'Market sentiment analysis'}],
            'search_reasoning': 'Futures sentiment is neutral',
            'search_confidence': 0.6,
            'step': 'futures_search_complete'
        }
    
    def trend_analysis_futures(self, state: IScoreState) -> Dict:
        """Futures Trend Analysis (25% weight) - Price trend, OI trend, Expiry considerations"""
        logger.info(f"I-Score Futures Node 5: Trend analysis for {state['symbol']}")
        
        symbol = state['symbol']
        
        try:
            from services.futures_service import futures_service
            futures_data = futures_service.analyze_for_iscore(symbol)
            
            if futures_data.get('success'):
                trend = futures_data.get('trend', 'neutral')
                coi_buildup = futures_data.get('coi_buildup', 'neutral')
                days_to_expiry = futures_data.get('days_to_expiry', 30)
                basis_pct = futures_data.get('basis_pct', 0)
                change_pct = futures_data.get('price_change_pct', 0)
                
                if trend == 'bullish' and coi_buildup == 'long_buildup':
                    trend_signal = 'strong_bullish'
                    score = 72
                elif trend == 'bearish' and coi_buildup == 'short_buildup':
                    trend_signal = 'strong_bearish'
                    score = 28
                elif trend == 'bullish':
                    trend_signal = 'mild_bullish'
                    score = 60
                elif trend == 'bearish':
                    trend_signal = 'mild_bearish'
                    score = 40
                else:
                    trend_signal = 'neutral'
                    score = 50
                
                if days_to_expiry <= 3:
                    expiry_view = 'near_expiry'
                    expiry_note = 'Very near expiry - expect high volatility'
                elif days_to_expiry <= 7:
                    expiry_view = 'expiry_week'
                    expiry_note = 'Expiry week - increased activity expected'
                elif days_to_expiry <= 15:
                    expiry_view = 'mid_series'
                    expiry_note = 'Mid-series - normal trading conditions'
                else:
                    expiry_view = 'new_series'
                    expiry_note = 'New series - rollover considerations apply'
                
                sources_list = [
                    {'name': 'Trend Analysis', 'type': 'trend', 'coverage': f'Signal: {trend_signal.replace("_", " ").title()}'},
                    {'name': 'COI Trend', 'type': 'oi', 'coverage': f'COI: {coi_buildup.replace("_", " ").title()}'},
                    {'name': 'Expiry Status', 'type': 'expiry', 'coverage': f'{days_to_expiry} days to expiry'}
                ]
                
                reasoning = f"Futures trend is {trend_signal.replace('_', ' ')}. {expiry_note}. Basis at {basis_pct:+.2f}%."
                
                return {
                    'trend_score': min(100, max(0, score)),
                    'trend_details': {
                        'trend_signal': trend_signal,
                        'coi_buildup': coi_buildup,
                        'days_to_expiry': days_to_expiry,
                        'expiry_view': expiry_view,
                        'expiry_note': expiry_note,
                        'basis_pct': basis_pct
                    },
                    'trend_sources': sources_list,
                    'trend_reasoning': reasoning,
                    'trend_confidence': 0.75,
                    'step': 'futures_trend_complete'
                }
        except Exception as e:
            logger.error(f"Futures Trend analysis error: {e}")
        
        return {
            'trend_score': 50,
            'trend_details': {'error': 'Trend data unavailable'},
            'trend_sources': [{'name': 'Futures', 'type': 'error', 'coverage': 'Data temporarily unavailable'}],
            'trend_reasoning': 'Trend analysis unavailable',
            'trend_confidence': 0.3,
            'step': 'futures_trend_fallback'
        }
    
    # ==================== END FUTURES ANALYSIS METHODS ====================
    
    def aggregate_scores(self, state: IScoreState) -> Dict:
        """Node 6: Aggregate all component scores with nonlinear penalties + confidence."""
        logger.info(f"I-Score Node 6: Aggregating scores for {state['symbol']}")

        asset_type = state.get('asset_type', 'stocks')
        is_stock = asset_type in ('stocks', 'stock')

        thresholds = self.THRESHOLDS
        qual_score = state.get('qualitative_score', 50)
        quant_score = state.get('quantitative_score', 50)
        search_score = state.get('search_score', 50)
        trend_score = state.get('trend_score', 50)
        risk_score_val = state.get('risk_score', 50)
        mkt_ctx_score = state.get('market_context_score', 50)

        if is_stock:
            raw_score = (
                quant_score * 0.30 +
                trend_score * 0.20 +
                risk_score_val * 0.20 +
                qual_score * 0.15 +
                search_score * 0.10 +
                mkt_ctx_score * 0.05
            )
        else:
            raw_score = (
                qual_score * 0.15 +
                quant_score * 0.50 +
                search_score * 0.10 +
                trend_score * 0.25
            )
            risk_score_val = 50
            mkt_ctx_score = 50

        indicators = state.get('raw_indicators', {})
        if is_stock and indicators:
            from services.iscore.penalties import apply_penalties
            final_score, penalty, penalty_reasons = apply_penalties(
                raw_score, risk_score_val, trend_score, quant_score, indicators
            )
        else:
            final_score = round(raw_score, 2)
            penalty = 1.0
            penalty_reasons = []

        component_scores = [quant_score, trend_score, risk_score_val, qual_score, search_score, mkt_ctx_score]
        data_quality = {
            'has_real_indicators': bool(indicators),
            'has_volume': bool(indicators.get('volume')),
            'days_of_data': 120 if indicators else 0,
            'is_fallback': state.get('market_status') in ('demo', 'unknown'),
        }
        from services.iscore.confidence import compute_confidence, generate_score_factors
        conf = compute_confidence(component_scores, data_quality)

        score_factors = []
        if is_stock and indicators:
            comp_scores = {
                'qualitative': qual_score, 'quantitative': quant_score,
                'search': search_score, 'trend': trend_score,
                'risk': risk_score_val, 'market_context': mkt_ctx_score,
            }
            score_factors = generate_score_factors(indicators, comp_scores)

        strong_buy = thresholds['strong_buy']
        buy = thresholds['buy']
        hold_low = thresholds['hold_low']
        sell = thresholds['sell']

        if conf['value'] < thresholds['min_confidence']:
            recommendation = 'INCONCLUSIVE'
            summary = f"Confidence too low ({conf['level']}) for a definitive recommendation. I-Score: {final_score:.1f}."
        elif final_score >= strong_buy:
            recommendation = 'STRONG_BUY'
            summary = f"I-Score {final_score:.1f}/100 — Strong bullish alignment across quant, trend, and sentiment."
        elif final_score >= buy:
            recommendation = 'BUY'
            summary = f"I-Score {final_score:.1f}/100 — Favorable momentum with positive technical signals."
        elif final_score >= hold_low:
            recommendation = 'HOLD'
            summary = f"I-Score {final_score:.1f}/100 — Mixed signals; maintain current positions."
        elif final_score >= sell:
            recommendation = 'CAUTIONARY_SELL'
            summary = f"I-Score {final_score:.1f}/100 — Caution advised; consider reducing exposure."
        else:
            recommendation = 'STRONG_SELL'
            summary = f"I-Score {final_score:.1f}/100 — Significant bearish pressure across indicators."

        if penalty_reasons:
            summary += ' ' + '; '.join(penalty_reasons[:2]) + '.'

        return {
            'overall_score': final_score,
            'overall_confidence': conf['value'],
            'recommendation': recommendation,
            'recommendation_summary': summary,
            'penalty_applied': penalty,
            'penalty_reasons': penalty_reasons,
            'confidence_level': conf['level'],
            'score_factors': score_factors,
            'step': 'aggregation_complete',
        }
    
    def store_results(self, state: IScoreState) -> Dict:
        """Node 7: Store results in database and cache"""
        logger.info(f"I-Score Node 7: Storing results for {state['symbol']}")
        
        if state.get('cache_hit'):
            logger.info("Using cached result, skipping storage")
            return {'step': 'cached_result_used'}
        
        try:
            from models import ResearchRun, ResearchSignalComponent, ResearchCache
            
            research_run = ResearchRun(
                tenant_id='live',
                user_id=state['user_id'],
                asset_type=state['asset_type'],
                symbol=state['symbol'],
                asset_name=state.get('asset_name', state['symbol']),
                analysis_date=date.today(),
                status='completed',
                overall_score=Decimal(str(state.get('overall_score', 0))),
                confidence=Decimal(str(state.get('overall_confidence', 0))),
                recommendation=state.get('recommendation'),
                recommendation_summary=state.get('recommendation_summary'),
                inputs_json=state.get('config'),
                run_started_at=datetime.utcnow() - timedelta(seconds=30),
                run_completed_at=datetime.utcnow()
            )
            research_run.cache_key = research_run.generate_cache_key()
            db.session.add(research_run)
            db.session.flush()
            
            config = state.get('config', {})
            weights = config.get('weights', {})
            
            is_stock = state.get('asset_type', 'stocks') in ('stocks', 'stock')
            w = self.WEIGHTS if is_stock else {'qualitative_pct': 15, 'quantitative_pct': 50, 'search_pct': 10, 'trend_pct': 25}

            components = [
                {
                    'type': 'qualitative',
                    'weight': w.get('qualitative_pct', 15),
                    'score': state.get('qualitative_score', 50),
                    'confidence': state.get('qualitative_confidence', 0.5),
                    'details': state.get('qualitative_details', {})
                },
                {
                    'type': 'quantitative',
                    'weight': w.get('quantitative_pct', 30),
                    'score': state.get('quantitative_score', 50),
                    'confidence': state.get('quantitative_confidence', 0.5),
                    'details': state.get('quantitative_details', {})
                },
                {
                    'type': 'search',
                    'weight': w.get('search_pct', 10),
                    'score': state.get('search_score', 50),
                    'confidence': state.get('search_confidence', 0.5),
                    'details': state.get('search_details', {})
                },
                {
                    'type': 'trend',
                    'weight': w.get('trend_pct', 20),
                    'score': state.get('trend_score', 50),
                    'confidence': state.get('trend_confidence', 0.5),
                    'details': state.get('trend_details', {})
                },
            ]
            if is_stock:
                components.append({
                    'type': 'risk',
                    'weight': w.get('risk_pct', 20),
                    'score': state.get('risk_score', 50),
                    'confidence': state.get('risk_confidence', 0.5),
                    'details': state.get('risk_details', {}),
                })
                components.append({
                    'type': 'market_context',
                    'weight': w.get('market_context_pct', 5),
                    'score': state.get('market_context_score', 50),
                    'confidence': state.get('market_context_confidence', 0.5),
                    'details': state.get('market_context_details', {}),
                })
            
            for comp in components:
                signal = 'bullish' if comp['score'] > 60 else 'bearish' if comp['score'] < 40 else 'neutral'
                component = ResearchSignalComponent(
                    run_id=research_run.id,
                    component_type=comp['type'],
                    weight_pct=comp['weight'],
                    raw_score=Decimal(str(comp['score'])),
                    weighted_score=Decimal(str(comp['score'] * comp['weight'] / 100)),
                    confidence=Decimal(str(comp['confidence'])),
                    signal=signal,
                    breakdown=comp['details']
                )
                db.session.add(component)
            
            cache_key = self._generate_cache_key(
                state['asset_type'],
                state['symbol'],
                date.today()
            )
            
            from services.iscore.holding_period import compute_holding_period as _chp
            _hp = _chp(
                recommendation=state.get('recommendation', 'HOLD'),
                overall_score=state.get('overall_score', 50) or 50,
                risk_score=state.get('risk_score', 50) or 50,
                trend_score=state.get('trend_score', 50) or 50,
                quantitative_score=state.get('quantitative_score', 50) or 50,
                raw_indicators=state.get('raw_indicators', {}),
                is_stock=is_stock,
            )

            payload = {
                'overall_score': state.get('overall_score'),
                'overall_confidence': state.get('overall_confidence'),
                'recommendation': state.get('recommendation'),
                'recommendation_summary': state.get('recommendation_summary'),
                'penalty_applied': state.get('penalty_applied', 1.0),
                'penalty_reasons': state.get('penalty_reasons', []),
                'confidence_level': state.get('confidence_level', 'Medium'),
                'score_factors': state.get('score_factors', []),
                'holding_period': _hp,
                'qualitative': {'score': state.get('qualitative_score'), 'details': state.get('qualitative_details')},
                'quantitative': {'score': state.get('quantitative_score'), 'details': state.get('quantitative_details')},
                'search': {'score': state.get('search_score'), 'details': state.get('search_details')},
                'trend': {'score': state.get('trend_score'), 'details': state.get('trend_details')},
            }
            if is_stock:
                payload['risk'] = {'score': state.get('risk_score'), 'details': state.get('risk_details')}
                payload['market_context'] = {'score': state.get('market_context_score'), 'details': state.get('market_context_details')}

            cache_entry = ResearchCache(
                tenant_id='live',
                cache_key=cache_key,
                asset_type=state['asset_type'],
                symbol=state['symbol'],
                analysis_date=date.today(),
                result_payload=payload,
                overall_score=Decimal(str(state.get('overall_score', 0))),
                recommendation=state.get('recommendation'),
                expires_at=datetime.utcnow() + timedelta(hours=4)
            )
            db.session.add(cache_entry)
            
            db.session.commit()
            logger.info(f"I-Score results stored for {state['symbol']}: {state.get('overall_score')}/100")
            
            return {'step': 'storage_complete'}
            
        except Exception as e:
            logger.error(f"Error storing I-Score results: {e}")
            db.session.rollback()
            return {'step': 'storage_error', 'error': str(e)}
    
    def _get_fallback_price_data(self, symbol: str) -> Dict:
        """Get fallback demo pricing data for a symbol - accurate last close prices from Perplexity (27 Dec 2025)"""
        fallback_data = {
            'RELIANCE': {'current_price': 1559.20, 'previous_close': 1558.25, 'change_percent': 0.06},
            'TCS': {'current_price': 3280.00, 'previous_close': 3275.50, 'change_percent': 0.14},
            'HDFCBANK': {'current_price': 1615.50, 'previous_close': 1610.75, 'change_percent': 0.29},
            'INFY': {'current_price': 1656.10, 'previous_close': 1663.40, 'change_percent': -0.44},
            'ICICIBANK': {'current_price': 995.85, 'previous_close': 992.40, 'change_percent': 0.35},
            'SBIN': {'current_price': 512.40, 'previous_close': 508.80, 'change_percent': 0.71},
        }
        return fallback_data.get(symbol, {
            'current_price': 1000.00, 
            'previous_close': 995.50, 
            'change_percent': 0.45
        })
    
    def _parse_llm_response(self, response: str, response_type: str) -> Dict:
        """Parse LLM response to extract structured data"""
        import re
        
        try:
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        score_match = re.search(r'score[:\s]*(\d+)', response.lower())
        score = int(score_match.group(1)) if score_match else 50
        
        return {
            'sentiment_score': score,
            'search_score': score,
            'confidence': 0.6,
            'key_findings': [],
            'trend_direction': 'stable',
            'buzz_level': 'medium'
        }
    
    def analyze(self, asset_type: str, symbol: str, user_id: int, asset_name: str = None) -> Dict:
        """Run I-Score analysis for an asset."""
        is_stock = asset_type in ('stocks', 'stock')
        initial_state: IScoreState = {
            'asset_type': asset_type,
            'symbol': symbol,
            'asset_name': asset_name or symbol,
            'user_id': user_id,
            'current_price': 0, 'previous_close': 0, 'price_change_pct': 0,
            'market_status': 'unknown',
            'data_timestamp': datetime.utcnow().isoformat(),
            'cache_hit': False, 'cached_result': None,
            'qualitative_score': 0, 'qualitative_details': {}, 'qualitative_confidence': 0,
            'qualitative_sources': [], 'qualitative_reasoning': '',
            'quantitative_score': 0, 'quantitative_details': {}, 'quantitative_confidence': 0,
            'quantitative_sources': [], 'quantitative_reasoning': '',
            'search_score': 0, 'search_details': {}, 'search_confidence': 0,
            'search_sources': [], 'search_reasoning': '',
            'trend_score': 0, 'trend_details': {}, 'trend_confidence': 0,
            'trend_sources': [], 'trend_reasoning': '',
            'risk_score': 50, 'risk_details': {}, 'risk_confidence': 0.5,
            'market_context_score': 50, 'market_context_details': {}, 'market_context_confidence': 0.5,
            'raw_indicators': {},
            'overall_score': 0, 'overall_confidence': 0,
            'recommendation': '', 'recommendation_summary': '',
            'penalty_applied': 1.0, 'penalty_reasons': [],
            'confidence_level': 'Medium', 'score_factors': [],
            'config': {}, 'evidence': [], 'audit_trail': [],
            'error': None, 'step': 'start',
            'hist_data_source': '',
        }

        try:
            result = self.graph.invoke(initial_state)

            current_price = result.get('current_price', 0)
            previous_close = result.get('previous_close', 0)
            price_change_pct = result.get('price_change_pct', 0)
            quant_details = result.get('quantitative_details', {})
            if current_price == 0 and quant_details:
                price_data = quant_details.get('price_data', {})
                if price_data:
                    current_price = price_data.get('current', 0)
                    previous_close = price_data.get('previous_close', previous_close)
                    price_change_pct = price_data.get('change_pct', price_change_pct)
                    logger.info(f"Using price from quantitative_details: ₹{current_price}")

            weights = self.WEIGHTS if is_stock else {'qualitative_pct': 15, 'quantitative_pct': 50, 'search_pct': 10, 'trend_pct': 25}

            components = {
                'qualitative': {
                    'score': result.get('qualitative_score', 0),
                    'weight': weights.get('qualitative_pct', 15),
                    'confidence': result.get('qualitative_confidence', 0),
                    'details': result.get('qualitative_details', {}),
                    'sources': result.get('qualitative_sources', []),
                    'reasoning': result.get('qualitative_reasoning', ''),
                },
                'quantitative': {
                    'score': result.get('quantitative_score', 0),
                    'weight': weights.get('quantitative_pct', 30),
                    'confidence': result.get('quantitative_confidence', 0),
                    'details': result.get('quantitative_details', {}),
                    'sources': result.get('quantitative_sources', []),
                    'reasoning': result.get('quantitative_reasoning', ''),
                },
                'search': {
                    'score': result.get('search_score', 0),
                    'weight': weights.get('search_pct', 10),
                    'confidence': result.get('search_confidence', 0),
                    'details': result.get('search_details', {}),
                    'sources': result.get('search_sources', []),
                    'reasoning': result.get('search_reasoning', ''),
                },
                'trend': {
                    'score': result.get('trend_score', 0),
                    'weight': weights.get('trend_pct', 20),
                    'confidence': result.get('trend_confidence', 0),
                    'details': result.get('trend_details', {}),
                    'sources': result.get('trend_sources', []),
                    'reasoning': result.get('trend_reasoning', ''),
                },
            }

            if is_stock:
                components['risk'] = {
                    'score': result.get('risk_score', 50),
                    'weight': weights.get('risk_pct', 20),
                    'confidence': result.get('risk_confidence', 0.5),
                    'details': result.get('risk_details', {}),
                }
                components['market_context'] = {
                    'score': result.get('market_context_score', 50),
                    'weight': weights.get('market_context_pct', 5),
                    'confidence': result.get('market_context_confidence', 0.5),
                    'details': result.get('market_context_details', {}),
                }

            from services.iscore.holding_period import compute_holding_period
            holding_period = compute_holding_period(
                recommendation=result.get('recommendation', 'HOLD'),
                overall_score=result.get('overall_score', 50),
                risk_score=result.get('risk_score', 50),
                trend_score=result.get('trend_score', 50),
                quantitative_score=result.get('quantitative_score', 50),
                raw_indicators=result.get('raw_indicators', {}),
                is_stock=is_stock,
            )

            return {
                'success': True,
                'symbol': symbol,
                'asset_type': asset_type,
                'asset_name': result.get('asset_name', symbol),
                'iscore': result.get('overall_score', 0),
                'confidence': result.get('overall_confidence', 0),
                'confidence_level': result.get('confidence_level', 'Medium'),
                'recommendation': result.get('recommendation', 'INCONCLUSIVE'),
                'summary': result.get('recommendation_summary', ''),
                'holding_period': holding_period,
                'market_data': {
                    'current_price': current_price,
                    'previous_close': previous_close,
                    'change_pct': price_change_pct,
                    'timestamp': result.get('data_timestamp', ''),
                },
                'components': components,
                'penalty': {
                    'applied': result.get('penalty_applied', 1.0),
                    'reasons': result.get('penalty_reasons', []),
                },
                'score_factors': result.get('score_factors', []),
                'transparency': {
                    'description': 'Scentric AI Decision Engine — every recommendation comes with explainable reasoning.',
                    'model_version': 'v2.0',
                    'weights': weights,
                },
                'cached': result.get('cache_hit', False),
                'timestamp': datetime.utcnow().isoformat(),
                'data_source': result.get('hist_data_source', 'yfinance'),
            }

        except Exception as e:
            logger.error(f"I-Score analysis failed for {symbol}: {e}")
            return {
                'success': False, 'symbol': symbol, 'asset_type': asset_type,
                'iscore': 0, 'recommendation': 'ERROR', 'error': str(e),
            }


def get_iscore_for_symbol(symbol: str, asset_type: str = 'stocks', user_id: int = None) -> Dict:
    """
    Convenience function to get I-Score for a symbol
    
    Recommended I-Score thresholds:
    - >= 65: Recommended for investment (BUY or STRONG_BUY)
    - 45-64: Hold/Monitor
    - < 45: Avoid or Sell
    """
    engine = LangGraphIScoreEngine()
    return engine.analyze(asset_type, symbol, user_id or 1)
