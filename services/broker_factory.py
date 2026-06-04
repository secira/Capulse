import logging
from typing import Dict, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)

BROKER_REGISTRY = {
    "dhan": "brokers.dhan.DhanBroker",
    "zerodha": "brokers.zerodha.ZerodhaBroker",
    "fyers": "brokers.fyers.FyersBroker",
    "shoonya": "brokers.shoonya.ShoonyaBroker",
    "upstox": "brokers.upstox.UpstoxBroker",
    "angel_broking": "brokers.angel.AngelBroker",
    "angel": "brokers.angel.AngelBroker",
    "5paisa": "brokers.fivepaisa.FivePaisaBroker",
    "alice_blue": "brokers.aliceblue.AliceBlueBroker",
    "aliceblue": "brokers.aliceblue.AliceBlueBroker",
}


def _import_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def get_broker(broker_name: str, credentials: Dict[str, str]) -> Optional[BrokerBase]:
    broker_name = broker_name.lower().strip()
    if broker_name not in BROKER_REGISTRY:
        logger.error(f"Unsupported broker: {broker_name}")
        return None
    try:
        cls = _import_class(BROKER_REGISTRY[broker_name])
        return cls(credentials)
    except Exception as e:
        logger.error(f"Failed to create broker {broker_name}: {e}")
        return None


def get_data_broker_for_user(user_id: int) -> Optional[BrokerBase]:
    """
    Return a broker instance to use for market data for the given user.
    Priority:
      1. BrokerAccount with is_data_broker=True (user picked from their connected brokers)
      2. DataApiBroker row (legacy separate-credentials model)
    """
    try:
        from app import db
        from models_broker import BrokerAccount
        account = BrokerAccount.query.filter_by(
            user_id=user_id,
            is_data_broker=True,
            is_active=True,
        ).first()

        if account:
            creds = account.get_credentials()
            broker_type = account.broker_type.value if hasattr(account.broker_type, 'value') else str(account.broker_type)
            broker = get_broker(broker_type, creds)
            if broker:
                logger.info(f"Data API broker for user {user_id}: {account.broker_name} (account id={account.id})")
            return broker
    except Exception as e:
        logger.error(f"Failed to get data broker from BrokerAccount for user {user_id}: {e}")

    try:
        from models_broker import DataApiBroker
        legacy = DataApiBroker.query.filter_by(
            user_id=user_id,
            is_active=True,
            connection_status='connected',
        ).first()
        if not legacy:
            return None
        creds = legacy.get_credentials()
        broker = get_broker(legacy.broker_type, creds)
        if broker:
            logger.info(f"Data API broker (legacy) for user {user_id}: {legacy.broker_name}")
        return broker
    except Exception as e:
        logger.error(f"Failed to get data broker for user {user_id}: {e}")
        return None


def get_all_data_brokers_for_user(user_id: int) -> list:
    """
    Return ALL data-capable broker instances for the given user, ordered by
    preference so the caller can try each in sequence until one succeeds.

    Ordering:
      1. BrokerAccount rows with is_data_broker=True  (explicit user preference)
      2. Remaining active BrokerAccount rows           (Zerodha, Angel, etc.)
      3. Legacy DataApiBroker rows                     (backward-compat)

    Returns a list of (broker_name, BrokerBase) tuples. Duplicates (same
    broker_type) are deduplicated — the first occurrence wins.
    """
    results = []
    seen_keys: set = set()

    # ── 1 & 2. BrokerAccount entries ─────────────────────────────────────────
    try:
        from models_broker import BrokerAccount
        accounts = (
            BrokerAccount.query
            .filter_by(user_id=user_id, is_active=True)
            .order_by(
                BrokerAccount.is_data_broker.desc(),  # is_data_broker=True first
                BrokerAccount.id.asc(),
            )
            .all()
        )
        for acct in accounts:
            try:
                btype = acct.broker_type.value if hasattr(acct.broker_type, 'value') else str(acct.broker_type)
                btype_key = btype.lower().strip()
                if btype_key not in BROKER_REGISTRY or btype_key in seen_keys:
                    continue
                seen_keys.add(btype_key)
                broker = get_broker(btype_key, acct.get_credentials())
                if broker:
                    results.append((acct.broker_name or btype_key, broker))
            except Exception as _e:
                logger.debug(f"get_all_data_brokers_for_user: account {acct.id} error: {_e}")
    except Exception as e:
        logger.error(f"get_all_data_brokers_for_user BrokerAccount query: {e}")

    # ── 3. Legacy DataApiBroker ───────────────────────────────────────────────
    try:
        from models_broker import DataApiBroker
        for row in DataApiBroker.query.filter_by(
            user_id=user_id, is_active=True, connection_status='connected'
        ).all():
            try:
                btype_key = (row.broker_type or '').lower().strip()
                if btype_key not in BROKER_REGISTRY or btype_key in seen_keys:
                    continue
                seen_keys.add(btype_key)
                broker = get_broker(btype_key, row.get_credentials())
                if broker:
                    results.append((row.broker_name or btype_key, broker))
            except Exception as _e:
                logger.debug(f"get_all_data_brokers_for_user: legacy row {row.id} error: {_e}")
    except Exception as e:
        logger.error(f"get_all_data_brokers_for_user DataApiBroker query: {e}")

    logger.info(
        f"get_all_data_brokers_for_user(user={user_id}): "
        f"{len(results)} broker(s) — {[n for n, _ in results]}"
    )
    return results


