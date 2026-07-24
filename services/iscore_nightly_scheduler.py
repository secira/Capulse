"""
Nightly I-Score batch scheduler.

Runs the same "pending I-Scores" batch that the admin button triggers, but
automatically every night at 02:00 IST so the morning view is always fresh.

Design notes (mirrors services/fno_monitor.py):
  - APScheduler BackgroundScheduler, daemon thread.
  - Postgres session-level advisory lock so only ONE gunicorn worker runs
    the scheduler (otherwise 2 workers => 2 parallel batch runs).
  - Persists "last run" / "next run" metadata in module-level state so the
    admin page can show status.
  - Honours DISABLE_SCHEDULERS=1 and SKIP_SCHEDULER=1 (same env vars the
    F&O monitor honours).
  - Reuses run_pending_iscore_batch() — the same function the manual button
    invokes — so behaviour is identical.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── Shared status (read by admin UI) ──────────────────────────────────────
_state = {
    "scheduler_started": False,
    "last_run_started":  None,    # ISO string
    "last_run_finished": None,    # ISO string
    "last_run_status":   None,    # 'completed' | 'failed' | 'skipped'
    "last_run_total":    0,
    "last_run_success":  0,
    "last_run_errors":   0,
    "last_run_job_id":   None,
    "next_run":          None,    # ISO string
    "currently_running": False,
}

_NIGHTLY_ADVISORY_LOCK_ID = 728193002  # unique vs fno_monitor (…001)
_IST = timezone(timedelta(hours=5, minutes=30))


def _persist_state():
    """Save scheduler state to the DB so ANY gunicorn worker can read it.

    Module-level _state only exists in the worker that runs the scheduler;
    admin status requests land on arbitrary workers. This keeps the admin
    page truthful in multi-worker production.
    """
    try:
        import json
        from sqlalchemy import text
        from app import app, db
        with app.app_context():
            db.session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            ))
            db.session.execute(text(
                """
                INSERT INTO scheduler_state (key, value, updated_at)
                VALUES ('iscore_nightly', :v, NOW())
                ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()
                """
            ), {"v": json.dumps(_state)})
            db.session.commit()
    except Exception as e:
        logger.warning(f"Could not persist nightly scheduler state: {e}")


def _load_persisted_state() -> dict | None:
    try:
        import json
        from sqlalchemy import text
        from app import app, db
        with app.app_context():
            row = db.session.execute(text(
                "SELECT value FROM scheduler_state WHERE key = 'iscore_nightly'"
            )).scalar()
            return json.loads(row) if row else None
    except Exception:
        return None


def get_status() -> dict:
    """Snapshot of scheduler state for the admin UI (any worker)."""
    state = dict(_state)
    if not state["scheduler_started"]:
        # This worker isn't the one running the scheduler — read the
        # DB-persisted state written by the worker that is.
        persisted = _load_persisted_state()
        if persisted:
            state = {**state, **persisted}
    return {
        **state,
        "batch_limit":    NIGHTLY_BATCH_LIMIT,
        "stale_days":     STALE_DAYS_THRESHOLD,
    }


# ── Nightly batch limits (env-configurable) ───────────────────────────────
# How many stocks to score per nightly run.  With ~2 167 NSE stocks and the
# default of 300/night the full catalogue refreshes every ~7 nights.
# Override with ISCORE_NIGHTLY_BATCH_LIMIT env var.
NIGHTLY_BATCH_LIMIT  = int(os.environ.get("ISCORE_NIGHTLY_BATCH_LIMIT", "300"))

# Stocks scored more recently than this threshold are considered "fresh" and
# are skipped during a stale-refresh run.
# Override with ISCORE_STALE_DAYS env var.
STALE_DAYS_THRESHOLD = int(os.environ.get("ISCORE_STALE_DAYS", "7"))


# ── The actual batch runner (shared with the manual button) ───────────────
def run_pending_iscore_batch(app, batch_jobs: dict, mode: str = "pending",
                             job_id: str | None = None,
                             polite_sleep: float = 1.5) -> str:
    """
    Run the I-Score engine over Research List stocks.

    mode values:
      "pending" — only stocks where i_score IS NULL  (original behaviour,
                  used by the manual Admin button when explicitly chosen)
      "stale"   — unscored stocks first, then oldest-scored stocks next,
                  capped at NIGHTLY_BATCH_LIMIT per run.  This is what the
                  nightly cron uses so ALL stocks are refreshed on a rolling
                  ~7-night cycle instead of only the initial pass.
      "all"     — every active stock, no cap (manual force-refresh)

    Writes progress into `batch_jobs[job_id]` using the SAME shape the
    manual admin endpoint uses, so the existing /batch-iscore/status
    polling endpoint works for nightly runs too.

    Returns the job_id.
    """
    # Local imports to avoid circular imports at module load.
    from app import db
    from models import ResearchList

    jid = job_id or str(uuid.uuid4())[:8]

    with app.app_context():
        if mode == "stale":
            # Unscored stocks come first (NULLS FIRST), then by age of last
            # computation ascending so the stalest are always refreshed next.
            # Cap to NIGHTLY_BATCH_LIMIT so the job finishes well before market
            # open even if the catalogue grows.
            stale_cutoff = datetime.utcnow() - timedelta(days=STALE_DAYS_THRESHOLD)
            stocks = (
                ResearchList.query
                .filter_by(is_active=True)
                .filter(
                    db.or_(
                        ResearchList.i_score.is_(None),
                        ResearchList.last_computed_at < stale_cutoff,
                    )
                )
                .order_by(ResearchList.last_computed_at.asc().nullsfirst())
                .limit(NIGHTLY_BATCH_LIMIT)
                .all()
            )
        elif mode == "pending":
            stocks = [
                s for s in ResearchList.query.filter_by(is_active=True).all()
                if s.i_score is None
            ]
        else:
            # mode == "all" — no filter, no cap
            stocks = ResearchList.query.filter_by(is_active=True).all()

        stock_ids = [s.id for s in stocks]

        batch_jobs[jid] = {
            "status": "running",
            "mode": mode,
            "total": len(stock_ids),
            "done": 0,
            "success": 0,
            "errors": 0,
            "current_symbol": "",
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "log": [],
            "source": "nightly" if job_id else "manual",
        }

        if not stock_ids:
            batch_jobs[jid]["status"] = "completed"
            batch_jobs[jid]["finished_at"] = datetime.utcnow().isoformat()
            if mode == "stale":
                batch_jobs[jid]["log"].append(
                    f"All stocks are fresh (scored within last {STALE_DAYS_THRESHOLD} days) — nothing to do."
                )
            else:
                batch_jobs[jid]["log"].append("No pending stocks — nothing to do.")
            return jid

        try:
            from services.langgraph_iscore_engine import LangGraphIScoreEngine
            engine = LangGraphIScoreEngine()
        except Exception as e:
            batch_jobs[jid]["status"] = "failed"
            batch_jobs[jid]["log"].append(f"Engine init error: {e}")
            return jid

        for idx, sid in enumerate(stock_ids):
            try:
                stock = ResearchList.query.get(sid)
                if not stock:
                    continue
                batch_jobs[jid]["current_symbol"] = stock.symbol
                result = engine.analyze(
                    asset_type=stock.asset_type,
                    symbol=stock.symbol,
                    user_id=1,
                    asset_name=stock.company_name or stock.symbol,
                )
                if result and result.get("success"):
                    components = result.get("components", {})
                    market = result.get("market_data", {})
                    mapped = {
                        "overall_score": result.get("iscore", 0),
                        "overall_confidence": result.get("confidence", 0),
                        "recommendation": result.get("recommendation", "HOLD"),
                        "recommendation_summary": result.get("summary", ""),
                        "qualitative_score": components.get("qualitative", {}).get("score", 0),
                        "quantitative_score": components.get("quantitative", {}).get("score", 0),
                        "search_score": components.get("search", {}).get("score", 0),
                        "trend_score": components.get("trend", {}).get("score", 0),
                        "risk_score": components.get("risk", {}).get("score") if components.get("risk") else None,
                        "market_context_score": components.get("market_context", {}).get("score") if components.get("market_context") else None,
                        "qualitative_details": components.get("qualitative", {}).get("details", {}),
                        "quantitative_details": components.get("quantitative", {}).get("details", {}),
                        "search_details": components.get("search", {}).get("details", {}),
                        "trend_details": components.get("trend", {}).get("details", {}),
                        "risk_details": components.get("risk", {}).get("details") if components.get("risk") else None,
                        "market_context_details": components.get("market_context", {}).get("details") if components.get("market_context") else None,
                        "current_price": market.get("current_price"),
                        "previous_close": market.get("previous_close"),
                        "price_change_pct": market.get("change_pct"),
                        "data_source": result.get("data_source", ""),
                    }
                    stock.update_from_iscore_result(mapped)
                    stock.computation_source = "nightly" if batch_jobs[jid]["source"] == "nightly" else "batch"
                    db.session.commit()
                    batch_jobs[jid]["success"] += 1
                    batch_jobs[jid]["log"].append(f'✓ {stock.symbol}: {result.get("iscore", 0):.1f}')
                else:
                    err = (result or {}).get("error", "No result")
                    batch_jobs[jid]["errors"] += 1
                    batch_jobs[jid]["log"].append(f"✗ {stock.symbol}: {err}")
            except Exception as ex:
                db.session.rollback()
                batch_jobs[jid]["errors"] += 1
                batch_jobs[jid]["log"].append(f"✗ {stock.symbol}: {ex}")
            finally:
                batch_jobs[jid]["done"] = idx + 1
                # polite delay so we never hammer Perplexity / OpenAI
                time.sleep(polite_sleep)

        batch_jobs[jid]["status"] = "completed"
        batch_jobs[jid]["finished_at"] = datetime.utcnow().isoformat()
        batch_jobs[jid]["current_symbol"] = ""
        return jid


# ── Wrapper used by APScheduler ───────────────────────────────────────────
def _nightly_job(app):
    """Cron entry point — runs the stale-refresh batch and updates _state."""
    if _state["currently_running"]:
        logger.warning("Nightly I-Score job tick fired while previous run still active — skipping.")
        _state["last_run_status"] = "skipped"
        return

    _state["currently_running"] = True
    _state["last_run_started"] = datetime.now(_IST).isoformat()
    _state["last_run_status"] = "running"
    _persist_state()

    jid = f"nightly-{datetime.now(_IST).strftime('%Y%m%d')}"

    logger.info(
        f"Nightly I-Score batch starting — stale mode "
        f"(threshold={STALE_DAYS_THRESHOLD}d, cap={NIGHTLY_BATCH_LIMIT} stocks/night)"
    )

    try:
        # Import here to get the SAME _batch_jobs dict the manual UI polls.
        from admin_routes import _batch_jobs
        # "stale" mode: unscored first, then oldest-scored, capped at
        # NIGHTLY_BATCH_LIMIT. This ensures ALL ~2167 stocks are refreshed
        # on a rolling ~7-night cycle instead of only the first pass ever.
        run_pending_iscore_batch(app, _batch_jobs, mode="stale", job_id=jid)
        job = _batch_jobs.get(jid, {})
        _state["last_run_status"]  = job.get("status", "completed")
        _state["last_run_total"]   = job.get("total", 0)
        _state["last_run_success"] = job.get("success", 0)
        _state["last_run_errors"]  = job.get("errors", 0)
        _state["last_run_job_id"]  = jid
        logger.info(
            f"Nightly I-Score batch finished: "
            f"{job.get('success', 0)}✓ / {job.get('errors', 0)}✗ of {job.get('total', 0)} stocks "
            f"(stale>{STALE_DAYS_THRESHOLD}d, cap={NIGHTLY_BATCH_LIMIT})"
        )
    except Exception as e:
        _state["last_run_status"] = "failed"
        logger.error(f"Nightly I-Score batch crashed: {e}", exc_info=True)
    finally:
        _state["last_run_finished"] = datetime.now(_IST).isoformat()
        _state["currently_running"] = False
        _persist_state()


# ── Singleton lock + APScheduler wiring ───────────────────────────────────
def _try_acquire_lock(app, lock_id: int) -> bool:
    """Postgres advisory lock — only one gunicorn worker runs the scheduler."""
    if os.environ.get("DISABLE_SCHEDULERS", "").lower() in ("1", "true", "yes"):
        return False
    try:
        from sqlalchemy import text
        from app import db
        with app.app_context():
            conn = db.engine.connect()
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_id}
            ).scalar()
            if got:
                # Never close the connection — keeps the lock for this worker.
                return True
            conn.close()
            return False
    except Exception as e:
        logger.warning(f"I-Score nightly lock check failed ({e}); starting anyway")
        return True


def start_scheduler(app):
    """Boot the nightly cron — call once from app.py at startup."""
    if _state["scheduler_started"]:
        return
    if not _try_acquire_lock(app, _NIGHTLY_ADVISORY_LOCK_ID):
        logger.info("I-Score nightly scheduler skipped on this worker (lock held elsewhere)")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        # Configurable hour/minute via env var (default 02:00 IST = market closed).
        hour   = int(os.environ.get("ISCORE_NIGHTLY_HOUR", "2"))
        minute = int(os.environ.get("ISCORE_NIGHTLY_MINUTE", "0"))

        scheduler = BackgroundScheduler(daemon=True, timezone=_IST)
        scheduler.add_job(
            _nightly_job, CronTrigger(hour=hour, minute=minute, timezone=_IST),
            args=[app], id="iscore_nightly_pending",
            replace_existing=True, max_instances=1, coalesce=True,
            misfire_grace_time=3600,   # if worker was down, still run if <1h late
        )
        scheduler.start()
        _state["scheduler_started"] = True
        job = scheduler.get_job("iscore_nightly_pending")
        if job and job.next_run_time:
            _state["next_run"] = job.next_run_time.isoformat()
        _persist_state()
        logger.info(
            f"Nightly I-Score scheduler started — runs daily at "
            f"{hour:02d}:{minute:02d} IST (next: {_state['next_run']})"
        )
    except Exception as e:
        logger.error(f"Failed to start nightly I-Score scheduler: {e}", exc_info=True)


def trigger_now(app) -> str:
    """Manually fire the nightly job in a background thread (admin endpoint)."""
    if _state["currently_running"]:
        return "already_running"
    t = threading.Thread(target=_nightly_job, args=(app,), daemon=True)
    t.start()
    return "started"
