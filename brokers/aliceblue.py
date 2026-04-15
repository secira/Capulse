import logging
import requests
import hashlib
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class AliceBlueBroker(BrokerBase):

    BROKER_NAME = "alice_blue"
    SUPPORTS_DIRECT_CHAIN = False

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.user_id = credentials.get("user_id", credentials.get("client_id", ""))
        self.api_key = credentials.get("api_key", credentials.get("api_secret", ""))
        self.access_token = credentials.get("access_token", "")
        self.base_url = "https://ant.aliceblueonline.com/rest/AliceBlueAPIService/api"
        self.headers = {
            "Authorization": f"Bearer {self.user_id} {self.access_token}",
            "Content-Type": "application/json",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def connect(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/customer/accountDetails", timeout=10)
            if resp.status_code == 200:
                self._connected = True
                return True
            return False
        except Exception as e:
            logger.error(f"AliceBlue connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            exchange = "NSE"
            token = self._map_token(symbol)
            payload = [{"exchange": exchange, "token": token}]
            resp = self.session.post(f"{self.base_url}/marketdata/ltp", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    return float(data[0].get("ltp", 0))
            return 0.0
        except Exception as e:
            logger.error(f"AliceBlue get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            payload = [{"exchange": "NFO", "token": t} for t in tokens]
            resp = self.session.post(f"{self.base_url}/marketdata/quote", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                result = {}
                if isinstance(data, list):
                    for item in data:
                        t = item.get("token", "")
                        result[t] = {
                            "ltp": float(item.get("ltp", 0)),
                            "oi": int(item.get("openInterest", 0)),
                            "volume": int(item.get("volume", 0)),
                            "bid": float(item.get("bestBidPrice", 0)),
                            "ask": float(item.get("bestAskPrice", 0)),
                        }
                return result
            return {}
        except Exception as e:
            logger.error(f"AliceBlue get_quotes error: {e}")
            return {}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        instruments = self.get_instruments("NFO")
        if not instruments:
            return []
        price = self.get_price(symbol)
        if not price:
            return []
        from services.option_chain_builder import build_option_chain
        return build_option_chain(self, instruments, price, symbol, expiry)

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/master/{exchange}", timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                instruments = []
                for row in data:
                    if row.get("symbol") in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
                        instruments.append({
                            "token": row.get("token", ""),
                            "symbol": row.get("symbol", ""),
                            "strike": float(row.get("strikePrice", 0)),
                            "type": row.get("optionType", ""),
                            "expiry": row.get("expiryDate", ""),
                            "exchange": exchange,
                        })
                return instruments
            return []
        except Exception as e:
            logger.error(f"AliceBlue get_instruments error: {e}")
            return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
                "exchange": "NFO",
                "order_type": "BUY" if side.upper() == "BUY" else "SELL",
                "instrument_token": symbol,
                "quantity": qty,
                "price_type": order_type.upper(),
                "product": "MIS" if product.upper() in ("INTRADAY", "MIS") else "CNC",
                "price": price,
                "trigger_price": trigger_price,
                "validity": "DAY",
            }
            resp = self.session.post(f"{self.base_url}/placeOrder/executePlaceOrder", json=payload, timeout=15)
            data = resp.json()
            return {
                "status": "success" if data.get("status") == "success" else "error",
                "order_id": data.get("NOrdNo"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"AliceBlue place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/positionAndHoldings/holdings", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/positionAndHoldings/positionBook", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/placeOrder/fetchOrderBook", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def _map_token(self, symbol: str) -> str:
        mapping = {"NIFTY": "26000", "NIFTY 50": "26000", "BANKNIFTY": "26009"}
        return mapping.get(symbol.upper(), symbol)
