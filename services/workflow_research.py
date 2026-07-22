import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from services.anthropic_service import AnthropicService
from services.workflow_engine import WorkflowNode, WorkflowPipeline, WorkflowState

logger = logging.getLogger(__name__)


class ResearchWorkflowPipeline:
    PIPELINE_NAME = "research_assistant"
    PIPELINE_DESCRIPTION = "Anthropic-powered multi-step research assistant pipeline"

    def __init__(self, anthropic_service: Optional[AnthropicService] = None):
        self._anthropic = anthropic_service or AnthropicService()
        self._connector = None

    def _get_connector(self, user_id: int):
        if self._connector is None:
            try:
                from services.data_connectors import ConnectorRegistry
                registry = ConnectorRegistry()
                self._connector = registry.get_best_connector(user_id)
            except Exception as e:
                logger.warning(f"Could not initialise data connector: {e}")
        return self._connector

    def build_pipeline(self) -> WorkflowPipeline:
        pipeline = WorkflowPipeline(
            name=self.PIPELINE_NAME,
            description=self.PIPELINE_DESCRIPTION,
        )

        pipeline.add_node(WorkflowNode(
            name="query_understanding",
            execute_fn=self._query_understanding,
            description="Parse user query intent and extract key entities",
            retry_count=1,
        ))

        pipeline.add_node(WorkflowNode(
            name="context_retrieval",
            execute_fn=self._context_retrieval,
            description="Retrieve relevant portfolio, market and historical context",
            retry_count=1,
        ))

        pipeline.add_node(WorkflowNode(
            name="market_analysis",
            execute_fn=self._market_analysis,
            description="Perform deep market analysis using Claude",
            retry_count=1,
        ))

        pipeline.add_node(WorkflowNode(
            name="response_generation",
            execute_fn=self._response_generation,
            description="Generate comprehensive research response with citations",
            retry_count=1,
        ))

        pipeline.add_node(WorkflowNode(
            name="trade_suggestions",
            execute_fn=self._trade_suggestions,
            description="Optionally generate actionable trade suggestions",
            retry_count=1,
            condition_fn=self._should_suggest_trades,
        ))

        return pipeline

    def execute(self, query: str, user_id: Optional[int] = None,
                user_context: Optional[Dict] = None) -> Dict[str, Any]:
        pipeline = self.build_pipeline()

        initial_data: Dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "user_context": user_context or {},
        }

        state = pipeline.execute(initial_data=initial_data, stop_on_failure=True)
        summary = pipeline.get_execution_summary(state)

        return {
            "answer": state.get("research_response", ""),
            "citations": state.get("citations", []),
            "trade_suggestions": state.get("trade_suggestions_list", []),
            "query_analysis": state.get("query_analysis", {}),
            "market_analysis": state.get("market_analysis_result", {}),
            "execution": summary,
        }

    def _query_understanding(self, state: WorkflowState) -> Dict[str, Any]:
        query = state.get("query", "")
        user_context = state.get("user_context", {})

        system_prompt = (
            "You are a senior financial research analyst at Capulse specialising in Indian equity markets (NSE/BSE). "
            "Analyse the user's research query and extract structured information.\n\n"
            "Return a JSON object with:\n"
            "- intent: one of 'stock_research', 'sector_analysis', 'portfolio_review', 'market_outlook', 'trade_signal', 'general'\n"
            "- entities: list of stock symbols, sector names or indices mentioned\n"
            "- timeframe: 'intraday', 'short_term', 'medium_term', 'long_term' or 'unspecified'\n"
            "- requires_market_data: boolean\n"
            "- suggest_trades: boolean — true if the user asks for buy/sell/invest recommendations\n"
            "- summary: one-line summary of the query"
        )

        context_hint = ""
        if user_context.get("preferences"):
            prefs = user_context["preferences"]
            context_hint = (
                f"\n\nUser profile — Risk tolerance: {prefs.get('risk_tolerance', 'MODERATE')}, "
                f"Investment horizon: {prefs.get('investment_horizon', 'medium_term')}"
            )

        schema = {
            "intent": "string",
            "entities": ["string"],
            "timeframe": "string",
            "requires_market_data": "boolean",
            "suggest_trades": "boolean",
            "summary": "string",
        }

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": f"Research query: {query}{context_hint}"}],
            output_schema=schema,
            system=system_prompt,
            max_tokens=1024,
            temperature=0.1,
        )

        parsed = result.get("parsed") or {
            "intent": "general",
            "entities": [],
            "timeframe": "unspecified",
            "requires_market_data": True,
            "suggest_trades": False,
            "summary": query[:200],
        }

        logger.info(f"Query understood — intent={parsed.get('intent')}, entities={parsed.get('entities')}")
        return {"query_analysis": parsed}

    def _context_retrieval(self, state: WorkflowState) -> Dict[str, Any]:
        user_id = state.get("user_id")
        query = state.get("query", "")
        query_analysis = state.get("query_analysis", {})
        user_context = state.get("user_context", {})
        context_parts: List[str] = []

        if user_id:
            connector = self._get_connector(user_id)
            if connector:
                try:
                    portfolio = connector.get_portfolio(user_id)
                    portfolio_dict = portfolio.to_dict()
                    user_context["portfolio"] = portfolio_dict

                    if portfolio_dict.get("holdings"):
                        context_parts.append("### User Portfolio")
                        context_parts.append(f"Total value: ₹{portfolio_dict['total_value']:,.2f}")
                        context_parts.append(f"Holdings: {len(portfolio_dict['holdings'])} positions")
                        for h in portfolio_dict["holdings"][:10]:
                            pnl_str = f"₹{h.get('pnl', 0):,.2f}"
                            context_parts.append(
                                f"- {h.get('symbol', '?')} | Qty {h.get('quantity', 0)} | "
                                f"Price ₹{h.get('current_price', 0):,.2f} | PnL {pnl_str}"
                            )
                except Exception as e:
                    logger.warning(f"Portfolio retrieval failed: {e}")

        entities = query_analysis.get("entities", [])
        if entities and user_id:
            connector = self._get_connector(user_id)
            if connector:
                context_parts.append("\n### Market Quotes")
                for symbol in entities[:5]:
                    try:
                        md = connector.get_market_data(symbol)
                        if md and md.current_price > 0:
                            context_parts.append(
                                f"- {md.symbol}: ₹{md.current_price:,.2f} "
                                f"({md.change_pct:+.2f}%) Vol {md.volume:,}"
                            )
                    except Exception as e:
                        logger.warning(f"Market data error for {symbol}: {e}")

        try:
            from services.research_assistant_service import ResearchAssistantService
            ras = ResearchAssistantService()
            embedding = ras.generate_embedding(query)
            if embedding:
                docs = ras.vector_search(embedding, limit=5)
                if docs:
                    context_parts.append("\n### Relevant Research Documents")
                    for doc in docs:
                        context_parts.append(
                            f"- **{doc.get('title', 'Untitled')}** "
                            f"(similarity {doc.get('similarity', 0):.2f}): "
                            f"{(doc.get('content', '') or '')[:200]}"
                        )
        except Exception as e:
            logger.warning(f"Vector search unavailable: {e}")

        if user_context.get("preferences"):
            prefs = user_context["preferences"]
            context_parts.append("\n### Investor Profile")
            if prefs.get("age"):
                context_parts.append(f"- Age: {prefs['age']}")
            if prefs.get("risk_tolerance"):
                context_parts.append(f"- Risk tolerance: {prefs['risk_tolerance']}")
            if prefs.get("investment_horizon"):
                context_parts.append(f"- Investment horizon: {prefs['investment_horizon']}")
            if prefs.get("financial_goals"):
                goals = prefs["financial_goals"]
                if isinstance(goals, list):
                    context_parts.append(f"- Financial goals: {', '.join(goals)}")

        retrieved_context = "\n".join(context_parts) if context_parts else "No additional context available."
        logger.info(f"Context retrieved — {len(context_parts)} sections")
        return {
            "retrieved_context": retrieved_context,
            "user_context": user_context,
        }

    def _market_analysis(self, state: WorkflowState) -> Dict[str, Any]:
        query = state.get("query", "")
        query_analysis = state.get("query_analysis", {})
        retrieved_context = state.get("retrieved_context", "")

        perplexity_context = ""
        perplexity_citations: list = []
        entities = query_analysis.get("entities", [])
        try:
            from services.perplexity_service import PerplexityService
            pplx = PerplexityService()
            primary_symbol = entities[0] if entities else ""
            if primary_symbol:
                result = pplx.research_indian_stock(primary_symbol, research_type="comprehensive")
            else:
                result = pplx.get_market_insights(focus_area="general")
            if result.get("success"):
                perplexity_context = result.get("research_content", result.get("insights", ""))
                perplexity_citations = result.get("citations", [])
                logger.info(f"Perplexity market intelligence fetched ({len(perplexity_context)} chars)")
            else:
                logger.info("Perplexity returned fallback — API key may be absent")
        except Exception as e:
            logger.warning(f"Perplexity search unavailable: {e}")

        system_prompt = (
            "You are a senior equity research analyst at Capulse covering Indian markets (NSE/BSE). "
            "Provide thorough, data-driven market analysis. Include specific numbers, ratios, "
            "price levels, and technical/fundamental observations. "
            "Reference SEBI regulations and Indian tax implications where relevant. "
            "Use ₹ for currency."
        )

        entities_str = ", ".join(entities) or "general market"
        perplexity_section = f"\n\nReal-time market intelligence (Perplexity):\n{perplexity_context}" if perplexity_context else ""
        user_message = (
            f"Research query: {query}\n\n"
            f"Focus entities: {entities_str}\n"
            f"Timeframe: {query_analysis.get('timeframe', 'unspecified')}\n\n"
            f"Available context:\n{retrieved_context}"
            f"{perplexity_section}\n\n"
            "Provide a detailed market analysis covering:\n"
            "1. Current price action and technical levels\n"
            "2. Fundamental metrics (P/E, ROE, debt ratios)\n"
            "3. Sector performance and macro context\n"
            "4. Recent news, earnings or regulatory developments\n"
            "5. Key risks and catalysts\n"
            "6. Peer comparison where applicable"
        )

        schema = {
            "analysis_summary": "string",
            "key_metrics": {},
            "technical_view": "string",
            "fundamental_view": "string",
            "sector_context": "string",
            "risks": ["string"],
            "catalysts": ["string"],
            "outlook": "string",
        }

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": user_message}],
            output_schema=schema,
            system=system_prompt,
            max_tokens=4096,
            temperature=0.2,
        )

        parsed = result.get("parsed") or {"analysis_summary": result.get("text", "")}
        logger.info("Market analysis completed")
        return {
            "market_analysis_result": parsed,
            "perplexity_citations": perplexity_citations,
        }

    def _response_generation(self, state: WorkflowState) -> Dict[str, Any]:
        query = state.get("query", "")
        retrieved_context = state.get("retrieved_context", "")
        market_analysis = state.get("market_analysis_result", {})
        query_analysis = state.get("query_analysis", {})
        user_context = state.get("user_context", {})

        system_prompt = (
            "You are the Chief Research Analyst at Capulse. "
            "Create a comprehensive, publication-quality research report that directly answers the user's question. "
            "Use markdown formatting with clear sections. Include specific data points. "
            "End with a disclaimer: 'This information is for educational purposes only. "
            "Investors should consult a qualified financial advisor before making investment decisions.'\n\n"
            "Use ₹ for currency. Reference NSE/BSE exchanges."
        )

        analysis_json = json.dumps(market_analysis, indent=2, default=str) if market_analysis else "N/A"

        prefs_note = ""
        if user_context.get("preferences"):
            prefs = user_context["preferences"]
            prefs_note = (
                f"\n\nPersonalisation note — user's risk tolerance is {prefs.get('risk_tolerance', 'MODERATE')}, "
                f"horizon {prefs.get('investment_horizon', 'medium_term')}. "
                "Tailor the language and recommendations accordingly."
            )

        user_message = (
            f"User Question: {query}\n\n"
            f"Query Analysis: {json.dumps(query_analysis, default=str)}\n\n"
            f"Retrieved Context:\n{retrieved_context}\n\n"
            f"Market Analysis:\n{analysis_json}"
            f"{prefs_note}\n\n"
            "Write a detailed research report in markdown."
        )

        result = self._anthropic.chat(
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
            max_tokens=4096,
            temperature=0.25,
        )

        response_text = result.get("text", "")

        perplexity_citations = state.get("perplexity_citations", [])
        citations: List[Dict[str, str]] = []
        if perplexity_citations:
            citations.append({"source": "Perplexity AI (real-time web search)", "type": "web_search"})
            for url in perplexity_citations[:5]:
                citations.append({"source": str(url), "type": "web_citation"})
        if market_analysis.get("key_metrics"):
            citations.append({"source": "Claude Market Analysis", "type": "ai_analysis"})
        if "Research Documents" in retrieved_context:
            citations.append({"source": "Capulse Knowledge Base", "type": "vector_search"})
        if user_context.get("portfolio"):
            citations.append({"source": "User Portfolio Data", "type": "portfolio"})

        logger.info(f"Research response generated — {len(response_text)} chars, {len(citations)} citations")
        return {
            "research_response": response_text,
            "citations": citations,
        }

    def _should_suggest_trades(self, state: WorkflowState) -> bool:
        query_analysis = state.get("query_analysis", {})
        if query_analysis.get("suggest_trades"):
            return True

        query_lower = (state.get("query", "") or "").lower()
        trade_keywords = [
            "buy", "sell", "trade", "invest", "recommendation",
            "should i", "entry", "target price", "stop loss",
        ]
        return any(kw in query_lower for kw in trade_keywords)

    def _trade_suggestions(self, state: WorkflowState) -> Dict[str, Any]:
        query = state.get("query", "")
        market_analysis = state.get("market_analysis_result", {})
        research_response = state.get("research_response", "")
        user_context = state.get("user_context", {})

        system_prompt = (
            "You are a SEBI-registered investment advisor at Capulse. "
            "Based on the research analysis, suggest specific, actionable trades for the Indian market (NSE). "
            "For each trade provide: symbol, action (BUY/SELL/HOLD), entry price range, "
            "target price, stop loss, timeframe, risk-reward ratio, and reasoning.\n\n"
            "Important: Always include the disclaimer that these are educational suggestions only."
        )

        portfolio_summary = ""
        if user_context.get("portfolio", {}).get("holdings"):
            holdings = user_context["portfolio"]["holdings"]
            symbols = [h.get("symbol", "?") for h in holdings[:10]]
            portfolio_summary = f"\nUser currently holds: {', '.join(symbols)}"

        prefs_note = ""
        if user_context.get("preferences"):
            prefs = user_context["preferences"]
            prefs_note = (
                f"\nUser risk tolerance: {prefs.get('risk_tolerance', 'MODERATE')}, "
                f"horizon: {prefs.get('investment_horizon', 'medium_term')}"
            )

        schema = {
            "suggestions": [
                {
                    "symbol": "string",
                    "action": "BUY|SELL|HOLD",
                    "entry_price_low": "number",
                    "entry_price_high": "number",
                    "target_price": "number",
                    "stop_loss": "number",
                    "timeframe": "string",
                    "risk_reward_ratio": "number",
                    "reasoning": "string",
                }
            ],
            "disclaimer": "string",
        }

        user_message = (
            f"Research query: {query}\n\n"
            f"Market analysis summary: {json.dumps(market_analysis, default=str)}\n\n"
            f"Research report excerpt:\n{research_response[:2000]}"
            f"{portfolio_summary}{prefs_note}\n\n"
            "Suggest 2-3 specific trades with all required fields."
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": user_message}],
            output_schema=schema,
            system=system_prompt,
            max_tokens=2048,
            temperature=0.2,
        )

        parsed = result.get("parsed") or {}
        suggestions = parsed.get("suggestions", [])

        logger.info(f"Trade suggestions generated — {len(suggestions)} suggestions")
        return {"trade_suggestions_list": suggestions}

    @staticmethod
    def save_research_results(query: str, user_id: int, result: dict, tenant_id: str = "live") -> None:
        try:
            import hashlib
            from datetime import date, timedelta
            from app import db as app_db
            from models import ResearchCache

            cache_key = hashlib.md5(
                f"research:{tenant_id}:{user_id}:{query[:200]}:{date.today().isoformat()}".encode()
            ).hexdigest()

            payload = {
                "answer": result.get("answer", ""),
                "citations": result.get("citations", []),
                "trade_suggestions": result.get("trade_suggestions", []),
                "query_analysis": result.get("query_analysis", {}),
                "pipeline": "anthropic_research",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            expires = datetime.now(timezone.utc) + timedelta(hours=6)

            existing = ResearchCache.query.filter_by(cache_key=cache_key).first()
            if existing:
                existing.result_payload = payload
                existing.computed_at = datetime.now(timezone.utc)
                existing.expires_at = expires
            else:
                entry = ResearchCache()
                entry.cache_key = cache_key
                entry.symbol = (result.get("query_analysis", {}).get("entities") or ["general"])[0][:20]
                entry.asset_type = "research"
                entry.result_payload = payload
                entry.overall_score = 0
                entry.recommendation = ""
                entry.analysis_date = date.today()
                entry.expires_at = expires
                app_db.session.add(entry)

            app_db.session.commit()
            logger.info(f"Research results saved for query: {query[:50]}...")
        except Exception as exc:
            logger.warning(f"Failed to save research results: {exc}")
