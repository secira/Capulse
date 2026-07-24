"""
Behavioural AI Routes — Capulse
Serves the Behavioural Insights dashboard, sub-pages, and pre-trade check API.
"""
from flask import render_template, request, jsonify, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from flask_limiter.util import get_remote_address
from app import app, db, limiter
from decorators import paid_plan_required
import logging
import csv
import io
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _get_engine():
    from services.behaviour_engine import BehaviourEngine
    return BehaviourEngine(current_user.id, current_user.tenant_id or 'live')


@app.route('/dashboard/behavioural-insights')
@login_required
@paid_plan_required
def behavioural_insights():
    try:
        engine = _get_engine()
        analysis = engine.get_full_analysis()
    except Exception as e:
        logger.error(f"Behavioural analysis error: {e}")
        analysis = None

    from models_broker import BrokerAccount
    broker_accounts = BrokerAccount.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()

    return render_template(
        'dashboard/behaviour/overview.html',
        analysis=analysis,
        page_title='Behavioural Insights',
        broker_accounts=broker_accounts,
    )


@app.route('/dashboard/behavioural-insights/trading')
@login_required
@paid_plan_required
def behavioural_trading():
    try:
        engine = _get_engine()
        data = engine.get_trading_behavior()
        stats = {
            'total_trades': len(engine._get_trades()),
            'by_hour': engine.get_win_rate_by_hour(),
        }
    except Exception as e:
        logger.error(f"Trading behavior error: {e}")
        data = None
        stats = {}

    return render_template(
        'dashboard/behaviour/trading.html',
        data=data, stats=stats,
        page_title='Trading Behavior',
    )


@app.route('/dashboard/behavioural-insights/risk')
@login_required
@paid_plan_required
def behavioural_risk():
    try:
        engine = _get_engine()
        data = engine.get_risk_behavior()
    except Exception as e:
        logger.error(f"Risk behavior error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/risk.html',
        data=data,
        page_title='Risk Analysis',
    )


@app.route('/dashboard/behavioural-insights/portfolio')
@login_required
@paid_plan_required
def behavioural_portfolio():
    try:
        engine = _get_engine()
        data = engine.get_portfolio_behavior()
        intel = engine.get_portfolio_intelligence()
    except Exception as e:
        logger.error(f"Portfolio behavior error: {e}")
        data = None
        intel = None

    return render_template(
        'dashboard/behaviour/portfolio.html',
        data=data,
        intel=intel,
        page_title='Portfolio Behavior',
    )


@app.route('/dashboard/behavioural-insights/performance')
@login_required
@paid_plan_required
def behavioural_performance():
    try:
        engine = _get_engine()
        data = engine.get_performance_patterns()
        root_cause = engine.get_performance_root_cause()
    except Exception as e:
        logger.error(f"Performance patterns error: {e}")
        data = None
        root_cause = None

    return render_template(
        'dashboard/behaviour/performance.html',
        data=data,
        root_cause=root_cause,
        page_title='Performance Patterns',
    )


@app.route('/dashboard/behavioural-insights/psychology')
@login_required
@paid_plan_required
def behavioural_psychology():
    try:
        engine = _get_engine()
        data = engine.get_psychology_patterns()
        narratives = engine.get_psychology_narratives()
    except Exception as e:
        logger.error(f"Psychology patterns error: {e}")
        data = None
        narratives = {}

    return render_template(
        'dashboard/behaviour/psychology.html',
        data=data,
        narratives=narratives,
        page_title='Psychological Patterns',
    )


MONTH_MAP = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}


def _parse_date_any(s):
    """Parse date strings in many common formats."""
    s = s.strip()
    FMTS = [
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
        '%d-%m-%Y', '%d %b %Y', '%d %B %Y',
    ]
    for fmt in FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f'Unrecognised date: {s!r}')


def _parse_scrip_name(scrip):
    """
    Parse Dhan/broker scrip names into (symbol, asset_type, detail, expiry_dt).
    Examples:
      "OPT NIFTY 07 Apr 2026 22700 CE" → NIFTY22700CE, OPTION, 07-Apr-2026
      "OPT SENSEX 02 Apr 2026 71600 CE" → SENSEX71600CE, OPTION
      "FUT NIFTY 24 Apr 2026"           → NIFTY, FUTURES
      "RELIANCE"                        → RELIANCE, STOCK
    """
    import re
    scrip = scrip.strip().strip('"')

    # OPTIONS: OPT <UNDERLYING> <DD> <Mon> <YYYY> <STRIKE> <CE|PE>
    m = re.match(
        r'^OPT\s+(\w+)\s+(\d{1,2})\s+(\w{3})\s+(\d{4})\s+([\d.]+)\s+(CE|PE)$',
        scrip, re.IGNORECASE
    )
    if m:
        underlying, day, mon, year, strike, opt_type = m.groups()
        try:
            expiry_dt = datetime(int(year), MONTH_MAP.get(mon, 1), int(day))
        except Exception:
            expiry_dt = None
        symbol = f"{underlying.upper()}{int(float(strike))}{opt_type.upper()}"
        detail = f"{underlying.upper()} {opt_type.upper()} ₹{int(float(strike))} {day}{mon}{year}"
        return symbol, 'OPTION', detail, expiry_dt

    # OPTIONS (Dhan Trade History): <UNDERLYING> <DD> <Mon> <STRIKE> <CALL|PUT>
    # e.g. "NIFTY 19 MAY 23650 PUT"  (no year, PUT/CALL spelled out)
    m = re.match(
        r'^(\w+)\s+(\d{1,2})\s+(\w{3})\s+([\d.]+)\s+(CALL|PUT|CE|PE)$',
        scrip, re.IGNORECASE
    )
    if m:
        underlying, day, mon, strike, opt_type = m.groups()
        opt_type_u = opt_type.upper()
        short = 'CE' if opt_type_u in ('CALL', 'CE') else 'PE'
        # Year not present — assume current year, roll forward if expiry already past
        try:
            now = datetime.utcnow()
            expiry_dt = datetime(now.year, MONTH_MAP.get(mon.capitalize(), now.month), int(day))
            if expiry_dt < now - timedelta(days=180):
                expiry_dt = expiry_dt.replace(year=now.year + 1)
        except Exception:
            expiry_dt = None
        symbol = f"{underlying.upper()}{int(float(strike))}{short}"
        year_str = expiry_dt.strftime('%Y') if expiry_dt else ''
        detail = f"{underlying.upper()} {short} ₹{int(float(strike))} {day}{mon.capitalize()}{year_str}"
        return symbol, 'OPTION', detail, expiry_dt

    # FUTURES: FUT <UNDERLYING> <DD> <Mon> <YYYY>
    m = re.match(r'^FUT\s+(\w+)\s+(\d{1,2})\s+(\w{3})\s+(\d{4})$', scrip, re.IGNORECASE)
    if m:
        underlying, day, mon, year = m.groups()
        try:
            expiry_dt = datetime(int(year), MONTH_MAP.get(mon, 1), int(day))
        except Exception:
            expiry_dt = None
        symbol = f"{underlying.upper()}FUT"
        detail = f"{underlying.upper()} Futures {day}{mon}{year}"
        return symbol, 'FUTURES', detail, expiry_dt

    # MUTUAL FUNDS: starts with MF or ETF
    if re.match(r'^(MF|ETF)\s+', scrip, re.IGNORECASE):
        symbol = re.sub(r'^(MF|ETF)\s+', '', scrip, flags=re.IGNORECASE).strip()
        return symbol.upper()[:50], 'MF', scrip, None

    # STOCK: plain name
    clean = re.sub(r'[^\w\s-]', '', scrip).strip().replace(' ', '-')
    return clean.upper()[:50], 'STOCK', scrip, None


