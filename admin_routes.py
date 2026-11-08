"""
admin_routes.py

Admin dashboard (only while admin mode is on, see permissions.py):

- /admin                         list of users and events
- /admin/users/{steam_id}        change a user's display name, picture and
                                 email, open their schedule
- /admin/users/{steam_id}/messages
                                 pick one of a user's conversations
- /admin/events/{event_id}       edit or delete any event
- /admin/games/ignore            hide a game everywhere (ignored_games)
- /admin/games/{app_id}/unignore stop hiding it
- /admin/libraries/refresh       re-sync every user's library from Steam
                                 (runs in the background)
- /admin/libraries/max-age       how long a synced library counts as recent
                                 (other syncs reuse it instead of calling Steam)
- /admin/content-check          rate unchecked games and auto-hide adult
                                 ones (see content_filter.py)
- /admin/users/{steam_id}/messages/{other_id}
                                 read a user's conversation and send
                                 messages as that user
- /admin/users/{steam_id}/groups/{group_id}
                                 the same for one of their group chats

Editing someone's weekend schedule reuses the normal Availability page:
/availability?user={steam_id} (see availability_routes.py).
"""

import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote
from pathlib import Path

import requests
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    MAX_MESSAGE_LENGTH,
    admin_set_avatar,
    admin_set_display_name,
    admin_set_email,
    content_rating_counts,
    delete_event,
    get_conversation,
    get_conversation_summaries,
    get_event,
    get_group,
    get_group_messages,
    get_group_summaries,
    is_group_member,
    get_profile_overrides,
    get_saved_friends,
    get_saved_games,
    get_user_settings,
    ignore_game,
    list_all_events,
    list_ignored_games,
    list_known_games,
    list_unknown_rated_games,
    list_users,
    restore_game_ownership,
    save_group_message,
    save_owned_games,
    set_setting,
    save_message,
    unignore_game,
    update_event,
    user_exists,
)
import content_filter
from email_service import app_base_url, is_valid_email
from library_service import (
    LIBRARY_MAX_AGE_SETTING,
    MAX_LIBRARY_MAX_AGE_MINUTES,
    library_max_age_minutes,
)
from group_routes import decorate_messages, group_title, message_people_ids, people_info
from messages_routes import _can_message
from notifications import notify_group_message, notify_new_message
from permissions import admin_mode_active
from steam_api import get_owned_games, get_player_summaries
from test_accounts import dev_login_enabled, is_test_account

router = APIRouter(prefix="/admin")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

ALLOWED_DURATIONS = [60, 90, 120, 180, 240]

# Uploaded profile pictures are served from /static/avatars/.
AVATAR_DIR = BASE_DIR / "static" / "avatars"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_DISPLAY_NAME_LENGTH = 32

# Check the file's first bytes rather than trusting its name/content type.
AVATAR_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]

STATUS_MESSAGES = {
    "email_saved": ("Email address updated.", False),
    "email_removed": ("Email address removed.", False),
    "email_invalid": ("That doesn't look like a valid email address.", True),
    "name_saved": ("Display name updated.", False),
    "name_reset": ("Display name reset to their Steam name.", False),
    "name_invalid": (f"Display names must be 1-{MAX_DISPLAY_NAME_LENGTH} characters.", True),
    "avatar_saved": ("Profile picture updated.", False),
    "avatar_reset": ("Profile picture reset to their Steam avatar.", False),
    "avatar_missing": ("Choose an image to upload.", True),
    "avatar_large": ("Pictures can be at most 2 MB.", True),
    "avatar_type": ("Pictures must be PNG, JPEG, GIF or WebP.", True),
    "event_saved": ("Event updated.", False),
    "event_deleted": ("Event deleted.", False),
    "event_missing": ("That event no longer exists.", True),
    "message_sent": ("Message sent.", False),
    "message_empty": ("Message can't be empty.", True),
    "message_long": (f"Messages can be at most {MAX_MESSAGE_LENGTH} characters.", True),
    "game_invalid": ("Enter a Steam app ID (a number).", True),
    "refresh_started": ("Refreshing every library from Steam. Reload this page to see when it's done.", False),
    "refresh_running": ("A library refresh is already running.", True),
    "max_age_saved": ("Recent-library time saved.", False),
    "max_age_invalid": ("Enter a whole number of minutes from 0 to 10080 (one week).", True),
    "content_started": ("Checking games for adult content. Reload this page to see progress.", False),
    "content_running": ("A content check is already running.", True),
    "content_nothing": ("Every game has already been checked.", False),
}

