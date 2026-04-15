import logging
import requests
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class FyersBroker(BrokerBase):

    BROKER_NAME = "fyers"
    SUPPORTS_DIRECT_CHAIN = True

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.app_id = credentials.get("app_id", credentials.get("client_id", ""))
        self.access_token = credentials.get("access_token", "")
        self.base_url = "https://api-t1.fyers.in/api/v3"
        self.data_url = "https://api-t1.fyers.in/data"
        self.headers = {
            "Authorization": f"{self.app_id}:{self.access_token}",
            "Content-Type": "application/json",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def connect(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/profile", timeout=10)
            if resp.status_code == 200 and resp.json().get("s") == "ok":
                self._connected = True
                return True
            return False
        except Exception as e:
            logger.error(f"Fyers connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            fyers_symbol = self._map_symbol(symbol)
            resp = self.session.get(
                f"{self.data_url}/quotes",
                params={"symbols": fyers_symbol},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("d", [])
                if data:
                    return float(data[0].get("v", {}).get("lp", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Fyers get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            symbols = ",".join(tokens)
            resp = self.session.get(
                f"{self.data_url}/quotes",
                params={"symbols": symbols},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("d", [])
                result = {}
                for item in data:
                    sym = item.get("n", "")
                    v = item.get("v", {})
                    result[sym] = {
                        "ltp": v.get("lp", 0),
                        "oi": v.get("open_interest", 0),
                        "volume": v.get("volume", 0),
                        "bid": v.get("bid", 0),
                        "ask": v.get("ask", 0),
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"Fyers get_quotes error: {e}")
            return {}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        try:
            fyers_symbol = self._map_symbol(symbol)
            params = {"symbol": fyers_symbol, "strikecount": 13}
            if expiry:
                params["timestamp"] = expiry
            resp = self.session.get(
                f"{self.data_url}/optionchain",
                params=params,
                timeout=15,
            )
            if resp.status_code == 200 and resp.json().get("s") == "ok":
                raw = resp.json().get("data", {}).get("optionsChain", [])
                chain = []
                strike_map = {}
                for row in raw:
                    strike = int(row.get("strikePrice", 0))
                    opt_type = row.get("option_type", "")
                    if strike not in strike_map:
                        strike_map[strike] = {"strike": strike}
                    prefix = "call" if opt_type == "CE" else "put"
                    strike_map[strike][f"{prefix}_ltp"] = row.get("ltp", 0)
                    strike_map[strike][f"{prefix}_oi"] = row.get("oi", 0)
                    strike_map[strike][f"{prefix}_iv"] = row.get("iv", 0)
                    strike_map[strike][f"{prefix}_volume"] = row.get("volume", 0)
                    strike_map[strike][f"{prefix}_bid"] = row.get("bid", 0)
                    strike_map[strike][f"{prefix}_ask"] = row.get("ask", 0)
                    strike_map[strike][f"{prefix}_oi_change"] = row.get("oi_change", 0)
                for strike in sorted(strike_map.keys()):
                    chain.append(strike_map[strike])
                return chain
            logger.warning(f"Fyers option chain failed: {resp.status_code}")
            return []
        except Exception as e:
            logger.error(f"Fyers get_option_chain error: {e}")
            return []

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
                "symbol": symbol,
                "qty": qty,
                "type": 2 if order_type.upper() == "MARKET" else 1,
                "side": 1 if side.upper() == "BUY" else -1,
                "productType": "INTRADAY" if product.upper() in ("INTRADAY", "MIS") else "CNC",
                "limitPrice": price,
                "stopPrice": trigger_price,
                "validity": "DAY",
                "offlineOrder": False,
            }
            resp = self.session.post(f"{self.base_url}/orders", json=payload, timeout=15)
            data = resp.json()
            return {
                "status": "success" if data.get("s") == "ok" else "error",
                "order_id": data.get("id"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"Fyers place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/holdings", timeout=10)
            return resp.json().get("holdings", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/positions", timeout=10)
            return resp.json().get("netPositions", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/orders", timeout=10)
            return resp.json().get("orderBook", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def _map_symbol(self, symbol: str) -> str:
        mapping = {
            "NIFTY": "NSE:NIFTY50-INDEX",
            "NIFTY 50": "NSE:NIFTY50-INDEX",
            "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
            "BANK NIFTY": "NSE:NIFTYBANK-INDEX",
            "FINNIFTY": "NSE:FINNIFTY-INDEX",
        }
        return mapping.get(symbol.upper(), symbol)