def _detect_format(content):
    """Return 'dhan_pnl', 'dhan_trades', 'zerodha_trades', 'zerodha_pnl', or 'generic'."""
    head = content[:1500].lower()
    # Dhan P&L export
    if 'pnl report' in head or 'scrip name' in head:
        return 'dhan_pnl'
    # Dhan Trade History export — header has these exact columns
    if 'buy/sell' in head and 'trade price' in head and 'quantity/lot' in head:
        return 'dhan_trades'
    # Zerodha Console tradewise P&L (round-trip rows) — has 'buy avg' or 'sell avg'
    if 'tradingsymbol' in head and ('buy avg' in head or 'sell avg' in head):
        return 'zerodha_pnl'
    # Zerodha Kite / Console trade-by-trade (individual legs)
    if 'tradingsymbol' in head and ('trade_type' in head or 'trade type' in head):
        return 'zerodha_trades'
    return 'generic'


def _parse_dhan_trade_history(content):
    """
    Parse Dhan Trade History CSV (individual BUY/SELL legs) into round-trip trades.

    Columns: Date,Time,Name,Buy/Sell,Order,Exchange,Segment,Quantity/Lot,Trade Price,Trade Value,Status

    Strategy: group rows by symbol Name, sort chronologically, then FIFO-match
    BUY legs against SELL legs to produce one round-trip trade per matched pair.
    Open positions (unmatched legs) are skipped.
    """
    from collections import defaultdict, deque

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError('CSV appears empty.')

    # Validate it's actually Dhan Trade History
    headers_lc = {h.strip().lower() for h in reader.fieldnames}
    needed = {'date', 'time', 'name', 'buy/sell', 'quantity/lot', 'trade price'}
    if not needed.issubset(headers_lc):
        raise ValueError(
            'Could not find Dhan Trade History columns. Expected: Date, Time, Name, '
            'Buy/Sell, Quantity/Lot, Trade Price.'
        )

    # Bucket legs per symbol
    legs_by_symbol = defaultdict(list)
    for row in reader:
        row = {k.strip(): (v or '').strip() for k, v in row.items() if k}
        status = row.get('Status', '').strip().lower()
        if status and status != 'traded':
            continue  # skip cancelled / rejected
        name = row.get('Name', '').strip()
        side = row.get('Buy/Sell', '').strip().upper()
        if not name or side not in ('BUY', 'SELL'):
            continue
        try:
            dt_str = f"{row.get('Date','').strip()} {row.get('Time','').strip()}".strip()
            ts = _parse_date_any(dt_str)
            qty = int(float(row.get('Quantity/Lot', '0') or '0'))
            price = float((row.get('Trade Price', '0') or '0').replace(',', ''))
            if qty <= 0 or price <= 0:
                continue
        except Exception as e:
            logger.warning(f'Dhan trade-history row skipped (parse): {e} :: {row}')
            continue
        legs_by_symbol[name].append({
            'ts': ts, 'side': side, 'qty': qty, 'price': price,
            'segment': row.get('Segment', '').strip(),
            'exchange': row.get('Exchange', 'NSE').strip(),
        })

    trades = []
    for name, legs in legs_by_symbol.items():
        legs.sort(key=lambda x: x['ts'])
        # Parse symbol once
        try:
            symbol, asset_type, detail, expiry_dt = _parse_scrip_name(name)
        except Exception:
            symbol, asset_type, detail, expiry_dt = name.upper()[:50], 'STOCK', name, None
        # If parser fell back to STOCK but segment says Derivative, treat as OPTION
        # so downstream behavioural modules apply F&O rules.
        seg_lc = (legs[0].get('segment') or '').lower()
        if asset_type == 'STOCK' and 'deriv' in seg_lc:
            asset_type = 'OPTION'

        # FIFO matching: two queues — opens (long buys waiting for sell), shorts (sells waiting for buy-back)
        opens = deque()   # holds {'ts','qty','price'} BUY legs awaiting SELL
        shorts = deque()  # holds {'ts','qty','price'} SELL legs awaiting BUY (short cover)

        for leg in legs:
            qty_remaining = leg['qty']
            if leg['side'] == 'BUY':
                # First close any outstanding shorts (cover)
                while qty_remaining > 0 and shorts:
                    s = shorts[0]
                    match_qty = min(qty_remaining, s['qty'])
                    # Closed short trade: entry=SELL price, exit=BUY price
                    trades.append(_build_round_trip(
                        symbol, asset_type, detail, expiry_dt,
                        entry_ts=s['ts'], entry_price=s['price'],
                        exit_ts=leg['ts'], exit_price=leg['price'],
                        qty=match_qty, direction='SHORT',
                    ))
                    s['qty'] -= match_qty
                    qty_remaining -= match_qty
                    if s['qty'] == 0:
                        shorts.popleft()
                if qty_remaining > 0:
                    opens.append({'ts': leg['ts'], 'qty': qty_remaining, 'price': leg['price']})
            else:  # SELL
                # First close any outstanding longs
                while qty_remaining > 0 and opens:
                    o = opens[0]
                    match_qty = min(qty_remaining, o['qty'])
                    trades.append(_build_round_trip(
                        symbol, asset_type, detail, expiry_dt,
                        entry_ts=o['ts'], entry_price=o['price'],
                        exit_ts=leg['ts'], exit_price=leg['price'],
                        qty=match_qty, direction='LONG',
                    ))
                    o['qty'] -= match_qty
                    qty_remaining -= match_qty
                    if o['qty'] == 0:
                        opens.popleft()
                if qty_remaining > 0:
                    shorts.append({'ts': leg['ts'], 'qty': qty_remaining, 'price': leg['price']})

        # Unmatched legs remain open positions — silently skipped (no round trip yet)

    return trades


def _build_round_trip(symbol, asset_type, detail, expiry_dt,
                       entry_ts, entry_price, exit_ts, exit_price, qty, direction):
    """Build a ManualTradeImport dict from a matched BUY/SELL pair."""
    if direction == 'LONG':
        realized_pnl = (exit_price - entry_price) * qty
    else:  # SHORT
        realized_pnl = (entry_price - exit_price) * qty
    pnl_pct = round((realized_pnl / (entry_price * qty)) * 100, 2) if entry_price and qty else 0.0
    hold_hrs = max(0.0, (exit_ts - entry_ts).total_seconds() / 3600)
    result = 'WIN' if realized_pnl > 0 else ('LOSS' if realized_pnl < 0 else 'BREAKEVEN')
    return {
        'symbol': symbol,
        'asset_type': asset_type,
        'instrument_detail': detail,
        'quantity': qty,
        'entry_price': round(entry_price, 4),
        'exit_price': round(exit_price, 4),
        'realized_pnl': round(realized_pnl, 2),
        'pnl_percentage': pnl_pct,
        'holding_period_hours': round(hold_hrs, 2),
        'trade_result': result,
        'exit_reason': 'MANUAL',
        'broker_name': 'Dhan',
        'total_charges': 0.0,
        'net_pnl': round(realized_pnl, 2),
        'entry_time': entry_ts,
        'exit_time': exit_ts,
        'strategy_name': f'{asset_type} {direction}',
        'source': 'dhan_trades',
    }


