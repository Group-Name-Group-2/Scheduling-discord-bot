import os
import requests
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from steam_auth import router as steam_auth_router
from steam_api import get_game_details


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