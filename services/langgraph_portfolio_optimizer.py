"""
LangGraph Multi-Agent Portfolio Optimizer for Target Capital
Coordinates 4 specialized agents for comprehensive portfolio analysis
"""

import os
import logging
from typing import Dict, List, TypedDict, Annotated
from datetime import datetime
import operator
import json

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from services.comprehensive_portfolio_service import ComprehensivePortfolioService

logger = logging.getLogger(__name__)


class PortfolioState(TypedDict):
    """State for the portfolio optimizer graph"""
    messages: Annotated[List, operator.add]
    user_id: int
    portfolio_data: Dict
    user_preferences: Dict
    risk_analysis: Dict
    sector_analysis: Dict
    allocation_recommendations: Dict
    opportunities: List[Dict]
    final_report: str
    agent_outputs: Dict


class LangGraphPortfolioOptimizer:
    """
    Multi-agent portfolio optimizer with 4 specialized agents:
    
    1. Risk Analyzer Agent - Analyzes portfolio risk, volatility, diversification
    2. Sector Analyzer Agent - Evaluates sector allocation and concentration
    3. Asset Allocator Agent - Recommends optimal asset allocation
    4. Opportunity Finder Agent - Identifies investment opportunities based on preferences
    
    Coordinator orchestrates agents and synthesizes insights
    """
    
    def __init__(self):
        self._risk_llm = None
        self._creative_llm = None
        self._balanced_llm = None
        self._graph = None
    
    def _get_api_key(self):
        """Get API key lazily"""
        return os.environ.get("OPENAI_API_KEY", "")
    
    @property
    def risk_llm(self):
        """Lazy-load risk LLM"""
        if self._risk_llm is None:
            self._risk_llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.1,
                api_key=self._get_api_key()
            )
        return self._risk_llm
    
    @property
    def creative_llm(self):
        """Lazy-load creative LLM"""
        if self._creative_llm is None:
            self._creative_llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.7,
                api_key=self._get_api_key()
            )
        return self._creative_llm
    
    @property
    def balanced_llm(self):
        """Lazy-load balanced LLM"""
        if self._balanced_llm is None:
            self._balanced_llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.4,
                api_key=self._get_api_key()
            )
        return self._balanced_llm
    
    @property
    def graph(self):
        """Lazy-load the graph"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph
    
    @staticmethod
    def _parse_llm_json(content: str, fallback_key: str = "raw_output") -> any:
        """
        Robustly parse JSON from LLM output.
        Handles markdown code fences, trailing commas, and whitespace.
        Returns parsed object, or a fallback dict on failure.
        """
        import re as _re
        text = content if isinstance(content, str) else str(content)
        # Strip ```json ... ``` or ``` ... ``` fences
        text = _re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=_re.IGNORECASE | _re.MULTILINE)
        text = _re.sub(r'\s*```$', '', text.strip(), flags=_re.MULTILINE)
        # Remove trailing commas before ] or } (common LLM mistake)
        text = _re.sub(r',\s*([}\]])', r'\1', text)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract first JSON object or array
            match = _re.search(r'(\{.*\}|\[.*\])', text, _re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        logger.warning(f"JSON parse failed for {fallback_key}, returning raw text")
        return {fallback_key: content}

    def _build_graph(self):
        """Build the multi-agent coordination graph"""
        workflow = StateGraph(PortfolioState)
        
        # Add agent nodes
        workflow.add_node("fetch_portfolio", self.fetch_portfolio_data)
        workflow.add_node("risk_agent", self.risk_analyzer_agent)
        workflow.add_node("sector_agent", self.sector_analyzer_agent)
        workflow.add_node("allocation_agent", self.asset_allocator_agent)
        workflow.add_node("opportunity_agent", self.opportunity_finder_agent)
        workflow.add_node("coordinator", self.coordinator_agent)
        
        # Define the flow
        workflow.set_entry_point("fetch_portfolio")
        
        # Fetch portfolio first
        workflow.add_edge("fetch_portfolio", "risk_agent")
        
        # Run specialized agents in parallel (conceptually)
        workflow.add_edge("risk_agent", "sector_agent")
        workflow.add_edge("sector_agent", "allocation_agent")
        workflow.add_edge("allocation_agent", "opportunity_agent")
        
        # Coordinator synthesizes all agent outputs
        workflow.add_edge("opportunity_agent", "coordinator")
        workflow.add_edge("coordinator", END)
        
        return workflow.compile()
    
    def fetch_portfolio_data(self, state: PortfolioState) -> Dict:
        """Fetch comprehensive portfolio data with vector embeddings and user preferences"""
        logger.info("Fetching portfolio data and preferences")
        
        user_id = state["user_id"]
        
        try:
            # Get portfolio data from service
            portfolio_service = ComprehensivePortfolioService(user_id)
            portfolio_summary = portfolio_service.get_complete_portfolio_summary()
            
            # Fetch user portfolio preferences
            from models import User
            from app import db
            user = db.session.query(User).filter_by(id=user_id).first()
            
            user_preferences = {}
            if user and user.portfolio_preferences:
                prefs = user.portfolio_preferences
                user_preferences = {
                    "age": prefs.age,
                    "risk_tolerance": prefs.risk_tolerance,
                    "investment_horizon": prefs.investment_horizon,
                    "financial_goals": prefs.financial_goals,
                    "annual_income": prefs.annual_income,
                    "expected_return": prefs.expected_return,
                    "max_acceptable_loss": prefs.max_acceptable_loss,
                    "minimum_return_required": prefs.minimum_return_required,
                    "preferred_asset_classes": prefs.preferred_asset_classes,
                    "asset_allocation": prefs.asset_allocation,
                    "rebalancing_frequency": prefs.rebalancing_frequency,
                    "liquidity_requirement": prefs.liquidity_requirement,
                    "geographic_preference": prefs.geographic_preference,
                    "special_instructions": prefs.special_instructions
                }
                logger.info(f"User preferences loaded: {user_preferences.get('risk_tolerance', 'Not set')}")
            else:
                logger.warning("No portfolio preferences found for user")
            
            # Ensure portfolio embeddings are up to date
            try:
                from services.portfolio_embedding_service import PortfolioEmbeddingService
                embedding_service = PortfolioEmbeddingService()
                embedding_service.sync_all_user_assets(user_id)
                logger.info("Portfolio embeddings synced for analysis")
            except Exception as e:
                logger.warning(f"Could not sync embeddings: {e}")
            
            return {
                "portfolio_data": portfolio_summary,
                "user_preferences": user_preferences,
                "messages": [AIMessage(content="Portfolio data and preferences fetched successfully")],
                "agent_outputs": {}
            }
        except Exception as e:
            logger.error(f"Failed to fetch portfolio data: {e}")
            return {
                "portfolio_data": {},
                "user_preferences": {},
                "messages": [AIMessage(content=f"Error fetching portfolio: {e}")],
                "agent_outputs": {}
            }
    
    def risk_analyzer_agent(self, state: PortfolioState) -> Dict:
        """Agent 1: Analyze portfolio risk and volatility"""
        logger.info("Risk Analyzer Agent running")

        portfolio = state.get("portfolio_data", {})
        user_preferences = state.get("user_preferences", {})

        system_prompt = """You are a Risk Analysis Specialist at Target Capital covering Indian equity markets.
