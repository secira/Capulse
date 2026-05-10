"""
Periodic I-Score alert scheduler.

Two responsibilities:

1. **Partner webhooks** — walks the DISTINCT set of stock symbols any partner
   is subscribed to, recomputes the I-Score, and fires a webhook when:
     • the score moved by ≥ subscription.delta_threshold vs. last_score, OR
     • the recommendation tier changed (e.g. HOLD → BUY), OR
     • this is the first delivery AND score ≥ subscription.min_confidence.

2. **Telegram "Top 5 Stocks to Buy" digest** — instead of pinging Telegram
   for every stock that crosses a threshold (which would flood the chat),
   we ROLL UP all currently-buying stocks from `ResearchList` into a single
   ranked digest of the **top 5 by I-Score** (BUY or STRONG_BUY only).
   The digest is only sent when its composition changes vs. the last
   digest, so an unchanged leaderboard never re-spams the group.
"""
import hashlib
import logging
from datetime import datetime

from app import db
from models_partner_api import ApiSubscription
from services.partner_webhook import dispatch_event

logger = logging.getLogger(__name__)

_scheduler_started = False
SCAN_INTERVAL_MIN  = 30

# Top-N digest config
TOP_N_BUYS               = 5
_BUY_TIERS               = ('STRONG_BUY', 'BUY')
_last_digest_fingerprint = None   # set of (symbol, tier) — only resend when changed


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

        # After per-subscription webhooks, send a single consolidated
        # Telegram digest of the top 5 BUY-rated stocks.
        try:
            send_top_buys_digest()
        except Exception as e:
            logger.warning(f"Top-5 Telegram digest failed: {e}")


def send_top_buys_digest(force: bool = False) -> bool:
    """Build and send the "Top 5 Stocks to Buy" Telegram digest.

    Reads `ResearchList`, keeps only stocks recommended STRONG_BUY or BUY,
    sorts by I-Score (desc), takes the top 5, and sends a single Telegram
    message. Suppresses re-sends when the leaderboard composition is
    unchanged since the previous digest (set `force=True` to override).

    Returns True if a message was sent, False otherwise.
    """
    global _last_digest_fingerprint
    from models import ResearchList
    from services.messaging_service import send_telegram_message

    rows = (ResearchList.query
            .filter(ResearchList.is_active.is_(True))
            .filter(ResearchList.recommendation.in_(_BUY_TIERS))
            .filter(ResearchList.i_score.isnot(None))
            .order_by(ResearchList.i_score.desc())
            .limit(TOP_N_BUYS)
            .all())

    if not rows:
        logger.info("Top-5 digest: no BUY-rated stocks in ResearchList — skipping")
        return False

    # Fingerprint = ordered (symbol, tier, rounded score-bucket) — re-send
    # if any of the 5 stocks change OR a score moves by ≥ 5 points.
    fp_parts = [f"{r.symbol}:{r.recommendation}:{int(float(r.i_score) // 5)}" for r in rows]
    fingerprint = hashlib.md5("|".join(fp_parts).encode()).hexdigest()

    if not force and fingerprint == _last_digest_fingerprint:
        logger.info("Top-5 digest: unchanged leaderboard — not re-sending")
        return False

    # Build the message
    today = datetime.now().strftime("%d %b %Y, %I:%M %p")
    lines = [
        "📈 *Top 5 Stocks to Buy — Scentric I-Score*",
        f"_{today} IST_",
        "",
    ]
    medal = {0: "🥇", 1: "🥈", 2: "🥉", 3: "4️⃣", 4: "5️⃣"}
    for i, r in enumerate(rows):
        tier_tag = "STRONG BUY" if r.recommendation == 'STRONG_BUY' else "BUY"
        score = float(r.i_score)
        price = f"₹{float(r.current_price):,.2f}" if r.current_price else "—"
        chg   = f" ({float(r.price_change_pct):+.2f}%)" if r.price_change_pct is not None else ""
        sector = f" · _{r.sector}_" if r.sector else ""
        lines.append(
            f"{medal.get(i, '•')} *{r.symbol}* — I-Score *{score:.1f}/100*  ·  {tier_tag}\n"
            f"     {price}{chg}{sector}"
        )
    lines.append("")
    lines.append("_Disclaimer: AI-generated research, not investment advice._")

    message = "\n".join(lines)
    sent = send_telegram_message(message)
    if sent:
        _last_digest_fingerprint = fingerprint
        logger.info(f"📨 Top-5 Telegram digest sent ({len(rows)} stocks, fp={fingerprint[:8]})")
    else:
        logger.warning("Top-5 Telegram digest: send_telegram_message returned False")
    return bool(sent)


_ISCORE_ADVISORY_LOCK_ID = 728193002


def start_scheduler(app):
    global _scheduler_started
    if _scheduler_started:
        return
    # Reuse the same advisory-lock pattern as the F&O monitor so only one
    # gunicorn worker runs partner alerts (prevents duplicate webhooks).
    from services.fno_monitor import _try_acquire_scheduler_lock
    if not _try_acquire_scheduler_lock(app, _ISCORE_ADVISORY_LOCK_ID):
        logger.info("I-Score partner scheduler skipped on this worker (another worker holds the lock)")
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
        logger.info(f"I-Score partner alert scheduler started ({SCAN_INTERVAL_MIN} min interval) — singleton worker")
    except Exception as e:
        logger.error(f"Failed to start I-Score partner scheduler: {e}")
