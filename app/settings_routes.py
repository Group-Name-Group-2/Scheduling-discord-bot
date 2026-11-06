"""
settings_routes.py

Account settings: link an email address to a Steam account (confirmed
with a verification link) and turn message notification emails on/off.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    confirm_pending_email,
    get_user_settings,
    is_admin,
    remove_user_email,
    set_admin,
    set_notify_messages,
    set_pending_email,
)
from email_service import app_base_url, is_valid_email, send_email
from permissions import admin_mode_active

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

VERIFY_LINK_HOURS = 24
RESEND_COOLDOWN_SECONDS = 60

# TESTING ONLY: lets ANY signed-in user make themselves an admin (and back)
# from the Settings page, by changing steam_users.is_admin directly.
# Set to False before merging this branch. When False, only users already
# flagged as admins in the database can switch admin mode on/off.
ALLOW_SELF_ADMIN_FOR_TESTING = True

# Short codes in the URL after a form submit -> (message, is_error).
STATUS_MESSAGES = {
    "sent": ("Check your inbox: we sent a verification link to {email}.", False),
    "verified": ("Your email address is confirmed.", False),
    "removed": ("Your email address was removed.", False),
    "saved": ("Notification settings saved.", False),
    "invalid": ("That doesn't look like a valid email address.", True),
    "same": ("That address is already linked to your account.", True),
    "wait": ("Please wait a minute before requesting another link.", True),
    "send_failed": ("We couldn't send the verification email. Please try again later.", True),
    "bad_token": ("That verification link is invalid or has expired. Request a new one below.", True),
    "admin_on": ("Admin mode is on.", False),
    "admin_off": ("Admin mode is off. You're now using the site as a regular user.", False),
}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _redirect(status: str) -> RedirectResponse:
    return RedirectResponse(url=f"/settings?status={status}", status_code=303)


@router.get("/settings")
def settings_page(request: Request, status: str = ""):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    settings = get_user_settings(steam_id)

    message, is_error = STATUS_MESSAGES.get(status, (None, False))
    if message:
        message = message.format(email=settings["pending_email"] or "your address")

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "steam_id": steam_id,
            "settings": settings,
            "status_message": message,
            "status_is_error": is_error,
            "can_be_admin": ALLOW_SELF_ADMIN_FOR_TESTING or is_admin(steam_id),
            "self_admin_testing": ALLOW_SELF_ADMIN_FOR_TESTING,
            "admin_mode": admin_mode_active(request),
        },
    )


@router.post("/settings/admin-mode")
async def update_admin_mode(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    turn_on = form.get("admin_mode") == "on"

    if ALLOW_SELF_ADMIN_FOR_TESTING:
        # Testing: the switch changes the database flag itself.
        set_admin(steam_id, turn_on)

    # Only users flagged as admins in the database may switch it on.
    # Anyone may switch it off.
    if turn_on and not is_admin(steam_id):
        return RedirectResponse(url="/settings", status_code=303)

    if turn_on:
        request.session["admin_mode"] = True
    else:
        request.session.pop("admin_mode", None)

    return _redirect("admin_on" if turn_on else "admin_off")


@router.post("/settings/email")
async def request_email_link(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    email = str(form.get("email", "")).strip().lower()

    if not is_valid_email(email):
        return _redirect("invalid")

    settings = get_user_settings(steam_id)

    if email == settings["email"]:
        return _redirect("same")

    # Stop the form from being used to spam an inbox.
    if settings["verify_sent_at"]:
        last_sent = datetime.fromisoformat(settings["verify_sent_at"])
        if datetime.now(timezone.utc) - last_sent < timedelta(seconds=RESEND_COOLDOWN_SECONDS):
            return _redirect("wait")

    # Only a hash of the token is stored, so a leaked database can't be
    # used to confirm someone else's address.
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=VERIFY_LINK_HOURS)
    set_pending_email(steam_id, email, _hash_token(token), expires_at.isoformat())

    link = f"{app_base_url(str(request.base_url))}/settings/email/verify?token={token}"
    name = request.session.get("steam_name") or "your Steam account"

    sent = send_email(
        to=email,
        subject="Confirm your email for Game Night",
        text=(
            f"Click the link below to link this email address to {name} on Game Night:\n\n"
            f"{link}\n\n"
            f"The link expires in {VERIFY_LINK_HOURS} hours. "
            "If you didn't ask for this, you can ignore this email."
        ),
        html=(
            '<div style="font-family: Arial, sans-serif; max-width: 520px;">'
            f"<p>Click the button below to link this email address to "
            f"<strong>{escape(name)}</strong> on Game Night.</p>"
            f'<p><a href="{escape(link)}" style="display:inline-block;padding:10px 18px;'
            'background:#1a9fff;color:#fff;text-decoration:none;border-radius:6px;">'
            "Confirm email address</a></p>"
            f'<p style="color:#6b7785;font-size:13px;">The link expires in {VERIFY_LINK_HOURS} hours. '
            "If you didn't ask for this, you can ignore this email.</p></div>"
        ),
    )

    return _redirect("sent" if sent else "send_failed")


@router.get("/settings/email/verify")
def verify_email(request: Request, token: str = ""):
    result = confirm_pending_email(_hash_token(token)) if token else None

    if result is None:
        if request.session.get("steam_id"):
            return _redirect("bad_token")
        return RedirectResponse(url="/", status_code=303)

    # The link may be opened on a device that isn't signed in;
    # the address is confirmed either way.
    if request.session.get("steam_id") == result["steam_id"]:
        return _redirect("verified")

    return templates.TemplateResponse(
        request=request,
        name="settings_verified.html",
        context={"email": result["email"]},
    )


@router.post("/settings/email/remove")
def remove_email(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    remove_user_email(steam_id)
    return _redirect("removed")


@router.post("/settings/notifications")
async def update_notifications(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    set_notify_messages(steam_id, form.get("notify_messages") == "on")

    return _redirect("saved")
