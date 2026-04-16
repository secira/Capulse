"""
Shared route decorators for Target Capital.
"""
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user


def paid_plan_required(f):
    """
    Restrict a route to users on a paid plan (Growth / Pro / Elite).
    FREE (Starter) users are redirected to /pricing with an explanatory message.
    Always stack BELOW @login_required so current_user is already resolved.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.pricing_plan.value == 'FREE':
            flash(
                'This feature is available on the Growth Plan and above. '
                'Upgrade to unlock Research Co-Pilot, F&O Analysis, Trade Now, '
                'Behavioural AI, and broker connections.',
                'warning'
            )
            return redirect(url_for('pricing'))
        return f(*args, **kwargs)
    return decorated
