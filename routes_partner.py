"""
Partner Network routes — /partners/* blueprint
Completely isolated from the existing user flow.
"""

import uuid
import logging
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, jsonify)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func

from app import app, db
from models import User
from models_partner import Partner, TraderReferral, PartnerCommission, PayoutRequest, _gen_partner_code

logger = logging.getLogger(__name__)

partner_bp = Blueprint('partner', __name__, url_prefix='/partners')

PARTNER_TYPES = {
    'individual': 'Individual Partner',
    'broker':     'Broker Partner',
    'trainer':    'Training Partner',
    'pms':        'Portfolio Manager (PMS)',
}

PLAN_PRICES = {
    'TARGET_PLUS': 1499,
    'TARGET_PRO':  2499,
    'HNI':         4999,
}

GATEWAY_FEE_RATE = 0.02   # 2%
HOLD_DAYS        = 7
MIN_PAYOUT       = 1000


def _partner_required(f):
    """Decorator: user must be logged in AND have an active partner profile."""
    from functools import wraps
    @wraps(f)
    def _wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('partner.login'))
        if not hasattr(current_user, 'partner_profile') or not current_user.partner_profile:
            flash('Partner profile not found.', 'error')
            return redirect(url_for('partner.login'))
        p = current_user.partner_profile
        if p.status not in ('active', 'pending'):
            flash('Your partner account is not active.', 'error')
            return redirect(url_for('partner.login'))
        return f(*args, **kwargs)
    return _wrapped


# ── Public landing page ────────────────────────────────────────────────────────
@partner_bp.route('/')
def index():
    return redirect(url_for('partners'))        # marketing page lives at /partners


