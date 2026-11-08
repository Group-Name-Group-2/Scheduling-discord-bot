"""
auth_email_routes.py

HTTP routes for email magic-link sign-in (sending the link and opening it).
"""

import requests
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from auth_email import request_magic_link, verify_magic_link
from email_service import app_base_url
from steam_api import get_player_summaries

router = APIRouter()

@router.post("/auth/email/request")
async def request_email_login(request: Request):
    form = await request.form()
    email = str(form.get("email", "")).strip()

    # APP_BASE_URL when set, so a forged Host header can't make the
    # sign-in link (and its token) point at someone else's site.
    base_url = app_base_url(str(request.base_url))
    # Sends an email (SMTP), so run it in a thread to keep the server responsive.
    ok, message = await run_in_threadpool(request_magic_link, email, base_url)

    if not ok:
        return JSONResponse({"error": message}, status_code=400)

    return JSONResponse({"message": message})


@router.get("/auth/email/verify")
def verify_email_login(request: Request, token: str = ""):
    result = verify_magic_link(token)

    if result is None:
        return RedirectResponse(url="/", status_code=303)

    # Establish full user session upon magic link verification
    steam_id = result["steam_id"]
    email = result["email"]

    request.session["steam_id"] = steam_id
    request.session["email"] = email

    # Fetch display name & avatar for navigation bar display
    try:
        profile = get_player_summaries([steam_id]).get(steam_id, {})
    except requests.RequestException:
        profile = {}

    request.session["steam_name"] = profile.get("personaname")
    request.session["steam_avatar"] = profile.get("avatarmedium")

    return RedirectResponse(url="/events", status_code=303)


# Signing out is /auth/logout in steam_auth.py.
