"""Admin dashboard (admin_routes.py) and the admin-mode checks in permissions.py."""

import io

import pytest

import admin_routes
import database
from tests.conftest import ALICE, BOB, CAROL, DAVE, EVE

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def admin(client, login, make_user, steam):
    """Alice is an admin with admin mode on. Bob is a regular user."""
    steam.profiles[ALICE] = {"personaname": "Alice"}
    steam.profiles[BOB] = {"personaname": "Bob"}
    make_user(ALICE, admin=True)
    make_user(BOB, games=[(10, "Game"), (11, "Other")], friends=[CAROL, DAVE])
    login(client, ALICE, admin=True)
    return ALICE


@pytest.fixture
def avatar_dir(tmp_path, monkeypatch):
    """Uploaded avatars go to a temp folder, not static/avatars."""
    folder = tmp_path / "avatars"
    monkeypatch.setattr(admin_routes, "AVATAR_DIR", folder)
    return folder


# Every admin page and action, for the "regular users are blocked" tests.
ADMIN_ROUTES = [
    ("get", "/admin"),
    ("get", f"/admin/users/{BOB}"),
    ("post", f"/admin/users/{BOB}/email"),
    ("post", f"/admin/users/{BOB}/name"),
    ("post", f"/admin/users/{BOB}/avatar"),
    ("get", "/admin/events/1"),
    ("post", "/admin/events/1"),
    ("post", "/admin/events/1/delete"),
    ("post", "/admin/games/ignore"),
    ("post", "/admin/games/10/unignore"),
    ("post", "/admin/libraries/refresh"),
    ("post", "/admin/libraries/max-age"),
    ("post", "/admin/content-check"),
    ("get", f"/admin/users/{BOB}/messages"),
    ("get", f"/admin/users/{BOB}/messages/{CAROL}"),
    ("post", f"/admin/users/{BOB}/messages/{CAROL}"),
    ("get", f"/admin/users/{BOB}/groups/1"),
    ("post", f"/admin/users/{BOB}/groups/1"),
]


# ---------------------------------------------------------------------
# Who can get in
# ---------------------------------------------------------------------

@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_signed_out_blocked(client, method, path):
    assert getattr(client, method)(path).status_code == 403


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_regular_user_blocked(client, login, make_user, method, path):
    make_user(BOB)
    login(client, BOB)
    assert getattr(client, method)(path).status_code == 403


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_admin_without_admin_mode_blocked(client, login, make_user, method, path):
    make_user(ALICE, admin=True)
    login(client, ALICE)            # admin flag, but admin mode not switched on
    assert getattr(client, method)(path).status_code == 403


def test_admin_flag_removed_mid_session(client, admin):
    assert client.get("/admin").status_code == 200
    database.set_admin(ALICE, False)
    assert client.get("/admin").status_code == 403
    database.set_admin(ALICE, True)
    # admin_mode was dropped from the session, so it stays off.
    assert client.get("/admin").status_code == 403


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

@pytest.mark.parametrize("status", list(admin_routes.STATUS_MESSAGES) + [""])
def test_dashboard_with_every_status(client, admin, status):
    assert client.get("/admin", params={"status": status}).status_code == 200


def test_dashboard_lists_users_and_events(client, admin):
    database.save_game_night_event(BOB, 10, "2099-01-01", "19:00", 60, [CAROL])
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Bob" in response.text and "Game" in response.text


def test_dashboard_survives_steam_down(client, admin, steam):
    import steam_api
    steam_api._profile_cache.clear()
    steam.down = True
    assert client.get("/admin").status_code == 200


# ---------------------------------------------------------------------
# Editing users
# ---------------------------------------------------------------------

class TestEditUsers:
    def test_user_page(self, client, admin):
        assert client.get(f"/admin/users/{BOB}").status_code == 200

    def test_unknown_user_redirects(self, client, admin):
        assert client.get(f"/admin/users/{EVE}").headers["location"] == "/admin"

    def test_set_and_remove_email(self, client, admin):
        response = client.post(f"/admin/users/{BOB}/email", data={"email": "Bob@Example.com"})
        assert response.headers["location"].endswith("email_saved")
        assert database.get_user_settings(BOB)["email"] == "bob@example.com"

        client.post(f"/admin/users/{BOB}/email", data={"action": "remove"})
        assert database.get_user_settings(BOB)["email"] is None

    def test_invalid_email(self, client, admin):
        response = client.post(f"/admin/users/{BOB}/email", data={"email": "nope"})
        assert response.headers["location"].endswith("email_invalid")

    def test_rename_and_reset(self, client, admin):
        response = client.post(f"/admin/users/{BOB}/name", data={"display_name": "  Bobby   B  "})
        assert response.headers["location"].endswith("name_saved")
        assert database.get_profile_overrides([BOB])[BOB]["display_name"] == "Bobby B"

        client.post(f"/admin/users/{BOB}/name", data={"action": "reset"})
        assert database.get_profile_overrides([BOB]) == {}

    @pytest.mark.parametrize("name", ["", "   ", "x" * (admin_routes.MAX_DISPLAY_NAME_LENGTH + 1)])
    def test_invalid_names(self, client, admin, name):
        response = client.post(f"/admin/users/{BOB}/name", data={"display_name": name})
        assert response.headers["location"].endswith("name_invalid")

    def test_renaming_yourself_updates_session(self, client, admin):
        client.post(f"/admin/users/{ALICE}/name", data={"display_name": "Boss"})
        assert "Boss" in client.get("/").text


