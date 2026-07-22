"""
Risk Engine — Scentric AI Decision Engine
Calculates Risk Heat Map, Goal Progress, Portfolio Pulse, and Behavioural Guardrails.
"""
import json
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_risk_cache: dict = {}
CACHE_TTL_SECONDS = 300

# Risk configuration per asset class
ASSET_RISK_CONFIG = {
    'Equities':              {'risk': 'high',   'color': '#ef4444', 'bg': '#fef2f2', 'score': 8, 'label': 'High'},
    'Mutual Funds':          {'risk': 'medium', 'color': '#f59e0b', 'bg': '#fffbeb', 'score': 5, 'label': 'Medium'},
    'Fixed Deposits':        {'risk': 'low',    'color': '#22c55e', 'bg': '#f0fdf4', 'score': 2, 'label': 'Low'},
    'Real Estate':           {'risk': 'medium', 'color': '#f59e0b', 'bg': '#fffbeb', 'score': 5, 'label': 'Medium'},
    'Gold & Commodities':    {'risk': 'medium', 'color': '#f59e0b', 'bg': '#fffbeb', 'score': 4, 'label': 'Medium'},
    'Cryptocurrency':        {'risk': 'high',   'color': '#dc2626', 'bg': '#fef2f2', 'score': 9, 'label': 'Very High'},
    'F&O / Derivatives':     {'risk': 'high',   'color': '#dc2626', 'bg': '#fef2f2', 'score': 10, 'label': 'Very High'},
    'Insurance':             {'risk': 'low',    'color': '#22c55e', 'bg': '#f0fdf4', 'score': 1, 'label': 'Very Low'},
    'Bonds':                 {'risk': 'low',    'color': '#22c55e', 'bg': '#f0fdf4', 'score': 3, 'label': 'Low'},
}

DEFAULT_RISK = {'risk': 'medium', 'color': '#f59e0b', 'bg': '#fffbeb', 'score': 5, 'label': 'Medium'}


