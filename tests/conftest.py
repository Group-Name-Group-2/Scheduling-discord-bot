"""
Shared test setup. pytest loads this before any test file.

Every test gets:
- its own empty SQLite database (your real gamenight.db is never touched),
- a fake Steam (`steam` fixture) instead of the real Steam API,
- a fake mail server (`outbox` fixture) instead of real SMTP,
- real network calls blocked, so a missed fake fails loudly instead of
  quietly calling Steam or sending email.

Handy fixtures:
    client        a TestClient for the app (doesn't follow redirects)
    login         login(client, steam_id, admin=False) signs a user in
    steam         the fake Steam; fill steam.profiles / owned_games / ...
    outbox        emails "sent" during the test; outbox.fail = True makes sending fail
    make_user     make_user(steam_id, games=[...], friends=[...]) in the DB
"""

import os
import smtplib
import sys
import tempfile
from pathlib import Path

import pytest
import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# Set before the app is imported. load_dotenv() never overrides variables
# that already exist, so these win over anything in your real .env.
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["STEAM_API_KEY"] = "test-steam-key"
os.environ["APP_BASE_URL"] = "http://testserver"
os.environ["SMTP_HOST"] = "smtp.invalid"   # never a real mail server
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USERNAME"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["EMAIL_FROM"] = "Game Night <test@example.com>"
os.environ["DEV_LOGIN"] = "0"

import database  # noqa: E402

# main.py creates the database as soon as it's imported, so point it at a
# throwaway file first. Each test then gets its own (see fresh_database).
_import_db_dir = tempfile.mkdtemp(prefix="gamenight-tests-")
database.DATABASE_PATH = Path(_import_db_dir) / "import.db"

import admin_routes  # noqa: E402
import content_filter  # noqa: E402
import library_service  # noqa: E402
import notifications  # noqa: E402
import steam_api  # noqa: E402
import steam_auth  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402


# ---------------------------------------------------------------------
# Test-only sign-in route
# ---------------------------------------------------------------------

def _test_login(request: Request, steam_id: str, admin: int = 0, name: str = ""):
    request.session["steam_id"] = steam_id
    request.session["steam_name"] = name or None
    if admin:
        request.session["admin_mode"] = True
    return {"ok": True}


# Only exists inside the test process; the real app never has it.
app.add_api_route("/__test__/login/{steam_id}", _test_login, methods=["POST"])


# ---------------------------------------------------------------------
# Fake Steam
# ---------------------------------------------------------------------

class FakeResponse:
    def __init__(self, data, status_code=200, text=""):
        self._data = data
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSteam:
    """
    Stands in for steam_api._http, the one place every Steam Web API and
    Store API call goes through.

    profiles:     steam_id -> {"personaname": ..., "avatarfull": ...}
    owned_games:  steam_id -> [{"appid", "name", "playtime_forever"}]
                  (missing = Steam returns no games, like a private library)
    friends:      steam_id -> [friend steam_id, ...]
                  (missing = Steam returns no friends list)
    store:        app_id -> store "data" dict (missing = success: false)
    down:         True makes every call raise a connection error
    rate_limited: True makes store calls answer 429
    calls:        (kind, params) for every request, to assert on
    """

    def __init__(self):
        self.profiles = {}
        self.owned_games = {}
        self.friends = {}
        self.store = {}
        self.down = False
        self.rate_limited = False
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = params or {}

        if self.down:
            raise requests.ConnectionError("fake Steam is down")

        if "GetOwnedGames" in url:
            self.calls.append(("owned_games", params))
            games = self.owned_games.get(params["steamid"])
            return FakeResponse({"response": {"games": games} if games else {}})

        if "GetFriendList" in url:
            self.calls.append(("friends", params))
            ids = self.friends.get(params["steamid"])
            if ids is None:
                return FakeResponse({})
            return FakeResponse({"friendslist": {"friends": [
                {"steamid": f, "relationship": "friend", "friend_since": 0} for f in ids
            ]}})

        if "GetPlayerSummaries" in url:
            self.calls.append(("profiles", params))
            players = [
                {"steamid": sid, **self.profiles[sid]}
                for sid in params["steamids"].split(",")
                if sid in self.profiles
            ]
            return FakeResponse({"response": {"players": players}})

        if "appdetails" in url:
            self.calls.append(("store", params))
            if self.rate_limited:
                return FakeResponse(None, status_code=429)
            app_id = int(params["appids"])
            data = self.store.get(app_id)
            if data is None:
                return FakeResponse({str(app_id): {"success": False}})
            return FakeResponse({str(app_id): {"success": True, "data": data}})

        raise AssertionError(f"Unexpected Steam URL in a test: {url}")

    def calls_of(self, kind):
        return [params for k, params in self.calls if k == kind]


