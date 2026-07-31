"""
Tests for the fundamentals fetching and card-building added in Task #30.

Covers:
  - _rec_label()                    — mapping recommendationMean to label
  - _fmt_market_cap()               — Indian Cr vs US $B formatting
  - _fetch_fundamentals()           — yfinance.info mocking, field extraction
  - fetch_fundamentals()            — timeout / pool integration
  - build_fundamentals_context_block() — LLM prompt text output

Run with:  python -m pytest tests/test_fundamentals.py -v
"""

import sys
import os
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.financial_context_builder import (
    _rec_label,
    _fmt_market_cap,
    _fetch_fundamentals,
    fetch_fundamentals,
    build_fundamentals_context_block,
)


# ── _rec_label ────────────────────────────────────────────────────────────────

class TestRecLabel:
    def test_strong_buy(self):
        assert _rec_label(1.2) == 'Strong Buy'

    def test_buy(self):
        assert _rec_label(2.0) == 'Buy'

    def test_hold(self):
        assert _rec_label(3.0) == 'Hold'

    def test_underperform(self):
        assert _rec_label(4.0) == 'Underperform'

    def test_sell(self):
        assert _rec_label(5.0) == 'Sell'

    def test_boundary_1_5_is_strong_buy(self):
        assert _rec_label(1.5) == 'Strong Buy'

    def test_boundary_2_5_is_buy(self):
        assert _rec_label(2.5) == 'Buy'

    def test_none_with_key_fallback(self):
        assert _rec_label(None, 'buy') == 'Buy'

    def test_none_with_no_key(self):
        assert _rec_label(None, '') is None

    def test_rec_key_underscore_converted(self):
        assert _rec_label(None, 'strong_buy') == 'Strong Buy'


# ── _fmt_market_cap ───────────────────────────────────────────────────────────

class TestFmtMarketCap:
    def test_indian_crores(self):
        result = _fmt_market_cap(1_38_000_00_00_000, 'indian')  # ~₹1,38,000 Cr
        assert '₹' in result
        assert 'Cr' in result

    def test_indian_lakh_crore(self):
        result = _fmt_market_cap(1_38_00_000_00_00_000, 'indian')  # 1.38L Cr
        assert 'L Cr' in result

    def test_us_billions(self):
        result = _fmt_market_cap(3_000_000_000_000, 'us_equity')  # $3T
        assert '$' in result

    def test_us_small_cap(self):
        result = _fmt_market_cap(500_000_000, 'us_equity')  # $0.5B
        assert '$' in result and 'B' in result


# ── _fetch_fundamentals ───────────────────────────────────────────────────────

def _mock_info(**kwargs):
    """Build a mock yfinance.Ticker whose .info returns kwargs."""
    ticker = MagicMock()
    ticker.info = kwargs
    return ticker


