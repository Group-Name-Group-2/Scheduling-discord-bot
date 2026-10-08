import os
import requests
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

templates = Jinja2Templates(directory="Web/templates")
app.mount("/static", StaticFiles(directory="Web/static"), name="static")

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )

@app.get("/auth/discord")
async def discord_login():
    client_id = os.getenv("DISCORD_CLIENT_ID")
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI")

    discord_url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri}"
        "&scope=identify"
    )

    return RedirectResponse(discord_url)

@app.get("/auth/discord/callback")
async def discord_callback(code: str):

    client_id = os.getenv("DISCORD_CLIENT_ID")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET")
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI")

    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri
    }

    response = requests.post(
        "https://discord.com/api/oauth2/token",
        data=token_data
    )

    response.raise_for_status()

    token = response.json()

    access_token = token["access_token"]

    user_response = requests.get(
        "https://discord.com/api/users/@me",
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    )

    user_response.raise_for_status()

    discord_user = user_response.json()

    print(discord_user)

    return discord_user