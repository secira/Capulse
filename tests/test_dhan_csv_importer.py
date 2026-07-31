"""
Tests for services/dhan_csv_importer.py

Covers:
  - resolve_name_to_symbol()   — name-to-symbol resolution strategies
  - is_dhan_csv()              — header detection
  - parse_dhan_csv()           — CSV parsing, BOM handling, bad rows
  - parse_and_import_dhan_csv()— end-to-end upsert via mocked DB

Run with:  python -m pytest tests/test_dhan_csv_importer.py -v
"""
import io
import sys
import os
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ── resolve_name_to_symbol ────────────────────────────────────────────────────

class TestResolveNameToSymbol:
    def setup_method(self):
        # Reset cached alias map so tests load fresh
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None

    def _resolve(self, name):
        from services.dhan_csv_importer import resolve_name_to_symbol
        return resolve_name_to_symbol(name)

    def test_direct_match(self):
        sym, conf = self._resolve('Tata Steel')
        assert sym == 'TATASTEEL'
        assert conf == 100

    def test_direct_match_case_insensitive(self):
        sym, conf = self._resolve('canara bank')
        assert sym == 'CANARABANK'
        assert conf == 100

    def test_exact_alias_with_trailing_space(self):
        sym, conf = self._resolve('  sbi  ')
        assert sym == 'SBIN'

    def test_suffix_stripped_match(self):
        # "Infosys Limited" → strip "Limited" → "Infosys" → INFY
        sym, conf = self._resolve('Infosys Limited')
        assert sym == 'INFY'
        assert conf >= 70

    def test_already_uppercase_symbol(self):
        sym, conf = self._resolve('RELIANCE')
        # Should match via alias map (direct)
        assert sym == 'RELIANCE'
        assert conf > 0

    def test_unresolvable_name_returns_none(self):
        sym, conf = self._resolve('XYZ Unknown Corp ZZZ')
        assert sym is None
        assert conf == 0

    def test_tcs(self):
        sym, _ = self._resolve('Tata Consultancy Services')
        assert sym == 'TCS'

    def test_hdfc_bank(self):
        sym, _ = self._resolve('HDFC Bank')
        assert sym == 'HDFCBANK'

    def test_state_bank_of_india(self):
        sym, _ = self._resolve('State Bank of India')
        assert sym == 'SBIN'


# ── is_dhan_csv ───────────────────────────────────────────────────────────────

class TestIsDhanCsv:
    def test_valid_dhan_headers(self):
        from services.dhan_csv_importer import is_dhan_csv
        headers = ['name', 'quantity', 'avg price', 'last traded', 'investment', 'current value', 'p&l', 'p&l %']
        assert is_dhan_csv(headers) is True

    def test_valid_headers_with_spaces_and_case(self):
        from services.dhan_csv_importer import is_dhan_csv
        headers = ['Name', 'Quantity', 'Avg Price', 'Last Traded', 'Investment', 'Current Value']
        assert is_dhan_csv(headers) is True

    def test_missing_required_header(self):
        from services.dhan_csv_importer import is_dhan_csv
        headers = ['Name', 'Quantity', 'Avg Price', 'Last Traded']  # missing Investment, Current Value
        assert is_dhan_csv(headers) is False

    def test_unrelated_headers(self):
        from services.dhan_csv_importer import is_dhan_csv
        headers = ['Symbol', 'Qty', 'Price', 'Date']
        assert is_dhan_csv(headers) is False


# ── parse_dhan_csv ────────────────────────────────────────────────────────────

def _make_csv(rows, bom=True):
    """Helper to build a Dhan-format CSV bytes object."""
    header = '"Name","Quantity","Avg Price","Last Traded","Investment","Current Value","P&L","P&L %"'
    lines  = [header] + [
        f'"{r["Name"]}","{r["Qty"]}","{r["Avg"]}","{r["LTP"]}","{r["Inv"]}","{r["CV"]}","0","0%"'
        for r in rows
    ]
    content = '\n'.join(lines)
    if bom:
        content = '\ufeff' + content
    return io.BytesIO(content.encode('utf-8'))


