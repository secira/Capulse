import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from services.anthropic_service import AnthropicService
from services.data_connectors import ConnectorRegistry, MarketData
from services.workflow_engine import (
    NodeStatus,
    WorkflowNode,
    WorkflowPipeline,
    WorkflowState,
)

logger = logging.getLogger(__name__)

QUALITATIVE_WEIGHT = 15
QUANTITATIVE_WEIGHT = 50
SEARCH_WEIGHT = 10
TREND_WEIGHT = 25

ISCORE_SCHEMA = {
    "sentiment_score": "number (0-100)",
    "confidence": "number (0-1)",
    "key_findings": ["string"],
    "reasoning": "string",
}

RECOMMENDATION_THRESHOLDS = {
    "strong_buy": 80,
    "buy": 65,
    "hold_high": 64,
    "hold_low": 45,
    "sell": 30,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class IScoreWorkflow:
    def __init__(self, anthropic_service: Optional[AnthropicService] = None):
        self._anthropic = anthropic_service or AnthropicService()
        self._registry = ConnectorRegistry()

    def build_pipeline(self) -> WorkflowPipeline:
        pipeline = WorkflowPipeline(
            name="iscore_pipeline",
            description="Anthropic-powered I-Score calculation with 7-node workflow",
        )

        pipeline.add_node(WorkflowNode(
            name="data_collection",
            execute_fn=self._node_data_collection,
            description="Collect market data and portfolio context via connectors",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="qualitative_analysis",
            execute_fn=self._node_qualitative,
            description="Qualitative sentiment analysis (15% weight)",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="quantitative_analysis",
            execute_fn=self._node_quantitative,
            description="Quantitative technical analysis (50% weight)",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="search_sentiment",
            execute_fn=self._node_search_sentiment,
            description="Search and trend sentiment analysis (10% weight)",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="trend_analysis",
            execute_fn=self._node_trend_analysis,
            description="Market trend analysis – OI, PCR, VIX (25% weight)",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="score_aggregation",
            execute_fn=self._node_aggregate,
            description="Calculate weighted I-Score and generate recommendation",
        ))
        pipeline.add_node(WorkflowNode(
            name="store_results",
            execute_fn=self._node_store_results,
            description="Persist I-Score results to database cache",
        ))

        return pipeline

    def calculate_iscore(
        self,
        symbol: str,
        asset_type: str = "stocks",
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        pipeline = self.build_pipeline()
        initial_data: Dict[str, Any] = {
            "symbol": symbol,
            "asset_type": asset_type,
            "user_id": user_id,
            "analysis_date": date.today().isoformat(),
        }

        state = pipeline.execute(initial_data=initial_data, stop_on_failure=False)
        summary = pipeline.get_execution_summary(state)
        summary["results"] = {
            "symbol": symbol,
            "asset_type": asset_type,
            "overall_score": state.get("overall_score", 0),
            "overall_confidence": state.get("overall_confidence", 0),
            "recommendation": state.get("recommendation", "HOLD"),
            "recommendation_summary": state.get("recommendation_summary", ""),
            "qualitative": {
                "score": state.get("qualitative_score", 50),
                "confidence": state.get("qualitative_confidence", 0),
                "details": state.get("qualitative_details", {}),
            },
            "quantitative": {
                "score": state.get("quantitative_score", 50),
                "confidence": state.get("quantitative_confidence", 0),
                "details": state.get("quantitative_details", {}),
            },
            "search": {
                "score": state.get("search_score", 50),
                "confidence": state.get("search_confidence", 0),
                "details": state.get("search_details", {}),
            },
            "trend": {
                "score": state.get("trend_score", 50),
                "confidence": state.get("trend_confidence", 0),
                "details": state.get("trend_details", {}),
            },
            "market_data": state.get("market_data_summary", {}),
        }
        return summary

    def _get_market_context(self, symbol: str) -> Dict[str, Any]:
        try:
            connector = self._registry.get_best_connector(user_id=0)
            md: MarketData = connector.get_market_data(symbol)
            return md.to_dict()
        except Exception as exc:
            logger.warning(f"Market data fetch failed for {symbol}: {exc}")
            return {"symbol": symbol, "current_price": 0, "error": str(exc)}

    def _node_data_collection(self, state: WorkflowState) -> Dict[str, Any]:
        symbol = state.get("symbol", "")
        asset_type = state.get("asset_type", "stocks")
        logger.info(f"I-Score data collection for {symbol} ({asset_type})")

        market = self._get_market_context(symbol)

        nse_quote: Dict[str, Any] = {}
        try:
            from services.nse_service import NSEService
            nse = NSEService()
            nse_quote = nse.get_stock_quote(symbol) or {}
        except Exception as exc:
            logger.warning(f"NSE quote unavailable for {symbol}: {exc}")

        current_price = _safe_float(nse_quote.get("current_price", market.get("current_price", 0)))
        previous_close = _safe_float(nse_quote.get("previous_close", market.get("previous_close", 0)))
        change_pct = _safe_float(nse_quote.get("change_percent", market.get("change_pct", 0)))

        perplexity_news_context = ""
        perplexity_citations: List[str] = []
        try:
            from services.perplexity_service import PerplexityService
            pplx = PerplexityService()
            result = pplx.research_indian_stock(symbol, research_type="news_sentiment")
            if result.get("success"):
                perplexity_news_context = result.get("research_content", "")
                perplexity_citations = result.get("citations", [])
                logger.info(f"Perplexity news context fetched for {symbol} ({len(perplexity_news_context)} chars)")
            else:
                logger.info(f"Perplexity returned fallback for {symbol} — API key may be absent")
        except Exception as exc:
            logger.warning(f"Perplexity news fetch failed for {symbol}: {exc}")

        return {
            "market_data_summary": {
                "current_price": current_price,
                "previous_close": previous_close,
                "change_pct": change_pct,
                "day_high": _safe_float(nse_quote.get("day_high", 0)),
                "day_low": _safe_float(nse_quote.get("day_low", 0)),
                "volume": int(_safe_float(nse_quote.get("volume", 0))),
                "data_source": nse_quote.get("data_source", "connector"),
            },
            "current_price": current_price,
            "previous_close": previous_close,
            "price_change_pct": change_pct,
            "perplexity_news_context": perplexity_news_context,
            "perplexity_citations": perplexity_citations,
        }

    def _node_qualitative(self, state: WorkflowState) -> Dict[str, Any]:
        symbol = state.get("symbol", "")
        price = state.get("current_price", 0)
        change = state.get("price_change_pct", 0)
        news_context = state.get("perplexity_news_context", "")

        system_prompt = (
            "You are a senior financial analyst at Target Capital specializing in Indian equity markets. "
            "Analyze qualitative sentiment for the given stock using the real-time news and analyst data provided. "
            "Where specific data is given, use it directly — do not rely on training data alone. "
            "Return a JSON object."
        )

        news_section = (
            f"\n\nReal-time news and analyst intelligence (Perplexity web search):\n{news_context}"
            if news_context
            else "\n\n(No real-time news context available — use available training knowledge with lower confidence.)"
        )

        user_prompt = (
            f"Analyze qualitative sentiment for {symbol} in the Indian stock market.\n"
            f"Current price: ₹{price:,.2f}, recent change: {change:+.2f}%."
            f"{news_section}\n\n"
            "Based on the context above, evaluate:\n"
            "1. Recent news from Moneycontrol, Economic Times, NSE/BSE announcements\n"
            "2. Social media and Twitter/X sentiment from Indian retail investors\n"
            "3. Annual report highlights and corporate governance quality\n"
            "4. Analyst ratings and brokerage recommendations (Indian brokerages)\n"
            "5. Management commentary and earnings guidance\n\n"
            "Score 0-100 (0=very negative sentiment, 100=very positive sentiment). "
            "Set confidence lower if news context was unavailable.\n"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": user_prompt}],
            output_schema=ISCORE_SCHEMA,
            system=system_prompt,
            max_tokens=2048,
            temperature=0.1,
        )

        parsed = result.get("parsed") or {}
        score = min(100, max(0, _safe_float(parsed.get("sentiment_score", 50))))
        confidence = min(1.0, max(0, _safe_float(parsed.get("confidence", 0.5))))

        return {
            "qualitative_score": score,
            "qualitative_confidence": confidence,
            "qualitative_details": {
                "key_findings": parsed.get("key_findings", []),
                "reasoning": parsed.get("reasoning", ""),
                "model": result.get("model", ""),
                "data_source": "Perplexity + Claude" if news_context else "Claude (no live news)",
                "perplexity_sourced": bool(news_context),
            },
        }

    def _node_quantitative(self, state: WorkflowState) -> Dict[str, Any]:
        symbol = state.get("symbol", "")
        price = state.get("current_price", 0)
        change = state.get("price_change_pct", 0)
        market_data = state.get("market_data_summary", {})

        tech_context = self._gather_technical_indicators(symbol)

        system_prompt = (
            "You are a quantitative analyst at Target Capital. "
            "Analyze the technical indicators provided and score the stock. "
            "Return a JSON object."
        )

        user_prompt = (
            f"Quantitative analysis for {symbol}.\n"
            f"Price: {price}, Change: {change}%\n"
            f"Day high/low: {market_data.get('day_high', 0)} / {market_data.get('day_low', 0)}\n"
            f"Volume: {market_data.get('volume', 0)}\n\n"
            f"Technical indicators:\n{json.dumps(tech_context, indent=2)}\n\n"
            "Evaluate RSI, SuperTrend, EMA crossovers, and momentum. "
            "Score 0-100 (0=strong bearish technicals, 100=strong bullish technicals).\n"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": user_prompt}],
            output_schema=ISCORE_SCHEMA,
            system=system_prompt,
            max_tokens=2048,
            temperature=0.1,
        )

        parsed = result.get("parsed") or {}
        score = min(100, max(0, _safe_float(parsed.get("sentiment_score", 50))))
        confidence = min(1.0, max(0, _safe_float(parsed.get("confidence", 0.5))))

        return {
            "quantitative_score": score,
            "quantitative_confidence": confidence,
            "quantitative_details": {
                "key_findings": parsed.get("key_findings", []),
                "reasoning": parsed.get("reasoning", ""),
                "technical_indicators": tech_context,
                "model": result.get("model", ""),
            },
        }

    def _node_search_sentiment(self, state: WorkflowState) -> Dict[str, Any]:
        symbol = state.get("symbol", "")
        price = state.get("current_price", 0)
        news_context = state.get("perplexity_news_context", "")

        system_prompt = (
            "You are a market intelligence analyst at Target Capital. "
            "Evaluate search-based and social sentiment for the stock using the real-time web data provided. "
            "Focus specifically on retail investor interest, search popularity, and online community buzz — "
            "distinct from news quality or analyst opinion. "
            "Return a JSON object."
        )

        news_section = (
            f"\n\nReal-time web intelligence (Perplexity search):\n{news_context}"
            if news_context
            else "\n\n(No real-time web context available — estimate from training knowledge with lower confidence.)"
        )

        user_prompt = (
            f"Assess search and social media sentiment for {symbol} (Indian market).\n"
            f"Current price: ₹{price:,.2f}."
            f"{news_section}\n\n"
            "From the context above, specifically evaluate:\n"
            "1. Retail investor discussion volume on Moneycontrol, StockEdge, Reddit r/IndiaInvestments\n"
            "2. Social media buzz on Twitter/X among Indian traders and investors\n"
            "3. Trending interest vs sector peers (relative search popularity)\n"
            "4. Sentiment polarity in online discussions (positive/neutral/negative)\n"
            "5. Any viral or newsworthy events driving unusual search interest\n\n"
            "Score 0-100 (0=very low or negative buzz, 100=very high positive retail interest). "
            "Set confidence lower if web context was unavailable.\n"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": user_prompt}],
            output_schema=ISCORE_SCHEMA,
            system=system_prompt,
            max_tokens=2048,
            temperature=0.1,
        )

        parsed = result.get("parsed") or {}
        score = min(100, max(0, _safe_float(parsed.get("sentiment_score", 50))))
        confidence = min(1.0, max(0, _safe_float(parsed.get("confidence", 0.4))))

        return {
            "search_score": score,
            "search_confidence": confidence,
            "search_details": {
                "key_findings": parsed.get("key_findings", []),
                "reasoning": parsed.get("reasoning", ""),
                "model": result.get("model", ""),
                "data_source": "Perplexity + Claude" if news_context else "Claude (no live web data)",
                "perplexity_sourced": bool(news_context),
            },
        }

    def _node_trend_analysis(self, state: WorkflowState) -> Dict[str, Any]:
        symbol = state.get("symbol", "")
        price = state.get("current_price", 0)

        trend_data = self._gather_trend_data(symbol)

        system_prompt = (
            "You are a derivatives and market-structure analyst at Target Capital. "
            "Analyze the trend data (Open Interest, Put-Call Ratio, India VIX) "
            "to determine market direction bias. Return a JSON object."
        )

        user_prompt = (
            f"Trend / derivatives analysis for {symbol}.\n"
            f"Current price: {price}.\n\n"
            f"Trend data:\n{json.dumps(trend_data, indent=2)}\n\n"
            "Evaluate:\n"
            "1. Open Interest build-up direction (long/short)\n"
            "2. Put-Call Ratio interpretation\n"
            "3. India VIX level and what it implies for volatility\n"
            "4. Overall market structure bias\n\n"
            "Score 0-100 (0=strong bearish trend, 100=strong bullish trend).\n"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": user_prompt}],
            output_schema=ISCORE_SCHEMA,
            system=system_prompt,
            max_tokens=2048,
            temperature=0.1,
        )

        parsed = result.get("parsed") or {}
        score = min(100, max(0, _safe_float(parsed.get("sentiment_score", 50))))
        confidence = min(1.0, max(0, _safe_float(parsed.get("confidence", 0.4))))

        return {
            "trend_score": score,
            "trend_confidence": confidence,
            "trend_details": {
                "key_findings": parsed.get("key_findings", []),
                "reasoning": parsed.get("reasoning", ""),
                "raw_trend_data": trend_data,
                "model": result.get("model", ""),
            },
        }

    def _node_aggregate(self, state: WorkflowState) -> Dict[str, Any]:
        q_score = _safe_float(state.get("qualitative_score", 50))
        qt_score = _safe_float(state.get("quantitative_score", 50))
        s_score = _safe_float(state.get("search_score", 50))
        t_score = _safe_float(state.get("trend_score", 50))

        q_conf = _safe_float(state.get("qualitative_confidence", 0.5))
        qt_conf = _safe_float(state.get("quantitative_confidence", 0.5))
        s_conf = _safe_float(state.get("search_confidence", 0.4))
        t_conf = _safe_float(state.get("trend_confidence", 0.4))

        overall_score = (
            q_score * (QUALITATIVE_WEIGHT / 100)
            + qt_score * (QUANTITATIVE_WEIGHT / 100)
            + s_score * (SEARCH_WEIGHT / 100)
            + t_score * (TREND_WEIGHT / 100)
        )
        overall_score = round(min(100, max(0, overall_score)), 2)

        total_weight = QUALITATIVE_WEIGHT + QUANTITATIVE_WEIGHT + SEARCH_WEIGHT + TREND_WEIGHT
        overall_confidence = round(
            (q_conf * QUALITATIVE_WEIGHT
             + qt_conf * QUANTITATIVE_WEIGHT
             + s_conf * SEARCH_WEIGHT
             + t_conf * TREND_WEIGHT) / total_weight,
            3,
        )

        recommendation = self._score_to_recommendation(overall_score)

        symbol = state.get("symbol", "")
        recommendation_summary = (
            f"{symbol} receives an I-Score of {overall_score}/100 "
            f"(confidence {overall_confidence:.0%}). "
            f"Recommendation: {recommendation}. "
            f"Qualitative={q_score:.0f}, Quantitative={qt_score:.0f}, "
            f"Search={s_score:.0f}, Trend={t_score:.0f}."
        )

        return {
            "overall_score": overall_score,
            "overall_confidence": overall_confidence,
            "recommendation": recommendation,
            "recommendation_summary": recommendation_summary,
        }

    def _node_store_results(self, state: WorkflowState) -> Dict[str, Any]:
        try:
            from app import db as app_db
            from models import ResearchCache

            symbol = state.get("symbol", "")
            asset_type = state.get("asset_type", "stocks")

            import hashlib
            cache_key = hashlib.md5(
                f"iscore:{asset_type}:{symbol}:{date.today().isoformat()}".encode()
            ).hexdigest()

            payload = {
                "qualitative": {
                    "score": state.get("qualitative_score", 50),
                    "confidence": state.get("qualitative_confidence", 0),
                    "details": state.get("qualitative_details", {}),
                },
                "quantitative": {
                    "score": state.get("quantitative_score", 50),
                    "confidence": state.get("quantitative_confidence", 0),
                    "details": state.get("quantitative_details", {}),
                },
                "search": {
                    "score": state.get("search_score", 50),
                    "confidence": state.get("search_confidence", 0),
                    "details": state.get("search_details", {}),
                },
                "trend": {
                    "score": state.get("trend_score", 50),
                    "confidence": state.get("trend_confidence", 0),
                    "details": state.get("trend_details", {}),
                },
                "overall_score": state.get("overall_score", 0),
                "overall_confidence": state.get("overall_confidence", 0),
                "recommendation": state.get("recommendation", "HOLD"),
                "recommendation_summary": state.get("recommendation_summary", ""),
                "market_data": state.get("market_data_summary", {}),
                "pipeline": "anthropic_workflow",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            from datetime import timedelta
            expires = datetime.now(timezone.utc) + timedelta(hours=12)

            existing = ResearchCache.query.filter_by(cache_key=cache_key).first()
            if existing:
                existing.result_payload = payload
                existing.overall_score = state.get("overall_score", 0)
                existing.recommendation = state.get("recommendation", "HOLD")
                existing.expires_at = expires
                existing.computed_at = datetime.now(timezone.utc)
            else:
                cache_entry = ResearchCache()
                cache_entry.cache_key = cache_key
                cache_entry.symbol = symbol
                cache_entry.asset_type = asset_type
                cache_entry.result_payload = payload
                cache_entry.overall_score = state.get("overall_score", 0)
                cache_entry.recommendation = state.get("recommendation", "HOLD")
                cache_entry.analysis_date = date.today()
                cache_entry.expires_at = expires
                app_db.session.add(cache_entry)

            app_db.session.commit()
            logger.info(f"I-Score results stored for {symbol}: {state.get('overall_score', 0)}")
            return {"stored": True, "cache_key": cache_key}

        except Exception as exc:
            logger.error(f"Failed to store I-Score results: {exc}")
            return {"stored": False, "store_error": str(exc)}

    def _score_to_recommendation(self, score: float) -> str:
        if score >= RECOMMENDATION_THRESHOLDS["strong_buy"]:
            return "STRONG BUY"
        if score >= RECOMMENDATION_THRESHOLDS["buy"]:
            return "BUY"
        if score >= RECOMMENDATION_THRESHOLDS["hold_low"]:
            return "HOLD"
        if score >= RECOMMENDATION_THRESHOLDS["sell"]:
            return "SELL"
        return "STRONG SELL"

    def _gather_technical_indicators(self, symbol: str) -> Dict[str, Any]:
        indicators: Dict[str, Any] = {"symbol": symbol}
        try:
            from services.nse_service import NSEService
            nse = NSEService()
            df = nse.get_intraday_data(symbol, interval="1h")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                indicators["rsi"] = round(float(latest.get("rsi", 50)), 2) if "rsi" in df.columns else None
                ema9_val = latest.get("ema_9") if "ema_9" in df.columns else None
                indicators["ema_9"] = round(_safe_float(ema9_val), 2) if ema9_val is not None else None
                ema20_val = latest.get("ema_20") if "ema_20" in df.columns else None
                indicators["ema_20"] = round(_safe_float(ema20_val), 2) if ema20_val is not None else None
                indicators["close"] = round(float(latest.get("close", 0)), 2)
                if indicators.get("ema_9") is not None and indicators.get("ema_20") is not None:
                    indicators["ema_crossover"] = "bullish" if float(indicators["ema_9"]) > float(indicators["ema_20"]) else "bearish"
                indicators["data_source"] = "yfinance_intraday"
            else:
                indicators["data_source"] = "unavailable"
        except Exception as exc:
            logger.warning(f"Technical indicator fetch failed for {symbol}: {exc}")
            indicators["data_source"] = "error"
            indicators["error"] = str(exc)
        return indicators

    def _gather_trend_data(self, symbol: str) -> Dict[str, Any]:
        trend: Dict[str, Any] = {"symbol": symbol}
        try:
            from services.nse_service import NSEService
            nse = NSEService()

            vix_data = nse.get_india_vix()
            trend["vix"] = {
                "value": vix_data.get("vix_value", 15.0),
                "source": vix_data.get("source", "fallback"),
            }

            pcr_data = nse.get_pcr("NIFTY")
            trend["pcr"] = {
                "value": pcr_data.get("pcr_value", 0.85),
                "source": pcr_data.get("source", "fallback"),
            }

            oi_data = nse.get_option_chain_oi("NIFTY")
            trend["open_interest"] = {
                "total_ce_oi": oi_data.get("total_ce_oi", 0),
                "total_pe_oi": oi_data.get("total_pe_oi", 0),
                "oi_pcr": oi_data.get("oi_pcr", 1.0),
                "source": oi_data.get("source", "fallback"),
            }
        except Exception as exc:
            logger.warning(f"Trend data fetch failed: {exc}")
            trend["error"] = str(exc)
        return trend
