"""
Create or reset the Capulse admin (User model) account.

Usage:
  On Railway console:
    python reset_admin.py

  Non-interactive via env vars:
    ADMIN_EMAIL=udayid@gmail.com ADMIN_PASSWORD=yourpassword python reset_admin.py --env

  On Railway: set ADMIN_EMAIL + ADMIN_PASSWORD in Variables, then run once in the Railway console.
"""

import os
import sys

# Suppress heavy startup noise
os.environ.setdefault("SKIP_SCHEDULER", "1")

from app import app, db
from models import User, PricingPlan, SubscriptionStatus


TENANT_ID = "live"


def _upsert(email: str, password: str):
    with app.app_context():
        user = User.query.filter_by(email=email, tenant_id=TENANT_ID).first()
        if user:
            user.set_password(password)
            user.is_admin = True
            user.active = True
            user.pricing_plan = PricingPlan.HNI
            action = "updated"
        else:
            username = email.split("@")[0]
            # ensure username is unique in this tenant
            if User.query.filter_by(username=username, tenant_id=TENANT_ID).first():
                username = username + "_admin"
            user = User(
                tenant_id=TENANT_ID,
                username=username,
                email=email,
                is_admin=True,
                active=True,
                pricing_plan=PricingPlan.HNI,
                subscription_status=SubscriptionStatus.ACTIVE,
            )
            user.set_password(password)
            db.session.add(user)
            action = "created"

        db.session.commit()
        print(f"✅ Admin user {action}: {email}  (id={user.id})")
        print(f"   Username : {user.username}")
        print(f"   is_admin : {user.is_admin}")
        print(f"   active   : {user.active}")
        print(f"   Plan     : {user.pricing_plan}")
        print()
        print("   Login at  /login  with the email + password you just set.")


def _from_env():
    email    = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not email:
        print("❌ Set ADMIN_EMAIL env var."); sys.exit(1)
    if len(password) < 8:
        print("❌ Set ADMIN_PASSWORD (≥ 8 chars)."); sys.exit(1)
    _upsert(email, password)


def _interactive():
    import getpass
    print("🔐 Capulse admin account reset")
    print("=" * 40)
    email    = input("Email   : ").strip()
    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("❌ Password must be ≥ 8 characters."); sys.exit(1)
    confirm  = getpass.getpass("Confirm : ")
    if password != confirm:
        print("❌ Passwords do not match."); sys.exit(1)
    _upsert(email, password)


if __name__ == "__main__":
    if "--env" in sys.argv:
        _from_env()
    else:
        _interactive()
