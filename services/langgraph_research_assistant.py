"""
LangGraph-powered Research Assistant for Capulse
Implements a conversational, multi-step research agent with state management
"""

import os
import logging
from typing import Dict, List, TypedDict, Annotated
from datetime import datetime
import operator

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from services.research_assistant_service import ResearchAssistantService
from services.perplexity_service import PerplexityService

logger = logging.getLogger(__name__)


class ResearchState(TypedDict):
    """State for the research assistant graph"""
    messages: Annotated[List, operator.add]
    user_query: str
    user_context: Dict
    retrieved_context: str
    market_data: Dict
    analysis_result: str
    trade_suggestions: List[Dict]
    next_step: str
    iteration_count: int


class LangGraphResearchAssistant:
    """
    LangGraph-powered research assistant with multi-step reasoning:
    1. Query Understanding - Parse user intent and extract key information
    2. Context Retrieval - Get relevant historical research and user portfolio
    3. Market Analysis - Use Perplexity to get real-time market insights
    4. Response Generation - Create comprehensive, cited response
    5. Trade Suggestion - Optionally suggest actionable trades
    """
    
    def __init__(self):
        self._llm = None
        self._research_service = None
        self._perplexity_service = None
        self._graph = None
    
    @property
    def llm(self):
        """Lazy-load the LLM"""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.2,
                api_key=os.environ.get("OPENAI_API_KEY")
            )
        return self._llm
    
    @property
    def research_service(self):
        """Lazy-load research service"""
        if self._research_service is None:
            self._research_service = ResearchAssistantService()
        return self._research_service
    
    @property
    def perplexity_service(self):
        """Lazy-load perplexity service"""
        if self._perplexity_service is None:
            self._perplexity_service = PerplexityService()
        return self._perplexity_service
    
    @property
    def graph(self):
        """Lazy-load the graph"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph
    
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow"""
        workflow = StateGraph(ResearchState)
        
        # Add nodes for each step
        workflow.add_node("understand_query", self.understand_query)
        workflow.add_node("retrieve_context", self.retrieve_context)
        workflow.add_node("analyze_market", self.analyze_market)
        workflow.add_node("generate_response", self.generate_response)
        workflow.add_node("suggest_trades", self.suggest_trades)
        
        # Define the flow
        workflow.set_entry_point("understand_query")
        
        workflow.add_edge("understand_query", "retrieve_context")
        workflow.add_edge("retrieve_context", "analyze_market")
        workflow.add_edge("analyze_market", "generate_response")
        
        # Conditional edge: only suggest trades if market analysis indicates opportunities
        workflow.add_conditional_edges(
            "generate_response",
            self.should_suggest_trades,
            {
                "suggest": "suggest_trades",
                "end": END
            }
        )
        
        workflow.add_edge("suggest_trades", END)
        
        return workflow.compile()
    
    def understand_query(self, state: ResearchState) -> Dict:
        """Step 1: Understand user query and extract intent"""
        logger.info("Step 1: Understanding query")
        
        system_prompt = """You are a financial research analyst. Analyze the user's query and:
1. Identify the main intent (research, trade signal, portfolio analysis, market news)
2. Extract key entities (stocks, sectors, timeframes)
3. Determine if this requires real-time market data
4. Assess if trade suggestions would be appropriate

Respond in JSON format with: intent, entities, requires_realtime, suggest_trades"""
        
        response = self.llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_query"])
        ])
        
        return {
            "messages": [AIMessage(content=f"Query understood: {response.content}")],
            "next_step": "retrieve_context",
            "iteration_count": state.get("iteration_count", 0) + 1
        }
    
    def retrieve_context(self, state: ResearchState) -> Dict:
        """Step 2: Retrieve relevant context from vector DB and user portfolio"""
        logger.info("Step 2: Retrieving context")
        
        user_context = state.get("user_context", {})
        query = state["user_query"]
        
        # Get semantic search results from vector DB
        search_results = self.research_service.semantic_search(query, limit=5)
        
        # Build context string
        context_parts = []
        
        if search_results:
            context_parts.append("### Previous Research:")
            for result in search_results:
                context_parts.append(f"\n**{result.get('title', 'Untitled')}** (Similarity: {result.get('similarity', 0):.2f})")
                context_parts.append(f"{result.get('summary', '')}\n")
        
        # Search user's portfolio using vector embeddings
        if user_context.get('user_id'):
            try:
                from services.portfolio_embedding_service import PortfolioEmbeddingService
                embedding_service = PortfolioEmbeddingService()
                portfolio_assets = embedding_service.search_user_assets(
                    user_context['user_id'], 
                    query, 
                    limit=5
                )
                
                if portfolio_assets:
                    context_parts.append("\n### Your Relevant Portfolio Holdings:")
                    for asset in portfolio_assets:
                        context_parts.append(f"\n- **{asset.get('symbol')}** ({asset.get('asset_type')})")
                        context_parts.append(f"  Quantity: {asset.get('quantity', 0)}, Value: ₹{asset.get('value', 0):,.2f}")
                        if asset.get('metadata'):
                            context_parts.append(f"  {asset.get('metadata', {}).get('sector', '')}")
            except Exception as e:
                logger.warning(f"Could not search portfolio embeddings: {e}")
        
        if user_context.get('portfolio'):
            context_parts.append("\n### Portfolio Summary:")
            context_parts.append(f"Total Value: ₹{user_context['portfolio'].get('total_value', 0):,.2f}")
            context_parts.append(f"Holdings: {len(user_context['portfolio'].get('holdings', []))} stocks")
        
        retrieved_context = "\n".join(context_parts)
        
        return {
            "retrieved_context": retrieved_context,
            "messages": [AIMessage(content="Context retrieved with portfolio semantic search")],
            "iteration_count": state.get("iteration_count", 0) + 1
        }
    
    def analyze_market(self, state: ResearchState) -> Dict:
        """Step 3: Get real-time market analysis using Perplexity"""
        logger.info("Step 3: Analyzing market with Perplexity")
        
        query = state["user_query"]
        
        # Enhance query for market analysis
        enhanced_query = f"""Analyze the Indian stock market regarding: {query}
        
Focus on:
- Current market prices (NSE/BSE)
- Recent news and developments
- Fundamental analysis
- Technical indicators
- Risk factors

Provide specific, actionable insights with citations."""
        
        try:
            perplexity_response = self.perplexity_service.search(enhanced_query)
            
            market_data = {
                "answer": perplexity_response.get("answer", ""),
                "citations": perplexity_response.get("citations", []),
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Perplexity analysis failed: {e}")
            market_data = {
                "answer": "Real-time market analysis unavailable",
                "citations": [],
                "error": str(e)
            }
        
        return {
            "market_data": market_data,
            "messages": [AIMessage(content="Market analysis completed")],
            "iteration_count": state.get("iteration_count", 0) + 1
        }
    
    def generate_response(self, state: ResearchState) -> Dict:
        """Step 4: Generate comprehensive response with citations"""
        logger.info("Step 4: Generating response")
        
        system_prompt = """You are an expert financial analyst at Capulse. 
Create a comprehensive research report that:
1. Directly answers the user's question
2. Incorporates historical context and current market data
3. Provides specific data points with citations
4. Highlights key risks and opportunities
5. Uses clear, professional language

Format the response in markdown with proper sections."""
        
        user_prompt = f"""User Question: {state['user_query']}

Historical Context:
{state.get('retrieved_context', 'None available')}

Current Market Analysis:
{state.get('market_data', {}).get('answer', 'None available')}

Create a detailed research report."""
        
        response = self.llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        # Extract citations from Perplexity
        citations = state.get('market_data', {}).get('citations', [])
        
        # Append citations to response
        response_with_citations = response.content
        if citations:
            response_with_citations += "\n\n### Sources:\n"
            for i, citation in enumerate(citations, 1):
                response_with_citations += f"{i}. {citation}\n"
        
        return {
            "analysis_result": response_with_citations,
            "messages": [AIMessage(content=response_with_citations)],
            "iteration_count": state.get("iteration_count", 0) + 1
        }
    
    def should_suggest_trades(self, state: ResearchState) -> str:
        """Determine if trade suggestions should be generated"""
        query_lower = state["user_query"].lower()
        
        # Keywords that indicate user wants trade suggestions
        trade_keywords = ["buy", "sell", "trade", "invest", "recommendation", "should i"]
        
        if any(keyword in query_lower for keyword in trade_keywords):
            return "suggest"
        return "end"
    
    def suggest_trades(self, state: ResearchState) -> Dict:
        """Step 5: Generate actionable trade suggestions"""
        logger.info("Step 5: Suggesting trades")
        
        system_prompt = """You are a trade advisor. Based on the research analysis, suggest specific trades.
For each suggestion provide:
- Stock/Symbol
- Action (BUY/SELL)
- Entry Price Range
- Target Price
- Stop Loss
- Reasoning

Output as JSON array."""
        
        user_prompt = f"""Based on this analysis, suggest 2-3 specific trades:

{state.get('analysis_result', '')}

User's current holdings: {state.get('user_context', {}).get('portfolio', {}).get('holdings', [])}"""
        
        response = self.llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        return {
            "trade_suggestions": [{"raw_suggestion": response.content}],
            "messages": [AIMessage(content=f"Trade suggestions generated: {response.content}")],
            "iteration_count": state.get("iteration_count", 0) + 1
        }
    
    def research(self, query: str, user_context: Dict = None) -> Dict:
        """
        Main entry point for research queries
        
        Args:
            query: User's research question
            user_context: User's portfolio and preferences
        
        Returns:
            Dict with answer, citations, and optional trade suggestions
        """
        logger.info(f"Starting LangGraph research for query: {query}")
        
        # Initialize state
        initial_state = {
            "messages": [],
            "user_query": query,
            "user_context": user_context or {},
            "retrieved_context": "",
            "market_data": {},
            "analysis_result": "",
            "trade_suggestions": [],
            "next_step": "understand_query",
            "iteration_count": 0
        }
        
        try:
            # Run the graph
            final_state = self.graph.invoke(initial_state)
            
            return {
                "answer": final_state.get("analysis_result", ""),
                "citations": final_state.get("market_data", {}).get("citations", []),
                "trade_suggestions": final_state.get("trade_suggestions", []),
                "metadata": {
                    "iterations": final_state.get("iteration_count", 0),
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
        except Exception as e:
            logger.error(f"LangGraph research failed: {e}")
            return {
                "answer": f"Research analysis encountered an error: {str(e)}",
                "citations": [],
                "trade_suggestions": [],
                "error": str(e)
            }
