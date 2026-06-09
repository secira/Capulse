"""
F&O configuration service.

Single-row table `fno_config` controls:
  - Per-index Stop-Loss / Target points (absolute points only — no percent mode)
  - Per-index "send to Telegram" flag (only ticked indices broadcast alerts)
  - Which fields are included in F&O Telegram alerts

Admin manages everything from  Admin → F&O Settings.
Engine + monitor read via get_fno_config() / compute_sl_target_points() /
is_index_telegram_enabled().
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

# ── Indices ──────────────────────────────────────────────────────────────────
# (key, display label) — keep in sync with services/fno_monitor.SCAN_INDICES.
FNO_INDICES: List[Tuple[str, str]] = [
    ("NIFTY",     "NIFTY 50"),
    ("BANKNIFTY", "Bank Nifty"),
    ("FINNIFTY",  "Fin Nifty"),
    ("SENSEX",    "SENSEX"),
]
_INDEX_KEYS = [k for k, _ in FNO_INDICES]

# Per-index SL/Target defaults (absolute option premium points).
# R:R is enforced at minimum 1:2 (SL : T1) in the engine regardless of
# what is stored here. These defaults give 1:2 / 1:3 / 1:4 for T1/T2/T3.
# Wider SL (15% of typical ATM premium) reduces premature stop-outs on
# normal intraday noise that historically caused >60% of SL hits.
_INDEX_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "NIFTY":     {"sl_points": 30.0,  "target_points": 60.0,  "target_2_points": 90.0,  "target_3_points": 120.0, "telegram": True},
    "BANKNIFTY": {"sl_points": 60.0,  "target_points": 120.0, "target_2_points": 180.0, "target_3_points": 240.0, "telegram": True},
    "FINNIFTY":  {"sl_points": 30.0,  "target_points": 60.0,  "target_2_points": 90.0,  "target_3_points": 120.0, "telegram": True},
    "SENSEX":    {"sl_points": 60.0,  "target_points": 120.0, "target_2_points": 180.0, "target_3_points": 240.0, "telegram": True},
}


def _col(index_key: str, suffix: str) -> str:
    """Return the table column name for a given index + suffix."""
    return f"{index_key.lower()}_{suffix}"


# ── Telegram field catalogue ─────────────────────────────────────────────────
# (key, label, default_on, description)
TELEGRAM_MODE_OPTIONS = [
    ("teaser", "Teaser (direction + conviction only — encourages site visits)"),
    ("full",   "Full (all selected fields, including entry / SL / targets)"),
]
DEFAULT_TELEGRAM_MODE = "teaser"

TELEGRAM_FIELDS: List[Tuple[str, str, bool, str]] = [
    ("header",        "Header (Index + Signal type + Trade code)", True,  "🔒 NIFTY 50 F&O — TRADE TRIGGER  XYZ123"),
    ("direction",     "Direction (BULLISH / BEARISH / BOTH)",      True,  "🟢 Direction: BULLISH"),
    ("confidence",    "Confidence score & grade",                  True,  "📊 Confidence: 82/100 (HIGH)"),
    ("entry_mode",    "Entry mode (CONFIRMED / EARLY / …)",        True,  "🎯 Entry Mode: CONFIRMED"),
    ("spot_atm",      "Spot price + ATM strike",                   True,  "💰 Spot: ₹24,150.00 | ATM: 24150"),
    ("trades_list",   "Trade list (Symbol / Entry / T1 / T2 / T3 / SL)", True,  "📗 NIFTY 24200 CE — Entry ₹120, T1 ₹138, T2 ₹158, T3 ₹178, SL ₹108"),
    ("active_trade",  "Active-trade live block (elapsed, LTP, PnL)", True,  "⏱ Running: 12 min | 18 min left  📈 LTP ₹135"),
    ("exit_reason",   "Exit reason (TRADE_EXIT only)",             True,  "🚪 Exit Reason: Target hit"),
    ("timestamp",     "IST timestamp",                             True,  "⏰ 24/05/2026 02:15 PM IST"),
    ("dashboard_link","“View on Target Capital” link",             True,  "https://www.targetcapital.ai/dashboard/fno/nifty"),
]

DEFAULT_TELEGRAM_FIELDS: List[str] = [k for k, _, on, _ in TELEGRAM_FIELDS if on]


def _default_indices() -> Dict[str, Dict[str, Any]]:
    """Deep copy of the per-index defaults."""
    return {k: dict(v) for k, v in _INDEX_DEFAULTS.items()}


# ── Bootstrap ────────────────────────────────────────────────────────────────
def bootstrap_fno_config() -> None:
    """Create the table + seed the single row if missing. Idempotent.

    The legacy percent-mode columns (sl_mode/sl_value/… ) are left untouched for
    backward compatibility; new per-index point columns are added on the fly."""
    try:
        # Base table (legacy columns kept so older deployments don't break).
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS fno_config (
                id              SERIAL PRIMARY KEY,
                telegram_fields TEXT         DEFAULT '',
                updated_at      TIMESTAMP    DEFAULT NOW(),
                updated_by      VARCHAR(100)
            )
        """))

        # Per-index point + telegram columns (additive, safe to re-run).
        for key in _INDEX_KEYS:
            d = _INDEX_DEFAULTS[key]
            db.session.execute(text(
                f"ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS "
                f"{_col(key, 'sl_points')} FLOAT DEFAULT {d['sl_points']}"
            ))
            db.session.execute(text(
                f"ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS "
                f"{_col(key, 'target_points')} FLOAT DEFAULT {d['target_points']}"
            ))
            db.session.execute(text(
                f"ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS "
                f"{_col(key, 'target_2_points')} FLOAT DEFAULT {d['target_2_points']}"
            ))
            db.session.execute(text(
                f"ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS "
                f"{_col(key, 'target_3_points')} FLOAT DEFAULT {d['target_3_points']}"
            ))
            db.session.execute(text(
                f"ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS "
                f"{_col(key, 'telegram')} BOOLEAN DEFAULT {'TRUE' if d['telegram'] else 'FALSE'}"
            ))

        # telegram_mode column
        db.session.execute(text(
            "ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS "
            "telegram_mode VARCHAR(10) DEFAULT 'teaser'"
        ))

        # Seed the single row if the table is empty.
        db.session.execute(text("""
            INSERT INTO fno_config (telegram_fields, telegram_mode)
            SELECT :fields, :mode
            WHERE NOT EXISTS (SELECT 1 FROM fno_config)
        """), {"fields": ",".join(DEFAULT_TELEGRAM_FIELDS), "mode": DEFAULT_TELEGRAM_MODE})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"bootstrap_fno_config failed (non-fatal): {e}")


