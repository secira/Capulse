"""
Data Connector Layer for Capulse AI Workflow Engine

Abstract connector interface with pluggable backends:
- B2C Connector: User's connected broker accounts (Dhan, Zerodha, Angel, etc.)
- B2B Connector: Partner broker backend API for white-label deployments
- Connector Registry: Central registry for discovering and managing connectors
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class PortfolioData:
    """Standardized portfolio data returned by all connectors"""

    def __init__(self, holdings: List[Dict] = None, positions: List[Dict] = None,
                 total_value: float = 0.0, total_pnl: float = 0.0,
                 cash_balance: float = 0.0, metadata: Dict = None):
        self.holdings = holdings or []
        self.positions = positions or []
        self.total_value = total_value
        self.total_pnl = total_pnl
        self.cash_balance = cash_balance
        self.metadata = metadata or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict:
        return {
            'holdings': self.holdings,
            'positions': self.positions,
            'total_value': self.total_value,
            'total_pnl': self.total_pnl,
            'cash_balance': self.cash_balance,
            'metadata': self.metadata,
            'timestamp': self.timestamp.isoformat(),
        }


class MarketData:
    """Standardized market data returned by all connectors"""

    def __init__(self, symbol: str, current_price: float = 0.0,
                 previous_close: float = 0.0, change_pct: float = 0.0,
                 day_high: float = 0.0, day_low: float = 0.0,
                 volume: int = 0, additional: Dict = None):
        self.symbol = symbol
        self.current_price = current_price
        self.previous_close = previous_close
        self.change_pct = change_pct
        self.day_high = day_high
        self.day_low = day_low
        self.volume = volume
        self.additional = additional or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'current_price': self.current_price,
            'previous_close': self.previous_close,
            'change_pct': self.change_pct,
            'day_high': self.day_high,
            'day_low': self.day_low,
            'volume': self.volume,
            'additional': self.additional,
            'timestamp': self.timestamp.isoformat(),
        }


class BaseDataConnector(ABC):
    """Abstract base class for all data connectors"""

    connector_type: str = "base"
    connector_name: str = "Base Connector"

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection and clean up resources."""
        pass

    @abstractmethod
    def get_portfolio(self, user_id: int) -> PortfolioData:
        """Retrieve portfolio data for a given user."""
        pass

    @abstractmethod
    def get_market_data(self, symbol: str) -> MarketData:
        """Retrieve market data for a given symbol."""
        pass

    @abstractmethod
    def get_holdings(self, user_id: int) -> List[Dict]:
        """Retrieve holdings for a user."""
        pass

    @abstractmethod
    def get_positions(self, user_id: int) -> List[Dict]:
        """Retrieve open positions for a user."""
        pass

    def health_check(self) -> Dict:
        """Check connector health and return status."""
        return {
            'connector_type': self.connector_type,
            'connector_name': self.connector_name,
            'connected': self._connected,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }


def _fetch_market_quote(symbol: str) -> MarketData:
    try:
        from services.market_data_service import MarketDataService
        mds = MarketDataService()
        quote = mds.get_stock_quote(symbol, exchange='NSE')
        if quote:
            return MarketData(
                symbol=symbol,
                current_price=quote.get('current_price', 0),
                previous_close=quote.get('previous_close', 0),
                change_pct=quote.get('change_percent', 0),
                day_high=quote.get('day_high', 0),
                day_low=quote.get('day_low', 0),
                volume=quote.get('volume', 0),
                additional=quote,
            )
    except Exception as e:
        logger.error(f"Market data fetch error for {symbol}: {e}")
    return MarketData(symbol=symbol)


