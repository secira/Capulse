"""
F&O configuration service.

Single-row table `fno_config` controls:
  - SL/Target mode (percent of premium  OR  absolute points)
  - SL/Target value
  - Which fields are included in F&O Telegram alerts

Admin manages everything from  Admin → F&O Settings.
Engine + monitor read via get_fno_config().
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

# ── Telegram field catalogue ─────────────────────────────────────────────────
# (key, label, default_on, description)
TELEGRAM_FIELDS: List[Tuple[str, str, bool, str]] = [
    ("header",        "Header (Index + Signal type + Trade code)", True,  "🔒 NIFTY 50 F&O — TRADE TRIGGER  XYZ123"),
    ("direction",     "Direction (BULLISH / BEARISH / BOTH)",      True,  "🟢 Direction: BULLISH"),
    ("confidence",    "Confidence score & grade",                  True,  "📊 Confidence: 82/100 (HIGH)"),
    ("entry_mode",    "Entry mode (CONFIRMED / EARLY / …)",        True,  "🎯 Entry Mode: CONFIRMED"),
    ("spot_atm",      "Spot price + ATM strike",                   True,  "💰 Spot: ₹24,150.00 | ATM: 24150"),
    ("trades_list",   "Trade list (Symbol / Entry / Target / SL)", True,  "📗 NIFTY 24200 CE — Entry ₹120, Target ₹138, SL ₹108"),
    ("active_trade",  "Active-trade live block (elapsed, LTP, PnL)", True,  "⏱ Running: 12 min | 18 min left  📈 LTP ₹135"),
    ("exit_reason",   "Exit reason (TRADE_EXIT only)",             True,  "🚪 Exit Reason: Target hit"),
    ("timestamp",     "IST timestamp",                             True,  "⏰ 24/05/2026 02:15 PM IST"),
    ("dashboard_link","“View on Target Capital” link",             True,  "https://www.targetcapital.ai/dashboard/fno/nifty"),
]

DEFAULT_TELEGRAM_FIELDS: List[str] = [k for k, _, on, _ in TELEGRAM_FIELDS if on]

_DEFAULTS: Dict[str, Any] = {
    "sl_mode":         "percent",   # percent | absolute
    "sl_value":        10.0,        # 10% of premium  OR  10 points
    "sl_floor":        20.0,        # minimum SL points (only applied when sl_mode=percent)
    "target_mode":     "percent",
    "target_value":    15.0,
    "target_floor":    30.0,
    "telegram_fields": DEFAULT_TELEGRAM_FIELDS,
}


# ── Bootstrap ────────────────────────────────────────────────────────────────
def bootstrap_fno_config() -> None:
    """Create the table + seed the single row if missing. Idempotent."""
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS fno_config (
                id              SERIAL PRIMARY KEY,
                sl_mode         VARCHAR(10)  DEFAULT 'percent',
                sl_value        FLOAT        DEFAULT 10.0,
                sl_floor        FLOAT        DEFAULT 20.0,
                target_mode     VARCHAR(10)  DEFAULT 'percent',
                target_value    FLOAT        DEFAULT 15.0,
                target_floor    FLOAT        DEFAULT 30.0,
                telegram_fields TEXT         DEFAULT '',
                updated_at      TIMESTAMP    DEFAULT NOW(),
                updated_by      VARCHAR(100)
            )
        """))
        db.session.execute(text("""
            INSERT INTO fno_config (sl_mode, sl_value, sl_floor, target_mode, target_value, target_floor, telegram_fields)
            SELECT 'percent', 10.0, 20.0, 'percent', 15.0, 30.0, :fields
            WHERE NOT EXISTS (SELECT 1 FROM fno_config)
        """), {"fields": ",".join(DEFAULT_TELEGRAM_FIELDS)})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"bootstrap_fno_config failed (non-fatal): {e}")