# State of the "refresh all libraries" background job. Kept in memory:
# it's lost on restart, which only means the dashboard forgets the last
# result (and a refresh interrupted by a restart simply stops).
_refresh_lock = threading.Lock()
_refresh_state: dict = {"running": False, "started_at": None, "finished_at": None,
                        "updated": 0, "unavailable": 0, "total": 0}


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
def dashboard(request: Request, status: str = "", notice: str = ""):
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
            "ignored_games": list_ignored_games(),
            "refresh": dict(_refresh_state),
            "library_max_age": library_max_age_minutes(),
            "max_library_max_age": MAX_LIBRARY_MAX_AGE_MINUTES,
            "content": dict(content_filter.state),
            "content_counts": content_rating_counts(),
            "unknown_games": list_unknown_rated_games(),
            "known_games": list_known_games(),
            "people": people,
            "today": today,
            "dev_login": dev_login_enabled(),
            "status_message": message or notice[:300] or None,
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
    override = get_profile_overrides([steam_id]).get(steam_id, {})
    message, is_error = _status(status)

    # The name on their Steam account, ignoring any override set here.
    try:
        steam_name = get_player_summaries([steam_id], apply_overrides=False).get(
            steam_id, {}
        ).get("personaname")
    except (requests.RequestException, RuntimeError):
        steam_name = None

    return templates.TemplateResponse(
        request=request,
        name="admin_user.html",
        context={
            "user_id": steam_id,
            "person": person,
            "override": override,
            "steam_name": steam_name,
            "max_name_length": MAX_DISPLAY_NAME_LENGTH,
            "settings": get_user_settings(steam_id),
            "is_test": is_test_account(steam_id),
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


def _refresh_own_session(request: Request, steam_id: str) -> None:
    """
    The navbar reads the name and avatar from the session, which is set
    at sign-in. If the admin edited their own profile, update it now;
    other users see the change next time they sign in.
    """
    if request.session.get("steam_id") != steam_id:
        return

    try:
        profile = get_player_summaries([steam_id]).get(steam_id, {})
    except (requests.RequestException, RuntimeError):
        return

    request.session["steam_name"] = profile.get("personaname")
    request.session["steam_avatar"] = profile.get("avatarmedium")


@router.post("/users/{steam_id}/name")
async def set_user_name(request: Request, steam_id: str):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id):
        return RedirectResponse(url="/admin", status_code=303)

    form = await request.form()
    back = f"/admin/users/{steam_id}?status="

    if form.get("action") == "reset":
        admin_set_display_name(steam_id, None)
        await run_in_threadpool(_refresh_own_session, request, steam_id)
        return RedirectResponse(url=back + "name_reset", status_code=303)

    # Collapse whitespace so names stay on one line everywhere
    # (navbar, email subjects, ...).
    name = " ".join(str(form.get("display_name", "")).split())

    if not 1 <= len(name) <= MAX_DISPLAY_NAME_LENGTH:
        return RedirectResponse(url=back + "name_invalid", status_code=303)

    admin_set_display_name(steam_id, name)
    await run_in_threadpool(_refresh_own_session, request, steam_id)
    return RedirectResponse(url=back + "name_saved", status_code=303)


def _image_extension(data: bytes) -> str | None:
    for signature, extension in AVATAR_SIGNATURES:
        if data.startswith(signature):
            return extension

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    return None


def _delete_uploaded_avatar(steam_id: str) -> None:
    """Remove the user's previous upload, if they had one."""
    current = get_profile_overrides([steam_id]).get(steam_id, {}).get("avatar_path")

    if current and current.startswith("/static/avatars/"):
        old_file = AVATAR_DIR / Path(current).name
        old_file.unlink(missing_ok=True)


@router.post("/users/{steam_id}/avatar")
async def set_user_avatar(request: Request, steam_id: str):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id):
        return RedirectResponse(url="/admin", status_code=303)

    form = await request.form()
    back = f"/admin/users/{steam_id}?status="

    if form.get("action") == "reset":
        _delete_uploaded_avatar(steam_id)
        admin_set_avatar(steam_id, None)
        await run_in_threadpool(_refresh_own_session, request, steam_id)
        return RedirectResponse(url=back + "avatar_reset", status_code=303)

    upload = form.get("avatar")

    if upload is None or isinstance(upload, str) or not upload.filename:
        return RedirectResponse(url=back + "avatar_missing", status_code=303)

    data = await upload.read(MAX_AVATAR_BYTES + 1)

    if not data:
        return RedirectResponse(url=back + "avatar_missing", status_code=303)
    if len(data) > MAX_AVATAR_BYTES:
        return RedirectResponse(url=back + "avatar_large", status_code=303)

    extension = _image_extension(data)
    if extension is None:
        return RedirectResponse(url=back + "avatar_type", status_code=303)

    # Random part in the name so browsers don't show a cached old picture.
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{steam_id}-{secrets.token_hex(4)}.{extension}"
    (AVATAR_DIR / filename).write_bytes(data)

    _delete_uploaded_avatar(steam_id)
    admin_set_avatar(steam_id, f"/static/avatars/{filename}")
    await run_in_threadpool(_refresh_own_session, request, steam_id)
    return RedirectResponse(url=back + "avatar_saved", status_code=303)


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


