"""database.py: each test starts with an empty database (see conftest.py)."""

from datetime import datetime, timedelta, timezone

import pytest

import database
from tests.conftest import ALICE, BOB, CAROL, DAVE


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------

class TestSchema:
    def test_init_db_is_repeatable(self):
        database._db_initialized = False
        database.init_db()
        database._db_initialized = False
        database.init_db()   # must not fail on an existing database

    def test_old_database_gets_new_columns(self, tmp_path, monkeypatch):
        """A database from before is_admin/display_name/etc. existed is migrated."""
        import sqlite3

        old = tmp_path / "old.db"
        with sqlite3.connect(old) as connection:
            connection.executescript("""
                CREATE TABLE steam_users (steam_id TEXT PRIMARY KEY, last_synced TEXT);
                CREATE TABLE ignored_games (app_id INTEGER PRIMARY KEY, name TEXT NOT NULL, ignored_at TEXT NOT NULL);
                CREATE TABLE messages (message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_steam_id TEXT NOT NULL, recipient_steam_id TEXT NOT NULL,
                    body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
            """)

        monkeypatch.setattr(database, "DATABASE_PATH", old)
        monkeypatch.setattr(database, "_db_initialized", False)
        database.init_db()

        with database.get_connection() as connection:
            users = {r[1] for r in connection.execute("PRAGMA table_info(steam_users)")}
            ignored = {r[1] for r in connection.execute("PRAGMA table_info(ignored_games)")}
            messages = {r[1] for r in connection.execute("PRAGMA table_info(messages)")}

        assert {"is_admin", "display_name", "avatar_path", "library_synced_at"} <= users
        assert "reason" in ignored
        assert "sent_by_steam_id" in messages


# ---------------------------------------------------------------------
# Users and admins
# ---------------------------------------------------------------------

class TestUsers:
    def test_unknown_user(self):
        assert not database.user_exists(ALICE)
        assert not database.is_admin(ALICE)

    def test_set_admin_needs_existing_user(self):
        assert database.set_admin(ALICE) is False
        database.mark_signed_in(ALICE)
        assert database.set_admin(ALICE) is True
        assert database.is_admin(ALICE)
        database.set_admin(ALICE, False)
        assert not database.is_admin(ALICE)

    def test_list_users(self, make_user):
        make_user(ALICE, admin=True, email="alice@example.com")
        make_user(BOB)
        users = {u["steam_id"]: u for u in database.list_users()}
        assert users[ALICE]["is_admin"] and users[ALICE]["email"] == "alice@example.com"
        assert not users[BOB]["is_admin"] and users[BOB]["email"] is None

    def test_profile_overrides(self, make_user):
        make_user(ALICE)
        make_user(BOB)
        database.admin_set_display_name(ALICE, "Ally")
        database.admin_set_avatar(BOB, "/static/avatars/bob.png")

        assert database.get_profile_overrides([ALICE, BOB, CAROL]) == {
            ALICE: {"display_name": "Ally", "avatar_path": None},
            BOB: {"display_name": None, "avatar_path": "/static/avatars/bob.png"},
        }

        database.admin_set_display_name(ALICE, None)
        assert ALICE not in database.get_profile_overrides([ALICE])

    def test_profile_overrides_empty_list(self):
        assert database.get_profile_overrides([]) == {}

    def test_app_settings(self):
        assert database.get_setting("x") is None
        database.set_setting("x", "1")
        database.set_setting("x", "2")
        assert database.get_setting("x") == "2"


# ---------------------------------------------------------------------
# Libraries
# ---------------------------------------------------------------------

