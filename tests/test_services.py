"""Notifications, library syncing, the content filter and magic links."""

from datetime import datetime, timedelta, timezone

import pytest

import auth_email
import content_filter
import database
import library_service
import notifications
import steam_api
import fake_accounts
from tests.conftest import ALICE, BOB, CAROL


# ---------------------------------------------------------------------
# notifications.py
# ---------------------------------------------------------------------

class TestDirectMessageNotifications:
    def send(self, body="hello"):
        database.save_message(ALICE, BOB, body)
        notifications.notify_new_message(ALICE, "Alice", BOB, body, "http://testserver")

    def test_emails_first_unread_message(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        self.send()
        assert len(outbox) == 1
        assert outbox[0]["To"] == "bob@example.com"
        assert outbox[0]["Subject"] == "Alice sent you a message on Game Night"
        assert f"http://testserver/messages/{ALICE}" in outbox[0].get_body(("plain",)).get_content()

    def test_only_first_unread_message_emails(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        self.send("1")
        self.send("2")
        assert len(outbox) == 1

    def test_emails_again_after_reading(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        self.send("1")
        database.mark_conversation_read(BOB, ALICE)
        self.send("2")
        assert len(outbox) == 2

    def test_no_email_without_address(self, outbox):
        self.send()
        assert outbox == []

    def test_no_email_when_turned_off(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        database.set_notify_messages(BOB, False)
        self.send()
        assert outbox == []

    def test_no_email_while_viewing(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        notifications.mark_viewing(BOB, ALICE)
        self.send()
        assert outbox == []

    def test_long_message_preview_trimmed_and_html_escaped(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        body = "<script>" + "x" * 300
        database.save_message(ALICE, BOB, body)
        notifications.notify_new_message(ALICE, "Al<b>ice", BOB, body, "http://testserver")

        html = outbox[0].get_body(("html",)).get_content()
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "Al&lt;b&gt;ice" in html
        assert "x" * 300 not in html

    def test_sender_name_with_newline_stays_one_line(self, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        database.save_message(ALICE, BOB, "hi")
        notifications.notify_new_message(ALICE, "Evil\nBcc: x@y.z", BOB, "hi", "http://testserver")
        assert "\n" not in outbox[0]["Subject"]


class TestGroupNotifications:
    def test_each_other_member_emailed_once(self, outbox):
        group_id = database.create_group(ALICE, [BOB, CAROL], "Squad")
        database.admin_set_email(ALICE, "alice@example.com")
        database.admin_set_email(BOB, "bob@example.com")
        database.admin_set_email(CAROL, "carol@example.com")

        for body in ("1", "2"):
            database.save_group_message(group_id, ALICE, body)
            notifications.notify_group_message(
                group_id, "Squad", ALICE, "Alice", [ALICE, BOB, CAROL], body, "http://testserver"
            )

        assert sorted(m["To"] for m in outbox) == ["bob@example.com", "carol@example.com"]
        assert outbox[0]["Subject"] == "Alice sent a message in Squad on Game Night"

    def test_member_viewing_group_not_emailed(self, outbox):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        database.admin_set_email(BOB, "bob@example.com")
        notifications.mark_viewing(BOB, notifications.group_key(group_id))

        database.save_group_message(group_id, ALICE, "hi")
        notifications.notify_group_message(
            group_id, "a group chat", ALICE, "Alice", [ALICE, BOB, CAROL], "hi", "http://testserver"
        )
        assert outbox == []


# ---------------------------------------------------------------------
# library_service.py
# ---------------------------------------------------------------------

class TestLibraryMaxAge:
    def test_default(self):
        assert library_service.library_max_age_minutes() == library_service.DEFAULT_LIBRARY_MAX_AGE_MINUTES

    @pytest.mark.parametrize("stored, expected", [
        ("0", 0), ("30", 30), ("-5", 0), ("999999", library_service.MAX_LIBRARY_MAX_AGE_MINUTES),
        ("junk", library_service.DEFAULT_LIBRARY_MAX_AGE_MINUTES),
    ])
    def test_stored_values_clamped(self, stored, expected):
        database.set_setting(library_service.LIBRARY_MAX_AGE_SETTING, stored)
        assert library_service.library_max_age_minutes() == expected

    def test_recent_library(self, make_user):
        assert not library_service.library_is_recent(ALICE)
        make_user(ALICE)
        assert library_service.library_is_recent(ALICE)

    def test_zero_means_never_recent(self, make_user):
        make_user(ALICE)
        database.set_setting(library_service.LIBRARY_MAX_AGE_SETTING, "0")
        assert not library_service.library_is_recent(ALICE)

    def test_old_library_not_recent(self, make_user):
        make_user(ALICE)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with database.get_connection() as connection:
            connection.execute("UPDATE steam_users SET library_synced_at = ?", (old,))
        assert not library_service.library_is_recent(ALICE)


class TestSyncFriendLibrary:
    def test_downloads_and_saves(self, steam):
        steam.owned_games[BOB] = [{"appid": 1, "name": "Game", "playtime_forever": 5}]
        result = library_service.sync_friend_library(BOB)
        assert result["available"] is True
        assert result["games"] == [{"appid": 1, "name": "Game", "playtime_forever": 5}]
        # A friend synced this way hasn't signed in themselves.
        assert database.list_users()[0]["last_synced"] is None

    def test_recent_library_skips_steam(self, steam, make_user):
        make_user(BOB, games=[(1, "Saved")])
        result = library_service.sync_friend_library(BOB)
        assert result["games"][0]["name"] == "Saved"
        assert steam.calls_of("owned_games") == []

    def test_private_library_keeps_saved_games(self, steam, make_user):
        make_user(BOB, games=[(1, "Saved")])
        database.set_setting(library_service.LIBRARY_MAX_AGE_SETTING, "0")
        result = library_service.sync_friend_library(BOB)   # Steam returns nothing
        assert result["available"] is False
        assert result["games"][0]["name"] == "Saved"
        assert "private" in result["message"]

    def test_many_in_parallel_and_steam_down(self, steam):
        steam.owned_games[BOB] = [{"appid": 1, "name": "G"}]
        assert library_service.sync_friend_libraries([BOB, BOB])[BOB]["available"]

        steam.down = True
        assert library_service.sync_friend_libraries([CAROL]) == {CAROL: None}
        assert library_service.sync_friend_libraries([]) == {}


# ---------------------------------------------------------------------
# steam_api.py (against the fake Steam)
# ---------------------------------------------------------------------

class TestSteamApi:
    def test_profiles_cached(self, steam):
        steam.profiles[ALICE] = {"personaname": "Alice"}
        assert steam_api.get_player_summaries([ALICE])[ALICE]["personaname"] == "Alice"
        steam_api.get_player_summaries([ALICE])
        assert len(steam.calls_of("profiles")) == 1

    def test_profiles_batched_by_100(self, steam):
        ids = [str(76561198100000000 + i) for i in range(250)]
        steam_api.get_player_summaries(ids)
        assert [len(c["steamids"].split(",")) for c in steam.calls_of("profiles")] == [100, 100, 50]

    def test_admin_override_wins(self, steam, make_user):
        steam.profiles[ALICE] = {"personaname": "Alice", "avatarfull": "steam.png"}
        make_user(ALICE)
        database.admin_set_display_name(ALICE, "Renamed")

        assert steam_api.get_player_summaries([ALICE])[ALICE]["personaname"] == "Renamed"
        assert steam_api.get_player_summaries([ALICE], apply_overrides=False)[ALICE]["personaname"] == "Alice"

    def test_test_accounts_never_call_steam(self, steam):
        test_id = fake_accounts.test_steam_id(4)
        assert steam_api.get_player_summaries([test_id])[test_id]["personaname"] == "Test user 4"
        assert steam_api.get_owned_games(test_id) == []
        assert steam.calls == []

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.setattr(steam_api, "API_KEY", None)
        with pytest.raises(RuntimeError):
            steam_api.get_owned_games(ALICE)

    def test_friends_list(self, steam):
        steam.friends[ALICE] = [BOB]
        assert [f["steamid"] for f in steam_api.get_friends_list(ALICE)] == [BOB]
        assert steam_api.get_friends_list(CAROL) is None   # private list

    def test_content_rating(self, steam):
        steam.store[10] = {"required_age": "18", "content_descriptors": {"ids": [3, "4"]}}
        assert steam_api.get_content_rating(10) == {"descriptor_ids": [3, 4], "required_age": 18}
        assert steam_api.get_content_rating(11) is None

    def test_content_rating_rate_limited(self, steam):
        steam.rate_limited = True
        with pytest.raises(steam_api.SteamRateLimited):
            steam_api.get_content_rating(10)

    def test_game_details(self, steam):
        steam.store[10] = {"name": "Game", "genres": [{"description": "Action"}]}
        details = steam_api.get_game_details(10)
        assert details["name"] == "Game" and details["genres"] == ["Action"]
        assert steam_api.get_game_details(11) is None


# ---------------------------------------------------------------------
# content_filter.py (the background job, run directly)
# ---------------------------------------------------------------------

class TestContentFilterRun:
    @pytest.fixture(autouse=True)
    def no_sleep(self, monkeypatch):
        monkeypatch.setattr(content_filter.time, "sleep", lambda seconds: None)

    def test_rates_and_hides_adult_games(self, steam, make_user):
        make_user(ALICE, games=[(1, "Fine"), (2, "Adult"), (3, "Gone from store")])
        steam.store[1] = {"content_descriptors": {"ids": [2]}}
        steam.store[2] = {"content_descriptors": {"ids": [3]}, "required_age": 18}

        content_filter.state["running"] = True
        content_filter._run()

        assert [g["appid"] for g in database.get_saved_games(ALICE)] == [1, 3]
        assert database.list_ignored_games()[0]["reason"].startswith("Auto:")
        assert database.content_rating_counts() == {"sfw": 1, "nsfw": 1, "unknown": 1, "unrated": 0}
        assert content_filter.state["hidden"] == 1
        assert content_filter.state["checked"] == 3
        assert content_filter.state["running"] is False

    def test_stops_when_steam_keeps_rate_limiting(self, steam, make_user):
        make_user(ALICE, games=[(1, "A")])
        steam.rate_limited = True
        content_filter._run()
        assert "rate-limiting" in content_filter.state["error"]
        assert database.list_unrated_games() == [{"appid": 1, "name": "A"}]

    def test_network_error_leaves_game_unrated(self, steam, make_user):
        make_user(ALICE, games=[(1, "A")])
        steam.down = True
        content_filter._run()
        assert content_filter.state["error"]
        assert database.list_unrated_games() == [{"appid": 1, "name": "A"}]


# ---------------------------------------------------------------------
# auth_email.py (magic-link sign-in)
# ---------------------------------------------------------------------

class TestMagicLink:
    def link_token(self, message):
        text = message.get_body(("plain",)).get_content()
        return text.split("token=")[1].split()[0]

    def test_invalid_email(self, outbox):
        ok, message = auth_email.request_magic_link("nope", "http://testserver")
        assert not ok and "valid email" in message
        assert outbox == []

    def test_unknown_email(self, outbox):
        ok, message = auth_email.request_magic_link("who@example.com", "http://testserver")
        assert not ok and "No registered account" in message

    def test_full_sign_in(self, outbox):
        database.admin_set_email(ALICE, "alice@example.com")
        ok, _ = auth_email.request_magic_link("  Alice@Example.com ", "http://testserver")
        assert ok
        assert outbox[0]["To"] == "alice@example.com"

        token = self.link_token(outbox[0])
        assert auth_email.verify_magic_link(token) == {"steam_id": ALICE, "email": "alice@example.com"}
        assert auth_email.verify_magic_link(token) is None   # single use

    def test_cooldown(self, outbox):
        database.admin_set_email(ALICE, "alice@example.com")
        assert auth_email.request_magic_link("alice@example.com", "http://testserver")[0]
        ok, message = auth_email.request_magic_link("alice@example.com", "http://testserver")
        assert not ok and "less than a minute" in message
        assert len(outbox) == 1

    def test_send_failure_reported(self, outbox):
        outbox.fail = True
        database.admin_set_email(ALICE, "alice@example.com")
        ok, message = auth_email.request_magic_link("alice@example.com", "http://testserver")
        assert not ok and "Failed to send" in message

    @pytest.mark.parametrize("token", ["", "made-up-token"])
    def test_bad_tokens(self, token):
        assert auth_email.verify_magic_link(token) is None
