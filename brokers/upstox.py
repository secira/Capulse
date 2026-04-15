import logging
import requests
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class UpstoxBroker(BrokerBase):

    BROKER_NAME = "upstox"
    SUPPORTS_DIRECT_CHAIN = True

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.access_token = credentials.get("access_token", "")
        self.base_url = "https://api.upstox.com/v2"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def connect(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/user/profile", timeout=10)
            if resp.status_code == 200 and resp.json().get("status") == "success":
                self._connected = True
                return True
            return False
        except Exception as e:
            logger.error(f"Upstox connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            instrument_key = self._map_symbol(symbol)
            resp = self.session.get(
                f"{self.base_url}/market-quote/ltp",
                params={"instrument_key": instrument_key},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                for key, val in data.items():
                    return float(val.get("last_price", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Upstox get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            keys = ",".join(tokens)
            resp = self.session.get(
                f"{self.base_url}/market-quote/quotes",
                params={"instrument_key": keys},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                result = {}
                for key, val in data.items():
                    result[key] = {
                        "ltp": val.get("last_price", 0),
                        "oi": val.get("oi", 0),
                        "volume": val.get("volume", 0),
                        "bid": val.get("depth", {}).get("buy", [{}])[0].get("price", 0) if val.get("depth") else 0,
                        "ask": val.get("depth", {}).get("sell", [{}])[0].get("price", 0) if val.get("depth") else 0,
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"Upstox get_quotes error: {e}")
            return {}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        try:
            instrument_key = self._map_symbol(symbol)
            params = {"instrument_key": instrument_key}
            if expiry:
                params["expiry_date"] = expiry
            resp = self.session.get(
                f"{self.base_url}/option/chain",
                params=params,
                timeout=15,
            )
            if resp.status_code == 200:
                raw = resp.json().get("data", [])
                chain = []
                for row in raw:
                    strike = int(row.get("strike_price", 0))
                    ce = row.get("call_options", {}).get("market_data", {})
                    pe = row.get("put_options", {}).get("market_data", {})
                    chain.append({
                        "strike": strike,
                        "call_ltp": ce.get("ltp", 0),
                        "put_ltp": pe.get("ltp", 0),
                        "call_oi": ce.get("oi", 0),
                        "put_oi": pe.get("oi", 0),
                        "call_iv": row.get("call_options", {}).get("option_greeks", {}).get("iv", 0),
                        "put_iv": row.get("put_options", {}).get("option_greeks", {}).get("iv", 0),
                        "call_volume": ce.get("volume", 0),
                        "put_volume": pe.get("volume", 0),
                    })
                return chain
            return []
        except Exception as e:
            logger.error(f"Upstox get_option_chain error: {e}")
            return []

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
                "instrument_token": symbol,
                "quantity": qty,
                "transaction_type": side.upper(),
                "order_type": order_type.upper() if order_type.upper() != "MARKET" else "MARKET",
                "product": "I" if product.upper() in ("INTRADAY", "MIS") else "D",
                "validity": "DAY",
                "price": price,
                "trigger_price": trigger_price,
                "is_amo": False,
            }
            resp = self.session.post(f"{self.base_url}/order/place", json=payload, timeout=15)
            data = resp.json()
            return {
                "status": data.get("status", "unknown"),
                "order_id": data.get("data", {}).get("order_id"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"Upstox place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/portfolio/long-term-holdings", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/portfolio/short-term-positions", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/order/retrieve-all", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def _map_symbol(self, symbol: str) -> str:
        mapping = {
            "NIFTY": "NSE_INDEX|Nifty 50",
            "NIFTY 50": "NSE_INDEX|Nifty 50",
            "BANKNIFTY": "NSE_INDEX|Nifty Bank",
            "BANK NIFTY": "NSE_INDEX|Nifty Bank",
        }
        return mapping.get(symbol.upper(), symbol)