class TestLibraries:
    def test_save_and_read_sorted(self):
        database.save_owned_games(ALICE, [
            {"appid": 2, "name": "beta", "playtime_forever": 5},
            {"appid": 1, "name": "Alpha"},
        ])
        assert database.get_saved_games(ALICE) == [
            {"appid": 1, "name": "Alpha", "playtime_forever": 0},
            {"appid": 2, "name": "beta", "playtime_forever": 5},
        ]

    def test_resave_updates_playtime_and_name(self):
        database.save_owned_games(ALICE, [{"appid": 1, "name": "Old", "playtime_forever": 1}])
        database.save_owned_games(ALICE, [{"appid": 1, "name": "New", "playtime_forever": 99}])
        assert database.get_saved_games(ALICE) == [{"appid": 1, "name": "New", "playtime_forever": 99}]

    def test_missing_name_and_appid(self):
        database.save_owned_games(ALICE, [{"appid": 1}, {"name": "no id"}])
        assert database.get_saved_games(ALICE) == [{"appid": 1, "name": "Unknown Game", "playtime_forever": 0}]

    def test_mark_synced_false_keeps_last_signin(self):
        database.save_owned_games(ALICE, [], mark_synced=False)
        assert database.user_exists(ALICE)
        assert database.list_users()[0]["last_synced"] is None
        assert database.get_library_synced_at(ALICE) is not None

    def test_library_synced_at(self):
        assert database.get_library_synced_at(ALICE) is None
        database.save_owned_games(ALICE, [])
        synced = database.get_library_synced_at(ALICE)
        assert datetime.now(timezone.utc) - synced < timedelta(seconds=5)

    def test_shared_games(self, make_user):
        make_user(ALICE, games=[(1, "Both", 10), (2, "Alice only")])
        make_user(BOB, games=[(1, "Both", 3), (3, "Bob only")])
        assert database.get_shared_games([ALICE, BOB]) == [
            {"appid": 1, "name": "Both", "min_playtime_forever": 3}
        ]

    def test_shared_games_one_person_is_their_library(self, make_user):
        make_user(ALICE, games=[(1, "A"), (2, "B")])
        assert [g["appid"] for g in database.get_shared_games([ALICE])] == [1, 2]

    def test_shared_games_empty(self):
        assert database.get_shared_games([]) == []

    def test_game_ownership(self, make_user):
        make_user(ALICE, games=[(1, "X", 10)])
        make_user(BOB, games=[(1, "X", 0), (2, "Y", 5)])
        assert database.get_game_ownership([ALICE, BOB]) == {
            1: {"name": "X", "playtime": {ALICE: 10, BOB: 0}},
            2: {"name": "Y", "playtime": {BOB: 5}},
        }


# ---------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------

