"""
Tests for Task #24: F&O and i-Score personalisation based on user risk/style.

Covers:
  - _iscore_holding_note()     — style-aware framing notes
  - handle_fno_signal()        — personalisation_note in card_data
  - handle_iscore()            — holding_note in card_data

Run with:  python -m pytest tests/test_fno_personalisation.py -v
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _mem(trading_style='', risk_level=''):
    """Return a minimal user memory dict mimicking get_memory() output."""
    return {
        'trading_style':         trading_style,
        'risk_level':            risk_level,
        'preferred_instruments': '',
        'sectors':               '',
        'watchlist':             '',
        'capital_bracket':       '',
        'goals':                 '',
        'psychology_notes':      '',
        'interaction_count':     0,
        'updated_at':            None,
    }


# ── _iscore_holding_note ──────────────────────────────────────────────────────

class TestIScoreHoldingNote:
    def _get_note(self, trading_style):
        from services.capulse_router import _iscore_holding_note
        with patch('services.user_memory.get_memory', return_value=_mem(trading_style)):
            return _iscore_holding_note(user_id=1)

    def test_intraday_returns_note(self):
        note = self._get_note('intraday')
        assert note is not None
        assert 'intraday' in note.lower() or 'weeks' in note.lower()

    def test_swing_returns_trend_note(self):
        note = self._get_note('swing')
        assert note is not None
        assert 'Trend' in note or 'Sentiment' in note

    def test_long_term_returns_fundamentals_note(self):
        note = self._get_note('long_term')
        assert note is not None
        assert 'Qualitative' in note or 'Quantitative' in note

    def test_positional_returns_none(self):
        # 'positional' has no specific note
        note = self._get_note('positional')
        assert note is None

    def test_empty_style_returns_none(self):
        note = self._get_note('')
        assert note is None

    def test_no_user_id_returns_none(self):
        from services.capulse_router import _iscore_holding_note
        note = _iscore_holding_note(user_id=None)
        assert note is None

    def test_db_error_returns_none(self):
        from services.capulse_router import _iscore_holding_note
        with patch('services.user_memory.get_memory', side_effect=RuntimeError('db down')):
            note = _iscore_holding_note(user_id=1)
        assert note is None


# ── handle_fno_signal personalisation_note ───────────────────────────────────

def _make_analysis(**overrides):
    """Minimal NiftyOptionsEngine.generate_analysis() result for an actionable trade."""
    base = {
        'spot_price':      24000,
        'atm_strike':      24000,
        'trade_direction': 'BULLISH',
        'final_decision':  'TRADE',
        'confidence':      72,
        'confidence_grade': 'B',
        'is_blocked':      False,
        'block_reasons':   [],
        'trades': [{
            'strike': 24000, 'type': 'CE', 'action': 'BUY',
            'entry_price': 120.0, 'sl': 80.0, 'target': 200.0,
            'confidence': 72, 'label': 'ATM', 'risk_reward': '2.0', 'ltp': 118.0,
        }],
        'data_source': 'estimated',
        'direction':   {'direction': 'BULLISH'},
        'strength':    {},
        'oi_analysis': {},
        'momentum':    {},
        'halftrend':   {},
        'layer_status': {},
        'time_filter': {
            'caution': False, 'reason': '',
            'is_holiday': False, 'holiday_name': '', 'is_weekend': False,
        },
    }
    base.update(overrides)
    return base


def _run_fno(risk='', style='', analysis=None):
    """Invoke handle_fno_signal with a mocked engine and mocked memory."""
    from services.capulse_router import handle_fno_signal

    mock_eng = MagicMock()
    mock_eng.generate_analysis.return_value = analysis or _make_analysis()

    with patch('services.user_memory.get_memory', return_value=_mem(risk_level=risk, trading_style=style)), \
         patch('services.nifty_options_engine.NiftyOptionsEngine', return_value=mock_eng):
        return handle_fno_signal('NIFTY', None, user_id=1)


class TestFnoPersonalisationNote:
    def test_conservative_note_present(self):
        result = _run_fno(risk='conservative')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is not None
        assert 'conservative' in note.lower()

    def test_moderate_note_present(self):
        result = _run_fno(risk='moderate')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is not None
        assert 'moderate' in note.lower()

    def test_intraday_note_present(self):
        result = _run_fno(style='intraday')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is not None
        assert 'ntraday' in note or '3:00' in note

    def test_positional_note_present(self):
        result = _run_fno(style='positional')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is not None
        assert 'positional' in note.lower() or 'long-term' in note.lower()

    def test_long_term_style_note_present(self):
        result = _run_fno(style='long_term')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is not None
        assert 'long' in note.lower()

    def test_aggressive_swing_no_note(self):
        # aggressive + swing: neither condition triggers → note is None
        result = _run_fno(risk='aggressive', style='swing')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is None

    def test_combined_conservative_intraday_note(self):
        # Both risk and style trigger → combined note
        result = _run_fno(risk='conservative', style='intraday')
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is not None
        assert 'conservative' in note.lower()
        assert 'ntraday' in note or '3:00' in note

    def test_personalisation_note_key_always_present(self):
        # Key exists even when no profile triggers a note
        result = _run_fno()
        assert 'personalisation_note' in result.get('card_data', {})

    def test_note_absent_for_no_trade_decision(self):
        # When engine returns NO TRADE, no personalisation note should appear
        no_trade = _make_analysis(final_decision='NO TRADE', is_blocked=True, trades=[])
        result = _run_fno(risk='conservative', analysis=no_trade)
        note = result.get('card_data', {}).get('personalisation_note')
        assert note is None
