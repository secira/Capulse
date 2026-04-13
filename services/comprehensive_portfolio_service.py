"""
Comprehensive Portfolio Analytics Service
Aggregates all asset classes and provides AI-powered insights
"""

import os
import logging
from datetime import date
from typing import Dict, List, Any
from sqlalchemy import func

logger = logging.getLogger(__name__)

class ComprehensivePortfolioService:
    """Service for comprehensive portfolio analytics across all asset classes"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.openai_api_key = os.environ.get('OPENAI_API_KEY')
    
    def get_complete_portfolio_summary(self) -> Dict[str, Any]:
        """Get complete portfolio summary across all asset classes"""
        from models import (
            ManualEquityHolding, ManualMutualFundHolding, ManualFixedDepositHolding,
            ManualRealEstateHolding, ManualCommodityHolding, ManualCryptocurrencyHolding,
            ManualFuturesOptionsHolding, ManualInsuranceHolding
        )
        from app import db
        
        summary = {
            'total_investment': 0,
            'total_current_value': 0,
            'total_pnl': 0,
            'pnl_percentage': 0,
            'asset_classes': [],
            'risk_profile': {},
            'asset_distribution': []
        }
        
        # 1. Equities
        equities = ManualEquityHolding.query.filter_by(user_id=self.user_id).all()
        eq_investment = sum(h.total_investment for h in equities)
        eq_value = sum(h.current_value or 0 for h in equities)
        if equities:
            summary['asset_classes'].append({
                'name': 'Equities',
                'count': len(equities),
                'investment': eq_investment,
                'current_value': eq_value,
                'pnl': eq_value - eq_investment,
                'pnl_percentage': ((eq_value - eq_investment) / eq_investment * 100) if eq_investment > 0 else 0,
                'risk_level': 'High',
                'color': '#4CAF50'
            })
        
        # 2. Mutual Funds
        mfs = ManualMutualFundHolding.query.filter_by(user_id=self.user_id).all()
        mf_investment = sum(h.total_investment for h in mfs)
        mf_value = sum(h.current_value or 0 for h in mfs)
        if mfs:
            summary['asset_classes'].append({
                'name': 'Mutual Funds',
                'count': len(mfs),
                'investment': mf_investment,
                'current_value': mf_value,
                'pnl': mf_value - mf_investment,
                'pnl_percentage': ((mf_value - mf_investment) / mf_investment * 100) if mf_investment > 0 else 0,
                'risk_level': 'Medium-High',
                'color': '#2196F3'
            })
        
        # 3. Fixed Deposits
        fds = ManualFixedDepositHolding.query.filter_by(user_id=self.user_id).all()
        fd_investment = sum(f.principal_amount for f in fds)
        fd_value = sum(f.current_value or 0 for f in fds)
        if fds:
            summary['asset_classes'].append({
                'name': 'Fixed Deposits',
                'count': len(fds),
                'investment': fd_investment,
                'current_value': fd_value,
                'pnl': fd_value - fd_investment,
                'pnl_percentage': ((fd_value - fd_investment) / fd_investment * 100) if fd_investment > 0 else 0,
                'risk_level': 'Low',
                'color': '#9C27B0'
            })
        
        # 4. Real Estate
        properties = ManualRealEstateHolding.query.filter_by(user_id=self.user_id).all()
        re_investment = sum(p.total_investment for p in properties)
        re_value = sum(p.current_market_value or 0 for p in properties)
        if properties:
            summary['asset_classes'].append({
                'name': 'Real Estate',
                'count': len(properties),
                'investment': re_investment,
                'current_value': re_value,
                'pnl': re_value - re_investment,
                'pnl_percentage': ((re_value - re_investment) / re_investment * 100) if re_investment > 0 else 0,
                'risk_level': 'Medium',
                'color': '#FF9800'
            })
        
        # 5. Gold & Commodities
        commodities = ManualCommodityHolding.query.filter_by(user_id=self.user_id).all()
        gold_investment = sum(c.total_investment for c in commodities)
        gold_value = sum(c.current_market_value or 0 for c in commodities)
        if commodities:
            summary['asset_classes'].append({
                'name': 'Gold & Commodities',
                'count': len(commodities),
                'investment': gold_investment,
                'current_value': gold_value,
                'pnl': gold_value - gold_investment,
                'pnl_percentage': ((gold_value - gold_investment) / gold_investment * 100) if gold_investment > 0 else 0,
                'risk_level': 'Medium',
                'color': '#FFD700'
            })
        
        # 6. Cryptocurrency
        cryptos = ManualCryptocurrencyHolding.query.filter_by(user_id=self.user_id).all()
        crypto_investment = sum(c.total_investment for c in cryptos)
        crypto_value = sum(c.current_market_value or 0 for c in cryptos)
        if cryptos:
            summary['asset_classes'].append({
                'name': 'Cryptocurrency',
                'count': len(cryptos),
                'investment': crypto_investment,
                'current_value': crypto_value,
                'pnl': crypto_value - crypto_investment,
                'pnl_percentage': ((crypto_value - crypto_investment) / crypto_investment * 100) if crypto_investment > 0 else 0,
                'risk_level': 'Very High',
                'color': '#FF5722'
            })
        
        # 7. F&O Positions
        fnos = ManualFuturesOptionsHolding.query.filter_by(user_id=self.user_id, position_status='Open').all()
        fno_investment = sum(f.total_investment for f in fnos)
        fno_value = sum(f.current_value or 0 for f in fnos)
        if fnos:
            summary['asset_classes'].append({
                'name': 'F&O Positions',
                'count': len(fnos),
                'investment': fno_investment,
                'current_value': fno_value,
                'pnl': fno_value - fno_investment,
                'pnl_percentage': ((fno_value - fno_investment) / fno_investment * 100) if fno_investment > 0 else 0,
                'risk_level': 'Very High',
                'color': '#E91E63'
            })
        
        # 8. Insurance (track premiums paid)
        insurance_policies = ManualInsuranceHolding.query.filter_by(user_id=self.user_id, policy_status='Active').all()
        insurance_paid = sum(p.total_premiums_paid for p in insurance_policies)
        insurance_coverage = sum(p.sum_assured for p in insurance_policies)
        if insurance_policies:
            summary['asset_classes'].append({
                'name': 'Insurance',
                'count': len(insurance_policies),
                'investment': insurance_paid,
                'current_value': insurance_paid,  # Premiums don't appreciate
                'pnl': 0,
                'pnl_percentage': 0,
                'coverage': insurance_coverage,
                'risk_level': 'Protection',
                'color': '#607D8B'
            })

        # 9. Broker-synced holdings (Dhan, Zerodha, Angel, etc.)
        try:
            from models_broker import BrokerHolding, BrokerAccount
            broker_accounts = BrokerAccount.query.filter_by(
                user_id=self.user_id, is_active=True
            ).all()
            broker_account_ids = [a.id for a in broker_accounts]
            if broker_account_ids:
                broker_holdings = BrokerHolding.query.filter(
                    BrokerHolding.broker_account_id.in_(broker_account_ids)
                ).all()
                if broker_holdings:
                    bh_investment = sum(
                        (h.avg_cost_price or 0) * (h.available_quantity or h.total_quantity or 0)
                        for h in broker_holdings
                    )
                    bh_value = sum(
                        (h.current_price or 0) * (h.available_quantity or h.total_quantity or 0)
                        for h in broker_holdings
                    )
                    if bh_investment > 0 or bh_value > 0:
                        broker_names = list({a.broker_name or a.broker_type for a in broker_accounts})
                        label = f"Broker Equities ({', '.join(broker_names)})"
                        summary['asset_classes'].append({
                            'name': label,
                            'count': len(broker_holdings),
                            'investment': bh_investment,
                            'current_value': bh_value,
                            'pnl': bh_value - bh_investment,
                            'pnl_percentage': ((bh_value - bh_investment) / bh_investment * 100) if bh_investment > 0 else 0,
                            'risk_level': 'High',
                            'color': '#4CAF50',
                            'broker_synced': True
                        })
        except Exception as e:
            logger.warning(f"Could not load broker holdings for portfolio summary: {e}")

        # 10. Broker-synced open positions (intraday / delivery positions)
        try:
            from models_broker import BrokerPosition, BrokerAccount as _BA2
            _pos_accounts = _BA2.query.filter_by(user_id=self.user_id, is_active=True).all()
            _broker_account_ids = [a.id for a in _pos_accounts]

            if _broker_account_ids:
                broker_positions = BrokerPosition.query.filter(
                    BrokerPosition.broker_account_id.in_(_broker_account_ids)
                ).all()
                if broker_positions:
                    bp_investment = sum(
                        (p.avg_buy_price or 0) * (p.quantity or 0)
                        for p in broker_positions
                    )
                    bp_value = sum(
                        (p.current_price or p.avg_buy_price or 0) * (p.quantity or 0)
                        for p in broker_positions
                    )
                    if bp_investment > 0 or bp_value > 0:
                        summary['asset_classes'].append({
                            'name': 'Broker Open Positions',
                            'count': len(broker_positions),
                            'investment': bp_investment,
                            'current_value': bp_value if bp_value > 0 else bp_investment,
                            'pnl': bp_value - bp_investment,
                            'pnl_percentage': ((bp_value - bp_investment) / bp_investment * 100) if bp_investment > 0 else 0,
                            'risk_level': 'High',
                            'color': '#F59E0B',
                            'broker_synced': True
                        })
        except Exception as e:
            logger.warning(f"Could not load broker positions for portfolio summary: {e}")

        # Calculate totals
        summary['total_investment'] = sum(ac['investment'] for ac in summary['asset_classes'])
        summary['total_current_value'] = sum(ac['current_value'] for ac in summary['asset_classes'])
        summary['total_pnl'] = summary['total_current_value'] - summary['total_investment']
        summary['pnl_percentage'] = (summary['total_pnl'] / summary['total_investment'] * 100) if summary['total_investment'] > 0 else 0
        
        # Asset distribution
        for asset_class in summary['asset_classes']:
            if summary['total_current_value'] > 0:
                percentage = (asset_class['current_value'] / summary['total_current_value']) * 100
                summary['asset_distribution'].append({
                    'label': asset_class['name'],
                    'value': percentage,
                    'amount': asset_class['current_value'],
                    'color': asset_class['color']
                })
        
        # Risk profile calculation
        summary['risk_profile'] = self._calculate_risk_profile(summary['asset_classes'], summary['total_current_value'])
        
        return summary
    
    def _calculate_risk_profile(self, asset_classes: List[Dict], total_value: float) -> Dict[str, Any]:
        """Calculate portfolio risk profile"""
        risk_weights = {
            'Very High': 1.0,
            'High': 0.75,
            'Medium-High': 0.60,
            'Medium': 0.50,
            'Low': 0.25,
            'Protection': 0.0
        }
        
        weighted_risk = 0
        risk_distribution = {'Very High': 0, 'High': 0, 'Medium': 0, 'Low': 0, 'Protection': 0}
        
        for ac in asset_classes:
            if total_value > 0:
                allocation = ac['current_value'] / total_value
                risk_level = ac['risk_level']
                weighted_risk += risk_weights.get(risk_level, 0.5) * allocation
                
                # Group Medium-High with High for display
                display_risk = risk_level
                if risk_level == 'Medium-High':
                    display_risk = 'High'
                
                if display_risk in risk_distribution:
                    risk_distribution[display_risk] += allocation * 100
        
        # Determine overall risk category
        if weighted_risk >= 0.75:
            risk_category = 'Aggressive'
        elif weighted_risk >= 0.60:
            risk_category = 'Moderately Aggressive'
        elif weighted_risk >= 0.40:
            risk_category = 'Moderate'
        elif weighted_risk >= 0.25:
            risk_category = 'Moderately Conservative'
        else:
            risk_category = 'Conservative'
        
        return {
            'category': risk_category,
            'score': weighted_risk * 100,
            'distribution': risk_distribution
        }
    
    def generate_ai_insights(self, portfolio_summary: Dict[str, Any]) -> str:
        """Generate AI-powered portfolio insights using OpenAI"""
        if not self.openai_api_key:
            return "AI insights not available. Please configure OpenAI API key."
        
        try:
            from openai import OpenAI
            from models import PortfolioPreferences
            client = OpenAI(api_key=self.openai_api_key)
            
            # Get user preferences for personalized recommendations
            prefs = PortfolioPreferences.query.filter_by(user_id=self.user_id).first()
            preferences_text = ""
            if prefs and prefs.completed:
                preferences_text = f"""