class TestFriends:
    def test_save_replaces_previous_list(self, make_user):
        make_user(ALICE)
        database.save_friends(ALICE, [{"steamid": BOB}, {"steamid": CAROL}])
        database.save_friends(ALICE, [{"steamid": DAVE}, {"steamid": ""}, {}])
        assert [f["steamid"] for f in database.get_saved_friends(ALICE)] == [DAVE]

    def test_friends_are_one_way(self, make_user):
        make_user(ALICE, friends=[BOB])
        assert database.get_saved_friends(BOB) == []

    def test_test_accounts_are_friends_with_everyone(self, make_user, monkeypatch):
        from fake_accounts import test_steam_id
        test1, test2 = test_steam_id(1), test_steam_id(2)
        make_user(ALICE, friends=[BOB])
        make_user(BOB)
        make_user(test1)
        make_user(test2)

        def ids(steam_id):
            return [f["steamid"] for f in database.get_saved_friends(steam_id)]

        # Off without DEV_LOGIN=1.
        assert ids(test1) == []
        assert ids(ALICE) == [BOB]

        monkeypatch.setenv("DEV_LOGIN", "1")
        assert ids(ALICE) == sorted([BOB, test1, test2])
        assert ids(BOB) == sorted([test1, test2])
        assert ids(test1) == sorted([ALICE, BOB, test2])

        # A user who signs up later is included without re-seeding.
        make_user(CAROL)
        assert CAROL in ids(test1)
        assert ids(CAROL) == sorted([test1, test2])

    def test_test_friendships_are_not_stored(self, make_user, monkeypatch):
        from fake_accounts import test_steam_id
        make_user(ALICE)
        make_user(test_steam_id(1))
        monkeypatch.setenv("DEV_LOGIN", "1")
        # Steam list + test friends, as the Friends page saves it.
        database.save_friends(ALICE, database.get_saved_friends(ALICE) + [{"steamid": BOB}])
        friend_ids = [f["steamid"] for f in database.get_saved_friends(ALICE)]
        assert friend_ids == sorted([BOB, test_steam_id(1)])   # no duplicates
        with database.get_connection() as connection:
            stored = connection.execute("SELECT friend_steam_id FROM steam_friends").fetchall()
        assert stored == [(BOB,)]

    def test_test_account_list_is_never_saved(self, make_user, monkeypatch):
        """The Friends page saves the list it shows; for a test account that
        would store its worked-out friendships with real users."""
        from fake_accounts import test_steam_id
        test1 = test_steam_id(1)
        make_user(ALICE)
        make_user(test1)
        monkeypatch.setenv("DEV_LOGIN", "1")
        database.save_friends(test1, database.get_saved_friends(test1))
        with database.get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM steam_friends").fetchone()[0] == 0
        monkeypatch.setenv("DEV_LOGIN", "0")
        assert database.get_saved_friends(test1) == []

    def test_only_signed_in_users_are_test_account_friends(self, make_user, monkeypatch):
        from fake_accounts import test_steam_id
        test1 = test_steam_id(1)
        make_user(ALICE)
        make_user(test1)
        # BOB's library was looked up, but BOB never signed in.
        database.save_owned_games(BOB, [], mark_synced=False)
        monkeypatch.setenv("DEV_LOGIN", "1")
        assert [f["steamid"] for f in database.get_saved_friends(test1)] == [ALICE]

    def test_stored_test_friendships_survive_dev_login_off(self, make_user):
        """Rows stored by an older dev_seed.py still show without DEV_LOGIN."""
        from fake_accounts import test_steam_id
        test1 = test_steam_id(1)
        make_user(ALICE)
        with database.get_connection() as connection:
            connection.execute(
                "INSERT INTO steam_friends (steam_id, friend_steam_id) VALUES (?, ?)",
                (ALICE, test1),
            )
        assert [f["steamid"] for f in database.get_saved_friends(ALICE)] == [test1]


# ---------------------------------------------------------------------
# Ignored games and content ratings
# ---------------------------------------------------------------------

class TestIgnoredGames:
    def test_ignore_removes_from_every_library(self, make_user):
        make_user(ALICE, games=[(1, "Bad"), (2, "Good")])
        make_user(BOB, games=[(1, "Bad")])

        name, removed = database.ignore_game(1)

        assert (name, removed) == ("Bad", 2)
        assert [g["appid"] for g in database.get_saved_games(ALICE)] == [2]
        assert database.get_saved_games(BOB) == []
        assert database.list_ignored_games()[0]["app_id"] == 1

    def test_ignored_games_skipped_on_sync(self, make_user):
        database.ignore_game(1, "Bad")
        make_user(ALICE, games=[(1, "Bad"), (2, "Good")])
        assert [g["appid"] for g in database.get_saved_games(ALICE)] == [2]

    def test_unknown_game_gets_placeholder_name(self):
        assert database.ignore_game(555) == ("App 555", 0)

    def test_game_name_kept_if_an_event_uses_it(self, make_user):
        make_user(ALICE, games=[(1, "Bad")])
        event_id = database.save_game_night_event(ALICE, 1, "2099-01-01", "19:00", 60, [])
        database.ignore_game(1)
        assert database.get_event(event_id)["game_name"] == "Bad"

    def test_unignore_and_restore(self, make_user):
        make_user(ALICE, games=[(1, "Bad", 30)])
        database.ignore_game(1)

        assert database.unignore_game(1) is True
        assert database.unignore_game(1) is False
        assert database.restore_game_ownership(1, "Bad", {ALICE: 30}) == 1
        assert database.get_saved_games(ALICE) == [{"appid": 1, "name": "Bad", "playtime_forever": 30}]

    def test_restore_with_no_owners(self):
        assert database.restore_game_ownership(1, "X", {}) == 0

    def test_unrated_games_and_counts(self, make_user):
        make_user(ALICE, games=[(1, "A"), (2, "B"), (3, "C")])
        database.save_content_rating(1, "sfw", [], 0)
        database.save_content_rating(2, "unknown", [], None)

        assert database.list_unrated_games() == [{"appid": 3, "name": "C"}]
        assert database.list_unknown_rated_games() == [{"appid": 2, "name": "B"}]
        assert database.content_rating_counts() == {"sfw": 1, "nsfw": 0, "unknown": 1, "unrated": 1}

    def test_ignored_games_not_unrated(self, make_user):
        make_user(ALICE, games=[(1, "A")])
        database.ignore_game(1)
        assert database.list_unrated_games() == []


