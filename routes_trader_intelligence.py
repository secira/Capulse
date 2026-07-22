"""
Trader Intelligence Routes — Capulse
Serves the 20-question Trader DNA assessment, result page, and JSON APIs
that power the wizard and the dashboard/profile level badge.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

from flask import render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_wtf.csrf import validate_csrf, CSRFError

from app import app, db, csrf
from models import TraderProfile, TraderAnswer, TraderProgression
from services.trader_intelligence import (
    QUESTIONS, LEVELS, LEVEL_ORDER, DIMENSION_LABELS,
    compute_profile, level_progression, validate_answers,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_profile(user_id: int) -> Optional[TraderProfile]:
    try:
        return TraderProfile.query.filter_by(user_id=user_id).first()
    except Exception as e:
        logger.warning(f"trader_profile lookup failed: {e}")
        db.session.rollback()
        return None


def _profile_to_dict(p: TraderProfile) -> Dict:
    info = LEVELS.get(p.trader_level, LEVELS['L1'])
    return {
        'level':            p.trader_level,
        'level_name':       info['name'],
        'level_color':      info['color'],
        'level_tagline':    info['tagline'],
        'overall_score':    round(p.overall_score, 1),
        'behavioural_risk': p.behavioural_risk,
        'xp_points':        p.xp_points,
        'dimensions': {
            DIMENSION_LABELS['discipline']: round(p.discipline_score, 1),
            DIMENSION_LABELS['emotional']:  round(p.emotional_score, 1),
            DIMENSION_LABELS['strategy']:   round(p.strategy_score, 1),
            DIMENSION_LABELS['risk']:       round(p.risk_score, 1),
            DIMENSION_LABELS['experience']: round(p.experience_score, 1),
            DIMENSION_LABELS['market']:     round(p.market_understanding_score, 1),
        },
        'completed_at':     p.completed_at.isoformat() if p.completed_at else None,
        'updated_at':       p.updated_at.isoformat() if p.updated_at else None,
    }


def get_user_trader_profile(user_id: int) -> Optional[Dict]:
    """Public helper used by dashboard / profile templates via context processor."""
    p = _get_profile(user_id)
    if not p:
        return None
    return _profile_to_dict(p)


# ─────────────────────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/dashboard/trader-intelligence')
@login_required
def trader_intelligence():
    """The 20-question wizard. If the user already has a profile, the page
    still allows retaking — controlled client-side."""
    existing = _get_profile(current_user.id)
    return render_template(
        'dashboard/trader_intelligence/questionnaire.html',
        page_title='Trader DNA Assessment',
        questions=QUESTIONS,
        levels=LEVELS,
        level_order=LEVEL_ORDER,
        existing_profile=_profile_to_dict(existing) if existing else None,
    )


@app.route('/dashboard/trader-intelligence/result')
@login_required
def trader_intelligence_result():
    p = _get_profile(current_user.id)
    if not p:
        flash("Take the 5-minute Trader DNA assessment to discover your level.", "info")
        return redirect(url_for('trader_intelligence'))
    profile = _profile_to_dict(p)
    prog = level_progression(p.trader_level, p.overall_score)
    history = (
        TraderProgression.query
        .filter_by(user_id=current_user.id)
        .order_by(TraderProgression.date_achieved.desc())
        .limit(20)
        .all()
    )
    return render_template(
        'dashboard/trader_intelligence/result.html',
        page_title='Your Trader Intelligence Profile',
        profile=profile,
        progression=prog,
        levels=LEVELS,
        level_order=LEVEL_ORDER,
        history=history,
    )


# ─────────────────────────────────────────────────────────────────────────────
# JSON APIs
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/trader-profile/submit', methods=['POST'])
@login_required
def api_trader_profile_submit():
    """Accept {answers: {Q1..Q20}} and persist the computed profile +
    answer set + level-up audit row.

    Hardening:
      * CSRF validated explicitly (WTF_CSRF_CHECK_DEFAULT is False app-wide).
      * Strict server-side validation of all 20 answers — unknown ids, wrong
        types and out-of-range values are rejected with 400.
      * Idempotent: the user's existing answer set is replaced (not appended)
        and `xp_points` is set deterministically from the latest assessment
        rather than accumulated, preventing XP inflation on retries.
      * Generic 500 message — exception detail is logged, not leaked.
    """
    # 1. CSRF — header set by the wizard, also accepts form/JSON field fallbacks.
    token = (
        request.headers.get('X-CSRFToken')
        or request.headers.get('X-CSRF-Token')
        or (request.get_json(silent=True) or {}).get('csrf_token')
    )
    try:
        validate_csrf(token)
    except CSRFError:
        return jsonify({'success': False, 'error': 'Invalid or missing CSRF token'}), 400

    # 2. Strict validation.
    payload = request.get_json(silent=True) or {}
    ok, errors, answers = validate_answers(payload.get('answers'))
    if not ok:
        return jsonify({'success': False, 'error': 'Invalid answers', 'details': errors}), 400

    result = compute_profile(answers)
    tenant_id = getattr(current_user, 'tenant_id', None) or 'live'

    try:
        # 3. Upsert TraderProfile (deterministic XP, not additive).
        profile = _get_profile(current_user.id)
        previous_level = profile.trader_level if profile else None
        if profile is None:
            profile = TraderProfile(user_id=current_user.id, tenant_id=tenant_id)
            db.session.add(profile)

        dims = result['dimensions_raw']
        profile.trader_level               = result['level']
        profile.overall_score              = result['overall_score']
        profile.discipline_score           = dims['discipline']
        profile.risk_score                 = dims['risk']
        profile.emotional_score            = dims['emotional']
        profile.strategy_score             = dims['strategy']
        profile.experience_score           = dims['experience']
        profile.market_understanding_score = dims['market']
        profile.behavioural_risk           = result['behavioural_risk']
        profile.xp_points                  = int(result['xp_earned'])  # idempotent
        profile.completed_at               = datetime.utcnow()
        profile.updated_at                 = datetime.utcnow()

        db.session.flush()  # so profile.id is available for FK

        # 4. Idempotent answer set — replace any previous answers for this user.
        TraderAnswer.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
        for qid, ans in answers.items():
            stored = json.dumps(ans) if isinstance(ans, (list, dict)) else str(ans)
            db.session.add(TraderAnswer(
                user_id=current_user.id,
                profile_id=profile.id,
                question_id=qid,
                answer=stored,
            ))

        # 5. Audit trail: only when the level actually changes.
        if previous_level != result['level']:
            db.session.add(TraderProgression(
                user_id=current_user.id,
                from_level=previous_level,
                to_level=result['level'],
                overall_score=result['overall_score'],
                xp_earned=int(result['xp_earned']),
            ))

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("trader_profile submit failed")
        return jsonify({'success': False, 'error': 'Could not save profile'}), 500

    return jsonify({
        'success': True,
        'profile': _profile_to_dict(profile),
        'level_changed': previous_level != result['level'],
        'previous_level': previous_level,
        'redirect': url_for('trader_intelligence_result'),
    })


@app.route('/api/trader-profile/result')
@login_required
def api_trader_profile_result():
    p = _get_profile(current_user.id)
    if not p:
        return jsonify({'success': True, 'profile': None})
    return jsonify({'success': True, 'profile': _profile_to_dict(p)})


@app.route('/api/trader-profile/progression')
@login_required
def api_trader_profile_progression():
    p = _get_profile(current_user.id)
    if not p:
        return jsonify({'success': True, 'profile': None, 'progression': None})
    prog = level_progression(p.trader_level, p.overall_score)
    return jsonify({
        'success':     True,
        'profile':     _profile_to_dict(p),
        'progression': prog,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Inject `trader_profile` into every template context (used by dashboard +
# account profile badges). Safe no-op for anonymous users.
# ─────────────────────────────────────────────────────────────────────────────
@app.context_processor
def _inject_trader_profile():
    try:
        if current_user.is_authenticated:
            return {'trader_profile_badge': get_user_trader_profile(current_user.id)}
    except Exception:
        pass
    return {'trader_profile_badge': None}