def get_admin_data_brokers() -> list:
    """
    Return admin-managed data-source brokers ordered by REMAINING RUNWAY
    (most runway first), so a slot that's about to expire never gets picked
    over a fresher one. Skips already-expired rows.

    Falls back to static priority ordering if the runway-aware pool helper
    fails to load (e.g. early in app boot).

    Returns a list of (priority, broker_type, broker_name, BrokerBase) tuples.
    Failures to instantiate are skipped so the chain still falls through.
    """
    # Preferred path — runway-aware pool selection.
    try:
        from services.admin_data_broker_pool import get_admin_brokers_by_runway
        ranked = get_admin_brokers_by_runway()
        # Drop the trailing row object so the public tuple shape stays stable.
        return [(prio, btype, bname, broker) for prio, btype, bname, broker, _row in ranked]
    except Exception as e:
        logger.debug(f"runway-aware admin pool unavailable, falling back to priority order: {e}")

    out = []
    try:
        from models_broker import AdminDataBroker
        rows = AdminDataBroker.query.filter_by(is_active=True).order_by(AdminDataBroker.priority.asc()).all()
        for row in rows:
            try:
                creds = row.get_credentials()
                broker = get_broker(row.broker_type, creds)
                if broker:
                    out.append((row.priority, row.broker_type, row.broker_name, broker))
                else:
                    logger.warning(f"Admin data broker priority={row.priority} ({row.broker_type}) could not be instantiated")
            except Exception as e:
                logger.error(f"Admin data broker priority={row.priority} init error: {e}")
    except Exception as e:
        logger.error(f"get_admin_data_brokers failed: {e}")
    return out


INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "INDIAVIX", "INDIA VIX"}


def _underlying_from_symbol(symbol: str) -> Optional[str]:
    """
    Map an index / future / option contract symbol to its underlying index name
    that admin / user brokers know via get_price(). Returns None for equities.

    Examples:
      NIFTY                        → NIFTY
      BANKNIFTY                    → BANKNIFTY
      NIFTY25NOV24500CE            → NIFTY     (option)
      NIFTYFUT / NIFTY-FUT         → NIFTY     (future)
      RELIANCE                     → None
    """
    if not symbol:
        return None
    s = symbol.upper().strip().replace(" ", "")
    if s in {"NIFTY", "NIFTY50"}:
        return "NIFTY"
    if s in {"INDIAVIX", "VIX"}:
        return "INDIA VIX"
    for idx in ("BANKNIFTY", "FINNIFTY", "SENSEX", "NIFTY"):
        if s.startswith(idx):
            tail = s[len(idx):]
            if not tail or tail.endswith(("CE", "PE", "FUT")) or tail.startswith(("FUT", "-FUT")):
                return idx
    return None


def get_index_price_with_fallback(symbol: str, user_id: Optional[int] = None) -> tuple:
    """
    Resolve a live index price via the data-broker fallback chain.

    Priority (admin-first — Dhan/Zerodha connected by the admin always win):
      1. Admin primary data broker  (e.g. admin Dhan)
      2. Admin secondary data broker (e.g. admin Zerodha)
      3. User's own data broker (BrokerAccount / DataApiBroker) — only if the
         admin pool is empty / all admin brokers failed.

    Returns (price, source_label). price = 0.0 when no broker could supply one.
    source_label is e.g. 'admin:Dhan (P1)', 'user:Dhan', or 'unavailable'.
    """
    underlying = _underlying_from_symbol(symbol) or symbol.upper()

    # ── 1. Admin pool first — operator-controlled Dhan/Zerodha is the
    #      authoritative data source for every user. ─────────────────────
    for prio, btype, bname, broker in get_admin_data_brokers():
        try:
            if not broker.connect():
                continue
            px = float(broker.get_price(underlying) or 0)
            if px > 0:
                return px, f"admin:{bname} (P{prio})"
        except Exception as e:
            logger.debug(f"admin broker P{prio} {btype} get_price({underlying}) failed: {e}")
            continue

    # ── 2. Fall back to the user's personal data broker ──────────────────
    if user_id:
        try:
            ub = get_data_broker_for_user(user_id)
            if ub:
                try:
                    if ub.connect():
                        px = float(ub.get_price(underlying) or 0)
                        if px > 0:
                            return px, f"user:{ub.__class__.__name__.replace('Broker','')}"
                except Exception as e:
                    logger.debug(f"user data broker get_price({underlying}) failed: {e}")
        except Exception as e:
            logger.debug(f"user data broker lookup failed: {e}")

    return 0.0, "unavailable"


def get_supported_brokers() -> list:
    return [
        {"key": "dhan", "name": "Dhan", "supports_direct_chain": True},
        {"key": "zerodha", "name": "Zerodha", "supports_direct_chain": False},
        {"key": "fyers", "name": "Fyers", "supports_direct_chain": True},
        {"key": "shoonya", "name": "Shoonya (Finvasia)", "supports_direct_chain": False},
        {"key": "upstox", "name": "Upstox", "supports_direct_chain": True},
        {"key": "angel_broking", "name": "Angel One", "supports_direct_chain": True},
        {"key": "5paisa", "name": "5 Paisa", "supports_direct_chain": True},
        {"key": "alice_blue", "name": "Alice Blue", "supports_direct_chain": False},
    ]
