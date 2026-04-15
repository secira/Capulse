import logging
import requests
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)


class FivePaisaBroker(BrokerBase):

    BROKER_NAME = "5paisa"
    SUPPORTS_DIRECT_CHAIN = True

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.app_name = credentials.get("app_name", credentials.get("client_id", ""))
        self.app_source = credentials.get("app_source", "")
        self.user_key = credentials.get("user_key", credentials.get("api_secret", ""))
        self.encryption_key = credentials.get("encryption_key", "")
        self.access_token = credentials.get("access_token", "")
        self.client_code = credentials.get("client_code", "")
        self.base_url = "https://Openapi.5paisa.com"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"bearer {self.access_token}",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def connect(self) -> bool:
        try:
            payload = {"head": {"key": self.user_key}, "body": {"ClientCode": self.client_code}}
            resp = self.session.post(f"{self.base_url}/VendorsAPI/Service1.svc/V4/Margin", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("body", {}).get("EquityMargin"):
                    self._connected = True
                    return True
            return False
        except Exception as e:
            logger.error(f"5Paisa connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        try:
            scrip_data = self._get_scrip_data(symbol)
            if not scrip_data:
                return 0.0
            payload = {
                "head": {"key": self.user_key},
                "body": {"Count": 1, "MarketFeedData": [scrip_data]},
            }
            resp = self.session.post(f"{self.base_url}/VendorsAPI/Service1.svc/V1/MarketFeed", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("body", {}).get("Data", [])
                if data:
                    return float(data[0].get("LastRate", 0))
            return 0.0
        except Exception as e:
            logger.error(f"5Paisa get_price error: {e}")
            return 0.0

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        try:
            if not tokens:
                return {}
            feed_data = [{"Exch": "N", "ExchType": "D", "ScripCode": int(t)} for t in tokens]
            payload = {
                "head": {"key": self.user_key},
                "body": {"Count": len(feed_data), "MarketFeedData": feed_data},
            }
            resp = self.session.post(f"{self.base_url}/VendorsAPI/Service1.svc/V1/MarketFeed", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("body", {}).get("Data", [])
                result = {}
                for item in data:
                    sc = str(item.get("Token", ""))
                    result[sc] = {
                        "ltp": float(item.get("LastRate", 0)),
                        "oi": int(item.get("OpenInterest", 0)),
                        "volume": int(item.get("TotalQty", 0)),
                        "bid": float(item.get("BuyPrice", 0)),
                        "ask": float(item.get("SellPrice", 0)),
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"5Paisa get_quotes error: {e}")
            return {}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        try:
            payload = {
                "head": {"key": self.user_key},
                "body": {"Symbol": symbol, "Expiry": expiry or ""},
            }
            resp = self.session.post(
                f"{self.base_url}/VendorsAPI/Service1.svc/V1/OptionChain",
                json=payload, timeout=15,
            )
            if resp.status_code == 200:
                raw = resp.json().get("body", {}).get("Data", [])
                chain = []
                for row in raw:
                    chain.append({
                        "strike": int(row.get("StrikeRate", 0)),
                        "call_ltp": row.get("CE_LTP", 0),
                        "put_ltp": row.get("PE_LTP", 0),
                        "call_oi": row.get("CE_OI", 0),
                        "put_oi": row.get("PE_OI", 0),
                        "call_iv": row.get("CE_IV", 0),
                        "put_iv": row.get("PE_IV", 0),
                        "call_volume": row.get("CE_Volume", 0),
                        "put_volume": row.get("PE_Volume", 0),
                    })
                return chain
            return []
        except Exception as e:
            logger.error(f"5Paisa get_option_chain error: {e}")
            return []

    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            payload = {
                "head": {"key": self.user_key},
                "body": {
                    "ClientCode": self.client_code,
                    "OrderFor": "P",
                    "Exchange": "N",
                    "ExchangeType": "D",
                    "ScripCode": 0,
                    "ScripData": symbol,
                    "Price": price,
                    "Qty": qty,
                    "StopLossPrice": trigger_price,
                    "IsIntraday": product.upper() in ("INTRADAY", "MIS"),
                    "BuySell": "B" if side.upper() == "BUY" else "S",
                    "OrderType": order_type.upper(),
                },
            }
            resp = self.session.post(
                f"{self.base_url}/VendorsAPI/Service1.svc/V1/OrderRequest",
                json=payload, timeout=15,
            )
            data = resp.json()
            return {
                "status": "success" if data.get("body", {}).get("Status", 0) == 0 else "error",
                "order_id": data.get("body", {}).get("BrokerOrderID"),
                "raw": data,
            }
        except Exception as e:
            logger.error(f"5Paisa place_order error: {e}")
            return {"status": "error", "message": str(e)}

    def get_holdings(self) -> List[Dict]:
        try:
            payload = {"head": {"key": self.user_key}, "body": {"ClientCode": self.client_code}}
            resp = self.session.post(f"{self.base_url}/VendorsAPI/Service1.svc/V3/Holding", json=payload, timeout=10)
            return resp.json().get("body", {}).get("Data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_positions(self) -> List[Dict]:
        try:
            payload = {"head": {"key": self.user_key}, "body": {"ClientCode": self.client_code}}
            resp = self.session.post(f"{self.base_url}/VendorsAPI/Service1.svc/V1/NetPositionNetWise", json=payload, timeout=10)
            return resp.json().get("body", {}).get("Data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def get_orders(self) -> List[Dict]:
        try:
            payload = {"head": {"key": self.user_key}, "body": {"ClientCode": self.client_code}}
            resp = self.session.post(f"{self.base_url}/VendorsAPI/Service1.svc/V2/OrderBook", json=payload, timeout=10)
            return resp.json().get("body", {}).get("Data", []) if resp.status_code == 200 else []
        except Exception:
            return []

    def _get_scrip_data(self, symbol: str) -> Optional[Dict]:
        mapping = {
            "NIFTY": {"Exch": "N", "ExchType": "C", "ScripCode": 999920000},
            "BANKNIFTY": {"Exch": "N", "ExchType": "C", "ScripCode": 999920005},
        }
        return mapping.get(symbol.upper())