Investor Profile & Preferences:
- Age: {prefs.age if prefs.age else 'Not specified'}
- Risk Tolerance: {prefs.risk_tolerance or 'Moderate'}
- Investment Horizon: {prefs.investment_horizon or 'Medium term'}
- Financial Goals: {', '.join(prefs.to_dict().get('financial_goals', [])) if prefs.to_dict().get('financial_goals') else 'Not specified'}
- Expected Return: {f'{prefs.expected_return}% p.a.' if prefs.expected_return else 'Not specified'}
- Preferred Asset Classes: {', '.join(prefs.to_dict().get('preferred_asset_classes', [])) if prefs.to_dict().get('preferred_asset_classes') else 'Not specified'}
- Liquidity Requirement: {prefs.liquidity_requirement or 'Medium'}
"""
            
            # Prepare portfolio data for AI
            portfolio_text = f"""
Portfolio Summary:
- Total Value: ₹{portfolio_summary['total_current_value']:,.0f}
- Total Investment: ₹{portfolio_summary['total_investment']:,.0f}
- Overall P&L: ₹{portfolio_summary['total_pnl']:,.0f} ({portfolio_summary['pnl_percentage']:.2f}%)
- Risk Profile: {portfolio_summary['risk_profile']['category']}

Asset Allocation:
"""
            total_val = portfolio_summary['total_current_value'] or 1
            for ac in portfolio_summary['asset_classes']:
                portfolio_text += f"- {ac['name']}: ₹{ac['current_value']:,.0f} ({ac['current_value']/total_val*100:.1f}%) | P&L: {ac['pnl_percentage']:.1f}% | Risk: {ac['risk_level']}\n"
            
            prompt = f"""As a financial advisor, analyze this Indian investor's portfolio and provide PERSONALIZED recommendations:
