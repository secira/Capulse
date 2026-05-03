"""
B2B Partner API — exposes I-Score and F&O (MVLA) engines as a SaaS.

All endpoints are mounted under `/api/partner/v1` and require a Bearer API key
EXCEPT signup (admin-only) and the docs page.

    GET  /api/partner/v1/docs                         (public, HTML)
    POST /api/partner/v1/signup                       (admin-only — creates partner)
    GET  /api/partner/v1/me                           (current partner profile)

    GET  /api/partner/v1/fno/signals/live?index=NIFTY (live MVLA signal)
    GET  /api/partner/v1/fno/signals/history?index=NIFTY&days=7

    GET  /api/partner/v1/iscore/<symbol>?asset_type=stocks
    POST /api/partner/v1/iscore/batch                 {"symbols": [...]}

    GET  /api/partner/v1/subscriptions
    POST /api/partner/v1/subscriptions                {engine,symbol,min_confidence,...}
    DELETE /api/partner/v1/subscriptions/<id>

    GET  /api/partner/v1/alerts?limit=50              (delivery log)
"""
import json
import logging
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, render_template_string, request
from flask_login import current_user, login_required

from app import db
from models_partner_api import ApiAlertLog, ApiPartner, ApiSubscription
from services.partner_auth import generate_api_key, partner_api_key_required

logger = logging.getLogger(__name__)

partner_api = Blueprint('partner_api', __name__, url_prefix='/api/partner/v1')

VALID_ENGINES = {'fno', 'iscore'}
VALID_FNO_INDICES = {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX'}


# ── Admin-only signup ────────────────────────────────────────────────────────

@partner_api.route('/signup', methods=['POST'])
@login_required
def signup_partner():
    """
    Admin-only. Creates a partner and returns the raw API key ONCE.
    Body: {name, contact_email, organisation?, plan?, webhook_url?, webhook_secret?}
    """
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'success': False, 'error': 'Admin access required',
                        'code': 'FORBIDDEN'}), 403

    data = request.get_json(silent=True) or {}
    name  = (data.get('name') or '').strip()
    email = (data.get('contact_email') or '').strip().lower()
    if not name or not email:
        return jsonify({'success': False, 'error': 'name and contact_email are required',
                        'code': 'INVALID_REQUEST'}), 400

    raw_key, prefix, hashed = generate_api_key()
    partner = ApiPartner(
        name=name,
        contact_email=email,
        organisation=(data.get('organisation') or '').strip() or None,
        api_key_prefix=prefix,
        api_key_hash=hashed,
        plan=(data.get('plan') or 'basic').lower(),
        rate_limit_per_min=int(data.get('rate_limit_per_min') or 60),
        webhook_url=(data.get('webhook_url') or '').strip() or None,
        webhook_secret=(data.get('webhook_secret') or '').strip() or None,
        is_active=True,
        tenant_id='live',
    )
    try:
        db.session.add(partner)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Partner signup failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Could not create partner',
                        'code': 'DB_ERROR'}), 500

    return jsonify({
        'success': True,
        'partner': partner.to_dict(include_secret_meta=True),
        'api_key': raw_key,
        'note': 'Store this key now — it will not be shown again.',
    }), 201


# ── Partner self-service ─────────────────────────────────────────────────────

@partner_api.route('/me', methods=['GET'])
@partner_api_key_required
def me():
    return jsonify({'success': True, 'partner': g.partner.to_dict(include_secret_meta=True)})


@partner_api.route('/me', methods=['PATCH'])
@partner_api_key_required
def update_me():
    data = request.get_json(silent=True) or {}
    p = g.partner
    if 'webhook_url' in data:
        p.webhook_url = (data['webhook_url'] or '').strip() or None
    if 'webhook_secret' in data:
        p.webhook_secret = (data['webhook_secret'] or '').strip() or None
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e), 'code': 'DB_ERROR'}), 500
    return jsonify({'success': True, 'partner': p.to_dict(include_secret_meta=True)})