# ---------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------

class TestEvents:
    @pytest.fixture
    def event_id(self, make_user):
        make_user(ALICE, games=[(10, "Game")])
        return database.save_game_night_event(ALICE, 10, "2099-01-02", "19:00", 120, [BOB, BOB, CAROL])

    def test_calendar_shows_for_creator_and_participants(self, event_id):
        for person in (ALICE, BOB, CAROL):
            assert [e["event_id"] for e in database.get_calendar_events(person)] == [event_id]
        assert database.get_calendar_events(DAVE) == []

    def test_details_only_for_people_in_it(self, event_id):
        details = database.get_event_details_for_user(event_id, BOB)
        assert details["game_name"] == "Game"
        assert details["participant_steam_ids"] == sorted([BOB, CAROL])
        assert database.get_event_details_for_user(event_id, DAVE) is None

    def test_update_event(self, event_id):
        assert database.update_event(event_id, 10, "2099-02-02", "20:00", 60, [DAVE, DAVE])
        event = database.get_event(event_id)
        assert (event["event_date"], event["event_time"], event["duration_minutes"]) == ("2099-02-02", "20:00", 60)
        assert event["participant_steam_ids"] == [DAVE]

    def test_update_missing_event(self):
        assert database.update_event(999, 10, "2099-01-01", "19:00", 60, []) is False

    def test_delete_event(self, event_id):
        assert database.delete_event(event_id) is True
        assert database.delete_event(event_id) is False
        assert database.get_calendar_events(BOB) == []

    def test_events_for_users_in_range(self, event_id):
        events = database.get_events_for_users([CAROL], "2099-01-01", "2099-01-31")
        assert events[0]["members"] == {ALICE, BOB, CAROL}
        assert database.get_events_for_users([DAVE], "2099-01-01", "2099-01-31") == []
        assert database.get_events_for_users([CAROL], "2099-03-01", "2099-03-31") == []

    def test_list_all_events_newest_first(self, event_id):
        later = database.save_game_night_event(ALICE, 10, "2099-06-01", "19:00", 60, [])
        assert [e["event_id"] for e in database.list_all_events()] == [later, event_id]


# ---------------------------------------------------------------------
# Direct messages
# ---------------------------------------------------------------------

