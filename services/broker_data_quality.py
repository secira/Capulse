"""
Broker Data Quality & Freshness Service — Target Capital
Provides data freshness scoring, quality validation, and pre-trade safety checks.
"""
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

FRESHNESS_THRESHOLDS = {
    'high':   timedelta(minutes=5),
    'medium': timedelta(minutes=30),
    'low':    timedelta(hours=2),
}


class BrokerDataQuality:

    def __init__(self, user_id, tenant_id='live'):
        self.user_id = user_id
        self.tenant_id = tenant_id

    def get_data_freshness(self):
        from models_broker import BrokerAccount
        accounts = BrokerAccount.query.filter_by(
            user_id=self.user_id, is_active=True, connection_status='connected'
        ).all()

        if not accounts:
            return {
                'has_brokers': False,
                'brokers': [],
                'overall': 'none',
                'stale_count': 0,
                'warning': None,
            }

        now = datetime.utcnow()
        brokers = []
        stale_count = 0

        for acct in accounts:
            if not acct.last_sync:
                freshness = 'never'
                age_minutes = None
                stale_count += 1
            else:
                age = now - acct.last_sync
                age_minutes = int(age.total_seconds() / 60)
                if age <= FRESHNESS_THRESHOLDS['high']:
                    freshness = 'high'
                elif age <= FRESHNESS_THRESHOLDS['medium']:
                    freshness = 'medium'
                elif age <= FRESHNESS_THRESHOLDS['low']:
                    freshness = 'low'
                else:
                    freshness = 'stale'
                    stale_count += 1

            brokers.append({
                'id': acct.id,
                'name': acct.broker_name,
                'freshness': freshness,
                'age_minutes': age_minutes,
                'last_sync': acct.last_sync,
                'sync_status': acct.sync_status or 'pending',
                'age_display': self._format_age(age_minutes),
            })

        if stale_count == len(accounts):
            overall = 'stale'
        elif stale_count > 0:
            overall = 'partial'
        else:
            overall = 'fresh'

        warning = None
        if overall == 'stale':
            warning = 'All broker data is outdated. Tap Sync All to refresh.'
        elif overall == 'partial':
            stale_names = [b['name'] for b in brokers if b['freshness'] in ('stale', 'never')]
            warning = f"Portfolio partially updated — {', '.join(stale_names)} {'is' if len(stale_names)==1 else 'are'} outdated."

        return {
            'has_brokers': True,
            'brokers': brokers,
            'overall': overall,
            'stale_count': stale_count,
            'warning': warning,
        }

    def get_quality_score(self):
        from models_broker import BrokerAccount
        from models import ManualTradeImport
        accounts = BrokerAccount.query.filter_by(
            user_id=self.user_id, is_active=True, connection_status='connected'
        ).all()

        issues = []
        score = 100

        if not accounts:
            return {'score': 0, 'issues': ['No broker accounts connected'], 'grade': 'N/A'}

        for acct in accounts:
            if not acct.last_sync:
                issues.append(f"{acct.broker_name}: Never synced")
                score -= 15
            elif (datetime.utcnow() - acct.last_sync) > timedelta(hours=24):
                issues.append(f"{acct.broker_name}: Data older than 24 hours")
                score -= 10
            if acct.sync_status == 'failed':
                issues.append(f"{acct.broker_name}: Last sync failed")
                score -= 10

        trades = ManualTradeImport.query.filter_by(
            user_id=self.user_id, tenant_id=self.tenant_id
        ).all()

        if trades:
            seen = set()
            dupes = 0
            missing_exit = 0
            for t in trades:
                key = (t.symbol, str(getattr(t, 'entry_time', '')), getattr(t, 'quantity', 0))
                if key in seen:
                    dupes += 1
                seen.add(key)
                if getattr(t, 'exit_price', 0) == 0 and getattr(t, 'realized_pnl', 0) == 0:
                    missing_exit += 1

            if dupes > 0:
                issues.append(f"{dupes} potential duplicate trade(s) detected")
                score -= min(dupes * 3, 15)
            if missing_exit > len(trades) * 0.3:
                issues.append(f"{missing_exit} trades missing exit data (may affect Behavioural AI)")
                score -= 5

        score = max(score, 0)
        if score >= 85:
            grade = 'Excellent'
        elif score >= 65:
            grade = 'Good'
        elif score >= 40:
            grade = 'Fair'
        else:
            grade = 'Poor'

        return {'score': score, 'issues': issues, 'grade': grade}

    def pre_trade_validation(self, broker_account, order_data):
        checks = []
        passed = True

        if broker_account.connection_status != 'connected':
            checks.append({'check': 'Broker Connected', 'pass': False, 'detail': 'Broker is disconnected'})
            passed = False
        else:
            checks.append({'check': 'Broker Connected', 'pass': True, 'detail': f'{broker_account.broker_name} active'})

        if broker_account.sync_status == 'failed':
            checks.append({'check': 'Sync Health', 'pass': False, 'detail': 'Last sync failed — data may be stale'})
        else:
            checks.append({'check': 'Sync Health', 'pass': True, 'detail': 'Data up to date'})

        symbol = order_data.get('symbol', '')
        qty = order_data.get('quantity', 0)
        if not symbol:
            checks.append({'check': 'Symbol Valid', 'pass': False, 'detail': 'No symbol specified'})
            passed = False
        else:
            checks.append({'check': 'Symbol Valid', 'pass': True, 'detail': symbol})

        if not qty or qty <= 0:
            checks.append({'check': 'Quantity Valid', 'pass': False, 'detail': 'Quantity must be > 0'})
            passed = False
        else:
            checks.append({'check': 'Quantity Valid', 'pass': True, 'detail': f'{qty} units'})

        price = order_data.get('price')
        stop_loss = order_data.get('trigger_price')
        if price and stop_loss:
            action = order_data.get('transaction_type', 'BUY').upper()
            if action == 'BUY' and stop_loss >= price:
                checks.append({'check': 'Stop Loss', 'pass': False, 'detail': 'Stop loss must be below entry price for BUY'})
                passed = False
            elif action == 'SELL' and stop_loss <= price:
                checks.append({'check': 'Stop Loss', 'pass': False, 'detail': 'Stop loss must be above entry price for SELL'})
                passed = False
            else:
                risk_pct = abs(price - stop_loss) / price * 100 if price else 0
                checks.append({'check': 'Stop Loss', 'pass': True, 'detail': f'Risk: {risk_pct:.1f}% per unit'})

        margin = broker_account.margin_available or 0
        estimated_cost = (price or 0) * (qty or 0)
        if estimated_cost > 0 and margin > 0:
            if estimated_cost > margin:
                checks.append({'check': 'Margin Check', 'pass': False, 'detail': f'Estimated ₹{estimated_cost:,.0f} exceeds available margin ₹{margin:,.0f}'})
                passed = False
            else:
                checks.append({'check': 'Margin Check', 'pass': True, 'detail': f'₹{margin:,.0f} available'})

        return {'passed': passed, 'checks': checks}

    def should_auto_sync(self):
        from models_broker import BrokerAccount
        accounts = BrokerAccount.query.filter_by(
            user_id=self.user_id, is_active=True, connection_status='connected'
        ).all()
        stale = []
        for acct in accounts:
            if not acct.last_sync or (datetime.utcnow() - acct.last_sync) > timedelta(minutes=30):
                stale.append(acct.id)
        return stale

    @staticmethod
    def _format_age(age_minutes):
        if age_minutes is None:
            return 'Never'
        if age_minutes < 1:
            return 'Just now'
        if age_minutes < 60:
            return f'{age_minutes}m ago'
        hours = age_minutes // 60
        if hours < 24:
            return f'{hours}h ago'
        days = hours // 24
        return f'{days}d ago'