class TestFetchFundamentals:
    def _full_info(self):
        return {
            'longName':               'Tata Consultancy Services Limited',
            'sector':                 'Technology',
            'industry':               'IT Services',
            'marketCap':              1_38_000_00_00_000,
            'trailingPE':             28.5,
            'forwardPE':              24.3,
            'trailingEps':            133.5,
            'dividendYield':          0.018,
            'fiftyTwoWeekHigh':       4200.0,
            'fiftyTwoWeekLow':        3200.0,
            'targetMeanPrice':        4100.0,
            'recommendationMean':     2.0,
            'recommendationKey':      'buy',
            'numberOfAnalystOpinions': 32,
        }

    def test_basic_fields_populated(self):
        with patch('yfinance.Ticker', return_value=_mock_info(**self._full_info())):
            sym, data = _fetch_fundamentals('TCS', 'indian')
        assert sym == 'TCS'
        assert data['company_name'] == 'Tata Consultancy Services Limited'
        assert data['trailing_pe'] == 28.5
        assert data['forward_pe'] == 24.3
        assert data['trailing_eps'] == 133.5
        assert data['analyst_count'] == 32

    def test_recommendation_label_derived(self):
        with patch('yfinance.Ticker', return_value=_mock_info(**self._full_info())):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data['recommendation'] == 'Buy'
        assert data['recommendation_mean'] == 2.0

    def test_dividend_yield_converted_to_percent(self):
        info = self._full_info()
        info['dividendYield'] = 0.018
        with patch('yfinance.Ticker', return_value=_mock_info(**info)):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert abs(data['dividend_yield'] - 1.8) < 0.01

    def test_indian_equity_uses_ns_suffix(self):
        called = []
        def mock_ticker(sym):
            called.append(sym)
            t = MagicMock()
            t.info = {}
            return t
        with patch('yfinance.Ticker', side_effect=mock_ticker):
            _fetch_fundamentals('TCS', 'indian')
        assert 'TCS.NS' in called
        assert 'TCS' not in called

    def test_us_equity_uses_raw_symbol(self):
        called = []
        def mock_ticker(sym):
            called.append(sym)
            t = MagicMock()
            t.info = {}
            return t
        with patch('yfinance.Ticker', side_effect=mock_ticker):
            _fetch_fundamentals('AAPL', 'us_equity')
        assert 'AAPL' in called
        assert 'AAPL.NS' not in called

    def test_empty_info_returns_empty_dict(self):
        with patch('yfinance.Ticker', return_value=_mock_info()):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data == {}

    def test_exception_returns_empty_dict(self):
        with patch('yfinance.Ticker', side_effect=RuntimeError("network")):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data == {}

    def test_market_cap_str_populated(self):
        with patch('yfinance.Ticker', return_value=_mock_info(**self._full_info())):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data['market_cap_str'] is not None
        assert '₹' in data['market_cap_str']

    def test_52w_range_populated(self):
        with patch('yfinance.Ticker', return_value=_mock_info(**self._full_info())):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data['week52_high'] == 4200.0
        assert data['week52_low'] == 3200.0

    def test_target_and_upside_present(self):
        with patch('yfinance.Ticker', return_value=_mock_info(**self._full_info())):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data['target_price'] == 4100.0


# ── fetch_fundamentals (pool integration) ─────────────────────────────────────

class TestFetchFundamentalsPool:
    def test_returns_dict_on_success(self):
        info = {
            'longName': 'Apple Inc.', 'marketCap': 3_000_000_000_000,
            'trailingPE': 28.0, 'recommendationMean': 1.8,
            'numberOfAnalystOpinions': 45,
        }
        with patch('yfinance.Ticker', return_value=_mock_info(**info)):
            data = fetch_fundamentals('AAPL')
        assert isinstance(data, dict)
        assert data.get('company_name') == 'Apple Inc.'

    def test_returns_empty_on_yfinance_error(self):
        with patch('yfinance.Ticker', side_effect=RuntimeError("timeout")):
            data = fetch_fundamentals('AAPL', timeout=0.5)
        assert data == {}


# ── build_fundamentals_context_block ──────────────────────────────────────────

