import logging
import requests
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class DhanBroker(BrokerBase):

    BROKER_NAME = "dhan"
    SUPPORTS_DIRECT_CHAIN = True

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.base_url = "https://api.dhan.co/v2"
        self.client_id = credentials.get("client_id", "")
        self.access_token = credentials.get("access_token", "")
        self.headers = {
            "access-token": self.access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def connect(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/fundlimit", timeout=10)
            if resp.status_code == 200:
                self._connected = True
                return True
            logger.warning(f"Dhan connect failed: {resp.status_code} {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Dhan connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            security_id = self._get_security_id(symbol)
            if not security_id:
                return 0.0
            payload = {
                "NSE_INDEX": [str(security_id)]
            }
            resp = self.session.post(f"{self.base_url}/marketfeed/ltp", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data and len(data["data"]) > 0:
                    return float(data["data"][0].get("last_price", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Dhan get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            payload = {"NSE_FNO": [str(t) for t in tokens]}
            resp = self.session.post(f"{self.base_url}/marketfeed/quote", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                result = {}
                for item in data:
                    token_id = str(item.get("security_id", ""))
                    result[token_id] = {
                        "ltp": item.get("last_price", 0),
                        "oi": item.get("oi", 0),
                        "volume": item.get("volume", 0),
                        "bid": item.get("best_bid_price", 0),
                        "ask": item.get("best_ask_price", 0),
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"Dhan get_quotes error: {e}")
            return {}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        try:
            resp = self.session.get(
                f"{self.base_url}/optionchain",
                params={"under": symbol, "expiry": expiry or ""},
                timeout=15,
            )
            if resp.status_code == 200:
                raw = resp.json().get("data", [])
                chain = []
                for row in raw:
                    chain.append({
                        "strike": int(row.get("strikePrice", 0)),
                        "call_ltp": row.get("ce_ltp", 0),
                        "put_ltp": row.get("pe_ltp", 0),
                        "call_oi": row.get("ce_oi", 0),
                        "put_oi": row.get("pe_oi", 0),
                        "call_iv": row.get("ce_iv", 0),
                        "put_iv": row.get("pe_iv", 0),
                        "call_volume": row.get("ce_volume", 0),
                        "put_volume": row.get("pe_volume", 0),
                        "call_bid": row.get("ce_bid", 0),
                        "call_ask": row.get("ce_ask", 0),
                        "put_bid": row.get("pe_bid", 0),
                        "put_ask": row.get("pe_ask", 0),
                        "call_oi_change": row.get("ce_oi_change", 0),
                        "put_oi_change": row.get("pe_oi_change", 0),
                    })
                return chain
            logger.warning(f"Dhan option chain failed: {resp.status_code}")
            return []
        except Exception as e:
            logger.error(f"Dhan get_option_chain error: {e}")
            return []

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            security_id = self._get_security_id(symbol)
            payload = {
                "dhanClientId": self.client_id,
                "transactionType": "BUY" if side.upper() == "BUY" else "SELL",
                "exchangeSegment": "NSE_FNO",
                "productType": "INTRADAY" if product.upper() in ("INTRADAY", "MIS") else "CNC",
                "orderType": order_type.upper(),
                "validity": "DAY",
                "securityId": str(security_id),
                "quantity": qty,
                "price": price,
                "triggerPrice": trigger_price,
            }
            resp = self.session.post(f"{self.base_url}/orders", json=payload, timeout=15)
            data = resp.json()
            return {"status": data.get("status", "unknown"), "order_id": data.get("orderId"), "raw": data}
        except Exception as e:
            logger.error(f"Dhan place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/holdings", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Dhan get_holdings error: {e}")
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/positions", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Dhan get_positions error: {e}")
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/orders", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"Dhan get_orders error: {e}")
            return []

    def _get_security_id(self, symbol: str) -> Optional[str]:
        symbol_map = {
            "NIFTY": "13",
            "NIFTY 50": "13",
            "BANKNIFTY": "25",
            "BANK NIFTY": "25",
            "FINNIFTY": "27",
            "SENSEX": "1",
        }
        return symbol_map.get(symbol.upper(), symbol)
