"""
auth_email_routes.py

HTTP routes for email magic-link sign-in. Wired into main.py the same
way steam_auth_router already is:

    from auth_email_routes import router as email_auth_router
    app.include_router(email_auth_router)
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth_email import request_magic_link, verify_magic_link

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/login")
def login_page(request: Request):
    # If the person is already signed in via either method, send them
    # straight to the app instead of showing the sign-in form again.
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
    email = form.get("email", "")

    base_url = str(request.base_url).rstrip("/")
    ok, message = request_magic_link(email, base_url)

    if not ok:
        return JSONResponse({"error": message}, status_code=400)

    return JSONResponse({"message": message})


@router.get("/auth/email/verify")
def verify_email_login(request: Request, token: str = ""):
    email = verify_magic_link(token)

    if email is None:
        return RedirectResponse(url="/login?error=invalid_token", status_code=303)

    request.session["email"] = email
    return RedirectResponse(url="/events", status_code=303)


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return JSONResponse({"message": "You have been signed out."})