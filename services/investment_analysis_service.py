"""
Investment Analysis Service for AI Advisor
Provides comprehensive investment analysis using AI and market data
"""

import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
import json
from openai import OpenAI

class InvestmentAnalysisService:
    def __init__(self):
        self.openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        self.alpha_vantage_key = os.environ.get('ALPHA_VANTAGE_API_KEY')
        self.logger = logging.getLogger(__name__)
        
    def analyze_stock_comprehensive(self, symbol: str, user_portfolio: Dict = None) -> Dict[str, Any]:
        """Comprehensive AI-powered stock analysis"""
        try:
            # Get fundamental data
            fundamental_data = self._get_fundamental_data(symbol)
            
            # Get technical data
            technical_data = self._get_technical_data(symbol)
            
            # Get news sentiment
            news_sentiment = self._get_news_sentiment(symbol)
            
            # Perform AI analysis
            ai_analysis = self._perform_ai_analysis(symbol, fundamental_data, technical_data, news_sentiment, user_portfolio)
            
            return {
                'symbol': symbol,
                'analysis_timestamp': datetime.now(timezone.utc).isoformat(),
                'fundamental_analysis': fundamental_data,
                'technical_analysis': technical_data,
                'sentiment_analysis': news_sentiment,
                'ai_recommendation': ai_analysis,
                'overall_score': self._calculate_overall_score(fundamental_data, technical_data, news_sentiment, ai_analysis)
            }
            
        except Exception as e:
            self.logger.error(f"Stock analysis error for {symbol}: {str(e)}")
            return {
                'symbol': symbol,
                'error': 'Analysis failed',
                'analysis_timestamp': datetime.now(timezone.utc).isoformat()
            }
    
    def generate_portfolio_insights(self, portfolio_data: Dict, user_preferences: Dict = None) -> Dict[str, Any]:
        """Generate AI-powered portfolio insights and recommendations"""
        try:
            # Analyze portfolio composition
            composition_analysis = self._analyze_portfolio_composition(portfolio_data)
            
            # Risk assessment
            risk_analysis = self._assess_portfolio_risk(portfolio_data)
            
            # Diversification analysis
            diversification_analysis = self._analyze_diversification(portfolio_data)
            
            # Generate AI recommendations
            ai_recommendations = self._generate_portfolio_recommendations(
                portfolio_data, composition_analysis, risk_analysis, diversification_analysis, user_preferences
            )
            
            return {
                'portfolio_summary': {
                    'total_value': portfolio_data.get('total_value', 0),
                    'num_positions': len(portfolio_data.get('positions', [])),
                    'analysis_date': datetime.now(timezone.utc).isoformat()
                },
                'composition_analysis': composition_analysis,
                'risk_analysis': risk_analysis,
                'diversification_analysis': diversification_analysis,
                'ai_recommendations': ai_recommendations,
                'overall_health_score': self._calculate_portfolio_health_score(composition_analysis, risk_analysis, diversification_analysis)
            }
            
        except Exception as e:
            self.logger.error(f"Portfolio analysis error: {str(e)}")
            return {
                'error': 'Portfolio analysis failed',
                'analysis_timestamp': datetime.now(timezone.utc).isoformat()
            }
    
    def get_market_opportunities(self, investment_amount: float = None, risk_tolerance: str = 'moderate') -> Dict[str, Any]:
        """Identify market opportunities based on AI analysis"""
        try:
            # Get market screening results
            screening_results = self._screen_market_opportunities(risk_tolerance)
            
            # Analyze each opportunity with AI
            analyzed_opportunities = []
            for opportunity in screening_results[:10]:  # Analyze top 10
                analysis = self._analyze_opportunity(opportunity, investment_amount, risk_tolerance)
                if analysis:
                    analyzed_opportunities.append(analysis)
            
            # Rank opportunities
            ranked_opportunities = sorted(analyzed_opportunities, key=lambda x: x.get('ai_score', 0), reverse=True)
            
            return {
                'opportunities': ranked_opportunities[:5],  # Return top 5
                'market_context': self._get_market_context(),
                'investment_themes': self._identify_investment_themes(ranked_opportunities),
                'risk_factors': self._identify_risk_factors(),
                'analysis_timestamp': datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Market opportunities analysis error: {str(e)}")
            return {
                'error': 'Market opportunities analysis failed',
                'analysis_timestamp': datetime.now(timezone.utc).isoformat()
            }
    
    def _get_fundamental_data(self, symbol: str) -> Dict[str, Any]:
        """Get fundamental analysis data"""
        try:
            if not self.alpha_vantage_key:
                return self._get_mock_fundamental_data(symbol)
            
            # Company overview
            url = f"https://www.alphavantage.co/query"
            params = {
                'function': 'OVERVIEW',
                'symbol': symbol,
                'apikey': self.alpha_vantage_key
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return self._process_fundamental_data(data)
            else:
                return self._get_mock_fundamental_data(symbol)
                
        except Exception as e:
            self.logger.error(f"Fundamental data error for {symbol}: {str(e)}")
            return self._get_mock_fundamental_data(symbol)
    
    def _get_technical_data(self, symbol: str) -> Dict[str, Any]:
        """Get technical analysis data"""
        try:
            if not self.alpha_vantage_key:
                return self._get_mock_technical_data(symbol)
            
            # Get daily data for technical analysis
            url = f"https://www.alphavantage.co/query"
            params = {
                'function': 'TIME_SERIES_DAILY',
                'symbol': symbol,
                'outputsize': 'compact',
                'apikey': self.alpha_vantage_key
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return self._process_technical_data(data)
            else:
                return self._get_mock_technical_data(symbol)
                
        except Exception as e:
            self.logger.error(f"Technical data error for {symbol}: {str(e)}")
            return self._get_mock_technical_data(symbol)
    
    def _get_news_sentiment(self, symbol: str) -> Dict[str, Any]:
        """Get news sentiment analysis"""
        try:
            if not self.alpha_vantage_key:
                return self._get_mock_sentiment_data(symbol)
            
            url = f"https://www.alphavantage.co/query"
            params = {
                'function': 'NEWS_SENTIMENT',
                'tickers': symbol,
                'apikey': self.alpha_vantage_key,
                'limit': 20
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return self._process_sentiment_data(data, symbol)
            else:
                return self._get_mock_sentiment_data(symbol)
                
        except Exception as e:
            self.logger.error(f"Sentiment data error for {symbol}: {str(e)}")
            return self._get_mock_sentiment_data(symbol)
    
    def _perform_ai_analysis(self, symbol: str, fundamental: Dict, technical: Dict, sentiment: Dict, portfolio: Dict = None) -> Dict[str, Any]:
        """Perform comprehensive AI analysis using GPT-4o"""
        try:
            # Prepare context for AI analysis
            analysis_context = f"""
            Analyze the following investment data for {symbol}:
            
            Fundamental Data: {json.dumps(fundamental, indent=2)}
            Technical Data: {json.dumps(technical, indent=2)}
            Sentiment Data: {json.dumps(sentiment, indent=2)}
            {"Portfolio Context: " + json.dumps(portfolio, indent=2) if portfolio else ""}
            
            Provide a comprehensive investment analysis including:
            1. Investment recommendation (Strong Buy/Buy/Hold/Sell/Strong Sell)
            2. Confidence level (0-100%)
            3. Key strengths and weaknesses
            4. Risk factors
            5. Price target (if applicable)
            6. Time horizon recommendation
            7. Position sizing recommendation
            
            Format the response as JSON with clear structure.
            """
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert investment analyst with deep knowledge of fundamental analysis, technical analysis, and market sentiment. Provide detailed, actionable investment insights based on comprehensive data analysis."
                    },
                    {
                        "role": "user",
                        "content": analysis_context
                    }
                ],
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.1
            )
            
            ai_response = json.loads(response.choices[0].message.content)
            
            return {
                'recommendation': ai_response.get('recommendation', 'Hold'),
                'confidence': ai_response.get('confidence', 50),
                'strengths': ai_response.get('strengths', []),
                'weaknesses': ai_response.get('weaknesses', []),
                'risk_factors': ai_response.get('risk_factors', []),
                'price_target': ai_response.get('price_target'),
                'time_horizon': ai_response.get('time_horizon', '3-6 months'),
                'position_sizing': ai_response.get('position_sizing', 'Standard'),
                'analysis_summary': ai_response.get('analysis_summary', 'AI analysis completed'),
                'generated_at': datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"AI analysis error for {symbol}: {str(e)}")
            return {
                'recommendation': 'Hold',
                'confidence': 50,
                'analysis_summary': 'AI analysis failed',
                'generated_at': datetime.now(timezone.utc).isoformat()
            }
    
    def _analyze_portfolio_composition(self, portfolio_data: Dict) -> Dict[str, Any]:
        """Analyze portfolio composition"""
        positions = portfolio_data.get('positions', [])
        total_value = portfolio_data.get('total_value', 0)
        
        if not positions:
            return {'error': 'No positions found'}
        
        # Sector analysis
        sector_allocation = {}
        # Market cap analysis
        market_cap_allocation = {'large_cap': 0, 'mid_cap': 0, 'small_cap': 0}
        # Geographic allocation
        geographic_allocation = {'domestic': 0, 'international': 0}
        
        for position in positions:
            value = position.get('value', 0)
            percentage = (value / total_value) * 100 if total_value > 0 else 0
            
            # This would typically use real sector data
            sector = self._get_stock_sector(position.get('symbol', ''))
            sector_allocation[sector] = sector_allocation.get(sector, 0) + percentage
        
        return {
            'sector_allocation': sector_allocation,
            'market_cap_allocation': market_cap_allocation,
            'geographic_allocation': geographic_allocation,
            'concentration_risk': max(sector_allocation.values()) if sector_allocation else 0,
            'diversification_score': self._calculate_diversification_score(sector_allocation)
        }
    
    def _assess_portfolio_risk(self, portfolio_data: Dict) -> Dict[str, Any]:
        """Assess portfolio risk metrics"""
        # This would typically use real volatility and correlation data
        return {
            'overall_risk_level': 'Moderate',
            'volatility_estimate': 15.2,
            'beta': 1.1,
            'max_drawdown_estimate': -12.5,
            'risk_score': 6.8,  # Out of 10
            'risk_factors': [
                'Market concentration in technology sector',
                'High correlation between holdings',
                'Limited international exposure'
            ]
        }
    
    def _analyze_diversification(self, portfolio_data: Dict) -> Dict[str, Any]:
        """Analyze portfolio diversification"""
        positions = portfolio_data.get('positions', [])
        
        return {
            'diversification_score': 7.2,  # Out of 10
            'number_of_holdings': len(positions),
            'sector_diversification': 'Moderate',
            'geographic_diversification': 'Limited',
            'asset_class_diversification': 'Limited to Equities',
            'recommendations': [
                'Add international exposure',
                'Consider bond allocation',
                'Reduce sector concentration'
            ]
        }
    
    def _generate_portfolio_recommendations(self, portfolio_data: Dict, composition: Dict, risk: Dict, diversification: Dict, preferences: Dict = None) -> Dict[str, Any]:
        """Generate AI-powered portfolio recommendations"""
        try:
            context = f"""
            Portfolio Analysis Summary:
            Total Value: {portfolio_data.get('total_value', 0)}
            Number of Positions: {len(portfolio_data.get('positions', []))}
            
            Composition Analysis: {json.dumps(composition, indent=2)}
            Risk Analysis: {json.dumps(risk, indent=2)}
            Diversification Analysis: {json.dumps(diversification, indent=2)}
            
            {"User Preferences: " + json.dumps(preferences, indent=2) if preferences else ""}
            
            Provide specific portfolio recommendations including:
            1. Immediate actions to take
            2. Asset allocation adjustments
            3. Risk management suggestions
            4. Diversification improvements
            5. Performance optimization strategies
            
            Format as JSON with actionable recommendations.
            """
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a senior portfolio manager with expertise in asset allocation, risk management, and portfolio optimization. Provide practical, actionable recommendations based on comprehensive portfolio analysis."
                    },
                    {
                        "role": "user",
                        "content": context
                    }
                ],
                response_format={"type": "json_object"},
                max_tokens=1200,
                temperature=0.1
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            self.logger.error(f"Portfolio recommendations error: {str(e)}")
            return {
                'immediate_actions': ['Review portfolio allocation'],
                'asset_allocation_adjustments': ['Maintain current allocation'],
                'risk_management_suggestions': ['Monitor portfolio regularly'],
                'diversification_improvements': ['Consider adding more sectors'],
                'performance_optimization': ['Regular rebalancing recommended']
            }
    
    # Helper methods and mock data
    def _get_stock_sector(self, symbol: str) -> str:
        """Get stock sector (mock implementation)"""
        sector_map = {
            'RELIANCE': 'Energy',
            'TCS': 'Technology',
            'INFY': 'Technology',
            'HDFCBANK': 'Financial',
            'ICICIBANK': 'Financial',
            'HINDUNILVR': 'Consumer Goods',
            'SUNPHARMA': 'Healthcare'
        }
        return sector_map.get(symbol.upper(), 'Mixed')
    
    def _calculate_diversification_score(self, sector_allocation: Dict) -> float:
        """Calculate diversification score based on sector allocation"""
        if not sector_allocation:
            return 0.0
        
        # Herfindahl Index calculation
        hhi = sum([(percentage/100)**2 for percentage in sector_allocation.values()])
        
        # Convert to diversification score (lower HHI = better diversification)
        diversification_score = max(0, 10 - (hhi * 10))
        
        return round(diversification_score, 1)
    
    def _calculate_overall_score(self, fundamental: Dict, technical: Dict, sentiment: Dict, ai_analysis: Dict) -> float:
        """Calculate overall investment score"""
        scores = []
        
        # Fundamental score (0-10)
        if 'pe_ratio' in fundamental:
            pe_score = max(0, min(10, 10 - abs(fundamental['pe_ratio'] - 15) / 5))
            scores.append(pe_score)
        
        # Technical score (0-10)
        if 'trend_direction' in technical:
            trend_score = 8 if technical['trend_direction'] == 'Up' else 5 if technical['trend_direction'] == 'Sideways' else 3
            scores.append(trend_score)
        
        # Sentiment score (0-10)
        if 'sentiment_score' in sentiment:
            sent_score = min(10, max(0, (sentiment['sentiment_score'] + 1) * 5))
            scores.append(sent_score)
        
        # AI confidence score (0-10)
        if 'confidence' in ai_analysis:
            ai_score = ai_analysis['confidence'] / 10
            scores.append(ai_score)
        
        return round(sum(scores) / len(scores), 1) if scores else 5.0
    
    def _calculate_portfolio_health_score(self, composition: Dict, risk: Dict, diversification: Dict) -> float:
        """Calculate overall portfolio health score"""
        scores = []
        
        # Diversification score
        if 'diversification_score' in diversification:
            scores.append(diversification['diversification_score'])
        
        # Risk score (inverted, lower risk = higher score)
        if 'risk_score' in risk:
            risk_score = 10 - risk['risk_score']
            scores.append(max(0, risk_score))
        
        # Concentration risk (inverted)
        if 'concentration_risk' in composition:
            conc_score = max(0, 10 - (composition['concentration_risk'] / 10))
            scores.append(conc_score)
        
        return round(sum(scores) / len(scores), 1) if scores else 5.0
    
    # Mock data methods
    def _get_mock_fundamental_data(self, symbol: str) -> Dict[str, Any]:
        return {
            'pe_ratio': 22.5,
            'pb_ratio': 3.2,
            'roe': 15.8,
            'revenue_growth': 12.3,
            'profit_margin': 8.9,
            'debt_to_equity': 0.45,
            'market_cap': 1250000000000,  # 1.25T
            'dividend_yield': 2.1
        }
    
    def _get_mock_technical_data(self, symbol: str) -> Dict[str, Any]:
        return {
            'trend_direction': 'Up',
            'support_level': 2850,
            'resistance_level': 3100,
            'rsi': 58.3,
            'moving_average_50': 2920,
            'moving_average_200': 2780,
            'volume_trend': 'Increasing',
            'momentum': 'Positive'
        }
    
    def _get_mock_sentiment_data(self, symbol: str) -> Dict[str, Any]:
        return {
            'sentiment_score': 0.15,
            'sentiment_label': 'Positive',
            'news_count': 25,
            'positive_news': 15,
            'neutral_news': 8,
            'negative_news': 2
        }
    
    def _screen_market_opportunities(self, risk_tolerance: str) -> List[Dict]:
        """Screen market for opportunities (mock implementation)"""
        return [
            {'symbol': 'TECHM', 'score': 8.5, 'sector': 'Technology'},
            {'symbol': 'MARUTI', 'score': 8.2, 'sector': 'Automotive'},
            {'symbol': 'ASIANPAINT', 'score': 8.0, 'sector': 'Consumer Goods'},
            {'symbol': 'BAJFINANCE', 'score': 7.8, 'sector': 'Financial'},
            {'symbol': 'DRREDDY', 'score': 7.5, 'sector': 'Healthcare'}
        ]
    
    def _analyze_opportunity(self, opportunity: Dict, investment_amount: float = None, risk_tolerance: str = 'moderate') -> Dict[str, Any]:
        """Analyze individual market opportunity"""
        return {
            'symbol': opportunity['symbol'],
            'sector': opportunity['sector'],
            'ai_score': opportunity['score'],
            'recommendation': 'Buy' if opportunity['score'] > 7.5 else 'Hold',
            'risk_level': 'Moderate',
            'potential_return': '12-18%',
            'time_horizon': '6-12 months'
        }
    
    def _get_market_context(self) -> Dict[str, Any]:
        """Get current market context"""
        return {
            'market_phase': 'Bull Market',
            'volatility': 'Moderate',
            'sector_rotation': 'Technology to Healthcare',
            'key_themes': ['Digital Transformation', 'ESG Investing', 'Healthcare Innovation']
        }
    
    def _identify_investment_themes(self, opportunities: List[Dict]) -> List[str]:
        """Identify key investment themes"""
        return [
            'Technology Innovation',
            'Healthcare Transformation',
            'Sustainable Energy',
            'Financial Digitalization'
        ]
    
    def _identify_risk_factors(self) -> List[str]:
        """Identify current market risk factors"""
        return [
            'Interest rate volatility',
            'Geopolitical tensions',
            'Supply chain disruptions',
            'Regulatory changes'
        ]
    
    def _process_fundamental_data(self, data: Dict) -> Dict[str, Any]:
        """Process fundamental data from Alpha Vantage"""
        try:
            return {
                'pe_ratio': float(data.get('PERatio', 0)) if data.get('PERatio') != 'None' else None,
                'pb_ratio': float(data.get('PriceToBookRatio', 0)) if data.get('PriceToBookRatio') != 'None' else None,
                'roe': float(data.get('ReturnOnEquityTTM', 0)) if data.get('ReturnOnEquityTTM') != 'None' else None,
                'revenue_growth': float(data.get('QuarterlyRevenueGrowthYOY', 0)) if data.get('QuarterlyRevenueGrowthYOY') != 'None' else None,
                'profit_margin': float(data.get('ProfitMargin', 0)) if data.get('ProfitMargin') != 'None' else None,
                'debt_to_equity': float(data.get('DebtToEquity', 0)) if data.get('DebtToEquity') != 'None' else None,
                'market_cap': int(data.get('MarkeCapulseization', 0)) if data.get('MarkeCapulseization') != 'None' else None,
                'dividend_yield': float(data.get('DividendYield', 0)) if data.get('DividendYield') != 'None' else None
            }
        except:
            return self._get_mock_fundamental_data('')
    
    def _process_technical_data(self, data: Dict) -> Dict[str, Any]:
        """Process technical data from Alpha Vantage"""
        try:
            if 'Time Series (Daily)' not in data:
                return self._get_mock_technical_data('')
            
            time_series = data['Time Series (Daily)']
            dates = sorted(time_series.keys(), reverse=True)
            
            if len(dates) < 5:
                return self._get_mock_technical_data('')
            
            # Get recent prices
            recent_prices = [float(time_series[date]['4. close']) for date in dates[:20]]
            current_price = recent_prices[0]
            
            # Calculate simple moving averages
            sma_5 = sum(recent_prices[:5]) / 5
            sma_20 = sum(recent_prices[:20]) / 20 if len(recent_prices) >= 20 else sum(recent_prices) / len(recent_prices)
            
            # Determine trend
            trend = 'Up' if current_price > sma_5 > sma_20 else 'Down' if current_price < sma_5 < sma_20 else 'Sideways'
            
            return {
                'trend_direction': trend,
                'current_price': current_price,
                'sma_5': round(sma_5, 2),
                'sma_20': round(sma_20, 2),
                'support_level': min(recent_prices) * 0.98,
                'resistance_level': max(recent_prices) * 1.02,
                'volume_trend': 'Normal',
                'momentum': 'Positive' if trend == 'Up' else 'Negative' if trend == 'Down' else 'Neutral'
            }
        except:
            return self._get_mock_technical_data('')
    
    def _process_sentiment_data(self, data: Dict, symbol: str) -> Dict[str, Any]:
        """Process sentiment data from Alpha Vantage"""
        try:
            if 'feed' not in data:
                return self._get_mock_sentiment_data(symbol)
            
            articles = [article for article in data['feed'] if any(ticker['ticker'] == symbol for ticker in article.get('ticker_sentiment', []))]
            
            if not articles:
                return self._get_mock_sentiment_data(symbol)
            
            # Calculate sentiment scores
            sentiment_scores = []
            for article in articles[:20]:  # Analyze recent 20 articles
                for ticker_sentiment in article.get('ticker_sentiment', []):
                    if ticker_sentiment['ticker'] == symbol:
                        score = float(ticker_sentiment.get('ticker_sentiment_score', 0))
                        sentiment_scores.append(score)
            
            if not sentiment_scores:
                return self._get_mock_sentiment_data(symbol)
            
            avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
            
            # Categorize sentiment
            if avg_sentiment > 0.15:
                sentiment_label = 'Very Positive'
            elif avg_sentiment > 0.05:
                sentiment_label = 'Positive'
            elif avg_sentiment > -0.05:
                sentiment_label = 'Neutral'
            elif avg_sentiment > -0.15:
                sentiment_label = 'Negative'
            else:
                sentiment_label = 'Very Negative'
            
            return {
                'sentiment_score': round(avg_sentiment, 3),
                'sentiment_label': sentiment_label,
                'news_count': len(articles),
                'positive_news': len([s for s in sentiment_scores if s > 0.05]),
                'neutral_news': len([s for s in sentiment_scores if -0.05 <= s <= 0.05]),
                'negative_news': len([s for s in sentiment_scores if s < -0.05])
            }
        except:
            return self._get_mock_sentiment_data(symbol)