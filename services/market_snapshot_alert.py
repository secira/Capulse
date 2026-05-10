"""
Market Intelligence Snapshot — scheduled Telegram alerts.

Sends a consolidated snapshot for the four major Indian indices
(NIFTY 50, BANK NIFTY, FIN NIFTY, SENSEX) three times per market day:

    09:20 IST   — opening read
    12:00 IST   — mid-session check
    13:30 IST   — pre-close direction confirmation

For every index the snapshot reports:
    • Previous close, today's open, day high/low
    • Last traded price + % change
    • Support  (highest-OI Put strike — where buying interest sits)
    • Resistance (highest-OI Call strike — where selling pressure sits)
    • PCR (overall + ATM-window) and Max Pain
    • Market Direction — BULLISH / BEARISH / SIDEWAYS — with one-line reason

All four indices are computed in one message, so Telegram only pings
the group three times a day (no flood, no duplicates).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# (engine_index_code, telegram_label)
INDICES: list[tuple[str, str]] = [
    ('NIFTY',     'NIFTY 50'),
    ('BANKNIFTY', 'BANK NIFTY'),
    ('FINNIFTY',  'FIN NIFTY'),
    ('SENSEX',    'SENSEX'),
]

# Schedule (IST, Mon–Fri only — NSE/BSE market days)
SNAPSHOT_TIMES_IST: list[tuple[int, int, str]] = [
    (9, 20,  'opening'),
    (12, 0,  'midsession'),
    (13, 30, 'preclose'),
]


# ---------------------------------------------------------------- helpers
def _direction_emoji(direction: str) -> str:
    return {'BULLISH': '🟢', 'BEARISH': '🔴', 'SIDEWAYS': '🟡'}.get(direction, '⚪')


def _pcr_bias(pcr: float) -> str:
    """Translate PCR value into a one-word bias string."""
    if pcr >= 1.30:
        return "Strongly Bullish"
    if pcr >= 1.05:
        return "Bullish"
    if pcr <= 0.70:
        return "Strongly Bearish"
    if pcr <= 0.90:
        return "Bearish"
    return "Neutral"


def _build_index_block(index_code: str, label: str) -> str:
    """Build a Markdown block for a single index. Never raises."""
    from services.dhan_service import get_index_quotes, get_option_chain
    from services.nifty_options_engine import NiftyOptionsEngine

    # 1) Spot/OHLC quote
    try:
        quotes = get_index_quotes()
    except Exception as e:
        logger.warning(f"market-snapshot: get_index_quotes failed: {e}")
        quotes = {}

    q = quotes.get(index_code, {})
    ltp        = float(q.get('ltp')   or 0)
    open_p     = float(q.get('open')  or 0)
    high_p     = float(q.get('high')  or 0)
    low_p      = float(q.get('low')   or 0)
    prev_close = float(q.get('close') or 0)   # Dhan returns prev day close as 'close'
    pct_change = float(q.get('pct_change') or 0)

    # 2) Direction (uses cached intraday candles — cheap)
    direction_label = 'SIDEWAYS'
    direction_reason = 'Data unavailable'
    try:
        engine = NiftyOptionsEngine(index=index_code)
        d = engine.get_market_direction()
        direction_label  = d.get('direction', 'SIDEWAYS')
        direction_reason = d.get('reason', '')
    except Exception as e:
        logger.warning(f"market-snapshot: direction failed for {index_code}: {e}")
        engine = None

    # 3) Option-chain derived support / resistance / PCR / max pain
    pcr = atm_pcr = max_pain = 0.0
    support_strike = resistance_strike = None
    try:
        if engine is None:
            engine = NiftyOptionsEngine(index=index_code)
        chain_data = get_option_chain(index_code)
        chain = chain_data.get('option_chain') or {}
        spot  = float(chain_data.get('spot_price') or ltp or 0)
        if chain and spot:
            atm = int(round(spot / engine.strike_interval) * engine.strike_interval)
            oi  = engine._compute_oi_metrics(chain, atm, spot)
            pcr               = float(oi.get('pcr') or 0)
            atm_pcr           = float(oi.get('atm_pcr') or 0)
            max_pain          = float(oi.get('max_pain') or 0)
            top_ce            = oi.get('top_ce_strikes') or []
            top_pe            = oi.get('top_pe_strikes') or []
            if top_ce:
                resistance_strike = top_ce[0].get('strike')
            if top_pe:
                support_strike = top_pe[0].get('strike')
    except Exception as e:
        logger.warning(f"market-snapshot: OI metrics failed for {index_code}: {e}")

    # ---------------- format block ----------------
    emoji   = _direction_emoji(direction_label)
    chg_arr = '▲' if pct_change >= 0 else '▼'

    lines: list[str] = [
        f"{emoji} *{label}*  —  {direction_label}",
    ]

    if ltp:
        lines.append(f"   LTP: *₹{ltp:,.2f}*  {chg_arr} {pct_change:+.2f}%")
    if open_p or prev_close:
        lines.append(
            f"   Open: ₹{open_p:,.2f}   ·   Prev Close: ₹{prev_close:,.2f}"
        )
    if high_p or low_p:
        lines.append(f"   Day H/L: ₹{high_p:,.2f}  /  ₹{low_p:,.2f}")

    # Support & resistance from OI walls
    if support_strike or resistance_strike:
        sup = f"₹{support_strike:,}" if support_strike else "—"
        res = f"₹{resistance_strike:,}" if resistance_strike else "—"
        lines.append(f"   🛡 Support: {sup}   ·   ⛔ Resistance: {res}")
    if max_pain:
        lines.append(f"   🎯 Max Pain: ₹{max_pain:,.0f}")
    if pcr:
        lines.append(
            f"   📊 PCR: {pcr:.2f} ({_pcr_bias(pcr)})"
            f"{f'   ·   ATM PCR: {atm_pcr:.2f}' if atm_pcr else ''}"
        )

    if direction_reason:
        lines.append(f"   _{direction_reason}_")

    return "\n".join(lines)


# ---------------------------------------------------------------- public
def send_market_snapshot(slot: str = 'manual') -> bool:
    """
    Build and send the Market Intelligence snapshot for all four indices.

    `slot` is a free-form label ('opening' / 'midsession' / 'preclose' /
    'manual') used only for the Telegram subtitle — it does not affect
    the data fetched.
    """
    from services.messaging_service import send_telegram_message

    ist_now = datetime.now()  # cron is IST-pinned; assume server already set
    slot_titles = {
        'opening':    "Opening Read · 09:20 IST",
        'midsession': "Mid-Session Check · 12:00 IST",
        'preclose':   "Pre-Close Confirmation · 13:30 IST",
        'manual':     f"Snapshot · {ist_now.strftime('%I:%M %p')} IST",
    }
    subtitle = slot_titles.get(slot, slot_titles['manual'])

    blocks = [_build_index_block(code, label) for code, label in INDICES]

    header = [
        "🧭 *Market Intelligence — Live Snapshot*",
        f"_{ist_now.strftime('%d %b %Y')}  ·  {subtitle}_",
        "",
    ]
    legend = [
        "",
        "_🟢 Bullish · 🔴 Bearish · 🟡 Sideways  ·  PCR > 1 = Put-heavy (bullish), PCR < 1 = Call-heavy (bearish)._",
        "_AI-generated market read — not investment advice._",
    ]
    message = "\n".join(header) + "\n\n".join(blocks) + "\n".join(legend)

    sent = send_telegram_message(message)
    if sent:
        logger.info(f"📨 Market snapshot sent — slot={slot} ({len(INDICES)} indices)")
    else:
        logger.warning(f"Market snapshot Telegram send returned False (slot={slot})")
    return bool(sent)
