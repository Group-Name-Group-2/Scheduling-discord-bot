"""
auth_email.py

Handles generation, hashing, and database-backed verification 
for email magic links.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from database import confirm_pending_email, get_connection, init_db, set_pending_email
from email_service import is_valid_email, send_email

TOKEN_EXPIRE_MINUTES = 15


def request_magic_link(email: str, base_url: str) -> tuple[bool, str]:
    email = email.strip().lower()
    if not is_valid_email(email):
        return False, "Please provide a valid email address."

    init_db()

    # Look up existing steam_id linked to this email address in user_settings
    with get_connection() as connection:
        row = connection.execute(
            "SELECT steam_id FROM user_settings WHERE email = ?",
            (email,),
        ).fetchone()

    if not row:
        return False, "No registered account found with that email address. Please sign in with Steam first to link your email."

    steam_id = row[0]

    # Generate a secure random token and store its SHA-256 hash in SQLite
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    ).isoformat()

    # Save pending email state and verification token hash
    set_pending_email(
        steam_id=steam_id,
        email=email,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    params = urlencode({"token": raw_token})
    verify_url = f"{base_url}/auth/email/verify?{params}"

    subject = "Sign in to Game Night"
    text = (
        f"Click the link below to sign in to your Game Night account:\n\n"
        f"    {verify_url}\n\n"
        f"This link expires in {TOKEN_EXPIRE_MINUTES} minutes."
    )
    html = f"""\
<div style="font-family: Arial, sans-serif; max-width: 500px;">
  <h2>Sign in to Game Night</h2>
  <p>Click the button below to complete your sign in:</p>
  <p>
    <a href="{verify_url}" style="display: inline-block; padding: 10px 20px; background-color: #1a9fff; color: white; text-decoration: none; border-radius: 5px;">
      Sign In
    </a>
  </p>
  <p style="color: #666; font-size: 12px;">This link will expire in {TOKEN_EXPIRE_MINUTES} minutes.</p>
</div>
"""

    sent = send_email(email, subject, text, html)
    if not sent:
        return False, "Failed to send email. Please try again later."

    return True, "Check your inbox for your sign-in link!"


def verify_magic_link(raw_token: str) -> dict | None:
    if not raw_token:
        return None

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    result = confirm_pending_email(token_hash)
    return result  # Returns {"steam_id": ..., "email": ...} or None