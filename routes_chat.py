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

from app import db
from models import ChatConversation, ChatMessage

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)

DAILY_LIMIT_FREE = 5
DAILY_LIMIT_PAID = 200


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
                'content': f"Daily limit reached ({daily_limit} questions). Upgrade to Capulse Plus for ~150–200 questions per day.",
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

        # ── CSV — route to psychology / portfolio ─────────────────────────
        if filename.endswith('.csv'):
            from routes_behaviour import _detect_format
            try:
                text_content = file_bytes.decode('utf-8-sig', errors='replace')
                fmt = _detect_format(text_content)
                format_labels = {
                    'dhan_pnl': 'Dhan P&L', 'dhan_trades': 'Dhan Trade History',
                    'zerodha_trades': 'Zerodha Trade Book', 'zerodha_pnl': 'Zerodha P&L',
                }
                fmt_label = format_labels.get(fmt, 'CSV')
                user_label = f'[Uploaded CSV: {file.filename}]{(" — " + message) if message else ""}'
                row_count  = max(0, text_content.count('\n') - 1)
                reply_text = (
                    f"I can see your **{fmt_label}** file ({row_count} trades). "
                    f"To run a full Psychology Analysis on these trades, open the "
                    f"[Trading Psychology](/trading-psychology) page and upload this file there — "
                    f"you'll get pattern detection (revenge trading, overtrading, loss aversion, etc.) "
                    f"and a personalised AI report.\n\n"
                    + (f"You also asked: *{message}*\n\nI can answer that separately once you've imported the trades." if message else
                       "You can also use [Portfolio Upload](/portfolio) to import holdings.")
                )
            except Exception as csv_err:
                logger.warning(f'CSV parse error: {csv_err}')
                reply_text = f'I received your CSV file but could not parse it. Please check the format and try again via the [Trading Psychology](/trading-psychology) page.'
            result = {'card_type': 'prose', 'content': reply_text}
            return _save_and_return(f'[CSV: {file.filename}]', result)

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
