"""
Partner webhook dispatcher.

Called by:
  • services/fno_monitor.py  — every time an MVLA F&O signal fires
  • services/iscore_alert_dispatcher.py — when a subscribed stock's I-Score
    crosses a threshold or moves more than the partner's delta_threshold

Delivery model:
  • POST JSON to partner.webhook_url
  • Header  X-TC-Signature: sha256=<hmac of raw body using webhook_secret>
  • Header  X-TC-Event: fno.signal | iscore.update
  • 5 second timeout, single retry on 5xx, all attempts logged in api_alert_log
"""
import hmac
import json
import logging
import threading
import time
from datetime import datetime
from hashlib import sha256
from typing import Any

import requests

from app import db
from models_partner_api import ApiAlertLog, ApiSubscription

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_S = 5
WEBHOOK_RETRIES   = 1


def _sign(body: bytes, secret: str | None) -> str | None:
    if not secret:
        return None
    mac = hmac.new(secret.encode('utf-8'), body, sha256).hexdigest()
    return f"sha256={mac}"


def _post_one(app, partner_id: int, subscription_id: int | None,
              engine: str, symbol: str, payload: dict[str, Any],
              webhook_url: str, webhook_secret: str | None):
    """Runs in a background thread inside the Flask app context."""
    body = json.dumps(payload, default=str).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent':   'Capulse-Webhook/1.0',
        'X-TC-Event':   f"{engine}.signal" if engine == 'fno' else 'iscore.update',
    }
    sig = _sign(body, webhook_secret)
    if sig:
        headers['X-TC-Signature'] = sig

    last_err = None
    last_status = None
    for attempt in range(1 + WEBHOOK_RETRIES):
        try:
            r = requests.post(webhook_url, data=body, headers=headers, timeout=WEBHOOK_TIMEOUT_S)
            last_status = r.status_code
            if 200 <= r.status_code < 300:
                last_err = None
                break
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code < 500:
                break  # don't retry 4xx
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
        time.sleep(0.5)

    with app.app_context():
        try:
            log = ApiAlertLog(
                partner_id=partner_id,
                subscription_id=subscription_id,
                engine=engine,
                symbol=symbol,
                score=payload.get('score') or payload.get('confidence'),
                tier=payload.get('tier') or payload.get('recommendation'),
                channel='webhook',
                status='sent' if last_err is None else 'failed',
                http_status=last_status,
                error=last_err,
                payload_json=body.decode('utf-8'),
                delivered_at=datetime.utcnow() if last_err is None else None,
            )
            db.session.add(log)

            if last_err is None and subscription_id:
                sub = db.session.get(ApiSubscription, subscription_id)
                if sub:
                    sub.last_alert_at = datetime.utcnow()
                    if 'score' in payload:
                        sub.last_score = float(payload['score'])
                    if 'tier' in payload:
                        sub.last_tier = str(payload['tier'])

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Webhook log persist failed: {e}", exc_info=True)


def _get_app():
    """Return the Flask app singleton without import-time side effects."""
    from app import app as flask_app
    return flask_app


def dispatch_event(engine: str, symbol: str, payload: dict[str, Any],
                   score: float | None = None) -> int:
    """
    Fan out an event to every active subscription matching (engine, symbol)
    where score ≥ subscription.min_confidence. Returns the number of dispatches.

    Safe to call from any thread; webhook POSTs run in background threads so the
    caller (e.g. the F&O monitor scan loop) never blocks.
    """
    app = _get_app()
    sent = 0
    try:
        with app.app_context():
            score_val = float(score) if score is not None else float(payload.get('score') or payload.get('confidence') or 0)

            subs = (ApiSubscription.query
                    .filter_by(engine=engine, symbol=symbol.upper(), is_active=True)
                    .all())

            for sub in subs:
                if score_val < (sub.min_confidence or 0):
                    continue
                partner = sub.partner
                if not partner or not partner.is_active or not partner.webhook_url:
                    continue

                # Enrich payload with subscription/partner context for the receiver
                p = dict(payload)
                p.setdefault('engine', engine)
                p.setdefault('symbol', symbol.upper())
                p.setdefault('score', score_val)
                p.setdefault('timestamp', datetime.utcnow().isoformat() + 'Z')
                p['subscription_id'] = sub.id

                t = threading.Thread(
                    target=_post_one,
                    args=(app, partner.id, sub.id, engine, symbol.upper(),
                          p, partner.webhook_url, partner.webhook_secret),
                    daemon=True,
                )
                t.start()
                sent += 1
    except Exception as e:
        logger.error(f"dispatch_event({engine},{symbol}) failed: {e}", exc_info=True)

    if sent:
        logger.info(f"📡 Dispatched {engine} alert for {symbol} (score={score}) to {sent} partner(s)")
    return sent