# ── Partner login ──────────────────────────────────────────────────────────────
@partner_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and hasattr(current_user, 'partner_profile') \
            and current_user.partner_profile:
        return redirect(url_for('partner.dashboard'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            partner = Partner.query.filter_by(user_id=user.id).first()
            if not partner:
                flash('No partner account linked to this email. Please register as a partner.', 'error')
                return render_template('partner/login.html')
            if partner.status == 'terminated':
                flash('Your partner account has been terminated. Contact support.', 'error')
                return render_template('partner/login.html')
            login_user(user)
            logger.info(f"Partner login: {email} (partner_id={partner.id})")
            return redirect(url_for('partner.dashboard'))

        flash('Invalid email or password.', 'error')

    return render_template('partner/login.html')


@partner_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('partner.login'))


# ── Partner registration ───────────────────────────────────────────────────────
@partner_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        email        = request.form.get('email', '').strip().lower()
        mobile       = request.form.get('mobile', '').strip()
        password     = request.form.get('password', '')
        partner_type = request.form.get('partner_type', 'individual')
        pan          = request.form.get('pan_number', '').strip().upper() or None
        gst          = request.form.get('gst_number', '').strip().upper() or None

        if not all([name, email, mobile, password]):
            flash('Please fill in all required fields.', 'error')
            return render_template('partner/register.html', partner_types=PARTNER_TYPES)

        # Check for duplicate email
        if User.query.filter_by(email=email).first():
            flash('An account with this email already exists. Please log in.', 'error')
            return render_template('partner/register.html', partner_types=PARTNER_TYPES)

        if Partner.query.filter_by(email=email).first():
            flash('A partner account with this email already exists.', 'error')
            return render_template('partner/register.html', partner_types=PARTNER_TYPES)

        # Create User account
        username = email.split('@')[0] + '_partner'
        # Ensure username uniqueness
        base = username
        ctr  = 1
        while User.query.filter_by(username=username).first():
            username = f"{base}{ctr}"
            ctr += 1

        user = User(
            username=username,
            email=email,
            first_name=name.split()[0] if name else '',
            last_name=' '.join(name.split()[1:]) if len(name.split()) > 1 else '',
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # get user.id

        # Generate unique partner code
        base_code = _gen_partner_code(name)
        code      = base_code
        attempt   = 0
        while Partner.query.filter_by(partner_code=code).first():
            attempt += 1
            code = _gen_partner_code(name) + str(attempt)

        partner = Partner(
            user_id      = user.id,
            name         = name,
            email        = email,
            mobile       = mobile,
            partner_type = partner_type,
            partner_code = code,
            pan_number   = pan,
            gst_number   = gst,
            status       = 'pending',     # admin must approve
            kyc_status   = 'pending',
        )
        partner.generate_display_id()
        db.session.add(partner)
        db.session.commit()

        logger.info(f"New partner registered: {email} ({partner.partner_display_id})")
        flash('Registration successful! Your application is under review. '
              'We will notify you by email once approved.', 'success')
        return redirect(url_for('partner.login'))

    return render_template('partner/register.html', partner_types=PARTNER_TYPES)


# ── Partner dashboard ──────────────────────────────────────────────────────────
@partner_bp.route('/dashboard')
@_partner_required
def dashboard():
    partner = current_user.partner_profile

    # Recent commissions (last 10)
    recent_commissions = PartnerCommission.query.filter_by(
        partner_id=partner.id
    ).order_by(PartnerCommission.created_at.desc()).limit(10).all()

    # Stats
    total_referred = partner.total_referred
    paid_referred  = partner.paid_referred
    earned         = partner.total_commission_earned
    pending        = partner.pending_commission
    paid_out       = partner.paid_out
    wallet_bal     = float(partner.wallet_balance)
    rev_generated  = partner.total_revenue_generated

    return render_template('partner/dashboard.html',
        partner            = partner,
        recent_commissions = recent_commissions,
        total_referred     = total_referred,
        paid_referred      = paid_referred,
        earned             = earned,
        pending            = pending,
        paid_out           = paid_out,
        wallet_balance     = wallet_bal,
        rev_generated      = rev_generated,
        min_payout         = MIN_PAYOUT,
        partner_types      = PARTNER_TYPES,
    )


# ── Commissions list ───────────────────────────────────────────────────────────
@partner_bp.route('/commissions')
@_partner_required
def commissions():
    partner = current_user.partner_profile
    page    = request.args.get('page', 1, type=int)
    status  = request.args.get('status', '')

    q = PartnerCommission.query.filter_by(partner_id=partner.id)
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(PartnerCommission.created_at.desc()).paginate(page=page, per_page=20)

    return render_template('partner/commissions.html',
        partner  = partner,
        rows     = rows,
        status   = status,
    )


# ── Referrals list ─────────────────────────────────────────────────────────────
@partner_bp.route('/referrals')
@_partner_required
def referrals():
    partner = current_user.partner_profile
    page    = request.args.get('page', 1, type=int)

    refs = TraderReferral.query.filter_by(
        partner_id=partner.id
    ).order_by(TraderReferral.linked_date.desc()).paginate(page=page, per_page=20)

    return render_template('partner/referrals.html',
        partner = partner,
        refs    = refs,
    )


# ── Payout requests ────────────────────────────────────────────────────────────
@partner_bp.route('/payouts', methods=['GET', 'POST'])
@_partner_required
def payouts():
    partner = current_user.partner_profile

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
        except (ValueError, TypeError):
            flash('Invalid amount.', 'error')
            return redirect(url_for('partner.payouts'))

        wallet = float(partner.wallet_balance)
        if amount < MIN_PAYOUT:
            flash(f'Minimum withdrawal is ₹{MIN_PAYOUT:,.0f}.', 'error')
            return redirect(url_for('partner.payouts'))
        if amount > wallet:
            flash('Insufficient wallet balance.', 'error')
            return redirect(url_for('partner.payouts'))

        # Check no pending request already open
        open_req = PayoutRequest.query.filter_by(
            partner_id=partner.id
        ).filter(PayoutRequest.status.in_(['requested', 'under_review', 'approved'])).first()
        if open_req:
            flash('You already have an open payout request under review.', 'warning')
            return redirect(url_for('partner.payouts'))

        req = PayoutRequest(
            partner_id   = partner.id,
            amount       = amount,
            status       = 'requested',
            requested_at = datetime.utcnow(),
        )
        # Deduct from wallet immediately (hold)
        partner.wallet_balance = float(partner.wallet_balance) - amount
        db.session.add(req)
        db.session.commit()

        flash(f'Payout request of ₹{amount:,.2f} submitted. Admin will process it shortly.', 'success')
        return redirect(url_for('partner.payouts'))

    history = PayoutRequest.query.filter_by(
        partner_id=partner.id
    ).order_by(PayoutRequest.requested_at.desc()).all()

    return render_template('partner/payouts.html',
        partner      = partner,
        history      = history,
        min_payout   = MIN_PAYOUT,
        wallet_balance = float(partner.wallet_balance),
    )


# ── Profile ────────────────────────────────────────────────────────────────────
@partner_bp.route('/profile', methods=['GET', 'POST'])
@_partner_required
def profile():
    partner = current_user.partner_profile

    if request.method == 'POST':
        partner.mobile          = request.form.get('mobile', partner.mobile).strip()
        partner.pan_number      = request.form.get('pan_number', '').strip().upper() or partner.pan_number
        partner.gst_number      = request.form.get('gst_number', '').strip().upper() or partner.gst_number
        partner.bank_account_number = request.form.get('bank_account', '').strip() or partner.bank_account_number
        partner.bank_ifsc       = request.form.get('bank_ifsc', '').strip().upper() or partner.bank_ifsc
        partner.upi_id          = request.form.get('upi_id', '').strip() or partner.upi_id
        partner.updated_at      = datetime.utcnow()
        db.session.commit()
        flash('Profile updated successfully.', 'success')

    return render_template('partner/profile.html',
        partner       = partner,
        partner_types = PARTNER_TYPES,
    )


# ── Commission calculation helper (called from routes_payment.py) ──────────────
def calculate_and_record_commission(user_id: int, razorpay_payment_id: str,
                                    plan_type: str, gross_amount: float) -> bool:
    """
    Called after a successful payment. Checks if the trader was referred by a partner
    and, if so, creates a PartnerCommission record and credits the partner wallet.
    """
    try:
        referral = TraderReferral.query.filter_by(trader_id=user_id).first()
        if not referral:
            return False   # not referred by anyone

        partner = Partner.query.get(referral.partner_id)
        if not partner or partner.status not in ('active',):
            return False

        gateway_fee       = round(gross_amount * GATEWAY_FEE_RATE, 2)
        net_amount        = round(gross_amount - gateway_fee, 2)
        commission_pct    = float(partner.commission_percentage)
        commission_amount = round(net_amount * commission_pct / 100, 2)

        hold_until = datetime.utcnow() + timedelta(days=HOLD_DAYS)

        comm = PartnerCommission(
            partner_id        = partner.id,
            trader_id         = user_id,
            subscription_id   = razorpay_payment_id,
            gross_amount      = gross_amount,
            gateway_fee       = gateway_fee,
            net_amount        = net_amount,
            commission_percent= commission_pct,
            commission_amount = commission_amount,
            status            = 'pending_hold',
            hold_until        = hold_until,
            plan_type         = plan_type,
        )
        db.session.add(comm)
        db.session.commit()
        logger.info(f"Commission ₹{commission_amount} queued for partner {partner.partner_display_id} "
                    f"(hold until {hold_until.date()})")
        return True
    except Exception as e:
        logger.error(f"Commission calculation error: {e}", exc_info=True)
        return False


# ── Daily cron: release held commissions → wallet ─────────────────────────────
def release_held_commissions():
    """Move pending_hold → approved + credit wallet. Run daily via scheduler."""
    try:
        now   = datetime.utcnow()
        ready = PartnerCommission.query.filter(
            PartnerCommission.status == 'pending_hold',
            PartnerCommission.hold_until <= now,
        ).all()
        for comm in ready:
            comm.status      = 'approved'
            comm.approved_at = now
            partner = Partner.query.get(comm.partner_id)
            if partner:
                partner.wallet_balance = float(partner.wallet_balance) + float(comm.commission_amount)
        db.session.commit()
        logger.info(f"Released {len(ready)} held commission(s) to partner wallets")
    except Exception as e:
        logger.error(f"Commission release error: {e}")
