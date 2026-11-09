"""Sign-in (Steam + email), Settings, and the library/friends pages."""

from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

import database
import settings_routes
import steam_auth
from tests.conftest import ALICE, BOB, CAROL, EVE, FakeResponse


# ---------------------------------------------------------------------
# Steam sign-in
# ---------------------------------------------------------------------

class TestSteamLogin:
    def callback_params(self, steam_id=ALICE, **overrides):
        params = {
            "openid.mode": "id_res",
            "openid.claimed_id": f"https://steamcommunity.com/openid/id/{steam_id}",
            "openid.identity": f"https://steamcommunity.com/openid/id/{steam_id}",
            "openid.return_to": "http://testserver/auth/steam/callback",
        }
        params.update(overrides)
        return params

    @pytest.fixture
    def steam_says(self, monkeypatch):
        """Make Steam's OpenID check answer valid (True) or not (False)."""
        def _set(valid=True):
            text = "ns:http://specs.openid.net/auth/2.0\nis_valid:%s\n" % ("true" if valid else "false")
            monkeypatch.setattr(steam_auth.requests, "post", lambda *a, **k: FakeResponse(None, text=text))
        return _set

    def test_redirects_to_steam(self, client):
        response = client.get("/auth/steam")
        assert response.status_code in (302, 307)
        location = urlparse(response.headers["location"])
        assert location.netloc == "steamcommunity.com"
        assert parse_qs(location.query)["openid.return_to"] == ["http://testserver/auth/steam/callback"]

    def test_successful_login(self, client, steam, steam_says):
        steam_says(True)
        steam.owned_games[ALICE] = [{"appid": 1, "name": "Game", "playtime_forever": 10}]
        steam.profiles[ALICE] = {"personaname": "Alice"}

        response = client.get("/auth/steam/callback", params=self.callback_params())
        assert response.status_code == 303 and response.headers["location"] == "/library"
        assert database.get_saved_games(ALICE)[0]["name"] == "Game"

        # The session now works for signed-in pages.
        assert client.get("/library").status_code == 200

    def test_recent_library_not_downloaded_again(self, client, steam, steam_says, make_user):
        steam_says(True)
        make_user(ALICE, games=[(1, "Saved")])
        client.get("/auth/steam/callback", params=self.callback_params())
        assert steam.calls_of("owned_games") == []

    @pytest.mark.parametrize("overrides, status", [
        ({"openid.mode": "cancel"}, 400),
        ({"openid.claimed_id": "https://evil.example/openid/id/1"}, 400),
        ({"openid.identity": "https://steamcommunity.com/openid/id/999"}, 400),
        ({"openid.return_to": "https://other-site.example/auth/steam/callback"}, 400),
    ])
    def test_bad_responses_rejected(self, client, steam_says, overrides, status):
        steam_says(True)
        response = client.get("/auth/steam/callback", params=self.callback_params(**overrides))
        assert response.status_code == status
        assert not database.user_exists(ALICE)

    def test_non_numeric_steam_id_rejected(self, client, steam_says):
        steam_says(True)
        assert client.get("/auth/steam/callback", params=self.callback_params(steam_id="abc")).status_code == 400

    def test_steam_rejects_signature(self, client, steam_says):
        steam_says(False)
        assert client.get("/auth/steam/callback", params=self.callback_params()).status_code == 401

    def test_steam_unreachable_for_verification(self, client):
        # conftest blocks requests.post, which looks like Steam being down.
        assert client.get("/auth/steam/callback", params=self.callback_params()).status_code == 502

    def test_library_download_fails(self, client, steam, steam_says):
        steam_says(True)
        steam.down = True
        assert client.get("/auth/steam/callback", params=self.callback_params()).status_code == 502

    def test_logout(self, client, login):
        login(client, ALICE)
        assert client.post("/auth/logout").status_code == 200
        assert client.get("/library").status_code == 303


# ---------------------------------------------------------------------
# Email sign-in
# ---------------------------------------------------------------------

