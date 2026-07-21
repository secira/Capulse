"""
MFapi.in Service for Mutual Fund Data
Provides real-time NAV data, scheme information, and performance metrics
"""

import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class MFApiService:
    """Service to fetch mutual fund data from MFapi.in"""
    
    BASE_URL = "https://api.mfapi.in/mf"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'TargeTarget Capital/1.0'
        })
    
    def search_fund(self, query: str) -> List[Dict[str, Any]]:
        """Search for mutual funds by name"""
        try:
            response = self.session.get(f"{self.BASE_URL}/search?q={query}", timeout=6)
            response.raise_for_status()
            results = response.json()
            logger.info(f"Found {len(results)} funds matching '{query}'")
            return results
        except Exception as e:
            logger.error(f"Error searching funds: {e}")
            return []
    
    def get_fund_details(self, scheme_code: int) -> Dict[str, Any]:
        """Get detailed fund information and NAV history"""
        try:
            response = self.session.get(f"{self.BASE_URL}/{scheme_code}", timeout=8)
            response.raise_for_status()
            data = response.json()
            
            meta = data.get('meta', {})
            nav_data = data.get('data', [])
            
            result = {
                'scheme_code': meta.get('scheme_code'),
                'scheme_name': meta.get('scheme_name', ''),
                'fund_house': meta.get('fund_house', ''),
                'scheme_type': meta.get('scheme_type', ''),
                'scheme_category': meta.get('scheme_category', ''),
                'nav_history': nav_data,
                'current_nav': float(nav_data[0]['nav']) if nav_data else 0,
                'nav_date': nav_data[0]['date'] if nav_data else '',
                'success': True
            }
            
            if nav_data:
                result.update(self._calculate_returns(nav_data))
                result.update(self._calculate_risk_metrics(nav_data))
            
            logger.info(f"Retrieved fund details for {meta.get('scheme_name')}")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching fund details for {scheme_code}: {e}")
            return {'success': False, 'error': str(e)}
    
    def _calculate_returns(self, nav_data: List[Dict]) -> Dict[str, float]:
        """Calculate returns for various time periods"""
        if not nav_data or len(nav_data) < 2:
            return {}
        
        try:
            current_nav = float(nav_data[0]['nav'])
            returns = {}
            
            nav_by_date = {}
            for entry in nav_data:
                try:
                    date_str = entry['date']
                    date = datetime.strptime(date_str, '%d-%m-%Y')
                    nav_by_date[date] = float(entry['nav'])
                except:
                    continue
            
            today = max(nav_by_date.keys()) if nav_by_date else datetime.now()
            
            periods = {
                '1w': 7,
                '1m': 30,
                '3m': 90,
                '6m': 180,
                '1y': 365,
                '2y': 730,
                '3y': 1095,
                '5y': 1825
            }
            
            for period_name, days in periods.items():
                target_date = today - timedelta(days=days)
                closest_date = None
                min_diff = float('inf')
                
                for date in nav_by_date.keys():
                    diff = abs((date - target_date).days)
                    if diff < min_diff:
                        min_diff = diff
                        closest_date = date
                
                if closest_date and min_diff <= 30:
                    old_nav = nav_by_date[closest_date]
                    if old_nav > 0:
                        return_pct = ((current_nav - old_nav) / old_nav) * 100
                        returns[f'return_{period_name}'] = round(return_pct, 2)
                        
                        if days >= 365:
                            years = days / 365
                            cagr = ((current_nav / old_nav) ** (1/years) - 1) * 100
                            returns[f'cagr_{period_name}'] = round(cagr, 2)
            
            return returns
            
        except Exception as e:
            logger.error(f"Error calculating returns: {e}")
            return {}
    
    def _calculate_risk_metrics(self, nav_data: List[Dict]) -> Dict[str, float]:
        """Calculate risk metrics like volatility"""
        if len(nav_data) < 30:
            return {}
        
        try:
            navs = []
            for entry in nav_data[:365]:
                try:
                    navs.append(float(entry['nav']))
                except:
                    continue
            
            if len(navs) < 30:
                return {}
            
            daily_returns = []
            for i in range(1, len(navs)):
                if navs[i-1] > 0:
                    ret = (navs[i] - navs[i-1]) / navs[i-1]
                    daily_returns.append(ret)
            
            if not daily_returns:
                return {}
            
            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
            std_dev = variance ** 0.5
            
            annualized_volatility = std_dev * (252 ** 0.5) * 100
            annualized_return = mean_return * 252 * 100
            
            risk_free_rate = 6.0
            sharpe_ratio = (annualized_return - risk_free_rate) / annualized_volatility if annualized_volatility > 0 else 0
            
            return {
                'volatility': round(annualized_volatility, 2),
                'sharpe_ratio': round(sharpe_ratio, 2),
                'annualized_return': round(annualized_return, 2)
            }
            
        except Exception as e:
            logger.error(f"Error calculating risk metrics: {e}")
            return {}
    
    def get_fund_by_name(self, fund_name: str) -> Optional[Dict[str, Any]]:
        """Search and get fund details by name"""
        results = self.search_fund(fund_name)
        
        direct_growth_funds = [f for f in results if 'DIRECT' in f['schemeName'].upper() and 'GROWTH' in f['schemeName'].upper()]
        
        if direct_growth_funds:
            return self.get_fund_details(direct_growth_funds[0]['schemeCode'])
        elif results:
            return self.get_fund_details(results[0]['schemeCode'])
        
        return None
    
    def analyze_for_iscore(self, scheme_code_or_name: str) -> Dict[str, Any]:
        """Get comprehensive fund data for I-Score analysis"""
        try:
            if scheme_code_or_name.isdigit():
                fund_data = self.get_fund_details(int(scheme_code_or_name))
            else:
                fund_data = self.get_fund_by_name(scheme_code_or_name)
            
            if not fund_data or not fund_data.get('success'):
                return {'success': False, 'error': 'Fund not found'}
            
            fund_age_years = self._estimate_fund_age(fund_data.get('nav_history', []))
            
            analysis = {
                'success': True,
                'scheme_code': fund_data.get('scheme_code'),
                'scheme_name': fund_data.get('scheme_name'),
                'fund_house': fund_data.get('fund_house'),
                'scheme_type': fund_data.get('scheme_type'),
                'scheme_category': fund_data.get('scheme_category'),
                'current_nav': fund_data.get('current_nav'),
                'nav_date': fund_data.get('nav_date'),
                'fund_age_years': fund_age_years,
                'returns': {
                    '1w': fund_data.get('return_1w'),
                    '1m': fund_data.get('return_1m'),
                    '3m': fund_data.get('return_3m'),
                    '6m': fund_data.get('return_6m'),
                    '1y': fund_data.get('return_1y'),
                    '3y': fund_data.get('return_3y'),
                    '5y': fund_data.get('return_5y')
                },
                'cagr': {
                    '1y': fund_data.get('cagr_1y'),
                    '3y': fund_data.get('cagr_3y'),
                    '5y': fund_data.get('cagr_5y')
                },
                'risk_metrics': {
                    'volatility': fund_data.get('volatility'),
                    'sharpe_ratio': fund_data.get('sharpe_ratio'),
                    'annualized_return': fund_data.get('annualized_return')
                }
            }
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing fund for I-Score: {e}")
            return {'success': False, 'error': str(e)}
    
    def _estimate_fund_age(self, nav_history: List[Dict]) -> float:
        """Estimate fund age from NAV history"""
        if not nav_history:
            return 0
        
        try:
            dates = []
            for entry in nav_history:
                try:
                    date = datetime.strptime(entry['date'], '%d-%m-%Y')
                    dates.append(date)
                except:
                    continue
            
            if dates:
                oldest = min(dates)
                newest = max(dates)
                age_days = (newest - oldest).days
                return round(age_days / 365, 1)
            
        except Exception as e:
            logger.error(f"Error estimating fund age: {e}")
        
        return 0


mfapi_service = MFApiService()
