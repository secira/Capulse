"""
Payment and Subscription Models for Capulse
Tracks payments, subscriptions, and billing history
"""

from datetime import datetime, timedelta
from app import db
try:
    from models import PricingPlan
except ImportError:
    # Define PricingPlan locally if not available
    import enum
    class PricingPlan(enum.Enum):
        FREE = "free"
        TARGET_PLUS = "target_plus"
        TARGET_PRO = "target_pro"
        HNI = "hni"
import enum

class PaymentStatus(enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"

class SubscriptionStatus(enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING = "pending"

class Payment(db.Model):
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Razorpay details
    razorpay_order_id = db.Column(db.String(100), nullable=False, unique=True)
    razorpay_payment_id = db.Column(db.String(100), nullable=True)
    razorpay_signature = db.Column(db.String(200), nullable=True)
    
    # Payment details
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    status = db.Column(db.Enum(PaymentStatus), default=PaymentStatus.PENDING)
    payment_method = db.Column(db.String(50), nullable=True)
    
    # Plan details
    plan_type = db.Column(db.Enum(PricingPlan), nullable=False)
    plan_duration_days = db.Column(db.Integer, default=30)
    
    # Metadata
    description = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.JSON, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    paid_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    user = db.relationship('User', backref='payments')
    
    def mark_as_paid(self, razorpay_payment_id: str, payment_method: str = None):
        """Mark payment as completed"""
        self.status = PaymentStatus.COMPLETED
        self.razorpay_payment_id = razorpay_payment_id
        self.paid_at = datetime.utcnow()
        if payment_method:
            self.payment_method = payment_method
        db.session.commit()
    
    def mark_as_failed(self, reason: str = None):
        """Mark payment as failed"""
        self.status = PaymentStatus.FAILED
        if reason:
            if self.notes is None:
                self.notes = {}
            self.notes['failure_reason'] = reason
        db.session.commit()
    
    def __repr__(self):
        return f'<Payment {self.id}: {self.amount} {self.currency} - {self.status.value}>'

class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=True)
    
    # Subscription details
    plan_type = db.Column(db.Enum(PricingPlan), nullable=False)
    status = db.Column(db.Enum(SubscriptionStatus), default=SubscriptionStatus.PENDING)
    
    # Subscription period
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    
    # Billing
    amount_paid = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    
    # Auto-renewal (for future implementation)
    auto_renew = db.Column(db.Boolean, default=False)
    
    # Metadata
    notes = db.Column(db.JSON, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    user = db.relationship('User', backref='subscriptions')
    payment = db.relationship('Payment', backref='subscription', uselist=False)
    
    @property
    def is_active(self) -> bool:
        """Check if subscription is currently active"""
        return (self.status == SubscriptionStatus.ACTIVE and 
                self.end_date > datetime.utcnow())
    
    @property
    def days_remaining(self) -> int:
        """Get number of days remaining in subscription"""
        if self.status != SubscriptionStatus.ACTIVE:
            return 0
        
        remaining = self.end_date - datetime.utcnow()
        return max(0, remaining.days)
    
    def activate(self):
        """Activate the subscription"""
        self.status = SubscriptionStatus.ACTIVE
        db.session.commit()
    
    def cancel(self, reason: str = None):
        """Cancel the subscription"""
        self.status = SubscriptionStatus.CANCELLED
        self.cancelled_at = datetime.utcnow()
        if reason:
            if self.notes is None:
                self.notes = {}
            self.notes['cancellation_reason'] = reason
        db.session.commit()
    
    def extend(self, additional_days: int):
        """Extend subscription by additional days"""
        self.end_date += timedelta(days=additional_days)
        db.session.commit()
    
    @classmethod
    def create_from_payment(cls, payment: Payment):
        """Create subscription from completed payment"""
        subscription = cls(
            user_id=payment.user_id,
            payment_id=payment.id,
            plan_type=payment.plan_type,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=payment.plan_duration_days),
            amount_paid=payment.amount,
            currency=payment.currency,
            status=SubscriptionStatus.ACTIVE
        )
        return subscription
    
    def __repr__(self):
        return f'<Subscription {self.id}: {self.plan_type.value} - {self.status.value}>'

class Refund(db.Model):
    __tablename__ = 'refunds'
    
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Razorpay refund details
    razorpay_refund_id = db.Column(db.String(100), nullable=False, unique=True)
    
    # Refund details
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    status = db.Column(db.String(20), nullable=False)
    
    # Reason and notes
    reason = db.Column(db.String(255), nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    payment = db.relationship('Payment', backref='refunds')
    user = db.relationship('User', backref='refunds')
    
    def __repr__(self):
        return f'<Refund {self.id}: ₹{self.amount} for Payment {self.payment_id}>'

class PaymentAttempt(db.Model):
    __tablename__ = 'payment_attempts'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Attempt details
    plan_type = db.Column(db.Enum(PricingPlan), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # initiated, abandoned, failed, completed
    
    # Session data
    razorpay_order_id = db.Column(db.String(100), nullable=True)
    session_data = db.Column(db.JSON, nullable=True)
    
    # Browser/device info
    user_agent = db.Column(db.String(500), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    user = db.relationship('User', backref='payment_attempts')
    
    def __repr__(self):
        return f'<PaymentAttempt {self.id}: {self.plan_type.value} - {self.status}>'