"""
content_filter.py

Automatically hides games that aren't safe for work.

Each owned game is looked up once on the Steam Store, which reports the
developer-declared content descriptors. Games with a descriptor in
NSFW_DESCRIPTOR_IDS are added to ignored_games (reason "Auto: ..."), which
removes them from every library like a manual hide. An admin can un-hide
one from the dashboard; it has a rating already, so it won't be re-hidden.

Steam's content descriptor IDs:
    1  Some nudity or sexual content
    2  Frequent violence or gore
    3  Adult-only sexual content
    4  Frequent nudity or sexual content
    5  General mature content

The check runs in a background thread, started after library syncs and
from the admin dashboard. The Store API allows roughly 200 requests per
5 minutes, so lookups are spaced out.
"""

import threading
import time
from datetime import datetime

import requests

from database import ignore_game, list_unrated_games, save_content_rating
from steam_api import SteamRateLimited, get_content_rating

# Which descriptors make a game "not safe for work". Add 1 (some nudity)
# or 2/5 (violence, general mature) here to be stricter.
NSFW_DESCRIPTOR_IDS = {3, 4}

# Games Steam returns no store data for: False = show them (they're listed
# on the admin dashboard for review), True = hide them too.
HIDE_UNKNOWN = False

DESCRIPTOR_NAMES = {
    1: "some nudity or sexual content",
    2: "frequent violence or gore",
    3: "adult-only sexual content",
    4: "frequent nudity or sexual content",
    5: "general mature content",
}

SECONDS_BETWEEN_REQUESTS = 1.6   # ~187 per 5 minutes
RATE_LIMIT_PAUSE_SECONDS = 60
MAX_RATE_LIMIT_PAUSES = 5

_lock = threading.Lock()
state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "checked": 0,
    "hidden": 0,
    "total": 0,
    "error": None,
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def classify(rating: dict | None) -> tuple[str, str | None]:
    """(status, reason to hide or None)."""
    if rating is None:
        return "unknown", ("Auto: no Steam store data" if HIDE_UNKNOWN else None)

    flagged = sorted(set(rating["descriptor_ids"]) & NSFW_DESCRIPTOR_IDS)

    if flagged:
        return "nsfw", "Auto: " + ", ".join(DESCRIPTOR_NAMES.get(i, f"descriptor {i}") for i in flagged)

    return "sfw", None


def _check_game(game: dict) -> bool:
    """Rate one game and hide it if needed. Returns True if it was hidden."""
    rating = get_content_rating(game["appid"])
    status, reason = classify(rating)

    save_content_rating(
        game["appid"],
        status,
        rating["descriptor_ids"] if rating else [],
        rating["required_age"] if rating else None,
    )

    if reason:
        ignore_game(game["appid"], game["name"], reason=reason)
        return True

    return False


def _run() -> None:
    try:
        # Keep going until nothing is left, so games added by syncs that
        # happen while this runs are picked up too.
        while True:
            games = list_unrated_games()
            if not games:
                break

            state["total"] = state["checked"] + len(games)

            for game in games:
                pauses = 0

                while True:
                    try:
                        if _check_game(game):
                            state["hidden"] += 1
                        break
                    except SteamRateLimited:
                        pauses += 1
                        if pauses > MAX_RATE_LIMIT_PAUSES:
                            state["error"] = "Steam kept rate-limiting; stopped early. Try again later."
                            return
                        time.sleep(RATE_LIMIT_PAUSE_SECONDS)
                    except (requests.RequestException, ValueError):
                        # Network trouble: leave it unrated for the next run.
                        state["error"] = "Some games couldn't be reached and will be retried next time."
                        break

                state["checked"] += 1
                time.sleep(SECONDS_BETWEEN_REQUESTS)

            if state["error"]:
                break  # don't loop forever on games that keep failing
    finally:
        state["running"] = False
        state["finished_at"] = _now_text()


def start_check() -> bool:
    """
    Start checking unrated games in the background. Safe to call often:
    does nothing if a check is already running or nothing needs checking.
    Returns True if a new check was started.
    """
    with _lock:
        if state["running"] or not list_unrated_games():
            return False

        state.update(
            running=True, started_at=_now_text(), finished_at=None,
            checked=0, hidden=0, total=0, error=None,
        )

    threading.Thread(target=_run, name="content-filter", daemon=True).start()
    return True
