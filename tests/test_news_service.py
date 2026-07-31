"""
Tests for services/news_service.py and the news integration in
services/financial_context_builder.py.

Run with:  python -m pytest tests/test_news_service.py -v
"""

import pytest
import sys
import os
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.news_service import _age_str, get_stock_news, _yf_symbol


# ── _age_str ─────────────────────────────────────────────────────────────────

class TestAgeStr:
    def _ts(self, seconds_ago: int) -> int:
        return int(time.time()) - seconds_ago

    def test_minutes(self):
        assert _age_str(self._ts(300)) == '5m ago'

    def test_one_minute_minimum(self):
        assert _age_str(self._ts(30)) == '1m ago'

    def test_hours(self):
        assert _age_str(self._ts(3 * 3600)) == '3h ago'

    def test_days(self):
        assert _age_str(self._ts(2 * 86400)) == '2d ago'

    def test_weeks(self):
        assert _age_str(self._ts(14 * 86400)) == '2w ago'

    def test_zero_timestamp(self):
        assert _age_str(0) == ''

    def test_future_timestamp_returns_empty(self):
        assert _age_str(int(time.time()) + 3600) == ''


# ── _yf_symbol ───────────────────────────────────────────────────────────────

class TestYfSymbol:
    def test_indian_equity_gets_ns_suffix(self):
        assert _yf_symbol('RELIANCE', 'indian') == 'RELIANCE.NS'

    def test_us_equity_uses_raw(self):
        assert _yf_symbol('AAPL', 'us_equity') == 'AAPL'

    def test_crypto_uses_raw(self):
        assert _yf_symbol('BTC-USD', 'crypto') == 'BTC-USD'

    def test_global_index_uses_raw(self):
        assert _yf_symbol('^GSPC', 'global_index') == '^GSPC'


# ── get_stock_news — happy path ───────────────────────────────────────────────

def _make_ticker(news_items):
    t = MagicMock()
    t.news = news_items
    return t


def _sample_news(n=4):
    ts = int(time.time()) - 3600
    return [
        {'title': f'Headline {i+1}',
         'publisher': f'Source {i+1}',
         'providerPublishTime': ts - i * 600}
        for i in range(n)
    ]


class TestGetStockNews:
    def test_returns_up_to_max_items(self):
        with patch('yfinance.Ticker', return_value=_make_ticker(_sample_news(4))):
            result = get_stock_news('AAPL', market='us_equity', max_items=3)
        assert len(result) == 3

    def test_result_has_required_keys(self):
        with patch('yfinance.Ticker', return_value=_make_ticker(_sample_news(2))):
            result = get_stock_news('AAPL', market='us_equity', max_items=2)
        for item in result:
            assert 'title' in item
            assert 'publisher' in item
            assert 'age_str' in item

    def test_title_and_publisher_populated(self):
        with patch('yfinance.Ticker', return_value=_make_ticker(_sample_news(1))):
            result = get_stock_news('AAPL', market='us_equity', max_items=1)
        assert result[0]['title'] == 'Headline 1'
        assert result[0]['publisher'] == 'Source 1'

    def test_age_str_is_1h_ago(self):
        with patch('yfinance.Ticker', return_value=_make_ticker(_sample_news(1))):
            result = get_stock_news('AAPL', market='us_equity', max_items=1)
        assert result[0]['age_str'] == '1h ago'

    def test_indian_equity_uses_ns_suffix(self):
        called = []
        def mock_ticker(sym):
            called.append(sym)
            return _make_ticker([])
        with patch('yfinance.Ticker', side_effect=mock_ticker):
            get_stock_news('RELIANCE', market='indian', max_items=3)
        assert 'RELIANCE.NS' in called
        assert 'RELIANCE' not in called

    def test_us_equity_uses_raw_symbol(self):
        called = []
        def mock_ticker(sym):
            called.append(sym)
            return _make_ticker([])
        with patch('yfinance.Ticker', side_effect=mock_ticker):
            get_stock_news('AAPL', market='us_equity', max_items=3)
        assert 'AAPL' in called
        assert 'AAPL.NS' not in called

    def test_empty_news_returns_empty_list(self):
        with patch('yfinance.Ticker', return_value=_make_ticker([])):
            assert get_stock_news('AAPL', market='us_equity') == []

    def test_none_news_returns_empty_list(self):
        t = MagicMock(); t.news = None
        with patch('yfinance.Ticker', return_value=t):
            assert get_stock_news('AAPL', market='us_equity') == []

    def test_indian_index_returns_empty_without_calling_yfinance(self):
        with patch('yfinance.Ticker') as mock_yf:
            result = get_stock_news('NIFTY', market='indian_index')
        assert result == []
        mock_yf.assert_not_called()

    def test_yfinance_exception_returns_empty(self):
        with patch('yfinance.Ticker', side_effect=RuntimeError("network")):
            assert get_stock_news('AAPL', market='us_equity') == []

    def test_items_without_title_skipped(self):
        news = [
            {'title': '', 'publisher': 'Reuters', 'providerPublishTime': int(time.time()) - 60},
            {'title': 'Valid headline', 'publisher': 'Bloomberg', 'providerPublishTime': int(time.time()) - 120},
        ]
        with patch('yfinance.Ticker', return_value=_make_ticker(news)):
            result = get_stock_news('AAPL', market='us_equity', max_items=3)
        assert len(result) == 1
        assert result[0]['title'] == 'Valid headline'

    def test_new_yfinance_content_schema(self):
        """yfinance >= 0.2.x wraps items in a 'content' dict."""
        ts = int(time.time()) - 7200
        news = [{'content': {
            'title': 'Content-schema headline',
            'provider': {'displayName': 'CNBC'},
            'providerPublishTime': ts,
        }}]
        with patch('yfinance.Ticker', return_value=_make_ticker(news)):
            result = get_stock_news('TSLA', market='us_equity', max_items=1)
        assert len(result) == 1
        assert result[0]['title'] == 'Content-schema headline'
        assert result[0]['publisher'] == 'CNBC'
        assert result[0]['age_str'] == '2h ago'