class TestAvatars:
    def upload(self, client, data, filename="pic.png"):
        return client.post(
            f"/admin/users/{BOB}/avatar",
            files={"avatar": (filename, io.BytesIO(data), "image/png")},
        )

    @pytest.mark.parametrize("data, extension", [
        (PNG, "png"),
        (b"\xff\xd8\xff" + b"\x00" * 10, "jpg"),
        (b"GIF89a" + b"\x00" * 10, "gif"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10, "webp"),
    ])
    def test_upload_formats(self, client, admin, avatar_dir, data, extension):
        response = self.upload(client, data)
        assert response.headers["location"].endswith("avatar_saved")
        files = list(avatar_dir.iterdir())
        assert len(files) == 1 and files[0].suffix == f".{extension}"
        assert database.get_profile_overrides([BOB])[BOB]["avatar_path"] == f"/static/avatars/{files[0].name}"

    def test_new_upload_replaces_old_file(self, client, admin, avatar_dir):
        self.upload(client, PNG)
        self.upload(client, PNG)
        assert len(list(avatar_dir.iterdir())) == 1

    def test_reset_deletes_file(self, client, admin, avatar_dir):
        self.upload(client, PNG)
        client.post(f"/admin/users/{BOB}/avatar", data={"action": "reset"})
        assert list(avatar_dir.iterdir()) == []
        assert database.get_profile_overrides([BOB]) == {}

    def test_not_an_image(self, client, admin, avatar_dir):
        response = self.upload(client, b"<?php evil(); ?>", filename="pic.png")
        assert response.headers["location"].endswith("avatar_type")
        assert not avatar_dir.exists() or list(avatar_dir.iterdir()) == []

    def test_too_large(self, client, admin, avatar_dir):
        response = self.upload(client, PNG + b"\x00" * admin_routes.MAX_AVATAR_BYTES)
        assert response.headers["location"].endswith("avatar_large")

    def test_missing_file(self, client, admin, avatar_dir):
        assert client.post(f"/admin/users/{BOB}/avatar", data={}).headers["location"].endswith("avatar_missing")
        assert self.upload(client, b"").headers["location"].endswith("avatar_missing")


# ---------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------

class TestEditEvents:
    @pytest.fixture
    def event_id(self, admin):
        return database.save_game_night_event(BOB, 10, "2099-01-01", "19:00", 60, [CAROL])

    def form(self, **overrides):
        form = {"game_id": "11", "date": "2099-02-02", "time": "20:30",
                "duration": "90", "participant_ids": [CAROL, DAVE]}
        form.update(overrides)
        return form

    def test_event_page(self, client, event_id):
        assert client.get(f"/admin/events/{event_id}").status_code == 200

    def test_missing_event(self, client, admin):
        assert client.get("/admin/events/999").headers["location"].endswith("event_missing")
        assert client.post("/admin/events/999", data=self.form()).headers["location"].endswith("event_missing")

    def test_save(self, client, event_id):
        response = client.post(f"/admin/events/{event_id}", data=self.form())
        assert response.headers["location"].endswith("event_saved")
        event = database.get_event(event_id)
        assert (event["app_id"], event["event_date"], event["event_time"], event["duration_minutes"]) == (
            11, "2099-02-02", "20:30", 90,
        )
        assert sorted(event["participant_steam_ids"]) == sorted([CAROL, DAVE])

    @pytest.mark.parametrize("overrides", [
        {"game_id": "999"},
        {"game_id": "abc"},
        {"duration": "45"},
        {"date": "bad"},
        {"time": "bad"},
        {"participant_ids": [EVE]},
    ])
    def test_invalid_edits(self, client, event_id, overrides):
        response = client.post(f"/admin/events/{event_id}", data=self.form(**overrides))
        assert "error=" in response.headers["location"]
        assert database.get_event(event_id)["app_id"] == 10

    def test_delete(self, client, event_id):
        response = client.post(f"/admin/events/{event_id}/delete")
        assert response.headers["location"].endswith("event_deleted")
        assert database.get_event(event_id) is None
        assert client.post(f"/admin/events/{event_id}/delete").headers["location"].endswith("event_missing")


# ---------------------------------------------------------------------
# Hidden games, libraries, content check
# ---------------------------------------------------------------------

