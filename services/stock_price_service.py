"""
Stock Price Service - Get real prices from multiple sources
Priority: Dhan > Perplexity > yfinance > NSE > Fallback
"""

import logging
from typing import Dict, Any, Optional

class StockPriceService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def get_real_price(self, symbol: str, perplexity_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Get real stock price with priority fallback:
        1. Dhan OHLC (direct exchange data, most reliable)
        2. Perplexity-provided price (real-time web data)
        3. yfinance (Yahoo Finance - reliable Indian stock data)
        4. NSE API (if available)
        5. Fallback demo data
        
        Args:
            symbol: NSE stock symbol (e.g., 'RELIANCE', 'TCS')
            perplexity_price: Price already fetched by Perplexity (if available)
            
        Returns:
            Dict with 'price', 'source', and 'company_name'
        """
        
        # PRIORITY 1: Try Dhan OHLC
        try:
            from services.dhan_service import get_eq_quote
            dhan_data = get_eq_quote(symbol)
            if dhan_data and dhan_data.get("ltp", 0) > 0:
                price = float(dhan_data["ltp"])
                self.logger.info(f"{symbol}: Using Dhan price: ₹{price}")
                return {
                    'price': price,
                    'source': 'Dhan',
                    'company_name': None
                }
        except Exception as e:
            self.logger.debug(f"{symbol}: Dhan lookup skipped: {e}")

        # PRIORITY 2: Use Perplexity price if available
        if perplexity_price and perplexity_price > 0:
            self.logger.info(f"{symbol}: Using Perplexity real-time price: ₹{perplexity_price}")
            return {
                'price': perplexity_price,
                'source': 'Perplexity Real-time',
                'company_name': None
            }
        
        # PRIORITY 3: Try yfinance (Yahoo Finance)
        try:
            import yfinance as yf
            ticker = yf.Ticker(f"{symbol}.NS")
            
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = float(hist['Close'].iloc[-1])
                company_name = ticker.info.get('longName', ticker.info.get('shortName', None))
                self.logger.info(f"{symbol}: Using yfinance price: ₹{price}")
                return {
                    'price': price,
                    'source': 'Yahoo Finance',
                    'company_name': company_name
                }
        except Exception as e:
            self.logger.warning(f"{symbol}: yfinance failed: {str(e)}")
        
        # PRIORITY 4: Try NSE API
        try:
            from services.nse_service import nse_service
            live_quote = nse_service.get_stock_quote(symbol, delayed_minutes=5)
            if live_quote and live_quote.get('current_price', 0) > 0:
                price = live_quote.get('current_price')
                company_name = live_quote.get('company_name')
                self.logger.info(f"{symbol}: Using NSE API price: ₹{price}")
                return {
                    'price': price,
                    'source': 'NSE (5-min delayed)',
                    'company_name': company_name
                }
        except Exception as e:
            self.logger.warning(f"{symbol}: NSE API failed: {str(e)}")
        
        # FALLBACK: Demo data
        self.logger.warning(f"{symbol}: All price sources failed, using fallback")
        fallback_prices = {
            'RELIANCE': 1270.00,
            'TCS': 3200.00,
            'HDFCBANK': 1750.00,
            'INFY': 1450.00,
            'ICICIBANK': 1310.00,
            'SBIN': 780.00
        }
        
        return {
            'price': fallback_prices.get(symbol, 2500.0),
            'source': 'Fallback Data',
            'company_name': None
        }

# Global instance
stock_price_service = StockPriceService()
