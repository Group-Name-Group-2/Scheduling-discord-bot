import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("STEAM_API_KEY")

API_URL = (
    "https://api.steampowered.com/"
    "IPlayerService/GetOwnedGames/v0001/"
)


def fetch_api_data(url, params):
    try:
        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()
        return response.json()

    except requests.exceptions.HTTPError as error:
        print(f"HTTP error: {error}")

    except requests.exceptions.ConnectionError as error:
        print(f"Connection error: {error}")

    except requests.exceptions.Timeout as error:
        print(f"Timeout error: {error}")

    except requests.exceptions.RequestException as error:
        print(f"Request error: {error}")

    return None


def get_owned_games(steam_id):
    if not API_KEY:
        print("Missing STEAM_API_KEY")
        return None

    params = {
        "key": API_KEY,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json"
    }

    data = fetch_api_data(API_URL, params)

    if data is None:
        return None

    response = data.get("response", {})

    # no visible games in response but was success
    if "games" not in response:
        return []

    return response["games"]