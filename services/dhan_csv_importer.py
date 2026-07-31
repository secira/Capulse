"""
services/dhan_csv_importer.py
─────────────────────────────
Parse and import Dhan Holdings CSV exports into the Portfolio model.

Dhan CSV format (BOM-prefixed, all fields quoted):
  "Name","Quantity","Avg Price","Last Traded","Investment","Current Value","P&L","P&L %"

Usage:
  from services.dhan_csv_importer import parse_and_import_dhan_csv
  result = parse_and_import_dhan_csv(file_obj, user_id)
  # result = {'imported': int, 'updated': int, 'unresolved': [{'name': str, 'row': int}]}
"""

import csv
import io
import json
import logging
import os
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Alias map (lazy-loaded once) ─────────────────────────────────────────────
_ALIAS_MAP: Optional[Dict[str, str]] = None

# Noise tokens stripped before name lookup
_SUFFIX_NOISE = re.compile(
    r'\b(ltd|limited|pvt|private|corp|corporation|inc|industries|india|'
    r'enterprise|enterprises|holdings|group|co\.|co)\b',
    re.IGNORECASE,
)

# ── Expected Dhan CSV headers (lowercase, stripped) ──────────────────────────
_DHAN_HEADERS = {'name', 'quantity', 'avg price', 'last traded', 'investment', 'current value'}


def _load_alias_map() -> Dict[str, str]:
    """Load nse_aliases.json once and cache it."""
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    path = os.path.join(os.path.dirname(__file__), '..', 'static', 'data', 'nse_aliases.json')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        _ALIAS_MAP = {k.lower().strip(): v.upper() for k, v in raw.items()}
        logger.info(f"dhan_csv_importer: loaded {len(_ALIAS_MAP)} NSE aliases")
    except Exception as exc:
        logger.warning(f"dhan_csv_importer: could not load nse_aliases.json: {exc}")
        _ALIAS_MAP = {}
    return _ALIAS_MAP


def _strip_suffixes(name: str) -> str:
    """Remove common company suffixes that differ between Dhan display names and alias keys."""
    cleaned = _SUFFIX_NOISE.sub('', name)
    # Collapse multiple spaces
    return re.sub(r'\s+', ' ', cleaned).strip()


def resolve_name_to_symbol(name: str) -> Tuple[Optional[str], int]:
    """
    Resolve a Dhan display name to an NSE ticker symbol.

    Strategy (in priority order):
      1. Direct lowercase match in the alias map
      2. Suffix-stripped lowercase match
      3. Direct uppercase match (user already typed a symbol)

    Returns (symbol, confidence) where confidence is 0–100:
      100 = direct alias match
       80 = suffix-stripped alias match
       60 = direct uppercase match (assumed to be a valid symbol already)
        0 = no match → (None, 0)
    """
    alias = _load_alias_map()
    key = name.lower().strip()

    # 1. Direct match
    if key in alias:
        return alias[key], 100

    # 2. Suffix-stripped match
    stripped = _strip_suffixes(key).lower()
    if stripped and stripped in alias:
        return alias[stripped], 80

    # Partial word match — try progressively shorter prefixes of the stripped name
    # (e.g. "canara bank nifty" → try "canara bank" etc.)
    words = stripped.split()
    for length in range(len(words) - 1, 1, -1):
        candidate = ' '.join(words[:length])
        if candidate in alias:
            return alias[candidate], 70

    # 3. If it looks like an all-caps symbol already, accept it
    if re.match(r'^[A-Z][A-Z0-9&\-]{1,14}$', name.strip()):
        return name.strip(), 60

    return None, 0


def is_dhan_csv(header_row: List[str]) -> bool:
    """Return True if the header row matches the Dhan Holdings export format."""
    seen = {h.strip().lower() for h in header_row}
    return _DHAN_HEADERS.issubset(seen)