class TestEmailLogin:
    def test_request_and_verify(self, client, outbox, steam):
        database.admin_set_email(ALICE, "alice@example.com")
        steam.profiles[ALICE] = {"personaname": "Alice"}

        response = client.post("/auth/email/request", data={"email": "alice@example.com"})
        assert response.status_code == 200

        text = outbox[0].get_body(("plain",)).get_content()
        link = next(word for word in text.split() if "/auth/email/verify?token=" in word)
        assert link.startswith("http://testserver/")

        response = client.get(link.replace("http://testserver", ""))
        assert response.status_code == 303 and response.headers["location"] == "/events"
        assert client.get("/library").status_code == 200

    def test_unknown_email(self, client, outbox):
        response = client.post("/auth/email/request", data={"email": "who@example.com"})
        assert response.status_code == 400
        assert outbox == []

    def test_bad_token(self, client):
        response = client.get("/auth/email/verify", params={"token": "nope"})
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert client.get("/library").status_code == 303


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------

class TestSettings:
    @pytest.fixture
    def alice(self, client, login, make_user):
        make_user(ALICE)
        login(client, ALICE, name="Alice")
        return ALICE

    def verify_link(self, message):
        text = message.get_body(("plain",)).get_content()
        return next(w for w in text.split() if "/settings/email/verify?token=" in w)

    @pytest.mark.parametrize("path", ["/settings"])
    def test_redirect_when_signed_out(self, client, path):
        assert client.get(path).status_code == 303

    @pytest.mark.parametrize("status", list(settings_routes.STATUS_MESSAGES) + ["", "made-up"])
    def test_page_with_every_status(self, client, alice, status):
        assert client.get("/settings", params={"status": status}).status_code == 200

    def test_link_email(self, client, alice, outbox):
        response = client.post("/settings/email", data={"email": " Alice@Example.COM "})
        assert response.headers["location"] == "/settings?status=sent"
        assert outbox[0]["To"] == "alice@example.com"

        response = client.get(self.verify_link(outbox[0]).replace("http://testserver", ""))
        assert response.headers["location"] == "/settings?status=verified"
        assert database.get_user_settings(ALICE)["email"] == "alice@example.com"

    def test_verify_link_on_another_device(self, client, alice, outbox):
        from fastapi.testclient import TestClient
        from main import app

        client.post("/settings/email", data={"email": "alice@example.com"})
        link = self.verify_link(outbox[0]).replace("http://testserver", "")

        with TestClient(app, follow_redirects=False) as other_device:
            response = other_device.get(link)
        assert response.status_code == 200
        assert database.get_user_settings(ALICE)["email"] == "alice@example.com"

    @pytest.mark.parametrize("email", ["", "nope", "a@b"])
    def test_invalid_email(self, client, alice, outbox, email):
        assert client.post("/settings/email", data={"email": email}).headers["location"].endswith("invalid")
        assert outbox == []

    def test_same_email(self, client, alice):
        database.admin_set_email(ALICE, "alice@example.com")
        assert client.post("/settings/email", data={"email": "alice@example.com"}).headers["location"].endswith("same")

    def test_resend_cooldown(self, client, alice, outbox):
        client.post("/settings/email", data={"email": "a@example.com"})
        response = client.post("/settings/email", data={"email": "b@example.com"})
        assert response.headers["location"].endswith("wait")
        assert len(outbox) == 1

    def test_send_failure(self, client, alice, outbox):
        outbox.fail = True
        assert client.post("/settings/email", data={"email": "a@example.com"}).headers["location"].endswith("send_failed")

    def test_bad_verify_token(self, client, alice):
        response = client.get("/settings/email/verify", params={"token": "wrong"})
        assert response.headers["location"] == "/settings?status=bad_token"

    def test_remove_email(self, client, alice):
        database.admin_set_email(ALICE, "alice@example.com")
        client.post("/settings/email/remove")
        assert database.get_user_settings(ALICE)["email"] is None

    @pytest.mark.parametrize("value, expected", [("on", True), ("", False)])
    def test_notifications(self, client, alice, value, expected):
        client.post("/settings/notifications", data={"notify_messages": value})
        assert database.get_user_settings(ALICE)["notify_messages"] is expected

    def test_admin_mode_needs_admin_flag(self, client, alice, monkeypatch):
        monkeypatch.setattr(settings_routes, "ALLOW_SELF_ADMIN_FOR_TESTING", False)
        client.post("/settings/admin-mode", data={"admin_mode": "on"})
        assert client.get("/admin").status_code == 403

    def test_admin_mode_for_real_admin(self, client, alice, monkeypatch):
        monkeypatch.setattr(settings_routes, "ALLOW_SELF_ADMIN_FOR_TESTING", False)
        database.set_admin(ALICE, True)

        response = client.post("/settings/admin-mode", data={"admin_mode": "on"})
        assert response.headers["location"].endswith("admin_on")
        assert client.get("/admin").status_code == 200

        client.post("/settings/admin-mode", data={"admin_mode": ""})
        assert client.get("/admin").status_code == 403


