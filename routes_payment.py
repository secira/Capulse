"""
Payment Routes for Capulse
Handles Razorpay integration, subscription payments, and billing
"""

import os
import logging
from datetime import datetime, timedelta
from flask import request, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import hmac
import hashlib

from app import app, db, csrf
from models import User, PricingPlan, Payment, SubscriptionStatus
from services.razorpay_service import razorpay_service

logger = logging.getLogger(__name__)

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')

razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        import razorpay as _rzp
        razorpay_client = _rzp.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except Exception:
        pass

PLANS = {
    'capulse_plus': {
        'name': 'Capulse Plus',
        'price': 999,
        'duration_days': 30,
        'pricing_plan': PricingPlan.TARGET_PLUS,
        'features': [
            '~150–200 AI questions per day',
            'Portfolio upload & analysis',
            'Behavioural pattern analysis',
            'Persistent memory across sessions',
            'Score & signal history',
            'Priority response speed',
        ],
    },
    'target_plus': {
        'name': 'Growth Plan',
        'price': 1499,
        'duration_days': 30,
        'pricing_plan': PricingPlan.TARGET_PLUS,
        'features': [
            '1 broker connection (trading + data API)',
            'AI Research Co-Pilot',
            'F&O Analysis Engine',
            'Behavioural AI Engine',
            'Daily Trading Signals',
            'Portfolio Analytics',
            'Trade Now with guardrails',
        ],
    },
    'target_pro': {
        'name': 'Pro Plan',
        'price': 2499,
        'duration_days': 30,
        'pricing_plan': PricingPlan.TARGET_PRO,
        'features': [
            '3 broker connections (trading)',
            '1 data API broker',
            'All Growth Plan features',
            'Multi-broker Trade Now',
            'Advanced AI insights',
            'Portfolio optimization',
            'Priority email support',
        ],
    },
    'hni': {
        'name': 'Elite Plan',
        'price': 4999,
        'duration_days': 30,
        'pricing_plan': PricingPlan.HNI,
        'features': [
            '3 broker connections (trading)',
            '1 data API broker',
            'All Pro Plan features',
            'Dedicated account manager',
            'Premium 24/7 support',
            'Priority onboarding',
        ],
    },
}


@app.route('/subscribe/<plan_type>')
@login_required
def subscribe(plan_type):
    """Subscription checkout — creates Razorpay order and renders checkout page"""
    if plan_type not in PLANS:
        flash('Invalid plan selected.', 'danger')
        return redirect(url_for('pricing'))

    plan = PLANS[plan_type]

    if current_user.pricing_plan == plan['pricing_plan']:
        flash('You are already on this plan.', 'info')
        return redirect(url_for('chat.chat_home'))

    order_result = razorpay_service.create_subscription_order(
        user_id=current_user.id,
        plan_type=plan_type,
        amount=plan['price'],
    )

    if not order_result.get('success'):
        flash('Payment service is temporarily unavailable. Please try again shortly.', 'danger')
        return redirect(url_for('pricing'))

    # ── Create a pending Payment record so the webhook can activate the
    # subscription even if the frontend verification call never arrives
    # (network drop, browser closed after Razorpay succeeds, etc.)
    try:
        pending = Payment(
            user_id=current_user.id,
            razorpay_payment_id=f"pending_{order_result['order_id']}",
            razorpay_order_id=order_result['order_id'],
            amount=plan['price'],
            currency='INR',
            status='pending',
            plan_type=plan['pricing_plan'],
            billing_period='monthly',
            tenant_id='live',
        )
        db.session.add(pending)
        db.session.commit()
    except Exception as _pe:
        logger.warning(f"Could not create pending payment record: {_pe}")
        db.session.rollback()

    return render_template(
        'payment/checkout.html',
        plan=plan,
        plan_type=plan_type,
        order=order_result,
        user=current_user,
    )


