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

    def refresh_jwt(self, stored_refresh_token: str) -> Optional[Dict[str, str]]:
        """T007 — Invisible JWT refresh using Angel's refreshToken.

        Calls POST /rest/auth/angelbroking/jwt/v1/generateTokens with the
        previously-issued refresh token. On 200, returns dict with the new
        jwtToken/refreshToken/feedToken so the caller can persist them and
        keep the account 'connected' without forcing a TOTP re-login.

        Returns None on any failure (auth, network, 5xx). Caller should then
        fall back to the existing _refresh_token() (full TOTP) or flip the
        account status to EXPIRED.
        """
        if not stored_refresh_token or not self.api_key:
            return None
        try:
            r = requests.post(
                f"{self.base_url}/rest/auth/angelbroking/jwt/v1/generateTokens",
                json={"refreshToken": stored_refresh_token},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-UserType": "USER", "X-SourceID": "WEB",
                    "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": "127.0.0.1",
                    "X-MACAddress": "00:00:00:00:00:00",
                    "X-PrivateKey": self.api_key,
                },
                timeout=10,
            )
            if r.status_code != 200:
                logger.warning(f"Angel refresh_jwt HTTP {r.status_code}: {r.text[:160]}")
                return None
            body = r.json()
            if not body.get("status") or "data" not in body:
                logger.warning(f"Angel refresh_jwt rejected: {body.get('message','?')}")
                return None
            data = body["data"]
            new_jwt = data.get("jwtToken")
            if not new_jwt:
                return None
            # Update in-memory state so subsequent calls in this process use it.
            self.access_token = new_jwt
            self.refresh_token = data.get("refreshToken") or stored_refresh_token
            self._build_session()
            return {
                "jwt": new_jwt,
                "refresh_token": self.refresh_token,
                "feed_token": data.get("feedToken") or "",
            }
        except Exception as e:
            logger.error(f"Angel refresh_jwt error: {e}")
            return None

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

    # Module-level cache for ScripMaster (shared across instances). ~25 MB
    # JSON, but only the rows for the requested exchange are returned.
    _SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPI_File.json"
    _scrip_master_cache: Optional[List[Dict]] = None
    _scrip_master_loaded_at: Optional[datetime] = None
    _SCRIP_MASTER_TTL = timedelta(hours=12)

    @classmethod
    def _load_scrip_master(cls) -> List[Dict]:
        """Fetch + cache Angel's master contract file. Refreshed every 12h."""
        now = datetime.utcnow()
        if (
            cls._scrip_master_cache is not None
            and cls._scrip_master_loaded_at is not None
            and now - cls._scrip_master_loaded_at < cls._SCRIP_MASTER_TTL
        ):
            return cls._scrip_master_cache
        try:
            resp = requests.get(cls._SCRIP_MASTER_URL, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            cls._scrip_master_cache = data if isinstance(data, list) else []
            cls._scrip_master_loaded_at = now
            logger.info(f"Angel ScripMaster loaded: {len(cls._scrip_master_cache)} instruments")
            return cls._scrip_master_cache
        except Exception as e:
            logger.error(f"Angel ScripMaster fetch failed: {e}")
            return cls._scrip_master_cache or []

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        """Return Angel master contracts filtered by exchange.

        Angel's ScripMaster row schema (normalised here):
          token, symbol, name, expiry, strike, lotsize, instrumenttype, exch_seg
        """
        rows = self._load_scrip_master()
        exch = (exchange or "NFO").upper()
        out = []
        for r in rows:
            if (r.get("exch_seg") or "").upper() != exch:
                continue
            out.append({
                "token": r.get("token", ""),
                "tradingsymbol": r.get("symbol", ""),
                "name": r.get("name", ""),
                "expiry": r.get("expiry", ""),
                "strike": float(r.get("strike", 0) or 0) / 100.0,  # paise → rupees
                "lot_size": int(r.get("lotsize", 0) or 0),
                "instrument_type": r.get("instrumenttype", ""),
                "exchange": r.get("exch_seg", exch),
            })
        return out

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