# ---------------------------------------------------------------------
# Library and friends pages
# ---------------------------------------------------------------------

class TestLibraryPages:
    @pytest.fixture
    def alice(self, client, login, make_user, steam):
        steam.profiles[BOB] = {"personaname": "Bob"}
        make_user(ALICE, games=[(1, "Both"), (2, "Alice only")], friends=[BOB])
        login(client, ALICE)
        return ALICE

    @pytest.mark.parametrize("path", [
        "/library", "/friends", "/shared-games",
        f"/friends/{BOB}/library", f"/friends/{BOB}/shared-games",
    ])
    def test_redirect_when_signed_out(self, client, path):
        assert client.get(path).headers["location"] == "/"

    def test_library(self, client, alice):
        response = client.get("/library")
        assert response.status_code == 200 and "Alice only" in response.text

    def test_friends_saves_steam_list(self, client, alice, steam):
        steam.friends[ALICE] = [BOB, CAROL]
        steam.profiles[CAROL] = {"personaname": "Carol"}
        response = client.get("/friends")
        assert response.status_code == 200 and "Carol" in response.text
        assert {f["steamid"] for f in database.get_saved_friends(ALICE)} == {BOB, CAROL}

    def test_friends_private_list_keeps_saved(self, client, alice):
        response = client.get("/friends")   # fake Steam has no list for Alice
        assert response.status_code == 200 and "public" in response.text
        assert [f["steamid"] for f in database.get_saved_friends(ALICE)] == [BOB]

    def test_friends_steam_down(self, client, alice, steam):
        steam.down = True
        response = client.get("/friends")
        assert response.status_code == 200 and "Could not retrieve" in response.text

    def test_friend_library(self, client, alice, steam):
        steam.owned_games[BOB] = [{"appid": 1, "name": "Both"}, {"appid": 3, "name": "Bob only"}]
        response = client.get(f"/friends/{BOB}/library")
        assert response.status_code == 200 and "Bob only" in response.text

    def test_non_friend_library_blocked(self, client, alice):
        assert client.get(f"/friends/{EVE}/library").headers["location"] == "/friends"
        assert client.get(f"/friends/{EVE}/shared-games").headers["location"] == "/friends"

    def test_friend_shared_games(self, client, alice, steam):
        steam.owned_games[BOB] = [{"appid": 1, "name": "Both"}, {"appid": 3, "name": "Bob only"}]
        response = client.get(f"/friends/{BOB}/shared-games")
        assert response.status_code == 200
        assert "Both" in response.text and "Alice only" not in response.text

    def test_shared_games_compare(self, client, alice, steam):
        steam.owned_games[BOB] = [{"appid": 1, "name": "Both"}]
        response = client.get("/shared-games", params={"friend_ids": [BOB, EVE]})
        assert response.status_code == 200 and "Both" in response.text

    def test_shared_games_private_friend(self, client, alice):
        response = client.get("/shared-games", params={"friend_ids": [BOB]})
        assert response.status_code == 200 and "Bob" in response.text


# ---------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------

