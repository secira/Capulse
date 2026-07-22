"""
Currency Service for Capulse
Provides forex/currency data for trading analysis

Data Sources:
- Primary: TraderMade (real-time streaming with millisecond updates)
- Backup: XE Currency Data API / ExchangeRate-API (reliable REST API access)

API Integration:
- TraderMade API: https://tradermade.com/ (requires API key)
- ExchangeRate-API: https://exchangerate-api.com/ (requires API key)
"""

import requests
import logging
import os
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class CurrencyService:
    """Service to fetch currency/forex data from multiple sources"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'TargeCapulse/1.0'
        })
        
        self.tradermade_key = os.environ.get('TRADERMADE_API_KEY')
        self.exchangerate_key = os.environ.get('EXCHANGERATE_API_KEY')
        
        self.tradermade_base = 'https://marketdata.tradermade.com/api/v1'
        self.exchangerate_base = 'https://v6.exchangerate-api.com/v6'
        
        if self.tradermade_key:
            logger.info("TraderMade API initialized for real-time forex data")
        if self.exchangerate_key:
            logger.info("ExchangeRate-API initialized for forex data")
        
        self.common_pairs = {
            'USDINR': {
                'symbol': 'USDINR',
                'name': 'US Dollar / Indian Rupee',
                'base_currency': 'USD',
                'quote_currency': 'INR',
                'exchange': 'NSE',
                'category': 'Major',
                'lot_size': 1000,
                'current_rate': 83.25,
                'previous_close': 83.18,
                'day_high': 83.35,
                'day_low': 83.12,
                'bid': 83.24,
                'ask': 83.26,
                'spread': 0.02,
                'open_interest': 285000,
                'volume': 125000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-29',
                'margin_required': 2.5,
                'volatility': 4.8,
                'trend': 'bullish',
                'rbi_reference': 83.22,
                'correlation_dxy': 0.85,
                'interest_rate_diff': 4.25,
                'carry_trade_score': 65,
                'central_bank_stance': 'neutral'
            },
            'EURINR': {
                'symbol': 'EURINR',
                'name': 'Euro / Indian Rupee',
                'base_currency': 'EUR',
                'quote_currency': 'INR',
                'exchange': 'NSE',
                'category': 'Major',
                'lot_size': 1000,
                'current_rate': 91.45,
                'previous_close': 91.28,
                'day_high': 91.60,
                'day_low': 91.15,
                'bid': 91.43,
                'ask': 91.47,
                'spread': 0.04,
                'open_interest': 85000,
                'volume': 42000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-29',
                'margin_required': 2.5,
                'volatility': 6.2,
                'trend': 'neutral',
                'rbi_reference': 91.38,
                'correlation_dxy': -0.72,
                'interest_rate_diff': 2.75,
                'carry_trade_score': 55,
                'central_bank_stance': 'dovish'
            },
            'GBPINR': {
                'symbol': 'GBPINR',
                'name': 'British Pound / Indian Rupee',
                'base_currency': 'GBP',
                'quote_currency': 'INR',
                'exchange': 'NSE',
                'category': 'Major',
                'lot_size': 1000,
                'current_rate': 105.85,
                'previous_close': 105.62,
                'day_high': 106.10,
                'day_low': 105.45,
                'bid': 105.82,
                'ask': 105.88,
                'spread': 0.06,
                'open_interest': 62000,
                'volume': 28000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-29',
                'margin_required': 2.5,
                'volatility': 7.5,
                'trend': 'bullish',
                'rbi_reference': 105.72,
                'correlation_dxy': -0.65,
                'interest_rate_diff': 1.50,
                'carry_trade_score': 48,
                'central_bank_stance': 'hawkish'
            },
            'JPYINR': {
                'symbol': 'JPYINR',
                'name': 'Japanese Yen / Indian Rupee',
                'base_currency': 'JPY',
                'quote_currency': 'INR',
                'exchange': 'NSE',
                'category': 'Major',
                'lot_size': 100000,
                'current_rate': 0.5425,
                'previous_close': 0.5412,
                'day_high': 0.5440,
                'day_low': 0.5405,
                'bid': 0.5423,
                'ask': 0.5427,
                'spread': 0.0004,
                'open_interest': 45000,
                'volume': 18000,
                'contract_month': 'JAN2025',
                'expiry_date': '2025-01-29',
                'margin_required': 2.5,
                'volatility': 8.2,
                'trend': 'volatile',
                'rbi_reference': 0.5418,
                'correlation_dxy': -0.55,
                'interest_rate_diff': 6.50,
                'carry_trade_score': 72,
                'central_bank_stance': 'dovish'
            },
            'EURUSD': {
                'symbol': 'EURUSD',
                'name': 'Euro / US Dollar',
                'base_currency': 'EUR',
                'quote_currency': 'USD',
                'exchange': 'Global',
                'category': 'Major',
                'lot_size': 100000,
                'current_rate': 1.0985,
                'previous_close': 1.0972,
                'day_high': 1.1005,
                'day_low': 1.0960,
                'bid': 1.0984,
                'ask': 1.0986,
                'spread': 0.0002,
                'open_interest': None,
                'volume': 850000000,
                'contract_month': 'SPOT',
                'expiry_date': None,
                'margin_required': 3.33,
                'volatility': 5.8,
                'trend': 'neutral',
                'rbi_reference': None,
                'correlation_dxy': -0.95,
                'interest_rate_diff': -1.50,
                'carry_trade_score': 45,
                'central_bank_stance': 'dovish'
            },
            'GBPUSD': {
                'symbol': 'GBPUSD',
                'name': 'British Pound / US Dollar',
                'base_currency': 'GBP',
                'quote_currency': 'USD',
                'exchange': 'Global',
                'category': 'Major',
                'lot_size': 100000,
                'current_rate': 1.2715,
                'previous_close': 1.2698,
                'day_high': 1.2745,
                'day_low': 1.2680,
                'bid': 1.2714,
                'ask': 1.2716,
                'spread': 0.0002,
                'open_interest': None,
                'volume': 420000000,
                'contract_month': 'SPOT',
                'expiry_date': None,
                'margin_required': 3.33,
                'volatility': 7.2,
                'trend': 'bullish',
                'rbi_reference': None,
                'correlation_dxy': -0.88,
                'interest_rate_diff': -0.25,
                'carry_trade_score': 52,
                'central_bank_stance': 'hawkish'
            },
            'USDJPY': {
                'symbol': 'USDJPY',
                'name': 'US Dollar / Japanese Yen',
                'base_currency': 'USD',
                'quote_currency': 'JPY',
                'exchange': 'Global',
                'category': 'Major',
                'lot_size': 100000,
                'current_rate': 153.45,
                'previous_close': 153.28,
                'day_high': 153.80,
                'day_low': 153.05,
                'bid': 153.44,
                'ask': 153.46,
                'spread': 0.02,
                'open_interest': None,
                'volume': 520000000,
                'contract_month': 'SPOT',
                'expiry_date': None,
                'margin_required': 3.33,
                'volatility': 9.5,
                'trend': 'bullish',
                'rbi_reference': None,
                'correlation_dxy': 0.78,
                'interest_rate_diff': 5.25,
                'carry_trade_score': 75,
                'central_bank_stance': 'hawkish'
            },
            'AUDUSD': {
                'symbol': 'AUDUSD',
                'name': 'Australian Dollar / US Dollar',
                'base_currency': 'AUD',
                'quote_currency': 'USD',
                'exchange': 'Global',
                'category': 'Commodity',
                'lot_size': 100000,
                'current_rate': 0.6245,
                'previous_close': 0.6232,
                'day_high': 0.6268,
                'day_low': 0.6218,
                'bid': 0.6244,
                'ask': 0.6246,
                'spread': 0.0002,
                'open_interest': None,
                'volume': 180000000,
                'contract_month': 'SPOT',
                'expiry_date': None,
                'margin_required': 3.33,
                'volatility': 8.8,
                'trend': 'neutral',
                'rbi_reference': None,
                'correlation_dxy': -0.75,
                'interest_rate_diff': -0.75,
                'carry_trade_score': 48,
                'central_bank_stance': 'neutral'
            },
            'USDCAD': {
                'symbol': 'USDCAD',
                'name': 'US Dollar / Canadian Dollar',
                'base_currency': 'USD',
                'quote_currency': 'CAD',
                'exchange': 'Global',
                'category': 'Commodity',
                'lot_size': 100000,
                'current_rate': 1.4385,
                'previous_close': 1.4372,
                'day_high': 1.4410,
                'day_low': 1.4355,
                'bid': 1.4384,
                'ask': 1.4386,
                'spread': 0.0002,
                'open_interest': None,
                'volume': 165000000,
                'contract_month': 'SPOT',
                'expiry_date': None,
                'margin_required': 3.33,
                'volatility': 6.5,
                'trend': 'bullish',
                'rbi_reference': None,
                'correlation_dxy': 0.82,
                'interest_rate_diff': 1.75,
                'carry_trade_score': 58,
                'central_bank_stance': 'dovish'
            },
            'USDCHF': {
                'symbol': 'USDCHF',
                'name': 'US Dollar / Swiss Franc',
                'base_currency': 'USD',
                'quote_currency': 'CHF',
                'exchange': 'Global',
                'category': 'Safe Haven',
                'lot_size': 100000,
                'current_rate': 0.9025,
                'previous_close': 0.9012,
                'day_high': 0.9048,
                'day_low': 0.8998,
                'bid': 0.9024,
                'ask': 0.9026,
                'spread': 0.0002,
                'open_interest': None,
                'volume': 95000000,
                'contract_month': 'SPOT',
                'expiry_date': None,
                'margin_required': 3.33,
                'volatility': 5.2,
                'trend': 'neutral',
                'rbi_reference': None,
                'correlation_dxy': 0.68,
                'interest_rate_diff': 3.50,
                'carry_trade_score': 62,
                'central_bank_stance': 'dovish'
            }
        }
        
        self.currency_categories = {
            'INR Pairs': ['USDINR', 'EURINR', 'GBPINR', 'JPYINR'],
            'Major': ['EURUSD', 'GBPUSD', 'USDJPY'],
            'Commodity': ['AUDUSD', 'USDCAD'],
            'Safe Haven': ['USDCHF']
        }
    
    def _fetch_from_tradermade(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time forex data from TraderMade API (Primary)"""
        if not self.tradermade_key:
            return None
        
        try:
            url = f"{self.tradermade_base}/live"
            params = {'api_key': self.tradermade_key, 'currency': symbol}
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                quotes = data.get('quotes', [])
                if quotes:
                    quote = quotes[0]
                    logger.info(f"TraderMade: Fetched real-time data for {symbol}")
                    return {
                        'symbol': quote.get('instrument', symbol),
                        'current_rate': quote.get('mid', 0),
                        'bid': quote.get('bid', 0),
                        'ask': quote.get('ask', 0),
                        'day_high': quote.get('high', 0),
                        'day_low': quote.get('low', 0),
                        'previous_close': quote.get('previous_close', 0),
                        'data_source': 'TraderMade (Live)',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"TraderMade API error for {symbol}: {e}")
        
        return None
    
    def _fetch_from_exchangerate(self, base: str, quote: str) -> Optional[Dict[str, Any]]:
        """Fetch forex data from ExchangeRate-API (Backup)"""
        if not self.exchangerate_key:
            return None
        
        try:
            url = f"{self.exchangerate_base}/{self.exchangerate_key}/pair/{base}/{quote}"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('result') == 'success':
                    logger.info(f"ExchangeRate-API: Fetched data for {base}/{quote}")
                    return {
                        'symbol': f"{base}{quote}",
                        'current_rate': data.get('conversion_rate', 0),
                        'data_source': 'ExchangeRate-API',
                        'success': True
                    }
        except Exception as e:
            logger.warning(f"ExchangeRate-API error for {base}/{quote}: {e}")
        
        return None
    
    def _fetch_from_yfinance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch forex data from yfinance (always available, no API key)"""
        # Convert to yfinance format: USDINR -> USDINR=X
        yf_symbol = f"{symbol}=X"
        
        try:
            ticker = yf.Ticker(yf_symbol)
            history = ticker.history(period="5d")
            
            if history.empty:
                logger.warning(f"No yfinance data for currency {symbol}")
                return None
            
            # Get latest data
            latest = history.iloc[-1]
            current_rate = float(latest['Close'])
            
            # Get previous close
            previous_close = float(history.iloc[-2]['Close']) if len(history) >= 2 else current_rate
            
            day_high = float(latest['High'])
            day_low = float(latest['Low'])
            
            # Calculate bid/ask (approximate spread)
            spread = current_rate * 0.0002  # Typical spread
            bid = round(current_rate - spread/2, 4)
            ask = round(current_rate + spread/2, 4)
            
            logger.info(f"✅ Got real rate for {symbol} from yfinance: {current_rate}")
            
            return {
                'symbol': symbol,
                'current_rate': round(current_rate, 4),
                'previous_close': round(previous_close, 4),
                'day_high': round(day_high, 4),
                'day_low': round(day_low, 4),
                'bid': bid,
                'ask': ask,
                'data_source': 'yfinance (Live)',
                'success': True
            }
            
        except Exception as e:
            logger.warning(f"yfinance fetch failed for currency {symbol}: {e}")
            return None
    
    def _get_live_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Attempt to fetch live data - tries multiple sources"""
        # Try TraderMade first (primary)
        live_data = self._fetch_from_tradermade(symbol)
        if live_data:
            return live_data
        
        # Try ExchangeRate-API
        if len(symbol) == 6:
            base = symbol[:3]
            quote = symbol[3:]
            live_data = self._fetch_from_exchangerate(base, quote)
            if live_data:
                return live_data
        
        # Try yfinance as fallback (always available, no API key)
        live_data = self._fetch_from_yfinance(symbol)
        if live_data:
            return live_data
        
        return None
    
    def search_currency(self, query: str) -> List[Dict[str, Any]]:
        """Search for currency pairs by name or symbol"""
        query_upper = query.upper().strip().replace('/', '')
        results = []
        
        for symbol, data in self.common_pairs.items():
            if query_upper in symbol or query_upper in data['name'].upper().replace('/', '').replace(' ', ''):
                results.append({
                    'symbol': symbol,
                    'name': data['name'],
                    'exchange': data['exchange'],
                    'category': data['category'],
                    'current_rate': data['current_rate'],
                    'base_currency': data['base_currency'],
                    'quote_currency': data['quote_currency']
                })
        
        return results
    
    def get_currency_details(self, symbol: str) -> Dict[str, Any]:
        """Get detailed currency pair information - tries live APIs first, falls back to sample data"""
        symbol_upper = symbol.upper().strip().replace('/', '')
        
        live_data = self._get_live_data(symbol_upper)
        
        if symbol_upper in self.common_pairs:
            currency = self.common_pairs[symbol_upper].copy()
            
            if live_data:
                currency['current_rate'] = live_data.get('current_rate', currency['current_rate'])
                currency['bid'] = live_data.get('bid', currency.get('bid', 0))
                currency['ask'] = live_data.get('ask', currency.get('ask', 0))
                currency['day_high'] = live_data.get('day_high', currency.get('day_high', 0))
                currency['day_low'] = live_data.get('day_low', currency.get('day_low', 0))
                currency['previous_close'] = live_data.get('previous_close', currency.get('previous_close', 0))
                currency['data_source'] = live_data.get('data_source', 'TraderMade (Live)')
            else:
                currency['data_source'] = 'TraderMade/ExchangeRate-API (Sample)'
            
            rate_change = currency['current_rate'] - currency['previous_close']
            rate_change_pct = (rate_change / currency['previous_close']) * 100 if currency['previous_close'] else 0
            
            currency['rate_change'] = round(rate_change, 4)
            currency['rate_change_pct'] = round(rate_change_pct, 2)
            currency['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            currency['success'] = True
            
            return currency
        
        return {'success': False, 'error': f'Currency pair {symbol} not found'}
    
    def get_currency_list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available currency pairs"""
        results = []
        
        for symbol, data in self.common_pairs.items():
            if category and data['category'] != category:
                continue
            
            results.append({
                'symbol': symbol,
                'name': data['name'],
                'exchange': data['exchange'],
                'category': data['category'],
                'current_rate': data['current_rate'],
                'rate_change_pct': round(((data['current_rate'] - data['previous_close']) / data['previous_close']) * 100, 2),
                'trend': data['trend']
            })
        
        return results
    
    def analyze_for_iscore(self, query: str) -> Dict[str, Any]:
        """Get currency data formatted for I-Score analysis"""
        search_results = self.search_currency(query)
        
        if search_results:
            symbol = search_results[0]['symbol']
            currency_data = self.get_currency_details(symbol)
            
            if currency_data.get('success'):
                trend = currency_data.get('trend', 'neutral')
                volatility = currency_data.get('volatility', 6.0)
                spread = currency_data.get('spread', 0.0002)
                volume = currency_data.get('volume', 100000000)
                carry_trade_score = currency_data.get('carry_trade_score', 50)
                interest_rate_diff = currency_data.get('interest_rate_diff', 0)
                central_bank_stance = currency_data.get('central_bank_stance', 'neutral')
                
                trend_scores = {
                    'bullish': 72,
                    'neutral': 52,
                    'bearish': 38,
                    'volatile': 48
                }
                trend_score = trend_scores.get(trend, 50)
                
                if volatility < 5:
                    volatility_score = 75
                    volatility_risk = 'Low'
                elif volatility < 7:
                    volatility_score = 60
                    volatility_risk = 'Moderate'
                elif volatility < 9:
                    volatility_score = 45
                    volatility_risk = 'High'
                else:
                    volatility_score = 35
                    volatility_risk = 'Very High'
                
                current_rate = currency_data.get('current_rate', 1)
                spread_pct = (spread / current_rate) * 100
                if spread_pct < 0.01:
                    liquidity_score = 85
                elif spread_pct < 0.05:
                    liquidity_score = 70
                elif spread_pct < 0.1:
                    liquidity_score = 55
                else:
                    liquidity_score = 40
                
                stance_scores = {
                    'hawkish': 70,
                    'neutral': 55,
                    'dovish': 40
                }
                stance_score = stance_scores.get(central_bank_stance, 50)
                
                return {
                    'success': True,
                    'symbol': symbol,
                    'name': currency_data.get('name'),
                    'base_currency': currency_data.get('base_currency'),
                    'quote_currency': currency_data.get('quote_currency'),
                    'exchange': currency_data.get('exchange'),
                    'category': currency_data.get('category'),
                    'current_rate': currency_data.get('current_rate'),
                    'previous_close': currency_data.get('previous_close'),
                    'rate_change': currency_data.get('rate_change'),
                    'rate_change_pct': currency_data.get('rate_change_pct'),
                    'day_high': currency_data.get('day_high'),
                    'day_low': currency_data.get('day_low'),
                    'bid': currency_data.get('bid'),
                    'ask': currency_data.get('ask'),
                    'spread': spread,
                    'spread_pct': round(spread_pct, 4),
                    'open_interest': currency_data.get('open_interest'),
                    'volume': volume,
                    'contract_month': currency_data.get('contract_month'),
                    'expiry_date': currency_data.get('expiry_date'),
                    'margin_required': currency_data.get('margin_required'),
                    'lot_size': currency_data.get('lot_size'),
                    'volatility': volatility,
                    'volatility_score': volatility_score,
                    'volatility_risk': volatility_risk,
                    'trend': trend,
                    'trend_score': trend_score,
                    'liquidity_score': liquidity_score,
                    'carry_trade_score': carry_trade_score,
                    'interest_rate_diff': interest_rate_diff,
                    'central_bank_stance': central_bank_stance,
                    'stance_score': stance_score,
                    'correlation_dxy': currency_data.get('correlation_dxy'),
                    'rbi_reference': currency_data.get('rbi_reference'),
                    'data_source': 'TraderMade/ExchangeRate-API'
                }
        
        return {
            'success': False,
            'error': f'Currency pair {query} not found',
            'symbol': query
        }


currency_service = CurrencyService()
