import json
import os
import logging

import requests
from app import db
from flask import Blueprint, redirect, request, url_for, flash, render_template_string
from flask_login import login_required, login_user, logout_user
from models import User
from middleware.tenant_middleware import get_current_tenant_id, TenantQuery, create_for_tenant
from oauthlib.oauth2 import WebApplicationClient

logger = logging.getLogger(__name__)

# Safely load Google OAuth credentials
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

APP_DOMAIN = os.environ.get("APP_DOMAIN", os.environ.get("REPLIT_DEV_DOMAIN", "localhost:5000"))
REDIRECT_URL = f'https://{APP_DOMAIN}/google_login/callback'

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    logger.info(f"✅ Google OAuth configured with redirect URI: {REDIRECT_URL}")
else:
    logger.warning(f"⚠️ Google OAuth not configured. Please set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET")
    logger.info(f"""To make Google authentication work:
1. Go to https://console.cloud.google.com/apis/credentials
2. Create a new OAuth 2.0 Client ID
3. Add {REDIRECT_URL} to Authorized redirect URIs
4. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET environment variables
""")

google_auth = Blueprint("google_auth", __name__)


@google_auth.route("/oauth-check")
def oauth_check():
    """Public debug page — shows the exact redirect URI this app sends to Google."""
    html = f"""<!doctype html>
<html><head><title>OAuth Check</title>
<style>body{{font-family:monospace;padding:40px;background:#f5f5f5}}
.box{{background:#fff;border:2px solid #333;padding:20px;max-width:700px;word-break:break-all}}
h2{{color:#d00}}</style></head><body>
<h2>Google OAuth Redirect URI Check</h2>
<p>This is the <strong>exact</strong> redirect URI the app sends to Google.<br>
It must appear word-for-word in your Google Cloud Console → Authorized redirect URIs.</p>
<div class="box">{REDIRECT_URL}</div>
<br>
<p>Steps to fix:<br>
1. Go to <a href="https://console.cloud.google.com/apis/credentials" target="_blank">Google Cloud Console → Credentials</a><br>
2. Open your OAuth 2.0 Client ID<br>
3. Under <strong>Authorized redirect URIs</strong>, delete any existing Replit URIs and add exactly the URI shown above<br>
4. Click Save and wait 2–5 minutes</p>
</body></html>"""
    return html


@google_auth.route("/google_login")
def login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logger.error("Google OAuth not configured")
        flash('Google authentication is not configured. Please contact support.', 'error')
        return redirect(url_for('login'))

    try:
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]

        _client = WebApplicationClient(GOOGLE_CLIENT_ID)
        request_uri = _client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=REDIRECT_URL,
            scope=["openid", "email", "profile"],
        )
        logger.info(f"Redirecting to Google — redirect_uri={REDIRECT_URL}")
        return redirect(request_uri)
    except Exception as e:
        logger.error(f"Error during Google login: {str(e)}", exc_info=True)
        flash('An error occurred during Google authentication. Please try again.', 'error')
        return redirect(url_for('login'))


@google_auth.route("/google_login/callback")
def callback():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logger.error("Google OAuth not configured for callback")
        flash('Google authentication is not configured.', 'error')
        return redirect(url_for('login'))

    # Log any error Google sent back (e.g. redirect_uri_mismatch)
    error = request.args.get("error")
    if error:
        error_desc = request.args.get("error_description", "no description")
        logger.error(f"Google OAuth error returned: {error} — {error_desc}")
        flash(f'Google sign-in was rejected: {error}. Please try again.', 'error')
        return redirect(url_for('login'))

    try:
        code = request.args.get("code")
        if not code:
            logger.warning(f"No code in callback. Args: {dict(request.args)}")
            flash('Authorization failed. Please try again.', 'error')
            return redirect(url_for('login'))

        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        token_endpoint = google_provider_cfg["token_endpoint"]

        _client = WebApplicationClient(GOOGLE_CLIENT_ID)
        token_url, headers, body = _client.prepare_token_request(
            token_endpoint,
            authorization_response=request.url.replace("http://", "https://"),
            redirect_url=REDIRECT_URL,
            code=code,
        )
        token_response = requests.post(
            token_url,
            headers=headers,
            data=body,
            auth=(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
        )

        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            flash('Failed to authenticate with Google. Please try again.', 'error')
            return redirect(url_for('login'))

        _client.parse_request_body_response(json.dumps(token_response.json()))

        userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
        uri, headers, body = _client.add_token(userinfo_endpoint)
        userinfo_response = requests.get(uri, headers=headers, data=body)

        userinfo = userinfo_response.json()
        if userinfo.get("email_verified"):
            users_email = userinfo["email"]
            users_name = userinfo.get("given_name", userinfo.get("name", users_email.split('@')[0]))
        else:
            logger.warning(f"Google email not verified for user")
            flash('Please verify your email with Google and try again.', 'error')
            return redirect(url_for('login'))

        # Check for existing user in current tenant
        user = TenantQuery(User).filter_by(email=users_email).first()
        if not user:
            # Create new user bound to current tenant
            user = create_for_tenant(User,
                username=users_name,
                email=users_email,
                is_verified=True
            )
            db.session.add(user)
            db.session.commit()
            logger.info(f"New user created via Google OAuth: {users_email}")
        else:
            logger.info(f"Existing user logged in via Google OAuth: {users_email}")

        if not user.is_active:
            flash('Your account has been deactivated. Please contact support.', 'error')
            return redirect(url_for('login'))

        # Auto-promote to admin if email is listed in ADMIN_EMAILS env var
        admin_emails_raw = os.environ.get('ADMIN_EMAILS', '')
        admin_emails = [e.strip().lower() for e in admin_emails_raw.split(',') if e.strip()]
        if users_email.lower() in admin_emails and not user.is_admin:
            user.is_admin = True
            db.session.commit()
            logger.info(f"Auto-promoted {users_email} to admin via ADMIN_EMAILS env var")

        login_user(user)
        flash('Successfully logged in with Google!', 'success')
        logger.info(f"User {users_email} logged in successfully via Google OAuth")

        return redirect(url_for("dashboard"))
    
    except Exception as e:
        logger.error(f"Error during Google OAuth callback: {str(e)}", exc_info=True)
        flash('An error occurred during authentication. Please try again.', 'error')
        return redirect(url_for('login'))


@google_auth.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))