Analyze the portfolio and return ONLY a raw JSON object (no markdown, no code fences) with this exact schema:
{
  "overall_risk_score": <number 1-10>,
  "volatility_assessment": "<Low|Medium|High|Very High>",
  "diversification_quality": "<Poor|Fair|Good|Excellent>",
  "concentration_risks": ["<risk description>"],
  "risk_adjusted_return_comment": "<string>",
  "risk_mitigation_recommendations": ["<actionable recommendation>"],
  "preference_alignment": "<how well portfolio matches user risk tolerance>",
  "summary": "<2-3 sentence overall risk summary>"
}
Be precise and quantitative. Use INR (₹) for amounts. Output ONLY the JSON object."""

        portfolio_summary = json.dumps(portfolio, indent=2, default=str)
        preferences_summary = json.dumps(user_preferences, indent=2, default=str) if user_preferences else "No preferences set"

        response = self.risk_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Analyze this portfolio for risk:\n\nPortfolio:\n{portfolio_summary}\n\nUser Preferences:\n{preferences_summary}")
        ])

        content = response.content if isinstance(response.content, str) else str(response.content)
        risk_analysis = self._parse_llm_json(content, fallback_key="raw_analysis")

        logger.info(f"Risk analysis parsed — keys: {list(risk_analysis.keys()) if isinstance(risk_analysis, dict) else 'list'}")
        return {
            "risk_analysis": risk_analysis,
            "messages": [AIMessage(content="Risk analysis completed")],
            "agent_outputs": {"risk_agent": risk_analysis},
        }
    
    def sector_analyzer_agent(self, state: PortfolioState) -> Dict:
        """Agent 2: Analyze sector allocation and concentration"""
        logger.info("Sector Analyzer Agent running")

        portfolio = state.get("portfolio_data", {})
        user_preferences = state.get("user_preferences", {})

        system_prompt = """You are a Sector Analysis Specialist at Target Capital covering Indian equity markets (NSE/BSE).
