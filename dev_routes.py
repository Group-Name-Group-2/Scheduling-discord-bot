"""
/dev/login: sign in as a test account without Steam (see fake_accounts.py).

Only registered when DEV_LOGIN=1 is in .env, and only answers requests
from this computer, so it can't be used as a backdoor on a server.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from database import list_users, mark_signed_in
from steam_api import get_player_summaries
from fake_accounts import is_test_account

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

router = APIRouter(prefix="/dev")

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in LOCAL_HOSTS:
        raise HTTPException(status_code=404)


@router.get("/login")
def dev_login_page(request: Request):
    _require_local(request)

    test_ids = [u["steam_id"] for u in list_users() if is_test_account(u["steam_id"])]
    profiles = get_player_summaries(test_ids)

    accounts = sorted(
        (
            {
                "steam_id": steam_id,
                "name": profiles.get(steam_id, {}).get("personaname", steam_id),
                "current": steam_id == request.session.get("steam_id"),
            }
            for steam_id in test_ids
        ),
        key=lambda account: int(account["steam_id"]),
    )

    return templates.TemplateResponse(
        request=request,
        name="dev_login.html",
        context={"accounts": accounts},
    )


@router.post("/login/{steam_id}")
def dev_login(request: Request, steam_id: str):
    _require_local(request)

    # Only test accounts: real accounts still have to sign in with Steam.
    if not is_test_account(steam_id):
        raise HTTPException(status_code=400, detail="Not a test account.")

    profile = get_player_summaries([steam_id]).get(steam_id, {})

    # Same session the Steam sign-in sets up, so the rest of the site
    # can't tell the difference.
    request.session.clear()
    request.session["steam_id"] = steam_id
    request.session["steam_name"] = profile.get("personaname")
    request.session["steam_avatar"] = profile.get("avatarmedium") or None
    mark_signed_in(steam_id)

    return RedirectResponse(url="/events", status_code=303)
