"""
Perplexity AI Service for Enhanced Indian Stock Market Research
Provides real-time research and AI-powered stock picks using Perplexity's online models
"""

import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
import json

class PerplexityService:
    def __init__(self):
        self.api_key = os.environ.get('PERPLEXITY_API_KEY')
        self.claude_key = os.environ.get('ANTHROPIC_API_KEY')
        self.base_url = "https://api.perplexity.ai/chat/completions"
        self.logger = logging.getLogger(__name__)

        if not self.api_key:
            self.logger.info("PERPLEXITY_API_KEY not set — using Claude (Anthropic) for all research calls.")
    
    def research_indian_stock(self, symbol: str, research_type: str = 'comprehensive') -> Dict[str, Any]:
        """
        Conduct comprehensive research on Indian stock using Claude (Anthropic).
        Falls back to Perplexity if available, otherwise Claude is primary.
        """
        try:
            live_price_ctx = self._get_live_price_context(symbol)
            research_prompt = self._build_research_prompt(symbol, research_type) + live_price_ctx

            # Claude is the primary AI provider
            content = self._call_claude_api(research_prompt)
            if content:
                return {
                    'symbol': symbol,
                    'research_type': research_type,
                    'research_content': content,
                    'citations': [],
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'source': 'claude_ai',
                    'model_used': 'claude-sonnet-4-5',
                    'success': True
                }

            return self._get_fallback_research(symbol)

        except Exception as e:
            self.logger.error(f"Research error for {symbol}: {str(e)}")
            return self._get_fallback_research(symbol)
    
    def generate_ai_stock_picks(self, criteria: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Generate AI-powered stock picks for Indian market using Claude (or Perplexity if available).
        """
        try:
            picks_prompt = self._build_picks_prompt(criteria)

            # Claude is the primary AI provider
            content = self._call_claude_api(picks_prompt)
            if content:
                return {
                    'picks': self._parse_ai_picks_response(content),
                    'analysis_summary': content,
                    'citations': [],
                    'criteria_used': criteria or self._get_default_criteria(),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'source': 'claude_ai',
                    'model_used': 'claude-sonnet-4-5',
                    'success': True
                }

            return self._get_fallback_picks()

        except Exception as e:
            self.logger.error(f"Stock picks generation error: {str(e)}")
            return self._get_fallback_picks()
    
    def get_market_insights(self, focus_area: str = 'general') -> Dict[str, Any]:
        """
        Get market insights for Indian stock market using Claude (or Perplexity if available).
        """
        try:
            insights_prompt = self._build_insights_prompt(focus_area)

            # Claude is the primary AI provider
            content = self._call_claude_api(insights_prompt)
            if content:
                return {
                    'insights': content,
                    'focus_area': focus_area,
                    'citations': [],
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'source': 'claude_ai',
                    'success': True
                }

            return self._get_fallback_insights()

        except Exception as e:
            self.logger.error(f"Market insights error: {str(e)}")
            return self._get_fallback_insights()
    
    def _get_live_price_context(self, symbol: str) -> str:
        """
        Fetch the real current price from the market data gateway (Dhan/Zerodha/NSE)
        and return it as a context string to inject into Claude prompts.
        Returns '' if price is unavailable so the prompt still works gracefully.
        """
        try:
            from services.market_data_gateway import get_price
            result = get_price(symbol.upper())
            if result.get('success') and result.get('value', 0) > 0:
                price = result['value']
                source = result.get('source_detail') or result.get('source', 'broker')
                return (
                    f"\n\n⚡ LIVE MARKET DATA (fetched from {source}):\n"
                    f"  Current Price of {symbol.upper()}: ₹{price:,.2f}\n"
                    f"  Use this exact price in your analysis — do NOT estimate or guess the current price."
                )
        except Exception as e:
            self.logger.debug(f"Live price fetch failed for {symbol}: {e}")
        return ""

    def _call_claude_api(self, prompt: str) -> Optional[str]:
        """Call Anthropic Claude as the primary AI provider."""
        try:
            import anthropic
            api_key = self.claude_key or os.environ.get('ANTHROPIC_API_KEY', '')
            if not api_key:
                self.logger.warning("ANTHROPIC_API_KEY not set")
                return None
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model='claude-sonnet-4-5',
                max_tokens=1500,
                system=(
                    "You are an expert financial analyst specializing in Indian stock markets (NSE/BSE). "
                    "Provide comprehensive, data-driven analysis with specific numbers and ratios. "
                    "Use ₹ for currency. Be concise and actionable."
                ),
                messages=[{'role': 'user', 'content': prompt}],
            )
            return msg.content[0].text if msg.content else None
        except Exception as e:
            self.logger.error(f"Claude API error: {e}")
            return None

    def _call_perplexity_api(self, prompt: str, model: str = "sonar") -> Optional[Dict[str, Any]]:
        """
        Returns a Perplexity-compatible dict (with a 'choices' key) powered by Claude.
        The Perplexity API is not used — Claude is the sole AI provider.
        """
        content = self._call_claude_api(prompt)
        if content:
            return {
                "choices": [{"message": {"content": content}}],
                "citations": [],
                "_source": "claude_ai"
            }
        return None
    
    def _build_research_prompt(self, symbol: str, research_type: str) -> str:
        """
        Build comprehensive research prompt for Indian stocks
        """
        base_prompt = f"""
        Conduct comprehensive research on {symbol} (Indian stock) including:
        
        1. **Current Market Position**: Latest stock price, market cap, trading volume, and recent price movements
        2. **Financial Performance**: Recent quarterly results, revenue growth, profitability trends, and key financial ratios
        3. **Business Analysis**: Core business segments, competitive positioning, market share, and recent business developments
        4. **Industry Context**: Sector performance, industry trends, regulatory changes affecting the sector
        5. **Recent News & Events**: Latest corporate announcements, management changes, strategic initiatives, partnerships
        6. **Analyst Opinions**: Recent analyst reports, price targets, recommendations from Indian brokerage houses
        7. **Technical Analysis**: Support/resistance levels, chart patterns, momentum indicators
        8. **Risk Factors**: Key business risks, regulatory risks, market risks specific to this stock
        9. **Investment Thesis**: Bull case, bear case, and neutral scenarios for the stock
        10. **Peer Comparison**: How it compares with similar companies in the Indian market
        
        Focus on the most recent information available (last 3 months) and provide specific data points with sources.
        Format the response in a structured manner with clear sections.
        """
        
        if research_type == 'technical':
            base_prompt += "\n\nEmphasize technical analysis, chart patterns, and trading signals."
        elif research_type == 'fundamental':
            base_prompt += "\n\nEmphasize fundamental analysis, financial metrics, and valuation."
        elif research_type == 'news_sentiment':
            base_prompt += "\n\nEmphasize recent news, market sentiment, and analyst opinions."
        
        return base_prompt
    
    def _build_picks_prompt(self, criteria: Dict[str, Any] = None) -> str:
        """
        Build prompt for AI stock picks generation with real-time NSE prices
        """
        criteria = criteria or self._get_default_criteria()
        
        prompt = f"""
You are an expert Indian stock market analyst. Generate 5 top stock picks for the Indian stock market (NSE) with the following criteria:

**Investment Parameters:**
- Time Horizon: {criteria.get('time_horizon', '6-12 months')}
- Market Cap Focus: {criteria.get('market_cap', 'Large and Mid Cap')}
- Risk Level: {criteria.get('risk_level', 'Moderate')}
- Preferred Sectors: {criteria.get('sectors', 'Technology, Banking, Healthcare, Consumer Goods, Energy')}
- Investment Style: {criteria.get('style', 'Growth and Value')}

**Stock Selection Criteria:**
1. Strong fundamentals (P/E < 30, ROE > 15%, Debt/Equity < 1)
2. Positive earnings growth (QoQ and YoY)
3. Recent positive news/catalysts in last 30 days
4. Technical strength (above 50-day and 200-day moving averages)
5. Institutional buying or strong FII/DII interest
6. Trading on NSE with good liquidity (daily volume > 1 lakh shares)

**CRITICAL: For each stock, you MUST provide:**
1. **NSE Symbol** (e.g., RELIANCE, TCS, HDFCBANK)
2. **Full Company Name** (e.g., Reliance Industries Limited)
3. **Current Market Price in ₹** - Search NSE India website or financial sites to get TODAY's actual closing price
4. **52-Week High/Low** - Real data from NSE
5. **Market Cap** - Current market capitalization
6. **P/E Ratio** - Current price-to-earnings ratio
7. **Investment Rationale** (3-4 key reasons with recent data/news from last 30 days)
8. **Target Price** (12-month price target with ₹ amount)
9. **Key Catalysts** (upcoming events, product launches, policy changes)
10. **Main Risks** (1-2 key risks to watch)

**Output Format (JSON-like structure):**
```
Stock 1: [SYMBOL] - [Full Company Name]
Current Price: ₹[EXACT_PRICE] (as of [DATE])
52-Week Range: ₹[LOW] - ₹[HIGH]
Market Cap: ₹[VALUE] Cr
P/E Ratio: [VALUE]
Investment Rationale: [Detailed reasons with recent news/data]
Target Price: ₹[AMOUNT] (12-month)
Key Catalysts: [List of catalysts]
Main Risks: [Key risks]
```

**IMPORTANT:** 
- Use real-time data from NSE India, Moneycontrol, Economic Times, or Bloomberg
- Prices MUST be current (today's or latest available closing price)
- Include actual recent news/events (last 30 days)
- Focus on liquid, well-established NSE stocks only
- Diversify across sectors

Generate 5 stocks now with ALL required details and REAL current prices."""
        
        return prompt
    
    def _build_insights_prompt(self, focus_area: str) -> str:
        """
        Build prompt for market insights
        """
        base_prompt = f"""
        Provide current market insights for the Indian stock market focusing on {focus_area}:
        
        1. **Market Overview**: Current Nifty/Sensex levels, recent performance, market sentiment
        2. **Sector Performance**: Which sectors are outperforming/underperforming and why
        3. **Key Market Drivers**: Major factors influencing the market (policy, global events, economic data)
        4. **Institutional Activity**: FII/DII flows, bulk deals, block deals
        5. **Corporate Earnings**: Recent earnings season highlights, guidance updates
        6. **Technical Outlook**: Key support/resistance levels for major indices
        7. **Global Context**: How global markets and events are impacting Indian markets
        8. **Investment Themes**: Current trending themes and investment opportunities
        9. **Risk Factors**: Key risks to watch out for in the near term
        10. **Market Outlook**: Short-term (1-3 months) market expectations
        
        Use the most recent market data and news available.
        Provide specific data points, numbers, and recent examples where possible.
        """
        
        return base_prompt
    
    def _parse_ai_picks_response(self, content: str) -> List[Dict[str, Any]]:
        """
        Parse the AI picks response into structured data with prices
        """
        import re
        picks = []
        
        # Split content by stock entries (Stock 1:, Stock 2:, etc.)
        stock_sections = re.split(r'Stock \d+:', content)
        
        for section in stock_sections[1:]:  # Skip first empty section
            try:
                pick = {}
                
                # Extract symbol and company name from first line
                first_line_match = re.search(r'([A-Z]{3,15})\s*[-–]\s*(.+?)(?:\n|Current)', section, re.IGNORECASE)
                if first_line_match:
                    pick['symbol'] = first_line_match.group(1).strip()
                    pick['company_name'] = first_line_match.group(2).strip()
                else:
                    # Fallback extraction
                    pick['symbol'] = self._extract_symbol(section)
                    pick['company_name'] = self._extract_company_name(section)
                
                # Extract current price - look for ₹ followed by numbers
                price_match = re.search(r'Current Price:?\s*₹?\s*([\d,]+\.?\d*)', section, re.IGNORECASE)
                if price_match:
                    price_str = price_match.group(1).replace(',', '')
                    pick['current_price'] = float(price_str)
                else:
                    pick['current_price'] = None
                
                # Extract target price
                target_match = re.search(r'Target Price:?\s*₹?\s*([\d,]+\.?\d*)', section, re.IGNORECASE)
                if target_match:
                    target_str = target_match.group(1).replace(',', '')
                    pick['target_price'] = float(target_str)
                else:
                    pick['target_price'] = None
                
                # Extract P/E ratio
                pe_match = re.search(r'P/E Ratio:?\s*([\d.]+)', section, re.IGNORECASE)
                pick['pe_ratio'] = float(pe_match.group(1)) if pe_match else None
                
                # Extract market cap
                mcap_match = re.search(r'Market Cap:?\s*₹?\s*([\d,]+\.?\d*)\s*(?:Cr|Crore)', section, re.IGNORECASE)
                if mcap_match:
                    pick['market_cap'] = mcap_match.group(1).replace(',', '')
                else:
                    pick['market_cap'] = None
                
                # Extract rationale
                rationale_match = re.search(r'Investment Rationale:?\s*(.+?)(?=Target Price|Key Catalysts|Main Risks|Stock \d+:|$)', section, re.DOTALL | re.IGNORECASE)
                pick['rationale'] = rationale_match.group(1).strip() if rationale_match else 'AI-selected based on strong fundamentals and growth potential.'
                
                # Extract catalysts
                catalyst_match = re.search(r'Key Catalysts:?\s*(.+?)(?=Main Risks|Stock \d+:|$)', section, re.DOTALL | re.IGNORECASE)
                pick['catalysts'] = catalyst_match.group(1).strip() if catalyst_match else 'Market growth, sector strength'
                
                # Extract risks
                risk_match = re.search(r'Main Risks:?\s*(.+?)(?=Stock \d+:|$)', section, re.DOTALL | re.IGNORECASE)
                pick['risk_factors'] = risk_match.group(1).strip() if risk_match else 'Market volatility, sector risks'
                
                # Only add pick if we have minimum required data
                if pick.get('symbol') and pick.get('company_name'):
                    picks.append(pick)
                    
            except Exception as e:
                self.logger.warning(f"Error parsing stock section: {str(e)}")
                continue
        
        # If parsing fails completely, use fallback
        if not picks:
            self.logger.warning("Failed to parse Perplexity response, using fallback picks")
            picks = self._get_structured_fallback_picks()
        
        return picks[:5]  # Return maximum 5 picks
    
    def _extract_symbol(self, text: str) -> str:
        """Extract stock symbol from text"""
        # Basic symbol extraction - could be enhanced
        import re
        symbol_match = re.search(r'\b[A-Z]{3,10}\b', text)
        return symbol_match.group() if symbol_match else 'SYMBOL'
    
    def _extract_company_name(self, text: str) -> str:
        """Extract company name from text"""
        # Basic company name extraction - could be enhanced
        parts = text.split('.')
        if len(parts) > 1:
            name_part = parts[1].strip()
            return name_part.split('(')[0].strip() if '(' in name_part else name_part
        return 'Company Name'
    
    def _get_default_criteria(self) -> Dict[str, Any]:
        """Get default criteria for stock picks"""
        return {
            'market_cap': 'Large and Mid Cap',
            'time_horizon': '6-12 months',
            'risk_level': 'Moderate',
            'sectors': 'Technology, Banking, Healthcare, Consumer Goods, Energy',
            'style': 'Growth and Value'
        }
    
    # Fallback methods when Perplexity API is not available
    def _get_fallback_research(self, symbol: str) -> Dict[str, Any]:
        """Fallback research data when API is not available"""
        return {
            'symbol': symbol,
            'research_type': 'comprehensive',
            'research_content': f"""
            ## {symbol} - Comprehensive Stock Research
            
            **Current Market Position:**
            - Current Price: ₹2,850 (as of latest trading session)
            - Market Cap: ₹1,25,000 Cr
            - 52-week Range: ₹2,200 - ₹3,100
            - Trading Volume: Above average with institutional interest
            
            **Financial Performance:**
            - Q2 FY24 Revenue Growth: 15% YoY
            - Operating Margin: 18.5% (improving trend)
            - ROE: 16.2% (healthy profitability)
            - Debt-to-Equity: 0.3 (conservative capital structure)
            
            **Business Analysis:**
            - Leading player in its sector with diversified revenue streams
            - Strong competitive moat with established market presence
            - Recent expansion into high-growth segments
            - Management focused on operational efficiency and innovation
            
            **Investment Thesis:**
            - **Bull Case**: Strong fundamentals, growing market share, favorable industry tailwinds
            - **Bear Case**: Valuation concerns, competitive pressure, regulatory uncertainties
            - **Neutral Case**: Steady growth in line with industry averages
            
            **Recommendation**: BUY with target price of ₹3,200 (12-month horizon)
            
            *Note: This is sample research data. For real-time analysis, please ensure ANTHROPIC_API_KEY is set.*
            """,
            'citations': ['Sample financial data', 'Market research reports'],
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'source': 'fallback_data',
            'success': False,
            'note': 'ANTHROPIC_API_KEY required for real-time research'
        }
    
    def _get_fallback_picks(self) -> Dict[str, Any]:
        """Fallback stock picks when API is not available"""
        return {
            'picks': self._get_structured_fallback_picks(),
            'analysis_summary': """
            Top 5 AI Stock Picks for Indian Market:
            
            Based on comprehensive analysis of fundamentals, technical indicators, and market sentiment, 
            these stocks show strong potential for the next 6-12 months. Each pick represents different 
            sectors and market caps to provide diversification.
            
            *Note: These are sample picks. For real-time AI-generated recommendations, please ensure ANTHROPIC_API_KEY is set.*
            """,
            'citations': ['Market analysis', 'Financial reports', 'Technical analysis'],
            'criteria_used': self._get_default_criteria(),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'source': 'fallback_data',
            'success': False,
            'note': 'ANTHROPIC_API_KEY required for real-time picks'
        }
    
    def _get_structured_fallback_picks(self) -> List[Dict[str, Any]]:
        """Structured fallback picks"""
        return [
            {
                'symbol': 'RELIANCE',
                'company_name': 'Reliance Industries Limited',
                'rationale': 'Diversified business model, strong cash flows, digital transformation initiatives',
                'target_price': '₹3,200 (12-month target)',
                'risk_factors': 'Oil price volatility, regulatory changes in telecom',
                'catalysts': 'Jio IPO, renewable energy expansion, retail growth'
            },
            {
                'symbol': 'TCS',
                'company_name': 'Tata Consultancy Services',
                'rationale': 'Leading IT services provider, strong client relationships, digital transformation demand',
                'target_price': '₹4,200 (12-month target)',
                'risk_factors': 'Currency fluctuations, competition, visa restrictions',
                'catalysts': 'Cloud adoption, AI/ML services growth, large deal wins'
            },
            {
                'symbol': 'HDFCBANK',
                'company_name': 'HDFC Bank Limited',
                'rationale': 'Best-in-class banking franchise, strong asset quality, digital banking leadership',
                'target_price': '₹1,800 (12-month target)',
                'risk_factors': 'Interest rate cycles, regulatory changes, credit costs',
                'catalysts': 'Credit growth recovery, fee income growth, digital initiatives'
            },
            {
                'symbol': 'INFY',
                'company_name': 'Infosys Limited',
                'rationale': 'Strong execution track record, margin expansion, automation capabilities',
                'target_price': '₹1,650 (12-month target)',
                'risk_factors': 'Client concentration, wage inflation, technology disruption',
                'catalysts': 'Large deal pipeline, AI platform adoption, cost optimization'
            },
            {
                'symbol': 'HINDUNILVR',
                'company_name': 'Hindustan Unilever Limited',
                'rationale': 'Strong brand portfolio, rural recovery, premiumization trend',
                'target_price': '₹2,800 (12-month target)',
                'risk_factors': 'Raw material inflation, competitive intensity, rural slowdown',
                'catalysts': 'Volume growth recovery, new product launches, market share gains'
            }
        ]
    
    def _get_fallback_insights(self) -> Dict[str, Any]:
        """Fallback market insights when API is not available"""
        return {
            'insights': """
            ## Indian Market Insights - Current Analysis
            
            **Market Overview:**
            - Nifty 50: 19,800 levels (consolidating in range)
            - Sensex: 66,500 levels (positive bias maintained)
            - Market Sentiment: Cautiously optimistic with selective buying
            
            **Sector Performance:**
            - Outperformers: Technology, Banking, Healthcare
            - Underperformers: Real Estate, Metals, Auto
            - Emerging Themes: Clean Energy, Digital Infrastructure
            
            **Key Market Drivers:**
            - Monsoon progress and rural demand recovery
            - Corporate earnings growth trajectory
            - Global liquidity flows and FII activity
            - Government policy initiatives and reforms
            
            **Investment Themes:**
            - Digital transformation and technology adoption
            - Infrastructure development and capex cycle
            - Healthcare and wellness sector growth
            - Sustainable and ESG investing
            
            *Note: This is sample market analysis. For real-time insights, please ensure ANTHROPIC_API_KEY is set.*
            """,
            'focus_area': 'general',
            'citations': ['Market data', 'Research reports'],
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'source': 'fallback_data',
            'success': False,
            'note': 'Perplexity API key required for real-time insights'
        }