"""
Trader Intelligence Profiling — Capulse's behavioural classification
engine. Drives the 20-question Trader DNA assessment, computes a weighted
score across 6 dimensions and assigns one of 6 levels (L1..L6).

The questions, dimension weights and level thresholds are defined here as
the single source of truth — consumed by both the API and the wizard UI.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Strict server-side validation. Never trust the wizard — clients can craft
# arbitrary payloads, swap multi/single types, send unknown values, etc.
# ─────────────────────────────────────────────────────────────────────────────
def validate_answers(answers: Any) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Returns (ok, errors, normalised_answers). The caller should reject the
    payload if ok=False and never feed unvalidated data to compute_profile."""
    from services.trader_intelligence import QUESTIONS  # noqa: WPS433 (self-ref ok)
    errors: List[str] = []
    norm: Dict[str, Any] = {}

    if not isinstance(answers, dict) or not answers:
        return False, ['answers must be a non-empty object'], {}

    expected_ids = {q['id'] for q in QUESTIONS}
    unknown = set(answers.keys()) - expected_ids
    if unknown:
        errors.append(f"unknown question ids: {sorted(unknown)}")

    by_id = {q['id']: q for q in QUESTIONS}
    for qid in sorted(expected_ids):
        if qid not in answers:
            errors.append(f"missing answer for {qid}")
            continue
        q = by_id[qid]
        raw = answers[qid]

        if q['type'] == 'scale':
            try:
                n = int(raw)
            except (TypeError, ValueError):
                errors.append(f"{qid}: scale value must be an integer")
                continue
            lo, hi = int(q.get('min', 1)), int(q.get('max', 10))
            if n < lo or n > hi:
                errors.append(f"{qid}: scale must be between {lo} and {hi}")
                continue
            norm[qid] = n

        elif q['type'] == 'single':
            if isinstance(raw, (list, dict)):
                errors.append(f"{qid}: single-choice expects scalar, not list/object")
                continue
            allowed = {o['value'] for o in q.get('options', [])}
            if raw not in allowed:
                errors.append(f"{qid}: '{raw}' not in allowed options")
                continue
            norm[qid] = raw

        elif q['type'] == 'multi':
            if not isinstance(raw, list):
                errors.append(f"{qid}: multi-choice expects a list")
                continue
            allowed = {o['value'] for o in q.get('options', [])}
            cleaned: List[str] = []
            for v in raw:
                if v in allowed and v not in cleaned:
                    cleaned.append(v)
            if not cleaned:
                errors.append(f"{qid}: at least one valid option required")
                continue
            norm[qid] = cleaned

        else:
            errors.append(f"{qid}: unsupported question type")

    return (len(errors) == 0), errors, norm


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────
LEVELS = {
    'L1': {
        'code': 'L1', 'name': 'Beginner', 'min_score': 0, 'max_score': 25,
        'color': '#ef4444',
        'tagline': 'Just starting out — emotional, random entries, no process.',
        'traits': ['Emotional decisions', 'Random entries', 'No defined process', 'Social-media driven'],
        'next': 'L2',
    },
    'L2': {
        'code': 'L2', 'name': 'Learning Trader', 'min_score': 26, 'max_score': 40,
        'color': '#f97316',
        'tagline': 'Understands basics, inconsistent discipline, experimenting.',
        'traits': ['Understands basics', 'Inconsistent discipline', 'Experimenting with strategies'],
        'next': 'L3',
    },
    'L3': {
        'code': 'L3', 'name': 'Active Trader', 'min_score': 41, 'max_score': 55,
        'color': '#eab308',
        'tagline': 'Regular trader, strategy-aware, inconsistent execution.',
        'traits': ['Regular trader', 'Strategy-aware', 'Inconsistent execution'],
        'next': 'L4',
    },
    'L4': {
        'code': 'L4', 'name': 'Disciplined Trader', 'min_score': 56, 'max_score': 70,
        'color': '#22c55e',
        'tagline': 'Risk-managed, structured, emotionally stable.',
        'traits': ['Risk-managed', 'Structured process', 'Emotionally stable'],
        'next': 'L5',
    },
    'L5': {
        'code': 'L5', 'name': 'Advanced / Systematic Trader', 'min_score': 71, 'max_score': 85,
        'color': '#3b82f6',
        'tagline': 'Process-driven, probabilistic, portfolio-aware.',
        'traits': ['Process-driven', 'Probabilistic thinking', 'Strategy-focused', 'Portfolio-aware'],
        'next': 'L6',
    },
    'L6': {
        'code': 'L6', 'name': 'Professional-Level Trader', 'min_score': 86, 'max_score': 100,
        'color': '#8b5cf6',
        'tagline': 'Data-driven, behaviour-controlled, portfolio optimised.',
        'traits': ['Data-driven', 'Behaviour-controlled', 'Portfolio optimised', 'Systematic execution'],
        'next': None,
    },
}

