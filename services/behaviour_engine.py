"""
Behavioural AI Engine — Target Capital
Detects emotional and harmful trading patterns from TradeHistory.
Produces a Trading Discipline Score (0-100) and actionable insights.
"""
from datetime import datetime, timedelta
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

SEVERITY_RANK = {'high': 3, 'medium': 2, 'low': 1, 'none': 0}


class BehaviourEngine:
    REVENGE_WINDOW_MINS   = 30
    OVERTRADE_HOURS       = 4
    OVERTRADE_THRESHOLD   = 5
    TILT_SIZE_INCREASE    = 0.25
    LOSS_AVERSION_RATIO   = 1.5
    PANIC_SELL_HOURS      = 2
    OVERCONF_SIZE_INCREASE = 0.30

    def __init__(self, user_id, tenant_id):
        self.user_id   = user_id
        self.tenant_id = tenant_id
        self._trades   = None

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_trades(self, days=90):
        from models import TradeHistory
        since = datetime.utcnow() - timedelta(days=days)
        return (
            TradeHistory.query
            .filter_by(user_id=self.user_id, tenant_id=self.tenant_id)
            .filter(TradeHistory.exit_time >= since)
            .order_by(TradeHistory.exit_time.asc())
            .all()
        )

    def _get_trades(self):
        if self._trades is None:
            self._trades = self._load_trades()
        return self._trades

    # ── Pattern detectors ─────────────────────────────────────────────────────

    def detect_revenge_trading(self):
        trades   = self._get_trades()
        incidents = []

        for i, trade in enumerate(trades):
            if trade.trade_result != 'LOSS':
                continue
            for j in range(i + 1, len(trades)):
                nt  = trades[j]
                gap = (nt.entry_time - trade.exit_time).total_seconds() / 60
                if gap < 0:
                    continue
                if gap > self.REVENGE_WINDOW_MINS:
                    break
                if (nt.quantity * nt.entry_price) >= (trade.quantity * trade.entry_price):
                    incidents.append({
                        'date':          trade.exit_time.strftime('%d %b %Y'),
                        'loss_trade':    trade.symbol,
                        'loss_amount':   abs(round(trade.realized_pnl, 2)),
                        'revenge_trade': nt.symbol,
                        'gap_mins':      round(gap),
                    })

        count = len(incidents)
        sev   = 'high' if count >= 3 else 'medium' if count >= 1 else 'none'
        return {
            'detected':    count > 0,
            'count':       count,
            'incidents':   incidents[-3:],
            'severity':    sev,
            'label':       'Revenge Trading',
            'icon':        'fas fa-fire',
            'color':       '#e53e3e',
            'description': (
                f'You entered {count} trade(s) within {self.REVENGE_WINDOW_MINS} minutes of a loss, '
                'often with a larger position.'
            ) if count > 0 else 'No revenge trading detected in the last 90 days.',
            'advice': (
                'After a loss, step away for at least 30 minutes before your next trade. '
                'Emotional trades almost never recover losses.'
            ),
        }

    def detect_overtrading(self):
        trades = self._get_trades()
        overtrading_days = set()

        for i, trade in enumerate(trades):
            window_end = trade.entry_time + timedelta(hours=self.OVERTRADE_HOURS)
            count = sum(
                1 for t in trades[i:]
                if t.entry_time <= window_end
            )
            if count > self.OVERTRADE_THRESHOLD:
                overtrading_days.add(trade.entry_time.date())

        count = len(overtrading_days)
        sev   = 'high' if count >= 5 else 'medium' if count >= 2 else 'low' if count >= 1 else 'none'
        return {
            'detected':    count > 0,
            'count':       count,
            'severity':    sev,
            'label':       'Overtrading',
            'icon':        'fas fa-bolt',
            'color':       '#dd6b20',
            'description': (
                f'Detected {count} day(s) where you placed more than {self.OVERTRADE_THRESHOLD} '
                f'trades within a {self.OVERTRADE_HOURS}-hour window.'
            ) if count > 0 else 'No overtrading detected in the last 90 days.',
            'advice': (
                'Set a daily trade limit. Quality over quantity — more trades often means more '
                'losses from transaction costs and impulsive decisions.'
            ),
        }

    def detect_tilt(self):
        trades  = self._get_trades()
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
            'detected':    tilt_count > 0,
            'count':       tilt_count,
            'severity':    sev,
            'label':       'Position Size Tilt',
            'icon':        'fas fa-chart-bar',
            'color':       '#d53f8c',
            'description': (
                f'Found {tilt_count} instance(s) where you significantly increased position size '
                'after consecutive losses — a classic "martingale" danger pattern.'
            ) if tilt_count > 0 else 'No position sizing tilt detected in the last 90 days.',
            'advice': (
                'Never increase trade size to recover losses. This is one of the fastest ways '
                'to blow up a trading account. Stick to fixed position sizing rules.'
            ),
        }

    def detect_loss_aversion(self):
        trades  = self._get_trades()
        wins    = [t for t in trades if t.trade_result == 'WIN']
        losses  = [t for t in trades if t.trade_result == 'LOSS']

        if len(wins) < 3 or len(losses) < 3:
            return {
                'detected': False, 'severity': 'none',
                'label': 'Loss Aversion', 'icon': 'fas fa-clock', 'color': '#718096',
                'win_avg_hours': 0, 'loss_avg_hours': 0, 'ratio': 0,
                'description': 'Not enough trade data yet (need 3+ wins and 3+ losses).',
                'advice': '',
            }

        win_avg  = sum(t.holding_period_hours for t in wins)  / len(wins)
        loss_avg = sum(t.holding_period_hours for t in losses) / len(losses)
        ratio    = round(loss_avg / win_avg, 1) if win_avg > 0 else 0
        detected = loss_avg > win_avg * self.LOSS_AVERSION_RATIO

        return {
            'detected':       detected,
            'severity':       'high' if detected and ratio > 3 else 'medium' if detected else 'none',
            'label':          'Loss Aversion',
            'icon':           'fas fa-clock',
            'color':          '#3182ce',
            'win_avg_hours':  round(win_avg, 1),
            'loss_avg_hours': round(loss_avg, 1),
            'ratio':          ratio,
            'description': (
                f'You hold losing trades {ratio}x longer than winning ones '
                f'({round(loss_avg, 1)}h vs {round(win_avg, 1)}h average).'
            ) if detected else (
                f'You exit winners and losers at a healthy ratio '
                f'({round(win_avg, 1)}h wins vs {round(loss_avg, 1)}h losses).'
            ),
            'advice': (
                'Cutting losses quickly and letting winners run is the core of profitable trading. '
                'Use stop-losses on every trade without exception.'
            ),
        }

    def detect_panic_selling(self):
        trades = self._get_trades()
        panics = [
            t for t in trades
            if t.exit_reason == 'MANUAL'
            and t.trade_result == 'LOSS'
            and t.holding_period_hours <= self.PANIC_SELL_HOURS
        ]
        count = len(panics)
        sev   = 'medium' if count >= 3 else 'low' if count >= 1 else 'none'
        return {
            'detected':    count > 0,
            'count':       count,
            'severity':    sev,
            'label':       'Panic Selling',
            'icon':        'fas fa-running',
            'color':       '#e53e3e',
            'description': (
                f'You manually exited {count} trade(s) within 2 hours of entry at a loss.'
            ) if count > 0 else 'No panic selling detected in the last 90 days.',
            'advice': (
                'Set your stop-loss before entering a trade. Manual exits driven by fear '
                'often happen at the worst possible price.'
            ),
        }

    def detect_overconfidence(self):
        trades = self._get_trades()
        count  = 0
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
        return {
            'detected':    count > 0,
            'count':       count,
            'severity':    sev,
            'label':       'Overconfidence Bias',
            'icon':        'fas fa-trophy',
            'color':       '#d69e2e',
            'description': (
                f'Found {count} instance(s) where you sharply increased position size '
                'after a winning streak.'
            ) if count > 0 else 'No overconfidence bias detected in the last 90 days.',
            'advice': (
                'A winning streak can create false confidence. Consistent position sizing '
                'protects your capital when the streak ends.'
            ),
        }

    # ── Statistical analysis ──────────────────────────────────────────────────

    def get_win_rate_by_hour(self):
        trades  = self._get_trades()
        by_hour = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in trades:
            h = t.entry_time.hour
            by_hour[h]['total'] += 1
            if t.trade_result == 'WIN':
                by_hour[h]['wins'] += 1

        result = []
        for h in range(9, 16):  # Indian market hours
            d = by_hour.get(h, {'wins': 0, 'total': 0})
            result.append({
                'hour':     f"{h:02d}:00",
                'total':    d['total'],
                'wins':     d['wins'],
                'win_rate': round(d['wins'] / d['total'] * 100) if d['total'] > 0 else 0,
            })
        return result

    def get_win_rate_by_day(self):
        trades    = self._get_trades()
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        by_day    = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in trades:
            by_day[t.entry_time.weekday()]['total'] += 1
            if t.trade_result == 'WIN':
                by_day[t.entry_time.weekday()]['wins'] += 1

        result = []
        for d in range(5):  # Mon-Fri
            data = by_day.get(d, {'wins': 0, 'total': 0})
            result.append({
                'day':      day_names[d],
                'day_short': day_names[d][:3],
                'total':    data['total'],
                'wins':     data['wins'],
                'win_rate': round(data['wins'] / data['total'] * 100) if data['total'] > 0 else 0,
            })
        return result

    def get_win_rate_by_symbol(self):
        trades    = self._get_trades()
        by_symbol = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0.0})
        for t in trades:
            by_symbol[t.symbol]['total'] += 1
            by_symbol[t.symbol]['pnl']   += t.realized_pnl
            if t.trade_result == 'WIN':
                by_symbol[t.symbol]['wins'] += 1
            elif t.trade_result == 'LOSS':
                by_symbol[t.symbol]['losses'] += 1

        result = []
        for sym, data in sorted(by_symbol.items(), key=lambda x: x[1]['total'], reverse=True)[:10]:
            result.append({
                'symbol':   sym,
                'total':    data['total'],
                'wins':     data['wins'],
                'losses':   data['losses'],
                'win_rate': round(data['wins'] / data['total'] * 100) if data['total'] > 0 else 0,
                'pnl':      round(data['pnl'], 2),
            })
        return result

    # ── Scoring & personality ─────────────────────────────────────────────────

    def get_discipline_score(self, patterns):
        score = 100
        deductions = {
            'revenge_trading': {'high': 20, 'medium': 10},
            'overtrading':     {'high': 20, 'medium': 10, 'low': 5},
            'tilt':            {'high': 15, 'medium': 8},
            'loss_aversion':   {'high': 10, 'medium': 10},
            'panic_selling':   {'medium': 8, 'low': 4},
            'overconfidence':  {'medium': 5, 'low': 3},
        }
        for key, levels in deductions.items():
            sev = patterns.get(key, {}).get('severity', 'none')
            score -= levels.get(sev, 0)

        trades = self._get_trades()
        if trades:
            wins     = sum(1 for t in trades if t.trade_result == 'WIN')
            win_rate = wins / len(trades)
            if win_rate > 0.60:
                score += 5
            elif win_rate < 0.35:
                score -= 5

        return max(0, min(100, score))

    def get_trading_personality(self, patterns, score):
        revenge    = patterns.get('revenge_trading', {}).get('detected', False)
        overtrading = patterns.get('overtrading', {}).get('detected', False)
        tilt       = patterns.get('tilt', {}).get('detected', False)
        loss_av    = patterns.get('loss_aversion', {}).get('detected', False)

        if revenge and overtrading:
            return {
                'type':        'Emotional Trader',
                'icon':        'fas fa-fire',
                'color':       '#e53e3e',
                'description': 'You trade reactively — especially after losses. This is the most common pattern among retail traders who lose consistently.',
                'strength':    'Quick decision-making, high energy',
                'weakness':    'Emotions override strategy',
            }
        if loss_av and tilt:
            return {
                'type':        'Fearful Gambler',
                'icon':        'fas fa-dice',
                'color':       '#dd6b20',
                'description': 'You hold losers too long hoping they recover, then bet bigger after losses to "win it back". This combination is particularly dangerous.',
                'strength':    'Patience with positions',
                'weakness':    'Holding losers and doubling down after losses',
            }
        if revenge and not overtrading:
            return {
                'type':        'Revenge Trader',
                'icon':        'fas fa-redo',
                'color':       '#d53f8c',
                'description': 'You tend to jump back into trades after losses to immediately recover. This pattern often turns small losses into large ones.',
                'strength':    'Resilient, determined',
                'weakness':    'Trades without proper setup after a loss',
            }
        if score >= 80:
            return {
                'type':        'Disciplined Trader',
                'icon':        'fas fa-chess',
                'color':       '#38a169',
                'description': 'Your behaviour shows strong discipline and emotional control. You follow your rules consistently — this is rare and valuable.',
                'strength':    'Rule-based, consistent, emotionally stable',
                'weakness':    'May be overly cautious on high-conviction setups',
            }
        if score >= 60:
            return {
                'type':        'Developing Trader',
                'icon':        'fas fa-seedling',
                'color':       '#3182ce',
                'description': 'You are building good trading habits with a few areas still to strengthen. You are firmly on the right track.',
                'strength':    'Self-aware and improving',
                'weakness':    'Occasional emotional decisions breaking the pattern',
            }
        return {
            'type':        'Impulse Trader',
            'icon':        'fas fa-random',
            'color':       '#e53e3e',
            'description': 'Decisions appear largely driven by impulse rather than a consistent plan. With awareness and rules, this can be fixed.',
            'strength':    'Quick to act on opportunities',
            'weakness':    'Lacks systematic approach',
        }

    # ── Main analysis ─────────────────────────────────────────────────────────

    def get_full_analysis(self):
        trades = self._get_trades()

        patterns = {
            'revenge_trading': self.detect_revenge_trading(),
            'overtrading':     self.detect_overtrading(),
            'tilt':            self.detect_tilt(),
            'loss_aversion':   self.detect_loss_aversion(),
            'panic_selling':   self.detect_panic_selling(),
            'overconfidence':  self.detect_overconfidence(),
        }

        score       = self.get_discipline_score(patterns)
        personality = self.get_trading_personality(patterns, score)

        total  = len(trades)
        wins   = sum(1 for t in trades if t.trade_result == 'WIN')
        losses = sum(1 for t in trades if t.trade_result == 'LOSS')

        win_pnl  = [t.realized_pnl for t in trades if t.trade_result == 'WIN']
        loss_pnl = [t.realized_pnl for t in trades if t.trade_result == 'LOSS']
        avg_win  = sum(win_pnl)  / len(win_pnl)  if win_pnl  else 0
        avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        rr       = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        active_alerts = sorted(
            [v for v in patterns.values() if v.get('detected')],
            key=lambda x: SEVERITY_RANK.get(x.get('severity', 'none'), 0),
            reverse=True,
        )

        return {
            'score':        score,
            'score_label':  ('Excellent' if score >= 80 else 'Good' if score >= 65
                             else 'Needs Work' if score >= 45 else 'Critical'),
            'score_color':  ('#38a169' if score >= 80 else '#3182ce' if score >= 65
                             else '#dd6b20' if score >= 45 else '#e53e3e'),
            'personality':  personality,
            'patterns':     patterns,
            'active_alerts': active_alerts,
            'stats': {
                'total_trades': total,
                'wins':         wins,
                'losses':       losses,
                'win_rate':     round(wins / total * 100, 1) if total > 0 else 0,
                'total_pnl':    round(sum(t.realized_pnl for t in trades), 2),
                'avg_win':      round(avg_win, 2),
                'avg_loss':     round(avg_loss, 2),
                'risk_reward':  round(rr, 2),
            },
            'by_hour':   self.get_win_rate_by_hour(),
            'by_day':    self.get_win_rate_by_day(),
            'by_symbol': self.get_win_rate_by_symbol(),
            'has_data':  total >= 5,
        }

    # ── Pre-trade real-time check ─────────────────────────────────────────────

    def pre_trade_check(self):
        """Returns a list of active behavioural warnings before placing a trade."""
        trades   = self._get_trades()
        now      = datetime.utcnow()
        warnings = []

        if not trades:
            return warnings

        last = trades[-1]
        mins_since_last = (now - last.exit_time).total_seconds() / 60

        if last.trade_result == 'LOSS' and mins_since_last <= self.REVENGE_WINDOW_MINS:
            warnings.append({
                'type':     'revenge_trading',
                'severity': 'high',
                'icon':     'fas fa-fire',
                'message':  (
                    f'Your last trade ({last.symbol}) closed as a LOSS just '
                    f'{round(mins_since_last)}m ago. Traders who re-enter within 30 minutes '
                    f'of a loss have a statistically lower win rate. Consider waiting.'
                ),
            })

        today_trades = [t for t in trades if t.entry_time.date() == now.date()]
        if len(today_trades) >= self.OVERTRADE_THRESHOLD:
            warnings.append({
                'type':     'overtrading',
                'severity': 'medium',
                'icon':     'fas fa-bolt',
                'message':  (
                    f'You have already placed {len(today_trades)} trades today. '
                    'Overtrading increases costs and emotional decision-making.'
                ),
            })

        recent = trades[-3:] if len(trades) >= 3 else trades
        if len(recent) == 3 and all(t.trade_result == 'LOSS' for t in recent):
            warnings.append({
                'type':     'tilt',
                'severity': 'high',
                'icon':     'fas fa-chart-bar',
                'message':  (
                    'You have 3 consecutive losses. Win rates drop sharply during losing streaks. '
                    'Consider stopping for today and reviewing your setups.'
                ),
            })

        return warnings
