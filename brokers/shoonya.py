import logging
import requests
import hashlib
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class ShoonyaBroker(BrokerBase):

    BROKER_NAME = "shoonya"
    SUPPORTS_DIRECT_CHAIN = False

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.user_id = credentials.get("user_id", credentials.get("client_id", ""))
        self.password = credentials.get("password", "")
        self.totp_secret = credentials.get("totp_secret", "")
        self.api_key = credentials.get("api_key", credentials.get("api_secret", ""))
        self.vendor_code = credentials.get("vendor_code", "")
        self.imei = credentials.get("imei", "")
        self.base_url = "https://api.shoonya.com/NorenWClientTP"
        self._token = ""
        self.session = requests.Session()

    def connect(self) -> bool:
        try:
            pwd_hash = hashlib.sha256(self.password.encode()).hexdigest()
            app_key = hashlib.sha256(f"{self.user_id}|{self.api_key}".encode()).hexdigest()

            payload = {
                "source": "API",
                "apkversion": "1.0.0",
                "uid": self.user_id,
                "pwd": pwd_hash,
                "factor2": self._get_totp(),
                "vc": self.vendor_code or self.user_id,
                "appkey": app_key,
                "imei": self.imei or "api",
            }
            resp = self.session.post(f"{self.base_url}/QuickAuth", json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("stat") == "Ok":
                    self._token = data.get("susertoken", "")
                    self._connected = True
                    return True
            return False
        except Exception as e:
            logger.error(f"Shoonya connect error: {e}")
            return False

    def _get_totp(self) -> str:
        if not self.totp_secret:
            return ""
        try:
            import pyotp
            totp = pyotp.TOTP(self.totp_secret)
            return totp.now()
        except ImportError:
            logger.warning("pyotp not available for TOTP generation")
            return self.totp_secret

    def _auth_header(self) -> str:
        return f"{self.user_id}:{self._token}"

    def get_price(self, symbol: str) -> float:
        try:
            payload = {
                "uid": self.user_id,
                "exch": "NSE",
                "token": self._map_token(symbol),
            }
            resp = self.session.post(
                f"{self.base_url}/GetQuotes",
                json={"jData": str(payload), "jKey": self._token},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("lp", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Shoonya get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        result = {}
        for token in tokens:
            try:
                payload = {"uid": self.user_id, "exch": "NFO", "token": token}
                resp = self.session.post(
                    f"{self.base_url}/GetQuotes",
                    json={"jData": str(payload), "jKey": self._token},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
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
            payload = {"uid": self.user_id, "exch": exchange}
            resp = self.session.post(
                f"{self.base_url}/SearchScrip",
                json={"jData": str({"uid": self.user_id, "stext": "NIFTY", "exch": exchange}), "jKey": self._token},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
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
            return []
        except Exception as e:
            logger.error(f"Shoonya get_instruments error: {e}")
            return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
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
            }
            resp = self.session.post(
                f"{self.base_url}/PlaceOrder",
                json={"jData": str(payload), "jKey": self._token},
                timeout=15,
            )
            data = resp.json()
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
            resp = self.session.post(
                f"{self.base_url}/Holdings",
                json={"jData": str({"uid": self.user_id, "actid": self.user_id, "prd": "C"}), "jKey": self._token},
                timeout=10,
            )
            return resp.json() if resp.status_code == 200 and isinstance(resp.json(), list) else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.post(
                f"{self.base_url}/PositionBook",
                json={"jData": str({"uid": self.user_id, "actid": self.user_id}), "jKey": self._token},
                timeout=10,
            )
            return resp.json() if resp.status_code == 200 and isinstance(resp.json(), list) else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.post(
                f"{self.base_url}/OrderBook",
                json={"jData": str({"uid": self.user_id}), "jKey": self._token},
                timeout=10,
            )
            return resp.json() if resp.status_code == 200 and isinstance(resp.json(), list) else []
        except Exception:
            return []

    def _map_token(self, symbol: str) -> str:
        mapping = {"NIFTY": "26000", "NIFTY 50": "26000", "BANKNIFTY": "26009"}
        return mapping.get(symbol.upper(), symbol)