def _parse_dhan_pnl(content):
    """
    Parse Dhan P&L CSV.
    Returns list of dicts with our internal trade fields.
    """
    import re
    lines = content.splitlines()

    # Extract report date range from line 1 "PnL report,From DD-MM-YYYY to DD-MM-YYYY"
    report_start = None
    report_end = None
    m = re.search(r'From\s+(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})', lines[0], re.IGNORECASE)
    if m:
        try:
            report_start = datetime.strptime(m.group(1), '%d-%m-%Y').replace(hour=9, minute=15)
            report_end = datetime.strptime(m.group(2), '%d-%m-%Y').replace(hour=15, minute=30)
        except Exception:
            pass
    if not report_start:
        report_start = datetime.utcnow().replace(hour=9, minute=15)
    if not report_end:
        report_end = datetime.utcnow().replace(hour=15, minute=30)

    # Find the actual data header row (contains "Scrip Name")
    header_idx = None
    for i, line in enumerate(lines):
        if 'scrip name' in line.lower() and 'buy qty' in line.lower():
            header_idx = i
            break

    if header_idx is None:
        raise ValueError('Could not find data header row in Dhan CSV. Make sure this is a Dhan P&L export.')

    # Parse CSV from header row onward
    data_block = '\n'.join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(data_block))

    trades = []
    for row in reader:
        row = {k.strip().strip('"'): (v or '').strip().strip('"') for k, v in row.items() if k}
        scrip = row.get('Scrip Name', '').strip()
        if not scrip or 'net p&l' in scrip.lower() or 'brokerage' in scrip.lower():
            continue  # skip summary rows

        try:
            buy_qty = int(float(row.get('Buy Qty.', '0') or '0'))
            sell_qty = int(float(row.get('Sell Qty.', '0') or '0'))
            qty = buy_qty or sell_qty
            if qty == 0:
                continue

            avg_buy = float(row.get('Avg. Buy Price', '0') or '0')
            avg_sell = float(row.get('Avg. Sell Price', '0') or '0')

            # If one side is 0 (e.g. short open), use the other as entry
            entry_price = avg_buy if avg_buy > 0 else avg_sell
            exit_price = avg_sell if avg_sell > 0 else avg_buy

            pnl_str = row.get('Realised P&L', '0').replace(',', '') or '0'
            realized_pnl = float(pnl_str)

            # Parse scrip name
            symbol, asset_type, detail, expiry_dt = _parse_scrip_name(scrip)

            # Use expiry as exit time for options/futures; else report_end
            exit_time = expiry_dt if expiry_dt else report_end
            entry_time = report_start

            # pnl_pct: use reported % if available, else compute
            pnl_pct_str = row.get('Realised P&L %', '0').replace(',', '') or '0'
            try:
                pnl_pct = float(pnl_pct_str)
            except Exception:
                pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0

            hold_hrs = max(0.0, (exit_time - entry_time).total_seconds() / 3600)
            result = 'WIN' if realized_pnl > 0 else ('LOSS' if realized_pnl < 0 else 'BREAKEVEN')

            trades.append({
                'symbol': symbol,
                'asset_type': asset_type,
                'instrument_detail': detail,
                'quantity': qty,
                'entry_price': round(entry_price, 4),
                'exit_price': round(exit_price, 4),
                'realized_pnl': round(realized_pnl, 2),
                'pnl_percentage': round(pnl_pct, 2),
                'holding_period_hours': round(hold_hrs, 2),
                'trade_result': result,
                'exit_reason': 'EXPIRY' if asset_type in ('OPTION', 'FUTURES') else 'MANUAL',
                'broker_name': 'Dhan',
                'total_charges': 0.0,
                'net_pnl': round(realized_pnl, 2),
                'entry_time': entry_time,
                'exit_time': exit_time,
                'strategy_name': asset_type,
                'source': 'dhan_pnl',
            })
        except Exception as e:
            logger.warning(f'Dhan parse error for row {scrip!r}: {e}')
            continue

    return trades


def _parse_zerodha_trades(content):
    """
    Parse Zerodha Kite / Console trade-by-trade CSV (individual BUY/SELL legs).

    Expected columns (case-insensitive):
      tradingsymbol, trade_type / trade type, quantity, price,
      trade_date / trade date / order_execution_time, trade_id / order_id

    Strategy: FIFO-match BUY against SELL legs per symbol — same as Dhan Trade History.
    Open positions (unmatched legs) are silently skipped.
    """
    from collections import defaultdict, deque
    import re as _re

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError('CSV appears empty.')

    # Normalise header names (strip whitespace, lowercase, replace space→underscore)
    raw_headers = reader.fieldnames
    norm = {h.strip().lower().replace(' ', '_'): h for h in raw_headers}

    needed = {'tradingsymbol'}
    if not needed.issubset(norm.keys()):
        raise ValueError(
            'Could not find Zerodha trade columns. Expected at minimum: tradingsymbol, '
            'trade_type / trade type, quantity, price.'
        )

    def _col(row, *keys):
        """Return first matching key value from normalised row."""
        for k in keys:
            v = row.get(k, '').strip()
            if v:
                return v
        return ''

    legs_by_symbol = defaultdict(list)
    for row in reader:
        row_norm = {k.strip().lower().replace(' ', '_'): v.strip() for k, v in row.items() if k}
        sym = row_norm.get('tradingsymbol', '').strip().upper()
        side = _col(row_norm, 'trade_type', 'tradetype').upper()
        if side not in ('BUY', 'SELL') or not sym:
            continue
        try:
            qty = float(_col(row_norm, 'quantity') or '0')
            price = float((_col(row_norm, 'price') or '0').replace(',', ''))
            if qty <= 0 or price <= 0:
                continue
        except (ValueError, TypeError):
            continue

        # Parse timestamp — try several column name variants
        ts_raw = _col(row_norm, 'trade_date', 'order_execution_time', 'date', 'time')
        ts = _parse_date_any(ts_raw) if ts_raw else datetime.utcnow()

        tid = _col(row_norm, 'trade_id', 'order_id', 'tradeid', 'orderid') or ''

        # Classify asset type from symbol (F&O option/futures patterns)
        if _re.search(r'(CE|PE)\d*$', sym) or _re.search(
                r'\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2,4}', sym, _re.I):
            asset_type = 'OPTION'
        elif sym.endswith('FUT') or 'FUT' in sym:
            asset_type = 'FUTURES'
        else:
            asset_type = 'STOCK'

        legs_by_symbol[sym].append({
            'ts': ts, 'side': side, 'qty': qty, 'price': price,
            'tid': tid, 'asset_type': asset_type,
        })

    trades = []
    for sym, legs in legs_by_symbol.items():
        legs.sort(key=lambda x: x['ts'])
        asset_type = legs[0]['asset_type']
        try:
            parsed_sym, det_asset, detail, expiry_dt = _parse_scrip_name(sym)
            if det_asset != 'STOCK':
                asset_type = det_asset
        except Exception:
            parsed_sym, detail, expiry_dt = sym, sym, None

        opens = deque()
        shorts = deque()
        for leg in legs:
            qty_rem = leg['qty']
            if leg['side'] == 'BUY':
                while qty_rem > 0 and shorts:
                    s = shorts[0]
                    mq = min(qty_rem, s['qty'])
                    trades.append(_build_round_trip(
                        parsed_sym, asset_type, detail, expiry_dt,
                        entry_ts=s['ts'], entry_price=s['price'],
                        exit_ts=leg['ts'], exit_price=leg['price'],
                        qty=mq, direction='SHORT',
                    ))
                    # Override broker name for Zerodha
                    trades[-1]['broker_name'] = 'Zerodha'
                    trades[-1]['source'] = 'zerodha_trades'
                    s['qty'] -= mq
                    qty_rem -= mq
                    if s['qty'] == 0:
                        shorts.popleft()
                if qty_rem > 0:
                    opens.append({**leg, 'qty': qty_rem})
            else:
                while qty_rem > 0 and opens:
                    o = opens[0]
                    mq = min(qty_rem, o['qty'])
                    trades.append(_build_round_trip(
                        parsed_sym, asset_type, detail, expiry_dt,
                        entry_ts=o['ts'], entry_price=o['price'],
                        exit_ts=leg['ts'], exit_price=leg['price'],
                        qty=mq, direction='LONG',
                    ))
                    trades[-1]['broker_name'] = 'Zerodha'
                    trades[-1]['source'] = 'zerodha_trades'
                    o['qty'] -= mq
                    qty_rem -= mq
                    if o['qty'] == 0:
                        opens.popleft()
                if qty_rem > 0:
                    shorts.append({**leg, 'qty': qty_rem})

    return trades


