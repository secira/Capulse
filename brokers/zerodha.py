import logging
import requests
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class ZerodhaBroker(BrokerBase):

    BROKER_NAME = "zerodha"
    SUPPORTS_DIRECT_CHAIN = False

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.api_key = credentials.get("api_key", credentials.get("client_id", ""))
        self.access_token = credentials.get("access_token", "")
        self.base_url = "https://api.kite.trade"
        self.headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token}",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def connect(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/user/profile", timeout=10)
            if resp.status_code == 200:
                self._connected = True
                return True
            return False
        except Exception as e:
            logger.error(f"Zerodha connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            exchange = "NSE"
            trading_symbol = self._map_symbol(symbol)
            resp = self.session.get(
                f"{self.base_url}/quote/ltp",
                params={"i": f"{exchange}:{trading_symbol}"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                key = f"{exchange}:{trading_symbol}"
                return float(data.get(key, {}).get("last_price", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Zerodha get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            instruments = [f"NFO:{t}" for t in tokens]
            resp = self.session.get(
                f"{self.base_url}/quote",
                params={"i": instruments},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                result = {}
                for key, val in data.items():
                    token_name = key.replace("NFO:", "")
                    result[token_name] = {
                        "ltp": val.get("last_price", 0),
                        "oi": val.get("oi", 0),
                        "volume": val.get("volume", 0),
                        "bid": val.get("depth", {}).get("buy", [{}])[0].get("price", 0),
                        "ask": val.get("depth", {}).get("sell", [{}])[0].get("price", 0),
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"Zerodha get_quotes error: {e}")
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
            resp = self.session.get(
                f"{self.base_url}/instruments/{exchange}",
                timeout=30,
            )
            if resp.status_code == 200:
                import csv
                import io
                reader = csv.DictReader(io.StringIO(resp.text))
                instruments = []
                for row in reader:
                    if row.get("name") in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
                        instruments.append({
                            "token": row.get("tradingsymbol", ""),
                            "symbol": row.get("name", ""),
                            "strike": float(row.get("strike", 0)),
                            "type": "CE" if "CE" in row.get("instrument_type", "") else "PE",
                            "expiry": row.get("expiry", ""),
                            "exchange": exchange,
                            "instrument_type": row.get("instrument_type", ""),
                        })
                return instruments
            return []
        except Exception as e:
            logger.error(f"Zerodha get_instruments error: {e}")
            return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
                "tradingsymbol": symbol,
                "exchange": "NFO",
                "transaction_type": side.upper(),
                "order_type": order_type.upper(),
                "quantity": qty,
                "product": "MIS" if product.upper() in ("INTRADAY", "MIS") else "CNC",
                "validity": "DAY",
                "price": price,
                "trigger_price": trigger_price,
            }
            resp = self.session.post(f"{self.base_url}/orders/regular", data=payload, timeout=15)
            data = resp.json()
            return {
                "status": data.get("status", "unknown"),
                "order_id": data.get("data", {}).get("order_id"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"Zerodha place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/portfolio/holdings", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/portfolio/positions", timeout=10)
            data = resp.json().get("data", {})
            return data.get("net", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/orders", timeout=10)
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def _map_symbol(self, symbol: str) -> str:
        mapping = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}
        return mapping.get(symbol.upper(), symbol)
