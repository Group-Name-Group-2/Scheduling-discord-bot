"""
group_routes.py

Group conversations: chats with three or more people.
(One-to-one chats are in messages_routes.py.)

- GET  /messages/groups/new            pick friends to start a group
- POST /messages/groups/new            create it
- GET  /messages/groups/{id}           the group chat page
- POST /messages/groups/{id}           send a message (JSON response)
- GET  /api/groups/{id}/messages       poll for new messages
- POST /messages/groups/{id}/members   add friends to the group
- POST /messages/groups/{id}/rename    change or clear the group's name
- POST /messages/groups/{id}/leave     leave the group

Rules:
- Only members can read or post in a group.
- You can add people from your own saved Steam friends list.
- Anyone in the group can add people, rename it or leave. The group
  is deleted when the last member leaves.
"""

from pathlib import Path

import requests
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    MAX_GROUP_MEMBERS,
    MAX_GROUP_NAME_LENGTH,
    MAX_MESSAGE_LENGTH,
    add_group_members,
    create_group,
    get_group,
    get_group_messages,
    get_saved_friends,
    is_group_member,
    leave_group,
    mark_group_read,
    rename_group,
    save_group_message,
)
from email_service import app_base_url
from notifications import group_key, mark_viewing, notify_group_message
from steam_api import get_player_summaries

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

STATUS_MESSAGES = {
    "added": ("Added to the group.", False),
    "none_added": ("Pick at least one friend to add.", True),
    "full": (f"Groups can have at most {MAX_GROUP_MEMBERS} people.", True),
    "renamed": ("Group name updated.", False),
    "name_long": (f"Group names can be at most {MAX_GROUP_NAME_LENGTH} characters.", True),
}


# ---------------------------------------------------------------------
# Helpers (also used by messages_routes.py and admin_routes.py)
# ---------------------------------------------------------------------

def people_info(steam_ids) -> dict[str, dict]:
    """steam_id -> {"name", "avatar"}; falls back to the ID if Steam is down."""
    ids = list(dict.fromkeys(i for i in steam_ids if i))

    try:
        profiles = get_player_summaries(ids)
    except (requests.RequestException, RuntimeError):
        profiles = {}

    return {
        steam_id: {
            "name": profiles.get(steam_id, {}).get("personaname", steam_id),
            "avatar": profiles.get(steam_id, {}).get("avatarfull") or None,
        }
        for steam_id in ids
    }


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def group_title(name: str | None, member_ids: list[str], viewer: str, people: dict) -> str:
    """The group's name, or its other members' names if it has none."""
    if name:
        return name

    others = [people.get(m, {}).get("name", m) for m in member_ids if m != viewer]

    if not others:
        return "Just you"
    if len(others) <= 3:
        return _join_names(others)
    return f"{others[0]}, {others[1]} and {len(others) - 2} others"


def message_people_ids(messages: list[dict]) -> list[str]:
    """Everyone a list of group messages mentions (senders + added people)."""
    ids = []
    for message in messages:
        ids.append(message["sender_steam_id"])
        if message.get("sent_by_steam_id"):
            ids.append(message["sent_by_steam_id"])
        if message["kind"] == "added":
            ids.extend(message["body"].split(","))
    return ids


def notice_text(message: dict, viewer: str, people: dict) -> str:
    """Text for 'X added Y' style notices in a group chat."""
    def who(steam_id: str) -> str:
        if steam_id == viewer:
            return "You"
        return people.get(steam_id, {}).get("name", steam_id)

    actor = who(message["sender_steam_id"])
    kind = message["kind"]

    if kind == "created":
        return f"{actor} created the group"
    if kind == "added":
        added = [who(m) for m in message["body"].split(",") if m]
        added = ["you" if a == "You" else a for a in added]
        return f"{actor} added {_join_names(added)}"
    if kind == "left":
        return f"{actor} left the group"
    if kind == "renamed":
        if message["body"]:
            return f"{actor} named the group “{message['body']}”"
        return f"{actor} removed the group name"
    return ""


