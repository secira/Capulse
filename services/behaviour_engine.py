"""
Behavioural AI Engine — Capulse
Comprehensive trading psychology analysis with 20 modules across 5 categories.
Produces a Master Behavioral Score (0-100) and actionable insights.

Categories:
  A. Trading Behavior   (5 modules)
  B. Risk Behavior       (4 modules)
  C. Portfolio Behavior  (3 modules)
  D. Performance Patterns(3 modules)
  E. Psychological       (5 modules)
"""
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
import math
import logging

logger = logging.getLogger(__name__)

SEVERITY_RANK = {'high': 3, 'medium': 2, 'low': 1, 'none': 0}

CATEGORY_META = {
    'trading': {
        'label': 'Trading Behavior', 'icon': 'fas fa-exchange-alt', 'color': '#e53e3e',
        'desc': 'How you enter and exit trades — frequency, timing, and decision quality.',
    },
    'risk': {
        'label': 'Risk Behavior', 'icon': 'fas fa-shield-alt', 'color': '#eab308',
        'desc': 'How you manage risk — position sizing, exposure, leverage, and drawdown response.',
    },
    'portfolio': {
        'label': 'Portfolio Behavior', 'icon': 'fas fa-th-large', 'color': '#22c55e',
        'desc': 'How you construct and maintain your portfolio — diversification, churn, and efficiency.',
    },
    'performance': {
        'label': 'Performance Patterns', 'icon': 'fas fa-chart-line', 'color': '#3b82f6',
        'desc': 'Statistical analysis of your trading outcomes — win rates, risk-reward, consistency.',
    },
    'psychology': {
        'label': 'Psychological Patterns', 'icon': 'fas fa-brain', 'color': '#8b5cf6',
        'desc': 'Emotional biases that affect your trading — FOMO, panic, drift, and hidden biases.',
    },
}