class Mailbox(list):
    """
    The emails "sent" during one test (a list of EmailMessage objects).
    Set .fail = True to make the mail server reject every message.
    A new Mailbox is made for every test, so nothing carries over.
    """

    def __init__(self):
        super().__init__()
        self.fail = False


def make_fake_smtp(mailbox: Mailbox):
    """An SMTP class that delivers into `mailbox` instead of the network."""

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            pass

        def login(self, username, password):
            pass

        def send_message(self, message):
            if mailbox.fail:
                raise smtplib.SMTPException("fake SMTP failure")
            mailbox.append(message)

    return FakeSMTP


def _blocked(*args, **kwargs):
    raise requests.ConnectionError("Real network calls are blocked in tests")


# ---------------------------------------------------------------------
# Fixtures used automatically by every test
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    """A new, empty database file for each test."""
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr(database, "_db_initialized", False)
    database.init_db()
    yield tmp_path / "test.db"


@pytest.fixture(autouse=True)
def steam(monkeypatch):
    fake = FakeSteam()
    monkeypatch.setattr(steam_api, "_http", fake)
    monkeypatch.setattr(steam_api, "API_KEY", "test-steam-key")
    steam_api._profile_cache.clear()
    yield fake
    steam_api._profile_cache.clear()


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Emails sent during the test. outbox.fail = True makes sending fail."""
    mailbox = Mailbox()
    fake_smtp = make_fake_smtp(mailbox)
    # Patch smtplib itself: email_service uses smtplib.SMTP / SMTP_SSL,
    # so this covers every email the app sends.
    monkeypatch.setattr(smtplib, "SMTP", fake_smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", fake_smtp)
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    return mailbox


@pytest.fixture(autouse=True)
def isolate_background_work(monkeypatch):
    """
    Stop the content-filter thread from starting (it sleeps between Steam
    calls and would outlive the test), block real network calls, and reset
    in-memory state that would otherwise leak between tests.
    """
    no_check = lambda: False  # noqa: E731
    monkeypatch.setattr(content_filter, "start_check", no_check)
    monkeypatch.setattr(library_service, "start_content_check", no_check)
    monkeypatch.setattr(steam_auth, "start_content_check", no_check)

    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(requests, "post", _blocked)

    notifications._active_viewers.clear()
    content_filter.state.update(
        running=False, started_at=None, finished_at=None,
        checked=0, hidden=0, total=0, error=None,
    )
    admin_routes._refresh_state.update(
        running=False, started_at=None, finished_at=None,
        updated=0, unavailable=0, total=0,
    )
    yield


# ---------------------------------------------------------------------
# Helpers for tests
# ---------------------------------------------------------------------

@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def make_user():
    """
    Create a signed-up user. games: list of (app_id, name, playtime) or
    dicts; friends: steam IDs saved as their Steam friends.
    """
    def _make(steam_id, games=(), friends=(), admin=False, email=None):
        rows = [
            g if isinstance(g, dict)
            else {"appid": g[0], "name": g[1], "playtime_forever": g[2] if len(g) > 2 else 0}
            for g in games
        ]
        database.save_owned_games(steam_id, rows)
        if friends:
            database.save_friends(steam_id, [{"steamid": f} for f in friends])
        if admin:
            database.set_admin(steam_id, True)
        if email:
            database.admin_set_email(steam_id, email)
        return steam_id

    return _make


@pytest.fixture
def login():
    def _login(client, steam_id, admin=False, name=""):
        if admin:
            if not database.user_exists(steam_id):
                database.mark_signed_in(steam_id)
            database.set_admin(steam_id, True)
        response = client.post(
            f"/__test__/login/{steam_id}",
            params={"admin": int(admin), "name": name},
        )
        assert response.status_code == 200
        return steam_id

    return _login


# Real-looking Steam IDs for tests (above the fake-account range).
ALICE = "76561198000000001"
BOB = "76561198000000002"
CAROL = "76561198000000003"
DAVE = "76561198000000004"
EVE = "76561198000000005"
