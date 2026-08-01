"""
User Financial Memory Service

Builds a per-user trading profile automatically from chat conversations and
injects it into Claude's system prompt to personalise every response.

Architecture
────────────
• One DB row per user in user_financial_memory.
• List fields (instruments, sectors, watchlist, goals) are union-merged — never
  overwritten — so the profile grows incrementally across sessions.
• Extraction runs asynchronously in a daemon thread after each AI reply; it
  adds zero latency to chat response time.
• To manage API cost, a full Claude extraction fires every _EXTRACT_EVERY_N
  interactions; interim turns just increment the counter.
"""

import json
import logging
import re
import threading
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Display labels ────────────────────────────────────────────────────────────
STYLE_LABELS = {
    'intraday':   'Intraday',
    'swing':      'Swing Trader',
    'positional': 'Positional',
    'long_term':  'Long-Term Investor',
}
RISK_LABELS = {
    'conservative': 'Conservative',
    'moderate':     'Moderate',
    'aggressive':   'Aggressive',
}
CAPITAL_LABELS = {
    'small':  '< ₹5 Lakh',
    'medium': '₹5 L – ₹25 L',
    'large':  '> ₹25 Lakh',
}
INSTRUMENT_LABELS = {
    'equity': 'Equity',
    'fno':    'F&O',
    'mf':     'Mutual Funds',
    'etf':    'ETF',
}
GOAL_LABELS = {
    'wealth_creation': 'Wealth Creation',
    'income':          'Regular Income',
    'hedging':         'Hedging',
    'speculation':     'Speculation',
    'preservation':    'Capital Preservation',
}

# Full extraction fires once every N interactions to manage API cost
_EXTRACT_EVERY_N = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _csv_union(existing: Optional[str], new_items: list) -> str:
    """Union new items into an existing comma-separated list (deduplicating)."""
    existing_set = {x.strip().lower() for x in (existing or '').split(',') if x.strip()}
    for item in new_items:
        if item:
            existing_set.add(str(item).strip().lower())
    return ', '.join(sorted(existing_set))


def _safe_rollback() -> None:
    try:
        from models import db
        db.session.rollback()
    except Exception:
        pass


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_memory(user_id: int) -> Dict[str, Any]:
    """Return the stored financial memory for a user as a plain dict.
    Returns {} when no record exists yet.
    """
    try:
        from models import UserFinancialMemory
        mem = UserFinancialMemory.query.filter_by(user_id=user_id).first()
        if not mem:
            return {}
        return {
            'trading_style':          mem.trading_style,
            'risk_level':             mem.risk_level,
            'preferred_instruments':  mem.preferred_instruments,
            'sectors':                mem.sectors,
            'watchlist':              mem.watchlist,
            'capital_bracket':        mem.capital_bracket,
            'goals':                  mem.goals,
            'psychology_notes':       mem.psychology_notes,
            'interaction_count':      mem.interaction_count or 0,
            'updated_at':             mem.updated_at,
        }
    except Exception as exc:
        logger.warning(f"get_memory({user_id}): {exc}")
        return {}