# ---------------------------------------------------------------------
# Ignored games
# ---------------------------------------------------------------------

@router.post("/games/ignore")
async def add_ignored_game(request: Request):
    if not admin_mode_active(request):
        return _forbidden()

    form = await request.form()

    try:
        app_id = int(str(form.get("app_id", "")).strip())
    except ValueError:
        return RedirectResponse(url="/admin?status=game_invalid#games-heading", status_code=303)

    if app_id <= 0:
        return RedirectResponse(url="/admin?status=game_invalid#games-heading", status_code=303)

    name = " ".join(str(form.get("name", "")).split())[:200] or None
    name, removed = ignore_game(app_id, name)

    libraries = "1 library" if removed == 1 else f"{removed} libraries"
    message = quote(f"{name} hidden and removed from {libraries}.")
    return RedirectResponse(url=f"/admin?notice={message}#games-heading", status_code=303)


def _fetch_library(steam_id: str) -> list | None:
    """A user's Steam games, or None if Steam is down or the library is private."""
    try:
        return get_owned_games(steam_id) or None
    except (requests.RequestException, RuntimeError, ValueError):
        return None


@router.post("/games/{app_id}/unignore")
def remove_ignored_game(request: Request, app_id: int):
    if not admin_mode_active(request):
        return _forbidden()

    hidden = {g["app_id"]: g for g in list_ignored_games()}.get(app_id)

    if hidden is None or not unignore_game(app_id):
        return RedirectResponse(url="/admin#games-heading", status_code=303)

    # Hiding deleted everyone's ownership of the game, so ask Steam again
    # for every user's library and put it back where it's owned.
    steam_ids = [u["steam_id"] for u in list_users()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        libraries = dict(zip(steam_ids, pool.map(_fetch_library, steam_ids)))

    owners: dict[str, int] = {}
    name = hidden["name"]
    unavailable = 0

    for steam_id, games in libraries.items():
        if games is None:
            unavailable += 1
            continue

        for game in games:
            if game.get("appid") == app_id:
                owners[steam_id] = game.get("playtime_forever", 0)
                name = game.get("name") or name
                break

    restored = restore_game_ownership(app_id, name, owners)

    libraries_text = "1 library" if restored == 1 else f"{restored} libraries"
    text = f"{name} un-hidden and added back to {libraries_text}."
    if unavailable:
        text += (
            f" Couldn't check {unavailable} user(s) (private library or Steam unavailable);"
            " it'll appear for them on their next sync."
        )

    return RedirectResponse(url=f"/admin?notice={quote(text)}#games-heading", status_code=303)


# ---------------------------------------------------------------------
# Refresh every library
# ---------------------------------------------------------------------

def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _refresh_one(steam_id: str) -> bool:
    """Re-sync one library. False if Steam is down or the library is private."""
    games = _fetch_library(steam_id)

    # Like library_service: an empty result keeps the saved library.
    if games is None:
        return False

    save_owned_games(steam_id, games, mark_synced=False)
    return True


def _refresh_all_libraries(steam_ids: list[str]) -> None:
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for ok in pool.map(_refresh_one, steam_ids):
                if ok:
                    _refresh_state["updated"] += 1
                else:
                    _refresh_state["unavailable"] += 1
    finally:
        _refresh_state["running"] = False
        _refresh_state["finished_at"] = _now_text()
        content_filter.start_check()  # rate any games the refresh added


@router.post("/libraries/refresh")
def refresh_libraries(request: Request, background_tasks: BackgroundTasks):
    if not admin_mode_active(request):
        return _forbidden()

    back = "/admin?status={}#libraries-heading"

    with _refresh_lock:
        if _refresh_state["running"]:
            return RedirectResponse(url=back.format("refresh_running"), status_code=303)

        steam_ids = [u["steam_id"] for u in list_users()]
        _refresh_state.update(
            running=True, started_at=_now_text(), finished_at=None,
            updated=0, unavailable=0, total=len(steam_ids),
        )

    background_tasks.add_task(_refresh_all_libraries, steam_ids)
    return RedirectResponse(url=back.format("refresh_started"), status_code=303)


@router.post("/libraries/max-age")
async def set_library_max_age(request: Request):
    if not admin_mode_active(request):
        return _forbidden()

    form = await request.form()
    back = "/admin?status={}#libraries-heading"

    try:
        minutes = int(str(form.get("minutes", "")).strip())
    except ValueError:
        return RedirectResponse(url=back.format("max_age_invalid"), status_code=303)

    if not 0 <= minutes <= MAX_LIBRARY_MAX_AGE_MINUTES:
        return RedirectResponse(url=back.format("max_age_invalid"), status_code=303)

    set_setting(LIBRARY_MAX_AGE_SETTING, str(minutes))
    return RedirectResponse(url=back.format("max_age_saved"), status_code=303)


@router.post("/content-check")
def check_content(request: Request):
    if not admin_mode_active(request):
        return _forbidden()

    if content_filter.state["running"]:
        status = "content_running"
    elif content_filter.start_check():
        status = "content_started"
    else:
        status = "content_nothing"

    return RedirectResponse(url=f"/admin?status={status}#content-heading", status_code=303)


# ---------------------------------------------------------------------
# Messages as another user
# ---------------------------------------------------------------------

@router.get("/users/{steam_id}/messages")
def user_messages(request: Request, steam_id: str):
    """A user's conversations and friends, to pick who to message as them."""
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id):
        return RedirectResponse(url="/admin", status_code=303)

    conversations = get_conversation_summaries(steam_id)
    talked_to = {c["steamid"] for c in conversations}
    new_contacts = [
        f["steamid"] for f in get_saved_friends(steam_id)
        if f["steamid"] not in talked_to
    ]

    groups = get_group_summaries(steam_id)
    group_member_ids = [m for g in groups for m in g["member_ids"]]

    people = _names([steam_id, *talked_to, *new_contacts, *group_member_ids])

    for group in groups:
        group["title"] = group_title(group["name"], group["member_ids"], steam_id, people)

    return templates.TemplateResponse(
        request=request,
        name="admin_messages.html",
        context={
            "user_id": steam_id,
            "person": people[steam_id],
            "people": people,
            "conversations": conversations,
            "groups": groups,
            "new_contacts": new_contacts,
        },
    )


