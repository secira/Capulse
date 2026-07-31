"""
Capulse Chat Routes — ChatGPT-style conversational interface.
Handles chat sessions, message processing, and static Capulse pages.
"""
import uuid
import json
import logging
from datetime import datetime, date

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from flask_login import login_required, current_user

from db_instance import db
from models import ChatConversation, ChatMessage

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)

DAILY_LIMIT_FREE = 5
DAILY_LIMIT_PAID = 100


def _get_daily_limit(user):
    try:
        plan = user.pricing_plan.name
        return DAILY_LIMIT_FREE if plan == 'FREE' else DAILY_LIMIT_PAID
    except Exception:
        return DAILY_LIMIT_FREE


def _get_today_usage(user_id: int) -> int:
    """Count messages sent today by the user."""
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        return ChatMessage.query.filter(
            ChatMessage.user_id == user_id,
            ChatMessage.message_type == 'user',
            ChatMessage.created_at >= today_start
        ).count()
    except Exception:
        return 0


def _get_user_sessions(user_id: int, limit: int = 20):
    """Get recent chat sessions for the user."""
    try:
        return ChatConversation.query.filter_by(
            user_id=user_id, is_active=True
        ).order_by(ChatConversation.updated_at.desc()).limit(limit).all()
    except Exception:
        return []


def _get_or_create_session(user_id: int, session_id: str = None) -> ChatConversation:
    """Get existing session or create a new one."""
    if session_id:
        conv = ChatConversation.query.filter_by(
            session_id=session_id, user_id=user_id
        ).first()
        if conv:
            return conv

    # Create new session
    conv = ChatConversation(
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        title=None,
        tenant_id='live',
    )
    db.session.add(conv)
    db.session.commit()
    return conv


def _get_conversation_history(conversation_id: int, limit: int = 10) -> list:
    """Get recent messages as dicts for the router context."""
    try:
        msgs = ChatMessage.query.filter_by(
            conversation_id=conversation_id
        ).order_by(ChatMessage.created_at.desc()).limit(limit).all()
        return [{'role': m.message_type, 'content': m.content} for m in reversed(msgs)]
    except Exception:
        return []


def _auto_title(message: str) -> str:
    """Generate a short title from the first user message."""
    words = message.strip().split()
    title = ' '.join(words[:7])
    if len(words) > 7:
        title += '…'
    return title[:100]


# ── Routes ──────────────────────────────────────────────────────────────────

@chat_bp.route('/chat')
def chat_home():
    """Main Capulse chat interface — public landing, no login required."""
    session_id = request.args.get('session_id')
    import json as _json
    prefill_q     = request.args.get('q', '').strip()   # sidebar quick-action pre-fill
    prefill_q_js  = _json.dumps(prefill_q)               # safe JS literal, e.g. "" or "NIFTY signals"
    sessions = []
    today_usage = 0
    messages_history = []
    current_session_id = None
    has_holdings = False

    if current_user.is_authenticated:
        sessions = _get_user_sessions(current_user.id)
        today_usage = _get_today_usage(current_user.id)

        if session_id:
            conv = ChatConversation.query.filter_by(
                session_id=session_id, user_id=current_user.id
            ).first()
            if conv:
                current_session_id = conv.session_id
                messages_history = ChatMessage.query.filter_by(
                    conversation_id=conv.id
                ).order_by(ChatMessage.created_at.asc()).all()

        # Check if user has any manual equity holdings (for the "add holdings" prompt)
        try:
            from models import ManualEquityHolding
            has_holdings = ManualEquityHolding.query.filter_by(
                user_id=current_user.id
            ).count() > 0
        except Exception:
            has_holdings = True  # don't show the prompt if model is unavailable

    return render_template(
        'chat.html',
        active_page='chat',
        sessions=sessions,
        today_usage=today_usage,
        messages_history=messages_history,
        current_session_id=current_session_id,
        prefill_q=prefill_q,
        prefill_q_js=prefill_q_js,
        has_holdings=has_holdings,
    )


@chat_bp.route('/chat/new')
@login_required
def new_chat():
    """Start a new chat session."""
    return redirect(url_for('chat.chat_home'))


