"""
recommender.py

Recommends a date, start time and game for a game night.

Main entry point:

    recommend_event(steam_ids, duration_minutes=120, required_game_owner=None)

It takes the Steam IDs of everyone invited and returns the best-fitting
date + start time (from the weekend availability people have entered)
and the best game for the people who can make it.

How the time is picked
----------------------
Availability is stored as one-hour slots ("free 19:00-20:00 on Sat").
For every possible start hour on every date anyone is free:

1. A person can attend if they're free for every hour the event lasts
   and aren't already in another game night at that time.
2. Slots are ranked by:
   a. most people who can attend      (the main goal)
   b. soonest date                    (don't push the night weeks out)
   c. most "buffer": attendees also free the hour before/after, so the
      session can start late or run long
   d. closest to 19:00                (a typical game night start)

People who haven't entered any availability don't count for or against
a slot; they're reported separately so the organiser can nudge them.

How the game is picked
----------------------
Only games the organiser owns are considered (so it can be selected in
the event form). Games are ranked by:

1. how many of the attendees own it    (everyone, ideally)
2. how many of all invitees own it     (so latecomers can join too)
3. how many attendees have played it   (familiar beats untouched)
4. combined playtime, on a log scale   (one person's 2,000 hours
                                        shouldn't drown out the group)
"""

import math
from datetime import date, datetime, time, timedelta

from database import (
    get_availability_for_users,
    get_events_for_users,
    get_game_ownership,
)

# Hours shown on the availability grid: 08:00 until midnight.
EARLIEST_HOUR = 8
LATEST_END_HOUR = 24

PREFERRED_START_HOUR = 19
DEFAULT_WEEKS_AHEAD = 6
MAX_ALTERNATIVE_TIMES = 3
MAX_ALTERNATIVE_GAMES = 4


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------

def recommend_event(
    steam_ids: list[str],
    duration_minutes: int = 120,
    required_game_owner: str | None = None,
    now: datetime | None = None,
    weeks_ahead: int = DEFAULT_WEEKS_AHEAD,
) -> dict:
    """
    Recommend a date, time and game for the given group.

    Returns on success:
        {
            "ok": True,
            "date": "2026-11-07",
            "time": "19:00",
            "duration_minutes": 120,
            "attendees": [steam_id, ...],        # free at that time
            "unavailable": [steam_id, ...],      # entered availability, but busy
            "no_availability": [steam_id, ...],  # haven't entered any
            "game": {"appid", "name", "owned_by", "played_by", "hours"} | None,
            "alternative_games": [game, ...],
            "alternative_times": [{"date", "time", "attendees"}, ...],
        }

    Returns on failure:
        {"ok": False, "reason": "no_people" | "no_availability" | "no_slots"}
    """
    steam_ids = list(dict.fromkeys(steam_ids))  # dedupe, keep order

    if not steam_ids:
        return {"ok": False, "reason": "no_people"}

    now = now or datetime.now()
    start_date = now.date().isoformat()
    end_date = (now.date() + timedelta(weeks=weeks_ahead)).isoformat()

    availability = get_availability_for_users(steam_ids, start_date, end_date)

    if not availability:
        return {"ok": False, "reason": "no_availability"}

    events = get_events_for_users(steam_ids, start_date, end_date)
    busy = busy_slots_from_events(events, steam_ids)

    slots = rank_time_slots(steam_ids, availability, busy, duration_minutes, now)

    if not slots:
        return {"ok": False, "reason": "no_slots"}

    best = slots[0]
    attendees = best["attendees"]

    games = rank_games(
        get_game_ownership(steam_ids),
        attendees=attendees,
        invited=steam_ids,
        required_owner=required_game_owner,
    )

    return {
        "ok": True,
        "date": best["date"],
        "time": f"{best['hour']:02d}:00",
        "duration_minutes": duration_minutes,
        "attendees": attendees,
        "unavailable": [
            s for s in steam_ids if s in availability and s not in attendees
        ],
        "no_availability": [s for s in steam_ids if s not in availability],
        "game": games[0] if games else None,
        "alternative_games": games[1:1 + MAX_ALTERNATIVE_GAMES],
        "alternative_times": [
            {
                "date": slot["date"],
                "time": f"{slot['hour']:02d}:00",
                "attendees": slot["attendees"],
            }
            for slot in pick_alternatives(slots, hours_needed(duration_minutes))
        ],
    }