# ── F&O endpoints (MVLA engine) ──────────────────────────────────────────────

@partner_api.route('/fno/signals/live', methods=['GET'])
@partner_api_key_required
def fno_live():
    index_id = (request.args.get('index') or 'NIFTY').upper()
    if index_id not in VALID_FNO_INDICES:
        return jsonify({'success': False, 'error': f'Unsupported index. Use one of {sorted(VALID_FNO_INDICES)}',
                        'code': 'INVALID_INDEX'}), 400
    try:
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine(index_id=index_id)
        analysis = engine.generate_analysis()
        return jsonify({'success': True, 'index': index_id, 'analysis': analysis,
                        'timestamp': datetime.utcnow().isoformat() + 'Z'})
    except Exception as e:
        logger.error(f"fno_live({index_id}) failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'code': 'ENGINE_ERROR'}), 500


@partner_api.route('/fno/signals/history', methods=['GET'])
@partner_api_key_required
def fno_history():
    index_id = (request.args.get('index') or 'NIFTY').upper()
    if index_id not in VALID_FNO_INDICES:
        return jsonify({'success': False, 'error': 'Unsupported index',
                        'code': 'INVALID_INDEX'}), 400
    days  = max(1, min(int(request.args.get('days') or 7), 30))
    limit = max(1, min(int(request.args.get('limit') or 100), 500))
    cutoff = datetime.utcnow() - timedelta(days=days)
    try:
        rows = db.session.execute(
            db.text("""
                SELECT id, created_at AS signal_time, signal_type, direction AS trade_direction,
                       confidence, confidence_grade, entry_mode, atm_strike, spot_price,
                       trade_code, outcome, exit_spot, exit_time, alert_sent, data_source
                  FROM fno_signal_history
                 WHERE index_id = :idx AND created_at >= :cutoff
              ORDER BY created_at DESC
                 LIMIT :lim
            """),
            {'idx': index_id, 'cutoff': cutoff, 'lim': limit},
        ).mappings().all()
        signals = [dict(r) for r in rows]
        for s in signals:
            for k in ('signal_time', 'exit_time'):
                if s.get(k) and not isinstance(s[k], str):
                    s[k] = s[k].isoformat()
        return jsonify({'success': True, 'index': index_id, 'count': len(signals),
                        'signals': signals})
    except Exception as e:
        logger.error(f"fno_history failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'code': 'DB_ERROR'}), 500


# ── I-Score endpoints ────────────────────────────────────────────────────────

def _compute_iscore(symbol: str, asset_type: str = 'stocks') -> dict:
    from services.langgraph_iscore_engine import LangGraphIScoreEngine
    engine = LangGraphIScoreEngine()
    return engine.analyze(asset_type=asset_type, symbol=symbol.upper(),
                          user_id=1, asset_name=symbol.upper())


@partner_api.route('/iscore/<symbol>', methods=['GET'])
@partner_api_key_required
def iscore_one(symbol):
    asset_type = (request.args.get('asset_type') or 'stocks').lower()
    try:
        result = _compute_iscore(symbol, asset_type)
        return jsonify({'success': True, 'symbol': symbol.upper(), 'asset_type': asset_type,
                        'result': result})
    except Exception as e:
        logger.error(f"iscore_one({symbol}) failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'code': 'ENGINE_ERROR'}), 500


@partner_api.route('/iscore/batch', methods=['POST'])
@partner_api_key_required
def iscore_batch():
    data = request.get_json(silent=True) or {}
    symbols = data.get('symbols') or []
    asset_type = (data.get('asset_type') or 'stocks').lower()
    if not isinstance(symbols, list) or not symbols:
        return jsonify({'success': False, 'error': 'symbols must be a non-empty list',
                        'code': 'INVALID_REQUEST'}), 400
    if len(symbols) > 25:
        return jsonify({'success': False, 'error': 'Max 25 symbols per batch',
                        'code': 'BATCH_TOO_LARGE'}), 400

    out = []
    for sym in symbols:
        try:
            r = _compute_iscore(sym, asset_type)
            out.append({'symbol': sym.upper(), 'success': True, 'result': r})
        except Exception as e:
            out.append({'symbol': sym.upper(), 'success': False, 'error': str(e)})
    return jsonify({'success': True, 'count': len(out), 'results': out})


# ── Subscriptions ────────────────────────────────────────────────────────────

@partner_api.route('/subscriptions', methods=['GET'])
@partner_api_key_required
def list_subscriptions():
    subs = ApiSubscription.query.filter_by(partner_id=g.partner.id).all()
    return jsonify({'success': True, 'count': len(subs),
                    'subscriptions': [s.to_dict() for s in subs]})


@partner_api.route('/subscriptions', methods=['POST'])
@partner_api_key_required
def create_subscription():
    data = request.get_json(silent=True) or {}
    engine = (data.get('engine') or '').lower()
    symbol = (data.get('symbol') or '').upper().strip()
    if engine not in VALID_ENGINES or not symbol:
        return jsonify({'success': False, 'error': 'engine must be fno|iscore and symbol is required',
                        'code': 'INVALID_REQUEST'}), 400
    if engine == 'fno' and symbol not in VALID_FNO_INDICES:
        return jsonify({'success': False, 'error': f'F&O symbol must be one of {sorted(VALID_FNO_INDICES)}',
                        'code': 'INVALID_SYMBOL'}), 400

    existing = ApiSubscription.query.filter_by(
        partner_id=g.partner.id, engine=engine, symbol=symbol).first()
    if existing:
        # Idempotent update of mutable fields
        if 'min_confidence' in data:
            existing.min_confidence = int(data['min_confidence'])
        if 'delta_threshold' in data:
            existing.delta_threshold = int(data['delta_threshold'])
        if 'channels' in data:
            chans = data['channels']
            existing.channels = ','.join(chans) if isinstance(chans, list) else str(chans)
        if 'is_active' in data:
            existing.is_active = bool(data['is_active'])
        sub = existing
    else:
        chans = data.get('channels') or 'webhook'
        sub = ApiSubscription(
            partner_id=g.partner.id,
            engine=engine,
            symbol=symbol,
            min_confidence=int(data.get('min_confidence') or (75 if engine == 'fno' else 60)),
            delta_threshold=int(data.get('delta_threshold') or 5),
            channels=','.join(chans) if isinstance(chans, list) else str(chans),
            is_active=bool(data.get('is_active', True)),
        )
        db.session.add(sub)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e), 'code': 'DB_ERROR'}), 500
    return jsonify({'success': True, 'subscription': sub.to_dict()}), 201