LEVEL_ORDER = ['L1', 'L2', 'L3', 'L4', 'L5', 'L6']


# ─────────────────────────────────────────────────────────────────────────────
# DIMENSION WEIGHTS  (must sum to 1.00)
# ─────────────────────────────────────────────────────────────────────────────
DIMENSIONS = ['discipline', 'emotional', 'strategy', 'risk', 'experience', 'market']
DIMENSION_WEIGHTS = {
    'discipline':  0.25,
    'emotional':   0.25,
    'strategy':    0.20,
    'risk':        0.20,
    'experience':  0.05,
    'market':      0.05,
}

DIMENSION_LABELS = {
    'discipline':  'Discipline',
    'emotional':   'Emotional Control',
    'strategy':    'Strategy Maturity',
    'risk':        'Risk Management',
    'experience':  'Experience',
    'market':      'Market Understanding',
}


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL-UP REQUIREMENTS  (gamification roadmap)
# ─────────────────────────────────────────────────────────────────────────────
LEVEL_UP_REQUIREMENTS = {
    'L1': ['Use stop-loss consistently', 'Avoid revenge trading', 'Complete onboarding education modules'],
    'L2': ['Maintain a trading journal', 'Follow structured setups', 'Reduce impulsive trades'],
    'L3': ['Consistent risk sizing', 'Stable emotional control', 'Reduced FOMO score'],
    'L4': ['Portfolio-level risk management', 'Track expectancy', 'Systematic execution'],
    'L5': ['Strategy consistency', 'Drawdown management', 'Advanced risk discipline', 'Behavioural stability'],
    'L6': ['You are at the highest level — keep refining and mentor others.'],
}


# ─────────────────────────────────────────────────────────────────────────────
# THE 20 QUESTIONS
# Each option carries per-dimension scores on a 0..100 scale. Final dimension
# score = average of that dimension's contributing question scores.
# ─────────────────────────────────────────────────────────────────────────────
def _opt(value: str, label: str, **scores) -> Dict:
    return {'value': value, 'label': label, 'scores': scores}


