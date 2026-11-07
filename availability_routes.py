"""
availability_routes.py

- /availability: a page where users mark which weekend hours they're free.
- /api/availability: saves one weekend of availability.
- /api/events/recommend: runs recommender.recommend_event for the event
  creation form's "Suggest a time" button.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_availability, get_saved_friends, replace_availability, user_exists
from library_service import sync_friend_libraries
from permissions import admin_mode_active
from recommender import EARLIEST_HOUR, LATEST_END_HOUR, recommend_event
from steam_api import get_player_summaries

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

WEEKENDS_SHOWN = 6
ALLOWED_DURATIONS = {60, 90, 120, 180, 240}


def upcoming_weekends(today: date, count: int = WEEKENDS_SHOWN) -> list[tuple[date, date]]:
    """(Saturday, Sunday) pairs, starting with the current/next weekend."""
    if today.weekday() == 6:  # Sunday: this weekend is still going
        saturday = today - timedelta(days=1)
    else:
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)

    return [
        (saturday + timedelta(weeks=i), saturday + timedelta(weeks=i, days=1))
        for i in range(count)
    ]


def _target_user(request: Request, requested: str) -> str | None:
    """
    Whose availability to show/save: the signed-in user, or (for admins
    in admin mode) the user given in ?user= / the "user" field.
    Returns None if the request isn't allowed.
    """
    steam_id = request.session.get("steam_id")

    if not requested or requested == steam_id:
        return steam_id

    if admin_mode_active(request) and user_exists(requested):
        return requested

    return None


def _weekend_label(saturday: date, sunday: date) -> str:
    if saturday.month == sunday.month:
        return f"Sat {saturday.day} – Sun {sunday.day} {sunday:%b}"
    return f"Sat {saturday.day} {saturday:%b} – Sun {sunday.day} {sunday:%b}"


def _display_name(steam_id: str) -> str:
    try:
        return get_player_summaries([steam_id]).get(steam_id, {}).get("personaname", steam_id)
    except (requests.RequestException, RuntimeError):
        return steam_id


# ---------------------------------------------------------------------
# Availability page + save
# ---------------------------------------------------------------------

@router.get("/availability")
def availability_page(request: Request, user: str = ""):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    target = _target_user(request, user)

    if target is None:
        return RedirectResponse(url="/availability", status_code=303)

    weekends = upcoming_weekends(datetime.now().date())
    saved = get_availability(
        target, weekends[0][0].isoformat(), weekends[-1][1].isoformat()
    )

    weekend_data = []
    for saturday, sunday in weekends:
        days = {saturday.isoformat(), sunday.isoformat()}
        weekend_data.append({
            "id": saturday.isoformat(),
            "label": _weekend_label(saturday, sunday),
            "days": [
                {"date": saturday.isoformat(), "label": f"Saturday {saturday.day} {saturday:%b}"},
                {"date": sunday.isoformat(), "label": f"Sunday {sunday.day} {sunday:%b}"},
            ],
            "slots": [[d, h] for d, h in saved if d in days],
        })

    return templates.TemplateResponse(
        request=request,
        name="availability.html",
        context={
            "steam_id": steam_id,
            "weekends": weekend_data,
            "hours": list(range(EARLIEST_HOUR, LATEST_END_HOUR)),
            # Set when an admin is editing someone else's schedule.
            "editing_user": target if target != steam_id else None,
            "editing_name": _display_name(target) if target != steam_id else None,
        },
    )


@router.post("/api/availability")
async def save_availability(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse({"error": "Please sign in with Steam first."}, status_code=401)

    try:
        payload = await request.json()
        saturday = date.fromisoformat(payload["weekend"])
        raw_slots = payload.get("slots", [])
        target = _target_user(request, str(payload.get("user") or ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"error": "Invalid request."}, status_code=400)

    if target is None:
        return JSONResponse(
            {"error": "Only admins can change someone else's schedule."},
            status_code=403,
        )

    valid_saturdays = {sat for sat, _ in upcoming_weekends(datetime.now().date())}

    if saturday not in valid_saturdays:
        return JSONResponse({"error": "That weekend can't be edited."}, status_code=400)

    days = [saturday.isoformat(), (saturday + timedelta(days=1)).isoformat()]
    slots = []

    for item in raw_slots if isinstance(raw_slots, list) else []:
        try:
            slot_date, slot_hour = str(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            return JSONResponse({"error": "Invalid time slot."}, status_code=400)

        if slot_date not in days or not EARLIEST_HOUR <= slot_hour < LATEST_END_HOUR:
            return JSONResponse({"error": "Invalid time slot."}, status_code=400)

        slots.append((slot_date, slot_hour))

    replace_availability(target, days, slots)

    return JSONResponse({"saved": len(set(slots))})


# ---------------------------------------------------------------------
# Recommendation API for the event form
# ---------------------------------------------------------------------

def _format_date(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    return f"{day:%A} {day.day} {day:%B}"


def _format_hour_range(start: str, duration_minutes: int) -> str:
    begin = datetime.strptime(start, "%H:%M")
    end = begin + timedelta(minutes=duration_minutes)
    return f"{begin:%H:%M}–{end:%H:%M}"


@router.post("/api/events/recommend")
async def recommend_for_event(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse({"error": "Please sign in with Steam first."}, status_code=401)

    form = await request.form()

    try:
        duration = int(form.get("duration", 120))
    except (TypeError, ValueError):
        duration = 120

    if duration not in ALLOWED_DURATIONS:
        duration = 120

    friend_ids = set(form.getlist("friend_ids"))
    allowed = {friend["steamid"] for friend in get_saved_friends(steam_id)}

    if not friend_ids:
        return JSONResponse(
            {"error": "Select at least one friend first, so we know whose schedules to compare."},
            status_code=400,
        )

    if not friend_ids <= allowed:
        return JSONResponse({"error": "One or more selected friends are invalid."}, status_code=400)

    # Make sure friends' libraries are in the database so the game
    # recommendation can use them. Failures just mean older data is used.
    # Synced in parallel, in a worker thread so other requests aren't
    # blocked; this still waits for every sync before recommending.
    await run_in_threadpool(sync_friend_libraries, friend_ids)

    group = [steam_id, *sorted(friend_ids)]
    result = await run_in_threadpool(
        recommend_event, group, duration_minutes=duration, required_game_owner=steam_id
    )

    if not result["ok"]:
        messages = {
            "no_availability": (
                "Nobody in this group has added their weekend availability yet. "
                "Add yours on the Availability page and ask your friends to do the same."
            ),
            "no_slots": (
                "There are no upcoming weekend times where anyone in this group is free. "
                "Try adding more availability."
            ),
        }
        return JSONResponse(
            {"error": messages.get(result["reason"], "Couldn't make a recommendation.")},
            status_code=422,
        )

    try:
        profiles = await run_in_threadpool(get_player_summaries, group)
    except (requests.RequestException, RuntimeError):
        profiles = {}

    def name(person: str) -> str:
        if person == steam_id:
            return "You"
        return profiles.get(person, {}).get("personaname", person)

    def names(people: list[str]) -> list[str]:
        return [name(p) for p in people]

    attendees = result["attendees"]
    game = result["game"]

    # Plain-language reasons shown under the suggestion.
    reasons = [
        f"{len(attendees)} of {len(group)} people are free "
        f"{_format_date(result['date'])}, {_format_hour_range(result['time'], duration)}."
    ]

    if game:
        if game["owned_by"] == len(attendees):
            ownership = "Everyone who can make it owns it"
        else:
            ownership = f"{game['owned_by']} of the {len(attendees)} attendees own it"

        played = (
            f", and {game['played_by']} {'has' if game['played_by'] == 1 else 'have'} "
            f"played it ({game['hours']:g} hours combined)."
            if game["played_by"] else "."
        )
        reasons.append(f"{game['name']}: {ownership}{played}")

    if result["unavailable"]:
        reasons.append("Not free then: " + ", ".join(names(result["unavailable"])) + ".")

    if result["no_availability"]:
        reasons.append(
            "Haven't added availability yet: " + ", ".join(names(result["no_availability"])) + "."
        )

    return JSONResponse({
        "date": result["date"],
        "time": result["time"],
        "duration_minutes": duration,
        "date_label": _format_date(result["date"]),
        "time_label": _format_hour_range(result["time"], duration),
        "game": game,
        "attendees": names(attendees),
        "reasons": reasons,
        "alternative_games": result["alternative_games"],
        "alternative_times": [
            {
                **alt,
                "label": f"{_format_date(alt['date'])}, {_format_hour_range(alt['time'], duration)}",
                "attending": len(alt["attendees"]),
            }
            for alt in result["alternative_times"]
        ],
        "group_size": len(group),
    })
