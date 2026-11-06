"""
admin_routes.py

Admin dashboard (only while admin mode is on, see permissions.py):

- /admin                         list of users and events
- /admin/users/{steam_id}        set/remove a user's email, open their schedule
- /admin/events/{event_id}       edit or delete any event

Editing someone's weekend schedule reuses the normal Availability page:
/availability?user={steam_id} (see availability_routes.py).
"""

from datetime import datetime
from urllib.parse import quote
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    admin_set_email,
    delete_event,
    get_event,
    get_saved_friends,
    get_saved_games,
    get_user_settings,
    list_all_events,
    list_users,
    update_event,
    user_exists,
)
from email_service import is_valid_email
from permissions import admin_mode_active
from steam_api import get_player_summaries

router = APIRouter(prefix="/admin")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

ALLOWED_DURATIONS = [60, 90, 120, 180, 240]

STATUS_MESSAGES = {
    "email_saved": ("Email address updated.", False),
    "email_removed": ("Email address removed.", False),
    "email_invalid": ("That doesn't look like a valid email address.", True),
    "event_saved": ("Event updated.", False),
    "event_deleted": ("Event deleted.", False),
    "event_missing": ("That event no longer exists.", True),
}


def _forbidden() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Admins only</h1><p>Turn on admin mode in "
        '<a href="/settings">Settings</a> to use this page.</p>',
        status_code=403,
    )


def _status(code: str) -> tuple[str | None, bool]:
    return STATUS_MESSAGES.get(code, (None, False))


def _names(steam_ids: list[str]) -> dict[str, dict]:
    """steam_id -> {"name", "avatar"}; falls back to the ID if Steam is down."""
    ids = list(dict.fromkeys(steam_ids))

    try:
        profiles = get_player_summaries(ids)
    except (requests.RequestException, RuntimeError):
        profiles = {}

    return {
        steam_id: {
            "name": profiles.get(steam_id, {}).get("personaname", steam_id),
            "avatar": profiles.get(steam_id, {}).get("avatarmedium"),
        }
        for steam_id in ids
    }


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

@router.get("")
def dashboard(request: Request, status: str = ""):
    if not admin_mode_active(request):
        return _forbidden()

    users = list_users()
    events = list_all_events()

    people = _names(
        [u["steam_id"] for u in users]
        + [e["creator_steam_id"] for e in events]
        + [p for e in events for p in e["participant_steam_ids"]]
    )

    today = datetime.now().date().isoformat()
    message, is_error = _status(status)

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "users": users,
            "events": events,
            "people": people,
            "today": today,
            "status_message": message,
            "status_is_error": is_error,
        },
    )


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

@router.get("/users/{steam_id}")
def user_page(request: Request, steam_id: str, status: str = ""):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id):
        return RedirectResponse(url="/admin", status_code=303)

    person = _names([steam_id])[steam_id]
    message, is_error = _status(status)

    return templates.TemplateResponse(
        request=request,
        name="admin_user.html",
        context={
            "user_id": steam_id,
            "person": person,
            "settings": get_user_settings(steam_id),
            "status_message": message,
            "status_is_error": is_error,
        },
    )


@router.post("/users/{steam_id}/email")
async def set_user_email(request: Request, steam_id: str):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id):
        return RedirectResponse(url="/admin", status_code=303)

    form = await request.form()
    back = f"/admin/users/{steam_id}?status="

    if form.get("action") == "remove":
        admin_set_email(steam_id, None)
        return RedirectResponse(url=back + "email_removed", status_code=303)

    email = str(form.get("email", "")).strip().lower()

    if not is_valid_email(email):
        return RedirectResponse(url=back + "email_invalid", status_code=303)

    admin_set_email(steam_id, email)
    return RedirectResponse(url=back + "email_saved", status_code=303)


# ---------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------

def _event_options(event: dict) -> tuple[list[dict], list[str]]:
    """
    Games and participants the event can be changed to: the creator's
    library and friends, plus whatever the event already has (so an
    edit never silently drops something).
    """
    creator = event["creator_steam_id"]

    games = get_saved_games(creator)
    if event["app_id"] not in {g["appid"] for g in games}:
        games.insert(0, {"appid": event["app_id"], "name": event["game_name"]})

    participant_ids = list(dict.fromkeys(
        [f["steamid"] for f in get_saved_friends(creator)]
        + event["participant_steam_ids"]
    ))
    participant_ids = [p for p in participant_ids if p != creator]

    return games, participant_ids


@router.get("/events/{event_id}")
def event_page(request: Request, event_id: int, status: str = "", error: str = ""):
    if not admin_mode_active(request):
        return _forbidden()

    event = get_event(event_id)

    if event is None:
        return RedirectResponse(url="/admin?status=event_missing", status_code=303)

    games, participant_ids = _event_options(event)
    people = _names([event["creator_steam_id"], *participant_ids])
    message, is_error = _status(status)

    return templates.TemplateResponse(
        request=request,
        name="admin_event.html",
        context={
            "event": event,
            "games": games,
            "participant_ids": participant_ids,
            "people": people,
            "durations": ALLOWED_DURATIONS,
            "status_message": error or message,
            "status_is_error": bool(error) or is_error,
        },
    )


@router.post("/events/{event_id}")
async def save_event(request: Request, event_id: int):
    if not admin_mode_active(request):
        return _forbidden()

    event = get_event(event_id)

    if event is None:
        return RedirectResponse(url="/admin?status=event_missing", status_code=303)

    form = await request.form()
    games, participant_ids = _event_options(event)

    def fail(message: str):
        return RedirectResponse(
            url=f"/admin/events/{event_id}?error={quote(message)}",
            status_code=303,
        )

    try:
        app_id = int(form.get("game_id", ""))
        duration = int(form.get("duration", ""))
        event_date = str(form.get("date", ""))
        event_time = str(form.get("time", ""))
        datetime.strptime(event_date, "%Y-%m-%d")
        datetime.strptime(event_time, "%H:%M")
    except (TypeError, ValueError):
        return fail("Please enter a valid game, date, start time and duration.")

    if app_id not in {g["appid"] for g in games}:
        return fail("Choose a game from the list.")

    if duration not in ALLOWED_DURATIONS:
        return fail("Choose a duration from the list.")

    chosen = form.getlist("participant_ids")

    if not set(chosen) <= set(participant_ids):
        return fail("One or more participants are invalid.")

    update_event(event_id, app_id, event_date, event_time, duration, chosen)

    return RedirectResponse(url=f"/admin/events/{event_id}?status=event_saved", status_code=303)


@router.post("/events/{event_id}/delete")
def remove_event(request: Request, event_id: int):
    if not admin_mode_active(request):
        return _forbidden()

    deleted = delete_event(event_id)

    return RedirectResponse(
        url="/admin?status=" + ("event_deleted" if deleted else "event_missing"),
        status_code=303,
    )
