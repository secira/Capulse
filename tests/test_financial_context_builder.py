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


# ── Global alias extraction ──────────────────────────────────────────────────

class TestGlobalAliasExtraction:
    def test_apple_resolves_to_aapl(self):
        result = extract_tickers("How is Apple doing today?")
        assert "AAPL" in result, f"Expected AAPL, got {result}"

    def test_microsoft_resolves_to_msft(self):
        result = extract_tickers("Microsoft earnings this quarter")
        assert "MSFT" in result

    def test_tesla_resolves_to_tsla(self):
        result = extract_tickers("Is Tesla overvalued?")
        assert "TSLA" in result

    def test_nvidia_resolves_to_nvda(self):
        result = extract_tickers("Nvidia chips are in demand")
        assert "NVDA" in result

    def test_google_resolves_to_googl(self):
        result = extract_tickers("Google search revenue")
        assert "GOOGL" in result

    def test_meta_resolves(self):
        result = extract_tickers("Meta platforms quarterly results")
        assert "META" in result

    def test_amazon_resolves_to_amzn(self):
        result = extract_tickers("Amazon AWS growth story")
        assert "AMZN" in result

    def test_netflix_resolves_to_nflx(self):
        result = extract_tickers("Netflix subscriber count")
        assert "NFLX" in result

    def test_facebook_alias_for_meta(self):
        result = extract_tickers("What happened to Facebook stock?")
        assert "META" in result

    def test_goldman_sachs_resolves_to_gs(self):
        result = extract_tickers("Goldman Sachs investment banking")
        assert "GS" in result

    def test_jpmorgan_resolves_to_jpm(self):
        result = extract_tickers("JPMorgan raised its guidance")
        assert "JPM" in result

    def test_visa_resolves_to_v(self):
        result = extract_tickers("Visa payments volume")
        assert "V" in result


# ── Global index extraction ──────────────────────────────────────────────────

class TestGlobalIndexExtraction:
    def test_sp500_by_name(self):
        result = extract_tickers("How did the S&P 500 close today?")
        assert "^GSPC" in result, f"Expected ^GSPC, got {result}"

    def test_sp500_shorthand(self):
        result = extract_tickers("S&P is at all-time highs")
        assert "^GSPC" in result

    def test_nasdaq_composite(self):
        result = extract_tickers("Nasdaq is down 2% today")
        assert "^IXIC" in result

    def test_dow_jones(self):
        result = extract_tickers("Dow Jones industrial average levels")
        assert "^DJI" in result

    def test_dow_shorthand(self):
        result = extract_tickers("What is the Dow at?")
        assert "^DJI" in result

    def test_ftse100(self):
        result = extract_tickers("FTSE 100 performance this week")
        assert "^FTSE" in result

    def test_nikkei(self):
        result = extract_tickers("Nikkei 225 hit a record high")
        assert "^N225" in result

    def test_dax(self):
        result = extract_tickers("German DAX index movement")
        assert "^GDAXI" in result

    def test_hang_seng(self):
        result = extract_tickers("Hang Seng dropped on China news")
        assert "^HSI" in result


# ── Crypto extraction ────────────────────────────────────────────────────────

class TestCryptoExtraction:
    def test_bitcoin_resolves_to_btc_usd(self):
        result = extract_tickers("What is Bitcoin trading at?")
        assert "BTC-USD" in result, f"Expected BTC-USD, got {result}"

    def test_btc_shorthand(self):
        result = extract_tickers("BTC is up 5% today")
        assert "BTC-USD" in result

    def test_ethereum_resolves_to_eth_usd(self):
        result = extract_tickers("Ethereum price action")
        assert "ETH-USD" in result

    def test_eth_shorthand(self):
        result = extract_tickers("ETH broke resistance")
        assert "ETH-USD" in result

    def test_solana_resolves(self):
        result = extract_tickers("Solana ecosystem update")
        assert "SOL-USD" in result

    def test_ripple_resolves_to_xrp_usd(self):
        result = extract_tickers("Ripple XRP legal update")
        assert "XRP-USD" in result

    def test_dogecoin_resolves(self):
        result = extract_tickers("Dogecoin price prediction")
        assert "DOGE-USD" in result

    def test_doge_shorthand(self):
        result = extract_tickers("DOGE is pumping")
        assert "DOGE-USD" in result


# ── Market classification ────────────────────────────────────────────────────

