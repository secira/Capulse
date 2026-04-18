import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class AngelBroker(BrokerBase):

    BROKER_NAME = "angel"
    SUPPORTS_DIRECT_CHAIN = True

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.client_code = credentials.get("client_id", "")
        self.access_token = credentials.get("access_token", "")

        api_secret_raw = credentials.get("api_secret", "")
        parts = api_secret_raw.split(":")
        self.api_key = parts[0] if parts and parts[0] else self.client_code
        self.totp_secret = parts[1] if len(parts) > 1 else ""
        self.refresh_token = parts[2] if len(parts) > 2 else ""

        self.base_url = "https://apiconnect.angelone.in"
        self._build_session()

    def _build_session(self):
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.api_key,
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _refresh_token(self) -> bool:
        """Auto-refresh session using stored TOTP secret if available."""
        if not self.totp_secret or not self.client_code or not self.api_key:
            return False
        try:
            import pyotp
            from SmartApi import SmartConnect
            totp = pyotp.TOTP(self.totp_secret).now()
            smart = SmartConnect(api_key=self.api_key)
            stored_password = self.refresh_token or ""
            if not stored_password:
                logger.warning("Angel: no password stored, cannot auto-refresh token")
                return False
            data = smart.generateSession(self.client_code, stored_password, totp)
            if not data or data.get("status") is False:
                return False
            new_token = data["data"]["jwtToken"]
            self.access_token = new_token
            self.refresh_token = data["data"].get("refreshToken", self.refresh_token)
            self._build_session()
            logger.info(f"Angel One token auto-refreshed for {self.client_code}")
            return True
        except Exception as e:
            logger.error(f"Angel token refresh failed: {e}")
            return False

    def connect(self) -> bool:
        try:
            resp = self.session.get(
                f"{self.base_url}/rest/secure/angelbroking/user/v1/getProfile",
                timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("status"):
                self._connected = True
                return True
            if resp.status_code in (401, 403):
                logger.info("Angel: token expired, attempting auto-refresh")
                if self._refresh_token():
                    resp2 = self.session.get(
                        f"{self.base_url}/rest/secure/angelbroking/user/v1/getProfile",
                        timeout=10,
                    )
                    if resp2.status_code == 200 and resp2.json().get("status"):
                        self._connected = True
                        return True
            return False
        except Exception as e:
            logger.error(f"Angel connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            token, exchange = self._map_token(symbol)
            payload = {
                "mode": "LTP",
                "exchangeTokens": {exchange: [token]},
            }
            resp = self.session.post(
                f"{self.base_url}/rest/secure/angelbroking/market/v1/quote/",
                json=payload, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("fetched", [])
                if data:
                    return float(data[0].get("ltp", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Angel get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            payload = {
                "mode": "FULL",
                "exchangeTokens": {"NFO": tokens},
            }
            resp = self.session.post(
                f"{self.base_url}/rest/secure/angelbroking/market/v1/quote/",
                json=payload, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("fetched", [])
                result = {}
                for item in data:
                    token_id = item.get("symbolToken", "")
                    result[token_id] = {
                        "ltp": float(item.get("ltp", 0)),
                        "oi": int(item.get("opnInterest", 0)),
                        "volume": int(item.get("tradeVolume", 0)),
                        "bid": float(item.get("bestBidPrice", 0)),
                        "ask": float(item.get("bestAskPrice", 0)),
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"Angel get_quotes error: {e}")
            return {}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        try:
            payload = {"name": symbol.upper()}
            if expiry:
                payload["expirydate"] = expiry
            resp = self.session.post(
                f"{self.base_url}/rest/secure/angelbroking/market/v1/optionGreek",
                json=payload, timeout=15,
            )
            if resp.status_code == 200:
                raw = resp.json().get("data", [])
                strike_map = {}
                for row in raw:
                    strike = int(float(row.get("strikePrice", 0)))
                    opt_type = row.get("optionType", "")
                    if strike not in strike_map:
                        strike_map[strike] = {"strike": strike}
                    prefix = "call" if opt_type == "CE" else "put"
                    strike_map[strike][f"{prefix}_ltp"] = float(row.get("ltp", 0))
                    strike_map[strike][f"{prefix}_oi"] = int(row.get("opnInterest", 0))
                    strike_map[strike][f"{prefix}_iv"] = float(row.get("impliedVolatility", 0))
                    strike_map[strike][f"{prefix}_volume"] = int(row.get("tradeVolume", 0))
                return [strike_map[s] for s in sorted(strike_map)]
            return []
        except Exception as e:
            logger.error(f"Angel get_option_chain error: {e}")
            return []

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": "",
                "transactiontype": side.upper(),
                "exchange": "NFO",
                "ordertype": order_type.upper(),
                "producttype": "INTRADAY" if product.upper() in ("INTRADAY", "MIS") else "DELIVERY",
                "duration": "DAY",
                "price": str(price),
                "triggerprice": str(trigger_price),
                "quantity": str(qty),
            }
            resp = self.session.post(
                f"{self.base_url}/rest/secure/angelbroking/order/v1/placeOrder",
                json=payload, timeout=15,
            )
            data = resp.json()
            return {
                "status": "success" if data.get("status") else "error",
                "order_id": data.get("data", {}).get("orderid"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"Angel place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            resp = self.session.get(
                f"{self.base_url}/rest/secure/angelbroking/portfolio/v1/getHolding",
                timeout=10,
            )
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self.session.get(
                f"{self.base_url}/rest/secure/angelbroking/order/v1/getPosition",
                timeout=10,
            )
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self.session.get(
                f"{self.base_url}/rest/secure/angelbroking/order/v1/getOrderBook",
                timeout=10,
            )
            return resp.json().get("data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def _map_token(self, symbol: str):
        """Return (token, exchange) for LTP lookup."""
        mapping = {
            "NIFTY":      ("99926000", "NSE"),
            "NIFTY 50":   ("99926000", "NSE"),
            "BANKNIFTY":  ("99926009", "NSE"),
            "NIFTY BANK": ("99926009", "NSE"),
            "FINNIFTY":   ("99926037", "NSE"),
            "SENSEX":     ("1",        "BSE"),
            "BSE SENSEX": ("1",        "BSE"),
        }
        return mapping.get(symbol.upper(), (symbol, "NSE"))