class TestGamesAndLibraries:
    def test_ignore_game(self, client, admin):
        response = client.post("/admin/games/ignore", data={"app_id": "10"})
        assert "notice=" in response.headers["location"]
        assert [g["appid"] for g in database.get_saved_games(BOB)] == [11]

    @pytest.mark.parametrize("app_id", ["", "abc", "0", "-5"])
    def test_ignore_invalid_id(self, client, admin, app_id):
        response = client.post("/admin/games/ignore", data={"app_id": app_id})
        assert "status=game_invalid" in response.headers["location"]

    def test_unignore_restores_from_steam(self, client, admin, steam):
        database.ignore_game(10)
        steam.owned_games[BOB] = [{"appid": 10, "name": "Game", "playtime_forever": 42}]

        response = client.post("/admin/games/10/unignore")
        assert "notice=" in response.headers["location"]
        assert database.list_ignored_games() == []
        assert {"appid": 10, "name": "Game", "playtime_forever": 42} in database.get_saved_games(BOB)

    def test_unignore_unknown_game(self, client, admin):
        assert client.post("/admin/games/777/unignore").headers["location"] == "/admin#games-heading"

    @pytest.mark.parametrize("minutes, status", [
        ("0", "max_age_saved"), ("60", "max_age_saved"), ("10080", "max_age_saved"),
        ("-1", "max_age_invalid"), ("10081", "max_age_invalid"), ("abc", "max_age_invalid"),
    ])
    def test_library_max_age(self, client, admin, minutes, status):
        response = client.post("/admin/libraries/max-age", data={"minutes": minutes})
        assert f"status={status}" in response.headers["location"]

    def test_refresh_all_libraries(self, client, admin, steam):
        steam.owned_games[BOB] = [{"appid": 12, "name": "New game"}]
        response = client.post("/admin/libraries/refresh")
        assert "refresh_started" in response.headers["location"]
        # TestClient runs background tasks before returning.
        assert admin_routes._refresh_state["running"] is False
        assert 12 in [g["appid"] for g in database.get_saved_games(BOB)]

    def test_refresh_already_running(self, client, admin):
        admin_routes._refresh_state["running"] = True
        assert "refresh_running" in client.post("/admin/libraries/refresh").headers["location"]

    def test_content_check_nothing_to_do(self, client, admin):
        # conftest makes start_check a no-op, so it reports nothing to check.
        assert "content_nothing" in client.post("/admin/content-check").headers["location"]

    def test_content_check_running(self, client, admin):
        import content_filter
        content_filter.state["running"] = True
        assert "content_running" in client.post("/admin/content-check").headers["location"]


# ---------------------------------------------------------------------
# Messaging as another user
# ---------------------------------------------------------------------

class TestSendAsUser:
    def test_messages_overview(self, client, admin):
        database.save_message(CAROL, BOB, "hi bob")
        assert client.get(f"/admin/users/{BOB}/messages").status_code == 200

    def test_read_conversation_does_not_mark_read(self, client, admin):
        database.save_message(CAROL, BOB, "secret")
        response = client.get(f"/admin/users/{BOB}/messages/{CAROL}")
        assert response.status_code == 200 and "secret" in response.text
        assert database.get_unread_message_count(BOB) == 1

    def test_send_as_user_is_audited(self, client, admin, outbox):
        database.admin_set_email(CAROL, "carol@example.com")
        response = client.post(f"/admin/users/{BOB}/messages/{CAROL}", data={"body": "from bob"})
        assert response.headers["location"].endswith("message_sent")

        message = database.get_conversation(BOB, CAROL, include_audit=True)[0]
        assert (message["sender_steam_id"], message["sent_by_steam_id"]) == (BOB, ALICE)
        assert outbox[0]["Subject"].startswith("Bob ")

    @pytest.mark.parametrize("body, status", [
        ("", "message_empty"), ("x" * (database.MAX_MESSAGE_LENGTH + 1), "message_long"),
    ])
    def test_send_as_user_bad_body(self, client, admin, body, status):
        response = client.post(f"/admin/users/{BOB}/messages/{CAROL}", data={"body": body})
        assert response.headers["location"].endswith(status)

    def test_cannot_send_to_non_friend(self, client, admin):
        response = client.post(f"/admin/users/{BOB}/messages/{EVE}", data={"body": "hi"})
        assert response.headers["location"] == f"/admin/users/{BOB}/messages"
        assert database.get_conversation(BOB, EVE) == []

    def test_group_as_user(self, client, admin):
        group_id = database.create_group(BOB, [CAROL, DAVE])
        assert client.get(f"/admin/users/{BOB}/groups/{group_id}").status_code == 200

        response = client.post(f"/admin/users/{BOB}/groups/{group_id}", data={"body": "as bob"})
        assert response.headers["location"].endswith("message_sent")
        message = database.get_group_messages(group_id, include_audit=True)[-1]
        assert (message["sender_steam_id"], message["sent_by_steam_id"]) == (BOB, ALICE)

    def test_group_user_not_in(self, client, admin):
        group_id = database.create_group(CAROL, [DAVE, EVE])
        response = client.get(f"/admin/users/{BOB}/groups/{group_id}")
        assert response.headers["location"] == f"/admin/users/{BOB}/messages"