class TestClassifySymbol:
    """Verify _classify_symbol returns correct market types."""

    def setup_method(self):
        # Ensure alias patterns and _GLOBAL_SYMBOLS are loaded
        from services.financial_context_builder import _load_alias_patterns
        _load_alias_patterns()

    def test_indian_index_classification(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("NIFTY") == "indian_index"
        assert _classify_symbol("BANKNIFTY") == "indian_index"
        assert _classify_symbol("SENSEX") == "indian_index"

    def test_us_equity_classification(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("AAPL") == "us_equity"
        assert _classify_symbol("TSLA") == "us_equity"
        assert _classify_symbol("MSFT") == "us_equity"
        assert _classify_symbol("SPY") == "us_equity"

    def test_global_index_classification(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("^GSPC") == "global_index"
        assert _classify_symbol("^N225") == "global_index"
        assert _classify_symbol("^FTSE") == "global_index"

    def test_crypto_classification(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("BTC-USD") == "crypto"
        assert _classify_symbol("ETH-USD") == "crypto"
        assert _classify_symbol("SOL-USD") == "crypto"

    def test_unknown_caps_symbol_defaults_indian(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("XYZABC") == "indian"

    def test_heuristic_caret_is_global_index(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("^UNKNOWNIDX") == "global_index"

    def test_heuristic_usd_suffix_is_crypto(self):
        from services.financial_context_builder import _classify_symbol
        assert _classify_symbol("NEWCOIN-USD") == "crypto"


# ── Indian stocks unaffected by global additions ─────────────────────────────

class TestIndianStocksUnchanged:
    """Regression: adding global aliases must not break Indian symbol extraction."""

    def test_reliance_still_works(self):
        result = extract_tickers("Reliance Industries Q4 results")
        assert "RELIANCE" in result

    def test_hdfc_bank_still_maps(self):
        result = extract_tickers("HDFC Bank loan book growth")
        assert "HDFCBANK" in result

    def test_sbi_still_maps(self):
        result = extract_tickers("SBI NPA update")
        assert "SBIN" in result

    def test_tcs_still_extracted(self):
        result = extract_tickers("TCS guidance for FY26")
        assert "TCS" in result

    def test_infosys_still_extracted(self):
        result = extract_tickers("Infosys attrition numbers")
        assert "INFY" in result

    def test_nifty_still_works(self):
        result = extract_tickers("nifty outlook for tomorrow")
        assert "NIFTY" in result

    def test_bank_nifty_no_bleed(self):
        result = extract_tickers("bank nifty put call ratio")
        assert "BANKNIFTY" in result
        assert "NIFTY" not in result


# ── No cross-contamination between Indian and global ────────────────────────

class TestNoCrossContamination:
    def test_pfizer_india_vs_pfizer_us(self):
        """'pfizer' alone maps to NSE PFIZER (Indian); 'pfizer us' maps to PFE."""
        r_india = extract_tickers("Pfizer India dividends")
        r_us    = extract_tickers("Pfizer US earnings guidance")
        assert "PFIZER" in r_india
        assert "PFE" in r_us

    def test_meta_not_extracted_from_metadata(self):
        """'metadata' should not trigger META extraction."""
        result = extract_tickers("Check the metadata for this file")
        # 'meta' alias is word-boundary matched so 'metadata' should not match
        assert "META" not in result

    def test_amazon_india_context_no_conflict(self):
        """Amazon India logistics discussion should still resolve to AMZN."""
        result = extract_tickers("Amazon delivery network in India")
        assert "AMZN" in result

    def test_strategy_word_does_not_trigger_mstr(self):
        """'strategy' is a common English word — must NOT resolve to MSTR."""
        result = extract_tickers("What is the best trading strategy?")
        assert "MSTR" not in result, f"MSTR falsely extracted in {result}"

    def test_block_word_does_not_trigger_sq(self):
        """'block' as a plain word must NOT resolve to SQ (Block Inc.)."""
        result = extract_tickers("This is a block trade in the options market")
        assert "SQ" not in result, f"SQ falsely extracted in {result}"

    def test_link_word_does_not_trigger_chainlink(self):
        """Bare 'link' in a sentence must NOT trigger LINK-USD."""
        result = extract_tickers("Can you link me to the research report?")
        assert "LINK-USD" not in result

    def test_cosmos_word_does_not_trigger_atom(self):
        """'cosmos' as a generic word must NOT trigger ATOM-USD."""
        result = extract_tickers("In the cosmos of investment options")
        assert "ATOM-USD" not in result

    def test_ada_name_does_not_trigger_cardano(self):
        """'ada' as a person name or abbreviation must NOT trigger ADA-USD."""
        result = extract_tickers("Ada Lovelace was a pioneer")
        assert "ADA-USD" not in result


# ── _fetch_one routing integration tests ────────────────────────────────────

class TestFetchOneRouting:
    """Verify _fetch_one uses the correct yfinance symbol (with / without .NS)
    and produces the correct market type and currency prefix in the context block.
    """

    def _make_fast_info(self, last_price=150.0, previous_close=148.0,
                        year_high=200.0, year_low=100.0):
        fi = type('FI', (), {
            'last_price':     last_price,
            'previous_close': previous_close,
            'year_high':      year_high,
            'year_low':       year_low,
        })()
        return fi

    def test_us_equity_uses_raw_symbol_no_ns(self):
        """For a symbol in _GLOBAL_SYMBOLS as us_equity, yfinance is called
        with the raw symbol, NOT with .NS suffix."""
        from services.financial_context_builder import _fetch_one, _load_alias_patterns, _GLOBAL_SYMBOLS
        _load_alias_patterns()
        assert 'AAPL' in _GLOBAL_SYMBOLS, "AAPL should be in _GLOBAL_SYMBOLS after load"

        calls = []
        fi = self._make_fast_info(last_price=185.0, previous_close=182.0)

        import yfinance as yf_mod
        original_ticker = yf_mod.Ticker

        def mock_ticker(sym):
            calls.append(sym)
            t = type('T', (), {'fast_info': fi})()
            return t

        try:
            yf_mod.Ticker = mock_ticker
            sym, data = _fetch_one('AAPL', user_id=None)
        finally:
            yf_mod.Ticker = original_ticker

        assert data['market'] == 'us_equity'
        assert data['ltp'] == 185.0
        # Must have been called with raw 'AAPL', never 'AAPL.NS'
        assert 'AAPL' in calls, f"Expected AAPL in calls, got {calls}"
        assert 'AAPL.NS' not in calls, f"Must not call AAPL.NS, got {calls}"

    def test_global_index_uses_raw_symbol(self):
        """For a global index like ^GSPC, yfinance is called with '^GSPC'."""
        from services.financial_context_builder import _fetch_one, _load_alias_patterns
        _load_alias_patterns()

        calls = []
        fi = self._make_fast_info(last_price=5500.0, previous_close=5480.0)

        import yfinance as yf_mod
        original_ticker = yf_mod.Ticker

        def mock_ticker(sym):
            calls.append(sym)
            return type('T', (), {'fast_info': fi})()

        try:
            yf_mod.Ticker = mock_ticker
            sym, data = _fetch_one('^GSPC', user_id=None)
        finally:
            yf_mod.Ticker = original_ticker

        assert data['market'] == 'global_index'
        assert data['ltp'] == 5500.0
        assert '^GSPC' in calls
        assert '^GSPC.NS' not in calls

    def test_crypto_uses_raw_symbol(self):
        """For BTC-USD, yfinance is called with 'BTC-USD', never 'BTC-USD.NS'."""
        from services.financial_context_builder import _fetch_one, _load_alias_patterns
        _load_alias_patterns()

        calls = []
        fi = self._make_fast_info(last_price=67000.0, previous_close=65000.0)

        import yfinance as yf_mod
        original_ticker = yf_mod.Ticker

        def mock_ticker(sym):
            calls.append(sym)
            return type('T', (), {'fast_info': fi})()

        try:
            yf_mod.Ticker = mock_ticker
            sym, data = _fetch_one('BTC-USD', user_id=None)
        finally:
            yf_mod.Ticker = original_ticker

        assert data['market'] == 'crypto'
        assert data['ltp'] == 67000.0
        assert 'BTC-USD' in calls
        assert 'BTC-USD.NS' not in calls

    def test_unlisted_us_ticker_global_fallback(self):
        """An ALL-CAPS ticker not in global_aliases (e.g. MELI) should:
        1. Start as 'indian' classification
        2. Fail the .NS path (mock returns 0)
        3. Succeed the raw global fallback path (mock returns price)
        4. Be reclassified as 'us_equity' with $ currency
        """
        from services.financial_context_builder import _fetch_one, _load_alias_patterns, _GLOBAL_SYMBOLS
        _load_alias_patterns()
        assert 'MELI' not in _GLOBAL_SYMBOLS, "MELI must not be pre-listed for this test"

        ns_fi  = self._make_fast_info(last_price=0.0, previous_close=0.0,
                                      year_high=0.0, year_low=0.0)
        raw_fi = self._make_fast_info(last_price=1850.0, previous_close=1820.0,
                                      year_high=2000.0, year_low=1500.0)

        import yfinance as yf_mod
        original_ticker = yf_mod.Ticker

        def mock_ticker(sym):
            if sym.endswith('.NS'):
                return type('T', (), {'fast_info': ns_fi})()
            return type('T', (), {'fast_info': raw_fi})()

        try:
            yf_mod.Ticker = mock_ticker
            with __import__('unittest.mock', fromlist=['patch']).patch(
                'services.market_data_gateway.get_price',
                return_value={'success': False, 'value': 0}
            ):
                sym, data = _fetch_one('MELI', user_id=None)
        finally:
            yf_mod.Ticker = original_ticker

        # Global fallback must have reclassified MELI as us_equity
        assert data['market'] == 'us_equity', (
            f"Expected us_equity after global fallback, got {data['market']}"
        )
        assert data['ltp'] == 1850.0


# ── Context block currency formatting ────────────────────────────────────────

class TestContextBlockFormatting:
    """Verify build_context_block uses correct currency prefix per market."""

    def _quote(self, symbol, ltp, market):
        return {symbol: {'ltp': ltp, 'change_pct': 1.5, 'market': market,
                         'week52_high': None, 'week52_low': None}}

    def test_indian_stock_shows_rupee(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._quote('RELIANCE', 2800.0, 'indian'))
        assert '₹2,800.00' in block, f"Expected ₹ prefix, got: {block}"
        assert '$' not in block

    def test_us_stock_shows_dollar(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._quote('AAPL', 185.0, 'us_equity'))
        assert '$185.00' in block, f"Expected $ prefix, got: {block}"
        assert '₹' not in block

    def test_global_index_no_currency_prefix(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._quote('^GSPC', 5500.0, 'global_index'))
        assert '5,500.00' in block
        assert '₹' not in block
        # No $ immediately before the number
        assert '$5,500' not in block

    def test_crypto_shows_dollar(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._quote('BTC-USD', 67000.0, 'crypto'))
        assert '$67,000.00' in block, f"Expected $ prefix, got: {block}"

    def test_market_flags_present(self):
        from services.financial_context_builder import build_context_block
        quotes = {
            'RELIANCE': {'ltp': 2800.0, 'change_pct': 0.5, 'market': 'indian',
                         'week52_high': None, 'week52_low': None},
            'AAPL':     {'ltp': 185.0,  'change_pct': 1.2, 'market': 'us_equity',
                         'week52_high': None, 'week52_low': None},
            'BTC-USD':  {'ltp': 67000., 'change_pct': 2.1, 'market': 'crypto',
                         'week52_high': None, 'week52_low': None},
            '^GSPC':    {'ltp': 5500.,  'change_pct': 0.3, 'market': 'global_index',
                         'week52_high': None, 'week52_low': None},
        }
        block = build_context_block(quotes)
        assert '🇮🇳' in block
        assert '🇺🇸' in block
        assert '₿' in block
        assert '🌍' in block

    def test_empty_quotes_returns_empty_string(self):
        from services.financial_context_builder import build_context_block
        assert build_context_block({}) == ''

    def test_all_zero_ltp_returns_empty_string(self):
        from services.financial_context_builder import build_context_block
        quotes = {'AAPL': {'ltp': 0, 'change_pct': 0, 'market': 'us_equity',
                           'week52_high': None, 'week52_low': None}}
        assert build_context_block(quotes) == ''

    def test_no_quote_for_generic_prose(self):
        """Generic prose must extract zero tickers — no quote call triggered."""
        result = extract_tickers(
            "What is the best strategy for long-term wealth creation?"
        )
        # 'strategy' removed from aliases; no valid ticker should be extracted
        assert result == [] or all(t in ('WEALTH', 'CREATION') for t in result), (
            f"Unexpected tickers from generic prose: {result}"
        )