# ── build_context_block with news ────────────────────────────────────────────

class TestBuildContextBlockWithNews:
    def _q(self, sym, ltp, market, news=None):
        return {sym: {'ltp': ltp, 'change_pct': 1.5, 'market': market,
                      'week52_high': None, 'week52_low': None, 'news': news or []}}

    def _sample(self):
        return [
            {'title': 'Company posts record revenue', 'publisher': 'Reuters', 'age_str': '2h ago'},
            {'title': 'Analyst raises target price',  'publisher': 'Bloomberg', 'age_str': '5h ago'},
        ]

    def test_headlines_appear_in_block(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._q('AAPL', 185.0, 'us_equity', self._sample()))
        assert 'Company posts record revenue' in block
        assert 'Analyst raises target price' in block

    def test_news_emoji_header_present(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._q('AAPL', 185.0, 'us_equity', self._sample()))
        assert '📰' in block

    def test_publisher_and_age_in_block(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._q('AAPL', 185.0, 'us_equity', self._sample()))
        assert 'Reuters' in block
        assert '2h ago' in block

    def test_no_news_section_when_empty(self):
        from services.financial_context_builder import build_context_block
        assert '📰' not in build_context_block(self._q('AAPL', 185.0, 'us_equity', []))

    def test_no_news_section_when_key_absent(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block({'AAPL': {'ltp': 185.0, 'change_pct': 1.5,
                                              'market': 'us_equity',
                                              'week52_high': None, 'week52_low': None}})
        assert '📰' not in block

    def test_footer_mentions_news_when_present(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._q('AAPL', 185.0, 'us_equity', self._sample()))
        assert 'news headlines' in block.lower()
        assert 'Do not fabricate' in block

    def test_footer_no_news_instruction_when_no_news(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._q('AAPL', 185.0, 'us_equity', []))
        assert 'live/delayed' in block
        assert 'Do not fabricate' not in block

    def test_max_three_headlines_rendered(self):
        from services.financial_context_builder import build_context_block
        news = [{'title': f'H{i}', 'publisher': 'BBC', 'age_str': '1h ago'} for i in range(5)]
        block = build_context_block(self._q('TSLA', 250.0, 'us_equity', news))
        assert block.count('H') >= 3
        assert block.count(' – H') == 3   # 3 bullet dashes

    def test_price_and_news_both_present(self):
        from services.financial_context_builder import build_context_block
        block = build_context_block(self._q('RELIANCE', 2800.0, 'indian', self._sample()))
        assert '₹2,800.00' in block
        assert 'Company posts record revenue' in block

    def test_indian_stock_currency_correct_with_news(self):
        from services.financial_context_builder import build_context_block
        news = [{'title': 'RBI keeps rates steady', 'publisher': 'ET', 'age_str': '1h ago'}]
        block = build_context_block(self._q('SBIN', 650.0, 'indian', news))
        assert '₹650.00' in block
        assert 'RBI keeps rates steady' in block
        assert '$650' not in block


# ── Timeout resilience ────────────────────────────────────────────────────────

class TestNewsTimeoutResilience:
    def test_slow_news_does_not_block_price_result(self):
        """A hanging news fetch must not prevent prices from being returned."""
        import time as _time
        from services.financial_context_builder import fetch_live_context, _load_alias_patterns
        _load_alias_patterns()

        fi = type('FI', (), {
            'last_price': 185.0, 'previous_close': 183.0,
            'year_high': 200.0,  'year_low': 150.0,
        })()

        def slow_news(symbol, market='indian', max_items=3):
            _time.sleep(10)
            return []

        import yfinance as yf_mod
        orig = yf_mod.Ticker

        def mock_ticker(sym):
            return type('T', (), {'fast_info': fi})()

        try:
            yf_mod.Ticker = mock_ticker
            with patch('services.news_service.get_stock_news', side_effect=slow_news), \
                 patch('services.market_data_gateway.get_price',
                       return_value={'success': False, 'value': 0}):
                t0 = _time.monotonic()
                results = fetch_live_context(['AAPL'], timeout=1.5)
                elapsed = _time.monotonic() - t0
        finally:
            yf_mod.Ticker = orig

        assert elapsed < 3.0, f"Blocked for {elapsed:.1f}s — news timeout not respected"
        assert 'AAPL' in results, f"Price missing: {results}"
