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
from models import User, Admin, PricingPlan, SubscriptionStatus


TENANT_ID = "live"


def _upsert(email: str, password: str):
    with app.app_context():
        # ── 1. User model — powers /login (main app) ──────────────────────
        user = User.query.filter(
            User.tenant_id == TENANT_ID,
            (User.email == email) | (User.username == email)
        ).first()
        if user:
            user.set_password(password)
            user.is_admin = True
            user.active = True
            user.pricing_plan = PricingPlan.HNI
            user_action = "updated"
        else:
            username = email.split("@")[0]
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
            user_action = "created"

        # ── 2. Admin model — powers /admin/login (admin dashboard) ────────
        admin = Admin.query.filter(
            (Admin.email == email) | (Admin.username == email)
        ).first()
        if admin:
            admin.set_password(password)
            admin.active = True
            admin.is_super_admin = True
            admin_action = "updated"
        else:
            admin = Admin(
                username=email,
                email=email,
                is_super_admin=True,
                active=True,
            )
            admin.set_password(password)
            db.session.add(admin)
            admin_action = "created"

        db.session.commit()
        print(f"✅ User  (main app)     {user_action}: {email}  (id={user.id})")
        print(f"✅ Admin (dashboard)    {admin_action}: {email}  (id={admin.id})")
        print()
        print("   Main app login   →  /login")
        print("   Admin dashboard  →  /admin/login")


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
