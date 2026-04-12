"""
Behavioural AI Routes — Target Capital
Serves the Behavioural Insights dashboard, sub-pages, and pre-trade check API.
"""
from flask import render_template, request, jsonify, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from app import app, db
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
def behavioural_insights():
    try:
        engine = _get_engine()
        analysis = engine.get_full_analysis()
    except Exception as e:
        logger.error(f"Behavioural analysis error: {e}")
        analysis = None

    return render_template(
        'dashboard/behaviour/overview.html',
        analysis=analysis,
        page_title='Behavioural Insights',
    )


@app.route('/dashboard/behavioural-insights/trading')
@login_required
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
def behavioural_portfolio():
    try:
        engine = _get_engine()
        data = engine.get_portfolio_behavior()
    except Exception as e:
        logger.error(f"Portfolio behavior error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/portfolio.html',
        data=data,
        page_title='Portfolio Behavior',
    )


@app.route('/dashboard/behavioural-insights/performance')
@login_required
def behavioural_performance():
    try:
        engine = _get_engine()
        data = engine.get_performance_patterns()
    except Exception as e:
        logger.error(f"Performance patterns error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/performance.html',
        data=data,
        page_title='Performance Patterns',
    )


@app.route('/dashboard/behavioural-insights/psychology')
@login_required
def behavioural_psychology():
    try:
        engine = _get_engine()
        data = engine.get_psychology_patterns()
    except Exception as e:
        logger.error(f"Psychology patterns error: {e}")
        data = None

    return render_template(
        'dashboard/behaviour/psychology.html',
        data=data,
        page_title='Psychological Patterns',
    )


@app.route('/dashboard/behavioural-insights/upload', methods=['POST'])
@login_required
def behaviour_upload_trades():
    """Parse a CSV file and save trades to ManualTradeImport."""
    from models import ManualTradeImport

    file = request.files.get('trade_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('behavioural_insights'))

    REQUIRED = {'symbol', 'entry_date', 'exit_date', 'quantity', 'entry_price', 'exit_price'}
    DATE_FMTS = ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']

    def parse_dt(s):
        s = s.strip()
        for fmt in DATE_FMTS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f'Unrecognised date: {s!r}')

    content = file.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(content))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}

    missing = REQUIRED - headers
    if missing:
        flash(f'Missing columns: {", ".join(sorted(missing))}. Download the template for the correct format.', 'danger')
        return redirect(url_for('behavioural_insights'))

    tenant_id = current_user.tenant_id or 'live'
    imported = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
            entry_dt = parse_dt(row['entry_date'])
            exit_dt = parse_dt(row['exit_date'])
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

            trade = ManualTradeImport(
                user_id=current_user.id,
                tenant_id=tenant_id,
                symbol=row['symbol'].upper().strip(),
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
        flash(f'Successfully imported {imported} trade{"s" if imported != 1 else ""}. Your Behavioural AI analysis is now ready!', 'success')
    else:
        db.session.rollback()

    if errors:
        flash(f'Skipped {len(errors)} rows with errors: {errors[0]}', 'warning')

    return redirect(url_for('behavioural_insights'))


@app.route('/dashboard/behavioural-insights/template')
@login_required
def behaviour_csv_template():
    """Download a sample CSV template for trade imports."""
    header = 'symbol,entry_date,exit_date,quantity,entry_price,exit_price,broker_name,strategy_name,exit_reason,charges\n'
    rows = [
        'RELIANCE,2024-01-15 09:30:00,2024-01-15 14:45:00,10,2450.50,2510.00,Zerodha,Momentum,TARGET,25.00',
        'NIFTY24JAN21000CE,2024-01-20 10:00:00,2024-01-20 15:00:00,50,120.00,95.00,Dhan,Options Sell,STOPLOSS,12.00',
        'TATAMOTORS,2024-02-01 11:00:00,2024-02-03 13:30:00,100,800.00,845.00,Groww,Swing,TARGET,40.00',
    ]
    output = header + '\n'.join(rows)
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=target_capital_trade_template.csv'}
    )


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