class TestBuildFundamentalsContextBlock:
    def _fund(self, **overrides):
        base = {
            'market':             'indian',
            'company_name':       'TCS',
            'market_cap':         1_38_000_00_00_000,
            'market_cap_str':     '₹1,38,000 Cr',
            'trailing_pe':        28.5,
            'forward_pe':         24.3,
            'trailing_eps':       133.5,
            'dividend_yield':     1.8,
            'week52_high':        4200.0,
            'week52_low':         3200.0,
            'target_price':       4100.0,
            'recommendation':     'Buy',
            'recommendation_mean': 2.0,
            'analyst_count':      32,
        }
        base.update(overrides)
        return base

    def _quote(self, ltp=3800.0, chg=1.2):
        return {'ltp': ltp, 'change_pct': chg, 'market': 'indian'}

    def test_block_has_fundamentals_header(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'FUNDAMENTALS' in block

    def test_block_has_pe(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'P/E' in block

    def test_block_has_market_cap(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'Market Cap' in block

    def test_block_has_analyst_consensus(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'Consensus' in block
        assert 'Buy' in block

    def test_block_has_target_price(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'Target' in block

    def test_block_has_52w_range(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert '52w' in block

    def test_block_has_footer_instruction(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'Yahoo Finance' in block

    def test_empty_fundamentals_returns_empty_string(self):
        block = build_fundamentals_context_block('TCS', self._quote(), {})
        assert block == ''

    def test_us_stock_uses_dollar_sign(self):
        fund = self._fund(market='us_equity', market_cap_str='$3.0T')
        quote = {'ltp': 185.0, 'change_pct': 0.5, 'market': 'us_equity'}
        block = build_fundamentals_context_block('AAPL', quote, fund)
        assert '$' in block

    def test_upside_calculation_in_block(self):
        # target=4100, ltp=3800 → ~7.9% upside
        block = build_fundamentals_context_block('TCS', self._quote(ltp=3800), self._fund(target_price=4100))
        assert '↑' in block

    def test_downside_shown_when_below_target(self):
        # ltp=4500 > target=4100
        block = build_fundamentals_context_block('TCS', self._quote(ltp=4500), self._fund(target_price=4100))
        assert '↓' in block

    def test_no_price_line_when_ltp_zero(self):
        block = build_fundamentals_context_block('TCS', {'ltp': 0, 'change_pct': 0, 'market': 'indian'}, self._fund())
        # Block may exist (market cap, PE present), but no price line
        assert '↑' not in block or 'price' not in block.lower()

    def test_missing_pe_skipped_gracefully(self):
        fund = self._fund(trailing_pe=None, forward_pe=None)
        block = build_fundamentals_context_block('TCS', self._quote(), fund)
        assert 'FUNDAMENTALS' in block   # block still produced
        assert 'P/E' not in block

    def test_missing_dividend_skipped(self):
        fund = self._fund(dividend_yield=None)
        block = build_fundamentals_context_block('TCS', self._quote(), fund)
        assert 'Dividend' not in block


# ── _valuation_vs_sector ──────────────────────────────────────────────────────

from services.financial_context_builder import _valuation_vs_sector, _SECTOR_PE_BENCHMARKS

class TestValuationVsSector:
    def test_cheap_below_80pct(self):
        # Technology benchmark = 28; 20 / 28 = 0.71 → Cheap
        _, label = _valuation_vs_sector(20.0, 'Technology')
        assert label == 'Cheap'

    def test_fair_between_80_and_125(self):
        # Technology benchmark = 28; 28 / 28 = 1.0 → Fair
        _, label = _valuation_vs_sector(28.0, 'Technology')
        assert label == 'Fair'

    def test_expensive_above_125(self):
        # Technology benchmark = 28; 40 / 28 = 1.43 → Expensive
        _, label = _valuation_vs_sector(40.0, 'Technology')
        assert label == 'Expensive'

    def test_returns_sector_pe_value(self):
        sector_pe, _ = _valuation_vs_sector(28.0, 'Technology')
        assert sector_pe == _SECTOR_PE_BENCHMARKS['Technology']

    def test_none_when_pe_is_zero(self):
        sector_pe, label = _valuation_vs_sector(0.0, 'Technology')
        assert sector_pe is None
        assert label is None

    def test_none_when_pe_is_none(self):
        sector_pe, label = _valuation_vs_sector(None, 'Technology')
        assert sector_pe is None
        assert label is None

    def test_none_when_sector_unknown(self):
        sector_pe, label = _valuation_vs_sector(25.0, 'Junk Sector XYZ')
        assert sector_pe is None
        assert label is None

    def test_none_when_sector_empty(self):
        sector_pe, label = _valuation_vs_sector(25.0, '')
        assert sector_pe is None
        assert label is None

    def test_fuzzy_match_partial_sector_name(self):
        # "Financial" should fuzzy-match "Financial Services"
        sector_pe, label = _valuation_vs_sector(18.0, 'Financial')
        assert sector_pe is not None
        assert label is not None

    def test_boundary_exactly_80pct_is_cheap(self):
        # 28 * 0.80 = 22.4 → Cheap
        sector_pe, label = _valuation_vs_sector(22.4, 'Technology')
        assert label == 'Cheap'

    def test_boundary_exactly_125pct_is_fair(self):
        # 28 * 1.25 = 35 → Fair
        sector_pe, label = _valuation_vs_sector(35.0, 'Technology')
        assert label == 'Fair'

    def test_all_known_sectors_covered(self):
        # Every sector in the table should return a valid result for a mid-range PE
        for sector, benchmark_pe in _SECTOR_PE_BENCHMARKS.items():
            sp, lbl = _valuation_vs_sector(benchmark_pe, sector)
            assert sp == benchmark_pe, f"sector {sector}: expected {benchmark_pe}, got {sp}"
            assert lbl == 'Fair', f"sector {sector}: PE at median should be Fair, got {lbl}"


# ── _fetch_fundamentals: sector_pe + valuation_label fields ──────────────────

class TestFetchFundamentalsWithSectorPE:
    def _full_info(self):
        return {
            'longName':               'Tata Consultancy Services Limited',
            'sector':                 'Technology',
            'industry':               'IT Services',
            'marketCap':              1_38_000_00_00_000,
            'trailingPE':             20.0,   # < 80% of 28 → Cheap
            'forwardPE':              18.0,
            'trailingEps':            133.5,
            'dividendYield':          0.018,
            'fiftyTwoWeekHigh':       4200.0,
            'fiftyTwoWeekLow':        3200.0,
            'targetMeanPrice':        4100.0,
            'recommendationMean':     2.0,
            'recommendationKey':      'buy',
            'numberOfAnalystOpinions': 32,
        }

    def _mock_ticker(self, info):
        t = MagicMock()
        t.info = info
        return t

    def test_sector_pe_field_populated(self):
        with patch('yfinance.Ticker', return_value=self._mock_ticker(self._full_info())):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data.get('sector_pe') == _SECTOR_PE_BENCHMARKS['Technology']

    def test_valuation_label_cheap(self):
        # PE 20 vs Technology 28 → Cheap
        with patch('yfinance.Ticker', return_value=self._mock_ticker(self._full_info())):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data.get('valuation_label') == 'Cheap'

    def test_valuation_label_expensive(self):
        info = self._full_info()
        info['trailingPE'] = 55.0   # > 1.25 × 28 = 35
        with patch('yfinance.Ticker', return_value=self._mock_ticker(info)):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data.get('valuation_label') == 'Expensive'

    def test_valuation_label_none_when_sector_unknown(self):
        info = self._full_info()
        info['sector'] = ''
        with patch('yfinance.Ticker', return_value=self._mock_ticker(info)):
            _, data = _fetch_fundamentals('TCS', 'indian')
        assert data.get('sector_pe') is None
        assert data.get('valuation_label') is None


# ── build_fundamentals_context_block: sector comparison line ─────────────────

class TestContextBlockWithSectorPE:
    def _fund(self, **overrides):
        base = {
            'market':             'indian',
            'company_name':       'TCS',
            'market_cap':         1_38_000_00_00_000,
            'market_cap_str':     '₹1,38,000 Cr',
            'trailing_pe':        20.0,
            'sector':             'Technology',
            'sector_pe':          28.0,
            'valuation_label':    'Cheap',
        }
        base.update(overrides)
        return base

    def _quote(self, ltp=3800.0):
        return {'ltp': ltp, 'change_pct': 1.2, 'market': 'indian'}

    def test_sector_comparison_line_present(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'vs Sector' in block
        assert '28.0x' in block

    def test_cheap_label_in_block(self):
        block = build_fundamentals_context_block('TCS', self._quote(), self._fund())
        assert 'Cheap' in block

    def test_expensive_label_in_block(self):
        fund = self._fund(trailing_pe=55.0, sector_pe=28.0, valuation_label='Expensive')
        block = build_fundamentals_context_block('TCS', self._quote(), fund)
        assert 'Expensive' in block

    def test_no_sector_line_when_sector_pe_missing(self):
        fund = self._fund(sector_pe=None, valuation_label=None)
        block = build_fundamentals_context_block('TCS', self._quote(), fund)
        assert 'vs Sector' not in block
