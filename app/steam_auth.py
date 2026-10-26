from pathlib import Path

import requests
from fastapi import APIRouter
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from database import save_owned_games, get_saved_games, save_friends, get_saved_friends, get_shared_games
from steam_api import get_owned_games, get_friends_list, get_player_summaries
from library_service import sync_friend_library

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

router = APIRouter()

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"


@router.get("/auth/steam")
async def login_with_steam():
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": "http://127.0.0.1:8000/auth/steam/callback",
        "openid.realm": "http://127.0.0.1:8000/",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }

    from urllib.parse import urlencode

    login_url = f"{STEAM_OPENID_URL}?{urlencode(params)}"

    return RedirectResponse(url=login_url)

@router.get("/auth/steam/callback")
async def steam_callback(request: Request):
    params = dict(request.query_params)

    # Make sure Steam returned a successful OpenID response.
    if params.get("openid.mode") != "id_res":
        raise HTTPException(
            status_code=400,
            detail="Steam authentication was not successful."
        )

    claimed_id = params.get("openid.claimed_id", "")
    identity = params.get("openid.identity", "")

    # Steam's claimed ID should identify a Steam user.
    prefix = "https://steamcommunity.com/openid/id/"
    if not claimed_id.startswith(prefix) or identity != claimed_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid Steam identity."
        )

    # Ask Steam to verify the complete OpenID response.
    verification_params = params.copy()
    verification_params["openid.mode"] = "check_authentication"

    try:
        response = requests.post(
            STEAM_OPENID_URL,
            data=verification_params,
            timeout=10
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="Could not verify the Steam login."
        ) from error

    if "is_valid:true" not in response.text:
        raise HTTPException(
            status_code=401,
            detail="Steam could not verify the login."
        )

    # Extract the Steam ID from the verified identity URL.
    steam_id = claimed_id.removeprefix(prefix)

    if not steam_id.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Invalid Steam ID."
        )

    # Retrieve the user's Steam library.
    try:
        steam_games = get_owned_games(steam_id)
        save_owned_games(steam_id, steam_games)
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve the Steam game library.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

        # Remember the verified Steam account in the session.
    request.session["steam_id"] = steam_id

    # Redirect to the separate library page.
    return RedirectResponse(url="/library", status_code=303)

@router.get("/library")
async def library(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    games = get_saved_games(steam_id)

    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "steam_id": steam_id,
            "games": games,
        },
    )


@router.get("/friends")
async def friends_page(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    error_message = None

    try:
        friends = get_friends_list(steam_id)

        if friends is None:
            error_message = (
                "Steam did not return a friends list. "
                "Check whether your friends list is public."
            )
            friends = get_saved_friends(steam_id)

        else:
            save_friends(steam_id, friends)

    except requests.RequestException:
        error_message = (
            "Could not retrieve your Steam friends. "
            "Showing previously saved friends, if available."
        )
        friends = get_saved_friends(steam_id)

    # Retrieve profile summaries for the friend IDs.
    friend_ids = [friend["steamid"] for friend in friends]

    try:
        profiles = get_player_summaries(friend_ids)
    except requests.RequestException:
        profiles = {}

    # Add profile information to each friend.
    for friend in friends:
        profile = profiles.get(friend["steamid"], {})

        friend["name"] = profile.get(
            "personaname", friend["steamid"]
        )
        friend["avatar"] = profile.get("avatarfull")
        friend["profile_url"] = profile.get("profileurl")

    return templates.TemplateResponse(
        request=request,
        name="friends.html",
        context={
            "steam_id": steam_id,
            "friends": friends,
            "error_message": error_message,
        },
    )

@router.get("/friends/{friend_steam_id}/library")
async def friend_library_page(
    request: Request,
    friend_steam_id: str
):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    # Only allow access to libraries belonging to people
    # in the current user's saved friends list.
    saved_friends = get_saved_friends(steam_id)
    friend_ids = {
        friend["steamid"] for friend in saved_friends
    }

    if friend_steam_id not in friend_ids:
        return RedirectResponse(url="/friends", status_code=303)

    try:
        result = sync_friend_library(friend_steam_id)
    except requests.RequestException:
        result = {
            "steam_id": friend_steam_id,
            "games": get_saved_games(friend_steam_id),
            "available": False,
            "message": (
                "Could not contact Steam. "
                "Showing previously saved games, if available."
            ),
        }

    return templates.TemplateResponse(
        request=request,
        name="friend_library.html",
        context={
            "steam_id": steam_id,
            "friend_steam_id": friend_steam_id,
            "games": result["games"],
            "available": result["available"],
            "message": result["message"],
        },
    )


@router.get("/shared-games")
async def shared_games_page(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    # Get the user's saved friends.
    friends = get_saved_friends(steam_id)

    # Read the friend IDs selected in the form.
    requested_friend_ids = request.query_params.getlist("friend_ids")

    # Only allow IDs belonging to the user's saved friends.
    allowed_friend_ids = {
        friend["steamid"] for friend in friends
    }

    selected_friend_ids = [
        friend_id
        for friend_id in requested_friend_ids
        if friend_id in allowed_friend_ids
    ]

    # Retrieve profile information for displaying friend names.
    friend_ids = [friend["steamid"] for friend in friends]

    try:
        profiles = get_player_summaries(friend_ids)
    except requests.RequestException:
        profiles = {}

    for friend in friends:
        profile = profiles.get(friend["steamid"], {})

        friend["name"] = profile.get(
            "personaname", friend["steamid"]
        )
        friend["avatar"] = profile.get("avatarfull")

    # Only calculate shared games after friends have been selected.
    games = []
    has_compared = bool(selected_friend_ids)

    if has_compared:
        steam_ids = [steam_id] + selected_friend_ids
        games = get_shared_games(steam_ids)

    return templates.TemplateResponse(
        request=request,
        name="shared_games.html",
        context={
            "steam_id": steam_id,
            "friends": friends,
            "selected_friend_ids": selected_friend_ids,
            "games": games,
            "friend_count": len(selected_friend_ids),
            "has_compared": has_compared,
        },
    )

@router.get("/friends/{friend_steam_id}/shared-games")
async def friend_shared_games_page(request: Request, friend_steam_id: str):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    # Verify that this person is one of the user's saved friends.
    saved_friends = get_saved_friends(steam_id)

    friend_ids = {
        friend["steamid"] for friend in saved_friends
    }

    if friend_steam_id not in friend_ids:
        return RedirectResponse(url="/friends", status_code=303)

    # Retrieve the friend's profile information.
    try:
        profiles = get_player_summaries([friend_steam_id])
    except requests.RequestException:
        profiles = {}

    profile = profiles.get(friend_steam_id, {})
    friend_name = profile.get("personaname", friend_steam_id)
    friend_avatar = profile.get("avatarfull")

    # Retrieve the friend's library.
    try:
        result = sync_friend_library(friend_steam_id)
    except requests.RequestException:
        result = {
            "games": get_saved_games(friend_steam_id),
            "available": False,
            "message": (
                "Could not contact Steam. "
                "Showing previously saved games, if available."
            ),
        }

    # Only compare libraries when the friend's library is available.
    games = []

    if result["available"]:
        games = get_shared_games([steam_id, friend_steam_id])

    return templates.TemplateResponse(request=request, name="friend_shared_games.html", context=
        {
            "steam_id": steam_id,
            "friend_steam_id": friend_steam_id,
            "friend_name": friend_name,
            "friend_avatar": friend_avatar,
            "games": games,
            "available": result["available"],
            "message": result["message"],
        },
    )


