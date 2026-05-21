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
TOP_N_BUYS               = 10
_BUY_TIERS               = ('STRONG_BUY', 'BUY')
_last_digest_fingerprint = None   # set of (symbol, tier) — only resend when changed

# Module-level handles so the admin UI can reload jobs without a restart.
_scheduler = None     # type: ignore  — APScheduler BackgroundScheduler instance
_app       = None     # type: ignore  — Flask app, captured by start_scheduler

# Mapping schedule_key (alert_schedule.schedule_key) → callable that fires
# the alert. The admin "Send Now" button and the scheduler both look up
# alerts through this registry.
def _fire_top10_digest():
    return send_top_buys_digest(force=True)


def _fire_market_snapshot(slot: str):
    from services.market_snapshot_alert import send_market_snapshot
    return send_market_snapshot(slot=slot)


def _fire_premarket_report():
    from services.premarket_report import send_premarket_report
    return send_premarket_report()


SCHEDULE_REGISTRY = {
    'top10_digest':        lambda: _fire_top10_digest(),
    'premarket_report':    lambda: _fire_premarket_report(),
    'snapshot_opening':    lambda: _fire_market_snapshot('opening'),
    'snapshot_midsession': lambda: _fire_market_snapshot('midsession'),
    'snapshot_preclose':   lambda: _fire_market_snapshot('preclose'),
    'snapshot_close':      lambda: _fire_market_snapshot('close'),
}


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

    # Build the message — each row shows full trade plan (Entry/Target/SL/Duration)
    today = datetime.now().strftime("%d %b %Y, %I:%M %p")
    lines = [
        f"📈 *Top {TOP_N_BUYS} Stocks to Buy — Scentric I-Score*",
        f"_{today} IST_",
        "",
    ]
    medal = {0: "🥇", 1: "🥈", 2: "🥉"}
    for i, r in enumerate(rows):
        is_strong  = r.recommendation == 'STRONG_BUY'
        tier_tag   = "STRONG BUY" if is_strong else "BUY"
        score      = float(r.i_score)
        price_val  = float(r.current_price) if r.current_price else None
        chg        = f" ({float(r.price_change_pct):+.2f}%)" if r.price_change_pct is not None else ""
        sector     = f"  ·  _{r.sector}_" if r.sector else ""
        marker     = medal.get(i, f"{i + 1}.")

        # Trade plan derived from tier + market price.
        # STRONG_BUY: +10% target, -4% stop, 1–2 month positional swing.
        # BUY:        +6% target,  -3% stop, 2–4 week swing.
        if price_val:
            entry_lo  = price_val * 0.995
            entry_hi  = price_val * 1.005
            tgt_pct   = 0.10 if is_strong else 0.06
            sl_pct    = 0.04 if is_strong else 0.03
            target    = price_val * (1 + tgt_pct)
            stop_loss = price_val * (1 - sl_pct)
            duration  = "1–2 months" if is_strong else "2–4 weeks"
            rr_ratio  = tgt_pct / sl_pct
            plan = (
                f"     💰 Market: ₹{price_val:,.2f}{chg}\n"
                f"     🎯 Entry:  ₹{entry_lo:,.2f} – ₹{entry_hi:,.2f}\n"
                f"     ✅ Target: ₹{target:,.2f}  (+{tgt_pct*100:.0f}%)\n"
                f"     🛑 Stop:   ₹{stop_loss:,.2f}  (-{sl_pct*100:.0f}%)\n"
                f"     ⏱ Hold:   {duration}   ·   R:R 1:{rr_ratio:.1f}"
            )
        else:
            plan = "     _Market data unavailable — plan pending_"

        lines.append(
            f"{marker} *{r.symbol}* — I-Score *{score:.1f}/100*  ·  {tier_tag}{sector}\n"
            f"{plan}"
        )
        lines.append("")  # spacer between stocks for readability
    lines.append("_Disclaimer: AI-generated research, not investment advice. Always size positions to your own risk tolerance._")

    message = "\n".join(lines)
    sent = send_telegram_message(message)
    if sent:
        _last_digest_fingerprint = fingerprint
        logger.info(f"📨 Top-5 Telegram digest sent ({len(rows)} stocks, fp={fingerprint[:8]})")
    else:
        logger.warning("Top-5 Telegram digest: send_telegram_message returned False")
    return bool(sent)


