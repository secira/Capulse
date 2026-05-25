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
      - FREE plan within the 14-day trial window (or +7-day extension).

    Expired-trial FREE users are redirected to /pricing.
    Always stack BELOW @login_required so current_user is already resolved.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if getattr(current_user, 'is_admin', False):
            return f(*args, **kwargs)
        if not current_user.has_full_access():
            if hasattr(current_user, 'can_extend_trial') and current_user.can_extend_trial():
                flash(
                    'Your free trial has ended. Claim your one-time 7-day extension '
                    'from the sidebar, or upgrade to keep using Research Co-Pilot, '
                    'F&O Analysis, Trade Now, Behavioural AI, and broker connections.',
                    'warning'
                )
            else:
                flash(
                    'Your free trial has ended. '
                    'Upgrade to continue using Research Co-Pilot, F&O Analysis, '
                    'Trade Now, Behavioural AI, and broker connections.',
                    'warning'
                )
            return redirect(url_for('pricing'))
        return f(*args, **kwargs)
    return decorated