class BehaviourEngine:
    REVENGE_WINDOW_MINS    = 30
    OVERTRADE_HOURS        = 4
    OVERTRADE_THRESHOLD    = 5
    TILT_SIZE_INCREASE     = 0.25
    LOSS_AVERSION_RATIO    = 1.5
    PANIC_SELL_HOURS       = 2
    OVERCONF_SIZE_INCREASE = 0.30
    CONCENTRATION_LIMIT    = 0.30
    FOMO_RISE_PCT          = 5.0
    HIGH_LEVERAGE_RATIO    = 0.40

    def __init__(self, user_id, tenant_id):
        self.user_id   = user_id
        self.tenant_id = tenant_id
        self._trades   = None
        self._orders   = None
        self._holdings = None

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_trades(self, days=90):
        from models import TradeHistory, ManualTradeImport
        since = datetime.utcnow() - timedelta(days=days)
        broker_trades = (
            TradeHistory.query
            .filter_by(user_id=self.user_id, tenant_id=self.tenant_id)
            .filter(TradeHistory.exit_time >= since)
            .order_by(TradeHistory.exit_time.asc())
            .all()
        )
        manual_trades = (
            ManualTradeImport.query
            .filter_by(user_id=self.user_id, tenant_id=self.tenant_id)
            .filter(ManualTradeImport.exit_time >= since)
            .order_by(ManualTradeImport.exit_time.asc())
            .all()
        )
        combined = sorted(broker_trades + manual_trades, key=lambda t: t.exit_time)
        return combined

    def _get_trades(self):
        if self._trades is None:
            self._trades = self._load_trades()
        return self._trades

    def _load_broker_orders(self, days=90):
        try:
            from models_broker import BrokerOrder, BrokerAccount
            since = datetime.utcnow() - timedelta(days=days)
            accounts = BrokerAccount.query.filter_by(
                user_id=self.user_id, is_active=True
            ).all()
            if not accounts:
                return []
            account_ids = [a.id for a in accounts]
            return (
                BrokerOrder.query
                .filter(BrokerOrder.broker_account_id.in_(account_ids))
                .filter(BrokerOrder.order_time >= since)
                .order_by(BrokerOrder.order_time.asc())
                .all()
            )
        except Exception:
            return []

    def _get_orders(self):
        if self._orders is None:
            self._orders = self._load_broker_orders()
        return self._orders

    def _load_holdings(self):
        try:
            from models import Portfolio
            return Portfolio.query.filter_by(
                user_id=self.user_id, tenant_id=self.tenant_id
            ).all()
        except Exception:
            return []

    def _get_holdings(self):
        if self._holdings is None:
            self._holdings = self._load_holdings()
        return self._holdings

    # ═══════════════════════════════════════════════════════════════════════════
    # A. TRADING BEHAVIOR MODULES (5)
    # ═══════════════════════════════════════════════════════════════════════════

    def detect_overtrading(self):
        trades = self._get_trades()
        overtrading_days = set()
        daily_counts = defaultdict(int)

        for t in trades:
            daily_counts[t.entry_time.date()] += 1

        for i, trade in enumerate(trades):
            window_end = trade.entry_time + timedelta(hours=self.OVERTRADE_HOURS)
            count = sum(1 for t in trades[i:] if t.entry_time <= window_end)
            if count > self.OVERTRADE_THRESHOLD:
                overtrading_days.add(trade.entry_time.date())

        count = len(overtrading_days)
        avg_daily = round(sum(daily_counts.values()) / max(len(daily_counts), 1), 1) if daily_counts else 0
        sev = 'high' if count >= 5 else 'medium' if count >= 2 else 'low' if count >= 1 else 'none'
        score = max(0, 100 - count * 15)

        return {
            'detected': count > 0, 'count': count, 'severity': sev,
            'score': score, 'avg_daily_trades': avg_daily,
            'daily_distribution': {k.isoformat() if hasattr(k, 'isoformat') else str(k): v for k, v in sorted(daily_counts.items())[-14:]},
            'label': 'Overtrading Detector', 'icon': 'fas fa-bolt', 'color': '#dd6b20',
            'description': (
                f'Detected {count} day(s) with >{self.OVERTRADE_THRESHOLD} trades in {self.OVERTRADE_HOURS}h. '
                f'You average {avg_daily} trades/day.'
            ) if count > 0 else f'No overtrading detected. You average {avg_daily} trades/day.',
            'insight': f'You are trading {round(avg_daily / 3, 1)}x more than average' if avg_daily > 9 else 'Trading frequency is within healthy limits.',
            'advice': 'Set a daily trade limit. Quality over quantity — more trades often means more losses from transaction costs and impulsive decisions.',
        }

    def detect_revenge_trading(self):
        trades = self._get_trades()
        incidents = []

        for i, trade in enumerate(trades):
            if trade.trade_result != 'LOSS':
                continue
            for j in range(i + 1, len(trades)):
                nt = trades[j]
                gap = (nt.entry_time - trade.exit_time).total_seconds() / 60
                if gap < 0:
                    continue
                if gap > self.REVENGE_WINDOW_MINS:
                    break
                if (nt.quantity * nt.entry_price) >= (trade.quantity * trade.entry_price):
                    incidents.append({
                        'date': trade.exit_time.strftime('%d %b %Y'),
                        'loss_trade': trade.symbol,
                        'loss_amount': abs(round(trade.realized_pnl, 2)),
                        'revenge_trade': nt.symbol,
                        'gap_mins': round(gap),
                    })

        count = len(incidents)
        sev = 'high' if count >= 3 else 'medium' if count >= 1 else 'none'
        score = max(0, 100 - count * 20)

        return {
            'detected': count > 0, 'count': count, 'incidents': incidents[-5:],
            'severity': sev, 'score': score,
            'label': 'Loss Chasing / Revenge Trading', 'icon': 'fas fa-fire', 'color': '#e53e3e',
            'description': (
                f'You entered {count} trade(s) within {self.REVENGE_WINDOW_MINS}m of a loss with equal or larger size.'
            ) if count > 0 else 'No revenge trading detected in the last 90 days.',
            'insight': 'You increase risk after losses (revenge trading pattern)' if count > 0 else 'Good emotional control after losses.',
            'advice': 'After a loss, step away for at least 30 minutes. Emotional trades almost never recover losses.',
        }

    def detect_profit_booking_bias(self):
        trades = self._get_trades()
        wins = [t for t in trades if t.trade_result == 'WIN' and t.holding_period_hours]
        losses = [t for t in trades if t.trade_result == 'LOSS' and t.holding_period_hours]

        if len(wins) < 3 or len(losses) < 3:
            return {
                'detected': False, 'severity': 'none', 'score': 50,
                'label': 'Profit Booking Bias', 'icon': 'fas fa-hand-holding-usd', 'color': '#38a169',
                'win_avg_hours': 0, 'loss_avg_hours': 0,
                'avg_win_pct': 0, 'avg_loss_pct': 0,
                'description': 'Not enough data (need 3+ wins and 3+ losses).',
                'insight': 'Insufficient data to analyze profit booking patterns.',
                'advice': '',
            }

        win_avg_h = sum(t.holding_period_hours for t in wins) / len(wins)
        loss_avg_h = sum(t.holding_period_hours for t in losses) / len(losses)

        avg_win_pct = sum(
            abs(t.realized_pnl) / (t.entry_price * t.quantity) * 100
            for t in wins if t.entry_price and t.quantity
        ) / len(wins) if wins else 0

        avg_loss_pct = sum(
            abs(t.realized_pnl) / (t.entry_price * t.quantity) * 100
            for t in losses if t.entry_price and t.quantity
        ) / len(losses) if losses else 0

        detected = win_avg_h < loss_avg_h * 0.5 or (avg_win_pct < avg_loss_pct * 0.7 and avg_win_pct > 0)
        sev = 'high' if detected and avg_win_pct < avg_loss_pct * 0.4 else 'medium' if detected else 'none'
        score = max(0, 100 - (30 if sev == 'high' else 15 if sev == 'medium' else 0))

        return {
            'detected': detected, 'severity': sev, 'score': score,
            'win_avg_hours': round(win_avg_h, 1), 'loss_avg_hours': round(loss_avg_h, 1),
            'avg_win_pct': round(avg_win_pct, 2), 'avg_loss_pct': round(avg_loss_pct, 2),
            'label': 'Profit Booking Bias', 'icon': 'fas fa-hand-holding-usd', 'color': '#38a169',
            'description': (
                f'You exit profitable trades too early — avg win {round(avg_win_pct, 1)}% vs avg loss {round(avg_loss_pct, 1)}%. '
                f'Winners held {round(win_avg_h, 1)}h vs losers {round(loss_avg_h, 1)}h.'
            ) if detected else 'You let winners run at a healthy ratio.',
            'insight': 'You exit profitable trades too early' if detected else 'Healthy profit booking pattern.',
            'advice': 'Use trailing stop-losses instead of fixed profit targets. Let your winners run while protecting gains.',
        }

    def detect_loss_aversion(self):
        trades = self._get_trades()
        wins = [t for t in trades if t.trade_result == 'WIN']
        losses = [t for t in trades if t.trade_result == 'LOSS']

        if len(wins) < 3 or len(losses) < 3:
            return {
                'detected': False, 'severity': 'none', 'score': 50,
                'label': 'Holding Losses Too Long', 'icon': 'fas fa-clock', 'color': '#718096',
                'win_avg_hours': 0, 'loss_avg_hours': 0, 'ratio': 0,
                'description': 'Not enough data (need 3+ wins and 3+ losses).',
                'insight': 'Insufficient data to analyze holding behavior.',
                'advice': '',
            }

        win_avg = sum(t.holding_period_hours for t in wins) / len(wins)
        loss_avg = sum(t.holding_period_hours for t in losses) / len(losses)
        ratio = round(loss_avg / win_avg, 1) if win_avg > 0 else 0
        detected = loss_avg > win_avg * self.LOSS_AVERSION_RATIO

        sev = 'high' if detected and ratio > 3 else 'medium' if detected else 'none'
        score = max(0, 100 - (25 if sev == 'high' else 12 if sev == 'medium' else 0))

        return {
            'detected': detected, 'severity': sev, 'score': score,
            'win_avg_hours': round(win_avg, 1), 'loss_avg_hours': round(loss_avg, 1), 'ratio': ratio,
            'label': 'Holding Losses Too Long', 'icon': 'fas fa-clock', 'color': '#3182ce',
            'description': (
                f'You hold losing trades {ratio}x longer than winners ({round(loss_avg, 1)}h vs {round(win_avg, 1)}h).'
            ) if detected else f'Healthy holding ratio ({round(win_avg, 1)}h wins vs {round(loss_avg, 1)}h losses).',
            'insight': 'You hold losing positions longer than profitable ones' if detected else 'Balanced holding patterns.',
            'advice': 'Use stop-losses on every trade. Cutting losses quickly and letting winners run is the foundation of profitable trading.',
        }

    def detect_trade_timing(self):
        trades = self._get_trades()
        if not trades:
            return {
                'detected': False, 'severity': 'none', 'score': 50,
                'label': 'Trade Timing Quality', 'icon': 'fas fa-crosshairs', 'color': '#6366f1',
                'description': 'No trade data to analyze timing quality.', 'insight': '', 'advice': '',
                'morning_wr': 0, 'midday_wr': 0, 'closing_wr': 0,
            }

        sessions = {'morning': (9, 11), 'midday': (11, 14), 'closing': (14, 16)}
        session_stats = {}
        for name, (start, end) in sessions.items():
            st = [t for t in trades if start <= t.entry_time.hour < end]
            wins = sum(1 for t in st if t.trade_result == 'WIN')
            session_stats[name] = {
                'total': len(st), 'wins': wins,
                'win_rate': round(wins / len(st) * 100, 1) if st else 0,
            }

        first_hour = [t for t in trades if t.entry_time.hour == 9]
        first_hour_losses = sum(1 for t in first_hour if t.trade_result == 'LOSS')
        rush_ratio = first_hour_losses / len(first_hour) if first_hour else 0

        poor_timing = rush_ratio > 0.6 and len(first_hour) >= 5
        sev = 'medium' if poor_timing else 'none'
        score = max(0, 100 - (20 if poor_timing else 0))

        best_session = max(session_stats.items(), key=lambda x: x[1]['win_rate']) if session_stats else ('none', {'win_rate': 0})

        return {
            'detected': poor_timing, 'severity': sev, 'score': score,
            'session_stats': session_stats,
            'morning_wr': session_stats.get('morning', {}).get('win_rate', 0),
            'midday_wr': session_stats.get('midday', {}).get('win_rate', 0),
            'closing_wr': session_stats.get('closing', {}).get('win_rate', 0),
            'best_session': best_session[0],
            'label': 'Trade Timing Quality', 'icon': 'fas fa-crosshairs', 'color': '#6366f1',
            'description': (
                f'High loss rate ({round(rush_ratio * 100)}%) in the first hour of trading. '
                f'Best session: {best_session[0]} ({best_session[1]["win_rate"]}% win rate).'
            ) if poor_timing else f'Best session: {best_session[0]} ({best_session[1]["win_rate"]}% win rate).',
            'insight': 'You tend to buy near price peaks in the opening hour' if poor_timing else f'Your best time to trade is {best_session[0]}.',
            'advice': 'Avoid the first 15–30 minutes of market open. Wait for the morning volatility to settle before entering positions.',
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # B. RISK BEHAVIOR MODULES (4)
    # ═══════════════════════════════════════════════════════════════════════════

    def detect_position_sizing_consistency(self):
        trades = self._get_trades()
        sizes = [t.quantity * t.entry_price for t in trades if t.entry_price and t.quantity]

        if len(sizes) < 5:
            return {
                'detected': False, 'severity': 'none', 'score': 50,
                'label': 'Position Sizing Consistency', 'icon': 'fas fa-balance-scale', 'color': '#f59e0b',
                'cv': 0, 'min_size': 0, 'max_size': 0, 'avg_size': 0,
                'description': 'Not enough trades to analyze sizing consistency.',
                'insight': 'Insufficient data.', 'advice': '',
            }

        avg = statistics.mean(sizes)
        std = statistics.stdev(sizes) if len(sizes) > 1 else 0
        cv = round(std / avg * 100, 1) if avg > 0 else 0
        detected = cv > 50
        sev = 'high' if cv > 80 else 'medium' if cv > 50 else 'none'
        score = max(0, min(100, 100 - max(0, cv - 30)))

        return {
            'detected': detected, 'severity': sev, 'score': round(score),
            'cv': cv, 'min_size': round(min(sizes)), 'max_size': round(max(sizes)),
            'avg_size': round(avg), 'std': round(std),
            'label': 'Position Sizing Consistency', 'icon': 'fas fa-balance-scale', 'color': '#f59e0b',
            'description': (
                f'Position sizes vary by {cv}% (coefficient of variation). '
                f'Range: ₹{round(min(sizes)):,} to ₹{round(max(sizes)):,}.'
            ),
            'insight': 'Your position sizes are highly inconsistent' if detected else 'Position sizing is reasonably consistent.',
            'advice': 'Use a fixed percentage (1-2%) of portfolio for each trade. Consistent sizing prevents outsized losses on any single trade.',
        }

    def detect_overexposure(self):
        holdings = self._get_holdings()
        if not holdings:
            trades = self._get_trades()
            by_sym = defaultdict(float)
            for t in trades:
                by_sym[t.symbol] += abs(t.quantity * t.entry_price) if t.entry_price else 0
            total = sum(by_sym.values())
            sectors = {}
            if total > 0:
                for sym, val in sorted(by_sym.items(), key=lambda x: x[1], reverse=True)[:10]:
                    sectors[sym] = round(val / total * 100, 1)
        else:
            total = sum(
                (getattr(h, 'current_value', 0) or getattr(h, 'quantity', 0) * getattr(h, 'buy_price', 0) or 0)
                for h in holdings
            )
            sectors = {}
            if total > 0:
                by_sector = defaultdict(float)
                for h in holdings:
                    sector = getattr(h, 'sector', None) or getattr(h, 'asset_class', 'Other') or 'Other'
                    val = getattr(h, 'current_value', 0) or getattr(h, 'quantity', 0) * getattr(h, 'buy_price', 0) or 0
                    by_sector[sector] += val
                for sec, val in sorted(by_sector.items(), key=lambda x: x[1], reverse=True)[:10]:
                    sectors[sec] = round(val / total * 100, 1)

        max_conc = max(sectors.values()) if sectors else 0
        max_sector = max(sectors, key=sectors.get) if sectors else 'N/A'
        detected = max_conc > self.CONCENTRATION_LIMIT * 100
        sev = 'high' if max_conc > 50 else 'medium' if max_conc > 30 else 'none'
        score = max(0, min(100, 100 - max(0, max_conc - 25)))

        return {
            'detected': detected, 'severity': sev, 'score': round(score),
            'sector_exposure': sectors, 'max_concentration': max_conc, 'max_sector': max_sector,
            'label': 'Overexposure Detection', 'icon': 'fas fa-exclamation-triangle', 'color': '#ef4444',
            'description': (
                f'{round(max_conc)}% of your capital is in {max_sector}.'
            ) if detected else 'No dangerous concentration detected.',
            'insight': f'{round(max_conc)}% of your capital is concentrated in {max_sector}' if detected else 'Exposure is well distributed.',
            'advice': 'Limit any single sector to 25-30% of portfolio. Diversification protects against sector-specific crashes.',
        }

    def detect_leverage_risk(self):
        trades = self._get_trades()
        if not trades:
            return {
                'detected': False, 'severity': 'none', 'score': 80,
                'label': 'Leverage / Options Risk', 'icon': 'fas fa-layer-group', 'color': '#f97316',
                'fno_ratio': 0, 'fno_count': 0, 'equity_count': 0,
                'description': 'No trade data to analyze leverage risk.',
                'insight': '', 'advice': '',
            }

        fno_keywords = ['FUT', 'CE', 'PE', 'OPT', 'NRML', 'F&O', 'OPTION', 'FUTURE']
        fno_trades = []
        equity_trades = []
        for t in trades:
            sym = (t.symbol or '').upper()
            strategy = (t.strategy_name or '').upper()
            is_fno = any(kw in sym for kw in fno_keywords) or any(kw in strategy for kw in fno_keywords)
            if is_fno:
                fno_trades.append(t)
            else:
                equity_trades.append(t)

        total = len(trades)
        fno_ratio = len(fno_trades) / total if total > 0 else 0
        fno_pnl = sum(t.realized_pnl for t in fno_trades)
        eq_pnl = sum(t.realized_pnl for t in equity_trades)

        detected = fno_ratio > self.HIGH_LEVERAGE_RATIO
        sev = 'high' if fno_ratio > 0.6 else 'medium' if fno_ratio > 0.4 else 'low' if fno_ratio > 0.2 else 'none'
        score = max(0, min(100, 100 - round(fno_ratio * 80)))

        return {
            'detected': detected, 'severity': sev, 'score': score,
            'fno_ratio': round(fno_ratio * 100, 1),
            'fno_count': len(fno_trades), 'equity_count': len(equity_trades),
            'fno_pnl': round(fno_pnl, 2), 'equity_pnl': round(eq_pnl, 2),
            'label': 'Leverage / Options Risk', 'icon': 'fas fa-layer-group', 'color': '#f97316',
            'description': (
                f'{round(fno_ratio * 100)}% of your trades are in F&O/derivatives. '
                f'F&O P&L: ₹{round(fno_pnl):,} | Equity P&L: ₹{round(eq_pnl):,}.'
            ) if fno_ratio > 0 else 'No derivatives trading detected.',
            'insight': 'High exposure to derivatives increases risk' if detected else 'Derivatives usage is within safe limits.',
            'advice': 'Limit F&O to 20-30% of your trading capital. Options decay quickly and leverage amplifies losses.',
        }

    def detect_drawdown_sensitivity(self):
        trades = self._get_trades()
        if len(trades) < 10:
            return {
                'detected': False, 'severity': 'none', 'score': 50,
                'label': 'Drawdown Sensitivity', 'icon': 'fas fa-arrow-down', 'color': '#dc2626',
                'description': 'Not enough data to analyze drawdown response.',
                'insight': '', 'advice': '', 'recovery_pattern': 'unknown',
            }

        weekly = defaultdict(lambda: {'count': 0, 'pnl': 0.0})
        for t in trades:
            week_key = t.exit_time.strftime('%Y-W%U')
            weekly[week_key]['count'] += 1
            weekly[week_key]['pnl'] += t.realized_pnl

        weeks = sorted(weekly.items())
        activity_drops = 0
        for i in range(1, len(weeks)):
            prev_pnl = weeks[i - 1][1]['pnl']
            curr_count = weeks[i][1]['count']
            prev_count = weeks[i - 1][1]['count']
            if prev_pnl < 0 and curr_count < prev_count * 0.5 and prev_count >= 3:
                activity_drops += 1

        detected = activity_drops >= 2
        sev = 'medium' if activity_drops >= 3 else 'low' if activity_drops >= 1 else 'none'
        score = max(0, 100 - activity_drops * 15)

        return {
            'detected': detected, 'severity': sev, 'score': score,
            'activity_drops': activity_drops,
            'recovery_pattern': 'withdraws' if detected else 'resilient',
            'label': 'Drawdown Sensitivity', 'icon': 'fas fa-arrow-down', 'color': '#dc2626',
            'description': (
                f'You reduce activity sharply after drawdowns — {activity_drops} week(s) of significant pullback in trading.'
            ) if detected else 'You maintain consistent activity through drawdowns.',
            'insight': 'You reduce activity sharply after drawdowns' if detected else 'Resilient response to drawdowns.',
            'advice': 'Drawdowns are normal. Have a pre-planned response: reduce size slightly but stay engaged with the market.',
        }

    def detect_tilt(self):
        trades = self._get_trades()
        tilt_count = 0
        i = 0
        while i < len(trades):
            if trades[i].trade_result != 'LOSS':
                i += 1
                continue
            j = i + 1
            while j < len(trades) and trades[j].trade_result == 'LOSS':
                j += 1
            streak = j - i
            if streak >= 2 and j < len(trades):
                last_size = trades[j - 1].quantity * trades[j - 1].entry_price
                next_size = trades[j].quantity * trades[j].entry_price
                if next_size > last_size * (1 + self.TILT_SIZE_INCREASE):
                    tilt_count += 1
            i = j

        sev = 'high' if tilt_count >= 3 else 'medium' if tilt_count >= 1 else 'none'
        return {
            'detected': tilt_count > 0, 'count': tilt_count, 'severity': sev,
            'score': max(0, 100 - tilt_count * 20),
            'label': 'Position Size Tilt', 'icon': 'fas fa-chart-bar', 'color': '#d53f8c',
            'description': (
                f'Found {tilt_count} instance(s) where you significantly increased position size after consecutive losses.'
            ) if tilt_count > 0 else 'No position sizing tilt detected.',
            'insight': 'Martingale-style size increase after losses detected' if tilt_count > 0 else 'No tilt behavior.',
            'advice': 'Never increase trade size to recover losses. Stick to fixed position sizing rules.',
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # C. PORTFOLIO BEHAVIOR MODULES (3)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── F&O helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_fo_underlying(symbol):
        """Strip expiry/strike from an F&O symbol and return the underlying name.
        E.g. 'NIFTY 24APR25 CE 22000' → 'NIFTY', 'BANKNIFTY01MAY25PE45000' → 'BANKNIFTY'
        """
        import re as _re
        if not symbol:
            return symbol or 'UNKNOWN'
        s = str(symbol).upper().strip()
        # Pattern: UNDERLYING <space or no space> 2-digit-day 3-letter-month 2-4-digit-year
        m = _re.match(r'^([A-Z&]+)\s*\d{2}[A-Z]{3}\d{2,4}', s)
        if m:
            return m.group(1)
        # Pattern: UNDERLYING <space> CE/PE/FUT/CALL/PUT <space> strike
        m2 = _re.match(r'^([A-Z&]+)\s+(?:CE|PE|FUT|CALL|PUT)\s+\d', s)
        if m2:
            return m2.group(1)
        # Pattern: UNDERLYING followed by digits (no space compact format)
        m3 = _re.match(r'^([A-Z]{4,})\d', s)
        if m3:
            return m3.group(1)
        return s.split()[0] if ' ' in s else s

    @staticmethod
    def _is_fo_symbol(symbol):
        """Return True if the symbol looks like an F&O contract."""
        import re as _re
        if not symbol:
            return False
        s = str(symbol).upper()
        return bool(
            _re.search(r'\d{2}[A-Z]{3}\d{2,4}\s*(CE|PE|FUT)', s) or
            _re.search(r'\s+(CE|PE|FUT|CALL|PUT)\s+\d', s) or
            _re.search(r'(CE|PE|FUT)\d{4,}', s)
        )

    def get_diversification_score(self):
        holdings = self._get_holdings()
        trades = self._get_trades()

        equity_symbols = set()
        equity_sectors = set()
        fo_underlyings = set()
        fo_count = 0
        total_count = 0

        if holdings:
            for h in holdings:
                total_count += 1
                at = getattr(h, 'asset_type', '') or ''
                sym = (getattr(h, 'ticker_symbol', '') or getattr(h, 'symbol', '')
                       or getattr(h, 'name', '') or '')
                # Detect F&O by asset_type or by is_futures_options() or by symbol pattern
                is_fo = (
                    at == 'futures_options'
                    or (hasattr(h, 'is_futures_options') and h.is_futures_options())
                    or self._is_fo_symbol(sym)
                )
                if is_fo:
                    fo_count += 1
                    fo_underlyings.add(self._extract_fo_underlying(sym))
                else:
                    equity_symbols.add(sym)
                    sector = (getattr(h, 'sector', None)
                              or getattr(h, 'asset_class', None)
                              or at or 'Other')
                    equity_sectors.add(sector)
        else:
            total_count = len(trades)
            for t in trades:
                at = getattr(t, 'asset_type', 'STOCK') or 'STOCK'
                sym = getattr(t, 'symbol', '') or ''
                is_fo = (
                    at.upper() in ('OPTION', 'FUTURES', 'FUT', 'OPT', 'CE', 'PE')
                    or self._is_fo_symbol(sym)
                )
                if is_fo:
                    fo_count += 1
                    fo_underlyings.add(self._extract_fo_underlying(sym))
                else:
                    equity_symbols.add(sym)

        fo_pct = round(fo_count / max(total_count, 1) * 100)
        is_fo_heavy = fo_pct >= 60  # majority F&O

        if is_fo_heavy:
            n_underlying = len(fo_underlyings)
            underlying_list = ', '.join(sorted(fo_underlyings)) if fo_underlyings else 'Unknown'
            # Each F&O portfolio is concentrated — score by underlying count only
            if n_underlying <= 1:
                score, label = 15, 'High Risk — Single Underlying'
            elif n_underlying <= 3:
                score, label = 25, 'High Risk — F&O Concentrated'
            else:
                score, label = 35, 'High Risk — F&O Portfolio'

            return {
                'score': score, 'label_text': label,
                'num_stocks': fo_count,       # total contracts (for display)
                'num_underlying': n_underlying,
                'num_sectors': 1,             # F&O = 1 asset class regardless of expiries/strikes
                'fo_pct': fo_pct,
                'fo_underlyings': sorted(fo_underlyings),
                'is_fo_heavy': True,
                'label': 'Diversification Score', 'icon': 'fas fa-exclamation-triangle',
                'color': '#ef4444',
                'description': (
                    f'{fo_count} F&O contracts on {n_underlying} underlying(s) '
                    f'({underlying_list}) — {label}.'
                ),
                'insight': (
                    f'You have {fo_count} contracts but only {n_underlying} underlying(s). '
                    f'Multiple expiry/strike combinations on the same index DO NOT diversify risk — '
                    f'they all move together when {underlying_list} moves.'
                ),
                'advice': (
                    f'F&O contracts on {underlying_list} are all correlated to the same underlying. '
                    f'To genuinely diversify, add equity holdings, mutual funds, or bonds to your portfolio. '
                    f'Current effective asset classes: 1 (Derivatives). Target: 3–5.'
                ),
            }

        # Standard equity/mixed portfolio scoring
        # Treat each unique F&O underlying as 1 additional "diversified" asset
        num_stocks = len(equity_symbols) + len(fo_underlyings)
        num_sectors = len(equity_sectors) if equity_sectors else max(1, num_stocks // 3)
        if fo_underlyings:
            num_sectors = len(equity_sectors | {'Derivatives/F&O'})

        if num_stocks >= 15 and num_sectors >= 5:
            score, label = 85, 'Well Diversified'
        elif num_stocks >= 10 and num_sectors >= 3:
            score, label = 65, 'Moderately Diversified'
        elif num_stocks >= 5:
            score, label = 45, 'Under-Diversified'
        else:
            score, label = 25, 'Highly Concentrated'

        return {
            'score': score, 'label_text': label,
            'num_stocks': num_stocks, 'num_sectors': num_sectors,
            'is_fo_heavy': False,
            'fo_pct': fo_pct,
            'fo_underlyings': sorted(fo_underlyings),
            'label': 'Diversification Score', 'icon': 'fas fa-th', 'color': '#10b981',
            'description': f'{num_stocks} assets across {num_sectors} sectors — {label}.',
            'insight': f'Portfolio contains {num_stocks} assets across {num_sectors} sectors.',
            'advice': 'Aim for 15-25 stocks across 5+ sectors for optimal diversification without over-diversifying.',
        }

    def get_portfolio_churn(self):
        trades = self._get_trades()
        if len(trades) < 3:
            return {
                'score': 50, 'churn_rate': 0, 'weekly_changes': 0,
                'label': 'Portfolio Churn Rate', 'icon': 'fas fa-sync-alt', 'color': '#06b6d4',
                'description': 'Not enough data to measure churn.',
                'insight': 'Insufficient data.', 'advice': '',
            }

        weeks = defaultdict(set)
        for t in trades:
            week_key = t.entry_time.strftime('%Y-W%U')
            weeks[week_key].add(t.symbol)

        total_symbols = set(t.symbol for t in trades)
        avg_weekly_changes = sum(len(s) for s in weeks.values()) / max(len(weeks), 1)
        churn_rate = round(avg_weekly_changes / max(len(total_symbols), 1) * 100, 1)

        high_churn = churn_rate > 60
        score = max(0, min(100, 100 - max(0, round(churn_rate - 30))))

        return {
            'score': score, 'churn_rate': churn_rate,
            'weekly_changes': round(avg_weekly_changes, 1),
            'total_unique_symbols': len(total_symbols),
            'label': 'Portfolio Churn Rate', 'icon': 'fas fa-sync-alt', 'color': '#06b6d4',
            'description': f'{churn_rate}% weekly churn — {"high turnover, increasing costs" if high_churn else "reasonable turnover"}.',
            'insight': f'{churn_rate}% of portfolio changes weekly' if churn_rate > 0 else 'Stable portfolio.',
            'advice': 'High churn increases transaction costs and taxes. Hold positions longer when your thesis remains valid.' if high_churn else '',
        }

    def get_capital_efficiency(self):
        trades = self._get_trades()
        if not trades:
            return {
                'score': 50, 'roi': 0, 'capital_deployed': 0, 'total_returns': 0,
                'label': 'Capital Allocation Efficiency', 'icon': 'fas fa-coins', 'color': '#8b5cf6',
                'description': 'No trade data.', 'insight': '', 'advice': '',
                'winning_capital_pct': 0, 'losing_capital_pct': 0,
            }

        total_capital = sum(abs(t.quantity * t.entry_price) for t in trades if t.entry_price and t.quantity)
        total_returns = sum(t.realized_pnl for t in trades)
        roi = round(total_returns / total_capital * 100, 2) if total_capital > 0 else 0

        win_capital = sum(abs(t.quantity * t.entry_price) for t in trades if t.trade_result == 'WIN' and t.entry_price and t.quantity)
        loss_capital = sum(abs(t.quantity * t.entry_price) for t in trades if t.trade_result == 'LOSS' and t.entry_price and t.quantity)
        win_cap_pct = round(win_capital / total_capital * 100, 1) if total_capital > 0 else 0
        loss_cap_pct = round(loss_capital / total_capital * 100, 1) if total_capital > 0 else 0

        score = min(100, max(0, 50 + round(roi * 5)))

        return {
            'score': score, 'roi': roi,
            'capital_deployed': round(total_capital),
            'total_returns': round(total_returns, 2),
            'winning_capital_pct': win_cap_pct, 'losing_capital_pct': loss_cap_pct,
            'label': 'Capital Allocation Efficiency', 'icon': 'fas fa-coins', 'color': '#8b5cf6',
            'description': f'ROI: {roi}% on ₹{round(total_capital):,} deployed. {win_cap_pct}% capital went to winning trades.',
            'insight': f'Capital return on investment is {roi}%.',
            'advice': 'Allocate more capital to high-conviction setups where your win rate is highest.' if roi < 0 else 'Good capital deployment efficiency.',
        }

    def get_portfolio_intelligence(self):
        """Churn cost, top insights, trend, behavioral link — all in one call."""
        from models import TradeHistory, ManualTradeImport
        now = datetime.utcnow()
        period_start = now - timedelta(days=30)
        prev_start = now - timedelta(days=60)
        prev_end = now - timedelta(days=30)

        def period_trades(start, end):
            th = TradeHistory.query.filter_by(user_id=self.user_id, tenant_id=self.tenant_id)\
                 .filter(TradeHistory.entry_time.between(start, end)).all()
            mi = ManualTradeImport.query.filter_by(user_id=self.user_id, tenant_id=self.tenant_id)\
                 .filter(ManualTradeImport.entry_time.between(start, end)).all()
            return th + mi

        cur_trades = period_trades(period_start, now)
        prev_trades = period_trades(prev_start, prev_end)

        # ── Churn Cost Impact ────────────────────────────────────────────────
        n_trades = len(cur_trades)
        avg_brokerage_per_side = 20
        brokerage_cost = n_trades * avg_brokerage_per_side * 2   # buy + sell
        sell_value = sum(abs(t.quantity * (t.exit_price or 0)) for t in cur_trades if t.quantity and t.exit_price)
        stt = round(sell_value * 0.00025)   # ~0.025% STT on sell side
        other_charges = round(n_trades * 8)  # SEBI fees, stamp, etc.
        total_monthly_cost = brokerage_cost + stt + other_charges
        cur_pnl = sum(t.realized_pnl for t in cur_trades)
        cost_as_pct_pnl = round(total_monthly_cost / max(abs(cur_pnl), 1) * 100) if cur_pnl else 100

        # ── Portfolio Metrics (current vs previous) ──────────────────────────
        def metrics(trades):
            total = len(trades)
            wins = sum(1 for t in trades if t.trade_result == 'WIN')
            pnl = sum(t.realized_pnl for t in trades)
            capital = sum(abs(t.quantity * (t.entry_price or 0)) for t in trades if t.quantity and t.entry_price)
            symbols = set(t.symbol for t in trades)
            weeks = defaultdict(set)
            for t in trades:
                weeks[t.entry_time.strftime('%Y-W%U')].add(t.symbol)
            avg_weekly = sum(len(s) for s in weeks.values()) / max(len(weeks), 1)
            churn = round(avg_weekly / max(len(symbols), 1) * 100, 1)
            win_cap = sum(abs(t.quantity * (t.entry_price or 0)) for t in trades if t.trade_result == 'WIN' and t.quantity and t.entry_price)
            win_cap_pct = round(win_cap / max(capital, 1) * 100, 1)
            roi = round(pnl / max(capital, 1) * 100, 2)
            return {
                'trades': total, 'wins': wins,
                'win_rate': round(wins / max(total, 1) * 100, 1),
                'pnl': round(pnl, 2), 'capital': round(capital),
                'roi': roi, 'churn': churn,
                'win_cap_pct': win_cap_pct,
                'symbols': len(symbols),
            }

        cur_m = metrics(cur_trades)
        prev_m = metrics(prev_trades) if prev_trades else None

        trends = {}
        if prev_m:
            trends = {
                'churn': round(cur_m['churn'] - prev_m['churn'], 1),
                'roi': round(cur_m['roi'] - prev_m['roi'], 2),
                'win_rate': round(cur_m['win_rate'] - prev_m['win_rate'], 1),
                'trades': cur_m['trades'] - prev_m['trades'],
                'win_cap_pct': round(cur_m['win_cap_pct'] - prev_m['win_cap_pct'], 1),
            }

        # ── Top 3 Insights ───────────────────────────────────────────────────
        div = self.get_diversification_score()
        churn = self.get_portfolio_churn()
        cap_eff = self.get_capital_efficiency()

        insights = []
        if churn['churn_rate'] > 60:
            insights.append({
                'type': 'danger',
                'icon': 'fas fa-exclamation-circle',
                'text': f"High churn ({churn['churn_rate']}%) is eroding returns through transaction costs.",
                'action': 'Reduce trade frequency. Hold for longer when your thesis is intact.',
            })
        elif churn['churn_rate'] > 30:
            insights.append({
                'type': 'warning',
                'icon': 'fas fa-exclamation-triangle',
                'text': f"Moderate churn ({churn['churn_rate']}%) — watch transaction cost buildup.",
                'action': 'Each round trip costs ~₹48+ in brokerage and taxes.',
            })
        if cap_eff['winning_capital_pct'] < 50:
            insights.append({
                'type': 'warning',
                'icon': 'fas fa-exclamation-triangle',
                'text': f"Only {cap_eff['winning_capital_pct']}% of your capital went to winning trades.",
                'action': 'Shift capital toward your highest win-rate instruments.',
            })
        if cap_eff['roi'] < 0:
            insights.append({
                'type': 'danger',
                'icon': 'fas fa-arrow-down',
                'text': f"Negative ROI ({cap_eff['roi']}%) — trading is currently destroying capital.",
                'action': 'Reduce position sizing until your strategy improves.',
            })
        elif cap_eff['roi'] > 5:
            insights.append({
                'type': 'success',
                'icon': 'fas fa-check-circle',
                'text': f"Strong {cap_eff['roi']}% ROI on capital deployed — excellent efficiency.",
                'action': 'Scale up your best-performing instruments.',
            })
        if div.get('is_fo_heavy'):
            underlying_str = ', '.join(div.get('fo_underlyings', [])) or 'Unknown'
            n_u = div.get('num_underlying', 1)
            fo_pct = div.get('fo_pct', 100)
            insights.append({
                'type': 'danger',
                'icon': 'fas fa-exclamation-circle',
                'text': (
                    f"{fo_pct}% of your portfolio is F&O contracts on {n_u} underlying(s) "
                    f"({underlying_str}). Multiple expiries/strikes are NOT diversification — they all correlate."
                ),
                'action': (
                    'Add equity, mutual funds, or bonds to genuinely diversify. '
                    'F&O positions on the same index share 80–100% correlation.'
                ),
            })
        elif div['num_stocks'] >= 10:
            insights.append({
                'type': 'success',
                'icon': 'fas fa-check-circle',
                'text': f"Good diversification — {div['num_stocks']} assets across {div['num_sectors']} sectors.",
                'action': 'Maintain this diversification as you scale.',
            })
        elif div['num_stocks'] < 5:
            insights.append({
                'type': 'danger',
                'icon': 'fas fa-exclamation-circle',
                'text': f"Concentrated portfolio with only {div['num_stocks']} assets — high single-asset risk.",
                'action': 'Aim for at least 10 uncorrelated instruments.',
            })
        if trends.get('churn', 0) > 15:
            insights.append({
                'type': 'warning',
                'icon': 'fas fa-arrow-up',
                'text': f"Churn increased +{trends['churn']}% vs last month while ROI changed {trends['roi']:+.1f}%.",
                'action': 'More trading is not correlating with better results.',
            })

        # Keep top 3, prioritize danger > warning > success
        priority = {'danger': 0, 'warning': 1, 'success': 2}
        insights = sorted(insights, key=lambda x: priority.get(x['type'], 3))[:3]

        # ── Behavioral-Portfolio Link ─────────────────────────────────────────
        try:
            overtrade = self.detect_overtrading()
            revenge = self.detect_revenge_trading()
        except Exception:
            overtrade = {'detected': False, 'severity': 'none'}
            revenge = {'detected': False, 'severity': 'none'}

        beh_link = []
        if overtrade.get('detected') and cap_eff['roi'] < 3:
            beh_link.append({
                'behavior': 'Overtrading',
                'impact': f"High trade frequency ({n_trades} trades/month) is directly reducing your ROI to {cap_eff['roi']}%.",
                'color': '#ef4444',
                'icon': 'fas fa-fire',
            })
        if revenge.get('detected') and churn['churn_rate'] > 50:
            beh_link.append({
                'behavior': 'Revenge Trading',
                'impact': 'Loss-triggered trades are increasing churn and compounding losses.',
                'color': '#f59e0b',
                'icon': 'fas fa-bolt',
            })
        if not beh_link and cap_eff['roi'] > 3:
            beh_link.append({
                'behavior': 'Disciplined Trading',
                'impact': f"Your controlled trading frequency ({n_trades} trades/month) is supporting a {cap_eff['roi']}% ROI.",
                'color': '#22c55e',
                'icon': 'fas fa-medal',
            })

        return {
            'cost_impact': {
                'n_trades': n_trades,
                'brokerage': brokerage_cost,
                'stt': stt,
                'other': other_charges,
                'total': total_monthly_cost,
                'cost_pct_pnl': cost_as_pct_pnl,
                'cur_pnl': round(cur_pnl, 2),
            },
            'insights': insights,
            'trends': trends,
            'cur_metrics': cur_m,
            'prev_metrics': prev_m,
            'has_trend': bool(prev_m and prev_m['trades'] > 0),
            'beh_link': beh_link,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # D. PERFORMANCE PATTERNS (3)
    # ═══════════════════════════════════════════════════════════════════════════

    def get_win_rate_analysis(self):
        trades = self._get_trades()
        if not trades:
            return {
                'score': 50, 'win_rate': 0, 'total': 0, 'wins': 0, 'losses': 0,
                'streak_data': [], 'best_streak': 0, 'worst_streak': 0,
                'label': 'Win Rate Analysis', 'icon': 'fas fa-percentage', 'color': '#22c55e',
                'description': 'No trade data.', 'insight': '', 'advice': '',
                'by_hour': [], 'by_day': [], 'by_symbol': [],
            }

        total = len(trades)
        wins = sum(1 for t in trades if t.trade_result == 'WIN')
        losses = sum(1 for t in trades if t.trade_result == 'LOSS')
        win_rate = round(wins / total * 100, 1) if total > 0 else 0

        streaks = []
        current_type = None
        current_len = 0
        for t in trades:
            if t.trade_result == current_type:
                current_len += 1
            else:
                if current_type:
                    streaks.append({'type': current_type, 'length': current_len})
                current_type = t.trade_result
                current_len = 1
        if current_type:
            streaks.append({'type': current_type, 'length': current_len})

        win_streaks = [s['length'] for s in streaks if s['type'] == 'WIN']
        loss_streaks = [s['length'] for s in streaks if s['type'] == 'LOSS']
        best_streak = max(win_streaks) if win_streaks else 0
        worst_streak = max(loss_streaks) if loss_streaks else 0

        score = min(100, max(0, round(win_rate * 1.2)))

        return {
            'score': score, 'win_rate': win_rate, 'total': total, 'wins': wins, 'losses': losses,
            'breakeven': total - wins - losses,
            'best_streak': best_streak, 'worst_streak': worst_streak,
            'streak_data': streaks[-20:],
            'label': 'Win Rate Analysis', 'icon': 'fas fa-percentage', 'color': '#22c55e',
            'description': f'{win_rate}% win rate across {total} trades. Best winning streak: {best_streak}. Worst losing streak: {worst_streak}.',
            'insight': f'{win_rate}% profitable trades' if total > 0 else '',
            'advice': 'A win rate above 50% with positive risk-reward is the sweet spot. Focus on quality setups.',
            'by_hour': self.get_win_rate_by_hour(),
            'by_day': self.get_win_rate_by_day(),
            'by_symbol': self.get_win_rate_by_symbol(),
        }

    def get_risk_reward_analysis(self):
        trades = self._get_trades()
        win_pnl = [t.realized_pnl for t in trades if t.trade_result == 'WIN']
        loss_pnl = [t.realized_pnl for t in trades if t.trade_result == 'LOSS']

        avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        max_win = max(win_pnl) if win_pnl else 0
        max_loss = min(loss_pnl) if loss_pnl else 0
        total_pnl = sum(t.realized_pnl for t in trades)

        pnl_distribution = []
        for t in trades:
            pnl_distribution.append({
                'symbol': t.symbol,
                'pnl': round(t.realized_pnl, 2),
                'result': t.trade_result,
                'date': t.exit_time.strftime('%d %b') if t.exit_time else '',
            })

        score = min(100, max(0, round(rr * 40)))

        return {
            'score': score, 'risk_reward': round(rr, 2),
            'avg_win': round(avg_win, 2), 'avg_loss': round(avg_loss, 2),
            'max_win': round(max_win, 2), 'max_loss': round(max_loss, 2),
            'total_pnl': round(total_pnl, 2),
            'pnl_distribution': pnl_distribution[-30:],
            'label': 'Risk-Reward Ratio', 'icon': 'fas fa-balance-scale-right', 'color': '#3b82f6',
            'description': f'Risk-Reward: {round(rr, 2)}:1. Avg win ₹{round(avg_win):,} vs avg loss ₹{round(abs(avg_loss)):,}.',
            'insight': f'For every ₹1 you risk, you make ₹{round(rr, 1)}' if rr > 0 else 'No risk-reward data.',
            'advice': 'Aim for at least 2:1 risk-reward. Never enter a trade where potential loss exceeds potential gain.' if rr < 2 else 'Excellent risk-reward discipline.',
        }

    def get_strategy_consistency(self):
        trades = self._get_trades()
        if len(trades) < 10:
            return {
                'score': 50, 'consistency_pct': 0,
                'label': 'Strategy Consistency', 'icon': 'fas fa-route', 'color': '#6366f1',
                'description': 'Not enough data for consistency analysis.',
                'insight': '', 'advice': '', 'weekly_win_rates': [],
            }

        weekly = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in trades:
            wk = t.entry_time.strftime('%Y-W%U')
            weekly[wk]['total'] += 1
            if t.trade_result == 'WIN':
                weekly[wk]['wins'] += 1

        weekly_wrs = []
        for wk in sorted(weekly.keys()):
            d = weekly[wk]
            wr = round(d['wins'] / d['total'] * 100) if d['total'] > 0 else 0
            weekly_wrs.append({'week': wk, 'win_rate': wr, 'trades': d['total']})

        wrs = [w['win_rate'] for w in weekly_wrs if w['trades'] >= 2]
        if len(wrs) >= 3:
            wr_std = statistics.stdev(wrs)
            consistency = max(0, 100 - round(wr_std))
        else:
            consistency = 50

        score = consistency

        return {
            'score': score, 'consistency_pct': consistency,
            'weekly_win_rates': weekly_wrs[-12:],
            'label': 'Strategy Consistency', 'icon': 'fas fa-route', 'color': '#6366f1',
            'description': f'Strategy consistency: {consistency}%. {"Stable approach" if consistency > 60 else "Volatile — strategy may be changing frequently"}.',
            'insight': 'Consistent strategy execution' if consistency > 60 else 'Strategy appears random or inconsistent.',
            'advice': 'Pick one strategy and master it. Constantly switching approaches prevents you from learning what works.' if consistency < 50 else '',
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # E. PSYCHOLOGICAL PATTERNS (5)
    # ═══════════════════════════════════════════════════════════════════════════

    def detect_panic_selling(self):
        trades = self._get_trades()
        panics = [
            t for t in trades
            if t.exit_reason == 'MANUAL'
            and t.trade_result == 'LOSS'
            and t.holding_period_hours <= self.PANIC_SELL_HOURS
        ]
        count = len(panics)
        sev = 'high' if count >= 5 else 'medium' if count >= 3 else 'low' if count >= 1 else 'none'
        score = max(0, 100 - count * 12)

        return {
            'detected': count > 0, 'count': count, 'severity': sev, 'score': score,
            'label': 'Panic Selling Detection', 'icon': 'fas fa-running', 'color': '#e53e3e',
            'description': f'Manually exited {count} trade(s) within 2h at a loss.' if count > 0 else 'No panic selling detected.',
            'insight': 'You sell during short-term declines out of fear' if count > 0 else 'No panic-driven exits.',
            'advice': 'Set stop-losses before entering. Manual exits driven by fear happen at the worst price.',
        }

    def detect_overconfidence(self):
        trades = self._get_trades()
        count = 0
        i = 0
        while i < len(trades):
            if trades[i].trade_result != 'WIN':
                i += 1
                continue
            j = i + 1
            while j < len(trades) and trades[j].trade_result == 'WIN':
                j += 1
            streak = j - i
            if streak >= 3 and j < len(trades):
                last_size = trades[j - 1].quantity * trades[j - 1].entry_price
                next_size = trades[j].quantity * trades[j].entry_price
                if next_size > last_size * (1 + self.OVERCONF_SIZE_INCREASE):
                    count += 1
            i = j

        sev = 'medium' if count >= 2 else 'low' if count >= 1 else 'none'
        score = max(0, 100 - count * 15)

        return {
            'detected': count > 0, 'count': count, 'severity': sev, 'score': score,
            'label': 'FOMO / Overconfidence', 'icon': 'fas fa-trophy', 'color': '#d69e2e',
            'description': (
                f'Found {count} instance(s) of sharply increased position size after winning streaks.'
            ) if count > 0 else 'No overconfidence bias detected.',
            'insight': 'You tend to buy bigger after winning streaks (FOMO/overconfidence)' if count > 0 else 'Good size discipline after wins.',
            'advice': 'A winning streak can create false confidence. Consistent sizing protects capital when the streak ends.',
        }

    def detect_behavioral_drift(self):
        trades = self._get_trades()
        if len(trades) < 20:
            return {
                'detected': False, 'severity': 'none', 'score': 50,
                'label': 'Behavioral Drift', 'icon': 'fas fa-wind', 'color': '#64748b',
                'description': 'Need 20+ trades to detect behavioral drift.',
                'insight': '', 'advice': '', 'drift_data': [],
            }

        half = len(trades) // 2
        first_half = trades[:half]
        second_half = trades[half:]

        def calc_metrics(group):
            wins = sum(1 for t in group if t.trade_result == 'WIN')
            wr = wins / len(group) * 100 if group else 0
            avg_size = statistics.mean(
                [t.quantity * t.entry_price for t in group if t.entry_price and t.quantity]
            ) if group else 0
            avg_hold = statistics.mean(
                [t.holding_period_hours for t in group if t.holding_period_hours]
            ) if group else 0
            return {'win_rate': round(wr, 1), 'avg_size': round(avg_size), 'avg_hold': round(avg_hold, 1)}

        m1 = calc_metrics(first_half)
        m2 = calc_metrics(second_half)

        wr_change = abs(m2['win_rate'] - m1['win_rate'])
        size_change = abs(m2['avg_size'] - m1['avg_size']) / max(m1['avg_size'], 1) * 100
        hold_change = abs(m2['avg_hold'] - m1['avg_hold']) / max(m1['avg_hold'], 0.1) * 100

        drift_score = (wr_change + size_change / 5 + hold_change / 5)
        detected = drift_score > 20
        sev = 'high' if drift_score > 40 else 'medium' if drift_score > 20 else 'none'
        score = max(0, min(100, 100 - round(drift_score)))

        return {
            'detected': detected, 'severity': sev, 'score': score,
            'first_half': m1, 'second_half': m2,
            'wr_change': round(wr_change, 1), 'size_change': round(size_change, 1),
            'drift_data': [m1, m2],
            'label': 'Behavioral Drift', 'icon': 'fas fa-wind', 'color': '#64748b',
            'description': (
                f'Your strategy is shifting — win rate changed by {round(wr_change)}pp, '
                f'position size by {round(size_change)}%.'
            ) if detected else 'Trading behavior is consistent over time.',
            'insight': 'Your strategy is changing over time' if detected else 'Consistent behavioral patterns.',
            'advice': 'Track what changed. Was it intentional improvement or emotional drift? Journal your strategy changes.',
        }

    def detect_time_of_day_bias(self):
        trades = self._get_trades()
        by_hour = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0.0})
        for t in trades:
            h = t.entry_time.hour
            by_hour[h]['total'] += 1
            by_hour[h]['pnl'] += t.realized_pnl
            if t.trade_result == 'WIN':
                by_hour[h]['wins'] += 1
            elif t.trade_result == 'LOSS':
                by_hour[h]['losses'] += 1

        hourly = []
        best_hour = None
        worst_hour = None
        best_pnl = -float('inf')
        worst_pnl = float('inf')
        for h in range(9, 16):
            d = by_hour.get(h, {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0.0})
            wr = round(d['wins'] / d['total'] * 100) if d['total'] > 0 else 0
            hourly.append({
                'hour': f"{h:02d}:00", 'total': d['total'], 'wins': d['wins'],
                'losses': d['losses'], 'win_rate': wr, 'pnl': round(d['pnl'], 2),
            })
            if d['total'] >= 3:
                if d['pnl'] > best_pnl:
                    best_pnl = d['pnl']
                    best_hour = f"{h:02d}:00"
                if d['pnl'] < worst_pnl:
                    worst_pnl = d['pnl']
                    worst_hour = f"{h:02d}:00"

        pnl_range = best_pnl - worst_pnl if best_hour and worst_hour else 0
        has_bias = pnl_range > 0 and worst_pnl < 0

        return {
            'detected': has_bias, 'severity': 'medium' if has_bias else 'none',
            'score': max(0, min(100, 70 if has_bias else 80)),
            'hourly_data': hourly, 'best_hour': best_hour, 'worst_hour': worst_hour,
            'best_pnl': round(best_pnl, 2) if best_hour else 0,
            'worst_pnl': round(worst_pnl, 2) if worst_hour else 0,
            'label': 'Time-of-Day Bias', 'icon': 'fas fa-clock', 'color': '#0ea5e9',
            'description': (
                f'Best hour: {best_hour} (₹{round(best_pnl):,}). Worst: {worst_hour} (₹{round(worst_pnl):,}).'
            ) if best_hour else 'Not enough data for time analysis.',
            'insight': f'You perform best at {best_hour} and worst at {worst_hour}' if best_hour else '',
            'advice': f'Consider avoiding trades at {worst_hour} — your track record is consistently negative at this time.' if has_bias and worst_hour else '',
        }

    def detect_broker_bias(self):
        orders = self._get_orders()
        if not orders:
            return {
                'detected': False, 'severity': 'none', 'score': 80,
                'label': 'Broker Bias', 'icon': 'fas fa-building', 'color': '#a855f7',
                'description': 'No multi-broker data available.',
                'insight': 'Connect multiple brokers to detect cross-broker behavioral patterns.',
                'advice': '', 'broker_stats': {},
            }

        by_broker = defaultdict(lambda: {'trades': 0, 'total_value': 0.0})
        for o in orders:
            broker_id = getattr(o, 'broker_account_id', 0)
            by_broker[broker_id]['trades'] += 1
            val = (getattr(o, 'quantity', 0) or 0) * (getattr(o, 'price', 0) or getattr(o, 'avg_execution_price', 0) or 0)
            by_broker[broker_id]['total_value'] += val

        if len(by_broker) < 2:
            return {
                'detected': False, 'severity': 'none', 'score': 80,
                'label': 'Broker Bias', 'icon': 'fas fa-building', 'color': '#a855f7',
                'description': 'Only one broker connected — need 2+ brokers to detect bias.',
                'insight': 'Single broker in use.', 'advice': '', 'broker_stats': {},
            }

        avg_values = [d['total_value'] / d['trades'] for d in by_broker.values() if d['trades'] > 0]
        if len(avg_values) >= 2:
            max_avg = max(avg_values)
            min_avg = min(avg_values)
            ratio = max_avg / min_avg if min_avg > 0 else 1
            detected = ratio > 1.5
        else:
            detected = False
            ratio = 1

        sev = 'medium' if detected else 'none'
        score = max(0, min(100, 100 - round((ratio - 1) * 30)))

        return {
            'detected': detected, 'severity': sev, 'score': score,
            'broker_count': len(by_broker), 'size_ratio': round(ratio, 1),
            'broker_stats': {str(k): v for k, v in by_broker.items()},
            'label': 'Broker Bias', 'icon': 'fas fa-building', 'color': '#a855f7',
            'description': f'You take {round(ratio, 1)}x larger positions on one broker vs another.' if detected else 'No significant broker bias detected.',
            'insight': 'You take more risk on one broker vs another' if detected else 'Consistent behavior across brokers.',
            'advice': 'Apply the same risk rules regardless of which broker you trade on. Emotional separation by broker can lead to hidden risk.',
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # NEW INTELLIGENCE MODULES
    # ═══════════════════════════════════════════════════════════════════════════

    def get_behavior_timeline(self, days=30):
        """Group trades by day for the 30-day behavior timeline chart."""
        from models import TradeHistory, ManualTradeImport
        since = datetime.utcnow() - timedelta(days=days)
        trades = sorted(
            list(TradeHistory.query.filter_by(user_id=self.user_id, tenant_id=self.tenant_id)
                 .filter(TradeHistory.entry_time >= since).all()) +
            list(ManualTradeImport.query.filter_by(user_id=self.user_id, tenant_id=self.tenant_id)
                 .filter(ManualTradeImport.entry_time >= since).all()),
            key=lambda t: t.entry_time
        )

        by_day = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0.0, 'sizes': []})
        for t in trades:
            day = t.entry_time.strftime('%Y-%m-%d')
            by_day[day]['trades'] += 1
            by_day[day]['pnl'] += t.realized_pnl
            if t.trade_result == 'WIN':
                by_day[day]['wins'] += 1
            size = (getattr(t, 'quantity', 0) or 0) * (getattr(t, 'entry_price', 0) or 0)
            by_day[day]['sizes'].append(size)

        if not by_day:
            return []

        avg_daily = sum(d['trades'] for d in by_day.values()) / len(by_day)
        max_size = max(
            (max(d['sizes']) if d['sizes'] else 0) for d in by_day.values()
        ) or 1
        overall_avg_size = max_size * 0.5

        timeline = []
        for day in sorted(by_day.keys()):
            d = by_day[day]
            avg_size = sum(d['sizes']) / len(d['sizes']) if d['sizes'] else 0
            event = None
            if d['trades'] > avg_daily * 1.5 and d['pnl'] < 0:
                event = 'High activity after loss'
            elif d['trades'] > avg_daily * 1.5:
                event = 'High trading activity'
            elif d['pnl'] < -5000:
                event = 'Heavy loss day'
            elif avg_size > overall_avg_size * 1.5:
                event = 'Large position day'
            timeline.append({
                'date': day,
                'label': datetime.strptime(day, '%Y-%m-%d').strftime('%d %b'),
                'trades': d['trades'],
                'wins': d['wins'],
                'pnl': round(d['pnl'], 2),
                'avg_size': round(avg_size),
                'event': event,
            })
        return timeline

    def get_cross_broker_intelligence(self):
        """Per-broker win rate, avg size, frequency — the cross-broker USP."""
        try:
            from models_broker import BrokerAccount, BrokerOrder
        except ImportError:
            return {'brokers': [], 'insight': 'No broker data available.', 'detected': False}

        trades = self._get_trades()
        orders = self._get_orders()

        by_broker = defaultdict(lambda: {
            'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'sizes': []
        })

        # From TradeHistory/ManualTradeImport — keyed by broker_name
        for t in trades:
            bn = getattr(t, 'broker_name', 'Unknown') or 'Unknown'
            by_broker[bn]['trades'] += 1
            by_broker[bn]['pnl'] += t.realized_pnl
            if t.trade_result == 'WIN':
                by_broker[bn]['wins'] += 1
            elif t.trade_result == 'LOSS':
                by_broker[bn]['losses'] += 1
            size = (getattr(t, 'quantity', 0) or 0) * (getattr(t, 'entry_price', 0) or 0)
            by_broker[bn]['sizes'].append(size)

        # From BrokerOrder — map account_id → broker name
        if orders:
            try:
                accounts = {
                    a.id: a.broker_name
                    for a in BrokerAccount.query.filter_by(user_id=self.user_id).all()
                }
                for o in orders:
                    bn = accounts.get(getattr(o, 'broker_account_id', 0), 'Unknown')
                    size = (getattr(o, 'quantity', 0) or 0) * (
                        getattr(o, 'price', 0) or getattr(o, 'avg_execution_price', 0) or 0
                    )
                    by_broker[bn]['sizes'].append(size)
                    by_broker[bn]['trades'] += 1
            except Exception:
                pass

        if len(by_broker) < 2:
            return {
                'brokers': [],
                'insight': 'Connect multiple brokers to unlock cross-broker intelligence.',
                'detected': False,
            }

        avg_sizes = {bn: (sum(d['sizes']) / len(d['sizes']) if d['sizes'] else 0)
                     for bn, d in by_broker.items()}
        max_avg = max(avg_sizes.values()) if avg_sizes else 1
        min_avg = min(avg_sizes.values()) if avg_sizes else 1

        brokers = []
        for bn, d in sorted(by_broker.items(), key=lambda x: x[1]['trades'], reverse=True):
            total = d['trades'] or 1
            wr = round(d['wins'] / total * 100)
            avg_s = avg_sizes.get(bn, 0)
            ratio = avg_s / min_avg if min_avg > 0 else 1
            if ratio >= 1.8:
                risk_level = 'High Risk'
                risk_color = '#ef4444'
            elif ratio >= 1.3:
                risk_level = 'Moderate Risk'
                risk_color = '#f59e0b'
            else:
                risk_level = 'Low Risk'
                risk_color = '#22c55e'
            brokers.append({
                'broker': bn,
                'trades': d['trades'],
                'win_rate': wr,
                'avg_size': round(avg_s),
                'pnl': round(d['pnl'], 2),
                'risk_level': risk_level,
                'risk_color': risk_color,
                'size_ratio': round(ratio, 1),
            })

        if max_avg > min_avg * 1.3:
            high_risk_broker = max(avg_sizes, key=avg_sizes.get)
            low_risk_broker = min(avg_sizes, key=avg_sizes.get)
            ratio = round(max_avg / min_avg, 1) if min_avg > 0 else 1
            insight = f'You take {ratio}x larger positions on {high_risk_broker} vs {low_risk_broker}. This suggests different risk tolerance across platforms.'
            detected = True
        else:
            insight = 'Your risk behavior is consistent across all brokers. This is a positive sign of disciplined trading.'
            detected = False

        return {'brokers': brokers, 'insight': insight, 'detected': detected}

    def get_today_alerts(self):
        """Real-time alerts based on today's trading activity."""
        from models import TradeHistory, ManualTradeImport
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        all_trades = self._get_trades()
        today_trades = [t for t in all_trades if t.entry_time >= today_start]
        alerts = []

        # Daily average from last 30 days
        days_with_trades = set(t.entry_time.date() for t in all_trades)
        avg_daily = len(all_trades) / max(len(days_with_trades), 1)

        # Alert 1: Overtrading today
        if len(today_trades) > avg_daily * 1.5 and len(today_trades) >= 3:
            alerts.append({
                'type': 'overtrading', 'severity': 'warning',
                'icon': 'fas fa-bolt', 'color': '#f59e0b',
                'message': f"You've placed {len(today_trades)} trades today — {round(len(today_trades)/avg_daily, 1)}x your daily average.",
                'action': 'Consider pausing and reviewing before placing more orders.',
            })

        # Alert 2: Loss streak today
        if len(today_trades) >= 3:
            recent = today_trades[-3:]
            if all(t.trade_result == 'LOSS' for t in recent):
                alerts.append({
                    'type': 'loss_streak', 'severity': 'danger',
                    'icon': 'fas fa-exclamation-circle', 'color': '#ef4444',
                    'message': 'You have 3 consecutive losses today. Revenge trading risk is high.',
                    'action': 'Take a break. Emotional trading after losses often worsens performance.',
                })

        # Alert 3: Recent win streak — overconfidence risk
        if len(today_trades) >= 3:
            recent = today_trades[-3:]
            if all(t.trade_result == 'WIN' for t in recent):
                sizes = [(getattr(t, 'quantity', 0) or 0) * (getattr(t, 'entry_price', 0) or 0)
                         for t in today_trades]
                if len(sizes) >= 2 and sizes[-1] > sizes[-2] * 1.3:
                    alerts.append({
                        'type': 'overconfidence', 'severity': 'info',
                        'icon': 'fas fa-trophy', 'color': '#3b82f6',
                        'message': 'Win streak + growing position size detected. Overconfidence risk.',
                        'action': 'Winning streaks end. Maintain consistent position sizing.',
                    })

        # Alert 4: No trades today (positive — check if normally active)
        if len(today_trades) == 0 and avg_daily >= 3 and now.weekday() < 5:
            pass  # Positive — don't alarm

        return alerts

    def get_score_breakdown(self):
        """Break master score into Discipline, Risk, Timing, Psychology."""
        cats = {
            'trading': self.get_trading_behavior(),
            'risk': self.get_risk_behavior(),
            'portfolio': self.get_portfolio_behavior(),
            'performance': self.get_performance_patterns(),
            'psychology': self.get_psychology_patterns(),
        }
        discipline = round((cats['trading']['score'] + cats['performance']['score']) / 2)
        risk_score = round((cats['risk']['score'] + cats['portfolio']['score']) / 2)
        timing_score = cats['trading']['modules'].get('trade_timing', {}).get('score', 50)
        psych_score = cats['psychology']['score']
        total = round(discipline * 0.3 + risk_score * 0.25 + timing_score * 0.2 + psych_score * 0.25)
        return {
            'total': total,
            'discipline': discipline,
            'risk': risk_score,
            'timing': timing_score,
            'psychology': psych_score,
        }

    def get_progress_tracking(self):
        """Compare this month vs last month for key metrics."""
        from models import TradeHistory, ManualTradeImport
        now = datetime.utcnow()
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = this_month_start - timedelta(seconds=1)
        last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def fetch(start, end):
            th = TradeHistory.query.filter_by(user_id=self.user_id, tenant_id=self.tenant_id)\
                 .filter(TradeHistory.exit_time.between(start, end)).all()
            mi = ManualTradeImport.query.filter_by(user_id=self.user_id, tenant_id=self.tenant_id)\
                 .filter(ManualTradeImport.exit_time.between(start, end)).all()
            return th + mi

        cur = fetch(this_month_start, now)
        prev = fetch(last_month_start, last_month_end)

        def metrics(trades):
            total = len(trades)
            wins = sum(1 for t in trades if t.trade_result == 'WIN')
            pnl = sum(t.realized_pnl for t in trades)
            wr = round(wins / total * 100, 1) if total else 0
            return {'total': total, 'win_rate': wr, 'pnl': round(pnl, 2)}

        cur_m = metrics(cur)
        prev_m = metrics(prev)

        wr_delta = round(cur_m['win_rate'] - prev_m['win_rate'], 1)
        trades_delta = cur_m['total'] - prev_m['total']
        pnl_delta = round(cur_m['pnl'] - prev_m['pnl'], 2)

        return {
            'current': cur_m,
            'previous': prev_m,
            'wr_delta': wr_delta,
            'trades_delta': trades_delta,
            'pnl_delta': pnl_delta,
            'improving': wr_delta > 0,
            'has_prev': len(prev) > 0,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # STATISTICAL HELPERS (for existing compatibility)
    # ═══════════════════════════════════════════════════════════════════════════

    def get_win_rate_by_hour(self):
        trades = self._get_trades()
        by_hour = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in trades:
            h = t.entry_time.hour
            by_hour[h]['total'] += 1
            if t.trade_result == 'WIN':
                by_hour[h]['wins'] += 1
        result = []
        for h in range(9, 16):
            d = by_hour.get(h, {'wins': 0, 'total': 0})
            result.append({
                'hour': f"{h:02d}:00", 'total': d['total'], 'wins': d['wins'],
                'win_rate': round(d['wins'] / d['total'] * 100) if d['total'] > 0 else 0,
            })
        return result

    def get_win_rate_by_day(self):
        trades = self._get_trades()
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        by_day = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in trades:
            by_day[t.entry_time.weekday()]['total'] += 1
            if t.trade_result == 'WIN':
                by_day[t.entry_time.weekday()]['wins'] += 1
        result = []
        for d in range(5):
            data = by_day.get(d, {'wins': 0, 'total': 0})
            result.append({
                'day': day_names[d], 'day_short': day_names[d][:3],
                'total': data['total'], 'wins': data['wins'],
                'win_rate': round(data['wins'] / data['total'] * 100) if data['total'] > 0 else 0,
            })
        return result

    def get_win_rate_by_symbol(self):
        trades = self._get_trades()
        by_symbol = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0.0})
        for t in trades:
            by_symbol[t.symbol]['total'] += 1
            by_symbol[t.symbol]['pnl'] += t.realized_pnl
            if t.trade_result == 'WIN':
                by_symbol[t.symbol]['wins'] += 1
            elif t.trade_result == 'LOSS':
                by_symbol[t.symbol]['losses'] += 1
        result = []
        for sym, data in sorted(by_symbol.items(), key=lambda x: x[1]['total'], reverse=True)[:10]:
            result.append({
                'symbol': sym, 'total': data['total'], 'wins': data['wins'],
                'losses': data['losses'],
                'win_rate': round(data['wins'] / data['total'] * 100) if data['total'] > 0 else 0,
                'pnl': round(data['pnl'], 2),
            })
        return result

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY-LEVEL ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════

    def get_trading_behavior(self):
        modules = {
            'overtrading': self.detect_overtrading(),
            'revenge_trading': self.detect_revenge_trading(),
            'profit_booking': self.detect_profit_booking_bias(),
            'loss_aversion': self.detect_loss_aversion(),
            'trade_timing': self.detect_trade_timing(),
        }
        scores = [m['score'] for m in modules.values() if 'score' in m]
        avg = round(sum(scores) / len(scores)) if scores else 50
        return {'modules': modules, 'score': avg, **CATEGORY_META['trading']}

    def get_risk_behavior(self):
        modules = {
            'position_sizing': self.detect_position_sizing_consistency(),
            'overexposure': self.detect_overexposure(),
            'leverage_risk': self.detect_leverage_risk(),
            'drawdown_sensitivity': self.detect_drawdown_sensitivity(),
        }
        scores = [m['score'] for m in modules.values() if 'score' in m]
        avg = round(sum(scores) / len(scores)) if scores else 50
        return {'modules': modules, 'score': avg, **CATEGORY_META['risk']}

    def get_portfolio_behavior(self):
        modules = {
            'diversification': self.get_diversification_score(),
            'churn': self.get_portfolio_churn(),
            'capital_efficiency': self.get_capital_efficiency(),
        }
        scores = [m['score'] for m in modules.values() if 'score' in m]
        avg = round(sum(scores) / len(scores)) if scores else 50
        return {'modules': modules, 'score': avg, **CATEGORY_META['portfolio']}

    def get_performance_patterns(self):
        modules = {
            'win_rate': self.get_win_rate_analysis(),
            'risk_reward': self.get_risk_reward_analysis(),
            'strategy_consistency': self.get_strategy_consistency(),
        }
        scores = [m['score'] for m in modules.values() if 'score' in m]
        avg = round(sum(scores) / len(scores)) if scores else 50
        return {'modules': modules, 'score': avg, **CATEGORY_META['performance']}

    def get_psychology_patterns(self):
        modules = {
            'panic_selling': self.detect_panic_selling(),
            'fomo': self.detect_overconfidence(),
            'behavioral_drift': self.detect_behavioral_drift(),
            'time_of_day': self.detect_time_of_day_bias(),
            'broker_bias': self.detect_broker_bias(),
        }
        scores = [m['score'] for m in modules.values() if 'score' in m]
        avg = round(sum(scores) / len(scores)) if scores else 50
        return {'modules': modules, 'score': avg, **CATEGORY_META['psychology']}

    # ═══════════════════════════════════════════════════════════════════════════
    # MASTER BEHAVIORAL SCORE
    # ═══════════════════════════════════════════════════════════════════════════

    def get_master_score(self, categories):
        weights = {
            'trading': 0.25,
            'risk': 0.20,
            'portfolio': 0.15,
            'performance': 0.15,
            'psychology': 0.25,
        }
        weighted = sum(
            categories.get(cat, {}).get('score', 50) * w
            for cat, w in weights.items()
        )
        score = round(weighted)

        if score >= 80:
            category = 'Disciplined'
            color = '#22c55e'
        elif score >= 65:
            category = 'Developing'
            color = '#3b82f6'
        elif score >= 45:
            category = 'Needs Work'
            color = '#f59e0b'
        else:
            category = 'Critical'
            color = '#ef4444'

        return {
            'score': score,
            'category': category,
            'color': color,
            'weights': weights,
        }

    def get_trading_personality(self, patterns, score):
        revenge = patterns.get('revenge_trading', {}).get('detected', False)
        overtrading = patterns.get('overtrading', {}).get('detected', False)
        tilt = patterns.get('tilt', {}).get('detected', False)
        loss_av = patterns.get('loss_aversion', {}).get('detected', False)

        if revenge and overtrading:
            return {
                'type': 'Emotional Trader', 'icon': 'fas fa-fire', 'color': '#e53e3e',
                'description': 'You trade reactively — especially after losses. This is the most common pattern among retail traders who lose consistently.',
                'strength': 'Quick decision-making, high energy',
                'weakness': 'Emotions override strategy',
            }
        if loss_av and tilt:
            return {
                'type': 'Fearful Gambler', 'icon': 'fas fa-dice', 'color': '#dd6b20',
                'description': 'You hold losers too long hoping they recover, then bet bigger after losses to "win it back".',
                'strength': 'Patience with positions',
                'weakness': 'Holding losers and doubling down after losses',
            }
        if revenge and not overtrading:
            return {
                'type': 'Revenge Trader', 'icon': 'fas fa-redo', 'color': '#d53f8c',
                'description': 'You tend to jump back after losses to immediately recover. This often turns small losses into large ones.',
                'strength': 'Resilient, determined',
                'weakness': 'Trades without proper setup after a loss',
            }
        if score >= 80:
            return {
                'type': 'Disciplined Trader', 'icon': 'fas fa-chess', 'color': '#38a169',
                'description': 'Your behaviour shows strong discipline and emotional control. This is rare and valuable.',
                'strength': 'Rule-based, consistent, emotionally stable',
                'weakness': 'May be overly cautious on high-conviction setups',
            }
        if score >= 60:
            return {
                'type': 'Developing Trader', 'icon': 'fas fa-seedling', 'color': '#3182ce',
                'description': 'You are building good trading habits with a few areas to strengthen.',
                'strength': 'Self-aware and improving',
                'weakness': 'Occasional emotional decisions',
            }
        return {
            'type': 'Impulse Trader', 'icon': 'fas fa-random', 'color': '#e53e3e',
            'description': 'Decisions appear largely driven by impulse rather than a consistent plan.',
            'strength': 'Quick to act on opportunities',
            'weakness': 'Lacks systematic approach',
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # CROSS-MODULE INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════════════════

    def get_trading_dna(self):
        """Classify the user into a trading archetype from all modules combined."""
        trades = self._get_trades()
        n = len(trades)
        if n < 5:
            return {'has_data': False}

        win_pnl = [t.realized_pnl for t in trades if t.trade_result == 'WIN']
        loss_pnl = [t.realized_pnl for t in trades if t.trade_result == 'LOSS']
        avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        win_rate = sum(1 for t in trades if t.trade_result == 'WIN') / n * 100
        total_pnl = sum(t.realized_pnl for t in trades)

        now = datetime.utcnow()
        month_trades = [t for t in trades if (now - t.entry_time).days <= 30]
        monthly_count = len(month_trades)
        activity = 'High' if monthly_count >= 20 else 'Medium' if monthly_count >= 8 else 'Low'

        revenge = self.detect_revenge_trading()
        overtrade = self.detect_overtrading()
        panic = self.detect_panic_selling()
        drift = self.detect_behavioral_drift()
        overconf = self.detect_overconfidence()
        loss_av = self.detect_loss_aversion()

        emotional_score = sum([
            3 if revenge.get('detected') else 0,
            2 if overtrade.get('detected') else 0,
            2 if panic.get('detected') else 0,
            1 if drift.get('detected') else 0,
            1 if overconf.get('detected') else 0,
            1 if loss_av.get('detected') else 0,
        ])
        emotional_level = 'High' if emotional_score >= 4 else 'Medium' if emotional_score >= 2 else 'Low'
        rr_quality = 'Good' if rr >= 1.5 else 'Fair' if rr >= 0.9 else 'Poor'

        if activity == 'High' and rr_quality == 'Poor' and emotional_level in ('High', 'Medium'):
            archetype = 'High-Activity, Low-Reward Trader'
            summary = f'You trade frequently ({monthly_count}/month) but with a poor {round(rr, 1)}:1 risk-reward, which compounds losses over time.'
            strength = 'Quick decision-making and high market engagement'
            weakness = f'Low risk-reward ({round(rr, 1)}:1) negates high trading frequency'
            key_issue = 'Low risk-reward'
            priority_fix = 'Cut losers earlier and let winners run — target 1.5:1 minimum'
        elif emotional_level == 'High' and rr_quality == 'Poor':
            archetype = 'Emotional Reactive Trader'
            summary = 'Multiple emotional patterns detected — revenge trading, panic, or drift — combined with poor risk-reward creates a compounding loss engine.'
            strength = 'High market awareness and quick execution'
            weakness = 'Emotions consistently override strategy'
            key_issue = 'Emotional trading patterns'
            priority_fix = 'Address emotional triggers before improving technical strategy'
        elif rr_quality == 'Good' and activity in ('Medium', 'High') and emotional_level == 'Low':
            archetype = 'Disciplined Systematic Trader'
            summary = f'Good risk-reward ({round(rr, 1)}:1) with disciplined emotional control. Primary focus: improve win rate and scale up.'
            strength = 'Emotional control and strong risk-reward discipline'
            weakness = 'Win rate improvement needed' if win_rate < 50 else 'Maintain consistency while scaling'
            key_issue = 'Optimize win rate'
            priority_fix = 'Focus on high-probability setups and position sizing'
        elif loss_av.get('detected') and panic.get('detected'):
            archetype = 'Fearful Speculator'
            summary = 'You hold losing positions too long (loss aversion) but panic-exit others early — the worst combination of both fear responses.'
            strength = 'Patience on some positions'
            weakness = 'Fear distorts hold times in both directions'
            key_issue = 'Inconsistent fear response'
            priority_fix = 'Set mechanical stop-losses to remove emotion from exit decisions'
        elif activity == 'Low' and emotional_level == 'Low' and rr_quality != 'Good':
            archetype = 'Cautious Underperformer'
            summary = f'Low trading frequency with poor risk-reward ({round(rr, 1)}:1) — careful but not yet capturing enough upside per trade.'
            strength = 'Controlled, low-frequency approach'
            weakness = f'Risk-reward of {round(rr, 1)}:1 needs improvement before scaling'
            key_issue = 'Risk-reward too low'
            priority_fix = 'Keep trade count low, but improve position exit discipline'
        elif drift.get('detected') and overconf.get('detected'):
            archetype = 'Overconfident Drifter'
            summary = 'Your strategy keeps changing (drift) and winning streaks push you toward oversized positions — a volatile and costly combination.'
            strength = 'Adaptable and energetic market participant'
            weakness = 'No stable strategy + oversizing on winning streaks'
            key_issue = 'Strategy instability'
            priority_fix = 'Commit to one strategy for 3 months before evaluating'
        else:
            archetype = 'Developing Systematic Trader'
            summary = 'You are building trading habits with some inconsistencies. A clear pattern has not fully emerged — this is a critical decision point.'
            strength = 'Self-awareness and willingness to improve'
            weakness = 'Needs consistent strategy and risk-reward improvement'
            key_issue = 'Consistency'
            priority_fix = 'Pick one strategy, track it rigorously, and review weekly'

        return {
            'has_data': True,
            'archetype': archetype,
            'summary': summary,
            'strength': strength,
            'weakness': weakness,
            'key_issue': key_issue,
            'priority_fix': priority_fix,
            'activity': activity,
            'monthly_trades': monthly_count,
            'rr': round(rr, 1),
            'rr_quality': rr_quality,
            'win_rate': round(win_rate, 1),
            'emotional_level': emotional_level,
            'total_pnl': round(total_pnl, 2),
            'detected_patterns': {
                'revenge': revenge.get('detected'),
                'overtrade': overtrade.get('detected'),
                'panic': panic.get('detected'),
                'drift': drift.get('detected'),
                'overconf': overconf.get('detected'),
                'loss_aversion': loss_av.get('detected'),
            },
        }

    def get_cross_module_correlations(self):
        """Detect causal chains between behavior, performance, and psychology."""
        trades = self._get_trades()
        if len(trades) < 5:
            return []

        win_pnl = [t.realized_pnl for t in trades if t.trade_result == 'WIN']
        loss_pnl = [t.realized_pnl for t in trades if t.trade_result == 'LOSS']
        avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        total_pnl = sum(t.realized_pnl for t in trades)
        n = len(trades)
        win_rate = sum(1 for t in trades if t.trade_result == 'WIN') / n * 100

        overtrade = self.detect_overtrading()
        revenge = self.detect_revenge_trading()
        panic = self.detect_panic_selling()
        drift = self.detect_behavioral_drift()
        overconf = self.detect_overconfidence()
        loss_av = self.detect_loss_aversion()

        correlations = []

        if overtrade.get('detected') and rr < 1:
            correlations.append({
                'type': 'critical',
                'cause': 'Overtrading',
                'cause_module': 'Behavior',
                'effect': f'Low risk-reward ({round(rr, 1)}:1)',
                'effect_module': 'Performance',
                'outcome': 'Net losses from rushed exits',
                'chain': f'High trading frequency forces rushed exits — cutting winners short while letting losers run. This compresses your R:R to {round(rr, 1)}:1 and makes profitability mathematically impossible.',
                'fix': 'Limit to your top 2–3 setups per day. Quality over quantity.',
                'icon': 'fas fa-fire',
                'color': '#ef4444',
            })

        if revenge.get('detected') and total_pnl < 0:
            correlations.append({
                'type': 'critical',
                'cause': 'Revenge Trading',
                'cause_module': 'Psychology',
                'effect': 'Amplified losses',
                'effect_module': 'Performance',
                'outcome': f'Net P&L ₹{round(total_pnl):,}',
                'chain': 'Each loss triggers an unplanned recovery trade. These revenge trades have lower win rates and are entered without proper setups — each one compounds the original loss.',
                'fix': 'After any loss, implement a mandatory 30-minute break. No exceptions.',
                'icon': 'fas fa-redo',
                'color': '#ef4444',
            })

        if panic.get('detected') and loss_av.get('detected'):
            correlations.append({
                'type': 'critical',
                'cause': 'Panic Selling + Loss Aversion',
                'cause_module': 'Psychology',
                'effect': 'Inverted hold times',
                'effect_module': 'Performance',
                'outcome': 'Winners cut short, losers held long',
                'chain': 'You exit winning trades quickly (panic) but hold losing trades too long hoping they recover (loss aversion). This directly destroys risk-reward and is the most common retail trading mistake.',
                'fix': 'Set mechanical stop-loss and take-profit levels before every entry. Remove the decision from the moment.',
                'icon': 'fas fa-random',
                'color': '#ef4444',
            })

        if drift.get('detected') and win_rate < 45:
            correlations.append({
                'type': 'warning',
                'cause': 'Behavioral Drift',
                'cause_module': 'Psychology',
                'effect': f'Low win rate ({round(win_rate, 1)}%)',
                'effect_module': 'Performance',
                'outcome': 'Inconsistent results',
                'chain': f'Strategy kept changing — win rate shifted {drift.get("wr_change", 0)}pp between your early and recent trades. Switching strategies prevents you from learning which approach actually works.',
                'fix': 'Commit to one strategy for a minimum of 50 trades before evaluating its effectiveness.',
                'icon': 'fas fa-wind',
                'color': '#f59e0b',
            })

        if overconf.get('detected') and rr < 1.5:
            correlations.append({
                'type': 'warning',
                'cause': 'Overconfidence after wins',
                'cause_module': 'Psychology',
                'effect': 'Oversized losing positions',
                'effect_module': 'Portfolio',
                'outcome': 'Large drawdowns after win streaks',
                'chain': 'After winning streaks, position size increases significantly — exactly when mean reversion is most likely. These oversized trades produce outsized losses that offset multiple small wins.',
                'fix': 'Fix position size as a fixed % of capital. Never adjust it based on recent performance.',
                'icon': 'fas fa-trophy',
                'color': '#f59e0b',
            })

        if not correlations and rr >= 1.5 and win_rate >= 50:
            correlations.append({
                'type': 'success',
                'cause': 'Disciplined Behavior',
                'cause_module': 'All Modules',
                'effect': f'Good R:R ({round(rr, 1)}:1) + {round(win_rate, 1)}% win rate',
                'effect_module': 'Performance',
                'outcome': 'Positive expectancy',
                'chain': 'Your behavioral discipline — low emotional trading and consistent strategy — is directly contributing to a positive expectancy per trade. This is the foundation for compounding.',
                'fix': 'Protect this by maintaining position sizing discipline and not deviating from your strategy during drawdowns.',
                'icon': 'fas fa-medal',
                'color': '#22c55e',
            })

        return correlations[:4]

    def get_performance_root_cause(self):
        """Root cause, fix priority, and potential upside for the performance screen."""
        trades = self._get_trades()
        if len(trades) < 5:
            return None

        win_pnl = [t.realized_pnl for t in trades if t.trade_result == 'WIN']
        loss_pnl = [t.realized_pnl for t in trades if t.trade_result == 'LOSS']
        avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        n = len(trades)
        wins = sum(1 for t in trades if t.trade_result == 'WIN')
        win_rate = wins / n * 100
        total_pnl = sum(t.realized_pnl for t in trades)
        breakeven_rr = (1 - win_rate / 100) / (win_rate / 100) if win_rate > 0 else 99
        breakeven_wr = (1 / (1 + rr)) * 100 if rr > 0 else 99

        if rr < 1 and total_pnl < 0:
            root_cause = {
                'label': 'Low Risk-Reward Ratio',
                'detail': f'Your {round(rr, 1)}:1 R:R means you need to win {round(breakeven_wr)}%+ of trades to break even — but you\'re winning {round(win_rate)}%.',
                'severity': 'critical',
            }
            fix_priority = f'Fix risk-reward before working on win rate. R:R must exceed {round(breakeven_rr, 1)}:1 to make your current {round(win_rate)}% win rate profitable.'
            if avg_loss != 0:
                target_rr = 1.5
                target_avg_win = abs(avg_loss) * target_rr
                cur_exp = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss
                tgt_exp = win_rate / 100 * target_avg_win + (1 - win_rate / 100) * avg_loss
                if cur_exp < 0 and tgt_exp > 0:
                    potential_upside = f'Improving R:R from {round(rr, 1)} → 1.5 flips expectancy from ₹{round(cur_exp):,} to ₹{round(tgt_exp):+,} per trade.'
                elif cur_exp != 0:
                    mult = round(abs(tgt_exp / cur_exp), 1)
                    potential_upside = f'Improving R:R from {round(rr, 1)} → 1.5 could increase expected return by approximately {mult}x per trade.'
                else:
                    potential_upside = 'Even a modest improvement in risk-reward will have a compounding positive effect on P&L.'
            else:
                potential_upside = 'Improving risk-reward from current level to 1.5:1 is achievable by setting tighter stop-losses and wider take-profits.'

        elif win_rate < 40 and total_pnl < 0:
            root_cause = {
                'label': 'Low Win Rate',
                'detail': f'Only {round(win_rate)}% of trades are profitable. With your {round(rr, 1)}:1 R:R, you need at least {round(breakeven_wr)}% wins to break even.',
                'severity': 'critical',
            }
            fix_priority = f'Focus on trade selection quality — fewer, higher-quality setups targeting {round(breakeven_wr) + 5}%+ win rate'
            tgt_pnl = (0.5 * avg_win + 0.5 * avg_loss) * n
            potential_upside = f'Improving win rate from {round(win_rate)}% → 50% could generate ₹{round(tgt_pnl):,} in expected P&L across {n} trades.'

        elif rr < 0.8 and win_rate < 55:
            root_cause = {
                'label': 'Compound Weakness — Both Metrics Below Threshold',
                'detail': f'{round(win_rate)}% win rate AND {round(rr, 1)}:1 R:R are both below minimum thresholds — compounding each other and accelerating losses.',
                'severity': 'critical',
            }
            fix_priority = 'Address risk-reward first (faster to improve), then win rate — fixing exits is easier than fixing entries'
            potential_upside = 'Improving either metric by 30% will significantly improve profitability. Both are learnable with the right framework.'

        elif total_pnl >= 0 and rr >= 1.5:
            root_cause = {
                'label': 'No Critical Issue — Strategy Working',
                'detail': f'Positive P&L of ₹{round(total_pnl):,} with {round(rr, 1)}:1 R:R and {round(win_rate)}% win rate. Focus is now on consistency and scaling.',
                'severity': 'good',
            }
            fix_priority = 'Maintain and scale — do not deviate from what is working. Focus on consistency before increasing position size.'
            potential_upside = f'Scaling to 20% larger positions on your top-conviction setups could increase monthly P&L by ~20% without additional risk.'

        else:
            root_cause = {
                'label': 'Suboptimal Risk-Reward',
                'detail': f'Risk-reward of {round(rr, 1)}:1 is below the ideal 2:1. Performance will improve quickly as this improves.',
                'severity': 'warning',
            }
            fix_priority = 'Target 1.5:1 minimum R:R — cut losers at -1R, let winners run to +2R before exiting'
            potential_upside = f'Moving average R:R from {round(rr, 1)} → 2.0 could increase returns by 40–60% with the same win rate.'

        return {
            'root_cause': root_cause,
            'fix_priority': fix_priority,
            'potential_upside': potential_upside,
            'win_rate': round(win_rate, 1),
            'rr': round(rr, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'total_pnl': round(total_pnl, 2),
            'breakeven_rr': round(breakeven_rr, 2),
            'breakeven_wr': round(breakeven_wr, 1),
        }

    def get_psychology_narratives(self):
        """Emotional narrative + trigger detection + self-awareness for each psychology module."""
        trades = self._get_trades()
        narratives = {}

        drift = self.detect_behavioral_drift()
        panic = self.detect_panic_selling()
        overconf = self.detect_overconfidence()
        loss_av = self.detect_loss_aversion()

        if drift.get('detected'):
            half = len(trades) // 2
            trigger_trade = trades[half] if half < len(trades) else None
            trigger_date = trigger_trade.entry_time.strftime('%d %b %Y') if trigger_trade else 'mid-period'
            wr_change = drift.get('wr_change', 0)
            size_change = round(drift.get('size_change', 0))
            narratives['behavioral_drift'] = {
                'narrative': f'Your trading changed significantly around {trigger_date} — win rate shifted {wr_change}pp and position size changed {size_change}%. This suggests emotional decisions (not strategic adjustments) began overriding your original plan.',
                'trigger': f'Behavioral shift detected starting around {trigger_date}.',
                'self_awareness': 'Was this change intentional (planned strategy update) or emotional (reacting to a losing streak)? If unplanned, this is behavioral drift — the silent capital destroyer.',
            }

        if panic.get('detected'):
            panics = sorted(
                [t for t in trades if t.exit_reason == 'MANUAL' and t.trade_result == 'LOSS' and t.holding_period_hours <= 2],
                key=lambda x: x.exit_time, reverse=True
            )
            trigger_date = panics[0].exit_time.strftime('%d %b %Y') if panics else 'recently'
            narratives['panic_selling'] = {
                'narrative': f'You exited {panic["count"]} trade(s) within 2 hours at a loss — driven by fear of further downside. The most recent panic exit was on {trigger_date}. These exits typically happen at the worst possible price, locking in losses that would have recovered.',
                'trigger': f'Most recent panic exit: {trigger_date}.',
                'self_awareness': 'Ask yourself: Was the original thesis for this trade actually invalidated — or were you just uncomfortable with an unrealized loss?',
            }

        if overconf.get('detected'):
            narratives['fomo'] = {
                'narrative': f'After winning streaks, your position size jumped significantly ({overconf["count"]} instance(s)). This is classic overconfidence — past wins feel like proof of skill, but markets do not reward yesterday\'s results.',
                'trigger': 'Triggered after consecutive winning trades — each streak makes the next oversized bet more likely.',
                'self_awareness': 'Would you take the same position size if your last 3 trades were losses? If not, emotions are driving your sizing decisions — not strategy.',
            }

        if loss_av.get('detected'):
            avg_loss_hold = loss_av.get('avg_loss_hold', 0)
            avg_win_hold = loss_av.get('avg_win_hold', 0)
            narratives['loss_aversion'] = {
                'narrative': f'You hold losing positions {avg_loss_hold:.1f}h on average vs {avg_win_hold:.1f}h for winners. This asymmetry — cutting winners early and holding losers long — is loss aversion in action. It directly destroys your risk-reward ratio.',
                'trigger': 'Pattern detected across multiple trades — specifically the duration asymmetry between wins and losses.',
                'self_awareness': 'When a stop-loss level is reached, is there a new, specific reason to stay in the trade — or are you just hoping it will recover? Hope is not a strategy.',
            }

        return narratives

    # ═══════════════════════════════════════════════════════════════════════════
    # FULL ANALYSIS (overview)
    # ═══════════════════════════════════════════════════════════════════════════

    def get_full_analysis(self):
        trades = self._get_trades()

        categories = {
            'trading': self.get_trading_behavior(),
            'risk': self.get_risk_behavior(),
            'portfolio': self.get_portfolio_behavior(),
            'performance': self.get_performance_patterns(),
            'psychology': self.get_psychology_patterns(),
        }

        master = self.get_master_score(categories)

        all_modules = {}
        for cat_data in categories.values():
            all_modules.update(cat_data.get('modules', {}))

        patterns = {
            'revenge_trading': all_modules.get('revenge_trading', {}),
            'overtrading': all_modules.get('overtrading', {}),
            'tilt': self.detect_tilt(),
            'loss_aversion': all_modules.get('loss_aversion', {}),
            'panic_selling': all_modules.get('panic_selling', {}),
            'overconfidence': all_modules.get('fomo', {}),
        }

        personality = self.get_trading_personality(patterns, master['score'])

        total = len(trades)
        wins = sum(1 for t in trades if t.trade_result == 'WIN')
        losses = sum(1 for t in trades if t.trade_result == 'LOSS')

        win_pnl = [t.realized_pnl for t in trades if t.trade_result == 'WIN']
        loss_pnl = [t.realized_pnl for t in trades if t.trade_result == 'LOSS']
        avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        active_alerts = sorted(
            [m for m in all_modules.values() if m.get('detected') or m.get('severity') in ('high', 'medium')],
            key=lambda x: SEVERITY_RANK.get(x.get('severity', 'none'), 0),
            reverse=True,
        )

        top_insights = [m.get('insight', '') for m in active_alerts[:5] if m.get('insight')]

        return {
            'score': master['score'],
            'score_label': master['category'],
            'score_color': master['color'],
            'personality': personality,
            'patterns': patterns,
            'active_alerts': active_alerts[:8],
            'top_insights': top_insights,
            'categories': categories,
            'master': master,
            'stats': {
                'total_trades': total,
                'wins': wins,
                'losses': losses,
                'win_rate': round(wins / total * 100, 1) if total > 0 else 0,
                'total_pnl': round(sum(t.realized_pnl for t in trades), 2),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'risk_reward': round(rr, 2),
            },
            'by_hour': self.get_win_rate_by_hour(),
            'by_day': self.get_win_rate_by_day(),
            'by_symbol': self.get_win_rate_by_symbol(),
            'has_data': total >= 5,
        }

    # ── Pre-trade real-time check ─────────────────────────────────────────────

    def pre_trade_check(self):
        trades = self._get_trades()
        now = datetime.utcnow()
        warnings = []

        if not trades:
            return warnings

        last = trades[-1]
        mins_since_last = (now - last.exit_time).total_seconds() / 60

        if last.trade_result == 'LOSS' and mins_since_last <= self.REVENGE_WINDOW_MINS:
            warnings.append({
                'type': 'revenge_trading', 'severity': 'high', 'icon': 'fas fa-fire',
                'message': (
                    f'Your last trade ({last.symbol}) closed as a LOSS just '
                    f'{round(mins_since_last)}m ago. Consider waiting.'
                ),
            })

        today_trades = [t for t in trades if t.entry_time.date() == now.date()]
        if len(today_trades) >= self.OVERTRADE_THRESHOLD:
            warnings.append({
                'type': 'overtrading', 'severity': 'medium', 'icon': 'fas fa-bolt',
                'message': f'You have already placed {len(today_trades)} trades today.',
            })

        recent = trades[-3:] if len(trades) >= 3 else trades
        if len(recent) == 3 and all(t.trade_result == 'LOSS' for t in recent):
            warnings.append({
                'type': 'tilt', 'severity': 'high', 'icon': 'fas fa-chart-bar',
                'message': 'You have 3 consecutive losses. Consider stopping for today.',
            })

        return warnings