def update_memory(user_id: int, patch: Dict[str, Any]) -> None:
    """Merge a partial update dict into the user's stored memory.

    Scalar fields (trading_style, risk_level, capital_bracket) are replaced
    only when the incoming value is non-null.  List fields are union-merged so
    knowledge accumulates across sessions without losing earlier signals.
    """
    try:
        from models import UserFinancialMemory, db

        mem = UserFinancialMemory.query.filter_by(user_id=user_id).first()
        if mem is None:
            mem = UserFinancialMemory(user_id=user_id)
            db.session.add(mem)

        # ── Scalar fields ──────────────────────────────────────────────────
        if patch.get('trading_style'):
            mem.trading_style = str(patch['trading_style']).lower()
        if patch.get('risk_level'):
            mem.risk_level = str(patch['risk_level']).lower()
        if patch.get('capital_bracket'):
            mem.capital_bracket = str(patch['capital_bracket']).lower()

        # ── Psychology notes (append; cap at 500 chars) ────────────────────
        if patch.get('psychology_note'):
            note = str(patch['psychology_note']).strip()[:80]
            existing = mem.psychology_notes or ''
            if note and note.lower() not in existing.lower():
                mem.psychology_notes = (existing + '; ' + note).strip('; ')[:500]

        # ── List fields (union-merge) ──────────────────────────────────────
        if patch.get('instruments'):
            mem.preferred_instruments = _csv_union(
                mem.preferred_instruments, patch['instruments'])
        if patch.get('sectors'):
            mem.sectors = _csv_union(mem.sectors, patch['sectors'])
        if patch.get('watchlist'):
            upper = [str(s).upper() for s in patch['watchlist'] if s]
            existing_up = {x.strip().upper()
                           for x in (mem.watchlist or '').split(',') if x.strip()}
            existing_up.update(upper)
            mem.watchlist = ', '.join(sorted(existing_up))
        if patch.get('goals'):
            mem.goals = _csv_union(mem.goals, patch['goals'])

        mem.interaction_count = (mem.interaction_count or 0) + 1
        mem.updated_at = datetime.utcnow()
        db.session.commit()
        logger.debug(f"user_memory: updated memory for user {user_id}")

    except Exception as exc:
        logger.warning(f"update_memory({user_id}): {exc}")
        _safe_rollback()


def _increment_only(user_id: int) -> None:
    """Increment interaction_count without triggering a full LLM extraction."""
    try:
        from models import UserFinancialMemory, db
        mem = UserFinancialMemory.query.filter_by(user_id=user_id).first()
        if mem is None:
            mem = UserFinancialMemory(user_id=user_id)
            db.session.add(mem)
        mem.interaction_count = (mem.interaction_count or 0) + 1
        mem.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        logger.debug(f"_increment_only({user_id}): {exc}")
        _safe_rollback()


def reset_memory(user_id: int) -> bool:
    """Delete all stored financial memory for a user. Returns True on success."""
    try:
        from models import UserFinancialMemory, db
        mem = UserFinancialMemory.query.filter_by(user_id=user_id).first()
        if mem:
            db.session.delete(mem)
            db.session.commit()
        return True
    except Exception as exc:
        logger.warning(f"reset_memory({user_id}): {exc}")
        _safe_rollback()
        return False


# ── Prompt block builder ──────────────────────────────────────────────────────

def build_memory_block(user_id: int) -> str:
    """Format the stored profile as a [USER TRADING PROFILE] block for Claude.
    Returns '' when no meaningful profile data exists yet.
    """
    mem = get_memory(user_id)
    if not mem:
        return ''

    lines = []

    style = mem.get('trading_style')
    if style:
        lines.append(
            f"Trading Style: {STYLE_LABELS.get(style, style.replace('_', ' ').title())}")

    risk = mem.get('risk_level')
    if risk:
        lines.append(f"Risk Appetite: {RISK_LABELS.get(risk, risk.title())}")

    cap = mem.get('capital_bracket')
    if cap:
        lines.append(f"Capital Range: {CAPITAL_LABELS.get(cap, cap)}")

    instr = mem.get('preferred_instruments')
    if instr:
        parts = [x.strip() for x in instr.split(',') if x.strip()]
        if parts:
            labels = [INSTRUMENT_LABELS.get(p, p.upper()) for p in parts]
            lines.append(f"Instruments: {', '.join(labels)}")

    sectors = mem.get('sectors')
    if sectors:
        parts = [x.strip().title() for x in sectors.split(',') if x.strip()]
        if parts:
            lines.append(f"Sectors: {', '.join(parts[:6])}")

    watchlist = mem.get('watchlist')
    if watchlist:
        parts = [x.strip().upper() for x in watchlist.split(',') if x.strip()]
        if parts:
            lines.append(f"Stocks Mentioned: {', '.join(parts[:10])}")

    goals = mem.get('goals')
    if goals:
        parts = [x.strip() for x in goals.split(',') if x.strip()]
        if parts:
            labels = [GOAL_LABELS.get(p, p.replace('_', ' ').title()) for p in parts]
            lines.append(f"Goals: {', '.join(labels)}")

    notes = mem.get('psychology_notes')
    if notes:
        lines.append(f"Trader Notes: {notes[:200]}")

    if not lines:
        return ''

    block = "[USER TRADING PROFILE — personalise your response to this trader]\n"
    block += '\n'.join(f"  • {line}" for line in lines)
    block += (
        "\n[Tailor your answer to match this user's style, risk level, and goals. "
        "Do not mention that you have a profile — just respond naturally and relevantly.]"
    )
    return block


