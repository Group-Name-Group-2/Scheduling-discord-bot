import os

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("STEAM_API_KEY")

API_URL = (
    "https://api.steampowered.com/"
    "IPlayerService/GetOwnedGames/v1/"
)


def get_owned_games(steam_id: str):
    if not API_KEY:
        raise RuntimeError("STEAM_API_KEY is missing from .env")

    params = {
        "key": API_KEY,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }

    response = requests.get(API_URL, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()
    result = data.get("response", {})

    # Steam may return an empty response if game details
    # are unavailable or no games are returned.
    if "games" not in result:
        return []

    return result["games"]

def get_friends_list(steam_id: str):
    if not API_KEY:
        raise RuntimeError("STEAM_API_KEY is missing from .env")

    friends_url = (
        "https://api.steampowered.com/"
        "ISteamUser/GetFriendList/v1/"
    )

    params = {
        "key": API_KEY,
        "steamid": steam_id,
        "relationship": "friend",
    }

    response = requests.get(
        friends_url,
        params=params,
        timeout=15,
    )
    response.raise_for_status()

    data = response.json()
    friends_list = data.get("friendslist")

    if friends_list is None:
        return None

    return friends_list.get("friends", [])


def get_player_summaries(steam_ids: list[str]) -> dict:
    """Retrieve Steam profile details for a list of Steam IDs."""
    if not steam_ids:
        return {}

    profiles = {}
    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

    # Steam accepts up to 100 Steam IDs per request
    for start in range(0, len(steam_ids), 100):
        batch = steam_ids[start:start + 100]

        params = {
            "key": API_KEY,
            "steamids": ",".join(batch)
        }

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()

        data = response.json()
        players = data.get("response", {}).get("players", [])

        # Index profiles by Steam ID
        for player in players:
            profiles[player["steamid"]] = {
                "steamid": player["steamid"],
                "personaname": player.get("personaname", "Unknown Player"),
                "avatarfull": player.get("avatarfull", ""),
                "avatarmedium": player.get("avatarmedium", ""),
                "profileurl": player.get("profileurl", "")
            }

    return apply_profile_overrides(profiles, steam_ids)


def apply_profile_overrides(profiles: dict, steam_ids: list[str]) -> dict:
    """
    Swap in any display name / avatar an admin has set (see the admin
    dashboard). Every page gets names and pictures through
    get_player_summaries, so doing it here covers the whole site.
    """
    # Imported here so steam_api still works without the database.
    from database import get_profile_overrides

    try:
        overrides = get_profile_overrides(list(steam_ids))
    except Exception:
        return profiles

    for steam_id, override in overrides.items():
        profile = profiles.setdefault(steam_id, {
            "steamid": steam_id,
            "personaname": steam_id,
            "avatarfull": "",
            "avatarmedium": "",
            "profileurl": "",
        })

        if override["display_name"]:
            profile["personaname"] = override["display_name"]
        if override["avatar_path"]:
            profile["avatarfull"] = override["avatar_path"]
            profile["avatarmedium"] = override["avatar_path"]

    return profiles

def get_game_details(app_id: int) -> dict | None:
    """Retrieve detailed information about a game from the Steam Store API."""

    url = "https://store.steampowered.com/api/appdetails"

    params = {
        "appids": app_id,
        "l": "en",
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    result = response.json().get(str(app_id), {})

    # Steam can return success=False for unavailable or invalid games.
    if not result.get("success"):
        return None

    game = result.get("data", {})

    return {
        "appid": app_id,
        "name": game.get("name", "Unknown Game"),
        "description": game.get("short_description", ""),
        "release_date": game.get("release_date", {}).get(
            "date", "Unknown"
        ),
        "developers": game.get("developers", []),
        "publishers": game.get("publishers", []),
        "genres": [
            genre.get("description", "")
            for genre in game.get("genres", [])
        ],
        "header_image": game.get("header_image", ""),
        "website": game.get("website", ""),
    }

class SteamRateLimited(Exception):
    """The Steam Store API answered 429 Too Many Requests."""


def get_content_rating(app_id: int) -> dict | None:
    """
    A game's mature-content info from the Steam Store:
    {"descriptor_ids": [int, ...], "required_age": int}.
    None if Steam has no store data for it (removed, region-locked or
    some adult-only titles). Raises SteamRateLimited on 429.
    """
    response = requests.get(
        "https://store.steampowered.com/api/appdetails",
        params={"appids": app_id, "l": "en"},
        timeout=15,
    )

    if response.status_code == 429:
        raise SteamRateLimited()

    response.raise_for_status()
    result = (response.json() or {}).get(str(app_id)) or {}

    if not result.get("success"):
        return None

    data = result.get("data") or {}

    try:
        required_age = int(data.get("required_age") or 0)
    except (TypeError, ValueError):
        required_age = 0

    return {
        "descriptor_ids": [
            int(i) for i in (data.get("content_descriptors") or {}).get("ids") or []
        ],
        "required_age": required_age,
    }
