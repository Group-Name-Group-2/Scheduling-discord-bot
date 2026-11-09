"""Pages and APIs in main.py: home, game details and game night events."""

from datetime import date, timedelta

import pytest

import database
from tests.conftest import ALICE, BOB, CAROL, DAVE

FUTURE = (date.today() + timedelta(days=7)).isoformat()
PAST = (date.today() - timedelta(days=1)).isoformat()


@pytest.fixture
def alice(client, login, make_user):
    """Alice signed in, owning game 10, with Bob and Carol as friends."""
    make_user(ALICE, games=[(10, "Shared Game")], friends=[BOB, CAROL])
    login(client, ALICE)
    return ALICE


def event_form(**overrides):
    form = {"game_id": "10", "date": FUTURE, "time": "19:00", "duration": "120",
            "friend_ids": [BOB, CAROL]}
    form.update(overrides)
    return form


# ---------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/events"])
def test_public_pages_load(client, path):
    assert client.get(path).status_code == 200


def test_static_files_served(client):
    assert client.get("/static/style.css").status_code == 200


def test_unknown_page_404(client):
    assert client.get("/no-such-page").status_code == 404


class TestGameDetails:
    def test_found(self, client, steam):
        steam.store[10] = {"name": "Game", "short_description": "Fun"}
        response = client.get("/api/games/10")
        assert response.status_code == 200
        assert response.json()["description"] == "Fun"

    def test_not_found(self, client):
        assert client.get("/api/games/10").status_code == 404

    def test_steam_down(self, client, steam):
        steam.down = True
        assert client.get("/api/games/10").status_code == 502

    def test_non_numeric_id(self, client):
        assert client.get("/api/games/abc").status_code == 422


# ---------------------------------------------------------------------
# Signed-out access
# ---------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("get", "/api/events"),
    ("post", "/events/create"),
    ("get", "/api/events/1"),
])
def test_apis_need_sign_in(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_create_page_redirects_when_signed_out(client):
    response = client.get("/events/create")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------------
# Creating events
# ---------------------------------------------------------------------

class TestCreateEvent:
    def test_create_page(self, client, alice, steam):
        steam.profiles[BOB] = {"personaname": "Bobby"}
        response = client.get("/events/create", params={"friend_id": [BOB, DAVE]})
        assert response.status_code == 200
        assert "Shared Game" in response.text
        assert "Bobby" in response.text

    def test_create_page_survives_steam_down(self, client, alice, steam):
        steam.down = True
        assert client.get("/events/create").status_code == 200

    def test_success(self, client, alice):
        response = client.post("/events/create", data=event_form())
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True

        event = database.get_event(body["event_id"])
        assert event["creator_steam_id"] == ALICE
        assert sorted(event["participant_steam_ids"]) == sorted([BOB, CAROL])

    def test_no_participants_is_fine(self, client, alice):
        form = event_form()
        del form["friend_ids"]
        assert client.post("/events/create", data=form).status_code == 200

    def test_today_is_allowed(self, client, alice):
        assert client.post("/events/create", data=event_form(date=date.today().isoformat())).status_code == 200

    @pytest.mark.parametrize("overrides, error", [
        ({"game_id": ""}, "valid game"),
        ({"game_id": "abc"}, "valid game"),
        ({"game_id": "999"}, "isn't in your saved library"),
        ({"date": ""}, "valid date"),
        ({"date": "2026-13-01"}, "valid date"),
        ({"date": "07/11/2026"}, "valid date"),
        ({"time": "7pm"}, "valid date and start time"),
        ({"time": "25:00"}, "valid date and start time"),
        ({"date": PAST}, "already passed"),
        ({"duration": ""}, "valid event duration"),
        ({"duration": "45"}, "valid event duration"),
        ({"duration": "abc"}, "valid event duration"),
        ({"friend_ids": [BOB, DAVE]}, "participants are invalid"),
    ])
    def test_validation(self, client, alice, overrides, error):
        response = client.post("/events/create", data=event_form(**overrides))
        assert response.status_code == 400
        assert error in response.json()["error"]
        assert database.list_all_events() == []

    @pytest.mark.parametrize("duration", ["60", "90", "120", "180", "240"])
    def test_allowed_durations(self, client, alice, duration):
        assert client.post("/events/create", data=event_form(duration=duration)).status_code == 200


# ---------------------------------------------------------------------
# Calendar and event details
# ---------------------------------------------------------------------

class TestCalendar:
    @pytest.fixture
    def event_id(self, make_user):
        make_user(ALICE, games=[(10, "Shared Game")])
        return database.save_game_night_event(ALICE, 10, "2099-01-02", "19:30", 90, [BOB])

    def test_calendar_feed(self, client, login, event_id):
        login(client, BOB)
        events = client.get("/api/events").json()
        assert events == [{
            "id": event_id,
            "title": "Shared Game",
            "start": "2099-01-02T19:30:00",
            "end": "2099-01-02T21:00:00",
            "extendedProps": {"appId": 10, "creatorSteamId": ALICE, "duration": 90},
        }]

    def test_calendar_empty_for_others(self, client, login, event_id):
        login(client, DAVE)
        assert client.get("/api/events").json() == []

    def test_details(self, client, login, steam, event_id):
        steam.profiles[ALICE] = {"personaname": "Alice"}
        login(client, BOB)
        details = client.get(f"/api/events/{event_id}").json()
        assert details["creator"]["name"] == "Alice"
        assert [p["steam_id"] for p in details["participants"]] == [BOB]
        assert details["participants"][0]["name"] == BOB   # no Steam profile: falls back to ID

    def test_details_hidden_from_outsiders(self, client, login, event_id):
        login(client, DAVE)
        assert client.get(f"/api/events/{event_id}").status_code == 404

    def test_details_survive_steam_down(self, client, login, steam, event_id):
        steam.down = True
        login(client, ALICE)
        assert client.get(f"/api/events/{event_id}").status_code == 200