def parse_dhan_csv(file_obj) -> Tuple[List[dict], List[dict]]:
    """
    Parse a Dhan Holdings CSV file object.

    Returns:
      (resolved_rows, unresolved_rows)

    resolved_rows: list of dicts ready for Portfolio upsert:
      {ticker_symbol, stock_name, quantity, purchase_price, purchased_value,
       current_price, current_value}

    unresolved_rows: list of {name, row_number}
    """
    # Read bytes, strip BOM, decode
    raw = file_obj.read()
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8-sig')  # utf-8-sig strips BOM automatically
    else:
        raw = raw.lstrip('\ufeff')

    reader = csv.DictReader(io.StringIO(raw))

    # Normalise header names (strip whitespace and quotes)
    fieldnames = [f.strip().strip('"').lower() for f in (reader.fieldnames or [])]

    if not _DHAN_HEADERS.issubset(set(fieldnames)):
        raise ValueError(
            f"File does not look like a Dhan Holdings CSV. "
            f"Expected headers: {sorted(_DHAN_HEADERS)}. "
            f"Got: {sorted(fieldnames)}"
        )

    resolved: List[dict] = []
    unresolved: List[dict] = []

    for row_num, raw_row in enumerate(reader, start=2):  # row 1 is the header
        # Normalise keys
        row = {k.strip().strip('"').lower(): (v or '').strip().strip('"')
               for k, v in raw_row.items() if k}

        name = row.get('name', '').strip()
        if not name:
            continue

        # Skip any repeated header rows in the file
        if name.lower() == 'name':
            continue

        def _float(key: str, default: float = 0.0) -> float:
            try:
                return float(row.get(key, '').replace(',', '').replace('%', ''))
            except (ValueError, TypeError):
                return default

        quantity       = _float('quantity')
        purchase_price = _float('avg price')
        purchased_value = _float('investment')
        current_price  = _float('last traded')
        current_value  = _float('current value')

        if quantity <= 0:
            logger.debug(f"dhan_csv: skipping row {row_num} — zero quantity")
            continue

        symbol, confidence = resolve_name_to_symbol(name)

        if symbol is None:
            unresolved.append({'name': name, 'row': row_num})
            logger.warning(f"dhan_csv: could not resolve '{name}' (row {row_num})")
            continue

        logger.info(
            f"dhan_csv: resolved '{name}' → {symbol} "
            f"(confidence={confidence}, row={row_num})"
        )

        total_inv = purchased_value if purchased_value > 0 else round(quantity * purchase_price, 2)
        resolved.append({
            'symbol':            symbol,
            'company_name':      name,          # original Dhan display name
            'quantity':          quantity,
            'purchase_price':    purchase_price,
            'total_investment':  total_inv,
            'current_price':     current_price,
            'current_value':     current_value if current_value > 0 else round(quantity * current_price, 2),
        })

    return resolved, unresolved


DHAN_PORTFOLIO_NAME = 'Dhan Import'


def deactivate_removed_holdings(user_id: int, current_symbols: set) -> int:
    """
    Deactivate any active 'Dhan Import' holdings whose symbols are absent from
    the latest snapshot (i.e. the user has sold those positions since the last
    import).

    Must be called within the same DB session as upsert_dhan_holdings so both
    operations commit atomically.

    Returns the number of records deactivated.
    """
    from models import ManualEquityHolding

    active_dhan = ManualEquityHolding.query.filter_by(
        user_id       = user_id,
        portfolio_name= DHAN_PORTFOLIO_NAME,
        is_active     = True,
    ).all()

    deactivated = 0
    for holding in active_dhan:
        if holding.symbol not in current_symbols:
            holding.is_active = False
            deactivated += 1
            logger.info(
                f"dhan_csv: deactivated removed holding {holding.symbol} for user {user_id}"
            )
    return deactivated