@chat_bp.route('/chat/message', methods=['POST'])
@login_required
def chat_message():
    """Process a chat message and return the AI response."""
    try:
        data = request.get_json(force=True) or {}
        message = (data.get('message') or '').strip()
        session_id = data.get('session_id') or ''

        if not message:
            return jsonify({'error': 'Message is required'}), 400

        # Check daily limit
        today_usage = _get_today_usage(current_user.id)
        daily_limit = _get_daily_limit(current_user)
        if today_usage >= daily_limit:
            return jsonify({
                'error': f"You've used your {daily_limit} daily questions. Upgrade for more.",
                'card_type': 'prose',
                'content': f"Daily limit reached ({daily_limit} questions). Upgrade to Capulse Plus for 100 questions per day.",
            }), 429

        # Get or create conversation
        conv = _get_or_create_session(current_user.id, session_id or None)

        # Auto-title on first message
        if not conv.title:
            conv.title = _auto_title(message)

        # Save user message
        user_msg = ChatMessage(
            conversation_id=conv.id,
            user_id=current_user.id,
            message_type='user',
            content=message,
            tenant_id='live',
        )
        db.session.add(user_msg)
        db.session.flush()

        # Get conversation history for context
        history = _get_conversation_history(conv.id, limit=10)

        # Route to appropriate engine
        from services.capulse_router import route_message
        result = route_message(
            message=message,
            user_id=current_user.id,
            conversation_history=history,
        )

        # Format content for storage
        assistant_content = result.get('content', '')

        # Save assistant message with full result as context
        assist_msg = ChatMessage(
            conversation_id=conv.id,
            user_id=current_user.id,
            message_type='assistant',
            content=assistant_content,
            processing_time=result.get('processing_time'),
            tenant_id='live',
        )
        assist_msg.set_context_json(result)
        db.session.add(assist_msg)

        # Update conversation timestamp
        conv.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'session_id': conv.session_id,
            'card_type': result.get('card_type', 'prose'),
            'content': assistant_content,
            'card_data': result.get('card_data'),
            'intent': result.get('intent'),
        })

    except Exception as e:
        logger.error(f"Chat message error: {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': 'Something went wrong. Please try again.'}), 500


def _import_holdings_from_chat(file_bytes, user):
    """
    Import a Zerodha or Dhan/Groww Holdings CSV uploaded via chat, then
    immediately run a full Portfolio Intelligence analysis and return a
    rich portfolio card for the chat response.
    """
    import io
    from services.dhan_csv_importer import (
        is_zerodha_csv, parse_and_import_zerodha_csv,
        is_dhan_csv,    parse_and_import_dhan_csv,
    )
    from services.portfolio_intelligence import PortfolioIntelligenceEngine

    # ── 1. Detect broker and import ───────────────────────────────────────────
    raw_text   = file_bytes.decode('utf-8-sig', errors='replace')
    first_line = raw_text.split('\n')[0]
    sample_headers = [c.strip().strip('"').lower() for c in first_line.split(',')]

    if is_zerodha_csv(sample_headers):
        broker_label = 'Zerodha'
        result = parse_and_import_zerodha_csv(io.BytesIO(file_bytes), user_id=user.id)
    else:
        broker_label = 'Dhan / Groww'
        result = parse_and_import_dhan_csv(io.BytesIO(file_bytes), user_id=user.id)

    imported    = result['imported']
    updated     = result['updated']
    deactivated = result.get('deactivated', 0)
    unresolved  = result.get('unresolved', [])
    total_rows  = result.get('total_rows', imported + updated)

    # ── 2. Invalidate cache so PIE sees fresh data ────────────────────────────
    PortfolioIntelligenceEngine.invalidate_cache(user.id)

    # ── 3. Run Portfolio Intelligence Engine ─────────────────────────────────
    engine = PortfolioIntelligenceEngine(user_id=user.id)
    try:
        pir = engine.generate_report()
    except Exception as exc:
        logger.error(f'chat holdings PIE error: {exc}', exc_info=True)
        # Fall back to a plain import-confirmation card on PIE failure
        return _holdings_import_prose(broker_label, imported, updated,
                                      deactivated, unresolved)

    if not pir.get('has_holdings'):
        return _holdings_import_prose(broker_label, imported, updated,
                                      deactivated, unresolved)

    # ── 4. Map PIE report → chat 'portfolio' card ─────────────────────────────
    total_val = pir.get('total_current_value', 0)
    total_inv = pir.get('total_investment', 0)
    total_pnl = pir.get('total_pnl', 0)
    pnl_pct   = pir.get('pnl_pct', 0)
    n_hold    = pir.get('total_holdings', 0)
    pi_score  = pir.get('portfolio_intelligence_score', 50)
    div_score = pir.get('diversification_score', 50)
    sector_alloc = pir.get('sector_allocation', {})
    sub_scores   = pir.get('sub_scores', {})

    # Summary
    summary = {
        'total_value':          round(total_val, 0),
        'total_investment':     round(total_inv, 0),
        'total_pnl':            round(total_pnl, 0),
        'total_pnl_percentage': round(pnl_pct, 1),
        'holdings_count':       n_hold,
    }

    # Sectors: PIE gives {name: float_pct}; card wants [(name, pct_int), ...]
    sectors_sorted = sorted(sector_alloc.items(), key=lambda x: x[1], reverse=True)
    sectors = [(s, round(p, 1)) for s, p in sectors_sorted[:8]]

    # Top holdings
    top_holdings = []
    for card in sorted(pir.get('holdings', []),
                       key=lambda h: h.get('weight', 0), reverse=True)[:8]:
        top_holdings.append({
            'symbol':         card['symbol'],
            'value':          round(total_val * card.get('weight', 0), 0),
            'percentage':     round(card.get('weight', 0) * 100, 1),
            'pnl_percentage': card.get('pnl_pct', 0),
        })

    # Risk flags derived from PIE data
    flags = {
        'concentration_risk':   div_score < 35,
        'under_diversified':    len(pir.get('missing_sectors', [])) >= 4,
        'high_volatility':      sub_scores.get('risk', 50) < 40,
        'sector_concentration': any(v > 50 for v in sector_alloc.values()),
    }

    # AI assessment block (health_score = PI score)
    risk_level = 'Low' if pi_score >= 70 else ('Medium' if pi_score >= 50 else 'High')
    ai_assessment = {
        'health_score':        round(pi_score, 0),
        'risk_level':          risk_level,
        'concentration_risk':  flags['concentration_risk'],
        'under_diversified':   flags['under_diversified'],
        'high_volatility':     flags['high_volatility'],
        'sector_concentration': flags['sector_concentration'],
        'suggestions':         [a.get('action', '') for a in
                                pir.get('action_centre', [])[:3] if a.get('action')],
        'rebalance_actions':   [],
    }

    # Narrative from PIE executive summary
    narrative = pir.get('executive_summary', '').strip() or None

    # ── 5. Build import-summary header ────────────────────────────────────────
    import_note = (
        f'**{broker_label} import** — {imported} new, {updated} updated'
        + (f', {deactivated} removed' if deactivated else '')
        + f' ({total_rows} holdings total).'
    )
    if unresolved:
        names = ', '.join(r.get('name', '?') for r in unresolved[:4])
        more  = f' +{len(unresolved)-4} more' if len(unresolved) > 4 else ''
        import_note += (
            f' ⚠ {len(unresolved)} could not be matched to an NSE symbol '
            f'(`{names}{more}`) — run "i-Score \\<name>" in chat to resolve them.'
        )

    content = (
        f'✅ {import_note}\n\n'
        f"Here's your portfolio analysis across **{n_hold} holdings**:"
    )

    return {
        'card_type': 'portfolio',
        'content':   content,
        'card_data': {
            'summary':       summary,
            'sectors':       sectors,
            'top_holdings':  top_holdings,
            'risk_metrics':  pir.get('risk_radar', {}),
            'ai_assessment': ai_assessment,
            'narrative':     narrative,
            'flags':         flags,
            'suggestions':   ai_assessment['suggestions'],
            'rebalance':     [],
        },
    }


def _holdings_import_prose(broker_label, imported, updated, deactivated, unresolved):
    """Fallback plain-text card when PIE analysis cannot run after import."""
    lines = [
        f'✅ **{broker_label} Holdings imported** — '
        f'{imported} new, {updated} updated'
        + (f', {deactivated} removed position(s) hidden' if deactivated else '')
        + '.',
    ]
    if unresolved:
        names = ', '.join(r.get('name', '?') for r in unresolved[:5])
        extra = f' (+{len(unresolved)-5} more)' if len(unresolved) > 5 else ''
        lines.append(
            f'\n⚠️ **{len(unresolved)} holding(s) could not be matched** to an NSE '
            f'symbol: `{names}{extra}`. Run "i-Score \\<name>" in chat to resolve them.'
        )
    lines.append(
        '\nNow ask me to **"analyse my portfolio"** for a full breakdown, '
        'or visit the Portfolio Analysis page.'
    )
    return {'card_type': 'prose', 'content': '\n'.join(lines)}


def _run_psychology_analysis_inline(file_bytes, filename, message, user):
    """Parse a trade CSV, run full BehaviourEngine analysis, return a chat card payload."""
    from routes_behaviour import (
        _detect_format, _parse_dhan_pnl, _parse_dhan_trade_history,
        _parse_zerodha_trades, _parse_zerodha_pnl,
    )
    from models import ManualTradeImport
    from services.behaviour_engine import BehaviourEngine

    tenant_id = (user.tenant_id or 'live')

    # ── 1. Parse CSV ──────────────────────────────────────────────────────────
    try:
        text_content = file_bytes.decode('utf-8-sig', errors='replace')
    except Exception:
        return {'card_type': 'prose', 'content': 'Could not decode the CSV file. Please check the encoding and try again.'}

    fmt = _detect_format(text_content)
    _PARSER_MAP = {
        'dhan_pnl':      _parse_dhan_pnl,
        'dhan_trades':   _parse_dhan_trade_history,
        'zerodha_trades': _parse_zerodha_trades,
        'zerodha_pnl':   _parse_zerodha_pnl,
    }
    _FMT_LABELS = {
        'dhan_pnl': 'Dhan P&L', 'dhan_trades': 'Dhan Trade History',
        'zerodha_trades': 'Zerodha Trade Book', 'zerodha_pnl': 'Zerodha P&L',
    }
    fmt_label = _FMT_LABELS.get(fmt, 'Generic CSV')
    imported = 0
    errors   = []

    if fmt in _PARSER_MAP:
        try:
            trade_dicts = _PARSER_MAP[fmt](text_content)
        except ValueError as e:
            return {'card_type': 'prose', 'content': f'Could not parse your {fmt_label}: {e}'}
        if not trade_dicts:
            return {'card_type': 'prose', 'content': 'No completed round-trip trades found in the file. Open positions are skipped.'}
        for td in trade_dicts:
            try:
                db.session.add(ManualTradeImport(user_id=user.id, tenant_id=tenant_id, **td))
                imported += 1
            except Exception as e:
                errors.append(str(e))
    else:
        # Generic CSV
        import csv as _csv, io as _io
        from routes_behaviour import _parse_date_any
        REQUIRED = {'symbol', 'entry_date', 'exit_date', 'quantity', 'entry_price', 'exit_price'}
        reader = _csv.DictReader(_io.StringIO(text_content))
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing = REQUIRED - headers
        if missing:
            return {
                'card_type': 'prose',
                'content': (
                    f'Unrecognised CSV format — missing columns: `{", ".join(sorted(missing))}`.\n\n'
                    f'Supported formats: **Dhan P&L**, **Dhan Trade History**, **Zerodha P&L**, **Zerodha Trade Book**. '
                    f'Or use a generic CSV with columns: symbol, entry_date, exit_date, quantity, entry_price, exit_price.'
                )
            }
        for i, row in enumerate(reader, start=2):
            try:
                row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                from routes_behaviour import _parse_date_any
                entry_dt = _parse_date_any(row['entry_date'])
                exit_dt  = _parse_date_any(row['exit_date'])
                qty  = int(float(row['quantity']))
                ep   = float(row['entry_price'])
                xp   = float(row['exit_price'])
                pnl  = (xp - ep) * qty
                hold_hrs = max(0.0, (exit_dt - entry_dt).total_seconds() / 3600)
                result_str = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BREAKEVEN')
                pnl_pct  = round((xp - ep) / ep * 100, 2) if ep else 0
                charges  = float(row.get('charges', 0) or 0)
                asset_type = row.get('asset_type', 'STOCK').strip().upper()
                if asset_type not in ('STOCK', 'OPTION', 'FUTURES', 'MF'):
                    asset_type = 'STOCK'
                db.session.add(ManualTradeImport(
                    user_id=user.id, tenant_id=tenant_id,
                    symbol=row['symbol'].upper().strip(), asset_type=asset_type,
                    instrument_detail='',
                    strategy_name=row.get('strategy_name', 'Manual').strip() or 'Manual',
                    quantity=qty, entry_price=ep, exit_price=xp,
                    realized_pnl=round(pnl, 2), pnl_percentage=pnl_pct,
                    holding_period_hours=round(hold_hrs, 2),
                    trade_result=result_str,
                    exit_reason=(row.get('exit_reason', 'MANUAL').strip().upper() or 'MANUAL'),
                    broker_name=row.get('broker_name', 'Manual').strip() or 'Manual',
                    total_charges=charges, net_pnl=round(pnl - charges, 2),
                    entry_time=entry_dt, exit_time=exit_dt, source='csv_upload',
                ))
                imported += 1
            except Exception as e:
                errors.append(f'Row {i}: {e}')

    if not imported:
        db.session.rollback()
        return {'card_type': 'prose', 'content': 'No valid trades found. Please check the file format and try again.'}

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'Trade import commit error: {e}')
        return {'card_type': 'prose', 'content': 'Trade import failed. Please try again.'}

    # ── 2. Run full engine analysis ───────────────────────────────────────────
    engine  = BehaviourEngine(user_id=user.id, tenant_id=tenant_id)

    full          = {}
    score_bd      = {}
    root_cause    = None
    psych_narr    = {}
    correlations  = []

    try:
        full = engine.get_full_analysis()
    except Exception as e:
        logger.error(f'Psychology inline — get_full_analysis: {e}')

    try:
        score_bd = engine.get_score_breakdown()
    except Exception as e:
        logger.error(f'Psychology inline — get_score_breakdown: {e}')

    try:
        root_cause = engine.get_performance_root_cause()
    except Exception as e:
        logger.error(f'Psychology inline — get_performance_root_cause: {e}')

    try:
        psych_narr = engine.get_psychology_narratives()
    except Exception as e:
        logger.error(f'Psychology inline — get_psychology_narratives: {e}')

    try:
        correlations = engine.get_cross_module_correlations()
    except Exception as e:
        logger.error(f'Psychology inline — get_cross_module_correlations: {e}')

    stats       = full.get('stats', {})
    categories  = full.get('categories', {})
    personality = full.get('personality')

    # Flatten all modules, pick detected/high/medium ones
    all_modules = {}
    for cat_data in categories.values():
        all_modules.update(cat_data.get('modules', {}))

    active_issues = sorted(
        [
            {
                'key': k,
                'label': v.get('label', k.replace('_', ' ').title()),
                'severity': v.get('severity', 'none'),
                'score': v.get('score', 50),
                'insight': v.get('insight') or v.get('message', ''),
            }
            for k, v in all_modules.items()
            if v.get('detected') or v.get('severity') in ('high', 'medium')
        ],
        key=lambda x: {'high': 0, 'medium': 1, 'low': 2, 'none': 3}.get(x['severity'], 3)
    )

    # Simplified category list for JS rendering
    cat_summary = []
    _cat_labels = {
        'trading': 'Trading Behaviour', 'risk': 'Risk Management',
        'portfolio': 'Portfolio Health', 'performance': 'Performance Patterns',
        'psychology': 'Psychology',
    }
    for cat_key, cat_data in categories.items():
        modules_list = [
            {
                'key': mk,
                'label': mv.get('label', mk.replace('_', ' ').title()),
                'score': mv.get('score', 50),
                'severity': mv.get('severity', 'none'),
                'detected': mv.get('detected', False),
                'insight': mv.get('insight') or mv.get('message', ''),
            }
            for mk, mv in cat_data.get('modules', {}).items()
        ]
        cat_summary.append({
            'key': cat_key,
            'label': _cat_labels.get(cat_key, cat_key.title()),
            'score': cat_data.get('score', 50),
            'modules': modules_list,
        })

    # ── 3. AI narrative ───────────────────────────────────────────────────────
    narrative   = None
    action_items = []
    try:
        from services.anthropic_service import AnthropicService

        trade_count = stats.get('total_trades', imported)
        win_rate    = stats.get('win_rate', 0)
        wins        = stats.get('wins', 0)
        losses      = stats.get('losses', 0)
        total_pnl   = stats.get('total_pnl', 0)
        rr          = stats.get('risk_reward', 0)
        avg_win     = stats.get('avg_win', 0)
        avg_loss    = stats.get('avg_loss', 0)
        overall_score = full.get('score', 50)

        issue_lines = '\n'.join(
            f"  ⚠ {iss['label']} ({iss['severity']}): {iss['insight']}"
            for iss in active_issues[:8]
        ) or '  ✓ No major issues detected'

        rc_text = ''
        if root_cause:
            rc = root_cause.get('root_cause', {})
            rc_text = (
                f"\nROOT CAUSE: {rc.get('label', '')} — {rc.get('detail', '')}"
                f"\nFIX: {root_cause.get('fix_priority', '')}"
                f"\nUPSIDE: {root_cause.get('potential_upside', '')}"
            )

        psych_text = '\n'.join(
            f"  {k}: {v.get('narrative', '')} | Self-check: {v.get('self_awareness', '')}"
            for k, v in psych_narr.items()
        )

        prompt = (
            f"You are analysing the trading psychology of an Indian retail trader. Format: {fmt_label}\n\n"
            f"SCORE: {overall_score}/100 ({full.get('score_label', '')}) · Archetype: {personality['type'] if personality else 'Unknown'}\n"
            f"STATS: {trade_count} trades · {win_rate}% win rate ({wins}W/{losses}L) · P&L ₹{total_pnl:,.0f} · R:R {rr}:1 · Avg win ₹{avg_win:,.0f} · Avg loss ₹{avg_loss:,.0f}\n"
            f"SCORE BREAKDOWN: Discipline {score_bd.get('discipline','?')}/100 · Risk {score_bd.get('risk','?')}/100 · Timing {score_bd.get('timing','?')}/100 · Psychology {score_bd.get('psychology','?')}/100\n"
            f"\nDETECTED ISSUES:\n{issue_lines}"
            f"\n{rc_text}"
            + (f"\n\nDEEP PSYCHOLOGY:\n{psych_text}" if psych_text else '')
            + f"\n\nYour message: {message}" if message else ''
            + "\n\nWrite a focused 4-paragraph trading psychology report:\n"
            "Para 1 (Profile): Their archetype and dominant emotional pattern.\n"
            "Para 2 (Damage): The specific biases hurting P&L — use actual numbers.\n"
            "Para 3 (Root Cause): The core behavioural loop underneath the symptoms.\n"
            "Para 4 (Actions): 3–5 specific, ranked actions for Indian markets (F&O context if relevant).\n"
            "Use 'you'. Direct, data-driven, no headers, no bullets. Plain paragraphs. Max 380 words."
        )

        svc  = AnthropicService()
        resp = svc.chat(
            messages=[{'role': 'user', 'content': prompt}],
            system="You are a trading psychology expert for Indian retail traders. Blunt, specific, data-driven. Never generic. Never sugarcoat.",
            max_tokens=800,
            temperature=0.35,
        )
        narrative = resp.get('content', '').strip()

        # Action items ranked by severity
        _action_map = {
            'revenge_trading':     'Enforce a 30-minute no-trade cooldown after every loss.',
            'overtrading':         'Hard cap: 3 trades per day maximum. Set this in your broker app.',
            'loss_aversion':       'Write your stop-loss price before you enter. Close without negotiating when hit.',
            'profit_booking':      'Trail stops to entry on a 1R move — let the market take you out.',
            'tilt':                'Two consecutive losses = stop for the day. Log out, come back tomorrow.',
            'overconfidence':      'After 3 consecutive wins, cap position size to 1% of capital.',
            'fomo':                'After 3 consecutive wins, cap position size to 1% of capital.',
            'panic_selling':       'Hide your P&L view during market hours — evaluate by thesis, not pain.',
            'time_of_day':         'Only trade during your highest win-rate hours (see timing analysis above).',
            'drawdown_sensitivity': 'Stop trading when the day\'s drawdown hits 2% of capital.',
            'position_sizing':     'Use a fixed-risk calculator: risk exactly 1–2% of capital per trade.',
            'leverage_risk':       'Immediately halve your F&O lot count — one bad trade can be unrecoverable.',
            'behavioral_drift':    'Write your trading rules down. Read them every morning before market open.',
        }
        seen = set()
        for iss in active_issues:
            k = iss['key']
            if k in _action_map and k not in seen:
                action_items.append(_action_map[k])
                seen.add(k)

    except Exception as e:
        logger.error(f'Psychology inline AI error: {e}')

    # ── 4. Build card payload ─────────────────────────────────────────────────
    card_data = {
        'imported':       imported,
        'format':         fmt_label,
        'score':          full.get('score', 50),
        'score_label':    full.get('score_label', ''),
        'score_color':    full.get('score_color', '#6b7280'),
        'personality':    personality,
        'trade_count':    stats.get('total_trades', imported),
        'win_rate':       stats.get('win_rate', 0),
        'wins':           stats.get('wins', 0),
        'losses':         stats.get('losses', 0),
        'total_pnl':      stats.get('total_pnl', 0),
        'rr':             stats.get('risk_reward', 0),
        'avg_win':        stats.get('avg_win', 0),
        'avg_loss':       stats.get('avg_loss', 0),
        'score_breakdown': score_bd,
        'categories':     cat_summary,
        'active_issues':  active_issues,
        'root_cause':     root_cause,
        'psych_narratives': {
            k: {'narrative': v.get('narrative', ''), 'self_awareness': v.get('self_awareness', '')}
            for k, v in psych_narr.items()
        },
        'by_hour':        full.get('by_hour', []),
        'by_day':         full.get('by_day', []),
        'by_symbol':      full.get('by_symbol', []),
        'narrative':      narrative,
        'action_items':   action_items,
        'correlations':   correlations[:3],
        'errors':         errors[:3] if errors else [],
    }

    intro = f"I've analysed **{imported} trades** from your {fmt_label}."
    if errors:
        intro += f" ({len(errors)} rows skipped due to parse errors.)"

    return {
        'card_type': 'psychology',
        'content':   intro,
        'card_data': card_data,
    }


