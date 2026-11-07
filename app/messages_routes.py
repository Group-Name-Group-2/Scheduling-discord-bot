"""
messages_routes.py

Direct messages between Steam friends, and the Messages inbox (which
also lists group chats; those live in group_routes.py).

Rules:
- You must be signed in with Steam.
- You can message someone who is in your saved Steam friends list,
  or anyone who has already messaged you (so replies always work).
"""

from pathlib import Path

import requests
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    MAX_MESSAGE_LENGTH,
    get_conversation,
    get_conversation_summaries,
    get_group_summaries,
    get_saved_friends,
    get_unread_message_count,
    get_user_settings,
    has_messaged,
    mark_conversation_read,
    save_message,
)
from email_service import app_base_url
from group_routes import group_title, message_people_ids, notice_text, people_info
from notifications import mark_viewing, notify_new_message
from steam_api import get_player_summaries

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _can_message(steam_id: str, other_steam_id: str) -> bool:
    if not other_steam_id.isdigit() or other_steam_id == steam_id:
        return False

    friend_ids = {friend["steamid"] for friend in get_saved_friends(steam_id)}

    return other_steam_id in friend_ids or has_messaged(other_steam_id, steam_id)


def _profiles(steam_ids: list[str]) -> dict:
    try:
        return get_player_summaries(steam_ids)
    except (requests.RequestException, RuntimeError):
        return {}


@router.get("/messages")
def messages_inbox(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    conversations = get_conversation_summaries(steam_id)
    groups = get_group_summaries(steam_id)
    friends = get_saved_friends(steam_id)

    # Friends you haven't messaged yet, for starting a new conversation.
    talked_to = {c["steamid"] for c in conversations}
    new_chat_friends = [f for f in friends if f["steamid"] not in talked_to]

    group_messages = [
        {"sender_steam_id": g["last_sender"], "kind": g["last_kind"], "body": g["last_body"]}
        for g in groups
    ]
    people = people_info(
        [c["steamid"] for c in conversations]
        + [f["steamid"] for f in new_chat_friends]
        + [m for g in groups for m in g["member_ids"]]
        + message_people_ids(group_messages)
    )

    for person in conversations + new_chat_friends:
        person["name"] = people[person["steamid"]]["name"]
        person["avatar"] = people[person["steamid"]]["avatar"]

    new_chat_friends.sort(key=lambda f: f["name"].lower())

    # One list of direct and group chats, most recent first.
    inbox = [
        {
            "url": f"/messages/{c['steamid']}",
            "title": c["name"],
            "avatars": [c["avatar"]] if c["avatar"] else [],
            "preview": ("You: " if c["last_from_me"] else "") + c["last_body"],
            "unread_count": c["unread_count"],
            "last_created_at": c["last_created_at"],
            "is_group": False,
        }
        for c in conversations
    ]

    for group, last in zip(groups, group_messages):
        if last["kind"] == "message":
            sender = last["sender_steam_id"]
            who = "You" if sender == steam_id else people[sender]["name"]
            preview = f"{who}: {last['body']}"
        else:
            preview = notice_text(last, steam_id, people)

        others = [m for m in group["member_ids"] if m != steam_id]

        inbox.append({
            "url": f"/messages/groups/{group['group_id']}",
            "title": group_title(group["name"], group["member_ids"], steam_id, people),
            "avatars": [people[m]["avatar"] for m in others if people[m]["avatar"]][:2],
            "preview": preview,
            "unread_count": group["unread_count"],
            "last_created_at": group["last_created_at"],
            "is_group": True,
            "member_count": len(group["member_ids"]),
        })

    inbox.sort(key=lambda item: item["last_created_at"], reverse=True)

    return templates.TemplateResponse(
        request=request,
        name="messages.html",
        context={
            "steam_id": steam_id,
            "conversations": inbox,
            "new_chat_friends": new_chat_friends,
            "user_settings": get_user_settings(steam_id),
        },
    )


@router.get("/messages/{other_steam_id}")
def conversation_page(request: Request, other_steam_id: str):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    if not _can_message(steam_id, other_steam_id):
        return RedirectResponse(url="/messages", status_code=303)

    messages = get_conversation(steam_id, other_steam_id)
    mark_conversation_read(steam_id, other_steam_id)
    mark_viewing(steam_id, other_steam_id)

    profile = _profiles([other_steam_id]).get(other_steam_id, {})

    return templates.TemplateResponse(
        request=request,
        name="conversation.html",
        context={
            "steam_id": steam_id,
            "other_steam_id": other_steam_id,
            "other_name": profile.get("personaname", other_steam_id),
            "other_avatar": profile.get("avatarfull"),
            "messages": messages,
            "last_message_id": messages[-1]["message_id"] if messages else 0,
            "max_length": MAX_MESSAGE_LENGTH,
        },
    )


@router.post("/messages/{other_steam_id}")
async def send_message(
    request: Request,
    other_steam_id: str,
    background_tasks: BackgroundTasks,
):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse(
            {"error": "Please sign in with Steam first."}, status_code=401
        )

    if not _can_message(steam_id, other_steam_id):
        return JSONResponse(
            {"error": "You can only message people on your Steam friends list."},
            status_code=403,
        )

    form = await request.form()
    body = str(form.get("body", "")).strip()

    if not body:
        return JSONResponse({"error": "Message can't be empty."}, status_code=400)

    if len(body) > MAX_MESSAGE_LENGTH:
        return JSONResponse(
            {"error": f"Messages can be at most {MAX_MESSAGE_LENGTH} characters."},
            status_code=400,
        )

    message = save_message(steam_id, other_steam_id, body)

    # Email the recipient after the response is sent, so a slow or
    # failing mail server never delays or breaks the chat.
    background_tasks.add_task(
        notify_new_message,
        sender_steam_id=steam_id,
        sender_name=request.session.get("steam_name") or "A friend",
        recipient_steam_id=other_steam_id,
        body=body,
        base_url=app_base_url(str(request.base_url)),
    )

    return JSONResponse({"message": message})


@router.get("/api/messages/{other_steam_id}")
def poll_messages(request: Request, other_steam_id: str, after: int = 0):
    """New messages in a conversation since message ID `after`."""
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse(
            {"error": "Please sign in with Steam first."}, status_code=401
        )

    if not _can_message(steam_id, other_steam_id):
        return JSONResponse({"error": "Conversation not found."}, status_code=404)

    messages = get_conversation(steam_id, other_steam_id, after_id=after)
    mark_viewing(steam_id, other_steam_id)

    if any(m["sender_steam_id"] == other_steam_id for m in messages):
        mark_conversation_read(steam_id, other_steam_id)

    return JSONResponse({"messages": messages})


@router.get("/api/messages-unread-count")
def unread_count(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse({"count": 0})

    return JSONResponse({"count": get_unread_message_count(steam_id)})
