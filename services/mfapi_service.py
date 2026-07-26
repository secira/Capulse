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

    # Use HTTP — mfapi.in's HTTPS certificate causes SSL handshake timeouts
    # from many cloud hosts (confirmed: http:// responds in ~0.2 s; https:// hangs).
    BASE_URL = "http://api.mfapi.in/mf"

    # Class-level cache for the full fund list (37k+ entries, ~5.7 MB).
    # Shared across all instances; refreshed at most once every 6 hours.
    _all_funds_cache: List[Dict] = []
    _all_funds_fetched_at: Optional[datetime] = None
    _ALL_FUNDS_TTL_HOURS = 6

    # Common colloquial name → actual SEBI-categorised name fragment
    _ALIASES = {
        'bluechip':   'large cap',
        'blue chip':  'large cap',
        'bluchip':    'large cap',
        'flexi':      'flexi cap',
        'flexicap':   'flexi cap',
        'elss':       'tax saver',
        'tax saving': 'tax saver',
        'nifty 50':   'index',
        'sensex':     'index',
        'liquid':     'liquid',
        'gilt':       'gilt',
        'arbitrage':  'arbitrage',
        'hybrid':     'hybrid',
        'debt':       'debt',
        'midcap':     'mid cap',
        'mid-cap':    'mid cap',
        'smallcap':   'small cap',
        'small-cap':  'small cap',
        'largecap':   'large cap',
        'large-cap':  'large cap',
        'multi cap':  'multicap',
        'multicap':   'multicap',
        'multi-cap':  'multicap',
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'Capulse/1.0'
        })

    # ── fund-list helpers ──────────────────────────────────────────────────

    def _get_all_funds(self) -> List[Dict]:
        """Return the full AMFI fund list, refreshing the class-level cache every 6 h."""
        now = datetime.utcnow()
        if (MFApiService._all_funds_cache and
                MFApiService._all_funds_fetched_at and
                (now - MFApiService._all_funds_fetched_at).total_seconds() < MFApiService._ALL_FUNDS_TTL_HOURS * 3600):
            return MFApiService._all_funds_cache
        try:
            resp = self.session.get(self.BASE_URL, timeout=15)
            resp.raise_for_status()
            MFApiService._all_funds_cache = resp.json()
            MFApiService._all_funds_fetched_at = now
            logger.info(f"Refreshed full fund list: {len(MFApiService._all_funds_cache)} schemes")
        except Exception as e:
            logger.warning(f"Could not refresh fund list: {e}")
        return MFApiService._all_funds_cache

    def _expand_query(self, query: str) -> str:
        """Replace colloquial names with SEBI-standard terms."""
        q = query.lower()
        for alias, replacement in self._ALIASES.items():
            q = q.replace(alias, replacement)
        return q

    def _local_search(self, query: str, max_results: int = 15) -> List[Dict]:
        """
        Fuzzy word-overlap search against the full fund list.
        Returns up to max_results funds sorted by overlap score desc.
        Each fund dict matches the mfapi search format:
            {'schemeCode': int, 'schemeName': str, ...}
        """
        all_funds = self._get_all_funds()
        if not all_funds:
            return []

        expanded = self._expand_query(query)
        # Tokenise: keep alphanumeric words, drop stopwords
        stopwords = {'fund', 'scheme', 'plan', 'option', 'the', 'of', 'and', '-', '&'}
        q_tokens = {t for t in expanded.lower().split() if t not in stopwords and len(t) > 1}
        if not q_tokens:
            return []

        scored = []
        for f in all_funds:
            name = f.get('schemeName', '')
            name_low = name.lower()
            name_tokens = set(name_low.split())
            matched = q_tokens & name_tokens
            if not matched:
                continue
            # Score: fraction of query tokens matched, boosted if all tokens hit
            score = len(matched) / max(len(q_tokens), 1)
            if matched == q_tokens:
                score += 0.5  # perfect token match bonus
            # Boost Direct Growth plans (most useful for analysis)
            if 'direct' in name_low and 'growth' in name_low:
                score += 0.2
            scored.append((score, f))

        scored.sort(key=lambda x: -x[0])
        results = [f for _, f in scored[:max_results]]
        logger.info(f"Local fund search '{query}' → {len(results)} results (top: {results[0]['schemeName'] if results else 'none'})")
        return results

    def search_fund(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for mutual funds by name.
        1. Try the mfapi.in /search endpoint (fast, exact prefix match, max 15).
        2. If it returns nothing, fall back to local fuzzy search over the full
           cached fund list — handles renamed funds (e.g. "Axis Bluechip" →
           "Axis Large Cap") and spelling variants.
        """
        import urllib.parse
        try:
            encoded = urllib.parse.quote(query)
            response = self.session.get(f"{self.BASE_URL}/search?q={encoded}", timeout=6)
            response.raise_for_status()
            results = response.json()
            if results:
                logger.info(f"API search '{query}' → {len(results)} results")
                return results
        except Exception as e:
            logger.warning(f"API search error for '{query}': {e}")

        # API returned nothing — fall back to local fuzzy search
        logger.info(f"API search returned 0 for '{query}', trying local fuzzy search")
        return self._local_search(query)
    
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
            
            # mfapi.in returns NAV data newest-first (navs[0] = most recent).
            # Daily return from day i to day i+1 (forward in time) is:
            #   (navs[i] - navs[i+1]) / navs[i+1]   →   (newer - older) / older
            daily_returns = []
            for i in range(len(navs) - 1):
                older = navs[i + 1]
                newer = navs[i]
                if older > 0:
                    ret = (newer - older) / older
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
        """Search and get fund details by name — prefers Direct Growth plans."""
        results = self.search_fund(fund_name)
        if not results:
            return None
        # Prefer Direct Growth; fall back to first result
        direct_growth = [f for f in results
                         if 'DIRECT' in f.get('schemeName', '').upper()
                         and 'GROWTH' in f.get('schemeName', '').upper()]
        chosen = direct_growth[0] if direct_growth else results[0]
        return self.get_fund_details(chosen['schemeCode'])

    def get_current_nav(self, scheme_name: str) -> Optional[float]:
        """
        Return just the current NAV for a fund by name.
        Used for lightweight dashboard refreshes — avoids full history fetch.
        Returns None if not found or API unavailable.
        """
        try:
            results = self.search_fund(scheme_name)
            if not results:
                return None
            direct_growth = [f for f in results
                             if 'DIRECT' in f.get('schemeName', '').upper()
                             and 'GROWTH' in f.get('schemeName', '').upper()]
            chosen = direct_growth[0] if direct_growth else results[0]
            sc = chosen.get('schemeCode')
            if not sc:
                return None
            resp = self.session.get(f"{self.BASE_URL}/{sc}", timeout=8)
            resp.raise_for_status()
            data = resp.json()
            nav_data = data.get('data', [])
            if nav_data:
                return float(nav_data[0]['nav'])
        except Exception as e:
            logger.debug(f"get_current_nav({scheme_name}): {e}")
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