class TestParseDhanCsv:
    def test_two_rows_resolved(self):
        from services.dhan_csv_importer import parse_dhan_csv, _ALIAS_MAP
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None  # reset

        csv_bytes = _make_csv([
            {'Name': 'Tata Steel', 'Qty': '10', 'Avg': '204.29', 'LTP': '189.85',
             'Inv': '2042.90', 'CV': '1898.50'},
            {'Name': 'Canara Bank', 'Qty': '5', 'Avg': '137.96', 'LTP': '125.04',
             'Inv': '689.80', 'CV': '625.20'},
        ])
        resolved, unresolved = parse_dhan_csv(csv_bytes)
        assert len(resolved) == 2
        assert len(unresolved) == 0
        symbols = {r['ticker_symbol'] for r in resolved}
        assert 'TATASTEEL' in symbols
        assert 'CANARABANK' in symbols

    def test_bom_stripped(self):
        """BOM prefix should not cause a resolution failure."""
        from services.dhan_csv_importer import parse_dhan_csv
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None
        csv_bytes = _make_csv([
            {'Name': 'Tata Steel', 'Qty': '1', 'Avg': '200', 'LTP': '190',
             'Inv': '200', 'CV': '190'},
        ], bom=True)
        resolved, _ = parse_dhan_csv(csv_bytes)
        assert resolved[0]['ticker_symbol'] == 'TATASTEEL'

    def test_unresolved_name_reported(self):
        from services.dhan_csv_importer import parse_dhan_csv
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None
        csv_bytes = _make_csv([
            {'Name': 'ZZZUNKNOWN Corp', 'Qty': '3', 'Avg': '100', 'LTP': '90',
             'Inv': '300', 'CV': '270'},
        ])
        resolved, unresolved = parse_dhan_csv(csv_bytes)
        assert len(resolved) == 0
        assert len(unresolved) == 1
        assert unresolved[0]['name'] == 'ZZZUNKNOWN Corp'
        assert unresolved[0]['row'] == 2  # row 1 = header

    def test_zero_quantity_row_skipped(self):
        from services.dhan_csv_importer import parse_dhan_csv
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None
        csv_bytes = _make_csv([
            {'Name': 'Tata Steel', 'Qty': '0', 'Avg': '200', 'LTP': '190',
             'Inv': '0', 'CV': '0'},
        ])
        resolved, unresolved = parse_dhan_csv(csv_bytes)
        assert len(resolved) == 0
        assert len(unresolved) == 0

    def test_field_values_parsed_correctly(self):
        from services.dhan_csv_importer import parse_dhan_csv
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None
        csv_bytes = _make_csv([
            {'Name': 'Tata Steel', 'Qty': '10', 'Avg': '204.29', 'LTP': '189.85',
             'Inv': '2042.90', 'CV': '1898.50'},
        ])
        resolved, _ = parse_dhan_csv(csv_bytes)
        r = resolved[0]
        assert r['quantity']       == 10.0
        assert r['purchase_price'] == 204.29
        assert r['current_price']  == 189.85
        assert r['purchased_value'] == 2042.90
        assert r['current_value']   == 1898.50

    def test_wrong_format_raises(self):
        import pytest
        from services.dhan_csv_importer import parse_dhan_csv
        bad_csv = io.BytesIO(b'Symbol,Qty,Price\nRELIANCE,10,2500\n')
        with pytest.raises(ValueError, match='does not look like a Dhan'):
            parse_dhan_csv(bad_csv)

    def test_mixed_resolved_and_unresolved(self):
        from services.dhan_csv_importer import parse_dhan_csv
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None
        # Use a multi-word name that cannot match the all-caps fallback regex
        csv_bytes = _make_csv([
            {'Name': 'Tata Steel',              'Qty': '5', 'Avg': '200', 'LTP': '190',
             'Inv': '1000', 'CV': '950'},
            {'Name': 'Some Unknown Firm 2024',  'Qty': '2', 'Avg': '100', 'LTP': '90',
             'Inv': '200',  'CV': '180'},
        ])
        resolved, unresolved = parse_dhan_csv(csv_bytes)
        assert len(resolved) == 1
        assert len(unresolved) == 1


# ── upsert_dhan_holdings (insert path) ───────────────────────────────────────

class TestUpsertDhanHoldings:
    """
    upsert_dhan_holdings() imports Portfolio and db lazily inside the function
    via `from models import Portfolio, db`.  We must therefore patch at the
    source: `models.Portfolio` and `models.db`.
    """

    def _rows(self):
        return [{
            'ticker_symbol':   'TATASTEEL',
            'stock_name':      'Tata Steel',
            'quantity':        10.0,
            'purchase_price':  204.29,
            'purchased_value': 2042.90,
            'current_price':   189.85,
            'current_value':   1898.50,
        }]

    def test_insert_new_holding(self):
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        mock_holding = MagicMock()
        mock_db      = MagicMock()
        MockPortfolio = MagicMock()
        MockPortfolio.query.filter_by.return_value.first.return_value = None
        MockPortfolio.return_value = mock_holding

        with patch.object(models_module, 'Portfolio', MockPortfolio), \
             patch.object(models_module, 'db', mock_db):
            inserted, updated = upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        assert inserted == 1
        assert updated == 0
        mock_db.session.add.assert_called_once_with(mock_holding)
        mock_db.session.commit.assert_called_once()

    def test_update_existing_holding(self):
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        existing = MagicMock()
        existing.quantity        = 5.0
        existing.purchase_price  = 200.0
        existing.purchased_value = 1000.0
        existing.current_price   = 190.0
        existing.current_value   = 950.0

        mock_db = MagicMock()
        MockPortfolio = MagicMock()
        MockPortfolio.query.filter_by.return_value.first.return_value = existing

        with patch.object(models_module, 'Portfolio', MockPortfolio), \
             patch.object(models_module, 'db', mock_db):
            inserted, updated = upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        assert inserted == 0
        assert updated == 1
        assert existing.quantity == 15.0          # 5 + 10
        mock_db.session.commit.assert_called_once()

    def test_rollback_on_commit_failure(self):
        import pytest
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        mock_db = MagicMock()
        mock_db.session.commit.side_effect = RuntimeError('db error')
        MockPortfolio = MagicMock()
        MockPortfolio.query.filter_by.return_value.first.return_value = None
        MockPortfolio.return_value = MagicMock()

        with patch.object(models_module, 'Portfolio', MockPortfolio), \
             patch.object(models_module, 'db', mock_db):
            with pytest.raises(RuntimeError, match='db error'):
                upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        mock_db.session.rollback.assert_called_once()
