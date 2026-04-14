"""
Broker Routes - Handle broker connections, portfolio sync, and trading
"""

from flask import request, jsonify, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from app import app, db, csrf
from models_broker import (
    BrokerAccount, BrokerHolding, BrokerPosition, BrokerOrder,
    BrokerType, ConnectionStatus, OrderStatus, TransactionType,
    ProductType, OrderType
)
from models import ManualTradeImport
from services.broker_service import BrokerService, BrokerAPIError
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Broker Catalog - All supported brokers
BROKER_CATALOG = [
    {
        'type': BrokerType.ZERODHA,
        'name': 'Zerodha',
        'logo': 'https://zerodha.com/static/images/logo.svg',
        'status': 'active',
        'description': "India's largest broker · KiteConnect OAuth",
        'fields': ['client_id', 'access_token', 'api_secret'],
        'auth': 'oauth',
        'color': '#387ed1',
        'letter': 'Z',
    },
    {
        'type': BrokerType.UPSTOX,
        'name': 'Upstox',
        'logo': 'https://upstox.com/logo.png',
        'status': 'active',
        'description': 'Tech-first discount broker · v2 OAuth',
        'fields': ['client_id', 'access_token', 'api_secret'],
        'auth': 'oauth',
        'color': '#5a3fc0',
        'letter': 'U',
    },
    {
        'type': BrokerType.ANGEL_BROKING,
        'name': 'Angel One',
        'logo': 'https://angelone.in/logo.png',
        'status': 'active',
        'description': 'Full-service broker · TOTP direct connect',
        'fields': ['client_id', 'access_token', 'totp_secret'],
        'auth': 'totp',
        'color': '#e03c31',
        'letter': 'A',
    },
    {
        'type': BrokerType.ICICIDIRECT,
        'name': 'ICICI Direct',
        'logo': 'https://icicidirect.com/logo.png',
        'status': 'active',
        'description': 'Full-service broker · Breeze Connect OAuth',
        'fields': ['client_id', 'access_token', 'api_secret'],
        'auth': 'oauth',
        'color': '#c41230',
        'letter': 'I',
    },
    {
        'type': BrokerType.GROWW,
        'name': 'Groww',
        'logo': 'https://groww.in/logo.png',
        'status': 'active',
        'description': 'Simple & modern investing platform',
        'fields': ['access_token'],
        'auth': 'token',
        'color': '#00d09c',
        'letter': 'G',
    },
    {
        'type': BrokerType.ALICE_BLUE,
        'name': 'Alice Blue',
        'logo': 'https://aliceblueonline.com/logo.png',
        'status': 'active',
        'description': 'Discount broker · ANT API v2',
        'fields': ['client_id', 'api_secret'],
        'auth': 'totp',
        'color': '#1e3a8a',
        'letter': 'AB',
    },
    {
        'type': BrokerType.FIVE_PAISA,
        'name': '5 Paisa',
        'logo': 'https://5paisa.com/logo.png',
        'status': 'active',
        'description': 'Affordable brokerage · Direct API',
        'fields': ['client_id', 'access_token', 'api_secret'],
        'auth': 'totp',
        'color': '#e65100',
        'letter': '5P',
    },
    {
        'type': BrokerType.DHAN,
        'name': 'Dhan',
        'logo': 'https://dhan.co/logo.png',
        'status': 'active',
        'description': 'Low brokerage with advanced trading tools',
        'fields': ['client_id', 'access_token'],
        'auth': 'token',
        'color': '#0f766e',
        'letter': 'D',
    },
]

# Main broker routes - integrated into existing dashboard pages

