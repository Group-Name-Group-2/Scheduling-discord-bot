import os
import requests
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from database import get_saved_games, get_saved_friends, save_game_night_event, get_calendar_events
from steam_auth import router as steam_auth_router
from steam_api import get_game_details, get_player_summaries


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

SESSION_SECRET = os.getenv("SESSION_SECRET")

if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is missing from .env")

app = FastAPI(title="Game Night")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=False,  # Local development over HTTP
)

app.include_router(steam_auth_router)

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

templates = Jinja2Templates(directory=BASE_DIR / "templates")


# Entry point
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
    )

@app.get("/api/games/{app_id}")
def game_details(app_id: int):
    try:
        details = get_game_details(app_id)

        if details is None:
            raise HTTPException(
                status_code=404,
                detail="Game details were not found."
            )

        return details

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Steam could not provide game details right now."
        )

@app.get("/events")
def events(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={}
    )

@app.get("/api/events")
def get_events(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse(
            {"error": "Please sign in with Steam first."},
            status_code=401,
        )

    saved_events = get_calendar_events(steam_id)

    calendar_events = []

    for event in saved_events:
        start_datetime = datetime.strptime(
            f"{event['event_date']} {event['event_time']}",
            "%Y-%m-%d %H:%M",
        )

        end_datetime = start_datetime + timedelta(
            minutes=event["duration_minutes"]
        )

        calendar_events.append({
            "id": event["event_id"],
            "title": event["game_name"],
            "start": start_datetime.isoformat(),
            "end": end_datetime.isoformat(),
            "extendedProps": {
                "appId": event["app_id"],
                "creatorSteamId": event["creator_steam_id"],
                "duration": event["duration_minutes"],
            },
        })

    return JSONResponse(calendar_events)

@app.get("/events/create")
def create_event_page(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return RedirectResponse(url="/", status_code=303)

    # Get the user's saved game library.
    games = get_saved_games(steam_id)

    # Get the user's saved friends.
    friends = get_saved_friends(steam_id)

    # Only preselect friends who actually belong to this user's
    # saved friends list.
    requested_friend_ids = request.query_params.getlist("friend_id")

    allowed_friend_ids = {
        friend["steamid"] for friend in friends
    }

    selected_friend_ids = [
        friend_id
        for friend_id in requested_friend_ids
        if friend_id in allowed_friend_ids
    ]

    # Retrieve Steam profile information for the friends.
    friend_ids = [friend["steamid"] for friend in friends]

    try:
        profiles = get_player_summaries(friend_ids)
    except requests.RequestException:
        profiles = {}

    for friend in friends:
        profile = profiles.get(friend["steamid"], {})

        friend["name"] = profile.get(
            "personaname",
            friend["steamid"]
        )
        friend["avatar"] = profile.get("avatarfull", "")

    return templates.TemplateResponse(
        request=request,
        name="create_event.html",
        context={
            "steam_id": steam_id,
            "games": games,
            "friends": friends,
            "game_id": request.query_params.get("game_id", ""),
            "game_name": request.query_params.get("game_name", ""),
            "date": request.query_params.get("date", ""),
            "friend_ids": selected_friend_ids,
        },
    )

@app.post("/events/create")
async def create_event(request: Request):
    steam_id = request.session.get("steam_id")

    if not steam_id:
        return JSONResponse(
            {"error": "Please sign in with Steam first."},
            status_code=401,
        )

    form = await request.form()

    # Read the submitted form fields.
    game_id = form.get("game_id", "")
    event_date = form.get("date", "")
    event_time = form.get("time", "")
    duration = form.get("duration", "")

    participant_ids = list(set(form.getlist("friend_ids")))

    # Validate the game ID.
    try:
        app_id = int(game_id)
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": "Please select a valid game."},
            status_code=400,
        )

    # Only allow games in the creator's saved library.
    saved_games = get_saved_games(steam_id)
    allowed_game_ids = {
        game["appid"] for game in saved_games
    }

    if app_id not in allowed_game_ids:
        return JSONResponse(
            {"error": "The selected game isn't in your saved library."},
            status_code=400,
        )

    # Validate the date and time formats.
    try:
        datetime.strptime(event_date, "%Y-%m-%d")
        datetime.strptime(event_time, "%H:%M")
    except ValueError:
        return JSONResponse(
            {"error": "Please enter a valid date and start time."},
            status_code=400,
        )

    # Only allow the duration options offered by the form.
    try:
        duration_minutes = int(duration)
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": "Please select a valid event duration."},
            status_code=400,
        )

    allowed_durations = {60, 90, 120, 180, 240}

    if duration_minutes not in allowed_durations:
        return JSONResponse(
            {"error": "Please select a valid event duration."},
            status_code=400,
        )

    # Only allow participants from the creator's saved friends list.
    saved_friends = get_saved_friends(steam_id)
    allowed_friend_ids = {
        friend["steamid"] for friend in saved_friends
    }

    if not set(participant_ids).issubset(allowed_friend_ids):
        return JSONResponse(
            {"error": "One or more selected participants are invalid."},
            status_code=400,
        )

    try:
        event_id = save_game_night_event(
            creator_steam_id=steam_id,
            app_id=app_id,
            event_date=event_date,
            event_time=event_time,
            duration_minutes=duration_minutes,
            participant_steam_ids=participant_ids,
        )
    except Exception:
        # Log the exception on the server for debugging.
        import logging
        logging.exception("Failed to save game night event")

        return JSONResponse(
            {"error": "Something went wrong while saving the event."},
            status_code=500,
        )

    return JSONResponse({
        "success": True,
        "event_id": event_id,
        "message": "Your game night was created successfully!",
    })