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
        """Fetch real-time NSE indices data.

        Priority chain (via Market Data Gateway):
          0. Admin Broker Pool  — admin-configured Dhan/Zerodha/etc.
          1. System Dhan        — any connected DataApiBroker
          2. yfinance           — universal fallback
        """
        current_time = datetime.now(timezone.utc)

        # ── Priority 0: Admin Broker Pool (via gateway) ──────────────────────
        try:
            from services.market_data_gateway import get_index_prices, SRC_ADMIN
            gw = get_index_prices()
            if gw.get('_success') and gw.get('NIFTY', {}).get('ltp', 0) > 0:
                _sym_map = {
                    'NIFTY':     'NIFTY 50',
                    'BANKNIFTY': 'NIFTY BANK',
                    'FINNIFTY':  'NIFTY FIN SERVICE',
                    'SENSEX':    'SENSEX',
                    'INDIA VIX': 'INDIA VIX',
                }
                indices_data = {}
                for gw_key, display_name in _sym_map.items():
                    entry = gw.get(gw_key, {})
                    if isinstance(entry, dict) and entry.get('ltp', 0) > 0:
                        indices_data[display_name] = {
                            'value':          entry['ltp'],
                            'change':         entry.get('change', 0),
                            'change_percent': entry.get('pct_change', 0),
                            'timestamp':      current_time.isoformat(),
                            'source':         entry.get('source', SRC_ADMIN),
                        }
                if indices_data:
                    src_label = gw.get('_source', SRC_ADMIN)
                    logger.info(f"Live Market Pulse: gateway returned {list(indices_data.keys())} via {src_label}")
                    return {
                        'success': True, 'data': indices_data,
                        'timestamp': current_time.isoformat(), 'source': src_label,
                    }
        except Exception as e:
            logger.warning(f"Live Market Pulse gateway indices failed: {e}")

        # ── Priority 1: System Dhan DataApiBroker ────────────────────────────
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

        # ── Priority 2: yfinance (fallback) ────────────────────────────────
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
                logger.info(f"Live Market Pulse: yfinance fallback returned {list(indices_data.keys())}")
                return {'success': True, 'data': indices_data, 'timestamp': current_time.isoformat(), 'source': 'yfinance'}
        except Exception as e:
            logger.warning(f"yfinance indices fallback failed: {e}")

        return {'success': False, 'data': {}, 'timestamp': current_time.isoformat(), 'source': 'none'}

    def get_fallback_indices_data(self) -> Dict:
        """Kept for backward compatibility — delegates to get_nse_indices."""
        return self.get_nse_indices()
    
    def get_stock_data(self, symbol: str, user_id: Optional[int] = None) -> Dict:
        """Fetch real-time stock data for a given symbol.

        Priority chain (per user directive — data must come from a broker, not NSE):
          1. User's configured Data API broker:
               - Dhan     → dedicated NSE_EQ OHLC API (full OHLC + LTP)
               - Others   → broker.get_price(symbol) for LTP
          2. System-level Dhan DataApiBroker (any connected account)
          3. yfinance fast_info (last resort — no API key required)
        """

        # ── Priority 1: User's configured Data API broker ─────────────────
        if user_id:
            try:
                from services.broker_factory import get_data_broker_for_user
                from brokers.dhan import DhanBroker
                broker = get_data_broker_for_user(user_id)
                if broker:
                    if isinstance(broker, DhanBroker):
                        from services.dhan_service import get_eq_quote
                        dhan_data = get_eq_quote(symbol, user_id)
                        if dhan_data and dhan_data.get("ltp", 0) > 0:
                            return self._dhan_dict(symbol, dhan_data)
                    else:
                        if broker.connect():
                            ltp = broker.get_price(symbol)
                            if ltp and ltp > 0:
                                broker_name = getattr(broker, 'BROKER_NAME', 'broker').capitalize()
                                logger.info(f"{symbol}: {broker_name} price ₹{ltp}")
                                return {
                                    "success": True, "symbol": symbol,
                                    "price": round(ltp, 2), "current_price": round(ltp, 2),
                                    "change": 0.0, "change_percent": 0.0, "volume": 0,
                                    "source": broker_name,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
            except Exception as e:
                logger.debug(f"{symbol}: user data broker failed: {e}")

        # ── Priority 2: System-level Dhan (any connected DataApiBroker) ───
        try:
            from services.dhan_service import get_eq_quote
            dhan_data = get_eq_quote(symbol)
            if dhan_data and dhan_data.get("ltp", 0) > 0:
                return self._dhan_dict(symbol, dhan_data)
        except Exception as e:
            logger.debug(f"{symbol}: system Dhan lookup skipped: {e}")

        # ── Priority 3: yfinance fast_info (no API key needed) ────────────
        return self._yfinance_fallback(symbol)

    @staticmethod
    def _dhan_dict(symbol: str, dhan_data: Dict) -> Dict:
        """Build a normalised price dict from a dhan_service.get_eq_quote() result."""
        ltp       = float(dhan_data["ltp"])
        prev      = float(dhan_data.get("close", 0))
        change    = float(dhan_data.get("change", ltp - prev if prev else 0))
        change_pct = float(dhan_data.get("pct_change", (change / prev * 100) if prev else 0))
        return {
            "success": True, "symbol": symbol,
            "price": ltp, "current_price": ltp,
            "change": change, "change_percent": change_pct, "volume": 0,
            "source": "Dhan",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _yfinance_fallback(self, symbol: str) -> Dict:
        """Use yfinance fast_info as fallback — avoids slow history download.

        Falls back to `previous_close` when `last_price` is 0 (some symbols
        report only previous_close outside market hours), so the UI never
        shows "price unavailable" for a valid NSE stock just because the
        market is shut.
        """
        try:
            import yfinance as yf
            fi = yf.Ticker(f"{symbol}.NS").fast_info
            ltp  = float(getattr(fi, 'last_price', 0) or 0)
            prev = float(getattr(fi, 'previous_close', 0) or 0)
            # If live LTP is missing, fall back to previous close so the
            # user always sees the last known traded price.
            effective = ltp if ltp > 0 else prev
            if effective > 0:
                change     = round(ltp - prev, 2) if (ltp > 0 and prev) else 0.0
                change_pct = round(change / prev * 100, 2) if (ltp > 0 and prev) else 0.0
                src = 'yfinance' if ltp > 0 else 'yfinance (prev close)'
                logger.info(f"yfinance fast_info for {symbol}: ₹{effective:.2f} ({src})")
                return {
                    'success': True,
                    'symbol': symbol,
                    'price': round(effective, 2),
                    'current_price': round(effective, 2),
                    'change': change,
                    'change_percent': change_pct,
                    'volume': int(getattr(fi, 'three_month_average_volume', 0) or 0),
                    'source': src,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.warning(f"yfinance fast_info failed for {symbol}: {e}")

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

def get_stock_quote(symbol: str, user_id: Optional[int] = None):
    """Get live stock quote — routes through user's configured broker if user_id is provided."""
    return nse_service.get_stock_data(symbol, user_id=user_id)