1. Overall portfolio health assessment (aligned with investor's goals and risk tolerance)
2. Key strengths and weaknesses (relative to investor's preferences)
3. Diversification analysis (compared to preferred asset classes)
4. Risk assessment (relative to investor's risk tolerance)
5. 3-4 specific actionable recommendations (tailored to investment horizon and goals)

{portfolio_text}{preferences_text}

Provide concise, actionable insights in 150-200 words that align with the investor's profile and preferences."""
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an expert financial advisor specializing in Indian markets and portfolio management."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
                temperature=0.7
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"AI insights generation failed: {str(e)}")
            return f"AI analysis temporarily unavailable. Your portfolio shows a total value of ₹{portfolio_summary['total_current_value']/10000000:.2f} Cr with {portfolio_summary['pnl_percentage']:.1f}% returns."
    
    def get_top_performers(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top performing assets across all classes"""
        from models import ManualEquityHolding, ManualMutualFundHolding, ManualCryptocurrencyHolding
        
        performers = []
        
        # Equities
        equities = ManualEquityHolding.query.filter_by(user_id=self.user_id).all()
        for eq in equities:
            if eq.unrealized_pnl_percentage:
                performers.append({
                    'name': eq.symbol,
                    'type': 'Equity',
                    'return': eq.unrealized_pnl_percentage,
                    'value': eq.current_value or 0
                })
        
        # Mutual Funds
        mfs = ManualMutualFundHolding.query.filter_by(user_id=self.user_id).all()
        for mf in mfs:
            if mf.unrealized_pnl_percentage:
                performers.append({
                    'name': mf.scheme_name[:30],
                    'type': 'Mutual Fund',
                    'return': mf.unrealized_pnl_percentage,
                    'value': mf.current_value or 0
                })
        
        # Crypto
        cryptos = ManualCryptocurrencyHolding.query.filter_by(user_id=self.user_id).all()
        for crypto in cryptos:
            if crypto.unrealized_gain_percentage:
                performers.append({
                    'name': crypto.crypto_symbol,
                    'type': 'Cryptocurrency',
                    'return': crypto.unrealized_gain_percentage,
                    'value': crypto.current_market_value or 0
                })
        
        # Sort by return and get top performers
        performers.sort(key=lambda x: x['return'], reverse=True)
        return performers[:limit]


def get_comprehensive_portfolio_service(user_id: int) -> ComprehensivePortfolioService:
    """Factory function to get portfolio service instance"""
    return ComprehensivePortfolioService(user_id)
