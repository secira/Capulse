"""
Tests for services/financial_context_builder.py — ticker extraction logic.

Run with:  python -m pytest tests/test_financial_context_builder.py -v
"""

import pytest
import sys
import os

# Allow importing without the full app context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.financial_context_builder import extract_tickers


# ── False-positive regressions ──────────────────────────────────────────────

class TestFalsePositives:
    def test_recommend_does_not_match_recltd(self):
        """'rec' inside 'recommend' must NOT match RECLTD."""
        result = extract_tickers("Can you recommend a mutual fund?")
        assert "RECLTD" not in result, f"False positive RECLTD in {result}"

    def test_ordinary_english_sentence(self):
        """Generic question with no tickers should return empty."""
        result = extract_tickers("What is the best way to start investing?")
        # Should not match any stop-word-like tokens
        assert "IT" not in result
        assert "IS" not in result
        assert "TO" not in result

    def test_india_in_sentence_not_a_ticker(self):
        """'INDIA' alone should not be extracted as an equity ticker."""
        result = extract_tickers("What are India VIX levels today?")
        # INDIA VIX should be caught as the index, not as the equity INDIA
        assert "INDIA" not in result

    def test_no_false_positive_from_ema_mention(self):
        """EMA is a stop word, not a ticker."""
        result = extract_tickers("what is the EMA 9/21 for nifty?")
        assert "EMA" not in result


# ── Index keyword extraction ─────────────────────────────────────────────────

class TestIndexExtraction:
    def test_bank_nifty_single_symbol(self):
        """'BANK NIFTY' must produce exactly BANKNIFTY, not also NIFTY or BANK."""
        result = extract_tickers("What is the BANK NIFTY direction today?")
        assert "BANKNIFTY" in result
        assert "NIFTY" not in result, f"NIFTY should not appear separately: {result}"
        assert "BANK" not in result, f"BANK should not appear as a ticker: {result}"

    def test_nifty_extracted(self):
        result = extract_tickers("what is the nifty market direction today?")
        assert "NIFTY" in result

    def test_banknifty_single_word(self):
        result = extract_tickers("BANKNIFTY setup today")
        assert "BANKNIFTY" in result
        assert "NIFTY" not in result

    def test_finnifty(self):
        result = extract_tickers("FIN NIFTY analysis")
        assert "FINNIFTY" in result
        assert "NIFTY" not in result

    def test_sensex(self):
        result = extract_tickers("How is the Sensex performing?")
        assert "SENSEX" in result

    def test_india_vix(self):
        result = extract_tickers("India VIX is very high today")
        assert "INDIA VIX" in result

    def test_vix_alone(self):
        result = extract_tickers("check the vix levels")
        assert "INDIA VIX" in result


# ── Alias extraction ─────────────────────────────────────────────────────────

class TestAliasExtraction:
    def test_reliance_by_name(self):
        result = extract_tickers("How is Reliance doing today?")
        assert "RELIANCE" in result

    def test_hdfc_bank(self):
        result = extract_tickers("tell me about HDFC Bank")
        assert "HDFCBANK" in result

    def test_tcs_caps(self):
        result = extract_tickers("What is TCS trading at?")
        assert "TCS" in result

    def test_infosys_by_name(self):
        result = extract_tickers("infosys results today")
        assert "INFY" in result

    def test_two_stocks(self):
        result = extract_tickers("Compare TCS and Infosys")
        assert "TCS" in result
        assert "INFY" in result

    def test_sbi_by_acronym(self):
        result = extract_tickers("SBI loan growth this quarter")
        assert "SBIN" in result

    def test_maruti_by_name(self):
        result = extract_tickers("Maruti sales numbers")
        assert "MARUTI" in result


# ── Cap at 5 results ────────────────────────────────────────────────────────

class TestCap:
    def test_max_five_results(self):
        text = "Compare RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, AXISBANK all together"
        result = extract_tickers(text)
        assert len(result) <= 5


# ── Non-overlapping spans ────────────────────────────────────────────────────

class TestNonOverlapping:
    def test_longest_alias_wins(self):
        """'HDFC Bank' should map to HDFCBANK, not HDFC (which maps to HDFCBANK too — same)."""
        result = extract_tickers("HDFC Bank quarterly results")
        assert "HDFCBANK" in result
        # Should appear exactly once even if multiple aliases match
        assert result.count("HDFCBANK") == 1

    def test_nifty50_not_double_nifty(self):
        """'NIFTY 50' should produce NIFTY once."""
        result = extract_tickers("nifty 50 levels today")
        nifty_count = result.count("NIFTY")
        assert nifty_count == 1, f"NIFTY appeared {nifty_count} times in {result}"