class TestMessages:
    def test_conversation_both_directions_in_order(self):
        database.save_message(ALICE, BOB, "hi")
        database.save_message(BOB, ALICE, "hey")
        database.save_message(ALICE, CAROL, "other chat")

        bodies = [m["body"] for m in database.get_conversation(ALICE, BOB)]
        assert bodies == ["hi", "hey"]
        assert [m["body"] for m in database.get_conversation(BOB, ALICE)] == ["hi", "hey"]

    def test_after_id_for_polling(self):
        first = database.save_message(ALICE, BOB, "1")
        database.save_message(ALICE, BOB, "2")
        assert [m["body"] for m in database.get_conversation(BOB, ALICE, after_id=first["message_id"])] == ["2"]

    def test_limit_keeps_newest(self):
        for i in range(5):
            database.save_message(ALICE, BOB, str(i))
        assert [m["body"] for m in database.get_conversation(ALICE, BOB, limit=2)] == ["3", "4"]

    def test_audit_field_only_when_asked(self):
        database.save_message(ALICE, BOB, "as alice", sent_by_steam_id=DAVE)
        assert "sent_by_steam_id" not in database.get_conversation(ALICE, BOB)[0]
        assert database.get_conversation(ALICE, BOB, include_audit=True)[0]["sent_by_steam_id"] == DAVE

    def test_unread_and_mark_read(self):
        database.save_message(ALICE, BOB, "1")
        database.save_message(ALICE, BOB, "2")
        database.save_message(BOB, ALICE, "reply")

        assert database.get_unread_message_count(BOB) == 2
        assert database.count_unread_from(ALICE, BOB) == 2

        database.mark_conversation_read(BOB, ALICE)
        assert database.get_unread_message_count(BOB) == 0
        assert database.get_unread_message_count(ALICE) == 1   # unaffected

    def test_has_messaged_is_directional(self):
        database.save_message(ALICE, BOB, "hi")
        assert database.has_messaged(ALICE, BOB)
        assert not database.has_messaged(BOB, ALICE)

    def test_conversation_summaries(self):
        database.save_message(ALICE, BOB, "to bob")
        database.save_message(CAROL, ALICE, "from carol")
        database.save_message(BOB, ALICE, "bob reply")

        summaries = database.get_conversation_summaries(ALICE)
        assert [s["steamid"] for s in summaries] == [BOB, CAROL]
        assert summaries[0] == {
            "steamid": BOB, "last_body": "bob reply",
            "last_created_at": summaries[0]["last_created_at"],
            "last_from_me": False, "unread_count": 1,
        }


# ---------------------------------------------------------------------
# Group chats
# ---------------------------------------------------------------------

class TestGroups:
    def test_create_group(self):
        group_id = database.create_group(ALICE, [BOB, CAROL, BOB], "Squad")
        group = database.get_group(group_id)
        assert group["name"] == "Squad"
        assert group["member_ids"] == [ALICE, BOB, CAROL]
        assert database.get_group_messages(group_id)[0]["kind"] == "created"

    def test_size_limit(self, monkeypatch):
        monkeypatch.setattr(database, "MAX_GROUP_MEMBERS", 3)
        group_id = database.create_group(ALICE, [BOB, CAROL, DAVE])
        assert len(database.get_group(group_id)["member_ids"]) == 3
        assert database.add_group_members(group_id, ALICE, ["999"]) == []

    def test_missing_group(self):
        assert database.get_group(123) is None
        assert not database.is_group_member(123, ALICE)

    def test_add_members_skips_existing(self):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        assert database.add_group_members(group_id, ALICE, [BOB, DAVE]) == [DAVE]
        assert database.is_group_member(group_id, DAVE)
        assert database.get_group_messages(group_id)[-1]["body"] == DAVE

    def test_new_member_starts_with_nothing_unread(self):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        database.save_group_message(group_id, ALICE, "before dave")
        database.add_group_members(group_id, ALICE, [DAVE])
        assert database.count_group_unread(group_id, DAVE) == 0

    def test_unread_counts_only_others_messages(self):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        database.save_group_message(group_id, ALICE, "1")
        database.save_group_message(group_id, BOB, "2")
        database.rename_group(group_id, ALICE, "New name")   # notice, not a message

        assert database.count_group_unread(group_id, ALICE) == 1   # sending "1" read up to there; "2" is new
        assert database.count_group_unread(group_id, CAROL) == 2
        assert database.get_unread_message_count(CAROL) == 2

        database.mark_group_read(group_id, CAROL)
        assert database.count_group_unread(group_id, CAROL) == 0

    def test_admin_sending_as_member_does_not_mark_read(self):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        database.save_group_message(group_id, BOB, "from bob")
        database.save_group_message(group_id, ALICE, "as alice", sent_by_steam_id=DAVE)
        assert database.count_group_unread(group_id, ALICE) == 1

    def test_leave_and_delete_when_empty(self):
        group_id = database.create_group(ALICE, [BOB])
        database.leave_group(group_id, BOB)
        assert database.get_group(group_id)["member_ids"] == [ALICE]
        assert database.get_group_messages(group_id)[-1]["kind"] == "left"

        database.leave_group(group_id, ALICE)
        assert database.get_group(group_id) is None
        assert database.get_group_messages(group_id) == []

    def test_leave_when_not_member_does_nothing(self):
        group_id = database.create_group(ALICE, [BOB])
        database.leave_group(group_id, CAROL)
        assert len(database.get_group_messages(group_id)) == 1

    def test_rename_and_clear(self):
        group_id = database.create_group(ALICE, [BOB])
        database.rename_group(group_id, ALICE, "X")
        database.rename_group(group_id, ALICE, None)
        assert database.get_group(group_id)["name"] is None
        assert [m["body"] for m in database.get_group_messages(group_id)][-2:] == ["X", ""]

    def test_group_summaries(self):
        g1 = database.create_group(ALICE, [BOB, CAROL], "First")
        g2 = database.create_group(BOB, [ALICE, DAVE])
        database.save_group_message(g1, BOB, "newest")

        summaries = database.get_group_summaries(ALICE)
        assert [s["group_id"] for s in summaries] == [g1, g2]
        assert summaries[0]["last_body"] == "newest"
        assert summaries[0]["unread_count"] == 1
        assert summaries[1]["member_ids"] == [BOB, ALICE, DAVE]