# ---------------------------------------------------------------------
# Time slots
# ---------------------------------------------------------------------

def hours_needed(duration_minutes: int) -> int:
    """Whole one-hour slots an event covers (90 minutes -> 2)."""
    return max(1, math.ceil(duration_minutes / 60))


def busy_slots_from_events(
    events: list[dict],
    steam_ids: list[str],
) -> dict[str, set[tuple[str, int]]]:
    """steam_id -> (date, hour) slots already taken by other game nights."""
    wanted = set(steam_ids)
    busy: dict[str, set[tuple[str, int]]] = {}

    for event in events:
        start = datetime.strptime(
            f"{event['event_date']} {event['event_time']}", "%Y-%m-%d %H:%M"
        )
        end = start + timedelta(minutes=event["duration_minutes"])

        # Every hour block the event touches, even partly.
        block = start.replace(minute=0)
        while block < end:
            slot = (block.date().isoformat(), block.hour)
            for member in event["members"] & wanted:
                busy.setdefault(member, set()).add(slot)
            block += timedelta(hours=1)

    return busy


def rank_time_slots(
    steam_ids: list[str],
    availability: dict[str, set[tuple[str, int]]],
    busy: dict[str, set[tuple[str, int]]],
    duration_minutes: int,
    now: datetime,
) -> list[dict]:
    """Every future start time at least one person can make, best first."""
    needed = hours_needed(duration_minutes)
    dates = sorted({slot_date for slots in availability.values() for slot_date, _ in slots})
    candidates = []

    for slot_date in dates:
        day = date.fromisoformat(slot_date)

        for hour in range(EARLIEST_HOUR, LATEST_END_HOUR - needed + 1):
            if datetime.combine(day, time(hour)) <= now:
                continue

            window = [(slot_date, hour + i) for i in range(needed)]

            attendees = [
                person
                for person in steam_ids
                if person in availability
                and all(
                    slot in availability[person]
                    and slot not in busy.get(person, ())
                    for slot in window
                )
            ]

            if not attendees:
                continue

            buffer = sum(
                ((slot_date, hour - 1) in availability[person])
                + ((slot_date, hour + needed) in availability[person])
                for person in attendees
            )

            candidates.append({
                "date": slot_date,
                "hour": hour,
                "attendees": attendees,
                "buffer": buffer,
            })

    candidates.sort(key=lambda c: (
        -len(c["attendees"]),
        c["date"],
        -c["buffer"],
        abs(c["hour"] - PREFERRED_START_HOUR),
    ))

    return candidates


def pick_alternatives(slots: list[dict], needed: int) -> list[dict]:
    """
    Runner-up times that don't overlap the best slot or each other,
    so the alternatives are genuinely different options.
    """
    chosen = [slots[0]]

    for slot in slots[1:]:
        if len(chosen) > MAX_ALTERNATIVE_TIMES:
            break

        overlaps = any(
            slot["date"] == other["date"]
            and abs(slot["hour"] - other["hour"]) < needed
            for other in chosen
        )

        if not overlaps:
            chosen.append(slot)

    return chosen[1:]


# ---------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------

def rank_games(
    ownership: dict[int, dict],
    attendees: list[str],
    invited: list[str],
    required_owner: str | None = None,
) -> list[dict]:
    """Games for the group, best first (see module docstring)."""
    attending = set(attendees)
    invited_set = set(invited)
    ranked = []

    for app_id, game in ownership.items():
        playtime = game["playtime"]

        if required_owner and required_owner not in playtime:
            continue

        owners_attending = [p for p in playtime if p in attending]

        if not owners_attending:
            continue

        played_by = [p for p in owners_attending if playtime[p] > 0]
        score = sum(math.log1p(playtime[p] / 60) for p in owners_attending)

        ranked.append({
            "appid": app_id,
            "name": game["name"],
            "owned_by": len(owners_attending),
            "owned_by_invited": len([p for p in playtime if p in invited_set]),
            "played_by": len(played_by),
            "hours": round(sum(playtime[p] for p in owners_attending) / 60, 1),
            "_score": score,
        })

    ranked.sort(key=lambda g: (
        -g["owned_by"],
        -g["owned_by_invited"],
        -g["played_by"],
        -g["_score"],
        (g["name"] or "").lower(),
    ))

    for game in ranked:
        del game["_score"]

    return ranked
