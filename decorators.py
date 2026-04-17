"""
Shared route decorators for Target Capital.
"""
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user


def paid_plan_required(f):
    """
    Restrict a route to users who have full feature access:
      - Any paid plan (Growth / Pro / Elite), OR
      - FREE plan within the 30-day trial window.

    Expired-trial FREE users are redirected to /pricing.
    Always stack BELOW @login_required so current_user is already resolved.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if getattr(current_user, 'is_admin', False):
            return f(*args, **kwargs)
        if not current_user.has_full_access():
            flash(
                'Your 30-day free trial has ended. '
                'Upgrade to continue using Research Co-Pilot, F&O Analysis, '
                'Trade Now, Behavioural AI, and broker connections.',
                'warning'
            )
            return redirect(url_for('pricing'))
        return f(*args, **kwargs)
    return decorated