Analyze the portfolio's sector allocation and return ONLY a raw JSON object (no markdown, no code fences) with this exact schema:
{
  "current_sector_breakdown": {"<SectorName>": <percentage as number>},
  "overexposed_sectors": ["<sector name>"],
  "underexposed_sectors": ["<sector name>"],
  "cyclical_vs_defensive_ratio": "<e.g. 60% Cyclical / 40% Defensive>",
  "sector_concentration_risk": "<Low|Medium|High>",
  "sector_opportunities": ["<specific opportunity>"],
  "sector_risks": ["<specific risk>"],
  "rebalancing_recommendations": ["<actionable recommendation with target %>"],
  "summary": "<2-3 sentence sector outlook summary>"
}
Provide specific percentages for Indian market sectors (Banking, IT, FMCG, Pharma, Auto, Energy, etc.).
Output ONLY the JSON object."""

        portfolio_summary = json.dumps(portfolio, indent=2, default=str)
        preferences_summary = json.dumps(user_preferences, indent=2, default=str) if user_preferences else "No preferences set"

        response = self.balanced_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Analyze sector allocation:\n\nPortfolio:\n{portfolio_summary}\n\nUser Preferences:\n{preferences_summary}")
        ])

        content = response.content if isinstance(response.content, str) else str(response.content)
        sector_analysis = self._parse_llm_json(content, fallback_key="raw_analysis")

        logger.info(f"Sector analysis parsed — keys: {list(sector_analysis.keys()) if isinstance(sector_analysis, dict) else 'list'}")
        return {
            "sector_analysis": sector_analysis,
            "messages": [AIMessage(content="Sector analysis completed")],
            "agent_outputs": {**state.get("agent_outputs", {}), "sector_agent": sector_analysis},
        }
    
    def asset_allocator_agent(self, state: PortfolioState) -> Dict:
        """Agent 3: Recommend optimal asset allocation"""
        logger.info("Asset Allocator Agent running")
        
        portfolio = state.get("portfolio_data", {})
        risk_analysis = state.get("risk_analysis", {})
        sector_analysis = state.get("sector_analysis", {})
        user_preferences = state.get("user_preferences", {})
        
        system_prompt = """You are an Asset Allocation Specialist at Target Capital.
Based on the portfolio and previous analysis, recommend:
1. Optimal asset class allocation (Equity, Debt, Gold, etc.)
2. Rebalancing strategy aligned with user preferences
3. Specific allocation percentages matching user's preferred assets
4. Timeline for implementation based on investment horizon
5. Expected impact on risk/return profile

IMPORTANT: Align recommendations with user's:
- Risk tolerance
- Investment horizon
- Preferred asset classes
- Expected return targets
- Liquidity requirements

Output as JSON."""
        
        preferences_summary = json.dumps(user_preferences, indent=2) if user_preferences else "No preferences set"
        context = f"""Portfolio: {json.dumps(portfolio, indent=2)}
Risk Analysis: {json.dumps(risk_analysis, indent=2)}
Sector Analysis: {json.dumps(sector_analysis, indent=2)}
User Preferences: {preferences_summary}"""
        
        response = self.balanced_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Recommend asset allocation:\n{context}")
        ])

        content = response.content if isinstance(response.content, str) else str(response.content)
        allocation_recs = self._parse_llm_json(content, fallback_key="raw_recommendations")

        logger.info(f"Allocation recommendations parsed — keys: {list(allocation_recs.keys()) if isinstance(allocation_recs, dict) else 'list'}")
        return {
            "allocation_recommendations": allocation_recs,
            "messages": [AIMessage(content="Allocation recommendations generated")],
            "agent_outputs": {**state.get("agent_outputs", {}), "allocation_agent": allocation_recs},
        }
    
    def opportunity_finder_agent(self, state: PortfolioState) -> Dict:
        """Agent 4: Identify investment opportunities"""
        logger.info("Opportunity Finder Agent running")
        
        portfolio = state.get("portfolio_data", {})
        allocation_recs = state.get("allocation_recommendations", {})
        user_preferences = state.get("user_preferences", {})
        
        system_prompt = """You are an Investment Opportunities Specialist at Target Capital.
Identify 5-10 specific investment opportunities that match user preferences:
1. Underrepresented quality stocks (aligned with preferred asset classes)
2. Emerging sector plays (matching geographic preference)
3. Value opportunities (within risk tolerance)
4. Diversification additions (matching investment horizon)
5. Dividend yielders for income (if liquidity required)