def upsert_dhan_holdings(user_id: int, resolved_rows: List[dict]) -> Tuple[int, int]:
    """
    Upsert resolved rows into the ManualEquityHolding table for the given user.
    This is the model consumed by dashboard_equities — ManualEquityHolding
    (table: manual_equity_holdings).

    Snapshot semantics (idempotent):
      A Dhan Holdings export represents the broker's *current aggregate position*,
      not an incremental transaction.  Re-uploading the same file (or an updated
      snapshot) must produce the same result — it must NOT accumulate quantities.

    Duplicate detection:
      Same user_id + symbol + portfolio_name='Dhan Import' + is_active=True.
      Scoping to portfolio_name avoids touching manually-entered holdings for the
      same symbol.

    On match:  overwrite quantity, purchase_price, total_investment, current_price
               with the CSV values and recompute totals.
    On miss:   insert a new BUY record tagged portfolio_name='Dhan Import'.

    Returns (inserted_count, updated_count).
    """
    from models import ManualEquityHolding, db

    today    = date.today()
    inserted = 0
    updated  = 0

    for row in resolved_rows:
        symbol = row['symbol']

        # Scope match to Dhan-imported records only — never touch manual holdings
        existing = ManualEquityHolding.query.filter_by(
            user_id       = user_id,
            symbol        = symbol,
            portfolio_name= DHAN_PORTFOLIO_NAME,
            is_active     = True,
        ).first()

        if existing:
            # Snapshot replace — set to CSV values, do NOT add
            existing.quantity         = row['quantity']
            existing.purchase_price   = row['purchase_price']
            existing.total_investment = row['total_investment']
            existing.current_price    = row['current_price'] or existing.current_price
            existing.company_name     = row['company_name']
            existing.calculate_totals()
            updated += 1
            logger.info(f"dhan_csv: snapshot-updated ManualEquityHolding {symbol} for user {user_id}")
        else:
            holding = ManualEquityHolding(
                user_id          = user_id,
                symbol           = symbol,
                company_name     = row['company_name'],
                transaction_type = 'BUY',
                purchase_date    = today,        # Dhan snapshot has no purchase date
                quantity         = row['quantity'],
                purchase_price   = row['purchase_price'],
                current_price    = row['current_price'],
                portfolio_name   = DHAN_PORTFOLIO_NAME,
                notes            = 'Imported from Dhan Holdings CSV',
                is_active        = True,
            )
            holding.calculate_totals()
            db.session.add(holding)
            inserted += 1
            logger.info(f"dhan_csv: inserted new ManualEquityHolding {symbol} for user {user_id}")

    # NOTE: caller (parse_and_import_dhan_csv) owns the commit so that
    # upsert + deactivate are a single atomic transaction.
    return inserted, updated


def parse_and_import_dhan_csv(file_obj, user_id: int) -> dict:
    """
    High-level entry point: parse a Dhan Holdings CSV and sync into the DB.

    Implements full snapshot semantics within a single transaction:
      1. Upsert (insert / snapshot-replace) all resolved symbols.
      2. Deactivate any previously-imported Dhan holding whose symbol is absent
         from this snapshot — i.e. the user has sold that position.
    The deactivation runs inside the same DB session so both steps commit or
    roll back atomically.

    Returns:
      {
        'imported':    int,              # new rows inserted
        'updated':     int,              # existing rows snapshot-replaced
        'deactivated': int,              # removed positions deactivated
        'unresolved':  [{'name', 'row'}],# names that couldn't be mapped
        'total_rows':  int,
      }
    """
    resolved, unresolved = parse_dhan_csv(file_obj)

    # An empty resolved+unresolved list means the Dhan export contained no
    # position rows (header-only or every row had zero quantity).  This is a
    # valid authoritative empty snapshot — the user has sold everything.
    # We do NOT raise here; we fall through to reconciliation which will
    # deactivate all prior Dhan Import holdings.

    # upsert leaves DB session open (no commit yet inside upsert)
    inserted, updated = upsert_dhan_holdings(user_id, resolved)

    # Reconcile removed positions only when every row was resolved.
    # If any names are unresolved the snapshot is incomplete — we cannot safely
    # distinguish "sold" from "unmapped"; skipping deactivation preserves data
    # integrity and the caller surfaces a warning to the user.
    deactivated          = 0
    reconciliation_skipped = False
    if unresolved:
        reconciliation_skipped = True
        logger.info(
            f"dhan_csv: skipping position reconciliation for user {user_id} — "
            f"{len(unresolved)} unresolved row(s) mean the snapshot is incomplete"
        )
    else:
        current_symbols = {r['symbol'] for r in resolved}
        deactivated = deactivate_removed_holdings(user_id, current_symbols)

    # Commit once for both operations
    from models import db
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise

    return {
        'imported':               inserted,
        'updated':                updated,
        'deactivated':            deactivated,
        'unresolved':             unresolved,
        'total_rows':             len(resolved) + len(unresolved),
        'reconciliation_skipped': reconciliation_skipped,
    }