# ── Read / Write ─────────────────────────────────────────────────────────────
def get_fno_config() -> Dict[str, Any]:
    """Return current config as a plain dict. Never raises — falls back to defaults.

    Shape:
        {
            "indices": {
                "NIFTY":     {"sl_points": .., "target_points": .., "target_2_points": .., "target_3_points": .., "telegram": bool},
                "BANKNIFTY": {...}, "FINNIFTY": {...}, "SENSEX": {...},
            },
            "telegram_fields": [...],
        }
    """
    try:
        select_cols = ["telegram_fields", "telegram_mode"]
        for key in _INDEX_KEYS:
            select_cols += [_col(key, 'sl_points'), _col(key, 'target_points'),
                            _col(key, 'target_2_points'), _col(key, 'target_3_points'),
                            _col(key, 'telegram')]
        row = db.session.execute(text(
            f"SELECT {', '.join(select_cols)} FROM fno_config ORDER BY id ASC LIMIT 1"
        )).first()

        if not row:
            return {"indices": _default_indices(), "telegram_fields": list(DEFAULT_TELEGRAM_FIELDS),
                    "telegram_mode": DEFAULT_TELEGRAM_MODE}

        m = row._mapping
        fields_csv = (m.get("telegram_fields") or "").strip()
        fields = [f.strip() for f in fields_csv.split(",") if f.strip()] if fields_csv else list(DEFAULT_TELEGRAM_FIELDS)
        mode = (m.get("telegram_mode") or DEFAULT_TELEGRAM_MODE).strip()
        if mode not in ("teaser", "full"):
            mode = DEFAULT_TELEGRAM_MODE

        indices: Dict[str, Dict[str, Any]] = {}
        for key in _INDEX_KEYS:
            d = _INDEX_DEFAULTS[key]
            sl_raw  = m.get(_col(key, 'sl_points'))
            t1_raw  = m.get(_col(key, 'target_points'))
            t2_raw  = m.get(_col(key, 'target_2_points'))
            t3_raw  = m.get(_col(key, 'target_3_points'))
            tel_raw = m.get(_col(key, 'telegram'))
            indices[key] = {
                "sl_points":       float(sl_raw  if sl_raw  is not None else d["sl_points"]),
                "target_points":   float(t1_raw  if t1_raw  is not None else d["target_points"]),
                "target_2_points": float(t2_raw  if t2_raw  is not None else d["target_2_points"]),
                "target_3_points": float(t3_raw  if t3_raw  is not None else d["target_3_points"]),
                "telegram":        bool(tel_raw  if tel_raw is not None else d["telegram"]),
            }
        return {"indices": indices, "telegram_fields": fields, "telegram_mode": mode}
    except Exception as e:
        logger.warning(f"get_fno_config failed, returning defaults: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"indices": _default_indices(), "telegram_fields": list(DEFAULT_TELEGRAM_FIELDS),
                "telegram_mode": DEFAULT_TELEGRAM_MODE}