For each opportunity provide:
- Stock name and symbol
- Sector
- Rationale (2-3 sentences)
- Expected allocation (%)
- Risk level
- How it aligns with user goals

IMPORTANT: Suggest only opportunities that match user's:
- Risk tolerance
- Preferred asset classes
- Financial goals
- Geographic preference

Output as JSON array."""
        
        preferences_summary = json.dumps(user_preferences, indent=2) if user_preferences else "No preferences set"
        context = f"""Current Portfolio: {json.dumps(portfolio, indent=2)}
Allocation Gaps: {json.dumps(allocation_recs, indent=2)}
User Preferences: {preferences_summary}"""
        
        response = self.creative_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Find investment opportunities:\n{context}")
        ])

        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = self._parse_llm_json(content, fallback_key="raw_opportunities")
        opportunities = parsed if isinstance(parsed, list) else [parsed]

        logger.info(f"Opportunity finder parsed — {len(opportunities)} opportunities")
        return {
            "opportunities": opportunities,
            "messages": [AIMessage(content=f"Found {len(opportunities)} opportunities")],
            "agent_outputs": {**state.get("agent_outputs", {}), "opportunity_agent": opportunities},
        }
    
    def coordinator_agent(self, state: PortfolioState) -> Dict:
        """Coordinator: Synthesize all agent outputs into final report"""
        logger.info("Coordinator Agent synthesizing insights")
        
        user_preferences = state.get("user_preferences", {})
        
        system_prompt = """You are the Chief Portfolio Strategist at Target Capital.
Synthesize the analysis from all specialized agents into a comprehensive portfolio optimization report.

Create a well-structured markdown report with:
1. Executive Summary (highlight alignment with user's goals and preferences)
2. User Investment Profile (age, risk tolerance, horizon, goals)
3. Risk Assessment (from Risk Agent with preference alignment)
4. Sector Analysis (from Sector Agent)
5. Asset Allocation Strategy (from Allocation Agent aligned with preferences)
6. Investment Opportunities (from Opportunity Agent matching user criteria)
7. Action Plan with prioritized steps (considering user's timeline)
8. Expected Outcomes (realistic based on user's expected returns)

IMPORTANT: Emphasize how recommendations align with user's:
- Risk tolerance
- Investment horizon
- Financial goals
- Preferred asset classes
- Expected return targets

Make it clear, actionable, and personalized to the user."""
        
        agent_outputs = state.get("agent_outputs", {})
        preferences_summary = json.dumps(user_preferences, indent=2) if user_preferences else "No preferences set"
        
        context = f"""User Preferences:
{preferences_summary}

Agent Outputs:
        
Risk Analysis:
{json.dumps(agent_outputs.get('risk_agent', {}), indent=2)}

Sector Analysis:
{json.dumps(agent_outputs.get('sector_agent', {}), indent=2)}

Allocation Recommendations:
{json.dumps(agent_outputs.get('allocation_agent', {}), indent=2)}

Investment Opportunities:
{json.dumps(agent_outputs.get('opportunity_agent', {}), indent=2)}"""
        
        response = self.balanced_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Create comprehensive report:\n{context}")
        ])
        
        content = response.content if isinstance(response.content, str) else str(response.content)
        return {
            "final_report": content,
            "messages": [AIMessage(content="Portfolio optimization report completed")]
        }
    
    def optimize_portfolio(self, user_id: int) -> Dict:
        """
        Main entry point for portfolio optimization
        
        Args:
            user_id: User ID to optimize portfolio for
        
        Returns:
            Dict with comprehensive optimization report and agent insights
        """
        logger.info(f"Starting multi-agent portfolio optimization for user {user_id}")
        
        # Initialize state
        initial_state = {
            "messages": [],
            "user_id": user_id,
            "portfolio_data": {},
            "user_preferences": {},
            "risk_analysis": {},
            "sector_analysis": {},
            "allocation_recommendations": {},
            "opportunities": [],
            "final_report": "",
            "agent_outputs": {}
        }
        
        try:
            # Run the graph
            final_state = self.graph.invoke(initial_state)
            
            return {
                "report": final_state.get("final_report", ""),
                "risk_analysis": final_state.get("risk_analysis", {}),
                "sector_analysis": final_state.get("sector_analysis", {}),
                "allocation_recommendations": final_state.get("allocation_recommendations", {}),
                "opportunities": final_state.get("opportunities", []),
                "metadata": {
                    "agents_used": list(final_state.get("agent_outputs", {}).keys()),
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
        except Exception as e:
            logger.error(f"Portfolio optimization failed: {e}")
            return {
                "report": f"Portfolio optimization encountered an error: {str(e)}",
                "error": str(e)
            }
