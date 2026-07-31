"""
Tests for services/user_memory.py

Covers:
- Pure helper functions (_csv_union)
- build_memory_block() output with mocked DB
- Memory block is injected into the system prompt in handle_general()
- update_memory() merges list fields (union) and replaces scalars
- extract_and_update_memory() suppresses "nothing_new" patches

Run with:  python -m pytest tests/test_user_memory.py -v
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.user_memory import (
    _csv_union,
    build_memory_block,
    STYLE_LABELS, RISK_LABELS, CAPITAL_LABELS, INSTRUMENT_LABELS, GOAL_LABELS,
)


# ── _csv_union ────────────────────────────────────────────────────────────────

class TestCsvUnion:
    def test_adds_new_items(self):
        result = _csv_union('banking', ['it', 'pharma'])
        assert 'banking' in result
        assert 'it' in result
        assert 'pharma' in result

    def test_no_duplicates(self):
        result = _csv_union('banking, it', ['banking', 'fmcg'])
        parts = [x.strip() for x in result.split(',')]
        assert parts.count('banking') == 1

    def test_empty_existing(self):
        result = _csv_union(None, ['equity', 'fno'])
        assert 'equity' in result
        assert 'fno' in result

    def test_empty_new(self):
        result = _csv_union('equity', [])
        assert 'equity' in result

    def test_all_empty(self):
        result = _csv_union(None, [])
        assert result == ''

    def test_case_insensitive_dedup(self):
        result = _csv_union('BANKING', ['banking'])
        parts = [x.strip() for x in result.split(',') if x.strip()]
        assert len(parts) == 1


# ── build_memory_block ────────────────────────────────────────────────────────

def _mock_memory(**kwargs):
    """Return a get_memory-style dict with defaults overridden."""
    base = {
        'trading_style': None,
        'risk_level': None,
        'preferred_instruments': None,
        'sectors': None,
        'watchlist': None,
        'capital_bracket': None,
        'goals': None,
        'psychology_notes': None,
        'interaction_count': 0,
        'updated_at': None,
    }
    base.update(kwargs)
    return base


class TestBuildMemoryBlock:
    def test_empty_profile_returns_empty_string(self):
        with patch('services.user_memory.get_memory', return_value={}):
            result = build_memory_block(1)
        assert result == ''

    def test_profile_with_no_set_fields_returns_empty(self):
        with patch('services.user_memory.get_memory', return_value=_mock_memory()):
            result = build_memory_block(1)
        assert result == ''

    def test_trading_style_appears_in_block(self):
        mem = _mock_memory(trading_style='intraday', interaction_count=5)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert result != ''
        assert 'Intraday' in result

    def test_risk_level_appears(self):
        mem = _mock_memory(risk_level='aggressive', interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert 'Aggressive' in result

    def test_capital_bracket_label(self):
        mem = _mock_memory(capital_bracket='medium', interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert '₹5' in result or 'medium' in result.lower() or '25' in result

    def test_instruments_formatted(self):
        mem = _mock_memory(preferred_instruments='equity, fno', interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert 'Equity' in result or 'F&O' in result

    def test_watchlist_uppercased(self):
        mem = _mock_memory(watchlist='reliance, hdfcbank', interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert 'RELIANCE' in result
        assert 'HDFCBANK' in result

    def test_sectors_titlecased(self):
        mem = _mock_memory(sectors='banking, it', interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert 'Banking' in result or 'banking' in result.lower()

    def test_goals_labeled(self):
        mem = _mock_memory(goals='speculation, hedging', interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert 'Speculation' in result or 'Hedging' in result

    def test_psychology_notes_included(self):
        note = 'prefers quick setups'
        mem = _mock_memory(psychology_notes=note, interaction_count=3)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert note in result

    def test_full_profile_has_personalisation_header(self):
        mem = _mock_memory(trading_style='swing', risk_level='moderate', interaction_count=10)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        assert 'USER TRADING PROFILE' in result
        assert 'personalise' in result.lower() or 'tailor' in result.lower()

    def test_profile_not_revealed_to_user(self):
        """Block must NOT instruct Claude to mention the profile to the user."""
        mem = _mock_memory(trading_style='intraday', interaction_count=5)
        with patch('services.user_memory.get_memory', return_value=mem):
            result = build_memory_block(1)
        # The instruction should say NOT to mention the profile
        assert 'Do not mention' in result or 'not mention' in result.lower()


# ── System prompt injection ───────────────────────────────────────────────────

class TestSystemPromptInjection:
    """Verify that the capulse_router includes the memory block in the system prompt."""

    def test_memory_block_prepended_to_system(self):
        """When a profile exists, its block appears before the base instructions."""
        memory_block = (
            "[USER TRADING PROFILE — personalise your response]\n"
            "  • Trading Style: Intraday\n"
            "[Tailor your answer...]"
        )

        # Import the function; mock all external calls
        with patch('services.user_memory.build_memory_block', return_value=memory_block), \
             patch('services.financial_context_builder.get_live_context_for_message', return_value=''), \
             patch('services.user_memory.async_extract'), \
             patch('anthropic.Anthropic') as MockAnthropic:

            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='Test AI response')]
            mock_client.messages.create.return_value = mock_resp

            import os
            with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):
                from services.capulse_router import handle_general
                result = handle_general(
                    message='How does intraday trading work?',
                    conversation_history=[],
                    user_id=42,
                )

        assert result['card_type'] == 'prose'
        # The system prompt passed to Claude should contain the memory block
        call_kwargs = mock_client.messages.create.call_args
        system_arg = call_kwargs[1].get('system') or call_kwargs[0][2] if call_kwargs[0] else ''
        if not system_arg and hasattr(call_kwargs, 'kwargs'):
            system_arg = call_kwargs.kwargs.get('system', '')
        assert 'USER TRADING PROFILE' in system_arg, (
            f"Expected memory block in system prompt, got: {system_arg[:200]}"
        )

    def test_no_memory_block_when_profile_empty(self):
        """When no profile exists, the system prompt must not contain a stale block."""
        with patch('services.user_memory.build_memory_block', return_value=''), \
             patch('services.financial_context_builder.get_live_context_for_message', return_value=''), \
             patch('services.user_memory.async_extract'), \
             patch('anthropic.Anthropic') as MockAnthropic:

            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='Test response')]
            mock_client.messages.create.return_value = mock_resp

            import os
            with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):
                from services.capulse_router import handle_general
                handle_general(message='What is PE ratio?', conversation_history=[], user_id=99)

        call_kwargs = mock_client.messages.create.call_args
        system_arg = (call_kwargs[1].get('system', '') if call_kwargs[1] else '')
        assert 'USER TRADING PROFILE' not in system_arg


# ── update_memory merge logic ─────────────────────────────────────────────────

class TestUpdateMemoryLogic:
    """Test the pure merge logic using mocked DB row."""

    def _make_mock_mem(self, **existing):
        """Build a mock UserFinancialMemory row."""
        m = MagicMock()
        m.trading_style = existing.get('trading_style')
        m.risk_level = existing.get('risk_level')
        m.capital_bracket = existing.get('capital_bracket')
        m.preferred_instruments = existing.get('preferred_instruments', '')
        m.sectors = existing.get('sectors', '')
        m.watchlist = existing.get('watchlist', '')
        m.goals = existing.get('goals', '')
        m.psychology_notes = existing.get('psychology_notes', '')
        m.interaction_count = existing.get('interaction_count', 0)
        return m

    def test_scalar_trading_style_replaced(self):
        mock_mem = self._make_mock_mem(trading_style='swing')
        with patch('models.UserFinancialMemory') as MockModel, \
             patch('models.db') as mock_db:
            MockModel.query.filter_by.return_value.first.return_value = mock_mem
            from services.user_memory import update_memory
            update_memory(1, {'trading_style': 'intraday'})
        assert mock_mem.trading_style == 'intraday'

    def test_list_sectors_union_merged(self):
        mock_mem = self._make_mock_mem(sectors='banking, it')
        with patch('models.UserFinancialMemory') as MockModel, \
             patch('models.db') as mock_db:
            MockModel.query.filter_by.return_value.first.return_value = mock_mem
            from services.user_memory import update_memory
            update_memory(1, {'sectors': ['pharma']})
        assert 'banking' in mock_mem.sectors
        assert 'it' in mock_mem.sectors
        assert 'pharma' in mock_mem.sectors

    def test_psychology_notes_appended(self):
        mock_mem = self._make_mock_mem(psychology_notes='patient trader')
        with patch('models.UserFinancialMemory') as MockModel, \
             patch('models.db') as mock_db:
            MockModel.query.filter_by.return_value.first.return_value = mock_mem
            from services.user_memory import update_memory
            update_memory(1, {'psychology_note': 'avoids overnight positions'})
        assert 'patient trader' in mock_mem.psychology_notes
        assert 'avoids overnight' in mock_mem.psychology_notes

    def test_nothing_new_patch_not_applied(self):
        """extract_and_update_memory must skip patches with nothing_new=True."""
        import json
        import re as _re
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"nothing_new": true}')]

        mock_mem = self._make_mock_mem(interaction_count=0)
        with patch('models.UserFinancialMemory') as MockModel, \
             patch('models.db') as mock_db, \
             patch('anthropic.Anthropic') as MockAnthropic, \
             patch('services.user_memory.get_memory', return_value={'interaction_count': 0}), \
             patch('services.user_memory._increment_only') as mock_inc, \
             patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):

            MockModel.query.filter_by.return_value.first.return_value = mock_mem
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            from services.user_memory import extract_and_update_memory
            with patch('services.user_memory.update_memory') as mock_update:
                extract_and_update_memory(1, 'What is the stock market?', '')
                # update_memory should NOT be called when nothing_new
                mock_update.assert_not_called()
                # but the counter should still be incremented
                mock_inc.assert_called_once()