class RiskEngine:
    def __init__(self, user_id):
        self.user_id = user_id

    def _cache_key(self, section: str, fingerprint: str = "") -> str:
        return f"{self.user_id}:{section}:{fingerprint}"

    def _get_cached(self, section: str, fingerprint: str = ""):
        now = time.time()
        key = self._cache_key(section, fingerprint)
        entry = _risk_cache.get(key)
        if entry and (now - entry['ts']) < CACHE_TTL_SECONDS:
            return entry['data']
        if entry:
            _risk_cache.pop(key, None)
        if len(_risk_cache) > 500:
            expired = [k for k, v in _risk_cache.items() if (now - v['ts']) >= CACHE_TTL_SECONDS]
            for k in expired:
                _risk_cache.pop(k, None)
        return None

    def _set_cached(self, section: str, data, fingerprint: str = ""):
        _risk_cache[self._cache_key(section, fingerprint)] = {'data': data, 'ts': time.time()}

    # ─────────────────────────────────────────────────────────────
    # 1. RISK HEAT MAP
    # ─────────────────────────────────────────────────────────────
    @staticmethod
    def _portfolio_fingerprint(portfolio_summary: dict) -> str:
        import hashlib
        val = portfolio_summary.get('total_current_value', 0)
        cnt = len(portfolio_summary.get('asset_classes', []))
        return hashlib.md5(f"{val}:{cnt}".encode()).hexdigest()[:8]

    def get_risk_heatmap(self, portfolio_summary: dict) -> list:
        """Build colour-coded risk heat map from portfolio summary."""
        fp = self._portfolio_fingerprint(portfolio_summary)
        cached = self._get_cached('heatmap', fp)
        if cached is not None:
            return cached
        asset_classes = portfolio_summary.get('asset_classes', [])
        total_value = portfolio_summary.get('total_current_value', 0) or 0.01

        risk_map = []
        for ac in asset_classes:
            name = ac.get('name', 'Unknown')
            current_value = ac.get('current_value', 0) or 0
            weight = round((current_value / total_value) * 100, 1)
            pnl_pct = round(ac.get('pnl_percentage', 0) or 0, 2)

            config = dict(ASSET_RISK_CONFIG.get(name, DEFAULT_RISK))

            # Escalate risk when concentration is too high
            if weight > 40 and config['score'] < 8:
                config = dict(config)
                config['risk'] = 'high'
                config['color'] = '#ef4444'
                config['label'] = 'High (Concentrated)'
                config['score'] = max(config['score'], 8)

            risk_map.append({
                'name': name,
                'value': current_value,
                'weight': weight,
                'pnl_pct': pnl_pct,
                'pnl_positive': pnl_pct >= 0,
                'risk': config['risk'],
                'color': config['color'],
                'bg': config['bg'],
                'risk_score': config['score'],
                'risk_label': config['label'],
                'count': ac.get('count', 0),
            })

        risk_map.sort(key=lambda x: x['risk_score'], reverse=True)
        self._set_cached('heatmap', risk_map, fp)
        return risk_map

    # ─────────────────────────────────────────────────────────────
    # 2. GOAL PROGRESS
    # ─────────────────────────────────────────────────────────────
    def get_goal_progress(self, portfolio_summary: dict) -> list:
        """Return progress bars for each declared financial goal."""
        fp = self._portfolio_fingerprint(portfolio_summary)
        cached = self._get_cached('goals', fp)
        if cached is not None:
            return cached
        try:
            from models import PortfolioPreferences
            prefs = PortfolioPreferences.query.filter_by(user_id=self.user_id).first()
        except Exception:
            return []

        if not prefs or not prefs.financial_goals:
            return []

        total_value = portfolio_summary.get('total_current_value', 0) or 0

        try:
            raw_goals = json.loads(prefs.financial_goals)
        except Exception:
            return []

        GOAL_ICONS = {
            'retirement': 'fa-umbrella-beach',
            'education': 'fa-graduation-cap',
            'emergency': 'fa-shield-alt',
            'home': 'fa-home',
            'wealth': 'fa-chart-line',
            'travel': 'fa-plane',
            'car': 'fa-car',
        }

        goals = []
        for g in raw_goals[:4]:
            goal_name = g.get('goal') or g.get('type') or g.get('name') or 'Financial Goal'
            target = float(g.get('target_amount') or g.get('amount') or 0)
            # Use declared current amount or apportion portfolio value proportionally
            current = float(g.get('current_amount') or g.get('saved') or 0)
            if current == 0 and total_value > 0:
                current = total_value / max(len(raw_goals), 1)
            deadline = g.get('target_date') or g.get('deadline') or g.get('year') or ''

            icon_key = next((k for k in GOAL_ICONS if k in goal_name.lower()), 'wealth')
            progress = round(min(100, (current / target * 100)) if target > 0 else 0, 1)

            color = '#22c55e' if progress >= 80 else '#3b82f6' if progress >= 50 else '#f59e0b' if progress >= 25 else '#ef4444'

            goals.append({
                'name': goal_name.title(),
                'target': target,
                'current': current,
                'deadline': str(deadline),
                'progress': progress,
                'color': color,
                'icon': GOAL_ICONS.get(icon_key, 'fa-bullseye'),
            })

        self._set_cached('goals', goals, fp)
        return goals

    # ─────────────────────────────────────────────────────────────
    # 3. PORTFOLIO PULSE (weekly health summary)
    # ─────────────────────────────────────────────────────────────
    def get_portfolio_pulse(self, portfolio_summary: dict, risk_heatmap: list):
        """Generate the Weekly Portfolio Health Pulse.
        Returns None when the portfolio is empty so the UI can hide the widget."""
        total_value = portfolio_summary.get('total_current_value', 0) or 0
        asset_classes = portfolio_summary.get('asset_classes', [])

        # Nothing to score — return None so the template hides the pulse block
        if total_value == 0 and not asset_classes:
            return None

        fp = self._portfolio_fingerprint(portfolio_summary)
        cached = self._get_cached('pulse', fp)
        if cached is not None:
            return cached
        pnl_pct = portfolio_summary.get('pnl_percentage', 0) or 0

        # Weighted portfolio risk score (0–10)
        if risk_heatmap:
            total_w = sum(r['weight'] for r in risk_heatmap) or 1
            weighted_risk = sum(r['risk_score'] * r['weight'] for r in risk_heatmap) / total_w
        else:
            weighted_risk = 5.0

        # Health score (0–100): lower risk + positive returns = higher score
        health_score = max(0, min(100, 100 - weighted_risk * 6))
        if pnl_pct > 15:
            health_score = min(100, health_score + 15)
        elif pnl_pct > 5:
            health_score = min(100, health_score + 5)
        elif pnl_pct < -10:
            health_score = max(0, health_score - 15)
        elif pnl_pct < 0:
            health_score = max(0, health_score - 5)
        health_score = round(health_score)

        # Guardrail alerts for the pulse
        alerts = []
        for item in risk_heatmap:
            if item['weight'] > 40:
                alerts.append({
                    'level': 'danger',
                    'icon': 'fa-exclamation-triangle',
                    'text': f"{item['name']} is {item['weight']}% of portfolio — concentration risk",
                })
            elif item['risk'] == 'high' and item['weight'] > 25:
                alerts.append({
                    'level': 'warning',
                    'icon': 'fa-exclamation-circle',
                    'text': f"High-risk {item['name']} ({item['weight']}%) exceeds 25% safe threshold",
                })
        if not alerts and health_score >= 70:
            alerts.append({
                'level': 'success',
                'icon': 'fa-check-circle',
                'text': 'Portfolio is well-balanced and within risk limits.',
            })

        if health_score >= 80:
            health_label, health_color = 'Excellent', 'success'
        elif health_score >= 60:
            health_label, health_color = 'Good', 'primary'
        elif health_score >= 40:
            health_label, health_color = 'Fair', 'warning'
        else:
            health_label, health_color = 'At Risk', 'danger'

        result = {
            'health_score': health_score,
            'health_label': health_label,
            'health_color': health_color,
            'risk_score': round(weighted_risk, 1),
            'pnl_pct': round(pnl_pct, 2),
            'total_value': total_value,
            'asset_count': len(risk_heatmap),
            'alerts': alerts[:3],
            'generated_at': datetime.utcnow().strftime('%d %b %Y'),
        }
        self._set_cached('pulse', result, fp)
        return result

    # ─────────────────────────────────────────────────────────────
    # 4. BEHAVIOURAL GUARDRAILS (Trade Now check)
    # ─────────────────────────────────────────────────────────────
    def check_guardrails(self, asset_class: str, quantity: float,
                         price: float, portfolio_summary: dict) -> list:
        """
        Check a proposed trade against the user's declared risk profile.
        Returns a list of alert dicts with level, title, message.
        """
        try:
            from models import RiskProfile, PortfolioPreferences
            risk_profile = RiskProfile.query.filter_by(user_id=self.user_id).first()
            prefs = PortfolioPreferences.query.filter_by(user_id=self.user_id).first()
        except Exception:
            return []

        alerts = []
        trade_value = (quantity or 0) * (price or 0)
        total_portfolio = portfolio_summary.get('total_current_value', 0) or 0
        asset_lower = (asset_class or '').lower()

        HIGH_RISK_CLASSES = {'futures', 'options', 'f&o', 'derivatives', 'crypto', 'cryptocurrency'}
        is_high_risk = any(r in asset_lower for r in HIGH_RISK_CLASSES)

        # — Risk tolerance check —
        if risk_profile:
            tolerance = (risk_profile.risk_tolerance or '').lower()
            if tolerance == 'conservative' and is_high_risk:
                alerts.append({
                    'level': 'danger',
                    'icon': 'fa-shield-alt',
                    'title': 'Risk Profile Violation',
                    'message': (
                        f'Your declared risk appetite is Conservative. '
                        f'Trading {asset_class.title()} is classified as High Risk — '
                        f'this deviates from your investment strategy.'
                    ),
                })
            elif tolerance == 'moderate' and 'crypto' in asset_lower:
                alerts.append({
                    'level': 'warning',
                    'icon': 'fa-exclamation-triangle',
                    'title': 'Strategy Deviation Alert',
                    'message': (
                        'Cryptocurrency exceeds typical Moderate risk. '
                        'This trade may not align with your declared risk appetite.'
                    ),
                })

        # — Concentration check —
        if total_portfolio > 0 and trade_value > 0:
            concentration = (trade_value / total_portfolio) * 100
            if concentration > 30:
                alerts.append({
                    'level': 'danger',
                    'icon': 'fa-percentage',
                    'title': 'High Concentration Risk',
                    'message': (
                        f'This trade is {concentration:.1f}% of your total portfolio value. '
                        f'Capulse recommends a maximum of 20% per position.'
                    ),
                })
            elif concentration > 20:
                alerts.append({
                    'level': 'warning',
                    'icon': 'fa-balance-scale',
                    'title': 'Concentration Warning',
                    'message': (
                        f'This trade represents {concentration:.1f}% of your portfolio. '
                        f'Consider sizing down to maintain diversification.'
                    ),
                })

        # — Max acceptable loss check —
        if prefs and prefs.max_acceptable_loss and total_portfolio > 0:
            max_loss_amount = total_portfolio * (prefs.max_acceptable_loss / 100)
            if trade_value > max_loss_amount * 3:
                alerts.append({
                    'level': 'warning',
                    'icon': 'fa-tachometer-alt',
                    'title': 'Loss Tolerance Caution',
                    'message': (
                        f'Trade size is significant relative to your declared '
                        f'maximum acceptable loss of {prefs.max_acceptable_loss}%. '
                        f'Ensure you have a stop-loss in place.'
                    ),
                })

        return alerts

    # ─────────────────────────────────────────────────────────────
    # 5. LOG PORTFOLIO EVENT (Persistent Memory)
    # ─────────────────────────────────────────────────────────────
    def log_event(self, event_type: str, title: str, detail: str = None,
                  symbol: str = None, amount: float = None):
        """Log a portfolio event for persistent memory."""
        try:
            from models import PortfolioEvent
            from app import db
            event = PortfolioEvent(
                user_id=self.user_id,
                event_type=event_type,
                event_title=title,
                event_detail=detail,
                symbol=symbol,
                amount=amount,
            )
            db.session.add(event)
            db.session.commit()
        except Exception as e:
            logger.warning(f'Could not log portfolio event: {e}')

    def get_recent_events(self, limit: int = 10) -> list:
        """Retrieve recent portfolio events for memory context."""
        try:
            from models import PortfolioEvent
            events = (PortfolioEvent.query
                      .filter_by(user_id=self.user_id)
                      .order_by(PortfolioEvent.created_at.desc())
                      .limit(limit)
                      .all())
            return [e.to_dict() for e in events]
        except Exception as e:
            logger.warning(f'Could not fetch portfolio events: {e}')
            return []


def get_risk_engine(user_id: int) -> RiskEngine:
    return RiskEngine(user_id)