# ── Read / Write ─────────────────────────────────────────────────────────────
def get_fno_config() -> Dict[str, Any]:
    """Return current config as a plain dict. Never raises — falls back to defaults."""
    try:
        row = db.session.execute(text("""
            SELECT sl_mode, sl_value, sl_floor, target_mode, target_value, target_floor, telegram_fields
            FROM fno_config ORDER BY id ASC LIMIT 1
        """)).first()
        if not row:
            return dict(_DEFAULTS)
        fields_csv = (row[6] or "").strip()
        fields = [f.strip() for f in fields_csv.split(",") if f.strip()] if fields_csv else list(DEFAULT_TELEGRAM_FIELDS)
        return {
            "sl_mode":         row[0] or "percent",
            "sl_value":        float(row[1] or 10.0),
            "sl_floor":        float(row[2] or 20.0),
            "target_mode":     row[3] or "percent",
            "target_value":    float(row[4] or 15.0),
            "target_floor":    float(row[5] or 30.0),
            "telegram_fields": fields,
        }
    except Exception as e:
        logger.warning(f"get_fno_config failed, returning defaults: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return dict(_DEFAULTS)


def update_fno_config(
    *,
    sl_mode: str,
    sl_value: float,
    sl_floor: float,
    target_mode: str,
    target_value: float,
    target_floor: float,
    telegram_fields: List[str],
    updated_by: str = "admin",
) -> None:
    """Upsert the single config row."""
    sl_mode = sl_mode if sl_mode in ("percent", "absolute") else "percent"
    target_mode = target_mode if target_mode in ("percent", "absolute") else "percent"
    valid_keys = {k for k, _, _, _ in TELEGRAM_FIELDS}
    fields_csv = ",".join([f for f in telegram_fields if f in valid_keys])

    try:
        # Try update first
        result = db.session.execute(text("""
            UPDATE fno_config SET
                sl_mode = :sl_mode,
                sl_value = :sl_value,
                sl_floor = :sl_floor,
                target_mode = :target_mode,
                target_value = :target_value,
                target_floor = :target_floor,
                telegram_fields = :fields,
                updated_at = NOW(),
                updated_by = :by
            WHERE id = (SELECT id FROM fno_config ORDER BY id ASC LIMIT 1)
        """), {
            "sl_mode": sl_mode, "sl_value": sl_value, "sl_floor": sl_floor,
            "target_mode": target_mode, "target_value": target_value, "target_floor": target_floor,
            "fields": fields_csv, "by": updated_by,
        })
        if result.rowcount == 0:
            db.session.execute(text("""
                INSERT INTO fno_config (sl_mode, sl_value, sl_floor, target_mode, target_value, target_floor, telegram_fields, updated_by)
                VALUES (:sl_mode, :sl_value, :sl_floor, :target_mode, :target_value, :target_floor, :fields, :by)
            """), {
                "sl_mode": sl_mode, "sl_value": sl_value, "sl_floor": sl_floor,
                "target_mode": target_mode, "target_value": target_value, "target_floor": target_floor,
                "fields": fields_csv, "by": updated_by,
            })
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"update_fno_config failed: {e}")
        raise


# ── Helper used by the options engine ────────────────────────────────────────
def compute_sl_target_points(ltp: float) -> Tuple[float, float]:
    """Return (sl_points, target_points) for a given option premium, honouring
    the admin-configured mode (percent vs absolute) and the minimum floors.
    Floors only apply in percent mode."""
    cfg = get_fno_config()
    if cfg["sl_mode"] == "absolute":
        sl_points = float(cfg["sl_value"])
    else:
        sl_points = max(float(cfg["sl_floor"]), round(ltp * (float(cfg["sl_value"]) / 100.0)))
    if cfg["target_mode"] == "absolute":
        target_points = float(cfg["target_value"])
    else:
        target_points = max(float(cfg["target_floor"]), round(ltp * (float(cfg["target_value"]) / 100.0)))
    return sl_points, target_points