def decorate_messages(messages: list[dict], viewer: str, people: dict) -> list[dict]:
    """Add sender_name / sender_avatar, and text for notices."""
    for message in messages:
        sender = people.get(message["sender_steam_id"], {})
        message["sender_name"] = sender.get("name", message["sender_steam_id"])
        message["sender_avatar"] = sender.get("avatar")

        if message["kind"] != "message":
            message["text"] = notice_text(message, viewer, people)

    return messages


def _status(code: str) -> tuple[str | None, bool]:
    return STATUS_MESSAGES.get(code, (None, False))


def _clean_group_name(raw) -> str:
    return " ".join(str(raw or "").split())


def _friend_ids(steam_id: str) -> list[str]:
    return [f["steamid"] for f in get_saved_friends(steam_id)]


def _picked_friends(form, steam_id: str) -> list[str]:
    """The member_ids ticked in a form, limited to the user's friends."""
    friends = set(_friend_ids(steam_id))
    return [
        m for m in dict.fromkeys(str(v) for v in form.getlist("member_ids"))
        if m in friends and m != steam_id
    ]


def _members_only(request: Request, group_id: int):
    """(steam_id, group) if the signed-in user is in the group, else (steam_id, None)."""
    steam_id = request.session.get("steam_id")

    if not steam_id or not is_group_member(group_id, steam_id):
        return steam_id, None

    return steam_id, get_group(group_id)


# ---------------------------------------------------------------------
# Creating a group
# ---------------------------------------------------------------------

@router.get("/messages/groups/new")
def new_group_page(request: Request, error: str = "", selected: str = ""):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    friend_ids = _friend_ids(steam_id)
    people = people_info(friend_ids)
    friends = sorted(
        ({"steamid": f, **people[f]} for f in friend_ids),
        key=lambda f: f["name"].lower(),
    )

    return templates.TemplateResponse(
        request=request,
        name="group_new.html",
        context={
            "friends": friends,
            "selected": set(selected.split(",")) if selected else set(),
            "error": error,
            "max_name_length": MAX_GROUP_NAME_LENGTH,
            "max_members": MAX_GROUP_MEMBERS,
        },
    )


@router.post("/messages/groups/new")
async def create_group_route(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    members = _picked_friends(form, steam_id)
    name = _clean_group_name(form.get("name"))
    keep = ",".join(members)

    error = None
    if len(members) < 2:
        error = "Pick at least two friends. For one friend, send a direct message instead."
    elif len(members) + 1 > MAX_GROUP_MEMBERS:
        error = f"Groups can have at most {MAX_GROUP_MEMBERS} people, including you."
    elif len(name) > MAX_GROUP_NAME_LENGTH:
        error = f"Group names can be at most {MAX_GROUP_NAME_LENGTH} characters."

    if error:
        return new_group_page(request, error=error, selected=keep)

    group_id = create_group(steam_id, members, name or None)
    return RedirectResponse(url=f"/messages/groups/{group_id}", status_code=303)


# ---------------------------------------------------------------------
# The group chat
# ---------------------------------------------------------------------

@router.get("/messages/groups/{group_id}")
def group_page(request: Request, group_id: int, status: str = ""):
    steam_id, group = _members_only(request, group_id)

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)
    if group is None:
        return RedirectResponse(url="/messages", status_code=303)

    messages = get_group_messages(group_id)
    mark_group_read(group_id, steam_id)
    mark_viewing(steam_id, group_key(group_id))

    # Friends who could still be added.
    member_set = set(group["member_ids"])
    addable_ids = [f for f in _friend_ids(steam_id) if f not in member_set]

    people = people_info([*group["member_ids"], *message_people_ids(messages), *addable_ids])
    decorate_messages(messages, steam_id, people)

    addable = sorted(
        ({"steamid": f, **people[f]} for f in addable_ids),
        key=lambda f: f["name"].lower(),
    )
    message, is_error = _status(status)

    return templates.TemplateResponse(
        request=request,
        name="group_chat.html",
        context={
            "steam_id": steam_id,
            "group": group,
            "title": group_title(group["name"], group["member_ids"], steam_id, people),
            "members": [{"steamid": m, **people[m]} for m in group["member_ids"]],
            "addable": addable,
            "room_left": MAX_GROUP_MEMBERS - len(group["member_ids"]),
            "messages": messages,
            "last_message_id": messages[-1]["message_id"] if messages else 0,
            "max_length": MAX_MESSAGE_LENGTH,
            "max_name_length": MAX_GROUP_NAME_LENGTH,
            "status_message": message,
            "status_is_error": is_error,
        },
    )


