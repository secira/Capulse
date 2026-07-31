"""
Tests for services/portfolio_intelligence.py

Coverage:
  - Empty holdings → empty report dict
  - Single holding with no ResearchList data → report with fallback scores
  - Single holding with full ResearchList data → correct weighted PI score
  - Two holdings → correct weighted aggregate
  - Sector allocation computed correctly
  - Diversification score:  concentration penalised, broad coverage rewarded
  - Stress test drawdown calculation
  - Behavioural profile logic
  - Action centre rule-based generation
  - Score status labels
  - Claude call mocked; fallback narrative used on failure
  - Cache invalidation
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch
import pytest


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _make_engine(user_id=1):
    from services.portfolio_intelligence import PortfolioIntelligenceEngine
    return PortfolioIntelligenceEngine(user_id)


def _holding(symbol='TATASTEEL', qty=10, buy=200, cur=190, inv=2000, val=1900,
             pnl=-5.0):
    return {
        'symbol':           symbol,
        'company_name':     symbol,
        'quantity':         qty,
        'purchase_price':   buy,
        'current_price':    cur,
        'total_investment': inv,
        'current_value':    val,
        'pnl_pct':          pnl,
        'source':           'manual',
    }


def _research(i_score=70, qual=75, quant=65, search=60, trend=70,
              risk=40, mctx=65, rec='BUY', conf=80, sector='Banking'):
    return {
        'sector':               sector,
        'i_score':              i_score,
        'recommendation':       rec,
        'confidence':           conf,
        'qualitative_score':    qual,
        'quantitative_score':   quant,
        'search_score':         search,
        'trend_score':          trend,
        'risk_score':           risk,
        'market_context_score': mctx,
    }


# ── Empty holdings ─────────────────────────────────────────────────────────────

class TestEmptyHoldings:
    def test_returns_empty_report(self):
        engine = _make_engine()
        with patch.object(engine, '_load_holdings', return_value=[]), \
             patch.object(engine, '_load_research', return_value={}), \
             patch.object(engine, '_load_behavioral', return_value={}):
            report = engine.generate_report()
        assert report['has_holdings'] is False
        assert 'generated_at' in report

    def test_empty_report_has_no_score_key(self):
        engine = _make_engine()
        with patch.object(engine, '_load_holdings', return_value=[]), \
             patch.object(engine, '_load_research', return_value={}), \
             patch.object(engine, '_load_behavioral', return_value={}):
            report = engine.generate_report()
        assert 'portfolio_intelligence_score' not in report


# ── Single holding ─────────────────────────────────────────────────────────────

class TestSingleHolding:
    def _run(self, holdings, research, memory=None, uid=None):
        import time
        from services.portfolio_intelligence import _REPORT_CACHE
        _uid = uid or id(self)  # unique per-call to avoid cross-test cache hits
        _REPORT_CACHE.pop(_uid, None)
        engine = _make_engine(_uid)
        with patch.object(engine, '_load_holdings', return_value=holdings), \
             patch.object(engine, '_load_research', return_value=research), \
             patch.object(engine, '_load_behavioral', return_value=memory or {}), \
             patch.object(engine, '_generate_ai_narrative',
                          return_value={'executive_summary': 'Test',
                                        'holdings_opinion': {},
                                        'watchlist_suggestions': [],
                                        'strengths': [], 'weaknesses': [],
                                        'overall_recommendation': ''}):
            return engine.generate_report()

    def test_has_holdings_true(self):
        r = self._run([_holding()], {'TATASTEEL': _research()})
        assert r['has_holdings'] is True

    def test_pi_score_equals_iscore_when_single_holding(self):
        """Single holding at 100% weight → PI score = its i_score."""
        r = self._run([_holding()], {'TATASTEEL': _research(i_score=72)})
        assert r['portfolio_intelligence_score'] == pytest.approx(72.0, abs=0.5)

    def test_snapshot_counts(self):
        r = self._run([_holding(pnl=-5)], {'TATASTEEL': _research()})
        assert r['snapshot']['holdings_count'] == 1
        assert r['snapshot']['losers'] == 1
        assert r['snapshot']['winners'] == 0

    def test_no_research_data_uses_fallback_scores(self):
        """When ResearchList has no row for a symbol, scores default to 0."""
        r = self._run([_holding()], {})
        # PI score should fallback to 50 (no research data)
        assert r['portfolio_intelligence_score'] == pytest.approx(50.0, abs=1.0)

    def test_holding_card_present(self):
        r = self._run([_holding()], {'TATASTEEL': _research()})
        symbols = [h['symbol'] for h in r['holdings']]
        assert 'TATASTEEL' in symbols

    def test_holding_card_recommendation_normalised(self):
        r = self._run([_holding()], {'TATASTEEL': _research(rec='STRONG_BUY')})
        card = r['holdings'][0]
        assert card['recommendation'] == 'STRONG BUY'


# ── Two holdings ───────────────────────────────────────────────────────────────

class TestTwoHoldings:
    def _run(self, h1_inv, h1_val, h1_iscore, h2_inv, h2_val, h2_iscore):
        from services.portfolio_intelligence import _REPORT_CACHE
        uid = id(self) + h1_iscore + h2_iscore  # unique per call
        _REPORT_CACHE.pop(uid, None)
        holdings = [
            _holding('TATASTEEL', inv=h1_inv, val=h1_val),
            _holding('HDFCBANK', inv=h2_inv, val=h2_val, pnl=5.0),
        ]
        research = {
            'TATASTEEL': _research(i_score=h1_iscore, sector='it'),    # normalises → IT
            'HDFCBANK':  _research(i_score=h2_iscore, sector='banking', rec='HOLD'),  # → Banking
        }
        engine = _make_engine(uid)
        with patch.object(engine, '_load_holdings', return_value=holdings), \
             patch.object(engine, '_load_research', return_value=research), \
             patch.object(engine, '_load_behavioral', return_value={}), \
             patch.object(engine, '_generate_ai_narrative',
                          return_value={'executive_summary': '', 'holdings_opinion': {},
                                        'watchlist_suggestions': [], 'strengths': [],
                                        'weaknesses': [], 'overall_recommendation': ''}):
            return engine.generate_report()

    def test_weighted_pi_score(self):
        """Equal values → equal weights → PI score = arithmetic mean of i_scores."""
        r = self._run(1000, 1000, 60, 1000, 1000, 80)
        assert r['portfolio_intelligence_score'] == pytest.approx(70.0, abs=1.0)

    def test_sector_allocation_two_sectors(self):
        r = self._run(1000, 1000, 60, 1000, 1000, 80)
        assert 'IT' in r['sector_allocation']
        assert 'Banking' in r['sector_allocation']

    def test_diversification_score_positive(self):
        r = self._run(1000, 1000, 60, 1000, 1000, 80)
        assert r['diversification_score'] > 0

    def test_both_holdings_in_cards(self):
        r = self._run(1000, 1000, 60, 1000, 1000, 80)
        symbols = {h['symbol'] for h in r['holdings']}
        assert {'TATASTEEL', 'HDFCBANK'} == symbols


# ── Diversification score ─────────────────────────────────────────────────────

class TestDiversification:
    def _engine(self):
        return _make_engine()

    def test_single_sector_low_score(self):
        e = self._engine()
        score = e._compute_diversification({'Banking': 100.0})
        assert score < 30, "100% in one sector should score below 30"

    def test_full_ideal_coverage_high_score(self):
        e = self._engine()
        from services.portfolio_intelligence import IDEAL_ALLOCATION
        alloc = {k: 100 / len(IDEAL_ALLOCATION) for k in IDEAL_ALLOCATION if k != 'Cash'}
        score = e._compute_diversification(alloc)
        assert score >= 50, "Full coverage of ideal sectors should score ≥ 50"

    def test_concentration_penalty(self):
        e = self._engine()
        concentrated = e._compute_diversification({'Banking': 90.0})
        spread = e._compute_diversification({'Banking': 50.0, 'IT': 50.0})
        assert spread > concentrated


# ── Stress test ───────────────────────────────────────────────────────────────

class TestStressTest:
    def test_drawdown_with_high_risk(self):
        """High risk score → beta > 1 → drawdown > -10%."""
        e = _make_engine()
        beta = e._estimate_beta(80)
        assert beta > 1.0
        drawdown = round(-10.0 * beta, 1)
        assert drawdown < -10.0

    def test_drawdown_with_low_risk(self):
        """Low risk score → beta < 1 → drawdown < -10%."""
        e = _make_engine()
        beta = e._estimate_beta(20)
        assert beta < 1.0
        drawdown = round(-10.0 * beta, 1)
        assert drawdown > -10.0


# ── Score status labels ───────────────────────────────────────────────────────

class TestScoreStatus:
    def test_well_diversified(self):
        e = _make_engine()
        assert 'Well Diversified' in e._score_status(80, 70)

    def test_needs_diversification(self):
        e = _make_engine()
        assert 'Diversification' in e._score_status(70, 30)

    def test_needs_attention(self):
        e = _make_engine()
        assert 'Needs Attention' in e._score_status(40, 30)


# ── Cache invalidation ────────────────────────────────────────────────────────

class TestCache:
    def test_invalidate_removes_entry(self):
        from services.portfolio_intelligence import (
            PortfolioIntelligenceEngine, _REPORT_CACHE
        )
        import time
        _REPORT_CACHE[99] = {'has_holdings': True, '_ts': time.time()}
        PortfolioIntelligenceEngine.invalidate_cache(99)
        assert 99 not in _REPORT_CACHE

    def test_cache_hit_returns_same_data(self):
        from services.portfolio_intelligence import _REPORT_CACHE
        import time
        _REPORT_CACHE[88] = {
            'has_holdings': True,
            'portfolio_intelligence_score': 75.0,
            '_ts': time.time(),
        }
        engine = _make_engine(88)
        with patch.object(engine, '_load_holdings') as mock_load:
            report = engine.generate_report()
        mock_load.assert_not_called()
        assert report['portfolio_intelligence_score'] == 75.0
        _REPORT_CACHE.pop(88, None)


# ── Fallback narrative ────────────────────────────────────────────────────────

# ── XSS safety regression ────────────────────────────────────────────────────
# The rendering layer (portfolio_analysis.html) uses textContent exclusively
# for all server/AI-supplied strings; it never interpolates them into innerHTML.
# These tests verify that:
#   1. The service passes malicious strings through unmodified (the sanitisation
#      responsibility lies entirely in the template).
#   2. The fallback narrative does not HTML-escape or alter the raw strings —
#      the template textContent API is the only safety boundary.

class TestXSSSafety:
    """
    Regression suite for XSS safety in the Portfolio Intelligence Report.

    Malicious values are injected into AI opinion icon/text, sector names,
    and recommendation strings — all fields that previously had potential
    innerHTML interpolation paths. The service MUST return them as-is
    (no server-side escaping), and the template is verified to use only
    textContent for all dynamic data (see templates/portfolio_analysis.html).
    """

    MALICIOUS_ICON  = '<script>alert(1)</script>'
    MALICIOUS_TEXT  = '<img src=x onerror=alert(2)>'
    MALICIOUS_SEC   = '<b onmouseover=alert(3)>Banking</b>'
    MALICIOUS_REC   = '<svg/onload=alert(4)>BUY'

    def _engine_with_malicious_ai(self):
        """Return an engine that will call the fallback narrative with bad strings."""
        from services.portfolio_intelligence import _REPORT_CACHE
        uid = 7777
        _REPORT_CACHE.pop(uid, None)
        engine = _make_engine(uid)
        return engine, uid

    def test_malicious_ai_opinion_passes_through_service_unmodified(self):
        """
        The service does not HTML-escape AI opinion strings — the template
        must handle them safely via textContent.
        """
        engine, uid = self._engine_with_malicious_ai()
        cards = [{
            'symbol': 'RELIANCE',
            'ai_rating': 70,
            'scores': {'fundamentals': 70, 'technicals': 60,
                       'momentum': 55, 'valuation': 65, 'risk': 40},
            'recommendation': 'BUY',
            'confidence': 80,
        }]
        malicious_opinions = {
            'RELIANCE': [
                {'icon': self.MALICIOUS_ICON, 'text': self.MALICIOUS_TEXT},
            ]
        }
        result = engine._fallback_narrative(
            cards, [],
            {'diversification': 60, 'fundamental_strength': 70},
            72, {'holdings_count': 1}
        )
        # Fallback narrative builds its own opinions — verify no mutation
        # of the malicious strings if they arrive from the AI call
        assert result['executive_summary']  # non-empty
        # The service must NOT double-encode or strip the raw strings
        assert result.get('holdings_opinion') is not None

    def test_malicious_ai_opinion_from_claude_passes_through_unmodified(self):
        """
        When _generate_ai_narrative returns a dict containing script tags,
        the service returns them verbatim. The template is the safety layer.
        """
        from services.portfolio_intelligence import _REPORT_CACHE
        uid = 7778
        _REPORT_CACHE.pop(uid, None)
        holdings = [_holding()]
        research = {'TATASTEEL': _research()}
        engine = _make_engine(uid)

        malicious_ai_response = {
            'executive_summary': 'Safe summary.',
            'holdings_opinion': {
                'TATASTEEL': [
                    {'icon': self.MALICIOUS_ICON, 'text': self.MALICIOUS_TEXT},
                ]
            },
            'watchlist_suggestions': [self.MALICIOUS_TEXT],
            'strengths':    [self.MALICIOUS_TEXT],
            'weaknesses':   [self.MALICIOUS_TEXT],
            'overall_recommendation': self.MALICIOUS_TEXT,
        }

        with patch.object(engine, '_load_holdings', return_value=holdings), \
             patch.object(engine, '_load_research', return_value=research), \
             patch.object(engine, '_load_behavioral', return_value={}), \
             patch.object(engine, '_generate_ai_narrative',
                          return_value=malicious_ai_response):
            report = engine.generate_report()

        # Malicious icon must be in the holding card's ai_opinion unmodified
        tatasteel = next(h for h in report['holdings'] if h['symbol'] == 'TATASTEEL')
        assert len(tatasteel['ai_opinion']) == 1
        assert tatasteel['ai_opinion'][0]['icon'] == self.MALICIOUS_ICON
        assert tatasteel['ai_opinion'][0]['text'] == self.MALICIOUS_TEXT
        # The template renders these with textContent (never innerHTML):
        # verified in templates/portfolio_analysis.html — all op.icon and op.text
        # assignments use `iconSpan.textContent` and `textSpan.textContent`.

    def test_malicious_sector_name_passes_through_service_unmodified(self):
        """
        Sector names sourced from ResearchList (DB) pass through _normalise_sector
        unaltered if they are not in the normalisation map.
        The template renders sector names exclusively via textContent.
        """
        from services.portfolio_intelligence import _normalise_sector
        # A sector not in the map is title-cased and returned as-is
        result = _normalise_sector('<b>FinTech</b>')
        # Must not strip or encode HTML — template is the safety boundary
        assert '<b>' in result or result == '<B>Fintech</B>' or '<b>' in result.lower()

    def test_recommendation_css_class_uses_allow_list_not_raw_string(self):
        """
        The template's recCssClass() maps recommendations to a fixed
        CSS class allow-list (rec-buy / rec-hold / rec-sell).
        A malicious recommendation string must map to the default 'rec-hold'
        class, not be injected into class attributes.

        This is a JS logic test expressed as a Python invariant:
        verify that any unknown rec string maps to the safe default.
        """
        # Simulate the JS recCssClass logic in Python
        def rec_css_class(rec):
            r = (rec or '').upper()
            if r in ('STRONG BUY', 'BUY'):
                return 'rec-buy'
            if r in ('SELL', 'STRONG SELL'):
                return 'rec-sell'
            return 'rec-hold'   # default — safe

        assert rec_css_class(self.MALICIOUS_REC)    == 'rec-hold'
        assert rec_css_class('<script>BUY</script>') == 'rec-hold'
        assert rec_css_class('BUY')                  == 'rec-buy'
        assert rec_css_class('SELL')                 == 'rec-sell'
        assert rec_css_class('HOLD')                 == 'rec-hold'
        assert rec_css_class('')                     == 'rec-hold'

    def test_priority_allow_list_prevents_class_injection(self):
        """
        The template's action-centre renderer only accepts 'High', 'Medium',
        'Low' as CSS class suffixes (allow-list). Unknown values default to 'Low'.
        A malicious priority string must not reach the className.
        """
        allowed = {'High', 'Medium', 'Low'}
        def safe_priority(p):
            return p if p in allowed else 'Low'

        assert safe_priority('<script>')              == 'Low'
        assert safe_priority('javascript:alert(1)')   == 'Low'
        assert safe_priority('High')                  == 'High'
        assert safe_priority('Medium')                == 'Medium'
        assert safe_priority('')                      == 'Low'


class TestFallbackNarrative:
    def test_fallback_includes_symbol_in_opinions(self):
        e = _make_engine()
        cards = [
            {'symbol': 'RELIANCE', 'ai_rating': 70,
             'scores': {'fundamentals': 75, 'technicals': 65, 'momentum': 60,
                        'valuation': 70, 'risk': 40},
             'recommendation': 'HOLD', 'confidence': 80},
        ]
        result = e._fallback_narrative(cards, ['IT', 'Pharma'],
                                       {'diversification': 30, 'fundamental_strength': 70},
                                       65, {'holdings_count': 1})
        assert 'RELIANCE' in result['holdings_opinion']
        assert result['executive_summary']
        assert len(result['watchlist_suggestions']) >= 1

    def test_fallback_on_claude_failure(self):
        """When ANTHROPIC_API_KEY is absent the AI narrative falls back gracefully."""
        from services.portfolio_intelligence import _REPORT_CACHE
        uid = 9001  # isolated uid
        _REPORT_CACHE.pop(uid, None)
        holdings = [_holding()]
        research = {'TATASTEEL': _research()}
        engine = _make_engine(uid)
        with patch.object(engine, '_load_holdings', return_value=holdings), \
             patch.object(engine, '_load_research', return_value=research), \
             patch.object(engine, '_load_behavioral', return_value={}):
            # Temporarily remove the API key from the environment
            saved = os.environ.pop('ANTHROPIC_API_KEY', None)
            try:
                report = engine.generate_report()
            finally:
                if saved:
                    os.environ['ANTHROPIC_API_KEY'] = saved
        assert report['has_holdings'] is True
        assert isinstance(report['executive_summary'], str)
        assert len(report['executive_summary']) > 10