@router.get("/users/{steam_id}/groups/{group_id}")
def user_group(request: Request, steam_id: str, group_id: int, status: str = ""):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id) or not is_group_member(group_id, steam_id):
        return RedirectResponse(url=f"/admin/users/{steam_id}/messages", status_code=303)

    # Like direct chats: viewing here doesn't mark anything as read.
    group = get_group(group_id)
    messages = get_group_messages(group_id, include_audit=True)
    people = people_info([steam_id, *group["member_ids"], *message_people_ids(messages)])
    decorate_messages(messages, steam_id, people)
    message, is_error = _status(status)

    return templates.TemplateResponse(
        request=request,
        name="admin_group.html",
        context={
            "user_id": steam_id,
            "group": group,
            "title": group_title(group["name"], group["member_ids"], steam_id, people),
            "people": people,
            "messages": messages,
            "max_length": MAX_MESSAGE_LENGTH,
            "status_message": message,
            "status_is_error": is_error,
        },
    )


@router.post("/users/{steam_id}/groups/{group_id}")
async def send_as_user_in_group(
    request: Request,
    steam_id: str,
    group_id: int,
    background_tasks: BackgroundTasks,
):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id) or not is_group_member(group_id, steam_id):
        return RedirectResponse(url=f"/admin/users/{steam_id}/messages", status_code=303)

    form = await request.form()
    body = str(form.get("body", "")).strip()
    back = f"/admin/users/{steam_id}/groups/{group_id}?status="

    if not body:
        return RedirectResponse(url=back + "message_empty", status_code=303)

    if len(body) > MAX_MESSAGE_LENGTH:
        return RedirectResponse(url=back + "message_long", status_code=303)

    group = get_group(group_id)
    save_group_message(group_id, steam_id, body, sent_by_steam_id=request.session["steam_id"])

    sender_name = (await run_in_threadpool(_names, [steam_id]))[steam_id]["name"]

    background_tasks.add_task(
        notify_group_message,
        group_id=group_id,
        group_title=group["name"] or "a group chat",
        sender_steam_id=steam_id,
        sender_name=sender_name,
        member_ids=group["member_ids"],
        body=body,
        base_url=app_base_url(str(request.base_url)),
    )

    return RedirectResponse(url=back + "message_sent", status_code=303)


