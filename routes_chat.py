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

DAILY_LIMIT_FREE = 20
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
@login_required
def chat_home():
    """Main Capulse chat interface."""
    session_id = request.args.get('session_id')
    sessions = _get_user_sessions(current_user.id)
    today_usage = _get_today_usage(current_user.id)

    messages_history = []
    current_session_id = None

    if session_id:
        conv = ChatConversation.query.filter_by(
            session_id=session_id, user_id=current_user.id
        ).first()
        if conv:
            current_session_id = conv.session_id
            messages_history = ChatMessage.query.filter_by(
                conversation_id=conv.id
            ).order_by(ChatMessage.created_at.asc()).all()

    return render_template(
        'chat.html',
        active_page='chat',
        sessions=sessions,
        today_usage=today_usage,
        messages_history=messages_history,
        current_session_id=current_session_id,
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
