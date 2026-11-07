"""
auth_email_routes.py

HTTP routes for email magic-link sign-in.
"""

from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth_email import request_magic_link, verify_magic_link
from steam_api import get_player_summaries

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/login")
def login_page(request: Request):
    if request.session.get("steam_id") or request.session.get("email"):
        return RedirectResponse(url="/events", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "current_user_email": request.session.get("email"),
            "steam_id": request.session.get("steam_id"),
        },
    )


@router.post("/auth/email/request")
async def request_email_login(request: Request):
    form = await request.form()
    email = str(form.get("email", "")).strip()

    base_url = str(request.base_url).rstrip("/")
    # Sends an email (SMTP), so run it in a thread to keep the server responsive.
    ok, message = await run_in_threadpool(request_magic_link, email, base_url)

    if not ok:
        return JSONResponse({"error": message}, status_code=400)

    return JSONResponse({"message": message})


@router.get("/auth/email/verify")
def verify_email_login(request: Request, token: str = ""):
    result = verify_magic_link(token)

    if result is None:
        return RedirectResponse(url="/login?error=invalid_token", status_code=303)

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


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return JSONResponse({"message": "You have been signed out."})