def _parse_zerodha_pnl(content):
    """
    Parse Zerodha Console tradewise P&L CSV (already-rounded round-trip rows).

    Expected columns (case-insensitive):
      tradingsymbol, open date / buy date, sell date / close date,
      quantity, buy average / buy avg, sell average / sell avg, pnl

    Each row IS a complete round trip — no FIFO matching needed.
    """
    import re as _re

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError('CSV appears empty.')

    norm_map = {h.strip().lower().replace(' ', '_'): h for h in reader.fieldnames}

    def _col(row_norm, *keys):
        for k in keys:
            v = row_norm.get(k, '').strip()
            if v:
                return v
        return ''

    trades = []
    for row in reader:
        row_norm = {k.strip().lower().replace(' ', '_'): (v or '').strip() for k, v in row.items() if k}
        sym = _col(row_norm, 'tradingsymbol', 'symbol', 'scrip').upper()
        if not sym:
            continue
        try:
            qty = float(_col(row_norm, 'quantity', 'qty') or '0')
            buy_avg = float((_col(row_norm, 'buy_average', 'buy_avg', 'buy_price') or '0').replace(',', ''))
            sell_avg = float((_col(row_norm, 'sell_average', 'sell_avg', 'sell_price') or '0').replace(',', ''))
            if qty <= 0 or buy_avg <= 0 or sell_avg <= 0:
                continue
        except (ValueError, TypeError):
            continue

        entry_ts = _parse_date_any(_col(row_norm, 'open_date', 'buy_date', 'trade_date', 'date'))
        exit_ts = _parse_date_any(_col(row_norm, 'close_date', 'sell_date', 'exit_date'))
        if not exit_ts:
            exit_ts = entry_ts or datetime.utcnow()
        if not entry_ts:
            entry_ts = exit_ts

        try:
            parsed_sym, asset_type, detail, expiry_dt = _parse_scrip_name(sym)
        except Exception:
            parsed_sym, asset_type, detail, expiry_dt = sym, 'STOCK', sym, None

        pnl = (sell_avg - buy_avg) * qty
        pnl_pct = round((sell_avg - buy_avg) / buy_avg * 100, 2) if buy_avg else 0.0
        hold_hrs = max(0.0, (exit_ts - entry_ts).total_seconds() / 3600)
        result = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BREAKEVEN')

        trades.append({
            'symbol': parsed_sym,
            'asset_type': asset_type,
            'instrument_detail': detail,
            'quantity': int(qty),
            'entry_price': round(buy_avg, 4),
            'exit_price': round(sell_avg, 4),
            'realized_pnl': round(pnl, 2),
            'pnl_percentage': pnl_pct,
            'holding_period_hours': round(hold_hrs, 2),
            'trade_result': result,
            'exit_reason': 'EXPIRY' if asset_type in ('OPTION', 'FUTURES') else 'MANUAL',
            'broker_name': 'Zerodha',
            'total_charges': 0.0,
            'net_pnl': round(pnl, 2),
            'entry_time': entry_ts,
            'exit_time': exit_ts,
            'strategy_name': asset_type,
            'source': 'zerodha_pnl',
        })

    return trades


@app.route('/dashboard/behavioural-insights/upload', methods=['POST'])
@login_required
def behaviour_upload_trades():
    """Smart CSV upload — auto-detects Dhan, Zerodha, and generic formats."""
    from models import ManualTradeImport

    file = request.files.get('trade_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('behavioural_insights'))

    content = file.read().decode('utf-8-sig', errors='replace')
    fmt = _detect_format(content)
    tenant_id = current_user.tenant_id or 'live'
    imported = 0
    errors = []

    # ── Broker-specific parsers ──────────────────────────────────────────────
    _BROKER_FMTS = ('dhan_pnl', 'dhan_trades', 'zerodha_trades', 'zerodha_pnl')
    if fmt in _BROKER_FMTS:
        _PARSER_MAP = {
            'dhan_pnl':       _parse_dhan_pnl,
            'dhan_trades':    _parse_dhan_trade_history,
            'zerodha_trades': _parse_zerodha_trades,
            'zerodha_pnl':    _parse_zerodha_pnl,
        }
        _BROKER_LABEL = {
            'dhan_pnl':       'Dhan P&L',
            'dhan_trades':    'Dhan Trade History',
            'zerodha_trades': 'Zerodha Trade Book',
            'zerodha_pnl':    'Zerodha P&L',
        }
        try:
            trade_dicts = _PARSER_MAP[fmt](content)
        except ValueError as e:
            flash(str(e), 'danger')
            return redirect(url_for('behavioural_insights'))

        if not trade_dicts:
            flash(
                f'No completed round-trip trades found in this {_BROKER_LABEL[fmt]} CSV. '
                'Only matched BUY↔SELL pairs are imported. Open positions are skipped.',
                'warning',
            )
            return redirect(url_for('behavioural_insights'))

        for td in trade_dicts:
            try:
                trade = ManualTradeImport(
                    user_id=current_user.id,
                    tenant_id=tenant_id,
                    **td,
                )
                db.session.add(trade)
                imported += 1
            except Exception as e:
                errors.append(str(e))

    # ── Generic / Capulse template ───────────────────────────────────
    else:
        REQUIRED = {'symbol', 'entry_date', 'exit_date', 'quantity', 'entry_price', 'exit_price'}
        reader = csv.DictReader(io.StringIO(content))
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing = REQUIRED - headers
        if missing:
            flash(
                f'Could not recognise this CSV format. Missing columns: {", ".join(sorted(missing))}. '
                f'Download the template for the correct format, or upload a Dhan / Zerodha P&L export.',
                'danger'
            )
            return redirect(url_for('behavioural_insights'))

        for i, row in enumerate(reader, start=2):
            try:
                row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                entry_dt = _parse_date_any(row['entry_date'])
                exit_dt = _parse_date_any(row['exit_date'])
                qty = int(float(row['quantity']))
                ep = float(row['entry_price'])
                xp = float(row['exit_price'])
                pnl = (xp - ep) * qty
                hold_hrs = max(0.0, (exit_dt - entry_dt).total_seconds() / 3600)
                result = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BREAKEVEN')
                pnl_pct = round((xp - ep) / ep * 100, 2) if ep else 0
                charges = float(row.get('charges', 0) or 0)
                broker = row.get('broker_name', 'Manual').strip() or 'Manual'
                strategy = row.get('strategy_name', 'Manual Import').strip() or 'Manual Import'
                exit_reason = row.get('exit_reason', 'MANUAL').strip().upper() or 'MANUAL'
                if exit_reason not in ('MANUAL', 'TARGET', 'STOPLOSS', 'EXPIRY'):
                    exit_reason = 'MANUAL'

                raw_asset = row.get('asset_type', 'STOCK').strip().upper()
                asset_type = raw_asset if raw_asset in ('STOCK', 'OPTION', 'FUTURES', 'MF') else 'STOCK'
                symbol_raw = row['symbol'].upper().strip()
                # Auto-detect F&O from symbol name if asset_type not set explicitly
                if asset_type == 'STOCK':
                    import re
                    if re.search(r'\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{4}', symbol_raw, re.IGNORECASE):
                        asset_type = 'OPTION'
                    elif symbol_raw.endswith('FUT') or 'FUTURES' in symbol_raw:
                        asset_type = 'FUTURES'

                trade = ManualTradeImport(
                    user_id=current_user.id,
                    tenant_id=tenant_id,
                    symbol=symbol_raw,
                    asset_type=asset_type,
                    instrument_detail='',
                    strategy_name=strategy,
                    quantity=qty,
                    entry_price=ep,
                    exit_price=xp,
                    realized_pnl=round(pnl, 2),
                    pnl_percentage=pnl_pct,
                    holding_period_hours=round(hold_hrs, 2),
                    trade_result=result,
                    exit_reason=exit_reason,
                    broker_name=broker,
                    total_charges=charges,
                    net_pnl=round(pnl - charges, 2),
                    entry_time=entry_dt,
                    exit_time=exit_dt,
                    source='csv_upload',
                )
                db.session.add(trade)
                imported += 1
            except Exception as e:
                errors.append(f'Row {i}: {e}')

    if imported:
        db.session.commit()
        _label_map = {
            'dhan_pnl': 'Dhan', 'dhan_trades': 'Dhan',
            'zerodha_trades': 'Zerodha', 'zerodha_pnl': 'Zerodha',
        }
        broker_label = _label_map.get(fmt, '')
        breakdown = {}
        if fmt in _BROKER_FMTS:
            for td in trade_dicts:
                breakdown[td['asset_type']] = breakdown.get(td['asset_type'], 0) + 1
        if breakdown:
            parts = [f"{v} {k}" for k, v in sorted(breakdown.items())]
            flash(
                f'Imported {imported} trade{"s" if imported != 1 else ""} from {broker_label} — '
                f'{", ".join(parts)}. Your Behavioural AI is now ready!',
                'success',
            )
        else:
            flash(f'Successfully imported {imported} trade{"s" if imported != 1 else ""}. Your Behavioural AI analysis is now ready!', 'success')
    else:
        db.session.rollback()
        if not errors:
            flash('No valid trades found in the file. Please check the file and try again.', 'warning')

    if errors:
        flash(f'Skipped {len(errors)} row{"s" if len(errors) > 1 else ""} with errors: {errors[0]}', 'warning')

    return redirect(url_for('behavioural_insights'))


@app.route('/dashboard/behavioural-insights/template')
@login_required
def behaviour_csv_template():
    """Download a sample CSV template supporting Stocks, F&O, and MF."""
    header = 'symbol,asset_type,entry_date,exit_date,quantity,entry_price,exit_price,broker_name,strategy_name,exit_reason,charges\n'
    rows = [
        'RELIANCE,STOCK,2024-01-15 09:30:00,2024-01-15 14:45:00,10,2450.50,2510.00,Zerodha,Momentum,TARGET,25.00',
        'NIFTY22700CE,OPTION,2024-01-20 10:00:00,2024-01-20 15:00:00,50,120.00,95.00,Dhan,Options Buy,EXPIRY,12.00',
        'NIFTYFUT,FUTURES,2024-01-22 09:15:00,2024-01-22 15:30:00,75,22500.00,22650.00,Angel One,Futures,MANUAL,30.00',
        'HDFC FLEXI CAP FUND,MF,2024-02-01,2024-02-28,100,50.00,52.50,Groww,SIP,MANUAL,0.00',
        'TATAMOTORS,STOCK,2024-02-01 11:00:00,2024-02-03 13:30:00,100,800.00,845.00,Groww,Swing,TARGET,40.00',
    ]
    output = header + '\n'.join(rows)
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=target_capital_trade_template.csv'}
    )


