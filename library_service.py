from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from content_filter import start_check as start_content_check
from steam_api import get_owned_games
from database import get_library_synced_at, get_saved_games, get_setting, save_owned_games

# How long a downloaded library counts as recent, so syncs reuse the saved
# copy instead of calling Steam. Admins change it on the dashboard (stored
# in app_settings); this is the value until they do. 0 = always fetch.
LIBRARY_MAX_AGE_SETTING = "library_max_age_minutes"
DEFAULT_LIBRARY_MAX_AGE_MINUTES = 15
MAX_LIBRARY_MAX_AGE_MINUTES = 7 * 24 * 60  # one week


def library_max_age_minutes() -> int:
    value = get_setting(LIBRARY_MAX_AGE_SETTING)

    try:
        minutes = int(value) if value is not None else DEFAULT_LIBRARY_MAX_AGE_MINUTES
    except ValueError:
        minutes = DEFAULT_LIBRARY_MAX_AGE_MINUTES

    return max(0, min(minutes, MAX_LIBRARY_MAX_AGE_MINUTES))


def library_is_recent(steam_id: str) -> bool:
    """True if steam_id's library was downloaded within the max age."""
    minutes = library_max_age_minutes()
    synced_at = get_library_synced_at(steam_id)

    if minutes == 0 or synced_at is None:
        return False

    return datetime.now(timezone.utc) - synced_at < timedelta(minutes=minutes)


def sync_friend_library(friend_steam_id: str) -> dict:
    """
    Retrieve a friend's Steam library and save it when available.
    Reuses the saved library if it was downloaded recently.
    """

    if library_is_recent(friend_steam_id):
        return {
            "steam_id": friend_steam_id,
            "games": get_saved_games(friend_steam_id),
            "available": True,
            "message": None,
        }

    games = get_owned_games(friend_steam_id)

    # An empty result may mean the library is private
    # Preserve any previously saved library
    if not games:
        saved_games = get_saved_games(friend_steam_id)

        return {
            "steam_id": friend_steam_id,
            "games": saved_games,
            "available": False,
            "message": (
                "Steam did not return any games. "
                "The library may be private or unavailable."
            ),
        }

    save_owned_games(friend_steam_id, games)
    start_content_check()  # rate any new games in the background

    return {
        "steam_id": friend_steam_id,
        "games": get_saved_games(friend_steam_id),
        "available": True,
        "message": None,
    }


# How many friends' libraries to download from Steam at the same time.
MAX_PARALLEL_SYNCS = 8


def sync_friend_libraries(friend_steam_ids) -> dict[str, dict | None]:
    """
    sync_friend_library for several friends at once, in parallel, so N
    friends take about as long as one Steam call instead of N in a row.
    Waits for every sync to finish before returning, so callers always
    see the fresh data.

    Returns friend_steam_id -> sync result, or None if Steam couldn't be
    reached for that friend (the saved library, if any, is untouched).
    """
    ids = list(dict.fromkeys(friend_steam_ids))

    if not ids:
        return {}

    def safe_sync(friend_id: str) -> dict | None:
        try:
            return sync_friend_library(friend_id)
        except (requests.RequestException, RuntimeError):
            return None

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_SYNCS, len(ids))) as pool:
        return dict(zip(ids, pool.map(safe_sync, ids)))