QUESTIONS: List[Dict] = [
    # ── SECTION 1: EXPERIENCE ────────────────────────────────────────────────
    {
        'id': 'Q1', 'section': 'Experience',
        'text': 'How long have you been trading?',
        'type': 'single',
        'options': [
            _opt('never',   'Never traded',     experience=0),
            _opt('lt6m',    '< 6 months',       experience=20),
            _opt('6_12m',   '6–12 months',      experience=45),
            _opt('1_3y',    '1–3 years',        experience=75),
            _opt('3y_plus', '3+ years',         experience=100),
        ],
    },
    {
        'id': 'Q2', 'section': 'Experience',
        'text': 'Which market do you trade most?',
        'type': 'single',
        'options': [
            _opt('equity',   'Equity investing',  strategy=70, risk=70),
            _opt('swing',    'Swing trading',     strategy=65, risk=55),
            _opt('intraday', 'Intraday',          strategy=55, risk=40),
            _opt('fno',      'Options / F&O',     strategy=60, risk=35),
            _opt('crypto',   'Crypto',            strategy=40, risk=25),
        ],
    },
    {
        'id': 'Q3', 'section': 'Experience',
        'text': 'How often do you trade?',
        'type': 'single',
        'options': [
            _opt('rarely',     'Rarely',                  discipline=70, risk=70),
            _opt('weekly',     'Weekly',                  discipline=75, risk=70),
            _opt('daily',      'Daily',                   discipline=55, risk=50),
            _opt('multi_day',  'Multiple times/day',      discipline=35, risk=30),
        ],
    },
    {
        'id': 'Q4', 'section': 'Experience',
        'text': 'Are you currently profitable?',
        'type': 'single',
        'options': [
            _opt('losing',     'Mostly losing',           experience=20, strategy=20, risk=25, discipline=25),
            _opt('breakeven',  'Break-even',              experience=45, strategy=45, risk=50, discipline=50),
            _opt('slight',     'Slightly profitable',     experience=65, strategy=65, risk=65, discipline=70),
            _opt('consistent', 'Consistently profitable', experience=95, strategy=95, risk=90, discipline=90),
        ],
    },

    # ── SECTION 2: STRATEGY & EXECUTION ──────────────────────────────────────
    {
        'id': 'Q5', 'section': 'Strategy & Execution',
        'text': 'How do you usually decide trades?',
        'type': 'single',
        'options': [
            _opt('telegram',   'Telegram / social media',   strategy=10,  risk=15),
            _opt('news',       'News',                      strategy=30,  risk=35),
            _opt('indicators', 'Indicators',                strategy=55,  risk=55),
            _opt('priceaction','Price action',              strategy=75,  risk=70),
            _opt('structured', 'Structured strategy',       strategy=95,  risk=85),
        ],
    },
    {
        'id': 'Q6', 'section': 'Strategy & Execution',
        'text': 'Do you use stop-loss consistently?',
        'type': 'single',
        'options': [
            _opt('never',     'Never',     risk=5,   discipline=10),
            _opt('sometimes', 'Sometimes', risk=35,  discipline=35),
            _opt('mostly',    'Mostly',    risk=70,  discipline=70),
            _opt('always',    'Always',    risk=100, discipline=95),
        ],
    },
    {
        'id': 'Q7', 'section': 'Strategy & Execution',
        'text': 'Do you follow a predefined setup before entering trades?',
        'type': 'single',
        'options': [
            _opt('never',     'Never',     strategy=10, discipline=15),
            _opt('sometimes', 'Sometimes', strategy=40, discipline=40),
            _opt('often',     'Often',     strategy=70, discipline=70),
            _opt('always',    'Always',    strategy=95, discipline=95),
        ],
    },
    {
        'id': 'Q8', 'section': 'Strategy & Execution',
        'text': 'How do you manage position size?',
        'type': 'single',
        'options': [
            _opt('random',    'Randomly',           risk=10,  discipline=15),
            _opt('fixed_qty', 'Fixed quantity',     risk=40,  discipline=45),
            _opt('pct_cap',   '% of capital',       risk=75,  discipline=70),
            _opt('risk_based','Risk-based sizing',  risk=100, discipline=90),
        ],
    },
    {
        'id': 'Q9', 'section': 'Strategy & Execution',
        'text': 'Do you maintain a trading journal or review trades?',
        'type': 'single',
        'options': [
            _opt('never',     'Never',         discipline=10, strategy=15),
            _opt('sometimes', 'Occasionally',  discipline=40, strategy=40),
            _opt('weekly',    'Weekly',        discipline=75, strategy=70),
            _opt('regularly', 'Regularly',     discipline=100, strategy=90),
        ],
    },

    # ── SECTION 3: BEHAVIOURAL INTELLIGENCE ──────────────────────────────────
    {
        'id': 'Q10', 'section': 'Behavioural Intelligence',
        'text': 'After a loss, what do you usually do?',
        'type': 'single',
        'options': [
            _opt('recover',   'Try to recover losses immediately', emotional=10, discipline=15),
            _opt('increase',  'Increase trade size',               emotional=5,  discipline=5),
            _opt('pause',     'Pause briefly',                     emotional=70, discipline=70),
            _opt('reeval',    'Re-evaluate setup calmly',          emotional=100, discipline=95),
        ],
    },
    {
        'id': 'Q11', 'section': 'Behavioural Intelligence',
        'text': 'How often do you take trades due to FOMO?',
        'type': 'single',
        'options': [
            _opt('very_often', 'Very often', emotional=10,  discipline=15),
            _opt('sometimes',  'Sometimes',  emotional=40,  discipline=45),
            _opt('rarely',     'Rarely',     emotional=75,  discipline=75),
            _opt('never',      'Never',      emotional=100, discipline=95),
        ],
    },
    {
        'id': 'Q12', 'section': 'Behavioural Intelligence',
        'text': 'Do you move stop-losses emotionally?',
        'type': 'single',
        'options': [
            _opt('very_often', 'Very often', emotional=10,  risk=15,  discipline=15),
            _opt('sometimes',  'Sometimes',  emotional=40,  risk=40,  discipline=40),
            _opt('rarely',     'Rarely',     emotional=75,  risk=75,  discipline=75),
            _opt('never',      'Never',      emotional=100, risk=100, discipline=95),
        ],
    },
    {
        'id': 'Q13', 'section': 'Behavioural Intelligence',
        'text': 'What affects your trading most?',
        'type': 'single',
        'options': [
            _opt('fear',          'Fear',           emotional=30),
            _opt('greed',         'Greed',          emotional=25),
            _opt('fomo',          'FOMO',           emotional=20),
            _opt('impatience',    'Impatience',     emotional=30),
            _opt('overconfidence','Overconfidence', emotional=25),
            _opt('none',          'None — I stay neutral', emotional=100),
        ],
    },
    {
        'id': 'Q14', 'section': 'Behavioural Intelligence',
        'text': 'How often do you overtrade?',
        'type': 'single',
        'options': [
            _opt('daily',        'Daily',        emotional=10,  discipline=10),
            _opt('frequently',   'Frequently',   emotional=30,  discipline=30),
            _opt('occasionally', 'Occasionally', emotional=65,  discipline=65),
            _opt('rarely',       'Rarely',       emotional=100, discipline=95),
        ],
    },
    {
        'id': 'Q15', 'section': 'Behavioural Intelligence',
        'text': 'What is your biggest challenge?',
        'type': 'single',
        'options': [
            _opt('discipline', 'Discipline',         discipline=20, emotional=30),
            _opt('entries',    'Entries',            strategy=35),
            _opt('exits',      'Exits',              strategy=35,  risk=35),
            _opt('risk_mgmt',  'Risk management',    risk=20),
            _opt('emotional',  'Emotional control',  emotional=20),
            _opt('none',       'No major challenge', discipline=90, emotional=90, risk=90, strategy=90),
        ],
    },

    # ── SECTION 4: MARKET UNDERSTANDING ──────────────────────────────────────
    {
        'id': 'Q16', 'section': 'Market Understanding',
        'text': 'Do you understand risk/reward properly?',
        'type': 'single',
        'options': [
            _opt('no',       'No',         market=10,  risk=15),
            _opt('somewhat', 'Somewhat',   market=40,  risk=40),
            _opt('yes',      'Yes',        market=75,  risk=75),
            _opt('verywell', 'Very well',  market=100, risk=95),
        ],
    },
    {
        'id': 'Q17', 'section': 'Market Understanding',
        'text': 'Which matters most before entering a trade?',
        'type': 'single',
        'options': [
            _opt('tips',       'Tips / news',       market=10,  strategy=15),
            _opt('momentum',   'Momentum',          market=45,  strategy=45),
            _opt('indicators', 'Indicators',        market=60,  strategy=60),
            _opt('rr',         'Risk / reward',     market=85,  strategy=80),
            _opt('structure',  'Market structure',  market=100, strategy=95),
        ],
    },
    {
        'id': 'Q18', 'section': 'Market Understanding',
        'text': 'Which of these do you understand? (select all that apply)',
        'type': 'multi',
        'options': [
            _opt('vwap',     'VWAP',            market=20),
            _opt('oi',       'Open Interest',   market=20),
            _opt('greeks',   'Option Greeks',   market=25),
            _opt('pcr',      'Put-Call Ratio',  market=15),
            _opt('sizing',   'Position sizing', market=20, risk=20),
        ],
    },

    # ── SECTION 5: GOALS & MATURITY ──────────────────────────────────────────
    {
        'id': 'Q19', 'section': 'Goals & Maturity',
        'text': 'What is your primary goal?',
        'type': 'single',
        'options': [
            _opt('learn',     'Learning',                 experience=30, discipline=60),
            _opt('side_inc',  'Side income',              experience=50, discipline=55),
            _opt('fulltime',  'Full-time trading',        experience=70, discipline=65),
            _opt('wealth',    'Wealth building',          experience=75, discipline=75),
            _opt('pro',       'Professional trading',     experience=85, discipline=85),
        ],
    },
    {
        'id': 'Q20', 'section': 'Goals & Maturity',
        'text': 'How serious are you about becoming a disciplined trader?',
        'type': 'scale',
        'min': 1, 'max': 10,
        # The scale value (1..10) is mapped to discipline / emotional in
        # compute_profile() — see _scale_score().
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _question_by_id(qid: str) -> Optional[Dict]:
    for q in QUESTIONS:
        if q['id'] == qid:
            return q
    return None


def _scale_score(val) -> Dict[str, float]:
    """Q20 scale 1..10 → discipline + emotional contribution."""
    try:
        n = max(1, min(10, int(val)))
    except (TypeError, ValueError):
        return {}
    pct = (n - 1) * 100 / 9.0
    return {'discipline': pct, 'emotional': pct}


def _collect_option_scores(q: Dict, answer) -> Dict[str, float]:
    """Return {dim: score} for the chosen option(s) of a question."""
    if q['type'] == 'scale':
        return _scale_score(answer)

    chosen_values = answer if isinstance(answer, list) else [answer]
    if not chosen_values:
        return {}

    # For multi-select, sum every selected option's per-dim score (capped at
    # 100 per dim). For single-select, just take the one chosen option.
    accum: Dict[str, float] = {}
    for opt in q.get('options', []):
        if opt['value'] in chosen_values:
            for dim, score in (opt.get('scores') or {}).items():
                accum[dim] = min(100.0, accum.get(dim, 0.0) + float(score))
    return accum


def compute_profile(answers: Dict[str, object]) -> Dict:
    """Run the full scoring pipeline.

    Args:
        answers: {question_id: chosen_value_or_list_or_scale_int}

    Returns:
        {
          'dimensions':       {dim: 0..100},
          'overall_score':    0..100,
          'level':            'L1'..'L6',
          'level_info':       LEVELS[level],
          'behavioural_risk': 'LOW'|'MEDIUM'|'HIGH',
          'xp_earned':        int,
          'next_level':       'L2' | None,
          'next_requirements':[str, ...],
          'per_question':     [{id, dim_contribs}],  # for transparency
        }
    """
    # Sum contributions per dimension; also count contributing questions per
    # dimension so we can average.
    dim_sum: Dict[str, float] = {d: 0.0 for d in DIMENSIONS}
    dim_count: Dict[str, int] = {d: 0 for d in DIMENSIONS}
    per_question: List[Dict] = []

    for qid, answer in (answers or {}).items():
        q = _question_by_id(qid)
        if q is None or answer in (None, '', []):
            continue
        contribs = _collect_option_scores(q, answer)
        per_question.append({'id': qid, 'contribs': contribs})
        for dim, score in contribs.items():
            if dim not in dim_sum:
                continue
            dim_sum[dim] += score
            dim_count[dim] += 1

    # Average each dimension (fall back to 50 — "neutral" — if no questions
    # contributed, so a missing dimension does not crush the overall score).
    dimensions: Dict[str, float] = {}
    for d in DIMENSIONS:
        dimensions[d] = round(dim_sum[d] / dim_count[d], 1) if dim_count[d] else 50.0

    # Weighted overall (0..100).
    overall = sum(dimensions[d] * DIMENSION_WEIGHTS[d] for d in DIMENSIONS)
    overall = round(max(0.0, min(100.0, overall)), 1)

    # Level lookup.
    level_code = 'L1'
    for code in LEVEL_ORDER:
        info = LEVELS[code]
        if info['min_score'] <= overall <= info['max_score']:
            level_code = code
            break
    level_info = LEVELS[level_code]

    # Behavioural risk band — driven by emotional control + discipline.
    risk_signal = (dimensions['emotional'] + dimensions['discipline']) / 2
    if risk_signal >= 65:
        behavioural_risk = 'LOW'
    elif risk_signal >= 40:
        behavioural_risk = 'MEDIUM'
    else:
        behavioural_risk = 'HIGH'

    # XP — flat reward for completing the assessment + bonus scaled by score.
    xp_earned = 100 + int(overall * 4)

    return {
        'dimensions':         {DIMENSION_LABELS[d]: dimensions[d] for d in DIMENSIONS},
        'dimensions_raw':     dimensions,
        'overall_score':      overall,
        'level':              level_code,
        'level_info':         level_info,
        'behavioural_risk':   behavioural_risk,
        'xp_earned':          xp_earned,
        'next_level':         level_info['next'],
        'next_requirements':  LEVEL_UP_REQUIREMENTS.get(level_code, []),
        'per_question':       per_question,
    }


def level_progression(current_level: str, overall_score: float) -> Dict:
    """Return how far the trader is from the next level, for the progress UI."""
    current_level = current_level or 'L1'
    cur = LEVELS.get(current_level, LEVELS['L1'])
    nxt_code = cur.get('next')
    if not nxt_code:
        return {
            'current': current_level,
            'next': None,
            'percent_to_next': 100,
            'points_to_next': 0,
            'requirements': LEVEL_UP_REQUIREMENTS.get(current_level, []),
        }
    nxt = LEVELS[nxt_code]
    span = max(1, nxt['min_score'] - cur['min_score'])
    progress = max(0, min(span, overall_score - cur['min_score']))
    return {
        'current':         current_level,
        'next':            nxt_code,
        'percent_to_next': round(progress * 100 / span, 1),
        'points_to_next':  round(max(0, nxt['min_score'] - overall_score), 1),
        'requirements':    LEVEL_UP_REQUIREMENTS.get(current_level, []),
    }
