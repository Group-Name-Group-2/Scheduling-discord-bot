"""
notifications.py

Email notifications for new direct messages and group messages.

To avoid flooding inboxes, a recipient gets an email only when:
- they have a verified email address and notifications turned on,
- this is the first unread message from that sender, or in that group
  (later messages wait until they've read the conversation), and
- they aren't looking at that conversation right now.
"""

import time
from html import escape

from database import count_group_unread, count_unread_from, get_user_settings
from email_service import send_email

# (viewer_steam_id, other_steam_id) -> last time the viewer's chat page
# checked for messages. In memory, so it resets when the server restarts.
# For group chats, other_steam_id is group_key(group_id).
_active_viewers: dict[tuple[str, str], float] = {}

# The chat page polls every 4 seconds, so 15 seconds without a poll
# means the page was closed (or the tab is in the background).
ACTIVE_WINDOW_SECONDS = 15

PREVIEW_LENGTH = 200


def group_key(group_id: int) -> str:
    """The 'other' side of a group chat for mark_viewing / is_viewing."""
    return f"group:{group_id}"


def mark_viewing(viewer_steam_id: str, other_steam_id: str) -> None:
    _active_viewers[(viewer_steam_id, other_steam_id)] = time.monotonic()


def is_viewing(viewer_steam_id: str, other_steam_id: str) -> bool:
    last_seen = _active_viewers.get((viewer_steam_id, other_steam_id))
    return (
        last_seen is not None
        and time.monotonic() - last_seen < ACTIVE_WINDOW_SECONDS
    )


def notify_new_message(
    sender_steam_id: str,
    sender_name: str,
    recipient_steam_id: str,
    body: str,
    base_url: str,
) -> None:
    """Email the recipient about a new direct message, if appropriate."""
    settings = get_user_settings(recipient_steam_id)

    if not settings["email"] or not settings["notify_messages"]:
        return

    if count_unread_from(sender_steam_id, recipient_steam_id) != 1:
        return

    if is_viewing(recipient_steam_id, sender_steam_id):
        return

    _send_notification(
        to=settings["email"],
        sender_name=sender_name,
        body=body,
        conversation_url=f"{base_url}/messages/{sender_steam_id}",
        base_url=base_url,
    )


def notify_group_message(
    group_id: int,
    group_title: str,
    sender_steam_id: str,
    sender_name: str,
    member_ids: list[str],
    body: str,
    base_url: str,
) -> None:
    """Email each other member of a group about a new message, if appropriate."""
    for member in member_ids:
        if member == sender_steam_id:
            continue

        settings = get_user_settings(member)

        if not settings["email"] or not settings["notify_messages"]:
            continue

        if count_group_unread(group_id, member) != 1:
            continue

        if is_viewing(member, group_key(group_id)):
            continue

        _send_notification(
            to=settings["email"],
            sender_name=sender_name,
            body=body,
            conversation_url=f"{base_url}/messages/groups/{group_id}",
            base_url=base_url,
            group_title=group_title,
        )


def _one_line(text: str, fallback: str) -> str:
    # Steam names and group names are user-controlled; keep them to one
    # line so they're safe to put in the email subject.
    return " ".join(text.split())[:64] or fallback


def _send_notification(
    to: str,
    sender_name: str,
    body: str,
    conversation_url: str,
    base_url: str,
    group_title: str | None = None,
) -> None:
    sender_name = _one_line(sender_name, "A friend")
    preview = body if len(body) <= PREVIEW_LENGTH else body[:PREVIEW_LENGTH] + "…"
    settings_url = f"{base_url}/settings"

    if group_title is None:
        subject = f"{sender_name} sent you a message on Game Night"
        intro_text = f"{sender_name} sent you a message on Game Night:"
        intro_html = f"<strong>{escape(sender_name)}</strong> sent you a message on Game Night:"
    else:
        group_title = _one_line(group_title, "a group chat")
        subject = f"{sender_name} sent a message in {group_title} on Game Night"
        intro_text = f"{sender_name} sent a message in {group_title}:"
        intro_html = (
            f"<strong>{escape(sender_name)}</strong> sent a message in "
            f"<strong>{escape(group_title)}</strong>:"
        )

    text = (
        f"{intro_text}\n\n"
        f"    {preview}\n\n"
        f"Reply here: {conversation_url}\n\n"
        "You won't get another email for this conversation until you've "
        "read it.\n"
        f"Turn off these emails: {settings_url}\n"
    )

    html = f"""\
<div style="font-family: Arial, sans-serif; max-width: 520px; color: #1b1f24;">
  <p>{intro_html}</p>
  <blockquote style="margin: 16px 0; padding: 12px 16px; background: #f1f4f8;
                     border-left: 4px solid #1a9fff; white-space: pre-wrap;">{escape(preview)}</blockquote>
  <p>
    <a href="{escape(conversation_url)}"
       style="display: inline-block; padding: 10px 18px; background: #1a9fff;
              color: #ffffff; text-decoration: none; border-radius: 6px;">
      Reply
    </a>
  </p>
  <p style="color: #6b7785; font-size: 13px;">
    You won't get another email for this conversation until you've read it.
    <a href="{escape(settings_url)}" style="color: #6b7785;">Turn off these emails</a>.
  </p>
</div>
"""

    send_email(to, subject, text, html)
