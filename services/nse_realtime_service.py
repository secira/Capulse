"""
Real-time NSE data service for Target Capital
Fetches live market data from NSE API and other sources
"""

import requests
from datetime import datetime, timezone
import json
import time
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class NSERealTimeService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
    def get_nse_indices(self) -> Dict:
        """Fetch real-time NSE indices data — tries Dhan → NSE API → yfinance."""
        current_time = datetime.now(timezone.utc)

        # ── Priority 1: Dhan DataApiBroker ──────────────────────────────────
        try:
            from services.dhan_service import get_index_quotes
            dhan_data = get_index_quotes()
            if dhan_data:
                _sym_map = {
                    'NIFTY':     'NIFTY 50',
                    'BANKNIFTY': 'NIFTY BANK',
                    'FINNIFTY':  'NIFTY FIN SERVICE',
                    'SENSEX':    'SENSEX',
                    'INDIA VIX': 'INDIA VIX',
                }
                indices_data = {}
                for dhan_key, display_name in _sym_map.items():
                    entry = dhan_data.get(dhan_key, {})
                    if entry.get('ltp', 0) > 0:
                        ltp   = float(entry['ltp'])
                        close = float(entry.get('close', 0))
                        chg   = float(entry.get('change', ltp - close if close else 0))
                        pct   = float(entry.get('pct_change', (chg / close * 100) if close else 0))
                        indices_data[display_name] = {
                            'value':          ltp,
                            'change':         round(chg, 2),
                            'change_percent': round(pct, 2),
                            'timestamp':      current_time.isoformat(),
                            'source':         'dhan',
                        }
                if indices_data:
                    logger.info(f"Live Market Pulse: Dhan returned {list(indices_data.keys())}")
                    return {'success': True, 'data': indices_data, 'timestamp': current_time.isoformat(), 'source': 'dhan'}
        except Exception as e:
            logger.warning(f"Live Market Pulse Dhan fetch failed: {e}")

        # ── Priority 2: NSE official API ────────────────────────────────────
        try:
            url = "https://www.nseindia.com/api/allIndices"
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                _want = {'NIFTY 50', 'NIFTY BANK', 'NIFTY IT', 'NIFTY AUTO', 'NIFTY FIN SERVICE', 'INDIA VIX'}
                indices_data = {}
                for index in data.get('data', []):
                    if index['index'] in _want:
                        indices_data[index['index']] = {
                            'value':          float(index['last']),
                            'change':         float(index['change']),
                            'change_percent': float(index['pChange']),
                            'timestamp':      current_time.isoformat(),
                            'source':         'nse',
                        }
                if indices_data:
                    return {'success': True, 'data': indices_data, 'timestamp': current_time.isoformat(), 'source': 'nse'}
        except Exception as e:
            logger.warning(f"NSE API failed: {e}")

        # ── Priority 3: yfinance ─────────────────────────────────────────────
        try:
            import yfinance as yf
            _yf_map = {
                'NIFTY 50':   '^NSEI',
                'NIFTY BANK': '^NSEBANK',
                'SENSEX':     '^BSESN',
                'INDIA VIX':  '^INDIAVIX',
            }
            indices_data = {}
            for display_name, ticker_sym in _yf_map.items():
                try:
                    fi = yf.Ticker(ticker_sym).fast_info
                    ltp  = float(getattr(fi, 'last_price', 0) or 0)
                    prev = float(getattr(fi, 'previous_close', 0) or 0)
                    if ltp > 0:
                        chg = round(ltp - prev, 2) if prev else 0
                        pct = round(chg / prev * 100, 2) if prev else 0
                        indices_data[display_name] = {
                            'value': ltp, 'change': chg, 'change_percent': pct,
                            'timestamp': current_time.isoformat(), 'source': 'yfinance',
                        }
                except Exception:
                    pass
            if indices_data:
                logger.info(f"Live Market Pulse: yfinance returned {list(indices_data.keys())}")
                return {'success': True, 'data': indices_data, 'timestamp': current_time.isoformat(), 'source': 'yfinance'}
        except Exception as e:
            logger.warning(f"yfinance indices fallback failed: {e}")

        return {'success': False, 'data': {}, 'timestamp': current_time.isoformat(), 'source': 'none'}

    def get_fallback_indices_data(self) -> Dict:
        """Kept for backward compatibility — delegates to get_nse_indices."""
        return self.get_nse_indices()
    
    def get_stock_data(self, symbol: str) -> Dict:
        """Fetch real-time stock data for a given symbol"""
        # PRIORITY 1: Dhan OHLC (direct exchange data)
        try:
            from services.dhan_service import get_eq_quote
            dhan_data = get_eq_quote(symbol)
            if dhan_data and dhan_data.get("ltp", 0) > 0:
                ltp = float(dhan_data["ltp"])
                prev_close = float(dhan_data.get("close", 0))
                change_amt = float(dhan_data.get("change", ltp - prev_close if prev_close else 0))
                change_pct = float(dhan_data.get("pct_change",
                                   (change_amt / prev_close * 100) if prev_close else 0))
                logger.info(f"{symbol}: Dhan price ₹{ltp}")
                return {
                    "success": True,
                    "symbol": symbol,
                    "price": ltp,
                    "current_price": ltp,
                    "change": change_amt,
                    "change_percent": change_pct,
                    "volume": 0,
                    "source": "Dhan",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.debug(f"{symbol}: Dhan lookup skipped: {e}")

        # PRIORITY 2: NSE API
        try:
            url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'priceInfo' in data:
                    price_info = data['priceInfo']
                    return {
                        'success': True,
                        'symbol': symbol,
                        'price': float(price_info['lastPrice']),
                        'current_price': float(price_info['lastPrice']),
                        'change': float(price_info['change']),
                        'change_percent': float(price_info['pChange']),
                        'volume': int(price_info.get('totalTradedVolume', 0)),
                        'source': 'NSE',
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            logger.warning(f"NSE API failed for {symbol}: {e}")

        # PRIORITY 3: yfinance fallback
        return self._yfinance_fallback(symbol)

    def _yfinance_fallback(self, symbol: str) -> Dict:
        """Use yfinance as fallback to get real NSE prices"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(f"{symbol}.NS")
            hist = ticker.history(period="5d")
            if not hist.empty:
                latest = hist.iloc[-1]
                current_price = float(latest['Close'])
                prev_close = float(hist.iloc[-2]['Close']) if len(hist) >= 2 else current_price
                change = current_price - prev_close
                change_pct = (change / prev_close * 100) if prev_close else 0
                logger.info(f"yfinance fallback for {symbol}: ₹{current_price:.2f}")
                return {
                    'success': True,
                    'symbol': symbol,
                    'price': round(current_price, 2),
                    'change': round(change, 2),
                    'change_percent': round(change_pct, 2),
                    'volume': int(latest.get('Volume', 0)),
                    'source': 'yfinance',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.warning(f"yfinance fallback failed for {symbol}: {e}")

        return {
            'success': False,
            'error': f'No data available for {symbol}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    def get_fallback_stock_data(self, symbol: str) -> Dict:
        """Kept for backwards compatibility — delegates to yfinance fallback"""
        return self._yfinance_fallback(symbol)

# Global service instance
nse_service = NSERealTimeService()

def get_live_market_data():
    """Get comprehensive live market data"""
    return nse_service.get_nse_indices()

def get_stock_quote(symbol: str):
    """Get live stock quote"""
    return nse_service.get_stock_data(symbol)