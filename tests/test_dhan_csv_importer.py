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
        from services.dhan_csv_importer import parse_dhan_csv
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
        symbols = {r['symbol'] for r in resolved}
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
        assert resolved[0]['symbol'] == 'TATASTEEL'

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
        assert r['quantity']          == 10.0
        assert r['purchase_price']    == 204.29
        assert r['current_price']     == 189.85
        assert r['total_investment']  == 2042.90
        assert r['current_value']     == 1898.50
        # Field names must match ManualEquityHolding
        assert 'symbol'       in r
        assert 'company_name' in r
        assert 'symbol'       == 'symbol'   # not 'ticker_symbol'

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
    upsert_dhan_holdings() imports ManualEquityHolding and db lazily inside the
    function via `from models import ManualEquityHolding, db`.  Patch at the source:
    `models.ManualEquityHolding` and `models.db`.

    ManualEquityHolding is the model consumed by dashboard_equities — verifying
    that inserts go to this table ensures imported holdings are visible.
    """

    def _rows(self):
        return [{
            'symbol':           'TATASTEEL',
            'company_name':     'Tata Steel',
            'quantity':         10.0,
            'purchase_price':   204.29,
            'total_investment': 2042.90,
            'current_price':    189.85,
            'current_value':    1898.50,
        }]

    def _mock_holding(self):
        h = MagicMock()
        h.quantity         = 0.0
        h.purchase_price   = 0.0
        h.total_investment = 0.0
        h.current_price    = 0.0
        h.calculate_totals = MagicMock()
        return h

    def test_insert_new_holding(self):
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        mock_holding = self._mock_holding()
        mock_db      = MagicMock()
        MockMEH      = MagicMock()
        MockMEH.query.filter_by.return_value.first.return_value = None
        MockMEH.return_value = mock_holding

        with patch.object(models_module, 'ManualEquityHolding', MockMEH), \
             patch.object(models_module, 'db', mock_db):
            inserted, updated = upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        assert inserted == 1
        assert updated == 0
        mock_db.session.add.assert_called_once_with(mock_holding)
        # commit is owned by parse_and_import_dhan_csv, not upsert_dhan_holdings
        mock_db.session.commit.assert_not_called()
        # Verify it's going to ManualEquityHolding, not Portfolio
        MockMEH.assert_called_once()

    def test_update_existing_holding_snapshot_replaces_not_accumulates(self):
        """
        Key idempotency test: re-importing the same CSV must overwrite existing
        values, NOT add them. A snapshot import uploaded twice must not double
        the quantity or investment.
        """
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        existing = self._mock_holding()
        existing.quantity         = 10.0   # same values as the CSV row
        existing.purchase_price   = 204.29
        existing.total_investment = 2042.90
        existing.current_price    = 189.85

        mock_db = MagicMock()
        MockMEH = MagicMock()
        MockMEH.query.filter_by.return_value.first.return_value = existing

        with patch.object(models_module, 'ManualEquityHolding', MockMEH), \
             patch.object(models_module, 'db', mock_db):
            inserted, updated = upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        assert inserted == 0
        assert updated == 1
        # Quantity must equal the CSV value — NOT doubled
        assert existing.quantity == 10.0
        assert existing.purchase_price == 204.29
        assert existing.total_investment == 2042.90
        existing.calculate_totals.assert_called_once()
        mock_db.session.commit.assert_not_called()  # commit owned by parse_and_import_dhan_csv

    def test_update_changed_snapshot_takes_latest_values(self):
        """
        When the broker updates the position (e.g. price changed, more shares bought),
        the re-import should take the new CSV values — not average them in.
        """
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        existing = self._mock_holding()
        existing.quantity         = 8.0     # old value, different from CSV row (10)
        existing.purchase_price   = 210.0
        existing.total_investment = 1680.0
        existing.current_price    = 195.0

        mock_db = MagicMock()
        MockMEH = MagicMock()
        MockMEH.query.filter_by.return_value.first.return_value = existing

        with patch.object(models_module, 'ManualEquityHolding', MockMEH), \
             patch.object(models_module, 'db', mock_db):
            inserted, updated = upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        assert updated == 1
        # Should take CSV values, not old or combined
        assert existing.quantity         == 10.0
        assert existing.purchase_price   == 204.29
        assert existing.total_investment == 2042.90
        assert existing.current_price    == 189.85

    def test_rollback_on_commit_failure(self):
        """Rollback test at parse_and_import_dhan_csv level (commit lives there)."""
        import pytest, io
        from services.dhan_csv_importer import parse_and_import_dhan_csv
        import models as models_module
        import services.dhan_csv_importer as m
        m._ALIAS_MAP = None

        mock_db = MagicMock()
        mock_db.session.commit.side_effect = RuntimeError('db error')

        MockMEH = MagicMock()
        MockMEH.query.filter_by.return_value.first.return_value = None
        MockMEH.query.filter_by.return_value.all.return_value = []
        MockMEH.return_value = self._mock_holding()

        csv_bytes = _make_csv([
            {'Name': 'Tata Steel', 'Qty': '10', 'Avg': '204.29', 'LTP': '189.85',
             'Inv': '2042.90', 'CV': '1898.50'},
        ])

        with patch.object(models_module, 'ManualEquityHolding', MockMEH), \
             patch.object(models_module, 'db', mock_db):
            with pytest.raises(RuntimeError, match='db error'):
                parse_and_import_dhan_csv(csv_bytes, user_id=1)

        mock_db.session.rollback.assert_called_once()

    def test_imported_holdings_use_manual_equity_model(self):
        """
        Critical: confirm that the upsert writes to ManualEquityHolding —
        the model consumed by dashboard_equities — not Portfolio.
        If this test passes, imported holdings will be visible in the equities view.
        """
        from services.dhan_csv_importer import upsert_dhan_holdings
        import models as models_module

        mock_db  = MagicMock()
        MockMEH  = MagicMock()
        MockMEH.query.filter_by.return_value.first.return_value = None
        MockMEH.return_value = self._mock_holding()

        with patch.object(models_module, 'ManualEquityHolding', MockMEH), \
             patch.object(models_module, 'db', mock_db):
            upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        # ManualEquityHolding constructor was called → record goes to the right table
        MockMEH.assert_called_once()
        call_kwargs = MockMEH.call_args[1]
        assert call_kwargs.get('symbol') == 'TATASTEEL'
        assert call_kwargs.get('transaction_type') == 'BUY'
        assert call_kwargs.get('portfolio_name') == 'Dhan Import'
        assert call_kwargs.get('is_active') is True

    def test_deactivate_removed_holding(self):
        """
        Full snapshot reconciliation: a symbol present in the previous import
        but absent from the new CSV must be deactivated — not left showing.
        Manual holdings for the same symbol must not be touched.
        """
        from services.dhan_csv_importer import deactivate_removed_holdings, DHAN_PORTFOLIO_NAME
        import models as models_module

        # Two active Dhan Import holdings: TATASTEEL (still in snapshot), RELIANCE (sold)
        h1 = MagicMock(); h1.symbol = 'TATASTEEL'; h1.is_active = True
        h2 = MagicMock(); h2.symbol = 'RELIANCE';  h2.is_active = True

        MockMEH = MagicMock()
        MockMEH.query.filter_by.return_value.all.return_value = [h1, h2]

        current_symbols = {'TATASTEEL'}   # RELIANCE is gone from new snapshot

        with patch.object(models_module, 'ManualEquityHolding', MockMEH):
            count = deactivate_removed_holdings(user_id=1, current_symbols=current_symbols)

        assert count == 1
        assert h1.is_active is True    # still in snapshot — untouched
        assert h2.is_active is False   # removed from snapshot — deactivated

    def test_deactivate_none_when_all_present(self):
        from services.dhan_csv_importer import deactivate_removed_holdings
        import models as models_module

        h1 = MagicMock(); h1.symbol = 'TATASTEEL'; h1.is_active = True
        MockMEH = MagicMock()
        MockMEH.query.filter_by.return_value.all.return_value = [h1]

        with patch.object(models_module, 'ManualEquityHolding', MockMEH):
            count = deactivate_removed_holdings(user_id=1, current_symbols={'TATASTEEL'})

        assert count == 0
        assert h1.is_active is True

    def test_dhan_import_scoped_to_dhan_portfolio_name(self):
        """
        Duplicate detection must scope to portfolio_name='Dhan Import' only —
        a manually-entered holding for the same symbol must not be touched.
        """
        from services.dhan_csv_importer import upsert_dhan_holdings, DHAN_PORTFOLIO_NAME
        import models as models_module

        # Simulate: no Dhan Import record found (manual record exists but won't match)
        mock_db  = MagicMock()
        MockMEH  = MagicMock()
        MockMEH.query.filter_by.return_value.first.return_value = None
        new_holding = self._mock_holding()
        MockMEH.return_value = new_holding

        with patch.object(models_module, 'ManualEquityHolding', MockMEH), \
             patch.object(models_module, 'db', mock_db):
            inserted, updated = upsert_dhan_holdings(user_id=1, resolved_rows=self._rows())

        # The filter_by call must include portfolio_name scoping
        filter_call_kwargs = MockMEH.query.filter_by.call_args[1]
        assert filter_call_kwargs.get('portfolio_name') == DHAN_PORTFOLIO_NAME
        assert inserted == 1
