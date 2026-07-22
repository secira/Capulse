"""
Options Service for Capulse
Provides options chain data, Greeks, and analysis for F&O trading

Data Sources:
- Primary: TrueData API (real-time option chain + Greeks with low latency)
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
import math

logger = logging.getLogger(__name__)


class OptionsService:
    """Service to fetch options data from TrueData API and NSE"""
    
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
            logger.info("TrueData API initialized for real-time options data")
        
        self.common_options = {
            'NIFTY': {
                'symbol': 'NIFTY',
                'name': 'NIFTY 50 Options',
                'underlying': 'NIFTY',
                'exchange': 'NSE',
                'category': 'Index',
                'lot_size': 25,
                'spot_price': 23750.50,
                'previous_close': 23680.25,
                'expiry_dates': ['2025-01-02', '2025-01-09', '2025-01-16', '2025-01-30'],
                'strike_interval': 50,
                'option_chain': {
                    '23700CE': {
                        'strike': 23700,
                        'type': 'CE',
                        'ltp': 142.50,
                        'change': 12.30,
                        'volume': 1250000,
                        'oi': 4500000,
                        'oi_change': 125000,
                        'iv': 12.5,
                        'delta': 0.52,
                        'gamma': 0.0012,
                        'theta': -15.20,
                        'vega': 8.50,
                        'bid': 141.50,
                        'ask': 143.00
                    },
                    '23700PE': {
                        'strike': 23700,
                        'type': 'PE',
                        'ltp': 92.00,
                        'change': -8.50,
                        'volume': 980000,
                        'oi': 3800000,
                        'oi_change': -85000,
                        'iv': 13.2,
                        'delta': -0.48,
                        'gamma': 0.0011,
                        'theta': -14.80,
                        'vega': 8.20,
                        'bid': 91.00,
                        'ask': 92.50
                    },
                    '23750CE': {
                        'strike': 23750,
                        'type': 'CE',
                        'ltp': 98.50,
                        'change': 8.20,
                        'volume': 1450000,
                        'oi': 5200000,
                        'oi_change': 220000,
                        'iv': 11.8,
                        'delta': 0.48,
                        'gamma': 0.0013,
                        'theta': -16.50,
                        'vega': 9.10,
                        'bid': 97.50,
                        'ask': 99.00
                    },
                    '23750PE': {
                        'strike': 23750,
                        'type': 'PE',
                        'ltp': 125.00,
                        'change': -10.20,
                        'volume': 1100000,
                        'oi': 4100000,
                        'oi_change': -95000,
                        'iv': 12.8,
                        'delta': -0.52,
                        'gamma': 0.0012,
                        'theta': -15.80,
                        'vega': 8.80,
                        'bid': 124.00,
                        'ask': 125.50
                    },
                    '23800CE': {
                        'strike': 23800,
                        'type': 'CE',
                        'ltp': 68.50,
                        'change': 5.80,
                        'volume': 1680000,
                        'oi': 6100000,
                        'oi_change': 350000,
                        'iv': 11.2,
                        'delta': 0.42,
                        'gamma': 0.0014,
                        'theta': -17.20,
                        'vega': 9.50,
                        'bid': 67.50,
                        'ask': 69.00
                    },
                    '23800PE': {
                        'strike': 23800,
                        'type': 'PE',
                        'ltp': 165.50,
                        'change': -12.80,
                        'volume': 920000,
                        'oi': 3500000,
                        'oi_change': -120000,
                        'iv': 13.5,
                        'delta': -0.58,
                        'gamma': 0.0011,
                        'theta': -14.20,
                        'vega': 7.90,
                        'bid': 164.50,
                        'ask': 166.00
                    }
                },
                'pcr_oi': 0.84,
                'pcr_volume': 0.72,
                'max_pain': 23750,
                'atm_iv': 12.0,
                'iv_percentile': 35,
                'trend': 'bullish'
            },
            'BANKNIFTY': {
                'symbol': 'BANKNIFTY',
                'name': 'Bank NIFTY Options',
                'underlying': 'BANKNIFTY',
                'exchange': 'NSE',
                'category': 'Index',
                'lot_size': 15,
                'spot_price': 51200.75,
                'previous_close': 51050.50,
                'expiry_dates': ['2025-01-01', '2025-01-08', '2025-01-15', '2025-01-29'],
                'strike_interval': 100,
                'option_chain': {
                    '51200CE': {
                        'strike': 51200,
                        'type': 'CE',
                        'ltp': 285.50,
                        'change': 22.30,
                        'volume': 850000,
                        'oi': 2800000,
                        'oi_change': 95000,
                        'iv': 14.2,
                        'delta': 0.50,
                        'gamma': 0.0008,
                        'theta': -28.50,
                        'vega': 18.20,
                        'bid': 284.00,
                        'ask': 286.50
                    },
                    '51200PE': {
                        'strike': 51200,
                        'type': 'PE',
                        'ltp': 235.00,
                        'change': -18.50,
                        'volume': 720000,
                        'oi': 2400000,
                        'oi_change': -65000,
                        'iv': 15.0,
                        'delta': -0.50,
                        'gamma': 0.0007,
                        'theta': -26.80,
                        'vega': 17.50,
                        'bid': 233.50,
                        'ask': 236.00
                    }
                },
                'pcr_oi': 0.78,
                'pcr_volume': 0.68,
                'max_pain': 51200,
                'atm_iv': 14.5,
                'iv_percentile': 42,
                'trend': 'bullish'
            },
            'RELIANCE': {
                'symbol': 'RELIANCE',
                'name': 'Reliance Options',
                'underlying': 'RELIANCE',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 250,
                'spot_price': 1265.50,
                'previous_close': 1258.25,
                'expiry_dates': ['2025-01-30', '2025-02-27', '2025-03-27'],
                'strike_interval': 20,
                'option_chain': {
                    '1260CE': {
                        'strike': 1260,
                        'type': 'CE',
                        'ltp': 32.50,
                        'change': 4.20,
                        'volume': 125000,
                        'oi': 450000,
                        'oi_change': 25000,
                        'iv': 18.5,
                        'delta': 0.55,
                        'gamma': 0.0045,
                        'theta': -2.80,
                        'vega': 1.85,
                        'bid': 32.00,
                        'ask': 33.00
                    },
                    '1260PE': {
                        'strike': 1260,
                        'type': 'PE',
                        'ltp': 28.00,
                        'change': -3.50,
                        'volume': 98000,
                        'oi': 380000,
                        'oi_change': -18000,
                        'iv': 19.2,
                        'delta': -0.45,
                        'gamma': 0.0042,
                        'theta': -2.60,
                        'vega': 1.72,
                        'bid': 27.50,
                        'ask': 28.50
                    }
                },
                'pcr_oi': 0.82,
                'pcr_volume': 0.75,
                'max_pain': 1260,
                'atm_iv': 18.8,
                'iv_percentile': 48,
                'trend': 'neutral'
            },
            'TCS': {
                'symbol': 'TCS',
                'name': 'TCS Options',
                'underlying': 'TCS',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 175,
                'spot_price': 4125.80,
                'previous_close': 4098.50,
                'expiry_dates': ['2025-01-30', '2025-02-27', '2025-03-27'],
                'strike_interval': 50,
                'option_chain': {
                    '4100CE': {
                        'strike': 4100,
                        'type': 'CE',
                        'ltp': 98.50,
                        'change': 12.80,
                        'volume': 45000,
                        'oi': 180000,
                        'oi_change': 12000,
                        'iv': 16.8,
                        'delta': 0.58,
                        'gamma': 0.0018,
                        'theta': -5.20,
                        'vega': 4.50,
                        'bid': 97.50,
                        'ask': 99.00
                    },
                    '4100PE': {
                        'strike': 4100,
                        'type': 'PE',
                        'ltp': 72.00,
                        'change': -8.50,
                        'volume': 38000,
                        'oi': 155000,
                        'oi_change': -8500,
                        'iv': 17.5,
                        'delta': -0.42,
                        'gamma': 0.0016,
                        'theta': -4.80,
                        'vega': 4.20,
                        'bid': 71.00,
                        'ask': 72.50
                    }
                },
                'pcr_oi': 0.86,
                'pcr_volume': 0.80,
                'max_pain': 4100,
                'atm_iv': 17.2,
                'iv_percentile': 38,
                'trend': 'bullish'
            },
            'HDFCBANK': {
                'symbol': 'HDFCBANK',
                'name': 'HDFC Bank Options',
                'underlying': 'HDFCBANK',
                'exchange': 'NSE',
                'category': 'Stock',
                'lot_size': 550,
                'spot_price': 1725.40,
                'previous_close': 1718.80,
                'expiry_dates': ['2025-01-30', '2025-02-27', '2025-03-27'],
                'strike_interval': 20,
                'option_chain': {
                    '1720CE': {
                        'strike': 1720,
                        'type': 'CE',
                        'ltp': 38.50,
                        'change': 5.20,
                        'volume': 85000,
                        'oi': 320000,
                        'oi_change': 18000,
                        'iv': 15.2,
                        'delta': 0.52,
                        'gamma': 0.0035,
                        'theta': -2.20,
                        'vega': 2.10,
                        'bid': 38.00,
                        'ask': 39.00
                    },
                    '1720PE': {
                        'strike': 1720,
                        'type': 'PE',
                        'ltp': 32.00,
                        'change': -4.80,
                        'volume': 72000,
                        'oi': 285000,
                        'oi_change': -12000,
                        'iv': 15.8,
                        'delta': -0.48,
                        'gamma': 0.0032,
                        'theta': -2.05,
                        'vega': 1.95,
                        'bid': 31.50,
                        'ask': 32.50
                    }
                },
                'pcr_oi': 0.89,
                'pcr_volume': 0.82,
                'max_pain': 1720,
                'atm_iv': 15.5,
                'iv_percentile': 32,
                'trend': 'neutral'
            }
        }
        
        self.option_categories = {
            'Index': ['NIFTY', 'BANKNIFTY'],
            'Stock': ['RELIANCE', 'TCS', 'HDFCBANK']
        }
    
    def _fetch_from_truedata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time option chain from TrueData API (Primary)"""
        if not self.truedata_key:
            return None
        
        try:
            url = f"{self.truedata_base}/optionchain"
            params = {
                'api_key': self.truedata_key,
                'symbol': symbol,
                'expiry': 'nearest'
            }
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    logger.info(f"TrueData: Fetched real-time option chain for {symbol}")
                    return {
                        'symbol': symbol,
                        'spot_price': data.get('spot_price', 0),
                        'option_chain': data.get('option_chain', {}),
                        'pcr_oi': data.get('pcr_oi', 0),
                        'max_pain': data.get('max_pain', 0),
                        'atm_iv': data.get('atm_iv', 0),
                        'data_source': 'TrueData (Live)',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"TrueData API error for {symbol}: {e}")
        
        return None
    
    def _fetch_from_nse(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch option chain from NSE Official API (Backup)"""
        try:
            self.session.get('https://www.nseindia.com', headers=self.nse_headers, timeout=5)
            
            url = f"{self.nse_base}/option-chain-indices" if symbol in ['NIFTY', 'BANKNIFTY'] else f"{self.nse_base}/option-chain-equities"
            params = {'symbol': symbol}
            response = self.session.get(url, params=params, headers=self.nse_headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                records = data.get('records', {})
                if records:
                    logger.info(f"NSE: Fetched option chain for {symbol}")
                    
                    option_data = records.get('data', [])
                    spot = records.get('underlyingValue', 0)
                    
                    return {
                        'symbol': symbol,
                        'spot_price': spot,
                        'expiry_dates': records.get('expiryDates', []),
                        'raw_chain': option_data,
                        'data_source': 'NSE Official (Live)',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"NSE API error for {symbol}: {e}")
        
        return None
    
    def _fetch_spot_from_yfinance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch spot price from yfinance (always available, no API key)"""
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
                logger.warning(f"No yfinance data for options underlying {symbol}")
                return None
            
            latest = history.iloc[-1]
            spot_price = float(latest['Close'])
            previous_close = float(history.iloc[-2]['Close']) if len(history) >= 2 else spot_price
            
            logger.info(f"✅ Got spot price for {symbol} from yfinance: {spot_price}")
            
            return {
                'symbol': symbol,
                'spot_price': round(spot_price, 2),
                'previous_close': round(previous_close, 2),
                'data_source': 'yfinance (Live)',
                'success': True
            }
            
        except Exception as e:
            logger.warning(f"yfinance fetch failed for options underlying {symbol}: {e}")
            return None
    
    def _get_live_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Attempt to fetch live data - tries multiple sources"""
        # Try TrueData first (primary for full option chain)
        live_data = self._fetch_from_truedata(symbol)
        if live_data:
            return live_data
        
        # Try NSE Official API
        live_data = self._fetch_from_nse(symbol)
        if live_data:
            return live_data
        
        # Try yfinance for at least spot price (always available)
        live_data = self._fetch_spot_from_yfinance(symbol)
        if live_data:
            return live_data
        
        return None
    
    def search_options(self, query: str) -> List[Dict[str, Any]]:
        """Search for options by symbol or name"""
        query_upper = query.upper().strip()
        results = []
        
        for symbol, data in self.common_options.items():
            if query_upper in symbol or query_upper in data['name'].upper():
                results.append({
                    'symbol': symbol,
                    'name': data['name'],
                    'exchange': data['exchange'],
                    'category': data['category'],
                    'spot_price': data['spot_price'],
                    'lot_size': data['lot_size'],
                    'atm_iv': data['atm_iv']
                })
        
        return results
    
    def get_options_details(self, symbol: str) -> Dict[str, Any]:
        """Get detailed options information with chain data"""
        symbol_upper = symbol.upper().strip()
        
        live_data = self._get_live_data(symbol_upper)
        
        if symbol_upper in self.common_options:
            options = self.common_options[symbol_upper].copy()
            
            if live_data:
                options['spot_price'] = live_data.get('spot_price', options['spot_price'])
                if live_data.get('option_chain'):
                    options['option_chain'] = live_data.get('option_chain')
                options['pcr_oi'] = live_data.get('pcr_oi', options.get('pcr_oi', 0))
                options['max_pain'] = live_data.get('max_pain', options.get('max_pain', 0))
                options['atm_iv'] = live_data.get('atm_iv', options.get('atm_iv', 0))
                options['data_source'] = live_data.get('data_source', 'TrueData (Live)')
            else:
                options['data_source'] = 'TrueData/NSE (Sample)'
            
            price_change = options['spot_price'] - options['previous_close']
            price_change_pct = (price_change / options['previous_close']) * 100 if options['previous_close'] else 0
            
            options['price_change'] = round(price_change, 2)
            options['price_change_pct'] = round(price_change_pct, 2)
            options['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            options['success'] = True
            
            return options
        
        return {'success': False, 'error': f'Options for {symbol} not found'}
    
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
        """Get full option chain for a symbol"""
        details = self.get_options_details(symbol)
        
        if details.get('success'):
            return {
                'symbol': symbol,
                'spot_price': details.get('spot_price'),
                'option_chain': details.get('option_chain', {}),
                'expiry_dates': details.get('expiry_dates', []),
                'pcr_oi': details.get('pcr_oi'),
                'pcr_volume': details.get('pcr_volume'),
                'max_pain': details.get('max_pain'),
                'atm_iv': details.get('atm_iv'),
                'iv_percentile': details.get('iv_percentile'),
                'data_source': details.get('data_source'),
                'success': True
            }
        
        return details
    
    def get_options_list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available options"""
        results = []
        
        for symbol, data in self.common_options.items():
            if category and data['category'] != category:
                continue
            
            results.append({
                'symbol': symbol,
                'name': data['name'],
                'exchange': data['exchange'],
                'category': data['category'],
                'spot_price': data['spot_price'],
                'atm_iv': data['atm_iv'],
                'pcr_oi': data['pcr_oi'],
                'trend': data['trend']
            })
        
        return results
    
    def analyze_for_iscore(self, query: str) -> Dict[str, Any]:
        """Get options data formatted for I-Score analysis"""
        search_results = self.search_options(query)
        
        if search_results:
            symbol = search_results[0]['symbol']
            details = self.get_options_details(symbol)
            
            if details.get('success'):
                option_chain = details.get('option_chain', {})
                
                total_call_oi = sum(opt.get('oi', 0) for key, opt in option_chain.items() if 'CE' in key)
                total_put_oi = sum(opt.get('oi', 0) for key, opt in option_chain.items() if 'PE' in key)
                avg_iv = sum(opt.get('iv', 0) for opt in option_chain.values()) / len(option_chain) if option_chain else 0
                
                return {
                    'success': True,
                    'symbol': symbol,
                    'name': details.get('name'),
                    'asset_type': 'options',
                    'exchange': details.get('exchange'),
                    'category': details.get('category'),
                    'spot_price': details.get('spot_price'),
                    'previous_close': details.get('previous_close'),
                    'price_change': details.get('price_change'),
                    'price_change_pct': details.get('price_change_pct'),
                    'lot_size': details.get('lot_size'),
                    'option_chain': option_chain,
                    'total_call_oi': total_call_oi,
                    'total_put_oi': total_put_oi,
                    'pcr_oi': details.get('pcr_oi'),
                    'pcr_volume': details.get('pcr_volume'),
                    'max_pain': details.get('max_pain'),
                    'atm_iv': details.get('atm_iv'),
                    'avg_iv': round(avg_iv, 2),
                    'iv_percentile': details.get('iv_percentile'),
                    'expiry_dates': details.get('expiry_dates'),
                    'trend': details.get('trend'),
                    'data_source': details.get('data_source'),
                    'last_updated': details.get('last_updated')
                }
        
        return {'success': False, 'error': f'No options found for query: {query}'}


options_service = OptionsService()
