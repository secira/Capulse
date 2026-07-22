"""
Script to create an admin user for Target Capital Trading Platform.

Two modes of operation:
  1. Non-interactive (CI / first-deploy): set environment variables
       ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_SUPER
     and run:
       python create_admin_user.py --env

  2. Interactive (local setup): run without arguments and follow the prompts.
"""

import os
import sys
from app import app, db
from models import Admin


def _create_from_env():
    """Non-interactive: read credentials from environment variables."""
    admin_email    = os.environ.get("ADMIN_EMAIL", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
    is_super       = os.environ.get("ADMIN_SUPER", "true").lower() not in ("0", "false", "no")

    if not admin_email:
        print("❌ ADMIN_EMAIL environment variable is required.")
        sys.exit(1)
    if not admin_password or len(admin_password) < 8:
        print("❌ ADMIN_PASSWORD must be set and at least 8 characters long.")
        sys.exit(1)

    with app.app_context():
        existing = Admin.query.filter(
            (Admin.username == admin_username) | (Admin.email == admin_email)
        ).first()
        if existing:
            print(f"ℹ️  Admin '{existing.username}' already exists — nothing to do.")
            return

        admin = Admin(
            username=admin_username,
            email=admin_email,
            is_super_admin=is_super,
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        print(f"✅ Admin user '{admin_username}' ({admin_email}) created successfully.")
        print(f"   Super Admin: {'Yes' if is_super else 'No'}")
        print("   Login at /admin/login")


def _create_interactively():
    """Interactive mode: prompt for credentials."""
    with app.app_context():
        print("🔐 Target Capital Admin User Creation")
        print("=" * 40)

        # Ensure admin table exists
        try:
            existing_count = Admin.query.count()
            if existing_count > 0:
                print(f"⚠️  Found {existing_count} existing admin(s)")
                choice = input("Create another admin user? (y/n): ").lower()
                if choice != 'y':
                    print("Exiting...")
                    return
        except Exception as e:
            print(f"Note: Admin table might not exist yet: {e}")
            print("Creating admin table...")
            db.create_all()

        username = input("Username: ").strip()
        if not username:
            print("❌ Username cannot be empty!")
            return

        if Admin.query.filter_by(username=username).first():
            print(f"❌ Admin with username '{username}' already exists!")
            return

        email = input("Email: ").strip()
        if not email:
            print("❌ Email cannot be empty!")
            return

        if Admin.query.filter_by(email=email).first():
            print(f"❌ Admin with email '{email}' already exists!")
            return

        first_name = input("First Name (optional): ").strip()
        last_name  = input("Last Name (optional): ").strip()

        import getpass
        password = getpass.getpass("Password (min 8 chars): ")
        if len(password) < 8:
            print("❌ Password must be at least 8 characters long!")
            return

        confirm = getpass.getpass("Confirm Password: ")
        if password != confirm:
            print("❌ Passwords do not match!")
            return

        is_super = input("Make super admin? (y/n): ").lower() == 'y'

        try:
            admin = Admin(
                username=username,
                email=email,
                first_name=first_name or None,
                last_name=last_name or None,
                is_super_admin=is_super,
            )
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            print(f"\n✅ Admin user '{username}' created successfully!")
            print(f"📧 Email: {email}")
            print(f"👑 Super Admin: {'Yes' if is_super else 'No'}")
            print(f"\n🌐 Login at /admin/login")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error creating admin user: {e}")


if __name__ == "__main__":
    if "--env" in sys.argv:
        _create_from_env()
    else:
        _create_interactively()