@router.get("/users/{steam_id}/messages/{other_id}")
def user_conversation(request: Request, steam_id: str, other_id: str, status: str = ""):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id) or not _can_message(steam_id, other_id):
        return RedirectResponse(url=f"/admin/users/{steam_id}/messages", status_code=303)

    # include_audit shows which messages an admin sent. Viewing here does
    # NOT mark anything as read, so the real user still sees them as new.
    messages = get_conversation(steam_id, other_id, include_audit=True)
    people = _names([steam_id, other_id, *(m["sent_by_steam_id"] for m in messages if m["sent_by_steam_id"])])
    message, is_error = _status(status)

    return templates.TemplateResponse(
        request=request,
        name="admin_conversation.html",
        context={
            "user_id": steam_id,
            "other_id": other_id,
            "people": people,
            "messages": messages,
            "max_length": MAX_MESSAGE_LENGTH,
            "status_message": message,
            "status_is_error": is_error,
        },
    )


@router.post("/users/{steam_id}/messages/{other_id}")
async def send_as_user(
    request: Request,
    steam_id: str,
    other_id: str,
    background_tasks: BackgroundTasks,
):
    if not admin_mode_active(request):
        return _forbidden()

    if not user_exists(steam_id) or not _can_message(steam_id, other_id):
        return RedirectResponse(url=f"/admin/users/{steam_id}/messages", status_code=303)

    form = await request.form()
    body = str(form.get("body", "")).strip()
    back = f"/admin/users/{steam_id}/messages/{other_id}?status="

    if not body:
        return RedirectResponse(url=back + "message_empty", status_code=303)

    if len(body) > MAX_MESSAGE_LENGTH:
        return RedirectResponse(url=back + "message_long", status_code=303)

    save_message(
        steam_id,
        other_id,
        body,
        sent_by_steam_id=request.session["steam_id"],
    )

    sender_name = (await run_in_threadpool(_names, [steam_id]))[steam_id]["name"]

    # The recipient is notified exactly as if the user had sent it.
    background_tasks.add_task(
        notify_new_message,
        sender_steam_id=steam_id,
        sender_name=sender_name,
        recipient_steam_id=other_id,
        body=body,
        base_url=app_base_url(str(request.base_url)),
    )

    return RedirectResponse(url=back + "message_sent", status_code=303)