_ISCORE_ADVISORY_LOCK_ID = 728193002


def fire_schedule_now(schedule_key: str) -> bool:
    """Manually trigger a scheduled alert by key (used by admin "Send Now")."""
    fn = SCHEDULE_REGISTRY.get(schedule_key)
    if not fn:
        logger.warning(f"fire_schedule_now: unknown schedule_key '{schedule_key}'")
        return False
    try:
        return bool(fn())
    except Exception as e:
        logger.error(f"fire_schedule_now({schedule_key}) failed: {e}")
        return False


def reload_schedules() -> int:
    """Re-read alert_schedule rows from the DB and rebuild every cron job.

    Called by the admin Alert Schedules page after a save so timing changes
    take effect without a server restart. Returns the number of jobs
    (re)installed.
    """
    if _scheduler is None or _app is None:
        logger.warning("reload_schedules: scheduler not started yet — nothing to reload")
        return 0

    try:
        from apscheduler.triggers.cron import CronTrigger
    except Exception as e:
        logger.error(f"reload_schedules: APScheduler import failed: {e}")
        return 0

    try:
        import pytz
        ist_tz = pytz.timezone('Asia/Kolkata')
    except Exception:
        ist_tz = None

    # Pull current schedules from DB
    try:
        rows = db.session.execute(db.text(
            "SELECT schedule_key, hour, minute, days_of_week, enabled FROM alert_schedule"
        )).fetchall()
    except Exception as e:
        logger.error(f"reload_schedules: DB read failed: {e}")
        return 0

    installed = 0
    for schedule_key, hour, minute, dow, enabled in rows:
        job_id = f"alert_{schedule_key}"
        # Always remove any existing version first
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass

        if not enabled or schedule_key not in SCHEDULE_REGISTRY:
            continue

        cron_kwargs = {
            'day_of_week': dow or 'mon-fri',
            'hour':        int(hour),
            'minute':      int(minute),
        }
        if ist_tz is not None:
            cron_kwargs['timezone'] = ist_tz

        # Closure captures schedule_key by value
        def _make_runner(key: str):
            def _runner():
                with _app.app_context():
                    try:
                        fire_schedule_now(key)
                    except Exception as e:
                        logger.error(f"Scheduled alert '{key}' failed: {e}")
            return _runner

        _scheduler.add_job(
            _make_runner(schedule_key), CronTrigger(**cron_kwargs),
            id=job_id, replace_existing=True, max_instances=1,
        )
        installed += 1
        logger.info(
            f"  reload_schedules: '{schedule_key}' → {int(hour):02d}:{int(minute):02d} IST ({dow})"
        )

    logger.info(f"✅ reload_schedules: installed {installed} alert job(s) from DB")
    return installed


def start_scheduler(app):
    global _scheduler_started, _scheduler, _app
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
        from apscheduler.triggers.cron import CronTrigger

        # Most servers run UTC; pin the daily digest to IST so 8:30 AM
        # always means 8:30 AM in India regardless of host timezone.
        try:
            import pytz
            ist_tz = pytz.timezone('Asia/Kolkata')
        except Exception:
            ist_tz = None  # APScheduler will fall back to local tz

        scheduler = BackgroundScheduler(daemon=True)

        # Job: periodic partner-webhook scan (every 30 min, fixed interval)
        scheduler.add_job(
            scan_once, 'interval', minutes=SCAN_INTERVAL_MIN,
            args=[app], id='iscore_partner_scan',
            replace_existing=True, max_instances=1,
        )

        scheduler.start()

        # Persist handles for reload_schedules() to use later
        _scheduler = scheduler
        _app       = app
        _scheduler_started = True

        # Load all admin-managed alert jobs (top10 digest + market snapshots)
        # from the alert_schedule DB table. Admin can edit these via the
        # /admin/alert-schedules page and call reload_schedules() to apply.
        with app.app_context():
            try:
                installed = reload_schedules()
            except Exception as e:
                logger.error(f"Initial reload_schedules failed: {e}")
                installed = 0

        logger.info(
            f"I-Score scheduler started — partner scan every {SCAN_INTERVAL_MIN} min, "
            f"{installed} admin-managed alert job(s) loaded from alert_schedule"
        )
    except Exception as e:
        logger.error(f"Failed to start I-Score scheduler: {e}")