def update_fno_config(
    *,
    indices: Dict[str, Dict[str, Any]],
    telegram_fields: List[str],
    telegram_mode: str = DEFAULT_TELEGRAM_MODE,
    updated_by: str = "admin",
) -> None:
    """Upsert the single config row with per-index points + telegram flags."""
    valid_keys = {k for k, _, _, _ in TELEGRAM_FIELDS}
    fields_csv = ",".join([f for f in telegram_fields if f in valid_keys])
    mode = telegram_mode if telegram_mode in ("teaser", "full") else DEFAULT_TELEGRAM_MODE

    set_clauses = ["telegram_fields = :fields", "telegram_mode = :mode",
                   "updated_at = NOW()", "updated_by = :by"]
    params: Dict[str, Any] = {"fields": fields_csv, "mode": mode, "by": updated_by}

    for key in _INDEX_KEYS:
        d = _INDEX_DEFAULTS[key]
        cfg = indices.get(key, {}) or {}
        sl  = float(cfg.get("sl_points",       d["sl_points"]))
        t1  = float(cfg.get("target_points",   d["target_points"]))
        t2  = float(cfg.get("target_2_points", d["target_2_points"]))
        t3  = float(cfg.get("target_3_points", d["target_3_points"]))
        tel = bool(cfg.get("telegram",         d["telegram"]))
        set_clauses += [
            f"{_col(key, 'sl_points')} = :{key}_sl",
            f"{_col(key, 'target_points')} = :{key}_t1",
            f"{_col(key, 'target_2_points')} = :{key}_t2",
            f"{_col(key, 'target_3_points')} = :{key}_t3",
            f"{_col(key, 'telegram')} = :{key}_tel",
        ]
        params[f"{key}_sl"]  = sl
        params[f"{key}_t1"]  = t1
        params[f"{key}_t2"]  = t2
        params[f"{key}_t3"]  = t3
        params[f"{key}_tel"] = tel

    try:
        result = db.session.execute(text(
            f"UPDATE fno_config SET {', '.join(set_clauses)} "
            f"WHERE id = (SELECT id FROM fno_config ORDER BY id ASC LIMIT 1)"
        ), params)

        if result.rowcount == 0:
            # No row yet — insert one then re-apply the values.
            db.session.execute(text(
                "INSERT INTO fno_config (telegram_fields) VALUES (:fields)"
            ), {"fields": fields_csv})
            db.session.execute(text(
                f"UPDATE fno_config SET {', '.join(set_clauses)} "
                f"WHERE id = (SELECT id FROM fno_config ORDER BY id ASC LIMIT 1)"
            ), params)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"update_fno_config failed: {e}")
        raise


# ── Helpers used by the options engine + monitor ─────────────────────────────
def compute_sl_target_points(ltp: float, index: str = "NIFTY") -> Tuple[float, float, float, float]:
    """Return (sl_points, target_1_points, target_2_points, target_3_points) for the given index.

    Absolute points only — `ltp` is accepted for backward compatibility but the
    configured points are used directly. Each index has its own SL/Target points
    configured in Admin → F&O Settings."""
    index_key = (index or "NIFTY").upper()
    cfg = get_fno_config()
    idx = cfg["indices"].get(index_key) or _INDEX_DEFAULTS.get(index_key) or _INDEX_DEFAULTS["NIFTY"]
    d   = _INDEX_DEFAULTS.get(index_key, _INDEX_DEFAULTS["NIFTY"])
    return (
        float(idx.get("sl_points",       d["sl_points"])),
        float(idx.get("target_points",   d["target_points"])),
        float(idx.get("target_2_points", d["target_2_points"])),
        float(idx.get("target_3_points", d["target_3_points"])),
    )


def is_index_telegram_enabled(index: str) -> bool:
    """Return True if the given index is allowed to broadcast Telegram alerts."""
    index_key = (index or "").upper()
    try:
        cfg = get_fno_config()
        idx = cfg["indices"].get(index_key)
        if idx is None:
            # Unknown index — default to the per-index default (or True).
            return bool(_INDEX_DEFAULTS.get(index_key, {}).get("telegram", True))
        return bool(idx["telegram"])
    except Exception as e:
        logger.warning(f"is_index_telegram_enabled failed for {index}: {e}")
        return True