# ---------------------------------------------------------------------
# Email settings
# ---------------------------------------------------------------------

class TestUserSettings:
    def future(self, minutes=10):
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()

    def test_defaults(self):
        assert database.get_user_settings(ALICE) == {
            "email": None, "notify_messages": True, "pending_email": None,
            "verify_token_expires": None, "verify_sent_at": None,
        }

    def test_confirm_pending_email(self):
        database.set_pending_email(ALICE, "a@example.com", "hash1", self.future())
        assert database.get_user_settings(ALICE)["pending_email"] == "a@example.com"

        assert database.confirm_pending_email("wrong") is None
        assert database.confirm_pending_email("hash1") == {"steam_id": ALICE, "email": "a@example.com"}

        settings = database.get_user_settings(ALICE)
        assert settings["email"] == "a@example.com"
        assert settings["pending_email"] is None
        # A token works only once.
        assert database.confirm_pending_email("hash1") is None

    def test_expired_token_rejected(self):
        database.set_pending_email(ALICE, "a@example.com", "hash1", self.future(-1))
        assert database.confirm_pending_email("hash1") is None

    def test_remove_email(self):
        database.admin_set_email(ALICE, "a@example.com")
        database.remove_user_email(ALICE)
        assert database.get_user_settings(ALICE)["email"] is None

    def test_admin_set_email_clears_pending(self):
        database.set_pending_email(ALICE, "pending@example.com", "h", self.future())
        database.admin_set_email(ALICE, "set@example.com")
        settings = database.get_user_settings(ALICE)
        assert (settings["email"], settings["pending_email"]) == ("set@example.com", None)
        assert database.confirm_pending_email("h") is None

    def test_notify_toggle(self):
        database.set_notify_messages(ALICE, False)
        assert database.get_user_settings(ALICE)["notify_messages"] is False


# ---------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------

class TestAvailability:
    def test_replace_only_touches_given_dates(self):
        database.replace_availability(ALICE, ["2099-01-03", "2099-01-04"], [("2099-01-03", 19), ("2099-01-04", 20)])
        database.replace_availability(ALICE, ["2099-01-03"], [("2099-01-03", 10)])
        assert database.get_availability(ALICE, "2099-01-01", "2099-01-31") == [
            ("2099-01-03", 10), ("2099-01-04", 20),
        ]

    def test_duplicates_ignored(self):
        database.replace_availability(ALICE, ["2099-01-03"], [("2099-01-03", 19)] * 3)
        assert database.get_availability(ALICE, "2099-01-03", "2099-01-03") == [("2099-01-03", 19)]

    def test_for_users_omits_people_without_any(self):
        database.replace_availability(ALICE, ["2099-01-03"], [("2099-01-03", 19)])
        assert database.get_availability_for_users([ALICE, BOB], "2099-01-01", "2099-01-31") == {
            ALICE: {("2099-01-03", 19)}
        }
        assert database.get_availability_for_users([], "2099-01-01", "2099-01-31") == {}
