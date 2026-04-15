from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional


class BrokerBase(ABC):

    BROKER_NAME = "base"
    SUPPORTS_DIRECT_CHAIN = False

    def __init__(self, credentials: Dict[str, str]):
        self.credentials = credentials
        self._connected = False

    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        pass

    @abstractmethod
    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        pass

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        pass

    @abstractmethod
    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        pass

    @abstractmethod
    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        pass

    @abstractmethod
    def get_holdings(self) -> List[Dict]:
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict]:
        pass

    @abstractmethod
    def get_orders(self) -> List[Dict]:
        pass

    def is_connected(self) -> bool:
        return self._connected
