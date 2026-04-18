from datetime import datetime, timezone, timedelta
from models_broker import BrokerType

IST = timezone(timedelta(hours=5, minutes=30))


def _format_ist(dt, fmt='%d %b %Y, %I:%M %p'):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime(fmt)


BROKER_CATALOG = [
    {
        'type': BrokerType.ZERODHA,
        'name': 'Zerodha',
        'color': '#387ed1',
        'letter': 'Z',
        'description': "India's largest broker · KiteConnect OAuth · Secure redirect login",
        'status': 'active',
    },
    {
        'type': BrokerType.DHAN,
        'name': 'Dhan',
        'color': '#0f766e',
        'letter': 'D',
        'description': 'Modern API trading · Token-based authentication',
        'status': 'active',
    },
    {
        'type': BrokerType.UPSTOX,
        'name': 'Upstox',
        'color': '#5a3fc0',
        'letter': 'U',
        'description': 'Upstox Pro · OAuth 2.0 redirect login',
        'status': 'active',
    },
    {
        'type': BrokerType.ANGEL_BROKING,
        'name': 'Angel One',
        'color': '#e03c31',
        'letter': 'A',
        'description': 'SmartAPI · TOTP-based authentication',
        'status': 'active',
    },
    {
        'type': BrokerType.FYERS,
        'name': 'Fyers',
        'color': '#1a73e8',
        'letter': 'F',
        'description': 'Fyers API v3 · OAuth token login',
        'status': 'active',
    },
    {
        'type': BrokerType.SHOONYA,
        'name': 'Shoonya (Finvasia)',
        'color': '#6d28d9',
        'letter': 'S',
        'description': 'Shoonya QuickAuth · TOTP + app key',
        'status': 'active',
    },
    {
        'type': BrokerType.ALICE_BLUE,
        'name': 'Alice Blue',
        'color': '#1e3a8a',
        'letter': 'AB',
        'description': 'ANT API · Direct credentials login',
        'status': 'active',
    },
    {
        'type': BrokerType.FIVE_PAISA,
        'name': '5 Paisa',
        'color': '#e65100',
        'letter': '5P',
        'description': '5 Paisa API · Direct credentials login',
        'status': 'active',
    },
]
