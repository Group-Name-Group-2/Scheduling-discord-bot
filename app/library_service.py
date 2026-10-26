from steam_api import get_owned_games
from database import save_owned_games, get_saved_games


def sync_friend_library(friend_steam_id: str) -> dict:
    """
    Retrieve a friend's Steam library and save it when available.
    """

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

    return {
        "steam_id": friend_steam_id,
        "games": get_saved_games(friend_steam_id),
        "available": True,
        "message": None,
    }