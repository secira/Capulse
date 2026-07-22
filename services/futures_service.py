"""
Futures Service for Capulse
Provides futures contract data for F&O trading analysis

Data Sources:
- Primary: TrueData API (real-time futures data with low latency)
- Backup: NSE Official APIs (free but may have rate limits)

Analysis Platforms:
- Strike Money / Stolo for comprehensive analytics
- Quantsapp for backtesting and strategy learning
"""

import requests
import logging
import os
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class FuturesService:
    """Service to fetch futures data from TrueData API and NSE"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'TargeCapulse/1.0'
        })
        
        self.truedata_key = os.environ.get('TRUEDATA_API_KEY')
        self.truedata_base = 'https://api.truedata.in/v1'
        
        self.nse_base = 'https://www.nseindia.com/api'
        self.nse_headers = {
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        if self.truedata_key:
            logger.info("TrueData API initialized for real-time futures data")
        
        self.common_futures = {
            'NIFTY': {
                'symbol': 'NIFTY',
                'name': 'NIFTY 50 Futures',
                'underlying': 'NIFTY',
                'exchange': 'NSE',
                'category': 'Index',
                'lot_size': 25,
                'current_price': 23785.50,
                'previous_close': 23705.25,
                'day_high': 23825.00,
                'day_low': 23650.75,
                'spot_price': 23750.50,
                'basis': 35.00,
                'basis_pct': 0.15,
                'open_interest': 12500000,
                'oi_change': 450000,
                'oi_change_pct': 3.7,
                'volume': 8500000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-30',
                'days_to_expiry': 33,
                'margin_required': 12.5,
                'rollover_cost': 0.08,
                'coi_buildup': 'long_buildup',
                'trend': 'bullish',
                'support_levels': [23650, 23550, 23400],
                'resistance_levels': [23850, 23950, 24100]
            },
            'BANKNIFTY': {
                'symbol': 'BANKNIFTY',
                'name': 'Bank NIFTY Futures',
                'underlying': 'BANKNIFTY',
                'exchange': 'NSE',
                'category': 'Index',
                'lot_size': 15,
                'current_price': 51285.50,
                'previous_close': 51075.25,
                'day_high': 51425.00,
                'day_low': 50950.75,
                'spot_price': 51200.75,
                'basis': 84.75,
                'basis_pct': 0.17,
                'open_interest': 4800000,
                'oi_change': 185000,
                'oi_change_pct': 4.0,
                'volume': 3200000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-29',
                'days_to_expiry': 32,
                'margin_required': 15.0,
                'rollover_cost': 0.10,
                'coi_buildup': 'long_buildup',
                'trend': 'bullish',
                'support_levels': [50950, 50700, 50400],
                'resistance_levels': [51500, 51750, 52000]
            },
            'FINNIFTY': {
                'symbol': 'FINNIFTY',
                'name': 'Nifty Financial Services Futures',
                'underlying': 'FINNIFTY',
                'exchange': 'NSE',
                'category': 'Index',
                'lot_size': 25,
                'current_price': 22150.50,
                'previous_close': 22085.25,
                'day_high': 22225.00,
                'day_low': 22025.75,
                'spot_price': 22120.50,
                'basis': 30.00,
                'basis_pct': 0.14,
                'open_interest': 2100000,
                'oi_change': 85000,
                'oi_change_pct': 4.2,
                'volume': 1450000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-28',
                'days_to_expiry': 31,
                'margin_required': 13.5,
                'rollover_cost': 0.09,
                'coi_buildup': 'long_buildup',
                'trend': 'neutral',
                'support_levels': [22000, 21850, 21650],
                'resistance_levels': [22250, 22400, 22600]
            },
            'RELIANCE': {
                'symbol': 'RELIANCE',
                'name': 'Reliance Futures',
                'underlying': 'RELIANCE',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 250,
                'current_price': 1268.50,
                'previous_close': 1260.25,
                'day_high': 1275.00,
                'day_low': 1255.75,
                'spot_price': 1265.50,
                'basis': 3.00,
                'basis_pct': 0.24,
                'open_interest': 8500000,
                'oi_change': 320000,
                'oi_change_pct': 3.9,
                'volume': 4200000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-30',
                'days_to_expiry': 33,
                'margin_required': 18.5,
                'rollover_cost': 0.12,
                'coi_buildup': 'long_buildup',
                'trend': 'bullish',
                'support_levels': [1255, 1240, 1220],
                'resistance_levels': [1280, 1295, 1315]
            },
            'TCS': {
                'symbol': 'TCS',
                'name': 'TCS Futures',
                'underlying': 'TCS',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 175,
                'current_price': 4132.50,
                'previous_close': 4105.25,
                'day_high': 4155.00,
                'day_low': 4085.75,
                'spot_price': 4125.80,
                'basis': 6.70,
                'basis_pct': 0.16,
                'open_interest': 2800000,
                'oi_change': 125000,
                'oi_change_pct': 4.7,
                'volume': 1850000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-30',
                'days_to_expiry': 33,
                'margin_required': 16.0,
                'rollover_cost': 0.11,
                'coi_buildup': 'long_buildup',
                'trend': 'bullish',
                'support_levels': [4080, 4020, 3950],
                'resistance_levels': [4160, 4200, 4280]
            },
            'HDFCBANK': {
                'symbol': 'HDFCBANK',
                'name': 'HDFC Bank Futures',
                'underlying': 'HDFCBANK',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 550,
                'current_price': 1728.50,
                'previous_close': 1720.25,
                'day_high': 1738.00,
                'day_low': 1712.75,
                'spot_price': 1725.40,
                'basis': 3.10,
                'basis_pct': 0.18,
                'open_interest': 6200000,
                'oi_change': 245000,
                'oi_change_pct': 4.1,
                'volume': 3100000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-30',
                'days_to_expiry': 33,
                'margin_required': 14.5,
                'rollover_cost': 0.10,
                'coi_buildup': 'short_covering',
                'trend': 'neutral',
                'support_levels': [1710, 1690, 1665],
                'resistance_levels': [1740, 1760, 1785]
            },
            'INFY': {
                'symbol': 'INFY',
                'name': 'Infosys Futures',
                'underlying': 'INFY',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 300,
                'current_price': 1892.50,
                'previous_close': 1878.25,
                'day_high': 1905.00,
                'day_low': 1868.75,
                'spot_price': 1888.80,
                'basis': 3.70,
                'basis_pct': 0.20,
                'open_interest': 4500000,
                'oi_change': 180000,
                'oi_change_pct': 4.2,
                'volume': 2450000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-30',
                'days_to_expiry': 33,
                'margin_required': 15.5,
                'rollover_cost': 0.11,
                'coi_buildup': 'long_buildup',
                'trend': 'bullish',
                'support_levels': [1865, 1840, 1810],
                'resistance_levels': [1910, 1935, 1965]
            },
            'ICICIBANK': {
                'symbol': 'ICICIBANK',
                'name': 'ICICI Bank Futures',
                'underlying': 'ICICIBANK',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 700,
                'current_price': 1285.50,
                'previous_close': 1275.25,
                'day_high': 1295.00,
                'day_low': 1268.75,
                'spot_price': 1282.80,
                'basis': 2.70,
                'basis_pct': 0.21,
                'open_interest': 5800000,
                'oi_change': 225000,
                'oi_change_pct': 4.0,
                'volume': 2850000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-30',
                'days_to_expiry': 33,
                'margin_required': 14.0,
                'rollover_cost': 0.10,
                'coi_buildup': 'long_buildup',
                'trend': 'bullish',
                'support_levels': [1268, 1250, 1230],
                'resistance_levels': [1298, 1315, 1340]
            }
        }
        
        self.futures_categories = {
            'Index': ['NIFTY', 'BANKNIFTY', 'FINNIFTY'],
            'Stock': ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK']
        }
    
    def _fetch_from_truedata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time futures data from TrueData API (Primary)"""
        if not self.truedata_key:
            return None
        
        try:
            url = f"{self.truedata_base}/futures"
            params = {
                'api_key': self.truedata_key,
                'symbol': symbol,
                'expiry': 'nearest'
            }
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    logger.info(f"TrueData: Fetched real-time futures data for {symbol}")
                    return {
                        'symbol': symbol,
                        'current_price': data.get('ltp', 0),
                        'previous_close': data.get('previous_close', 0),
                        'day_high': data.get('high', 0),
                        'day_low': data.get('low', 0),
                        'spot_price': data.get('spot_price', 0),
                        'open_interest': data.get('open_interest', 0),
                        'oi_change': data.get('oi_change', 0),
                        'volume': data.get('volume', 0),
                        'basis': data.get('basis', 0),
                        'data_source': 'TrueData (Live)',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"TrueData API error for {symbol}: {e}")
        
        return None
    
    def _fetch_from_nse(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch futures data from NSE Official API (Backup)"""
        try:
            self.session.get('https://www.nseindia.com', headers=self.nse_headers, timeout=5)
            
            url = f"{self.nse_base}/quote-derivative"
            params = {'symbol': symbol}
            response = self.session.get(url, params=params, headers=self.nse_headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                stocks = data.get('stocks', [])
                
                for stock in stocks:
                    if stock.get('metadata', {}).get('instrumentType') == 'Stock Futures':
                        md = stock.get('marketDeptOrderBook', {}).get('tradeInfo', {})
                        meta = stock.get('metadata', {})
                        
                        logger.info(f"NSE: Fetched futures data for {symbol}")
                        return {
                            'symbol': symbol,
                            'current_price': meta.get('lastPrice', 0),
                            'previous_close': meta.get('previousClose', 0),
                            'day_high': meta.get('highPrice', 0),
                            'day_low': meta.get('lowPrice', 0),
                            'open_interest': md.get('openInterest', 0),
                            'oi_change': md.get('changeinopenInterest', 0),
                            'volume': md.get('tradedVolume', 0),
                            'data_source': 'NSE Official (Live)',
                            'success': True
                        }
        except Exception as e:
            logger.warning(f"NSE API error for {symbol}: {e}")
        
        return None
    
    def _fetch_from_yfinance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch futures underlying price from yfinance (always available)"""
        # Map symbols to yfinance format
        yf_symbol_map = {
            'NIFTY': '^NSEI',      # NIFTY 50 Index
            'BANKNIFTY': '^NSEBANK',  # Bank NIFTY Index
            'FINNIFTY': '^CNXFIN',    # Nifty Financial Services (approximate)
        }
        
        yf_symbol = yf_symbol_map.get(symbol.upper(), f"{symbol}.NS")
        
        try:
            ticker = yf.Ticker(yf_symbol)
            history = ticker.history(period="5d")
            
            if history.empty:
                logger.warning(f"No yfinance data for futures {symbol}")
                return None
            
            latest = history.iloc[-1]
            spot_price = float(latest['Close'])
            previous_close = float(history.iloc[-2]['Close']) if len(history) >= 2 else spot_price
            
            # Approximate futures price (spot + small premium)
            basis_pct = 0.15  # Approximate basis for near-month
            current_price = round(spot_price * (1 + basis_pct/100), 2)
            
            logger.info(f"✅ Got price for {symbol} futures from yfinance: {current_price}")
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'previous_close': round(previous_close * (1 + basis_pct/100), 2),
                'spot_price': round(spot_price, 2),
                'day_high': round(float(latest['High']) * (1 + basis_pct/100), 2),
                'day_low': round(float(latest['Low']) * (1 + basis_pct/100), 2),
                'data_source': 'yfinance (Live)',
                'success': True
            }
            
        except Exception as e:
            logger.warning(f"yfinance fetch failed for futures {symbol}: {e}")
            return None
    
    def _get_live_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Attempt to fetch live data - tries multiple sources"""
        # Try TrueData first (primary)
        live_data = self._fetch_from_truedata(symbol)
        if live_data:
            return live_data
        
        # Try NSE Official API
        live_data = self._fetch_from_nse(symbol)
        if live_data:
            return live_data
        
        # Try yfinance as fallback (always available)
        live_data = self._fetch_from_yfinance(symbol)
        if live_data:
            return live_data
        
        return None
    
    def search_futures(self, query: str) -> List[Dict[str, Any]]:
        """Search for futures by symbol or name"""
        query_upper = query.upper().strip()
        results = []
        
        for symbol, data in self.common_futures.items():
            if query_upper in symbol or query_upper in data['name'].upper():
                results.append({
                    'symbol': symbol,
                    'name': data['name'],
                    'exchange': data['exchange'],
                    'category': data['category'],
                    'current_price': data['current_price'],
                    'lot_size': data['lot_size'],
                    'basis': data['basis']
                })
        
        return results
    
    def get_futures_details(self, symbol: str) -> Dict[str, Any]:
        """Get detailed futures information"""
        symbol_upper = symbol.upper().strip()
        
        live_data = self._get_live_data(symbol_upper)
        
        if symbol_upper in self.common_futures:
            futures = self.common_futures[symbol_upper].copy()
            
            if live_data:
                futures['current_price'] = live_data.get('current_price', futures['current_price'])
                futures['previous_close'] = live_data.get('previous_close', futures['previous_close'])
                futures['day_high'] = live_data.get('day_high', futures.get('day_high', 0))
                futures['day_low'] = live_data.get('day_low', futures.get('day_low', 0))
                futures['spot_price'] = live_data.get('spot_price', futures.get('spot_price', 0))
                futures['open_interest'] = live_data.get('open_interest', futures.get('open_interest', 0))
                futures['oi_change'] = live_data.get('oi_change', futures.get('oi_change', 0))
                futures['volume'] = live_data.get('volume', futures.get('volume', 0))
                futures['basis'] = live_data.get('basis', futures.get('basis', 0))
                futures['data_source'] = live_data.get('data_source', 'TrueData (Live)')
            else:
                futures['data_source'] = 'TrueData/NSE (Sample)'
            
            price_change = futures['current_price'] - futures['previous_close']
            price_change_pct = (price_change / futures['previous_close']) * 100 if futures['previous_close'] else 0
            
            if futures['spot_price']:
                futures['basis'] = round(futures['current_price'] - futures['spot_price'], 2)
                futures['basis_pct'] = round((futures['basis'] / futures['spot_price']) * 100, 2)
            
            futures['price_change'] = round(price_change, 2)
            futures['price_change_pct'] = round(price_change_pct, 2)
            futures['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            futures['success'] = True
            
            return futures
        
        return {'success': False, 'error': f'Futures for {symbol} not found'}
    
    def get_futures_list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available futures"""
        results = []
        
        for symbol, data in self.common_futures.items():
            if category and data['category'] != category:
                continue
            
            results.append({
                'symbol': symbol,
                'name': data['name'],
                'exchange': data['exchange'],
                'category': data['category'],
                'current_price': data['current_price'],
                'basis': data['basis'],
                'oi_change_pct': data['oi_change_pct'],
                'trend': data['trend']
            })
        
        return results
    
    def analyze_for_iscore(self, query: str) -> Dict[str, Any]:
        """Get futures data formatted for I-Score analysis"""
        search_results = self.search_futures(query)
        
        if search_results:
            symbol = search_results[0]['symbol']
            details = self.get_futures_details(symbol)
            
            if details.get('success'):
                return {
                    'success': True,
                    'symbol': symbol,
                    'name': details.get('name'),
                    'asset_type': 'futures',
                    'exchange': details.get('exchange'),
                    'category': details.get('category'),
                    'current_price': details.get('current_price'),
                    'previous_close': details.get('previous_close'),
                    'price_change': details.get('price_change'),
                    'price_change_pct': details.get('price_change_pct'),
                    'day_high': details.get('day_high'),
                    'day_low': details.get('day_low'),
                    'spot_price': details.get('spot_price'),
                    'basis': details.get('basis'),
                    'basis_pct': details.get('basis_pct'),
                    'lot_size': details.get('lot_size'),
                    'open_interest': details.get('open_interest'),
                    'oi_change': details.get('oi_change'),
                    'oi_change_pct': details.get('oi_change_pct'),
                    'volume': details.get('volume'),
                    'contract_month': details.get('contract_month'),
                    'expiry_date': details.get('expiry_date'),
                    'days_to_expiry': details.get('days_to_expiry'),
                    'margin_required': details.get('margin_required'),
                    'rollover_cost': details.get('rollover_cost'),
                    'coi_buildup': details.get('coi_buildup'),
                    'support_levels': details.get('support_levels'),
                    'resistance_levels': details.get('resistance_levels'),
                    'trend': details.get('trend'),
                    'data_source': details.get('data_source'),
                    'last_updated': details.get('last_updated')
                }
        
        return {'success': False, 'error': f'No futures found for query: {query}'}


futures_service = FuturesService()