@app.route('/api/behaviour/narrative')
@login_required
@limiter.limit("30 per hour", key_func=lambda: f"u{current_user.id}" if current_user.is_authenticated else get_remote_address())
def behaviour_narrative():
    """Generate a personalized AI narrative using Claude (async-loaded)."""
    try:
        engine = _get_engine()
        analysis = engine.get_full_analysis()

        if not analysis.get('has_data'):
            return jsonify({'narrative': None, 'error': 'Not enough data'})

        patterns = analysis.get('categories', {})
        stats = analysis.get('stats', {})
        personality = analysis.get('personality', {})

        # Build a rich context for Claude
        detected_issues = []
        for cat_key, cat in patterns.items():
            for mod_key, mod in cat.get('modules', {}).items():
                if mod.get('severity') in ('high', 'medium') and mod.get('detected', True):
                    detected_issues.append(f"- {mod['label']}: {mod['insight']}")

        prompt = f"""Analyze this Indian retail trader's behavioral data and generate personalized insights.

Trading Stats (Last 90 days):
- Total trades: {stats.get('total_trades', 0)}
- Win rate: {stats.get('win_rate', 0)}%
- Total P&L: ₹{stats.get('total_pnl', 0):,.0f}
- Risk-Reward: {stats.get('risk_reward', 0)}:1
- Behavioral Score: {analysis.get('score', 50)}/100
- Trading Personality: {personality.get('type', 'Unknown')}

Detected behavioral issues:
{chr(10).join(detected_issues) if detected_issues else '- No major issues detected'}

Generate exactly 3 items in JSON format:
1. key_insight: One specific insight about their trading (reference actual numbers)
2. risk_warning: One concrete risk warning (or null if no major risks)  
3. action: One actionable next step they can take today

Rules:
- Be specific, not generic (use the actual numbers)
- Tone: Direct, human, supportive — like a mentor, not an advisor
- Keep each item under 20 words
- Do NOT say "consider" or "you might want to"
- Do NOT give financial advice

Respond only with valid JSON: {{"key_insight": "...", "risk_warning": "...", "action": "..."}}"""

        from services.anthropic_service import AnthropicService
        svc = AnthropicService()
        resp = svc._call_with_retry(
            model=AnthropicService.FALLBACK_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=300,
            temperature=0.4,
        )
        raw = resp.content[0].text.strip()
        # Extract JSON
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            import json
            narrative = json.loads(m.group())
        else:
            narrative = {'key_insight': raw[:120], 'risk_warning': None, 'action': None}

        return jsonify({'narrative': narrative})

    except Exception as e:
        logger.error(f"AI narrative error: {e}")
        return jsonify({'narrative': None, 'error': str(e)})


@app.route('/api/behaviour/timeline')
@login_required
def behaviour_timeline():
    """Return 30-day behavior timeline data."""
    try:
        engine = _get_engine()
        days = int(request.args.get('days', 30))
        timeline = engine.get_behavior_timeline(days=days)
        return jsonify({'timeline': timeline})
    except Exception as e:
        logger.error(f"Timeline error: {e}")
        return jsonify({'timeline': [], 'error': str(e)})


@app.route('/api/behaviour/cross-broker')
@login_required
def behaviour_cross_broker():
    """Return cross-broker intelligence data."""
    try:
        engine = _get_engine()
        data = engine.get_cross_broker_intelligence()
        return jsonify(data)
    except Exception as e:
        logger.error(f"Cross-broker error: {e}")
        return jsonify({'brokers': [], 'insight': 'Error loading data.', 'detected': False})


@app.route('/api/behaviour/today-alerts')
@login_required
def behaviour_today_alerts():
    """Return today's real-time behavior alerts."""
    try:
        engine = _get_engine()
        alerts = engine.get_today_alerts()
        return jsonify({'alerts': alerts})
    except Exception as e:
        logger.error(f"Today alerts error: {e}")
        return jsonify({'alerts': []})


@app.route('/api/behaviour/trading-dna')
@login_required
def behaviour_trading_dna():
    """Trading DNA archetype + cross-module correlations for overview AJAX."""
    try:
        engine = _get_engine()
        dna = engine.get_trading_dna()
        correlations = engine.get_cross_module_correlations()
        return jsonify({'dna': dna, 'correlations': correlations})
    except Exception as e:
        logger.error(f"Trading DNA error: {e}")
        return jsonify({'dna': None, 'correlations': [], 'error': str(e)})


