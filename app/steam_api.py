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
                "profileurl": player.get("profileurl", "")
            }

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