@partner_api.route('/subscriptions/<int:sub_id>', methods=['DELETE'])
@partner_api_key_required
def delete_subscription(sub_id):
    sub = ApiSubscription.query.filter_by(id=sub_id, partner_id=g.partner.id).first()
    if not sub:
        return jsonify({'success': False, 'error': 'Subscription not found',
                        'code': 'NOT_FOUND'}), 404
    try:
        db.session.delete(sub)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e), 'code': 'DB_ERROR'}), 500
    return jsonify({'success': True})


# ── Alert delivery log ───────────────────────────────────────────────────────

@partner_api.route('/alerts', methods=['GET'])
@partner_api_key_required
def list_alerts():
    limit  = max(1, min(int(request.args.get('limit') or 50), 200))
    engine = request.args.get('engine')
    q = ApiAlertLog.query.filter_by(partner_id=g.partner.id)
    if engine in VALID_ENGINES:
        q = q.filter_by(engine=engine)
    rows = q.order_by(ApiAlertLog.created_at.desc()).limit(limit).all()
    return jsonify({'success': True, 'count': len(rows),
                    'alerts': [r.to_dict() for r in rows]})


# ── Public docs page ─────────────────────────────────────────────────────────

_DOCS = """
<!doctype html><meta charset=utf-8>
<title>Target Capital — Partner API v1</title>
<style>
 body{font-family:'Poppins',system-ui,sans-serif;max-width:880px;margin:40px auto;padding:0 20px;color:#111;background:#fff}
 h1,h2{font-weight:600}h1{border-bottom:2px solid #00091a;padding-bottom:8px}
 code,pre{background:#f4f5f7;border-radius:6px;font-family:ui-monospace,Menlo,monospace}
 code{padding:2px 6px}pre{padding:14px;overflow:auto;font-size:13px;line-height:1.45}
 .pill{display:inline-block;background:#00091a;color:#fff;padding:2px 10px;border-radius:999px;font-size:12px;margin-right:6px}
 table{border-collapse:collapse;width:100%}td,th{border:1px solid #e3e5e8;padding:8px 10px;text-align:left}
</style>
<h1>Target Capital — Partner API v1</h1>
<p>Sell I-Score and MVLA F&amp;O signals to your customers. All endpoints under
<code>/api/partner/v1</code>. Authenticate with
<code>Authorization: Bearer &lt;api_key&gt;</code>.</p>

<h2>Endpoints</h2>
<table>
 <tr><th>Method</th><th>Path</th><th>Purpose</th></tr>
 <tr><td>GET</td><td>/me</td><td>Your partner profile</td></tr>
 <tr><td>GET</td><td>/fno/signals/live?index=NIFTY</td><td>Current MVLA signal + score breakdown</td></tr>
 <tr><td>GET</td><td>/fno/signals/history?index=NIFTY&amp;days=7</td><td>Recent signals from monitor</td></tr>
 <tr><td>GET</td><td>/iscore/&lt;symbol&gt;</td><td>Full I-Score with components &amp; explainability</td></tr>
 <tr><td>POST</td><td>/iscore/batch</td><td>Up to 25 symbols at once</td></tr>
 <tr><td>GET / POST</td><td>/subscriptions</td><td>List or create push subscription</td></tr>
 <tr><td>DELETE</td><td>/subscriptions/&lt;id&gt;</td><td>Unsubscribe</td></tr>
 <tr><td>GET</td><td>/alerts</td><td>Webhook delivery log</td></tr>
</table>

<h2>Webhook payload (F&amp;O)</h2>
<pre>POST &lt;your_webhook_url&gt;
X-TC-Event: fno.signal
X-TC-Signature: sha256=&lt;hmac of body using your webhook_secret&gt;

{
  "engine": "fno", "symbol": "NIFTY", "score": 82, "tier": "HIGH",
  "trade_direction": "BULLISH", "entry_mode": "BREAKOUT",
  "atm_strike": 22500, "entry_price": 120, "sl": 105, "target": 145,
  "subscription_id": 7, "timestamp": "2026-05-03T05:21:00Z"
}</pre>

<h2>Webhook payload (I-Score)</h2>
<pre>POST &lt;your_webhook_url&gt;
X-TC-Event: iscore.update

{
  "engine": "iscore", "symbol": "RELIANCE", "score": 72.4,
  "tier": "BUY", "recommendation": "BUY",
  "components": { "...": "..." },
  "subscription_id": 12, "timestamp": "2026-05-03T05:30:00Z"
}</pre>

<h2>Subscribe to a symbol</h2>
<pre>POST /api/partner/v1/subscriptions
{ "engine": "iscore", "symbol": "RELIANCE",
  "min_confidence": 65, "delta_threshold": 5,
  "channels": ["webhook"] }</pre>

<p style="margin-top:32px;color:#666">
<span class="pill">v1</span> Stable. Breaking changes will ship as <code>/v2</code>.</p>
"""


@partner_api.route('/docs', methods=['GET'])
def docs():
    return render_template_string(_DOCS)