@app.route('/api/behaviour/portfolio-narrative')
@login_required
def behaviour_portfolio_narrative():
    """AI narrative for portfolio health using Claude."""
    try:
        engine = _get_engine()
        data = engine.get_portfolio_behavior()
        intel = engine.get_portfolio_intelligence()

        if not data:
            return jsonify({'narrative': None})

        div = data['modules'].get('diversification', {})
        churn = data['modules'].get('churn', {})
        cap = data['modules'].get('capital_efficiency', {})
        cost = intel.get('cost_impact', {})

        prompt = f"""Analyze this Indian retail trader's portfolio behavior and generate a health summary.

Portfolio Stats (Last 30 days):
- Diversification: {div.get('num_stocks', 0)} assets, {div.get('num_sectors', 0)} sectors ({div.get('label_text', '')})
- Weekly Churn Rate: {churn.get('churn_rate', 0)}% ({churn.get('weekly_changes', 0)} symbol changes/week)
- Capital ROI: {cap.get('roi', 0)}% on ₹{cap.get('capital_deployed', 0):,.0f} deployed
- Capital to Winners: {cap.get('winning_capital_pct', 0)}%
- Monthly Transaction Costs: ₹{cost.get('total', 0):,} ({cost.get('n_trades', 0)} trades)
- Monthly P&L: ₹{cost.get('cur_pnl', 0):,.0f}
- Portfolio Score: {data.get('score', 0)}/100

Generate a portfolio health summary in JSON:
1. summary: One sentence (max 20 words) capturing the portfolio's health. Reference actual numbers.
2. top_risks: List of exactly 2 specific risks (short phrases, max 8 words each)
3. strength: One specific strength (max 12 words)

Rules:
- Be specific — use actual numbers from the data
- Direct tone, like a mentor
- Non-advisory (don't say "you should consider")
- No generic statements

Respond only with valid JSON: {{"summary": "...", "top_risks": ["...", "..."], "strength": "..."}}"""

        from services.anthropic_service import AnthropicService
        svc = AnthropicService()
        resp = svc._call_with_retry(
            model=AnthropicService.FALLBACK_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=250,
            temperature=0.35,
        )
        raw = resp.content[0].text.strip()
        import re as _re, json as _json
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        narrative = _json.loads(m.group()) if m else {'summary': raw[:150], 'top_risks': [], 'strength': ''}
        return jsonify({'narrative': narrative})
    except Exception as e:
        logger.error(f"Portfolio narrative error: {e}")
        return jsonify({'narrative': None, 'error': str(e)})


@app.route('/api/behaviour/progress')
@login_required
def behaviour_progress():
    """Return month-over-month progress metrics."""
    try:
        engine = _get_engine()
        progress = engine.get_progress_tracking()
        return jsonify(progress)
    except Exception as e:
        logger.error(f"Progress tracking error: {e}")
        return jsonify({'has_prev': False})


@app.route('/api/behaviour/score-breakdown')
@login_required
def behaviour_score_breakdown():
    """Return score breakdown by discipline/risk/timing/psychology."""
    try:
        engine = _get_engine()
        breakdown = engine.get_score_breakdown()
        return jsonify(breakdown)
    except Exception as e:
        logger.error(f"Score breakdown error: {e}")
        return jsonify({'discipline': 0, 'risk': 0, 'timing': 0, 'psychology': 0})


@app.route('/api/behaviour/pre-trade-check', methods=['POST'])
@login_required
def behaviour_pre_trade_check():
    try:
        engine = _get_engine()
        warnings = engine.pre_trade_check()
        return jsonify({'warnings': warnings})
    except Exception as e:
        logger.error(f"Pre-trade check error: {e}")
        return jsonify({'warnings': []})


@app.route('/behavioural-coach', methods=['GET', 'POST'])
@login_required
def behavioural_coach_assessment():
    """Investor behavioural assessment — in-page questionnaire in the Capulse dark layout."""
    from models import RiskProfile

    if request.method == 'POST':
        try:
            age_group = request.form.get('age_group', '26-35')
            investment_goal = request.form.get('investment_goal', 'wealth_creation')
            investment_horizon = request.form.get('investment_horizon', 'medium_term')
            investment_experience = request.form.get('investment_experience', 'intermediate')
            loss_choice = request.form.get('loss_tolerance', '5to10')

            age_score = {'18-25': 25, '26-35': 20, '36-45': 15, '46-55': 10, '55+': 5}
            goal_score = {'wealth_creation': 25, 'retirement': 15, 'children_education': 10,
                          'house_purchase': 10, 'emergency_fund': 5}
            horizon_score = {'short_term': 5, 'medium_term': 15, 'long_term': 25}
            exp_score = {'beginner': 5, 'intermediate': 15, 'advanced': 25}
            loss_map = {'lt5': 5, '5to10': 10, '10to20': 20, 'gt20': 30}
            loss_pct_map = {'lt5': 5, '5to10': 10, '10to20': 20, 'gt20': 30}

            risk_score = (
                age_score.get(age_group, 15) +
                goal_score.get(investment_goal, 15) +
                horizon_score.get(investment_horizon, 15) +
                exp_score.get(investment_experience, 15) +
                loss_map.get(loss_choice, 10)
            )

            if risk_score <= 40:
                risk_category = 'Conservative'
            elif risk_score <= 70:
                risk_category = 'Balanced'
            else:
                risk_category = 'Aggressive'

            rp = RiskProfile.query.filter_by(user_id=current_user.id).first()
            if rp:
                rp.age_group = age_group
                rp.investment_goal = investment_goal
                rp.investment_horizon = investment_horizon
                rp.risk_tolerance = risk_category.lower()
                rp.loss_tolerance = loss_pct_map.get(loss_choice, 10)
                rp.investment_experience = investment_experience
                rp.risk_score = risk_score
                rp.risk_category = risk_category
                rp.updated_at = datetime.utcnow()
            else:
                rp = RiskProfile(
                    user_id=current_user.id,
                    age_group=age_group,
                    investment_goal=investment_goal,
                    investment_horizon=investment_horizon,
                    risk_tolerance=risk_category.lower(),
                    loss_tolerance=loss_pct_map.get(loss_choice, 10),
                    investment_experience=investment_experience,
                    risk_score=risk_score,
                    risk_category=risk_category,
                )
                db.session.add(rp)
            db.session.commit()
        except Exception as e:
            logger.error(f"Behavioural assessment save error: {e}")

        return redirect(url_for('behavioural_coach_assessment'))

    # GET
    from models import RiskProfile
    risk_profile = RiskProfile.query.filter_by(user_id=current_user.id).first()

    from routes_chat import _get_user_sessions, _get_today_usage
    sessions = _get_user_sessions(current_user.id) if current_user.is_authenticated else []
    today_usage = _get_today_usage(current_user.id) if current_user.is_authenticated else 0

    return render_template(
        'behaviour_assessment.html',
        risk_profile=risk_profile,
        sessions=sessions,
        today_usage=today_usage,
        active_page='behaviour',
    )


