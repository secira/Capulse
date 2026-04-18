import hashlib
import json
import logging
import requests
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)

_BASE = "https://api.shoonya.com/NorenWClientTP"


def _jpost(session: requests.Session, endpoint: str, jdata: dict, token: str) -> dict:
    """Send a Noren API form-encoded request and return parsed JSON."""
    jdata_str = json.dumps(jdata)
    resp = session.post(
        f"{_BASE}/{endpoint}",
        data=f"jData={jdata_str}&jKey={token}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


class ShoonyaBroker(BrokerBase):

    BROKER_NAME = "shoonya"
    SUPPORTS_DIRECT_CHAIN = False

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.user_id = credentials.get("client_id", credentials.get("user_id", ""))
        self.session = requests.Session()

        raw_secret = credentials.get("api_secret", "")
        parts = raw_secret.split(":") if raw_secret else []
        self.api_secret = parts[0] if parts else ""
        self.vendor_code = parts[1] if len(parts) > 1 else ""
        self.totp_secret = parts[2] if len(parts) > 2 else ""
        self.password = parts[3] if len(parts) > 3 else ""

        self._token = credentials.get("access_token", "")

    def connect(self) -> bool:
        if self._token:
            try:
                data = _jpost(self.session, "UserDetails", {"uid": self.user_id}, self._token)
                if data.get("stat") == "Ok":
                    self._connected = True
                    return True
            except Exception:
                pass
        if self.password and self.api_secret:
            try:
                from routes_broker_oauth import _shoonya_quickauth
                self._token = _shoonya_quickauth(
                    self.user_id, self.password, self.api_secret,
                    self.vendor_code, self.totp_secret,
                )
                self._connected = True
                return True
            except Exception as e:
                logger.error(f"Shoonya connect via QuickAuth failed: {e}")
        return False

    def get_price(self, symbol: str) -> float:
        try:
            data = _jpost(
                self.session, "GetQuotes",
                {"uid": self.user_id, "exch": "NSE", "token": self._map_token(symbol)},
                self._token,
            )
            return float(data.get("lp", 0))
        except Exception as e:
            logger.error(f"Shoonya get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        result = {}
        for token in tokens:
            try:
                data = _jpost(
                    self.session, "GetQuotes",
                    {"uid": self.user_id, "exch": "NFO", "token": token},
                    self._token,
                )
                result[token] = {
                    "ltp": float(data.get("lp", 0)),
                    "oi": int(data.get("oi", 0)),
                    "volume": int(data.get("v", 0)),
                    "bid": float(data.get("bp1", 0)),
                    "ask": float(data.get("sp1", 0)),
                }
            except Exception:
                continue
        return result

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
            data = _jpost(
                self.session, "SearchScrip",
                {"uid": self.user_id, "stext": "NIFTY", "exch": exchange},
                self._token,
            )
            values = data.get("values", []) if isinstance(data, dict) else data
            instruments = []
            for row in values:
                if row.get("instname") in ("OPTIDX",):
                    instruments.append({
                        "token": row.get("token", ""),
                        "symbol": row.get("tsym", ""),
                        "strike": float(row.get("strprc", 0)),
                        "type": "CE" if "CE" in row.get("tsym", "") else "PE",
                        "expiry": row.get("exd", ""),
                        "exchange": exchange,
                    })
            return instruments
        except Exception as e:
            logger.error(f"Shoonya get_instruments error: {e}")
            return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            data = _jpost(self.session, "PlaceOrder", {
                "uid": self.user_id,
                "actid": self.user_id,
                "exch": "NFO",
                "tsym": symbol,
                "qty": str(qty),
                "prc": str(price),
                "trgprc": str(trigger_price),
                "prd": "I" if product.upper() in ("INTRADAY", "MIS") else "C",
                "trantype": "B" if side.upper() == "BUY" else "S",
                "prctyp": "MKT" if order_type.upper() == "MARKET" else "LMT",
                "ret": "DAY",
            }, self._token)
            return {
                "status": "success" if data.get("stat") == "Ok" else "error",
                "order_id": data.get("norenordno"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"Shoonya place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            data = _jpost(self.session, "Holdings",
                          {"uid": self.user_id, "actid": self.user_id, "prd": "C"}, self._token)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            data = _jpost(self.session, "PositionBook",
                          {"uid": self.user_id, "actid": self.user_id}, self._token)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            data = _jpost(self.session, "OrderBook", {"uid": self.user_id}, self._token)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _map_token(self, symbol: str) -> str:
        mapping = {
            "NIFTY": "26000",
            "NIFTY 50": "26000",
            "BANKNIFTY": "26009",
            "FINNIFTY": "26037",
            "SENSEX": "1",
        }
        return mapping.get(symbol.upper(), symbol)