@app.route('/dashboard/broker-accounts')
@login_required
def dashboard_broker_accounts():
    """Broker accounts management page - Available to all users"""
    
    # Get user's existing broker accounts
    user_brokers = BrokerAccount.query.filter_by(user_id=current_user.id).all()
    
    # Create a mapping of broker_type to account for quick lookup
    broker_accounts_map = {acc.broker_type: acc for acc in user_brokers}
    
    # Enrich broker catalog with user's account data
    enriched_catalog = []
    for broker in BROKER_CATALOG:
        broker_data = broker.copy()
        broker_type_value = broker['type'].value
        
        # Add user's account if exists
        if broker_type_value in broker_accounts_map:
            broker_data['account'] = broker_accounts_map[broker_type_value]
        else:
            broker_data['account'] = None
            
        enriched_catalog.append(broker_data)
    
    return render_template('dashboard/broker_accounts.html',
                         broker_catalog=enriched_catalog,
                         broker_types=BrokerType)

@app.route('/api/broker/add-account', methods=['POST'])
@login_required
def api_add_broker_account():
    """Add new broker account"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['broker_type', 'client_id', 'access_token']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'Missing {field}'}), 400
        
        # Check if broker type is valid
        try:
            broker_type = BrokerType(data['broker_type'])
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid broker type'}), 400
        
        # Prepare credentials based on broker type
        credentials = {
            'client_id': data['client_id'],
            'access_token': data['access_token'],
            'api_secret': data.get('api_secret'),
            'totp_secret': data.get('totp_secret')
        }
        
        # Check if user already has an account with this broker
        existing_account = BrokerAccount.query.filter_by(
            user_id=current_user.id, 
            broker_type=broker_type.value
        ).first()
        
        if existing_account:
            return jsonify({
                'success': False,
                'message': 'User can create only connection with each broker'
            }), 400
        
        # Check broker limits — all plans allow up to 3 broker connections
        from models import PricingPlan
        existing_brokers_count = BrokerAccount.query.filter_by(user_id=current_user.id).count()
        
        if existing_brokers_count >= 3:
            return jsonify({
                'success': False,
                'message': 'You can connect up to 3 brokers. Please remove an existing broker before adding a new one.'
            }), 400
        
        # Step 1: Save broker credentials (DO NOT CONNECT YET)
        # Create broker account in 'disconnected' state
        broker_account = BrokerAccount(
            user_id=current_user.id,
            broker_type=broker_type.value,
            broker_name=broker_type.value.title(),
            connection_status='disconnected',  # Saved but not connected
            is_primary=False,
            is_active=True
        )
        
        # Securely store encrypted credentials
        broker_account.set_credentials(
            client_id=credentials['client_id'],
            access_token=credentials['access_token'],
            api_secret=credentials.get('api_secret'),
            totp_secret=credentials.get('totp_secret')
        )
        
        db.session.add(broker_account)
        db.session.commit()
        
        logger.info(f"User {current_user.id} added broker {broker_type.value} (Step 1: Credentials saved)")
        
        return jsonify({
            'success': True,
            'message': f'{broker_type.value.title()} credentials saved successfully! Now click "Connect" to activate the broker.',
            'account_id': broker_account.id
        })
            
    except BrokerAPIError as e:
        logger.error(f"Broker API error in add_broker_account: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding broker account: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Failed to add broker. Please try again.'}), 500

@app.route('/api/broker/<int:account_id>/connect', methods=['POST'])
@login_required
def api_connect_broker(account_id):
    """Connect a broker (Step 2: Activate broker for trading)"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        if broker_account.connection_status == 'connected':
            return jsonify({'success': False, 'message': 'Broker is already connected'}), 400
        
        # Update connection status to connected
        broker_account.connection_status = 'connected'
        broker_account.last_connected = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f"User {current_user.id} connected broker {broker_account.broker_name} (ID: {account_id})")
        
        return jsonify({
            'success': True,
            'message': f'{broker_account.broker_name} connected successfully! You can now use it for trading.'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error connecting broker account: {e}")
        return jsonify({'success': False, 'message': 'Failed to connect broker'}), 500

@app.route('/api/broker/<int:account_id>/disconnect', methods=['POST'])
@login_required
def api_disconnect_broker(account_id):
    """Disconnect a broker (deactivate but keep credentials)"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        if broker_account.connection_status == 'disconnected':
            return jsonify({'success': False, 'message': 'Broker is already disconnected'}), 400
        
        # If this was the primary broker, unset it
        if broker_account.is_primary:
            broker_account.is_primary = False
        
        # Update connection status to disconnected (credentials preserved)
        broker_account.connection_status = 'disconnected'
        
        db.session.commit()
        
        logger.info(f"User {current_user.id} disconnected broker {broker_account.broker_name} (ID: {account_id})")
        
        return jsonify({
            'success': True,
            'message': f'{broker_account.broker_name} disconnected successfully. Credentials are saved for quick reconnection.'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error disconnecting broker account: {e}")
        return jsonify({'success': False, 'message': 'Failed to disconnect broker'}), 500

@app.route('/api/broker/sync-account/<int:account_id>', methods=['POST'])
@login_required
def api_sync_broker_account(account_id):
    """Sync broker account data"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        # Check if broker is connected
        if broker_account.connection_status != 'connected':
            return jsonify({
                'success': False, 
                'message': 'Please connect the broker first before syncing'
            }), 400
        
        # Get sync types from request
        data = request.get_json() or {}
        sync_types = data.get('sync_types', ['holdings', 'positions', 'orders'])
        
        logger.info(f"Starting sync for broker {broker_account.broker_name} (ID: {account_id})")

        broker_account.sync_status = 'syncing'
        db.session.commit()

        sync_results = BrokerService.sync_broker_data(broker_account, sync_types)

        broker_account.sync_status = 'success'
        broker_account.last_sync = datetime.utcnow()
        db.session.commit()

        logger.info(f"Sync completed for broker {broker_account.broker_name}: {sync_results}")

        # Build data preview from DB after sync — wrapped in its own try so it
        # never kills the success response even if serialisation fails.
        from models_broker import BrokerHolding, BrokerPosition, BrokerOrder
        holdings_preview = []
        positions_preview = []
        orders_preview = []

        try:
            raw_holdings = BrokerHolding.query.filter_by(broker_account_id=account_id).limit(50).all()
            logger.info(f"Data preview: found {len(raw_holdings)} holdings, querying broker_account_id={account_id}")
            for h in raw_holdings:
                try:
                    holdings_preview.append({
                        'symbol': str(h.trading_symbol or h.symbol or ''),
                        'exchange': str(h.exchange or ''),
                        'qty': int(h.total_quantity or h.available_quantity or 0),
                        'avg_price': round(float(h.avg_cost_price or 0), 2),
                        'current_price': round(float(h.current_price or 0), 2),
                        'invested': round(float(h.investment_value or 0), 2),
                        'current_val': round(float(h.total_value or 0), 2),
                        'pnl': round(float(h.pnl or 0), 2),
                        'pnl_pct': round(float(h.pnl_percentage or 0), 2),
                    })
                except Exception as he:
                    logger.warning(f"Skipping holding row due to error: {he}")

            raw_positions = BrokerPosition.query.filter_by(broker_account_id=account_id).limit(50).all()
            logger.info(f"Data preview: found {len(raw_positions)} positions")
            for p in raw_positions:
                try:
                    product_str = p.product_type.value if p.product_type else ''
                    positions_preview.append({
                        'symbol': str(p.trading_symbol or p.symbol or ''),
                        'exchange': str(p.exchange or ''),
                        'product': str(product_str),
                        'qty': int(p.quantity or 0),
                        'avg_buy': round(float(p.avg_buy_price or 0), 2),
                        'current_price': round(float(p.current_price or 0), 2),
                        'unrealized_pnl': round(float(p.unrealized_pnl or 0), 2),
                        'realized_pnl': round(float(p.realized_pnl or 0), 2),
                    })
                except Exception as pe:
                    logger.warning(f"Skipping position row due to error: {pe}")

            raw_orders = BrokerOrder.query.filter_by(broker_account_id=account_id).order_by(BrokerOrder.order_time.desc()).limit(20).all()
            logger.info(f"Data preview: found {len(raw_orders)} orders")
            for o in raw_orders:
                try:
                    orders_preview.append({
                        'broker_order_id': str(o.broker_order_id or '—'),
                        'symbol': str(o.trading_symbol or o.symbol or ''),
                        'type': str(o.transaction_type.value if o.transaction_type else ''),
                        'qty': int(o.quantity or 0),
                        'price': round(float(o.price or 0), 2),
                        'status': str(o.order_status.value if o.order_status else '—'),
                        'time': o.order_time.strftime('%d %b %H:%M') if o.order_time else '—',
                    })
                except Exception as oe:
                    logger.warning(f"Skipping order row due to error: {oe}")

        except Exception as preview_err:
            logger.error(f"Data preview query failed: {preview_err}", exc_info=True)

        logger.info(f"Data preview built: {len(holdings_preview)} holdings, {len(positions_preview)} positions, {len(orders_preview)} orders")

        return jsonify({
            'success': True,
            'message': 'Account synced successfully',
            'broker_name': str(broker_account.broker_name),
            'sync_results': sync_results,
            'last_sync': broker_account.last_sync.strftime('%b %d, %I:%M %p') if broker_account.last_sync else None,
            'data_preview': {
                'holdings': holdings_preview,
                'positions': positions_preview,
                'orders': orders_preview,
            }
        })

    except BrokerAPIError as e:
        logger.error(f"BrokerAPIError during sync: {str(e)}")
        try:
            broker_account.sync_status = 'failed'
            db.session.commit()
        except Exception:
            pass
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error syncing broker account: {str(e)}", exc_info=True)
        try:
            broker_account.sync_status = 'failed'
            db.session.commit()
        except Exception:
            pass
        return jsonify({'success': False, 'message': f'Sync failed: {str(e)}'}), 500

@app.route('/api/broker/trade-history/<int:account_id>', methods=['POST'])
@login_required
def api_broker_trade_history(account_id):
    """Fetch historic trades for a broker account within a date range.
    Works for Dhan via /v2/tradeHistory (paginated).
    Other brokers fall back to their trade book / generic implementation."""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()

        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404

        if broker_account.connection_status != 'connected':
            return jsonify({'success': False, 'message': 'Please connect the broker first'}), 400

        body = request.get_json() or {}
        from_date_str = body.get('from_date')
        to_date_str   = body.get('to_date')

        if not from_date_str or not to_date_str:
            return jsonify({'success': False, 'message': 'from_date and to_date are required (YYYY-MM-DD)'}), 400

        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date   = datetime.strptime(to_date_str,   '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid date format. Use YYYY-MM-DD'}), 400

        if from_date > to_date:
            return jsonify({'success': False, 'message': 'from_date cannot be after to_date'}), 400

        client = BrokerService.get_broker_client(broker_account)
        if not client.connect():
            return jsonify({'success': False, 'message': 'Failed to connect to broker'}), 400

        trades = client.get_trade_history(from_date=from_date, to_date=to_date)

        # Serialise trade_date field
        serialised = []
        for t in trades or []:
            row = dict(t)
            if isinstance(row.get('trade_date'), datetime):
                row['trade_date'] = row['trade_date'].strftime('%d %b %Y %H:%M')
            serialised.append(row)

        # Check which trade_ids are already imported for this user
        all_ext_ids_in_result = [t.get('trade_id') for t in serialised if t.get('trade_id')]
        already_imported = set()
        if all_ext_ids_in_result:
            rows = ManualTradeImport.query.filter(
                ManualTradeImport.user_id == current_user.id,
                ManualTradeImport.external_trade_id.in_(all_ext_ids_in_result),
            ).with_entities(ManualTradeImport.external_trade_id).all()
            already_imported = {r.external_trade_id for r in rows}

        return jsonify({
            'success': True,
            'broker_name': str(broker_account.broker_name),
            'from_date': from_date_str,
            'to_date': to_date_str,
            'trade_count': len(serialised),
            'trades': serialised,
            'already_imported_ids': list(already_imported),
        })

    except BrokerAPIError as e:
        logger.error(f"BrokerAPIError fetching trade history: {e}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error fetching trade history: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Failed to fetch trade history: {str(e)}'}), 500


@app.route('/api/broker/import-trades/<int:account_id>', methods=['POST'])
@login_required
def api_broker_import_trades(account_id):
    """Import selected broker trades into ManualTradeImport for Behavioural AI analysis.
    Trades are deduplicated by external_trade_id per user."""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id,
            user_id=current_user.id,
        ).first()
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404

        body = request.get_json() or {}
        trades = body.get('trades', [])
        if not trades:
            return jsonify({'success': False, 'message': 'No trades provided'}), 400

        # Fetch already-imported trade IDs for this user (avoid hitting DB per row)
        ext_ids_incoming = [t.get('trade_id') for t in trades if t.get('trade_id')]
        existing_ids = set()
        if ext_ids_incoming:
            rows = ManualTradeImport.query.filter(
                ManualTradeImport.user_id == current_user.id,
                ManualTradeImport.external_trade_id.in_(ext_ids_incoming),
            ).with_entities(ManualTradeImport.external_trade_id).all()
            existing_ids = {r.external_trade_id for r in rows}

        imported_count = 0
        skipped_count = 0
        tenant_id = getattr(current_user, 'tenant_id', 'live') or 'live'

        for t in trades:
            trade_id = t.get('trade_id') or ''

            # Skip duplicates
            if trade_id and trade_id in existing_ids:
                skipped_count += 1
                continue

            # Parse trade date — stored as formatted string from the fetch endpoint
            raw_date = t.get('trade_date') or ''
            trade_dt = None
            for fmt in ('%d %b %Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    trade_dt = datetime.strptime(raw_date, fmt)
                    break
                except ValueError:
                    continue
            if not trade_dt:
                trade_dt = datetime.utcnow()

            price = float(t.get('price') or 0.0)
            qty   = int(float(t.get('quantity') or 0))
            tx    = (t.get('transaction_type') or '').upper()
            sym   = (t.get('trading_symbol') or t.get('symbol') or '').strip()
            exch  = (t.get('exchange') or '').strip()
            prod  = (t.get('product_type') or '').strip()

            # Determine asset type from exchange / symbol
            if exch in ('NFO', 'BFO', 'MCX'):
                asset_type = 'FUTURES' if ('FUT' in sym.upper()) else 'OPTION'
            elif exch in ('CDS',):
                asset_type = 'FUTURES'
            else:
                asset_type = 'STOCK'

            # For single-leg imports we cannot compute real PnL.
            # Store with 0 PnL so Behavioural AI can analyse timing / frequency patterns.
            record = ManualTradeImport(
                tenant_id=tenant_id,
                user_id=current_user.id,
                symbol=sym or 'UNKNOWN',
                strategy_name='Broker Import',
                quantity=qty,
                entry_price=price,
                exit_price=price,
                realized_pnl=0.0,
                pnl_percentage=0.0,
                holding_period_hours=0.0,
                trade_result='BREAKEVEN',
                exit_reason='BROKER',
                broker_name=str(broker_account.broker_name),
                total_charges=0.0,
                net_pnl=0.0,
                entry_time=trade_dt,
                exit_time=trade_dt,
                asset_type=asset_type,
                instrument_detail=f"{exch} {prod}".strip(),
                source='broker_import',
                external_trade_id=trade_id or None,
                transaction_type=tx or None,
            )
            db.session.add(record)
            imported_count += 1

        db.session.commit()

        return jsonify({
            'success': True,
            'imported': imported_count,
            'skipped': skipped_count,
            'message': f'{imported_count} trade(s) imported, {skipped_count} duplicate(s) skipped.',
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error importing trades: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Import failed: {str(e)}'}), 500


@app.route('/api/broker/holding/<int:holding_id>', methods=['PUT'])
@login_required
def api_update_broker_holding(holding_id):
    """Edit avg_cost_price or current_price of a synced broker holding."""
    try:
        from models_broker import BrokerHolding, BrokerAccount
        holding = BrokerHolding.query.join(
            BrokerAccount, BrokerHolding.broker_account_id == BrokerAccount.id
        ).filter(
            BrokerHolding.id == holding_id,
            BrokerAccount.user_id == current_user.id,
        ).first()
        if not holding:
            return jsonify({'success': False, 'message': 'Holding not found'}), 404

        data = request.get_json() or {}
        if 'avg_cost_price' in data:
            holding.avg_cost_price = float(data['avg_cost_price'])
        if 'current_price' in data:
            holding.current_price = float(data['current_price'])
        if 'quantity' in data:
            holding.available_quantity = float(data['quantity'])
            holding.total_quantity = float(data['quantity'])

        # Recalculate derived fields
        holding.investment_value = holding.avg_cost_price * (holding.available_quantity or 0)
        holding.total_value = holding.current_price * (holding.available_quantity or 0)
        holding.pnl = holding.total_value - holding.investment_value
        holding.pnl_percentage = (holding.pnl / holding.investment_value * 100) if holding.investment_value else 0

        db.session.commit()
        return jsonify({'success': True, 'message': 'Holding updated.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating broker holding: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/broker/holding/<int:holding_id>', methods=['DELETE'])
@login_required
def api_delete_broker_holding(holding_id):
    """Delete a synced broker holding record (will re-appear on next sync)."""
    try:
        from models_broker import BrokerHolding, BrokerAccount
        holding = BrokerHolding.query.join(
            BrokerAccount, BrokerHolding.broker_account_id == BrokerAccount.id
        ).filter(
            BrokerHolding.id == holding_id,
            BrokerAccount.user_id == current_user.id,
        ).first()
        if not holding:
            return jsonify({'success': False, 'message': 'Holding not found'}), 404

        db.session.delete(holding)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Holding removed.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting broker holding: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/broker/remove-account/<int:account_id>', methods=['DELETE'])
@login_required
def api_remove_broker_account(account_id):
    """Remove broker account"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        broker_name = broker_account.broker_name
        
        # Delete account and all related data (cascade delete)
        db.session.delete(broker_account)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'{broker_name} account removed successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing broker account: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/broker/set-primary/<int:account_id>', methods=['POST'])
@login_required
def api_set_primary_broker(account_id):
    """Set primary broker account"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        broker_account.set_as_primary()
        
        return jsonify({
            'success': True,
            'message': f'{broker_account.broker_name} set as primary broker'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error setting primary broker: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/dashboard/live-portfolio')
@login_required
def dashboard_live_portfolio():
    """Live portfolio with real broker data"""
    # Check subscription access
    from models import PricingPlan
    if current_user.pricing_plan not in [PricingPlan.TARGET_PLUS, PricingPlan.TARGET_PRO, PricingPlan.HNI]:
        flash('Live portfolio access requires Target Plus subscription or higher.', 'warning')
        return redirect(url_for('pricing'))
    
    # Get portfolio summary with error handling
    try:
        from services.broker_service_helpers import get_portfolio_summary
        portfolio_summary = get_portfolio_summary(current_user.id)
    except:
        portfolio_summary = {
            'total_value': 0,
            'total_pnl': 0, 
            'holdings_count': 0,
            'brokers_count': 0,
            'broker_accounts': []
        }
    
    # Get all holdings across brokers
    broker_accounts = BrokerAccount.query.filter_by(user_id=current_user.id, is_active=True).all()
    all_holdings = []
    
    for account in broker_accounts:
        holdings = BrokerHolding.query.filter_by(broker_account_id=account.id).all()
        for holding in holdings:
            holding.broker_name = account.broker_name
            all_holdings.append(holding)
    
    # Sort by total value
    all_holdings.sort(key=lambda x: x.total_value, reverse=True)
    
    return render_template('dashboard/live_portfolio.html',
                         portfolio_summary=portfolio_summary,
                         holdings=all_holdings,
                         broker_accounts=broker_accounts)

@app.route('/api/broker/place-order', methods=['POST'])
@login_required
def api_place_broker_order():
    """Place order through broker"""
    try:
        # Check trading permissions based on pricing plan
        from models import PricingPlan
        
        if current_user.pricing_plan == PricingPlan.TARGET_PLUS:
            return jsonify({
                'success': False, 
                'message': 'Target Plus plan allows portfolio analysis only. Upgrade to Target Pro for trade execution.'
            }), 403
        
        if current_user.pricing_plan not in [PricingPlan.TARGET_PRO, PricingPlan.HNI]:
            return jsonify({
                'success': False, 
                'message': 'Trade execution requires Target Pro subscription or higher.'
            }), 403
        
        data = request.get_json()
        
        # Get broker account (use primary if not specified)
        account_id = data.get('broker_account_id')
        if account_id:
            broker_account = BrokerAccount.query.filter_by(
                id=account_id, 
                user_id=current_user.id
            ).first()
        else:
            broker_account = BrokerAccount.query.filter_by(
                user_id=current_user.id, 
                is_primary=True
            ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'No broker account found'}), 404
        
        # Ensure trading is only allowed with one broker (the primary one)
        if not broker_account.is_primary:
            return jsonify({
                'success': False, 
                'message': 'Trading is only allowed with your primary broker. Please set this broker as primary to trade.'
            }), 403
        
        # Validate order data
        required_fields = ['symbol', 'transaction_type', 'quantity', 'order_type', 'product_type']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'Missing {field}'}), 400
        
        # Prepare order data
        order_data = {
            'symbol': data['symbol'],
            'trading_symbol': data.get('trading_symbol', data['symbol']),
            'exchange': data.get('exchange', 'NSE'),
            'security_id': data.get('security_id'),
            'transaction_type': TransactionType(data['transaction_type']),
            'order_type': OrderType(data['order_type']),
            'product_type': ProductType(data['product_type']),
            'quantity': int(data['quantity']),
            'price': float(data.get('price', 0)),
            'trigger_price': float(data.get('trigger_price', 0)),
            'disclosed_quantity': int(data.get('disclosed_quantity', 0)),
            'correlation_id': data.get('correlation_id'),
            'trading_signal_id': data.get('trading_signal_id')
        }
        
        # Place order
        order = BrokerService.place_order_via_broker(broker_account, order_data)
        
        return jsonify({
            'success': True,
            'message': 'Order placed successfully',
            'order_id': order.id,
            'broker_order_id': order.broker_order_id
        })
        
    except BrokerAPIError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/broker/orders')
@login_required
def api_get_broker_orders():
    """Get user's broker orders"""
    try:
        # Get orders from all user's broker accounts
        broker_accounts = BrokerAccount.query.filter_by(user_id=current_user.id).all()
        account_ids = [account.id for account in broker_accounts]
        
        orders = BrokerOrder.query.filter(
            BrokerOrder.broker_account_id.in_(account_ids)
        ).order_by(BrokerOrder.order_time.desc()).limit(50).all()
        
        orders_data = []
        for order in orders:
            orders_data.append({
                'id': order.id,
                'broker_order_id': order.broker_order_id,
                'symbol': order.symbol,
                'transaction_type': order.transaction_type.value,
                'order_type': order.order_type.value,
                'product_type': order.product_type.value,
                'quantity': order.quantity,
                'filled_quantity': order.filled_quantity,
                'price': order.price,
                'order_status': order.order_status.value,
                'order_time': order.order_time.isoformat(),
                'broker_name': order.broker_account.broker_name,
                'avg_execution_price': order.avg_execution_price,
                'status_message': order.status_message
            })
        
        return jsonify({
            'success': True,
            'orders': orders_data
        })
        
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/dashboard/broker-trading')
@login_required
def dashboard_broker_trading():
    """Broker trading interface"""
    # Check subscription access
    from models import PricingPlan
    if current_user.pricing_plan not in [PricingPlan.TARGET_PRO, PricingPlan.HNI]:
        flash('Broker trading requires Target Pro or HNI subscription.', 'warning')
        return redirect(url_for('pricing'))
    
    broker_accounts = BrokerAccount.query.filter_by(
        user_id=current_user.id, 
        is_active=True,
        connection_status=ConnectionStatus.CONNECTED
    ).all()
    
    # Get recent orders
    if broker_accounts:
        account_ids = [account.id for account in broker_accounts]
        recent_orders = BrokerOrder.query.filter(
            BrokerOrder.broker_account_id.in_(account_ids)
        ).order_by(BrokerOrder.order_time.desc()).limit(10).all()
    else:
        recent_orders = []
    
    return render_template('dashboard/broker_trading.html',
                         broker_accounts=broker_accounts,
                         recent_orders=recent_orders)

@app.route('/api/broker/test-connection/<int:account_id>', methods=['POST'])
@login_required
def api_test_broker_connection(account_id):
    """Test broker connection"""
    try:
        broker_account = BrokerAccount.query.filter_by(
            id=account_id, 
            user_id=current_user.id
        ).first()
        
        if not broker_account:
            return jsonify({'success': False, 'message': 'Broker account not found'}), 404
        
        # Test connection
        client = BrokerService.get_broker_client(broker_account)
        if client.connect():
            return jsonify({
                'success': True,
                'message': 'Connection successful',
                'status': broker_account.connection_status.value,
                'last_connected': broker_account.last_connected.isoformat() if broker_account.last_connected else None
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Connection failed',
                'error': broker_account.connection_error
            })
            
    except Exception as e:
        logger.error(f"Error testing connection: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/broker/sync-all', methods=['POST'])
@login_required
def api_sync_all_brokers():
    """Sync all user's broker accounts"""
    try:
        broker_accounts = BrokerAccount.query.filter_by(
            user_id=current_user.id,
            is_active=True
        ).all()
        
        if not broker_accounts:
            return jsonify({'success': False, 'message': 'No broker accounts found'}), 404
        
        results = {}
        for account in broker_accounts:
            if account.connection_status != 'connected':
                results[account.broker_name] = {'skipped': 'not connected'}
                continue
            try:
                account.sync_status = 'syncing'
                db.session.commit()
                from services.broker_service_helpers import sync_broker_data
                sync_result = sync_broker_data(account)
                account.sync_status = 'success'
                account.last_sync = datetime.utcnow()
                db.session.commit()
                results[account.broker_name] = sync_result
            except Exception as e:
                account.sync_status = 'failed'
                db.session.commit()
                results[account.broker_name] = {'error': str(e)}

        return jsonify({
            'success': True,
            'message': f'Synced {len(broker_accounts)} broker accounts',
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error syncing all brokers: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/broker/cancel-order/<order_id>', methods=['POST'])
@login_required
def api_cancel_broker_order(order_id):
    """Cancel order by broker order ID"""
    try:
        # Find the order
        order = BrokerOrder.query.join(BrokerAccount).filter(
            BrokerOrder.broker_order_id == order_id,
            BrokerAccount.user_id == current_user.id
        ).first()
        
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404
        
        # Get broker client and cancel order
        client = BrokerService.get_broker_client(order.broker_account)
        if not client.connect():
            return jsonify({'success': False, 'message': 'Failed to connect to broker'}), 400
        
        result = client.cancel_order(order_id)
        
        if result.get('status') == 'success':
            # Update order status in database
            order.update_status(OrderStatus.CANCELLED, 'Order cancelled by user')
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': 'Order cancelled successfully'
            })
        else:
            return jsonify({'success': False, 'message': result.get('message', 'Failed to cancel order')}), 400
            
    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500