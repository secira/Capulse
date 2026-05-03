"""
Behavioural AI Routes — Target Capital
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
from datetime import datetime

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
    """Return 'dhan', 'zerodha', or 'generic' based on file content."""
    first_lines = content[:500].lower()
    if 'pnl report' in first_lines or 'scrip name' in first_lines:
        return 'dhan'
    if 'trade date' in first_lines and 'tradingsymbol' in first_lines:
        return 'zerodha'
    return 'generic'


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


@app.route('/dashboard/behavioural-insights/upload', methods=['POST'])
@login_required
def behaviour_upload_trades():
    """Smart CSV upload — auto-detects Dhan P&L, Zerodha, and generic formats."""
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
    if fmt == 'dhan':
        try:
            trade_dicts = _parse_dhan_pnl(content)
        except ValueError as e:
            flash(str(e), 'danger')
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

    # ── Generic / Target Capital template ───────────────────────────────────
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
        breakdown = {}
        for td in (trade_dicts if fmt == 'dhan' else []):
            breakdown[td['asset_type']] = breakdown.get(td['asset_type'], 0) + 1
        if breakdown:
            parts = [f"{v} {k}" for k, v in sorted(breakdown.items())]
            flash(
                f'Imported {imported} trades from Dhan — {", ".join(parts)}. '
                f'Your Behavioural AI is now ready!',
                'success'
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


@app.route('/api/behaviour/alert/<int:alert_id>/acknowledge', methods=['POST'])
@login_required
def acknowledge_behaviour_alert(alert_id):
    from models import BehaviouralAlert
    from datetime import datetime
    alert = BehaviouralAlert.query.filter_by(
        id=alert_id, user_id=current_user.id
    ).first_or_404()
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})
