"""
Periodic I-Score alert scheduler.

Walks the DISTINCT set of stock symbols any partner is subscribed to, recomputes
the I-Score (using the existing engine), and fires a webhook when:

  • the score moved by ≥ subscription.delta_threshold vs. last_score, OR
  • the recommendation tier changed (e.g. HOLD → BUY), OR
  • this is the first time we've delivered a score for that subscription
    AND the score ≥ subscription.min_confidence.
"""
import logging
from datetime import datetime

from app import db
from models_partner_api import ApiSubscription
from services.partner_webhook import dispatch_event

logger = logging.getLogger(__name__)

_scheduler_started = False
SCAN_INTERVAL_MIN  = 30


def _recompute_iscore(symbol: str) -> dict | None:
    """Run the existing I-Score engine for a single stock symbol."""
    try:
        from services.langgraph_iscore_engine import LangGraphIScoreEngine
        engine = LangGraphIScoreEngine()
        result = engine.analyze(asset_type='stocks', symbol=symbol,
                                user_id=1, asset_name=symbol)
        if not result or not result.get('success'):
            return None
        return result
    except Exception as e:
        logger.warning(f"I-Score recompute failed for {symbol}: {e}")
        return None


def scan_once(app):
    """Run one full pass over every subscribed I-Score symbol."""
    with app.app_context():
        try:
            symbols = (db.session.query(ApiSubscription.symbol)
                       .filter_by(engine='iscore', is_active=True)
                       .distinct()
                       .all())
            symbols = [s[0] for s in symbols]
        except Exception as e:
            logger.error(f"I-Score scan: subscription query failed: {e}")
            return

        if not symbols:
            return

        logger.info(f"📊 I-Score scan: {len(symbols)} subscribed symbol(s)")
        for symbol in symbols:
            result = _recompute_iscore(symbol)
            if not result:
                continue

            score = float(result.get('iscore') or 0)
            tier  = (result.get('recommendation') or 'HOLD').upper()

            # Decide per-subscription whether to fire
            subs = (ApiSubscription.query
                    .filter_by(engine='iscore', symbol=symbol, is_active=True)
                    .all())

            payload = {
                'engine':         'iscore',
                'symbol':         symbol,
                'score':          score,
                'confidence':     result.get('confidence'),
                'tier':           tier,
                'recommendation': tier,
                'summary':        result.get('summary'),
                'components':     result.get('components'),
                'market_data':    result.get('market_data'),
                'data_source':    result.get('data_source'),
                'timestamp':      datetime.utcnow().isoformat() + 'Z',
            }

            for sub in subs:
                fire = False
                if sub.last_score is None and score >= (sub.min_confidence or 0):
                    fire = True
                elif sub.last_tier and sub.last_tier != tier:
                    fire = True
                elif sub.last_score is not None and abs(score - sub.last_score) >= (sub.delta_threshold or 5):
                    fire = True

                if fire:
                    dispatch_event('iscore', symbol, payload, score=score)


def start_scheduler(app):
    global _scheduler_started
    if _scheduler_started:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            scan_once, 'interval', minutes=SCAN_INTERVAL_MIN,
            args=[app], id='iscore_partner_scan',
            replace_existing=True, max_instances=1,
        )
        scheduler.start()
        _scheduler_started = True
        logger.info(f"I-Score partner alert scheduler started ({SCAN_INTERVAL_MIN} min interval)")
    except Exception as e:
        logger.error(f"Failed to start I-Score partner scheduler: {e}")