# ── LLM extraction ────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """You extract trading profile signals from user chat messages.
Return ONLY valid compact JSON — no prose, no markdown fences.

Output schema (all fields optional):
{
  "trading_style": "intraday"|"swing"|"positional"|"long_term"|null,
  "risk_level": "conservative"|"moderate"|"aggressive"|null,
  "instruments": ["equity","fno","mf","etf"],
  "sectors": ["banking","it","pharma","auto","energy","fmcg","realty","metal","infra"],
  "watchlist": ["NSE_SYMBOL"],
  "capital_bracket": "small"|"medium"|"large"|null,
  "goals": ["wealth_creation","income","hedging","speculation","preservation"],
  "psychology_note": "max-80-char insight into this trader's psychology or null",
  "nothing_new": true
}

Rules:
- capital_bracket: "small" <₹5L, "medium" ₹5–25L, "large" >₹25L
- Only extract what is EXPLICITLY stated or STRONGLY implied by the user's own words
- Do NOT infer from topic alone (asking about F&O ≠ F&O trader; asking about SIP ≠ MF investor)
- watchlist: only real NSE symbols (RELIANCE, HDFCBANK, TATAMOTORS, NIFTY, etc.)
- For generic market questions, news, or education → {"nothing_new": true}"""


def extract_and_update_memory(user_id: int, user_message: str, ai_response: str) -> None:
    """Run a lightweight Claude extraction to find profile signals in the message.

    Designed to run in a daemon thread — never raises, never blocks.
    Fires a full extraction every _EXTRACT_EVERY_N interactions; otherwise
    just increments the counter.
    """
    try:
        mem = get_memory(user_id)
        count = mem.get('interaction_count') or 0

        if count % _EXTRACT_EVERY_N != 0:
            _increment_only(user_id)
            return

        from services.llm_client import get_llm_client, Model

        llm   = get_llm_client()
        patch = llm.structured_output(
            [{'role': 'user', 'content': f"USER MESSAGE:\n{user_message[:600]}"}],
            system=_EXTRACT_SYSTEM,
            max_tokens=200,
            temperature=0.1,
            model=Model.FAST,
        )
        if not patch:
            _increment_only(user_id)
            return

        if patch.get('nothing_new'):
            _increment_only(user_id)
            return

        patch.pop('nothing_new', None)
        update_memory(user_id, patch)
        nonempty = [k for k, v in patch.items() if v]
        logger.info(
            f"user_memory: extracted [{', '.join(nonempty)}] for user {user_id}")

    except Exception as exc:
        logger.debug(f"extract_and_update_memory({user_id}): {exc}")
        _increment_only(user_id)


def async_extract(user_id: int, user_message: str, ai_response: str) -> None:
    """Fire extract_and_update_memory in a daemon background thread.

    The thread wrapper acquires a Flask application context so that all
    SQLAlchemy / Flask-Login calls inside the extraction path work correctly
    outside the request cycle.  The app import is deferred to avoid circular
    imports at module load time.
    """
    def _target_with_ctx() -> None:
        try:
            from app import app as _flask_app          # deferred — avoids circular import
            with _flask_app.app_context():
                extract_and_update_memory(user_id, user_message, ai_response)
        except Exception as exc:
            logger.debug(f"async_extract thread({user_id}): {exc}")

    try:
        t = threading.Thread(
            target=_target_with_ctx,
            daemon=True,
            name=f'mem_extract_{user_id}',
        )
        t.start()
    except Exception as exc:
        logger.debug(f"async_extract({user_id}): {exc}")