@app.route('/payment/verify', methods=['POST'])
@login_required
def verify_payment():
    """Verify Razorpay payment signature and activate user subscription"""
    try:
        razorpay_order_id = request.form.get('razorpay_order_id')
        razorpay_payment_id = request.form.get('razorpay_payment_id')
        razorpay_signature = request.form.get('razorpay_signature')
        plan_type = request.form.get('plan_type')

        # Signature is mandatory — reject immediately if absent
        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, plan_type]):
            logger.warning("verify_payment: missing required parameter(s); rejecting")
            return jsonify({'success': False, 'error': 'Missing payment parameters'})

        if plan_type not in PLANS:
            return jsonify({'success': False, 'error': 'Invalid plan type'})

        # Always verify signature — no subscription activation without it
        if not razorpay_client:
            logger.error("verify_payment: Razorpay client not initialised")
            return jsonify({'success': False, 'error': 'Payment service not configured'})

        try:
            razorpay_client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature,
            })
        except Exception as sig_err:
            logger.error(f"Razorpay signature verification failed: {sig_err}")
            return jsonify({'success': False, 'error': 'Payment verification failed'})

        plan_info = PLANS[plan_type]

        # Determine billing cycle duration: annual flag → 365 days, else use plan default
        billing_period = request.form.get('billing_period', 'monthly').strip().lower()
        if billing_period in ('annual', 'yearly'):
            duration_days = 365
        else:
            duration_days = plan_info.get('duration_days', 30)
            billing_period = 'monthly'

        now = datetime.utcnow()
        current_user.pricing_plan = plan_info['pricing_plan']
        current_user.subscription_status = SubscriptionStatus.ACTIVE
        current_user.subscription_start_date = now
        current_user.billing_cycle = billing_period
        # Stack on top of existing expiry if still in the future
        base = current_user.subscription_end_date if (
            current_user.subscription_end_date and current_user.subscription_end_date > now
        ) else now
        current_user.subscription_end_date = base + timedelta(days=duration_days)
        current_user.total_payments = (current_user.total_payments or 0) + plan_info['price']

        payment = Payment(
            user_id=current_user.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=razorpay_order_id,
            amount=plan_info['price'],
            currency='INR',
            status='captured',
            plan_type=plan_info['pricing_plan'],
            billing_period=billing_period,
        )
        db.session.add(payment)
        db.session.commit()

        logger.info(f"User {current_user.id} upgraded to {plan_type}")

        # Send subscription upgrade email
        try:
            from services.email_service import send_subscription_update_email
            old_plan = current_user.pricing_plan.value if hasattr(current_user.pricing_plan, 'value') else str(current_user.pricing_plan)
            send_subscription_update_email(current_user, old_plan, plan_type)
        except Exception as _mail_err:
            logger.warning(f"Subscription email failed (non-fatal): {_mail_err}")

        # Partner Network: calculate commission for referred traders (non-fatal)
        try:
            from routes_partner import calculate_and_record_commission
            calculate_and_record_commission(
                user_id=current_user.id,
                razorpay_payment_id=razorpay_payment_id,
                plan_type=plan_type,
                gross_amount=float(plan_info['price']),  # price is already in rupees
            )
        except Exception as _comm_err:
            logger.warning(f"Partner commission calculation failed (non-fatal): {_comm_err}")

        return jsonify({
            'success': True,
            'message': 'Payment successful! Your subscription has been activated.',
            'redirect_url': url_for('payment_success', payment_id=razorpay_payment_id),
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Payment verification error: {e}")
        return jsonify({'success': False, 'error': 'Payment processing failed'})


@app.route('/payment/success')
@login_required
def payment_success():
    """Payment success confirmation page"""
    payment_id = request.args.get('payment_id')

    payment = None
    if payment_id:
        payment = Payment.query.filter_by(
            razorpay_payment_id=payment_id,
            user_id=current_user.id,
        ).first()

    if not payment:
        payment = Payment.query.filter_by(user_id=current_user.id)\
                               .order_by(Payment.created_at.desc()).first()

    if not payment:
        flash('Payment confirmed! Your account has been upgraded.', 'success')
        return redirect(url_for('chat.chat_home'))

    days_remaining = 30
    if current_user.subscription_end_date:
        delta = current_user.subscription_end_date - datetime.utcnow()
        days_remaining = max(0, delta.days)

    # Find the matching plan key by PricingPlan enum value (PLANS keys are
    # lowercase strings; payment.plan_type is a PricingPlan enum)
    _plan_key = next(
        (k for k, v in PLANS.items() if v['pricing_plan'] == payment.plan_type),
        None
    )
    subscription = {
        'end_date': current_user.subscription_end_date,
        'days_remaining': days_remaining,
        'features': PLANS[_plan_key]['features'] if _plan_key else [],
    }

    return render_template('payment/success.html',
                           payment=payment,
                           subscription=subscription)


@app.route('/payment/failed')
def payment_failed():
    """Payment failed page"""
    return render_template('payment/failed.html')


@app.route('/account/upgrade')
@login_required
def upgrade_plan():
    """Plan upgrade page"""
    plans = razorpay_service.get_subscription_plans()
    current_plan = current_user.pricing_plan.value if current_user.pricing_plan else 'FREE'

    upgrade_options = {}
    for plan_key, plan_details in plans.items():
        if plan_key != current_plan.upper():
            upgrade_cost = razorpay_service.calculate_plan_upgrade_cost(current_plan.upper(), plan_key)
            if upgrade_cost.get('success'):
                upgrade_options[plan_key] = {
                    'plan': plan_details,
                    'cost': upgrade_cost['upgrade_cost'],
                }

    return render_template('account/upgrade.html',
                           current_plan=current_plan,
                           upgrade_options=upgrade_options)


@app.route('/webhook/razorpay', methods=['POST'])
@csrf.exempt
def razorpay_webhook():
    """Handle Razorpay webhooks — fallback subscription activator."""
    try:
        webhook_signature = request.headers.get('X-Razorpay-Signature')
        webhook_body     = request.get_data()
        webhook_secret   = os.environ.get('RAZORPAY_WEBHOOK_SECRET')

        # ── Signature verification ────────────────────────────────────────
        if webhook_secret:
            if not webhook_signature:
                logger.warning("Webhook: missing X-Razorpay-Signature header — rejected")
                return jsonify({'status': 'error', 'message': 'Missing signature'}), 403
            expected = hmac.new(
                webhook_secret.encode(),
                webhook_body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(webhook_signature, expected):
                logger.warning("Webhook: invalid signature — rejected")
                return jsonify({'status': 'error', 'message': 'Invalid signature'}), 403
        else:
            # Secret not configured — log and accept (set RAZORPAY_WEBHOOK_SECRET for production)
            logger.warning("Webhook: RAZORPAY_WEBHOOK_SECRET not set; skipping signature check")

        event_data = request.get_json(silent=True) or {}
        event_type = event_data.get('event')
        logger.info(f"Razorpay webhook received: {event_type}")

        if event_type == 'payment.captured':
            payment_data  = event_data.get('payload', {}).get('payment', {}).get('entity', {})
            rzp_payment_id = payment_data.get('id')
            order_id       = payment_data.get('order_id')
            notes          = payment_data.get('notes', {})

            if not (order_id and rzp_payment_id):
                return jsonify({'status': 'ok'}), 200

            existing = Payment.query.filter_by(razorpay_order_id=order_id).first()

            if existing and existing.status == 'captured':
                # Frontend already verified and activated — nothing to do
                logger.info(f"Webhook: order {order_id} already captured, skipping")
                return jsonify({'status': 'ok'}), 200

            if existing and existing.status == 'pending':
                # Pending record exists (created by subscribe()) — activate it
                existing.razorpay_payment_id = rzp_payment_id
                existing.status = 'captured'
                user = User.query.get(existing.user_id)
                plan_info = next(
                    (v for v in PLANS.values() if v['pricing_plan'] == existing.plan_type),
                    None
                )
                duration_days = plan_info['duration_days'] if plan_info else 30
                if user:
                    now = datetime.utcnow()
                    user.pricing_plan = existing.plan_type
                    user.subscription_status = SubscriptionStatus.ACTIVE
                    user.subscription_start_date = now
                    base = user.subscription_end_date if (
                        user.subscription_end_date and user.subscription_end_date > now
                    ) else now
                    user.subscription_end_date = base + timedelta(days=duration_days)
                    logger.info(f"Webhook: activated {existing.plan_type} for user {user.id}")
                db.session.commit()

            elif not existing:
                # No record at all (edge case: pending write failed) — try from notes
                user_id_str = notes.get('user_id') or notes.get('user_id', '')
                plan_type   = notes.get('plan_type', '')
                if user_id_str and plan_type and plan_type in PLANS:
                    try:
                        uid = int(user_id_str)
                        plan_info = PLANS[plan_type]
                        user = User.query.get(uid)
                        if user:
                            now = datetime.utcnow()
                            new_payment = Payment(
                                user_id=uid,
                                razorpay_payment_id=rzp_payment_id,
                                razorpay_order_id=order_id,
                                amount=plan_info['price'],
                                currency='INR',
                                status='captured',
                                plan_type=plan_info['pricing_plan'],
                                billing_period='monthly',
                                tenant_id='live',
                            )
                            db.session.add(new_payment)
                            user.pricing_plan = plan_info['pricing_plan']
                            user.subscription_status = SubscriptionStatus.ACTIVE
                            user.subscription_start_date = now
                            base = user.subscription_end_date if (
                                user.subscription_end_date and user.subscription_end_date > now
                            ) else now
                            user.subscription_end_date = base + timedelta(days=plan_info['duration_days'])
                            db.session.commit()
                            logger.info(f"Webhook: created payment+activated user {uid} via notes fallback")
                    except Exception as _wb_err:
                        db.session.rollback()
                        logger.error(f"Webhook notes-fallback error: {_wb_err}")
                else:
                    logger.warning(f"Webhook: no pending payment and no usable notes for order {order_id}")

        elif event_type == 'payment.failed':
            payment_data = event_data.get('payload', {}).get('payment', {}).get('entity', {})
            order_id = payment_data.get('order_id')
            if order_id:
                payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
                if payment and payment.status == 'pending':
                    payment.status = 'failed'
                    db.session.commit()
                    logger.info(f"Webhook: marked order {order_id} as failed")

        elif event_type == 'subscription.charged':
            # Recurring subscription renewal
            sub_data = event_data.get('payload', {}).get('payment', {}).get('entity', {})
            order_id = sub_data.get('order_id')
            if order_id:
                payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
                if payment:
                    user = User.query.get(payment.user_id)
                    if user and user.subscription_end_date:
                        user.subscription_end_date += timedelta(days=30)
                        db.session.commit()

        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