@app.route('/trading-psychology', methods=['GET', 'POST'])
@login_required
def trading_psychology():
    """Trading Psychology Analysis — upload trades, get FOMO/revenge/overtrading report."""
    from models import ManualTradeImport

    # ── POST: CSV upload ──────────────────────────────────────────────────────
    if request.method == 'POST':
        file = request.files.get('trade_file')
        if not file or not file.filename.endswith('.csv'):
            flash('Please upload a valid CSV file.', 'danger')
            return redirect(url_for('trading_psychology'))

        content = file.read().decode('utf-8-sig', errors='replace')
        fmt = _detect_format(content)
        tenant_id = current_user.tenant_id or 'live'
        imported = 0
        errors = []

        _BROKER_FMTS = ('dhan_pnl', 'dhan_trades', 'zerodha_trades', 'zerodha_pnl')
        if fmt in _BROKER_FMTS:
            _PARSER_MAP = {
                'dhan_pnl': _parse_dhan_pnl,
                'dhan_trades': _parse_dhan_trade_history,
                'zerodha_trades': _parse_zerodha_trades,
                'zerodha_pnl': _parse_zerodha_pnl,
            }
            _BROKER_LABEL = {
                'dhan_pnl': 'Dhan P&L', 'dhan_trades': 'Dhan Trade History',
                'zerodha_trades': 'Zerodha Trade Book', 'zerodha_pnl': 'Zerodha P&L',
            }
            try:
                trade_dicts = _PARSER_MAP[fmt](content)
            except ValueError as e:
                flash(str(e), 'danger')
                return redirect(url_for('trading_psychology'))
            if not trade_dicts:
                flash('No completed round-trip trades found. Open positions are skipped.', 'warning')
                return redirect(url_for('trading_psychology'))
            for td in trade_dicts:
                try:
                    db.session.add(ManualTradeImport(user_id=current_user.id, tenant_id=tenant_id, **td))
                    imported += 1
                except Exception as e:
                    errors.append(str(e))
        else:
            # Generic CSV
            import csv as _csv, io as _io
            REQUIRED = {'symbol', 'entry_date', 'exit_date', 'quantity', 'entry_price', 'exit_price'}
            reader = _csv.DictReader(_io.StringIO(content))
            headers = {h.strip().lower() for h in (reader.fieldnames or [])}
            missing = REQUIRED - headers
            if missing:
                flash(f'Unrecognised CSV. Missing columns: {", ".join(sorted(missing))}. Download the template below.', 'danger')
                return redirect(url_for('trading_psychology'))
            for i, row in enumerate(reader, start=2):
                try:
                    row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                    entry_dt = _parse_date_any(row['entry_date'])
                    exit_dt = _parse_date_any(row['exit_date'])
                    qty = int(float(row['quantity']))
                    ep = float(row['entry_price'])
                    xp = float(row['exit_price'])
                    pnl = (xp - ep) * qty
                    hold_hrs = max(0.0, (exit_dt - entry_dt).total_seconds() / 3600)
                    result = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BREAKEVEN')
                    pnl_pct = round((xp - ep) / ep * 100, 2) if ep else 0
                    charges = float(row.get('charges', 0) or 0)
                    asset_type = row.get('asset_type', 'STOCK').strip().upper()
                    if asset_type not in ('STOCK', 'OPTION', 'FUTURES', 'MF'):
                        asset_type = 'STOCK'
                    db.session.add(ManualTradeImport(
                        user_id=current_user.id, tenant_id=tenant_id,
                        symbol=row['symbol'].upper().strip(), asset_type=asset_type,
                        instrument_detail='',
                        strategy_name=row.get('strategy_name', 'Manual').strip() or 'Manual',
                        quantity=qty, entry_price=ep, exit_price=xp,
                        realized_pnl=round(pnl, 2), pnl_percentage=pnl_pct,
                        holding_period_hours=round(hold_hrs, 2),
                        trade_result=result,
                        exit_reason=(row.get('exit_reason', 'MANUAL').strip().upper() or 'MANUAL'),
                        broker_name=row.get('broker_name', 'Manual').strip() or 'Manual',
                        total_charges=charges, net_pnl=round(pnl - charges, 2),
                        entry_time=entry_dt, exit_time=exit_dt, source='csv_upload',
                    ))
                    imported += 1
                except Exception as e:
                    errors.append(f'Row {i}: {e}')

        if imported:
            db.session.commit()
            flash(f'Imported {imported} trade{"s" if imported != 1 else ""}. Analysis ready!', 'success')
        else:
            db.session.rollback()
            flash('No valid trades found. Check the file and try again.', 'warning')
        if errors:
            flash(f'Skipped {len(errors)} rows with errors: {errors[0]}', 'warning')

        return redirect(url_for('trading_psychology'))

    # ── GET: fetch trades & run full engine analysis ──────────────────────────
    trades = ManualTradeImport.query.filter_by(user_id=current_user.id).all()

    from routes_chat import _get_user_sessions, _get_today_usage
    sessions = _get_user_sessions(current_user.id) if current_user.is_authenticated else []
    today_usage = _get_today_usage(current_user.id) if current_user.is_authenticated else 0

    if not trades:
        return render_template(
            'trading_psychology.html',
            has_trades=False,
            sessions=sessions, today_usage=today_usage,
            active_page='trading-psychology',
        )

    # ── Full engine run ───────────────────────────────────────────────────────
    engine = _get_engine()

    full = {}
    score_breakdown = {}
    progress = {}
    root_cause = None
    psych_narratives = {}
    dna = {}
    correlations = []

    try:
        full = engine.get_full_analysis()
    except Exception as e:
        logger.error(f'get_full_analysis error: {e}')
        full = {'has_data': False, 'score': 50, 'score_label': 'Unknown', 'score_color': '#6b7280',
                'stats': {}, 'categories': {}, 'patterns': {}, 'active_alerts': [],
                'by_hour': [], 'by_day': [], 'by_symbol': [], 'personality': None, 'master': {}}

    try:
        score_breakdown = engine.get_score_breakdown()
    except Exception as e:
        logger.error(f'get_score_breakdown error: {e}')

    try:
        progress = engine.get_progress_tracking()
    except Exception as e:
        logger.error(f'get_progress_tracking error: {e}')

    try:
        root_cause = engine.get_performance_root_cause()
    except Exception as e:
        logger.error(f'get_performance_root_cause error: {e}')

    try:
        psych_narratives = engine.get_psychology_narratives()
    except Exception as e:
        logger.error(f'get_psychology_narratives error: {e}')

    try:
        dna = engine.get_trading_dna()
    except Exception as e:
        logger.error(f'get_trading_dna error: {e}')

    try:
        correlations = engine.get_cross_module_correlations()
    except Exception as e:
        logger.error(f'get_cross_module_correlations error: {e}')

    stats       = full.get('stats', {})
    categories  = full.get('categories', {})
    personality = full.get('personality')
    overall_score = full.get('score', 50)
    trade_count = stats.get('total_trades', len(trades))
    wins        = stats.get('wins', 0)
    losses      = stats.get('losses', 0)
    win_rate    = stats.get('win_rate', 0)
    total_pnl   = stats.get('total_pnl', 0)

    # Flatten all modules across 5 categories for pattern cards
    all_modules = {}
    for cat_data in categories.values():
        all_modules.update(cat_data.get('modules', {}))

    # ── AI deep narrative using full analysis ─────────────────────────────────
    narrative = None
    action_items = []
    try:
        from services.anthropic_service import AnthropicService

        # Build detailed context from all 5 categories
        cat_lines = []
        for cat_key, cat_data in categories.items():
            cat_label = cat_data.get('label', cat_key.title())
            cat_score = cat_data.get('score', 50)
            cat_lines.append(f"\n{cat_label} (score {cat_score}/100):")
            for mod_key, mod in cat_data.get('modules', {}).items():
                severity = mod.get('severity', 'none')
                if severity in ('high', 'medium') or mod.get('detected'):
                    insight = mod.get('insight') or mod.get('message', '')
                    label   = mod.get('label', mod_key.replace('_', ' ').title())
                    cat_lines.append(f"  ⚠ {label}: {insight}")
                else:
                    label = mod.get('label', mod_key.replace('_', ' ').title())
                    cat_lines.append(f"  ✓ {label}: healthy")

        rc_text = ''
        if root_cause:
            rc = root_cause.get('root_cause', {})
            rc_text = (
                f"\nROOT CAUSE: {rc.get('label', '')} — {rc.get('detail', '')}"
                f"\nFIX PRIORITY: {root_cause.get('fix_priority', '')}"
                f"\nPOTENTIAL UPSIDE: {root_cause.get('potential_upside', '')}"
            )

        psych_text = ''
        for key, pn in psych_narratives.items():
            psych_text += f"\n{key.upper()}: {pn.get('narrative', '')} Self-awareness prompt: {pn.get('self_awareness', '')}"

        dna_text = ''
        if dna and dna.get('has_data'):
            dna_text = (
                f"\nTRADING DNA: {dna.get('archetype', '')} — {dna.get('archetype_description', '')}"
                f"\nActivity: {dna.get('activity', '')}, Emotional Level: {dna.get('emotional_level', '')}, R:R Quality: {dna.get('rr_quality', '')}"
            )

        rr = stats.get('risk_reward', 0)
        avg_win = stats.get('avg_win', 0)
        avg_loss = stats.get('avg_loss', 0)

        prompt = (
            f"You are analysing the trading psychology of an Indian retail trader.\n\n"
            f"OVERALL SCORE: {overall_score}/100 ({full.get('score_label', '')})\n"
            f"TRADER ARCHETYPE: {personality['type'] if personality else 'Unknown'}\n\n"
            f"TRADE STATS:\n"
            f"- Total trades: {trade_count}  |  Win rate: {win_rate}%  ({wins}W / {losses}L)\n"
            f"- Net P&L: ₹{total_pnl:,.0f}  |  Avg win: ₹{avg_win:,.0f}  |  Avg loss: ₹{avg_loss:,.0f}\n"
            f"- Risk-Reward ratio: {rr}:1\n"
            f"\nSCORE BREAKDOWN:\n"
            f"- Discipline: {score_breakdown.get('discipline', '?')}/100\n"
            f"- Risk Management: {score_breakdown.get('risk', '?')}/100\n"
            f"- Trade Timing: {score_breakdown.get('timing', '?')}/100\n"
            f"- Psychology: {score_breakdown.get('psychology', '?')}/100\n"
            f"\nDETAILED MODULE ANALYSIS:{''.join(cat_lines)}"
            f"\n{rc_text}"
            f"\nDEEP PSYCHOLOGY INSIGHTS:{psych_text}"
            f"\n{dna_text}"
            f"\n\nWrite a comprehensive, direct 4-paragraph trading psychology report:\n"
            "Paragraph 1 (Profile): Overall psychological fingerprint — their archetype, dominant pattern, and what drives their decision-making.\n"
            "Paragraph 2 (Damage): The specific biases causing the most P&L damage — use the actual numbers (win rate, R:R, avg win/loss). Be precise.\n"
            "Paragraph 3 (Root Cause): The root cause underneath the surface symptoms — what is the core behavioural loop creating these patterns?\n"
            "Paragraph 4 (Actions): Three to five very specific, actionable changes ranked by impact. Indian market context (NIFTY/BANKNIFTY F&O, Dhan/Zerodha). Each action should reference actual data.\n\n"
            "Use 'you' and 'your trades'. Be direct and data-driven. No headers, no bullet points. Plain paragraphs only. Max 450 words."
        )
        svc = AnthropicService()
        result = svc.chat(
            messages=[{'role': 'user', 'content': prompt}],
            system="You are a trading psychology expert specialising in Indian retail traders. You have the bluntness of a performance coach and the depth of a behavioural economist. Never sugarcoat. Never be generic. Reference actual numbers.",
            max_tokens=900,
            temperature=0.35,
        )
        narrative = result.get('content', '').strip()

        # Build prioritised action items from detected modules
        _severity_order = {'high': 0, 'medium': 1, 'low': 2, 'none': 3}
        _action_map = {
            'revenge_trading':    '<strong>30-minute revenge-trade cooldown:</strong> After any loss, set a phone timer and step away. Trades placed within 30 min of a loss have a statistically lower win rate in your data.',
            'overtrading':        '<strong>Hard cap of 3 trades per day:</strong> Excess frequency destroys edge through brokerage drag. Set the limit in your broker and treat it like a circuit breaker.',
            'loss_aversion':      '<strong>Pre-define your stop before entering:</strong> Write the exact exit price before you buy. If your stop is hit, close without renegotiating. Hope is not a trade plan.',
            'profit_booking':     '<strong>Trail stops instead of booking early:</strong> Move your stop to entry on a 1R move; trail at 1R below the high thereafter. Let the market take you out.',
            'tilt':               '<strong>Two-consecutive-loss rule:</strong> Log out of your broker after two back-to-back losses. Return the next session. Tilt compounds losses geometrically.',
            'overconfidence':     '<strong>Flat-size rule after win streaks:</strong> After 3+ consecutive wins, cap position size at 1% of capital regardless of confidence. Overconfidence peaks exactly when caution is most needed.',
            'fomo':               '<strong>Flat-size rule after win streaks:</strong> After 3+ consecutive wins, cap position size at 1% of capital regardless of confidence. Overconfidence peaks exactly when caution is most needed.',
            'panic_selling':      '<strong>Hide your P&L during market hours:</strong> Use broker settings to hide the unrealized P&L column. Evaluate positions on thesis validity, not current pain.',
            'time_of_day':        '<strong>Trade only in your profitable hour window:</strong> Your data shows a clear time-of-day bias. Only place live trades during your proven profitable hours.',
            'drawdown_sensitivity': '<strong>Cap daily drawdown at 2% of capital:</strong> When the day\'s loss hits 2%, stop trading. Drawdown sensitivity means further losses compound emotional decision-making.',
            'position_sizing':    '<strong>Standardise position sizing to 1–2% risk per trade:</strong> Inconsistent sizing means your results are driven by bet size, not skill. Use a fixed-risk calculator.',
            'leverage_risk':      '<strong>Reduce F&O lot count immediately:</strong> Leverage risk detected — one bad trade at current sizing can create unrecoverable drawdown. Cut to half the current lots.',
            'behavioral_drift':   '<strong>Journal your original trading plan:</strong> Behavioral drift was detected mid-period. Write down your rules and review them every morning before market open.',
        }
        detected_sorted = sorted(
            [(k, v) for k, v in all_modules.items() if v.get('detected') or v.get('severity') in ('high', 'medium')],
            key=lambda x: _severity_order.get(x[1].get('severity', 'none'), 3)
        )
        for key, _ in detected_sorted:
            if key in _action_map and _action_map[key] not in action_items:
                action_items.append(_action_map[key])

    except Exception as e:
        logger.error(f'Psychology AI narrative error: {e}')

    return render_template(
        'trading_psychology.html',
        has_trades=True,
        # core stats
        trade_count=trade_count,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pnl=total_pnl,
        overall_score=overall_score,
        # engine outputs
        full=full,
        categories=categories,
        all_modules=all_modules,
        personality=personality,
        score_breakdown=score_breakdown,
        progress=progress,
        root_cause=root_cause,
        psych_narratives=psych_narratives,
        dna=dna,
        correlations=correlations,
        by_hour=full.get('by_hour', []),
        by_day=full.get('by_day', []),
        by_symbol=full.get('by_symbol', []),
        active_alerts=full.get('active_alerts', []),
        # AI outputs
        narrative=narrative,
        action_items=action_items,
        # nav
        sessions=sessions,
        today_usage=today_usage,
        active_page='trading-psychology',
    )


@app.route('/trading-psychology/clear', methods=['POST'])
@login_required
def trading_psychology_clear():
    """Delete all imported trades for this user and restart fresh."""
    from models import ManualTradeImport
    ManualTradeImport.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash('All imported trades cleared. Upload a new CSV to start fresh.', 'success')
    return redirect(url_for('trading_psychology'))


@app.route('/api/behaviour/alert/<int:alert_id>/acknowledge', methods=['POST'])
@login_required
def acknowledge_behaviour_alert(alert_id):
    from models import BehaviouralAlert
    from datetime import datetime, timedelta
    alert = BehaviouralAlert.query.filter_by(
        id=alert_id, user_id=current_user.id
    ).first_or_404()
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})