@chat_bp.route('/chat/upload', methods=['POST'])
@login_required
def chat_upload():
    """Handle file + optional text message from the chat composer."""
    import base64
    import io

    try:
        file = request.files.get('file')
        message = (request.form.get('message') or '').strip()
        session_id = request.form.get('session_id') or ''

        if not file or not file.filename:
            return jsonify({'error': 'No file provided'}), 400

        # Rate-limit check
        today_usage = _get_today_usage(current_user.id)
        daily_limit = _get_daily_limit(current_user)
        if today_usage >= daily_limit:
            return jsonify({'error': f"Daily limit reached ({daily_limit} questions). Upgrade for more."}), 429

        filename   = file.filename.lower()
        mime_type  = file.content_type or ''
        file_bytes = file.read()

        conv = _get_or_create_session(current_user.id, session_id or None)

        # ── Helper: save & return ─────────────────────────────────────────
        def _save_and_return(user_label, assistant_result):
            if not conv.title:
                conv.title = _auto_title(user_label)
            user_msg = ChatMessage(
                conversation_id=conv.id, user_id=current_user.id,
                message_type='user', content=user_label, tenant_id='live',
            )
            db.session.add(user_msg)
            db.session.flush()
            assist_msg = ChatMessage(
                conversation_id=conv.id, user_id=current_user.id,
                message_type='assistant',
                content=assistant_result.get('content', ''),
                tenant_id='live',
            )
            assist_msg.set_context_json(assistant_result)
            db.session.add(assist_msg)
            conv.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({
                'session_id': conv.session_id,
                'card_type': assistant_result.get('card_type', 'prose'),
                'content': assistant_result.get('content', ''),
                'card_data': assistant_result.get('card_data'),
                'intent': assistant_result.get('intent'),
            })

        from services.anthropic_service import AnthropicService
        ai = AnthropicService()

        # ── CSV — detect format and route to the right handler ──────────────
        if filename.endswith('.csv'):
            from services.dhan_csv_importer import is_zerodha_csv, is_dhan_csv
            raw_text   = file_bytes.decode('utf-8-sig', errors='replace')
            first_line = raw_text.split('\n')[0]
            sample_hdr = [c.strip().strip('"').lower() for c in first_line.split(',')]

            if is_zerodha_csv(sample_hdr) or is_dhan_csv(sample_hdr):
                # Holdings CSV (Zerodha / Dhan / Groww) → import into portfolio
                try:
                    result = _import_holdings_from_chat(file_bytes, current_user)
                except Exception as exc:
                    logger.error(f'chat holdings import error: {exc}', exc_info=True)
                    result = {
                        'card_type': 'prose',
                        'content':   f'Holdings import failed: {exc}. Please try the Portfolio import page instead.',
                    }
            else:
                # Trade-history CSV (Dhan P&L, Zerodha Trade Book, etc.) → psychology
                result = _run_psychology_analysis_inline(
                    file_bytes=file_bytes,
                    filename=file.filename,
                    message=message,
                    user=current_user,
                )
            return _save_and_return(f'📊 {file.filename}', result)

        # ── Images — Claude vision ────────────────────────────────────────
        is_image = mime_type.startswith('image/') or any(filename.endswith(x) for x in ('.png', '.jpg', '.jpeg', '.webp', '.gif'))
        if is_image:
            MAX_IMG_BYTES = 4 * 1024 * 1024  # 4 MB hard limit
            if len(file_bytes) > MAX_IMG_BYTES:
                result = {'card_type': 'prose', 'content': 'Image is too large (max 4 MB). Please resize and try again.'}
                return _save_and_return(f'[Image: {file.filename}]', result)

            b64 = base64.standard_b64encode(file_bytes).decode()
            img_media = mime_type if mime_type.startswith('image/') else 'image/jpeg'

            user_text = message or 'Analyse this image in the context of Indian stock markets or trading.'
            vision_messages = [{
                'role': 'user',
                'content': [
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': img_media, 'data': b64}},
                    {'type': 'text',  'text': user_text},
                ]
            }]
            system_prompt = (
                "You are Capulse, an AI research assistant for Indian stock markets (NSE/BSE). "
                "Analyse images related to charts, stock quotes, trading screenshots, F&O data, "
                "mutual fund statements, or any financial document. Be concise and specific. "
                "If the image is not finance-related, briefly say so and offer to help with markets."
            )
            try:
                ai_resp = ai.chat(vision_messages, system=system_prompt, max_tokens=600, temperature=0.3)
                content = ai_resp.get('content', 'I could not analyse this image. Please try again.')
            except Exception as vis_err:
                logger.error(f'Vision error: {vis_err}')
                content = 'Image analysis failed. Please try again or describe what you see in text.'

            user_label = f'[Image: {file.filename}]{(" — " + message) if message else ""}'
            result = {'card_type': 'prose', 'content': content}
            return _save_and_return(user_label, result)

        # ── PDF — extract text, forward to chat ──────────────────────────
        if filename.endswith('.pdf') or mime_type == 'application/pdf':
            try:
                import pdfplumber
                pdf_text = ''
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    for page in pdf.pages[:6]:  # cap at 6 pages
                        pdf_text += (page.extract_text() or '') + '\n'
                pdf_text = pdf_text[:6000].strip()
            except ImportError:
                # pdfplumber not installed — fall back to telling the user
                pdf_text = ''
            except Exception as pdf_err:
                logger.warning(f'PDF extract error: {pdf_err}')
                pdf_text = ''

            if not pdf_text:
                result = {
                    'card_type': 'prose',
                    'content': (
                        f'I received your PDF **{file.filename}**. '
                        'PDF text extraction is not available right now. '
                        'Please paste the relevant text into the chat and I\'ll analyse it for you.'
                        + (f'\n\nYou also asked: *{message}*' if message else '')
                    )
                }
                return _save_and_return(f'[PDF: {file.filename}]', result)

            user_text = message or f'Analyse this document in the context of Indian markets:\n\n{pdf_text[:3000]}'
            from services.capulse_router import route_message
            result = route_message(message=user_text, user_id=current_user.id, conversation_history=[])
            return _save_and_return(f'[PDF: {file.filename}]', result)

        # ── Unsupported format ────────────────────────────────────────────
        result = {
            'card_type': 'prose',
            'content': f'Unsupported file type: **{file.filename}**. Supported: images (PNG/JPG/WEBP), PDF, CSV.'
        }
        return _save_and_return(file.filename, result)

    except Exception as e:
        logger.error(f'Chat upload error: {e}', exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': 'Upload failed. Please try again.'}), 500


@chat_bp.route('/chat/sessions')
@login_required
def chat_sessions():
    """API: Return user's chat sessions as JSON."""
    sessions = _get_user_sessions(current_user.id)
    return jsonify([{
        'session_id': s.session_id,
        'title': s.title or 'New conversation',
        'updated_at': s.updated_at.isoformat() if s.updated_at else None,
    } for s in sessions])


@chat_bp.route('/chat/session/<session_id>/delete', methods=['POST'])
@login_required
def delete_session(session_id):
    """Soft-delete a chat session."""
    try:
        conv = ChatConversation.query.filter_by(
            session_id=session_id, user_id=current_user.id
        ).first()
        if conv:
            conv.is_active = False
            db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/how-it-works')
def how_it_works():
    """How Capulse works — public page."""
    sessions = _get_user_sessions(current_user.id) if current_user.is_authenticated else []
    today_usage = _get_today_usage(current_user.id) if current_user.is_authenticated else 0
    return render_template(
        'capulse_how_it_works.html',
        active_page='how-it-works',
        sessions=sessions,
        today_usage=today_usage,
        current_session_id=None,
    )
