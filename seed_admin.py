"""
Seed the production admin account on every deploy (idempotent).

Runs automatically from entrypoint.sh. Stores only a password HASH,
never the plaintext password. Safe to run repeatedly.

To change the password later: generate a new hash with
    python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('NewPassword'))"
and replace ADMIN_PASSWORD_HASH below.
"""
import sys
from datetime import datetime

ADMIN_EMAIL = "udayid@gmail.com"
# Hash of the agreed admin password (scrypt, werkzeug format)
ADMIN_PASSWORD_HASH = "scrypt:32768:8:1$QFIlmfmIsJFRknaL$43aabab4279f098ff5439056512b0ab72ee8b8ac78f6cf9536ac0dbb381938fd513ee53739f45e70e3e03bad64539ef4c4e74e2df8398e0a8e1337139da0cf0a"


def seed():
    from app import app
    from db_instance import db
    from models import Admin, User

    with app.app_context():
        # 1) Admin table (for /admin/login)
        admin = Admin.query.filter(
            (Admin.email == ADMIN_EMAIL) | (Admin.username == ADMIN_EMAIL)
        ).first()
        if admin:
            admin.password_hash = ADMIN_PASSWORD_HASH
            admin.active = True
            admin.is_super_admin = True
            print(f"  ✓ Admin record updated: {ADMIN_EMAIL}")
        else:
            admin = Admin(
                username=ADMIN_EMAIL,
                email=ADMIN_EMAIL,
                password_hash=ADMIN_PASSWORD_HASH,
                first_name="Admin",
                active=True,
                is_super_admin=True,
            )
            db.session.add(admin)
            print(f"  ✓ Admin record created: {ADMIN_EMAIL}")

        # 2) User table (for /login)
        user = User.query.filter_by(email=ADMIN_EMAIL, tenant_id="live").first()
        if not user:
            user = User.query.filter_by(email=ADMIN_EMAIL).first()
        if user:
            user.password_hash = ADMIN_PASSWORD_HASH
            user.active = True
            user.is_admin = True
            user.is_verified = True
            print(f"  ✓ User record updated: {ADMIN_EMAIL}")
        else:
            user = User(
                tenant_id="live",
                username=ADMIN_EMAIL,
                email=ADMIN_EMAIL,
                password_hash=ADMIN_PASSWORD_HASH,
                active=True,
                is_admin=True,
                is_verified=True,
                created_at=datetime.utcnow(),
            )
            db.session.add(user)
            print(f"  ✓ User record created: {ADMIN_EMAIL}")

        db.session.commit()
        print("  ✓ Admin seed complete.")


if __name__ == "__main__":
    try:
        seed()
    except Exception as e:
        print(f"  ⚠️ Admin seed failed: {e}", file=sys.stderr)
        sys.exit(1)
