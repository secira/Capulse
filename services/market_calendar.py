"""
Market Calendar — NSE/BSE trading-holiday helpers.

Reads the `market_holiday` table (seeded from app.py) and exposes:

  - is_market_holiday(d=None)          → bool
  - get_holiday(d=None)                → dict | None  ({date, name})
  - is_trading_day(d=None)             → bool   (Mon-Fri AND not a holiday)
  - send_holiday_wish_once()           → bool   (idempotent per-IST-date)

The "wish" is a single Telegram greeting per holiday — replaces all market
data broadcasts (pre-market report, mid-session snapshots, close summary,
I-Score digest) on that day.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# Per-process fast-path cache so we don't hit the DB on every scheduler tick.
# The authoritative idempotency guard is a UNIQUE row in `holiday_wish_log`
# (see _claim_wish_slot below) which works across workers, restarts, and hosts.
_last_wish_date: Optional[date] = None


def _today_ist() -> date:
    return datetime.now(_IST).date()


def get_holiday(d: Optional[date] = None) -> Optional[dict]:
    """Return {'date', 'name', 'day_of_week'} if the given (or IST today) is a
    trading holiday, else None."""
    target = d or _today_ist()
    try:
        from app import db
        row = db.session.execute(db.text(
            "SELECT holiday_date, holiday_name, day_of_week "
            "FROM market_holiday WHERE holiday_date = :d LIMIT 1"
        ), {'d': target}).fetchone()
        if not row:
            return None
        return {
            'date': row[0],
            'name': row[1],
            'day_of_week': row[2],
        }
    except Exception as e:
        logger.warning(f"market_calendar.get_holiday failed: {e}")
        return None


def is_market_holiday(d: Optional[date] = None) -> bool:
    return get_holiday(d) is not None


def is_trading_day(d: Optional[date] = None) -> bool:
    target = d or _today_ist()
    if target.weekday() >= 5:  # Sat/Sun
        return False
    return not is_market_holiday(target)


def _claim_wish_slot(today: date) -> bool:
    """Atomically claim the right to send today's wish across all workers /
    hosts. Returns True iff THIS call inserted the row (i.e. nobody else has
    sent today). Uses INSERT … ON CONFLICT DO NOTHING on a UNIQUE wish_date
    column for race-free single-shot semantics.
    """
    try:
        from app import db
        # Lazy CREATE so the helper works even before any migration has been
        # registered for this table. All statements are idempotent.
        db.session.execute(db.text(
            "CREATE TABLE IF NOT EXISTS holiday_wish_log ("
            "  id SERIAL PRIMARY KEY,"
            "  wish_date DATE UNIQUE NOT NULL,"
            "  sent_at TIMESTAMP DEFAULT NOW()"
            ")"
        ))
        result = db.session.execute(db.text(
            "INSERT INTO holiday_wish_log (wish_date) VALUES (:d) "
            "ON CONFLICT (wish_date) DO NOTHING RETURNING id"
        ), {'d': today})
        row = result.fetchone()
        db.session.commit()
        return row is not None
    except Exception as e:
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
        logger.warning(f"_claim_wish_slot failed ({e}); falling back to in-process guard")
        # Fail-closed for duplicates: if DB is unreachable, defer to the
        # in-process flag in the caller (one wish per worker max, never spammy
        # across days). This is a degraded mode, not the normal path.
        return _last_wish_date != today


def send_holiday_wish_once() -> bool:
    """Send a single Telegram holiday greeting today (IST). Idempotent across
    workers, processes and restarts — backed by a UNIQUE row in
    `holiday_wish_log`. Safe to call from every scheduled job; only the first
    caller per IST date actually sends."""
    global _last_wish_date
    today = _today_ist()
    # Fast-path: this worker has already sent today.
    if _last_wish_date == today:
        return False
    holiday = get_holiday(today)
    if not holiday:
        return False
    # Atomic cross-process claim — only the row inserter proceeds to send.
    if not _claim_wish_slot(today):
        _last_wish_date = today  # cache so we skip the DB check next tick
        return False

    name = holiday.get('name') or 'Holiday'
    try:
        from services.messaging_service import send_telegram_message
        msg = (
            f"🌸 *Happy {name}!*\n\n"
            f"Markets are closed today in observance of *{name}*.\n"
            f"_{today.strftime('%A, %d %b %Y')}_\n\n"
            "Wishing you and your family a wonderful day. "
            "We'll be back with market intelligence on the next trading day.\n\n"
            "— Team Capulse 💙"
        )
        ok = send_telegram_message(msg, parse_mode='Markdown')
        if ok:
            _last_wish_date = today
            logger.info(f"📨 Holiday wish sent: {name} ({today})")
        else:
            # Send failed: free the slot so a later retry can send.
            try:
                from app import db
                db.session.execute(db.text(
                    "DELETE FROM holiday_wish_log WHERE wish_date = :d"
                ), {'d': today})
                db.session.commit()
            except Exception:
                try:
                    from app import db as _db
                    _db.session.rollback()
                except Exception:
                    pass
        return bool(ok)
    except Exception as e:
        logger.error(f"send_holiday_wish_once failed: {e}")
        try:
            from app import db
            db.session.execute(db.text(
                "DELETE FROM holiday_wish_log WHERE wish_date = :d"
            ), {'d': today})
            db.session.commit()
        except Exception:
            try:
                from app import db as _db
                _db.session.rollback()
            except Exception:
                pass
        return False