class TestAvailability:
    @pytest.fixture
    def alice(self, client, login, make_user):
        make_user(ALICE, games=[(10, "Game")], friends=[BOB])
        login(client, ALICE)
        return ALICE

    def this_saturday(self):
        from availability_routes import upcoming_weekends
        return upcoming_weekends(date.today())[0][0]

    def save(self, client, slots, saturday=None, user=None):
        saturday = saturday or self.this_saturday()
        payload = {"weekend": saturday.isoformat(), "slots": slots}
        if user:
            payload["user"] = user
        return client.post("/api/availability", json=payload)

    def test_page(self, client, alice):
        assert client.get("/availability").status_code == 200

    def test_signed_out(self, client):
        assert client.get("/availability").status_code == 303
        assert client.post("/api/availability", json={}).status_code == 401

    def test_save_and_reload(self, client, alice):
        sat = self.this_saturday()
        sun = sat + timedelta(days=1)
        response = self.save(client, [[sat.isoformat(), 19], [sun.isoformat(), 20], [sat.isoformat(), 19]])
        assert response.json() == {"saved": 2}
        assert database.get_availability(ALICE, sat.isoformat(), sun.isoformat()) == [
            (sat.isoformat(), 19), (sun.isoformat(), 20),
        ]

    def test_saving_empty_clears_weekend(self, client, alice):
        sat = self.this_saturday()
        self.save(client, [[sat.isoformat(), 19]])
        self.save(client, [])
        assert database.get_availability(ALICE, sat.isoformat(), sat.isoformat()) == []

    @pytest.mark.parametrize("slot", [
        ["2000-01-01", 19],   # not this weekend
        ["SAT", 7],           # before 08:00
        ["SAT", 24],          # past midnight
        ["SAT", "x"],
        ["SAT"],
        "nonsense",
    ])
    def test_invalid_slots(self, client, alice, slot):
        if isinstance(slot, list) and slot and slot[0] == "SAT":
            slot = [self.this_saturday().isoformat(), *slot[1:]]
        assert self.save(client, [slot]).status_code == 400

    def test_weekend_out_of_range(self, client, alice):
        far = self.this_saturday() + timedelta(weeks=20)
        assert self.save(client, [], saturday=far).status_code == 400

    def test_bad_payload(self, client, alice):
        assert client.post("/api/availability", json={"slots": []}).status_code == 400
        assert client.post("/api/availability", content=b"not json",
                           headers={"content-type": "application/json"}).status_code == 400

    def test_cannot_edit_someone_else(self, client, alice, make_user):
        make_user(BOB)
        assert self.save(client, [], user=BOB).status_code == 403
        assert client.get("/availability", params={"user": BOB}).headers["location"] == "/availability"

    def test_admin_can_edit_someone_else(self, client, login, make_user):
        make_user(BOB)
        login(client, ALICE, admin=True)
        sat = self.this_saturday().isoformat()
        assert self.save(client, [[sat, 19]], user=BOB).status_code == 200
        assert database.get_availability(BOB, sat, sat) == [(sat, 19)]
        assert client.get("/availability", params={"user": BOB}).status_code == 200


# ---------------------------------------------------------------------
# "Suggest a time" (/api/events/recommend)
# ---------------------------------------------------------------------

class TestRecommendApi:
    @pytest.fixture
    def alice(self, client, login, make_user, steam):
        steam.profiles[BOB] = {"personaname": "Bob"}
        make_user(ALICE, games=[(10, "Shared")], friends=[BOB, CAROL])
        make_user(BOB, games=[(10, "Shared")])
        login(client, ALICE)
        return ALICE

    def saturday(self):
        from availability_routes import upcoming_weekends
        # Next week's Saturday, so the time is always in the future.
        return upcoming_weekends(date.today())[1][0].isoformat()

    def test_needs_friends(self, client, alice):
        response = client.post("/api/events/recommend", data={"duration": "120"})
        assert response.status_code == 400 and "at least one friend" in response.json()["error"]

    def test_rejects_non_friends(self, client, alice):
        response = client.post("/api/events/recommend", data={"friend_ids": [EVE]})
        assert response.status_code == 400

    def test_nobody_has_availability(self, client, alice):
        response = client.post("/api/events/recommend", data={"friend_ids": [BOB]})
        assert response.status_code == 422 and "availability" in response.json()["error"]

    def test_suggestion(self, client, alice):
        sat = self.saturday()
        database.replace_availability(ALICE, [sat], [(sat, 19), (sat, 20)])
        database.replace_availability(BOB, [sat], [(sat, 19), (sat, 20)])

        response = client.post("/api/events/recommend", data={"friend_ids": [BOB, CAROL], "duration": "120"})
        assert response.status_code == 200
        body = response.json()
        assert (body["date"], body["time"]) == (sat, "19:00")
        assert body["attendees"] == ["You", "Bob"]
        assert body["game"]["name"] == "Shared"
        assert body["group_size"] == 3
        assert any("Haven't added availability" in r for r in body["reasons"])

    @pytest.mark.parametrize("duration", ["45", "abc", ""])
    def test_bad_duration_falls_back_to_two_hours(self, client, alice, duration):
        sat = self.saturday()
        database.replace_availability(ALICE, [sat], [(sat, 19), (sat, 20)])
        response = client.post("/api/events/recommend", data={"friend_ids": [BOB], "duration": duration})
        assert response.json()["duration_minutes"] == 120

    def test_steam_down_still_suggests(self, client, alice, steam):
        sat = self.saturday()
        database.replace_availability(ALICE, [sat], [(sat, 19), (sat, 20)])
        steam.down = True
        import steam_api
        steam_api._profile_cache.clear()
        assert client.post("/api/events/recommend", data={"friend_ids": [BOB]}).status_code == 200