@router.post("/messages/groups/{group_id}")
async def send_group_message(
    request: Request,
    group_id: int,
    background_tasks: BackgroundTasks,
):
    steam_id, group = _members_only(request, group_id)

    if not steam_id:
        return JSONResponse({"error": "Please sign in with Steam first."}, status_code=401)
    if group is None:
        return JSONResponse({"error": "You're not in this group."}, status_code=403)

    form = await request.form()
    body = str(form.get("body", "")).strip()

    if not body:
        return JSONResponse({"error": "Message can't be empty."}, status_code=400)

    if len(body) > MAX_MESSAGE_LENGTH:
        return JSONResponse(
            {"error": f"Messages can be at most {MAX_MESSAGE_LENGTH} characters."},
            status_code=400,
        )

    message = save_group_message(group_id, steam_id, body)
    # people_info may call Steam, so keep it off the event loop.
    people = await run_in_threadpool(people_info, group["member_ids"])
    decorate_messages([message], steam_id, people)

    # Each member gets a title from their own point of view
    # ("Alice and Bob" for Carol), so use the sender's name list only
    # when the group has no name.
    background_tasks.add_task(
        notify_group_message,
        group_id=group_id,
        group_title=group["name"] or "a group chat",
        sender_steam_id=steam_id,
        sender_name=people.get(steam_id, {}).get("name") or "A friend",
        member_ids=group["member_ids"],
        body=body,
        base_url=app_base_url(str(request.base_url)),
    )

    return JSONResponse({"message": message})


@router.get("/api/groups/{group_id}/messages")
def poll_group_messages(request: Request, group_id: int, after: int = 0):
    steam_id, group = _members_only(request, group_id)

    if not steam_id:
        return JSONResponse({"error": "Please sign in with Steam first."}, status_code=401)
    if group is None:
        return JSONResponse({"error": "Conversation not found."}, status_code=404)

    messages = get_group_messages(group_id, after_id=after)
    mark_viewing(steam_id, group_key(group_id))

    if messages:
        mark_group_read(group_id, steam_id)

    people = people_info([*group["member_ids"], *message_people_ids(messages)])
    decorate_messages(messages, steam_id, people)

    return JSONResponse({"messages": messages})


# ---------------------------------------------------------------------
# Managing the group
# ---------------------------------------------------------------------

@router.post("/messages/groups/{group_id}/members")
async def add_members_route(request: Request, group_id: int):
    steam_id, group = _members_only(request, group_id)

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)
    if group is None:
        return RedirectResponse(url="/messages", status_code=303)

    form = await request.form()
    back = f"/messages/groups/{group_id}?status="
    members = _picked_friends(form, steam_id)

    if not members:
        return RedirectResponse(url=back + "none_added", status_code=303)

    if len(group["member_ids"]) + len(set(members) - set(group["member_ids"])) > MAX_GROUP_MEMBERS:
        return RedirectResponse(url=back + "full", status_code=303)

    added = add_group_members(group_id, steam_id, members)
    return RedirectResponse(url=back + ("added" if added else "none_added"), status_code=303)


@router.post("/messages/groups/{group_id}/rename")
async def rename_group_route(request: Request, group_id: int):
    steam_id, group = _members_only(request, group_id)

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)
    if group is None:
        return RedirectResponse(url="/messages", status_code=303)

    form = await request.form()
    back = f"/messages/groups/{group_id}?status="
    name = _clean_group_name(form.get("name"))

    if len(name) > MAX_GROUP_NAME_LENGTH:
        return RedirectResponse(url=back + "name_long", status_code=303)

    if (name or None) != group["name"]:
        rename_group(group_id, steam_id, name or None)

    return RedirectResponse(url=back + "renamed", status_code=303)


@router.post("/messages/groups/{group_id}/leave")
def leave_group_route(request: Request, group_id: int):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    leave_group(group_id, steam_id)
    return RedirectResponse(url="/messages", status_code=303)