class B2CConnector(BaseDataConnector):
    """
    B2C Connector - Connects to user's personal broker accounts.

    Uses the existing BrokerService to fetch holdings, positions, and market
    data from brokers like Dhan, Zerodha, Angel Broking, etc.
    """

    connector_type = "b2c"
    connector_name = "B2C User Broker Connector"

    def __init__(self, config: Dict = None):
        super().__init__(config)
        self._broker_service = None

    def _get_broker_service(self):
        if self._broker_service is None:
            from services.broker_service import BrokerService
            self._broker_service = BrokerService()
        return self._broker_service

    def connect(self) -> bool:
        try:
            self._get_broker_service()
            self._connected = True
            logger.info("B2C connector initialized successfully")
            return True
        except Exception as e:
            logger.error(f"B2C connector initialization failed: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        self._broker_service = None
        self._connected = False

    def get_portfolio(self, user_id: int) -> PortfolioData:
        holdings = self.get_holdings(user_id)
        positions = self.get_positions(user_id)

        total_value = sum(h.get('current_value', 0) for h in holdings)
        total_pnl = sum(h.get('pnl', 0) for h in holdings)

        broker_accounts = self._get_user_broker_accounts(user_id)
        cash_balance = sum(ba.get('balance', 0) for ba in broker_accounts)

        return PortfolioData(
            holdings=holdings,
            positions=positions,
            total_value=total_value,
            total_pnl=total_pnl,
            cash_balance=cash_balance,
            metadata={
                'source': 'b2c_broker',
                'broker_count': len(broker_accounts),
                'brokers': [ba.get('broker_name', '') for ba in broker_accounts],
            },
        )

    def get_market_data(self, symbol: str) -> MarketData:
        return _fetch_market_quote(symbol)

    def get_holdings(self, user_id: int) -> List[Dict]:
        try:
            from models_broker import BrokerAccount, BrokerHolding
            from app import db

            accounts = BrokerAccount.query.filter_by(
                user_id=user_id, is_active=True
            ).all()

            all_holdings = []
            for account in accounts:
                holdings = BrokerHolding.query.filter_by(
                    broker_account_id=account.id
                ).all()
                for h in holdings:
                    all_holdings.append({
                        'symbol': h.symbol,
                        'quantity': h.quantity,
                        'average_price': h.average_price,
                        'current_price': h.current_price or 0,
                        'current_value': (h.current_price or 0) * h.quantity,
                        'pnl': ((h.current_price or 0) - h.average_price) * h.quantity,
                        'broker': account.broker_name,
                        'broker_account_id': account.id,
                    })
            return all_holdings
        except Exception as e:
            logger.error(f"Error fetching B2C holdings for user {user_id}: {e}")
            return []

    def get_positions(self, user_id: int) -> List[Dict]:
        try:
            from models_broker import BrokerAccount, BrokerPosition
            from app import db

            accounts = BrokerAccount.query.filter_by(
                user_id=user_id, is_active=True
            ).all()

            all_positions = []
            for account in accounts:
                positions = BrokerPosition.query.filter_by(
                    broker_account_id=account.id
                ).all()
                for p in positions:
                    all_positions.append({
                        'symbol': p.symbol,
                        'quantity': p.quantity,
                        'entry_price': p.entry_price,
                        'current_price': p.current_price or 0,
                        'pnl': p.pnl or 0,
                        'product_type': p.product_type,
                        'broker': account.broker_name,
                        'broker_account_id': account.id,
                    })
            return all_positions
        except Exception as e:
            logger.error(f"Error fetching B2C positions for user {user_id}: {e}")
            return []

    def _get_user_broker_accounts(self, user_id: int) -> List[Dict]:
        try:
            from models_broker import BrokerAccount
            accounts = BrokerAccount.query.filter_by(
                user_id=user_id, is_active=True
            ).all()
            return [
                {
                    'id': a.id,
                    'broker_name': a.broker_name,
                    'broker_type': a.broker_type,
                    'balance': a.account_balance or 0,
                    'status': a.connection_status,
                }
                for a in accounts
            ]
        except Exception as e:
            logger.error(f"Error fetching broker accounts for user {user_id}: {e}")
            return []


class B2BConnector(BaseDataConnector):
    """
    B2B Connector - Connects to partner broker backend APIs.

    Used for white-label deployments where a partner broker provides
    a backend API for portfolio and market data access.
    """

    connector_type = "b2b"
    connector_name = "B2B Partner Broker Connector"

    def __init__(self, config: Dict = None):
        super().__init__(config)
        self.api_base_url = self.config.get('api_base_url', '')
        self.api_key = self.config.get('api_key', '')
        self.partner_id = self.config.get('partner_id', '')
        self.tenant_id = self.config.get('tenant_id', 'live')
        self._session = None

    def _get_session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                'Authorization': f'Bearer {self.api_key}',
                'X-Partner-ID': self.partner_id,
                'X-Tenant-ID': self.tenant_id,
                'Content-Type': 'application/json',
            })
        return self._session

    def connect(self) -> bool:
        if not self.api_base_url:
            logger.error("B2B connector requires api_base_url in config")
            self._connected = False
            return False

        try:
            session = self._get_session()
            resp = session.get(f'{self.api_base_url}/health', timeout=10)
            if resp.status_code == 200:
                self._connected = True
                logger.info(f"B2B connector connected to {self.api_base_url}")
                return True

            logger.warning(
                f"B2B health check returned status {resp.status_code}"
            )
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"B2B connector failed to connect: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._session:
            self._session.close()
            self._session = None
        self._connected = False

    def get_portfolio(self, user_id: int) -> PortfolioData:
        holdings = self.get_holdings(user_id)
        positions = self.get_positions(user_id)

        total_value = sum(h.get('current_value', 0) for h in holdings)
        total_pnl = sum(h.get('pnl', 0) for h in holdings)

        return PortfolioData(
            holdings=holdings,
            positions=positions,
            total_value=total_value,
            total_pnl=total_pnl,
            metadata={
                'source': 'b2b_partner',
                'partner_id': self.partner_id,
                'tenant_id': self.tenant_id,
            },
        )

    def get_market_data(self, symbol: str) -> MarketData:
        try:
            session = self._get_session()
            resp = session.get(
                f'{self.api_base_url}/market/quote/{symbol}', timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                return MarketData(
                    symbol=symbol,
                    current_price=data.get('current_price', 0),
                    previous_close=data.get('previous_close', 0),
                    change_pct=data.get('change_pct', 0),
                    day_high=data.get('day_high', 0),
                    day_low=data.get('day_low', 0),
                    volume=data.get('volume', 0),
                    additional=data,
                )
        except Exception as e:
            logger.error(f"B2B market data error for {symbol}: {e}")

        return MarketData(symbol=symbol)

    def get_holdings(self, user_id: int) -> List[Dict]:
        try:
            session = self._get_session()
            resp = session.get(
                f'{self.api_base_url}/portfolio/holdings',
                params={'user_id': user_id},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get('holdings', [])
        except Exception as e:
            logger.error(f"B2B holdings error for user {user_id}: {e}")
        return []

    def get_positions(self, user_id: int) -> List[Dict]:
        try:
            session = self._get_session()
            resp = session.get(
                f'{self.api_base_url}/portfolio/positions',
                params={'user_id': user_id},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get('positions', [])
        except Exception as e:
            logger.error(f"B2B positions error for user {user_id}: {e}")
        return []

    def health_check(self) -> Dict:
        base = super().health_check()
        base.update({
            'api_base_url': self.api_base_url,
            'partner_id': self.partner_id,
            'tenant_id': self.tenant_id,
        })
        return base


class DatabaseConnector(BaseDataConnector):
    """
    Database Connector - Reads portfolio data directly from local database.

    Fallback connector that uses locally stored portfolio holdings
    when no broker connection is available.
    """

    connector_type = "database"
    connector_name = "Local Database Connector"

    def connect(self) -> bool:
        try:
            from app import db
            db.session.execute(db.text('SELECT 1'))
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"Database connector failed: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        self._connected = False

    def get_portfolio(self, user_id: int) -> PortfolioData:
        holdings = self.get_holdings(user_id)
        total_value = sum(h.get('current_value', 0) for h in holdings)
        total_pnl = sum(h.get('pnl', 0) for h in holdings)

        return PortfolioData(
            holdings=holdings,
            positions=[],
            total_value=total_value,
            total_pnl=total_pnl,
            metadata={'source': 'local_database'},
        )

    def get_market_data(self, symbol: str) -> MarketData:
        return _fetch_market_quote(symbol)

    def get_holdings(self, user_id: int) -> List[Dict]:
        try:
            from models import Portfolio
            holdings = Portfolio.query.filter_by(user_id=user_id).all()
            result = []
            for h in holdings:
                current_price = float(h.current_price) if h.current_price else 0
                purchase_price = float(h.purchase_price) if h.purchase_price else 0
                qty = float(h.quantity) if h.quantity else 0
                current_value = float(h.current_value) if h.current_value else current_price * qty
                pnl = current_value - (purchase_price * qty)
                result.append({
                    'symbol': h.ticker_symbol,
                    'name': h.stock_name,
                    'quantity': qty,
                    'average_price': purchase_price,
                    'current_price': current_price,
                    'current_value': current_value,
                    'pnl': pnl,
                    'asset_type': h.asset_type or 'equities',
                    'sector': h.sector,
                })
            return result
        except Exception as e:
            logger.error(f"Database holdings error for user {user_id}: {e}")
            return []

    def get_positions(self, user_id: int) -> List[Dict]:
        return []


class ConnectorRegistry:
    """
    Central registry for managing data connectors.

    Provides connector discovery, instantiation, and lifecycle management.
    Supports registering custom connector classes for extensibility.
    """

    _instance = None
    _connector_classes: Dict[str, type] = {}
    _active_connectors: Dict[str, BaseDataConnector] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connector_classes = {}
            cls._instance._active_connectors = {}
            cls._instance._register_defaults()
        return cls._instance

    def _register_defaults(self):
        self.register('b2c', B2CConnector)
        self.register('b2b', B2BConnector)
        self.register('database', DatabaseConnector)

    def register(self, connector_type: str, connector_class: type) -> None:
        if not issubclass(connector_class, BaseDataConnector):
            raise TypeError(
                f"{connector_class.__name__} must be a subclass of BaseDataConnector"
            )
        self._connector_classes[connector_type] = connector_class
        logger.info(f"Registered connector type: {connector_type} -> {connector_class.__name__}")

    def unregister(self, connector_type: str) -> None:
        if connector_type in self._active_connectors:
            self._active_connectors[connector_type].disconnect()
            del self._active_connectors[connector_type]
        self._connector_classes.pop(connector_type, None)

    def get_connector(self, connector_type: str, config: Dict = None) -> Optional[BaseDataConnector]:
        if connector_type in self._active_connectors:
            return self._active_connectors[connector_type]

        connector_class = self._connector_classes.get(connector_type)
        if not connector_class:
            logger.error(f"Unknown connector type: {connector_type}")
            return None

        connector = connector_class(config=config)
        if connector.connect():
            self._active_connectors[connector_type] = connector
            return connector

        logger.warning(f"Connector {connector_type} failed to connect, returning unconnected instance")
        return connector

    def get_best_connector(self, user_id: int, tenant_id: str = 'live') -> BaseDataConnector:
        """
        Get the best available connector for a user.

        Priority:
        1. B2C (user has connected broker accounts)
        2. B2B (tenant has partner broker config)
        3. Database (fallback to local data)
        """
        try:
            from models_broker import BrokerAccount
            has_broker = BrokerAccount.query.filter_by(
                user_id=user_id, is_active=True
            ).first() is not None
            if has_broker:
                connector = self.get_connector('b2c')
                if connector and connector.is_connected:
                    return connector
        except Exception:
            pass

        if tenant_id != 'live':
            try:
                from models import DataConnectorConfig
                db_config = DataConnectorConfig.query.filter_by(
                    tenant_id=tenant_id, is_active=True
                ).first()
                if db_config and db_config.config:
                    connector = self.get_connector(
                        db_config.connector_type, config=db_config.config
                    )
                    if connector and connector.is_connected:
                        return connector
            except Exception:
                pass

            try:
                from models import Tenant
                tenant = Tenant.query.get(tenant_id)
                if tenant and tenant.config:
                    b2b_config = tenant.config.get('b2b_connector')
                    if b2b_config:
                        connector = self.get_connector('b2b', config=b2b_config)
                        if connector and connector.is_connected:
                            return connector
            except Exception:
                pass

        connector = self.get_connector('database')
        if connector:
            return connector

        return DatabaseConnector()

    def list_registered(self) -> List[Dict]:
        return [
            {
                'type': ctype,
                'class': cls.__name__,
                'active': ctype in self._active_connectors,
                'connected': self._active_connectors[ctype].is_connected
                if ctype in self._active_connectors else False,
            }
            for ctype, cls in self._connector_classes.items()
        ]

    def health_check_all(self) -> Dict:
        results = {}
        for ctype, connector in self._active_connectors.items():
            results[ctype] = connector.health_check()
        return results

    def disconnect_all(self) -> None:
        for connector in self._active_connectors.values():
            try:
                connector.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting connector: {e}")
        self._active_connectors.clear()


def get_connector_registry() -> ConnectorRegistry:
    return ConnectorRegistry()


def get_portfolio_data(user_id: int, tenant_id: str = 'live') -> PortfolioData:
    registry = get_connector_registry()
    connector = registry.get_best_connector(user_id, tenant_id)
    return connector.get_portfolio(user_id)


def get_market_quote(symbol: str, connector_type: str = 'database') -> MarketData:
    registry = get_connector_registry()
    connector = registry.get_connector(connector_type)
    if connector:
        return connector.get_market_data(symbol)
    return MarketData(symbol=symbol)
