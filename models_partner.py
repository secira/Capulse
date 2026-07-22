"""
Partner Network models — Capulse
Keeps the partner system completely isolated from the existing user flow.
"""

import uuid
import random
import string
from datetime import datetime
from app import db


def _gen_uuid():
    return str(uuid.uuid4())


def _gen_partner_code(name: str) -> str:
    """Generate a unique referral code like TOP-UDAY25."""
    prefix = "TOP"
    slug = ''.join(c for c in name.upper() if c.isalpha())[:4] or "PART"
    digits = ''.join(random.choices(string.digits, k=2))
    return f"{prefix}-{slug}{digits}"


class Partner(db.Model):
    __tablename__ = 'partners'

    id                   = db.Column(db.String(36), primary_key=True, default=_gen_uuid)
    # Human-readable partner ID shown in the UI, e.g. PTN10234
    partner_display_id   = db.Column(db.String(20), unique=True, nullable=True)
    partner_code         = db.Column(db.String(30), unique=True, nullable=False)  # referral code e.g. TOP-UDAY25
    # Link to the User account so the partner logs in with the same credentials
    user_id              = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, unique=True)

    name                 = db.Column(db.String(200), nullable=False)
    mobile               = db.Column(db.String(15), nullable=False)
    email                = db.Column(db.String(200), nullable=False)

    partner_type         = db.Column(db.String(20), nullable=False, default='individual')
    # individual | broker | pms | trainer

    commission_percentage = db.Column(db.Numeric(5, 2), nullable=False, default=20.00)

    pan_number           = db.Column(db.String(10), nullable=True)
    gst_number           = db.Column(db.String(15), nullable=True)

    kyc_status           = db.Column(db.String(20), nullable=False, default='pending')
    # pending | verified | rejected

    status               = db.Column(db.String(20), nullable=False, default='pending')
    # pending | active | suspended | terminated

    # Banking for payouts
    bank_account_number  = db.Column(db.String(60), nullable=True)   # store encrypted in prod
    bank_ifsc            = db.Column(db.String(11), nullable=True)
    upi_id               = db.Column(db.String(100), nullable=True)

    wallet_balance       = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)

    # For sub-partner / broker hierarchy
    parent_partner_id    = db.Column(db.String(36), db.ForeignKey('partners.id'), nullable=True)

    # Admin notes
    admin_notes          = db.Column(db.Text, nullable=True)

    created_at           = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at           = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user             = db.relationship('User', backref=db.backref('partner_profile', uselist=False))
    referrals        = db.relationship('TraderReferral', backref='partner', lazy='dynamic',
                                       foreign_keys='TraderReferral.partner_id')
    commissions      = db.relationship('PartnerCommission', backref='partner', lazy='dynamic')
    payout_requests  = db.relationship('PayoutRequest', backref='partner', lazy='dynamic')
    children         = db.relationship('Partner', backref=db.backref('parent', remote_side='Partner.id'))

    def generate_display_id(self):
        num = random.randint(10000, 99999)
        self.partner_display_id = f"PTN{num}"

    def referral_link(self):
        return f"https://capulse.tech/register?ref={self.partner_code}"

    @property
    def total_referred(self):
        return self.referrals.count()

    @property
    def paid_referred(self):
        return self.commissions.filter(
            PartnerCommission.status.in_(['approved', 'paid'])
        ).with_entities(PartnerCommission.trader_id).distinct().count()

    @property
    def total_commission_earned(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(PartnerCommission.commission_amount)).filter(
            PartnerCommission.partner_id == self.id,
            PartnerCommission.status.in_(['approved', 'paid'])
        ).scalar()
        return float(result or 0)

    @property
    def pending_commission(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(PartnerCommission.commission_amount)).filter(
            PartnerCommission.partner_id == self.id,
            PartnerCommission.status == 'pending_hold'
        ).scalar()
        return float(result or 0)

    @property
    def paid_out(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(PayoutRequest.amount)).filter(
            PayoutRequest.partner_id == self.id,
            PayoutRequest.status == 'paid'
        ).scalar()
        return float(result or 0)

    @property
    def total_revenue_generated(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(PartnerCommission.gross_amount)).filter(
            PartnerCommission.partner_id == self.id,
            PartnerCommission.status != 'cancelled'
        ).scalar()
        return float(result or 0)


class TraderReferral(db.Model):
    """Links a trader (User) to the partner who referred them. One per trader."""
    __tablename__ = 'trader_referrals'

    id                 = db.Column(db.String(36), primary_key=True, default=_gen_uuid)
    trader_id          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    partner_id         = db.Column(db.String(36), db.ForeignKey('partners.id'), nullable=False)
    referral_code      = db.Column(db.String(30), nullable=False)
    referral_locked    = db.Column(db.Boolean, default=True, nullable=False)
    linked_date        = db.Column(db.DateTime, default=datetime.utcnow)
    attribution_source = db.Column(db.String(30), default='signup_code')
    # signup_code | profile_code | broker_bulk_import

    trader = db.relationship('User', backref=db.backref('partner_referral', uselist=False))


class PartnerCommission(db.Model):
    """One row per subscription payment that earns a commission."""
    __tablename__ = 'partner_commissions'

    id                  = db.Column(db.String(36), primary_key=True, default=_gen_uuid)
    partner_id          = db.Column(db.String(36), db.ForeignKey('partners.id'), nullable=False)
    trader_id           = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_id     = db.Column(db.String(100), nullable=True)  # razorpay payment id

    gross_amount        = db.Column(db.Numeric(10, 2), nullable=False)
    gateway_fee         = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    net_amount          = db.Column(db.Numeric(10, 2), nullable=False)
    commission_percent  = db.Column(db.Numeric(5, 2), nullable=False)
    commission_amount   = db.Column(db.Numeric(10, 2), nullable=False)

    status              = db.Column(db.String(20), nullable=False, default='pending_hold')
    # pending_hold | approved | paid | clawed_back | cancelled

    hold_until          = db.Column(db.DateTime, nullable=True)   # created_at + 7 days
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at         = db.Column(db.DateTime, nullable=True)
    paid_at             = db.Column(db.DateTime, nullable=True)
    clawback_reason     = db.Column(db.String(255), nullable=True)

    plan_type           = db.Column(db.String(30), nullable=True)

    trader = db.relationship('User', backref='partner_commissions_received')


class PayoutRequest(db.Model):
    """Partner payout request flow."""
    __tablename__ = 'payout_requests'

    id                = db.Column(db.String(36), primary_key=True, default=_gen_uuid)
    partner_id        = db.Column(db.String(36), db.ForeignKey('partners.id'), nullable=False)
    amount            = db.Column(db.Numeric(10, 2), nullable=False)

    status            = db.Column(db.String(20), nullable=False, default='requested')
    # requested | under_review | approved | paid | rejected

    requested_at      = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at       = db.Column(db.DateTime, nullable=True)
    paid_at           = db.Column(db.DateTime, nullable=True)

    payment_reference = db.Column(db.String(100), nullable=True)   # UTR / transaction ID
    rejection_reason  = db.Column(db.String(255), nullable=True)
    reviewed_by_note  = db.Column(db.String(255), nullable=True)


class BrokerDetail(db.Model):
    """Extended info for Broker-type partners."""
    __tablename__ = 'broker_details_partner'

    id                = db.Column(db.String(36), primary_key=True, default=_gen_uuid)
    broker_partner_id = db.Column(db.String(36), db.ForeignKey('partners.id'), nullable=False)
    broker_code       = db.Column(db.String(20), nullable=False)
    active            = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    broker_partner = db.relationship('Partner', backref='broker_details')
