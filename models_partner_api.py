"""
B2B Partner API models — exposes I-Score and F&O engines as a SaaS to brokers
and agencies.

Three tables:
  api_partner       — registered B2B account (API key, plan, webhook URL)
  api_subscription  — what symbols / indices a partner wants alerts for
  api_alert_log     — every webhook delivery attempt (idempotency + audit)
"""
from datetime import datetime
from db_instance import db


class ApiPartner(db.Model):
    __tablename__ = 'api_partner'

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), nullable=False)
    contact_email   = db.Column(db.String(180), nullable=False, index=True)
    organisation    = db.Column(db.String(180), nullable=True)

    # Bearer token. We store only the hash; the raw key is shown once at creation.
    api_key_prefix  = db.Column(db.String(16), nullable=False, index=True)
    api_key_hash    = db.Column(db.String(256), nullable=False)

    # Optional shared secret used to HMAC-sign webhook payloads
    webhook_url     = db.Column(db.String(512), nullable=True)
    webhook_secret  = db.Column(db.String(128), nullable=True)

    plan            = db.Column(db.String(32), nullable=False, default='basic')   # basic | pro | elite
    rate_limit_per_min = db.Column(db.Integer, nullable=False, default=60)

    is_active       = db.Column(db.Boolean, nullable=False, default=True)
    tenant_id       = db.Column(db.String(255), nullable=True, default='live', index=True)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at    = db.Column(db.DateTime, nullable=True)

    subscriptions   = db.relationship('ApiSubscription', backref='partner', lazy='dynamic',
                                      cascade='all, delete-orphan')

    def to_dict(self, include_secret_meta: bool = False) -> dict:
        d = {
            'id': self.id,
            'name': self.name,
            'contact_email': self.contact_email,
            'organisation': self.organisation,
            'plan': self.plan,
            'rate_limit_per_min': self.rate_limit_per_min,
            'is_active': self.is_active,
            'webhook_url': self.webhook_url,
            'api_key_prefix': self.api_key_prefix,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
        }
        if include_secret_meta:
            d['has_webhook_secret'] = bool(self.webhook_secret)
        return d


class ApiSubscription(db.Model):
    __tablename__ = 'api_subscription'

    id            = db.Column(db.Integer, primary_key=True)
    partner_id    = db.Column(db.Integer, db.ForeignKey('api_partner.id'), nullable=False, index=True)

    engine        = db.Column(db.String(16), nullable=False, index=True)   # 'fno' | 'iscore'
    symbol        = db.Column(db.String(64), nullable=False, index=True)   # NIFTY / BANKNIFTY / RELIANCE …

    # F&O: alert when MVLA confidence ≥ this. I-Score: alert when iscore ≥ this.
    min_confidence = db.Column(db.Integer, nullable=False, default=75)

    # I-Score only — fire when score moves by this many points vs. last delivered
    delta_threshold = db.Column(db.Integer, nullable=False, default=5)

    channels      = db.Column(db.String(64), nullable=False, default='webhook')  # csv: webhook,email

    is_active     = db.Column(db.Boolean, nullable=False, default=True)

    # Last value we pushed for this subscription (used for delta + tier-change detection)
    last_score      = db.Column(db.Float, nullable=True)
    last_tier       = db.Column(db.String(32), nullable=True)
    last_alert_at   = db.Column(db.DateTime, nullable=True)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('partner_id', 'engine', 'symbol', name='uq_partner_engine_symbol'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'partner_id': self.partner_id,
            'engine': self.engine,
            'symbol': self.symbol,
            'min_confidence': self.min_confidence,
            'delta_threshold': self.delta_threshold,
            'channels': [c.strip() for c in (self.channels or '').split(',') if c.strip()],
            'is_active': self.is_active,
            'last_score': self.last_score,
            'last_tier': self.last_tier,
            'last_alert_at': self.last_alert_at.isoformat() if self.last_alert_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ApiAlertLog(db.Model):
    __tablename__ = 'api_alert_log'

    id              = db.Column(db.Integer, primary_key=True)
    partner_id      = db.Column(db.Integer, db.ForeignKey('api_partner.id'), nullable=False, index=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('api_subscription.id'), nullable=True, index=True)

    engine          = db.Column(db.String(16), nullable=False)
    symbol          = db.Column(db.String(64), nullable=False, index=True)
    score           = db.Column(db.Float, nullable=True)
    tier            = db.Column(db.String(32), nullable=True)

    channel         = db.Column(db.String(32), nullable=False, default='webhook')
    status          = db.Column(db.String(16), nullable=False, default='pending')  # pending | sent | failed
    http_status     = db.Column(db.Integer, nullable=True)
    error           = db.Column(db.String(512), nullable=True)

    payload_json    = db.Column(db.Text, nullable=True)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    delivered_at    = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        import json as _json
        try:
            payload = _json.loads(self.payload_json) if self.payload_json else None
        except Exception:
            payload = None
        return {
            'id': self.id,
            'partner_id': self.partner_id,
            'subscription_id': self.subscription_id,
            'engine': self.engine,
            'symbol': self.symbol,
            'score': self.score,
            'tier': self.tier,
            'channel': self.channel,
            'status': self.status,
            'http_status': self.http_status,
            'error': self.error,
            'payload': payload,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'delivered_at': self.delivered_at.isoformat() if self.delivered_at else None,
        }
