"""
Behavioural AI Routes — Target Capital
Serves the Behavioural Insights dashboard and pre-trade check API.
"""
from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from app import app, db
import logging

logger = logging.getLogger(__name__)


@app.route('/dashboard/behavioural-insights')
@login_required
def behavioural_insights():
    try:
        from services.behaviour_engine import BehaviourEngine
        engine   = BehaviourEngine(current_user.id, current_user.tenant_id or 'live')
        analysis = engine.get_full_analysis()
    except Exception as e:
        logger.error(f"Behavioural analysis error: {e}")
        analysis = None

    return render_template(
        'dashboard/behavioural_insights.html',
        analysis=analysis,
        page_title='Behavioural Insights',
    )


@app.route('/api/behaviour/pre-trade-check', methods=['POST'])
@login_required
def behaviour_pre_trade_check():
    """Called from Trade Now page before placing a trade."""
    try:
        from services.behaviour_engine import BehaviourEngine
        engine   = BehaviourEngine(current_user.id, current_user.tenant_id or 'live')
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
    alert.acknowledged    = True
    alert.acknowledged_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})
