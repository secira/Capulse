"""
Commodity Service for Capulse
Provides commodity data from MCX/NCDEX and global commodity markets

Data Sources:
- APIdatafeed: MCX/NCDEX real-time data for Indian commodity exchanges (Primary)
- Commodities-API: International commodity prices and cross-market analysis (Backup)

API Integration:
- APIdatafeed API: https://apidatafeed.com/ (requires API key)
- Commodities-API: https://commodities-api.com/ (requires API key)
"""

import requests
import logging
import os
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class CommodityService:
    """Service to fetch commodity data from Indian and global sources"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'TargeCapulse/1.0'
        })
        
        self.apidatafeed_key = os.environ.get('APIDATAFEED_API_KEY')
        self.commodities_api_key = os.environ.get('COMMODITIES_API_KEY')
        
        self.apidatafeed_base = 'https://api.apidatafeed.com/v1'
        self.commodities_api_base = 'https://commodities-api.com/api'
        
        if self.apidatafeed_key:
            logger.info("APIdatafeed API initialized for MCX/NCDEX data")
        if self.commodities_api_key:
            logger.info("Commodities-API initialized for global commodity data")
        
        self.common_commodities = {
            'GOLD': {
                'symbol': 'GOLD',
                'name': 'Gold',
                'exchange': 'MCX',
                'category': 'Precious Metals',
                'unit': 'per 10 grams',
                'lot_size': 100,
                'current_price': 62500,
                'previous_close': 62280,
                'day_high': 62650,
                'day_low': 62150,
                'open_interest': 15250,
                'volume': 8500,
                'contract_month': 'FEB2025',
                'expiry_date': '2025-02-05',
                'margin_required': 6.0,
                'volatility': 12.5,
                'trend': 'bullish',
                'global_benchmark': 'COMEX Gold',
                'correlation_usd': -0.65,
                'seasonal_factor': 'high_demand',
                'supply_outlook': 'stable'
            },
            'SILVER': {
                'symbol': 'SILVER',
                'name': 'Silver',
                'exchange': 'MCX',
                'category': 'Precious Metals',
                'unit': 'per kg',
                'lot_size': 30,
                'current_price': 74500,
                'previous_close': 74100,
                'day_high': 74800,
                'day_low': 73950,
                'open_interest': 12800,
                'volume': 6200,
                'contract_month': 'MAR2025',
                'expiry_date': '2025-03-05',
                'margin_required': 6.5,
                'volatility': 18.2,
                'trend': 'bullish',
                'global_benchmark': 'COMEX Silver',
                'correlation_usd': -0.58,
                'seasonal_factor': 'industrial_demand',
                'supply_outlook': 'moderate_deficit'
            },
            'CRUDEOIL': {
                'symbol': 'CRUDEOIL',
                'name': 'Crude Oil',
                'exchange': 'MCX',
                'category': 'Energy',
                'unit': 'per barrel',
                'lot_size': 100,
                'current_price': 5850,
                'previous_close': 5920,
                'day_high': 5900,
                'day_low': 5810,
                'open_interest': 25600,
                'volume': 18500,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-17',
                'margin_required': 8.0,
                'volatility': 25.5,
                'trend': 'bearish',
                'global_benchmark': 'Brent Crude',
                'correlation_usd': 0.45,
                'seasonal_factor': 'winter_demand',
                'supply_outlook': 'oversupply'
            },
            'NATURALGAS': {
                'symbol': 'NATURALGAS',
                'name': 'Natural Gas',
                'exchange': 'MCX',
                'category': 'Energy',
                'unit': 'per mmBtu',
                'lot_size': 1250,
                'current_price': 285,
                'previous_close': 278,
                'day_high': 290,
                'day_low': 275,
                'open_interest': 18900,
                'volume': 12500,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-27',
                'margin_required': 10.0,
                'volatility': 35.8,
                'trend': 'volatile',
                'global_benchmark': 'Henry Hub',
                'correlation_usd': 0.25,
                'seasonal_factor': 'heating_demand',
                'supply_outlook': 'balanced'
            },
            'COPPER': {
                'symbol': 'COPPER',
                'name': 'Copper',
                'exchange': 'MCX',
                'category': 'Base Metals',
                'unit': 'per kg',
                'lot_size': 2500,
                'current_price': 745,
                'previous_close': 742,
                'day_high': 748,
                'day_low': 740,
                'open_interest': 9500,
                'volume': 4200,
                'contract_month': 'FEB2025',
                'expiry_date': '2025-02-28',
                'margin_required': 5.5,
                'volatility': 15.2,
                'trend': 'neutral',
                'global_benchmark': 'LME Copper',
                'correlation_usd': -0.35,
                'seasonal_factor': 'infrastructure_demand',
                'supply_outlook': 'tight'
            },
            'ALUMINIUM': {
                'symbol': 'ALUMINIUM',
                'name': 'Aluminium',
                'exchange': 'MCX',
                'category': 'Base Metals',
                'unit': 'per kg',
                'lot_size': 5000,
                'current_price': 218,
                'previous_close': 216,
                'day_high': 220,
                'day_low': 215,
                'open_interest': 7800,
                'volume': 3500,
                'contract_month': 'FEB2025',
                'expiry_date': '2025-02-28',
                'margin_required': 5.0,
                'volatility': 14.5,
                'trend': 'bullish',
                'global_benchmark': 'LME Aluminium',
                'correlation_usd': -0.28,
                'seasonal_factor': 'auto_sector_demand',
                'supply_outlook': 'stable'
            },
            'ZINC': {
                'symbol': 'ZINC',
                'name': 'Zinc',
                'exchange': 'MCX',
                'category': 'Base Metals',
                'unit': 'per kg',
                'lot_size': 5000,
                'current_price': 265,
                'previous_close': 263,
                'day_high': 268,
                'day_low': 262,
                'open_interest': 6200,
                'volume': 2800,
                'contract_month': 'FEB2025',
                'expiry_date': '2025-02-28',
                'margin_required': 5.0,
                'volatility': 16.8,
                'trend': 'bullish',
                'global_benchmark': 'LME Zinc',
                'correlation_usd': -0.32,
                'seasonal_factor': 'galvanizing_demand',
                'supply_outlook': 'deficit'
            },
            'NICKEL': {
                'symbol': 'NICKEL',
                'name': 'Nickel',
                'exchange': 'MCX',
                'category': 'Base Metals',
                'unit': 'per kg',
                'lot_size': 1500,
                'current_price': 1520,
                'previous_close': 1505,
                'day_high': 1535,
                'day_low': 1495,
                'open_interest': 4500,
                'volume': 1800,
                'contract_month': 'FEB2025',
                'expiry_date': '2025-02-28',
                'margin_required': 7.0,
                'volatility': 22.5,
                'trend': 'neutral',
                'global_benchmark': 'LME Nickel',
                'correlation_usd': -0.40,
                'seasonal_factor': 'ev_battery_demand',
                'supply_outlook': 'oversupply'
            },
            'LEAD': {
                'symbol': 'LEAD',
                'name': 'Lead',
                'exchange': 'MCX',
                'category': 'Base Metals',
                'unit': 'per kg',
                'lot_size': 5000,
                'current_price': 182,
                'previous_close': 180,
                'day_high': 184,
                'day_low': 179,
                'open_interest': 3800,
                'volume': 1500,
                'contract_month': 'FEB2025',
                'expiry_date': '2025-02-28',
                'margin_required': 5.0,
                'volatility': 13.2,
                'trend': 'neutral',
                'global_benchmark': 'LME Lead',
                'correlation_usd': -0.25,
                'seasonal_factor': 'battery_demand',
                'supply_outlook': 'balanced'
            },
            'COTTON': {
                'symbol': 'COTTON',
                'name': 'Cotton',
                'exchange': 'MCX',
                'category': 'Agricultural',
                'unit': 'per bale',
                'lot_size': 25,
                'current_price': 56800,
                'previous_close': 56500,
                'day_high': 57100,
                'day_low': 56200,
                'open_interest': 5200,
                'volume': 2100,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-31',
                'margin_required': 5.0,
                'volatility': 18.5,
                'trend': 'bullish',
                'global_benchmark': 'ICE Cotton',
                'correlation_usd': -0.20,
                'seasonal_factor': 'harvest_season',
                'supply_outlook': 'moderate_surplus'
            },
            'MENTHAOIL': {
                'symbol': 'MENTHAOIL',
                'name': 'Mentha Oil',
                'exchange': 'MCX',
                'category': 'Agricultural',
                'unit': 'per kg',
                'lot_size': 360,
                'current_price': 985,
                'previous_close': 978,
                'day_high': 992,
                'day_low': 972,
                'open_interest': 2800,
                'volume': 1200,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-31',
                'margin_required': 6.0,
                'volatility': 28.5,
                'trend': 'volatile',
                'global_benchmark': 'India MCX',
                'correlation_usd': -0.15,
                'seasonal_factor': 'monsoon_impact',
                'supply_outlook': 'weather_dependent'
            }
        }
        
        self.commodity_categories = {
            'Precious Metals': ['GOLD', 'SILVER'],
            'Energy': ['CRUDEOIL', 'NATURALGAS'],
            'Base Metals': ['COPPER', 'ALUMINIUM', 'ZINC', 'NICKEL', 'LEAD'],
            'Agricultural': ['COTTON', 'MENTHAOIL']
        }
    
    def _fetch_from_apidatafeed(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time MCX/NCDEX data from APIdatafeed API (Primary)"""
        if not self.apidatafeed_key:
            return None
        
        try:
            url = f"{self.apidatafeed_base}/commodity/{symbol}"
            headers = {'Authorization': f'Bearer {self.apidatafeed_key}'}
            response = self.session.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f"APIdatafeed: Fetched real-time data for {symbol}")
                    return {
                        'symbol': data.get('symbol', symbol),
                        'name': data.get('name', symbol),
                        'exchange': data.get('exchange', 'MCX'),
                        'current_price': data.get('ltp', 0),
                        'previous_close': data.get('previous_close', 0),
                        'day_high': data.get('high', 0),
                        'day_low': data.get('low', 0),
                        'open_interest': data.get('open_interest', 0),
                        'volume': data.get('volume', 0),
                        'volatility': data.get('volatility', 15.0),
                        'data_source': 'APIdatafeed (Live)',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"APIdatafeed API error for {symbol}: {e}")
        
        return None
    
    def _fetch_from_commodities_api(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch global commodity data from Commodities-API (Backup)"""
        if not self.commodities_api_key:
            return None
        
        symbol_map = {
            'GOLD': 'XAU', 'SILVER': 'XAG', 'CRUDEOIL': 'BRENT',
            'NATURALGAS': 'NG', 'COPPER': 'XCU', 'ALUMINIUM': 'ALU'
        }
        api_symbol = symbol_map.get(symbol.upper(), symbol)
        
        try:
            url = f"{self.commodities_api_base}/latest"
            params = {'access_key': self.commodities_api_key, 'base': 'USD', 'symbols': api_symbol}
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    rates = data.get('rates', {})
                    price = rates.get(api_symbol, 0)
                    if price > 0:
                        price = 1 / price
                    logger.info(f"Commodities-API: Fetched global data for {symbol}")
                    return {
                        'symbol': symbol,
                        'current_price': round(price, 2),
                        'data_source': 'Commodities-API (Global)',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"Commodities-API error for {symbol}: {e}")
        
        return None
    
    def _fetch_from_yfinance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch commodity data from yfinance (Global futures prices)"""
        # Map MCX symbols to yfinance commodity futures symbols
        yf_symbol_map = {
            'GOLD': 'GC=F',      # COMEX Gold Futures
            'SILVER': 'SI=F',    # COMEX Silver Futures
            'CRUDEOIL': 'CL=F',  # WTI Crude Oil Futures
            'NATURALGAS': 'NG=F', # Natural Gas Futures
            'COPPER': 'HG=F',    # Copper Futures
            'ALUMINIUM': 'ALI=F', # Aluminum Futures (CME)
            'ZINC': 'ZN=F',      # Zinc
            'NICKEL': 'NI=F',    # Nickel (note: may not have good data)
            'LEAD': 'LE=F',      # Lead (may not be available)
            'COTTON': 'CT=F',    # Cotton Futures
        }
        
        yf_symbol = yf_symbol_map.get(symbol.upper())
        if not yf_symbol:
            return None
        
        try:
            ticker = yf.Ticker(yf_symbol)
            history = ticker.history(period="5d")
            
            if history.empty:
                logger.warning(f"No yfinance data for commodity {symbol}")
                return None
            
            # Get latest data
            latest = history.iloc[-1]
            current_price_usd = float(latest['Close'])
            
            # Get previous close
            previous_close_usd = float(history.iloc[-2]['Close']) if len(history) >= 2 else current_price_usd
            
            # Get USDINR rate for conversion to Indian prices
            usdinr = yf.Ticker("INR=X")
            usdinr_history = usdinr.history(period="1d")
            usdinr_rate = float(usdinr_history.iloc[-1]['Close']) if not usdinr_history.empty else 83.0
            
            # Convert to INR and adjust units based on commodity
            # MCX Gold is per 10 grams, COMEX Gold is per troy ounce (31.1 grams)
            # MCX Silver is per kg, COMEX Silver is per troy ounce
            # MCX Crude is per barrel, same as WTI
            unit_conversions = {
                'GOLD': 31.1 / 10,      # Troy oz to 10 grams
                'SILVER': 31.1 / 1000,  # Troy oz to kg
                'CRUDEOIL': 1,          # Both per barrel
                'NATURALGAS': 1,        # mmBtu
                'COPPER': 2.205,        # lb to kg
                'ALUMINIUM': 2.205,     # lb to kg
                'COTTON': 1,            # per bale approximation
            }
            
            conversion = unit_conversions.get(symbol.upper(), 1)
            current_price = round(current_price_usd * usdinr_rate / conversion, 2)
            previous_close = round(previous_close_usd * usdinr_rate / conversion, 2)
            day_high = round(float(latest['High']) * usdinr_rate / conversion, 2)
            day_low = round(float(latest['Low']) * usdinr_rate / conversion, 2)
            
            logger.info(f"✅ Got real price for {symbol} from yfinance: ₹{current_price}")
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'previous_close': previous_close,
                'day_high': day_high,
                'day_low': day_low,
                'volume': int(latest.get('Volume', 0)),
                'data_source': 'yfinance (Global)',
                'success': True
            }
            
        except Exception as e:
            logger.warning(f"yfinance fetch failed for commodity {symbol}: {e}")
            return None
    
    def _get_live_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Attempt to fetch live data - tries multiple sources"""
        # Try APIdatafeed first (primary for Indian MCX data)
        live_data = self._fetch_from_apidatafeed(symbol)
        if live_data:
            return live_data
        
        # Try Commodities-API (global data)
        live_data = self._fetch_from_commodities_api(symbol)
        if live_data:
            return live_data
        
        # Try yfinance as fallback (always available, no API key needed)
        live_data = self._fetch_from_yfinance(symbol)
        if live_data:
            return live_data
        
        return None
    
    def search_commodity(self, query: str) -> List[Dict[str, Any]]:
        """Search for commodities by name or symbol"""
        query_upper = query.upper().strip()
        results = []
        
        for symbol, data in self.common_commodities.items():
            if query_upper in symbol or query_upper in data['name'].upper():
                results.append({
                    'symbol': symbol,
                    'name': data['name'],
                    'exchange': data['exchange'],
                    'category': data['category'],
                    'current_price': data['current_price'],
                    'unit': data['unit']
                })
        
        return results
    
    def get_commodity_details(self, symbol: str) -> Dict[str, Any]:
        """Get detailed commodity information - tries live APIs first, falls back to sample data"""
        symbol_upper = symbol.upper().strip()
        
        live_data = self._get_live_data(symbol_upper)
        
        if symbol_upper in self.common_commodities:
            commodity = self.common_commodities[symbol_upper].copy()
            
            if live_data:
                commodity['current_price'] = live_data.get('current_price', commodity['current_price'])
                commodity['previous_close'] = live_data.get('previous_close', commodity['previous_close'])
                commodity['day_high'] = live_data.get('day_high', commodity.get('day_high', 0))
                commodity['day_low'] = live_data.get('day_low', commodity.get('day_low', 0))
                commodity['open_interest'] = live_data.get('open_interest', commodity.get('open_interest', 0))
                commodity['volume'] = live_data.get('volume', commodity.get('volume', 0))
                commodity['data_source'] = live_data.get('data_source', 'MCX/APIdatafeed (Live)')
            else:
                commodity['data_source'] = 'MCX/APIdatafeed (Sample)'
            
            price_change = commodity['current_price'] - commodity['previous_close']
            price_change_pct = (price_change / commodity['previous_close']) * 100 if commodity['previous_close'] else 0
            
            commodity['price_change'] = round(price_change, 2)
            commodity['price_change_pct'] = round(price_change_pct, 2)
            commodity['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            commodity['success'] = True
            
            return commodity
        
        return {'success': False, 'error': f'Commodity {symbol} not found'}
    
    def get_commodity_list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available commodities"""
        results = []
        
        for symbol, data in self.common_commodities.items():
            if category and data['category'] != category:
                continue
            
            results.append({
                'symbol': symbol,
                'name': data['name'],
                'exchange': data['exchange'],
                'category': data['category'],
                'current_price': data['current_price'],
                'price_change_pct': round(((data['current_price'] - data['previous_close']) / data['previous_close']) * 100, 2),
                'trend': data['trend']
            })
        
        return results
    
    def analyze_for_iscore(self, query: str) -> Dict[str, Any]:
        """Get commodity data formatted for I-Score analysis"""
        search_results = self.search_commodity(query)
        
        if search_results:
            symbol = search_results[0]['symbol']
            commodity_data = self.get_commodity_details(symbol)
            
            if commodity_data.get('success'):
                trend = commodity_data.get('trend', 'neutral')
                volatility = commodity_data.get('volatility', 15.0)
                open_interest = commodity_data.get('open_interest', 5000)
                volume = commodity_data.get('volume', 2000)
                supply_outlook = commodity_data.get('supply_outlook', 'balanced')
                
                trend_scores = {
                    'bullish': 75,
                    'neutral': 55,
                    'bearish': 35,
                    'volatile': 50
                }
                trend_score = trend_scores.get(trend, 50)
                
                if volatility < 15:
                    volatility_score = 70
                    volatility_risk = 'Low'
                elif volatility < 25:
                    volatility_score = 55
                    volatility_risk = 'Moderate'
                elif volatility < 35:
                    volatility_score = 40
                    volatility_risk = 'High'
                else:
                    volatility_score = 30
                    volatility_risk = 'Very High'
                
                if volume > 10000:
                    liquidity_score = 80
                elif volume > 5000:
                    liquidity_score = 65
                elif volume > 2000:
                    liquidity_score = 50
                else:
                    liquidity_score = 35
                
                supply_scores = {
                    'deficit': 70,
                    'tight': 65,
                    'balanced': 55,
                    'moderate_surplus': 45,
                    'oversupply': 35,
                    'stable': 55,
                    'moderate_deficit': 65,
                    'weather_dependent': 50
                }
                supply_score = supply_scores.get(supply_outlook, 50)
                
                return {
                    'success': True,
                    'symbol': symbol,
                    'name': commodity_data.get('name'),
                    'exchange': commodity_data.get('exchange'),
                    'category': commodity_data.get('category'),
                    'unit': commodity_data.get('unit'),
                    'current_price': commodity_data.get('current_price'),
                    'previous_close': commodity_data.get('previous_close'),
                    'price_change': commodity_data.get('price_change'),
                    'price_change_pct': commodity_data.get('price_change_pct'),
                    'day_high': commodity_data.get('day_high'),
                    'day_low': commodity_data.get('day_low'),
                    'open_interest': open_interest,
                    'volume': volume,
                    'contract_month': commodity_data.get('contract_month'),
                    'expiry_date': commodity_data.get('expiry_date'),
                    'margin_required': commodity_data.get('margin_required'),
                    'lot_size': commodity_data.get('lot_size'),
                    'volatility': volatility,
                    'volatility_score': volatility_score,
                    'volatility_risk': volatility_risk,
                    'trend': trend,
                    'trend_score': trend_score,
                    'liquidity_score': liquidity_score,
                    'supply_outlook': supply_outlook,
                    'supply_score': supply_score,
                    'global_benchmark': commodity_data.get('global_benchmark'),
                    'correlation_usd': commodity_data.get('correlation_usd'),
                    'seasonal_factor': commodity_data.get('seasonal_factor'),
                    'data_source': 'MCX/APIdatafeed'
                }
        
        return {
            'success': False,
            'error': f'Commodity {query} not found',
            'symbol': query
        }


commodity_service = CommodityService()
