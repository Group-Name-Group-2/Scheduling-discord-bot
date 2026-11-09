"""Small functions with no database or network: quick to run, easy to read."""

from datetime import date, timedelta

import pytest

import content_filter
import fake_accounts
import notifications
from availability_routes import _format_date, _format_hour_range, _weekend_label, upcoming_weekends
from email_service import app_base_url, is_valid_email
from group_routes import _clean_group_name, _join_names, group_title, message_people_ids, notice_text


# ---------------------------------------------------------------------
# fake_accounts.py
# ---------------------------------------------------------------------

class TestFakeAccounts:
    def test_first_test_id(self):
        assert fake_accounts.test_steam_id(1) == str(fake_accounts.TEST_ID_BASE + 1)

    @pytest.mark.parametrize("number", [0, -1, fake_accounts.TEST_ID_LIMIT + 1])
    def test_out_of_range_numbers_rejected(self, number):
        with pytest.raises(ValueError):
            fake_accounts.test_steam_id(number)

    @pytest.mark.parametrize("number", [1, 2, 50, fake_accounts.TEST_ID_LIMIT])
    def test_generated_ids_are_test_accounts(self, number):
        assert fake_accounts.is_test_account(fake_accounts.test_steam_id(number))

    @pytest.mark.parametrize("steam_id", [
        "76561197960265728",            # smallest real account
        "76561198000000001",
        str(fake_accounts.TEST_ID_BASE),  # the base itself isn't a test ID
        str(fake_accounts.TEST_ID_BASE + fake_accounts.TEST_ID_LIMIT + 1),
        "not a number",
        "",
        None,
    ])
    def test_real_or_invalid_ids_are_not_test_accounts(self, steam_id):
        assert not fake_accounts.is_test_account(steam_id)

    def test_int_ids_work_too(self):
        assert fake_accounts.is_test_account(fake_accounts.TEST_ID_BASE + 5)

    def test_sql_condition_matches_python(self, fresh_database):
        import database

        ids = [fake_accounts.test_steam_id(3), "76561198000000001", "123"]
        with database.get_connection() as connection:
            connection.execute("CREATE TABLE t (steam_id TEXT)")
            connection.executemany("INSERT INTO t VALUES (?)", [(i,) for i in ids])
            rows = connection.execute(
                f"SELECT steam_id FROM t WHERE {fake_accounts.test_id_sql('steam_id')}"
            ).fetchall()

        assert [r[0] for r in rows] == [fake_accounts.test_steam_id(3)]

    @pytest.mark.parametrize("value, expected", [
        ("1", True), (" 1 ", True), ("0", False), ("", False), ("yes", False),
    ])
    def test_dev_login_enabled(self, monkeypatch, value, expected):
        monkeypatch.setenv("DEV_LOGIN", value)
        assert fake_accounts.dev_login_enabled() is expected


# ---------------------------------------------------------------------
# email_service.py
# ---------------------------------------------------------------------

class TestEmailValidation:
    @pytest.mark.parametrize("address", [
        "a@b.co",
        "someone@example.com",
        "first.last+tag@sub.example.org",
    ])
    def test_valid(self, address):
        assert is_valid_email(address)

    @pytest.mark.parametrize("address", [
        "",
        "plainaddress",
        "@example.com",
        "user@",
        "user@nodot",
        "two@@example.com",
        "has space@example.com",
        "line\nbreak@example.com",
        "a" * 250 + "@example.com",   # longer than 254 characters
    ])
    def test_invalid(self, address):
        assert not is_valid_email(address)

    def test_base_url_prefers_env(self, monkeypatch):
        monkeypatch.setenv("APP_BASE_URL", "https://gamenight.example/")
        assert app_base_url("http://fallback") == "https://gamenight.example"

    def test_base_url_falls_back_to_request(self, monkeypatch):
        monkeypatch.delenv("APP_BASE_URL", raising=False)
        assert app_base_url("http://localhost:8000/") == "http://localhost:8000"


class TestSendEmail:
    def test_sends_through_smtp(self, outbox):
        import email_service

        assert email_service.send_email("to@example.com", "Hi", "Body", "<p>Body</p>")
        assert len(outbox) == 1
        assert outbox[0]["To"] == "to@example.com"
        assert outbox[0]["Subject"] == "Hi"

    def test_returns_false_when_smtp_fails(self, outbox):
        outbox.fail = True
        import email_service

        assert email_service.send_email("to@example.com", "Hi", "Body") is False
        assert outbox == []

    def test_prints_instead_when_no_smtp_host(self, monkeypatch, outbox, capsys):
        import email_service

        monkeypatch.delenv("SMTP_HOST")
        assert email_service.send_email("to@example.com", "Hi", "Body")
        assert outbox == []
        assert "to@example.com" in capsys.readouterr().out


# ---------------------------------------------------------------------
# content_filter.classify
# ---------------------------------------------------------------------

class TestContentClassify:
    def test_no_store_data_is_unknown_and_shown(self):
        assert content_filter.classify(None) == ("unknown", None)

    def test_no_store_data_hidden_when_configured(self, monkeypatch):
        monkeypatch.setattr(content_filter, "HIDE_UNKNOWN", True)
        status, reason = content_filter.classify(None)
        assert status == "unknown"
        assert reason.startswith("Auto:")

    @pytest.mark.parametrize("ids", [[], [1], [2], [5], [1, 2, 5]])
    def test_mild_descriptors_are_safe(self, ids):
        assert content_filter.classify({"descriptor_ids": ids, "required_age": 0}) == ("sfw", None)

    @pytest.mark.parametrize("ids, expected_text", [
        ([3], "adult-only sexual content"),
        ([4], "frequent nudity or sexual content"),
        ([1, 4], "frequent nudity or sexual content"),
    ])
    def test_adult_descriptors_hidden(self, ids, expected_text):
        status, reason = content_filter.classify({"descriptor_ids": ids, "required_age": 18})
        assert status == "nsfw"
        assert expected_text in reason

    def test_reason_lists_each_flag_once_in_order(self):
        _, reason = content_filter.classify({"descriptor_ids": [4, 3, 4], "required_age": 18})
        assert reason == "Auto: adult-only sexual content, frequent nudity or sexual content"


# ---------------------------------------------------------------------
# group_routes helpers
# ---------------------------------------------------------------------

PEOPLE = {
    "1": {"name": "Alice"}, "2": {"name": "Bob"}, "3": {"name": "Carol"},
    "4": {"name": "Dave"}, "5": {"name": "Eve"},
}


class TestGroupHelpers:
    @pytest.mark.parametrize("names, expected", [
        ([], ""),
        (["A"], "A"),
        (["A", "B"], "A and B"),
        (["A", "B", "C"], "A, B and C"),
    ])
    def test_join_names(self, names, expected):
        assert _join_names(names) == expected

    def test_title_uses_name_when_set(self):
        assert group_title("Raid night", ["1", "2", "3"], "1", PEOPLE) == "Raid night"

    @pytest.mark.parametrize("members, expected", [
        (["1"], "Just you"),
        (["1", "2"], "Bob"),
        (["1", "2", "3"], "Bob and Carol"),
        (["1", "2", "3", "4"], "Bob, Carol and Dave"),
        (["1", "2", "3", "4", "5"], "Bob, Carol and 2 others"),
    ])
    def test_title_from_other_members(self, members, expected):
        assert group_title(None, members, "1", PEOPLE) == expected

    def test_title_falls_back_to_id_for_unknown_people(self):
        assert group_title(None, ["1", "999"], "1", PEOPLE) == "999"

    @pytest.mark.parametrize("raw, expected", [
        ("  Friday   night  ", "Friday night"),
        ("tab\tand\nnewline", "tab and newline"),
        (None, ""),
        ("", ""),
    ])
    def test_clean_group_name(self, raw, expected):
        assert _clean_group_name(raw) == expected

    def test_message_people_ids(self):
        messages = [
            {"sender_steam_id": "1", "kind": "message", "body": "hi"},
            {"sender_steam_id": "2", "kind": "added", "body": "3,4"},
            {"sender_steam_id": "1", "kind": "message", "body": "x", "sent_by_steam_id": "5"},
        ]
        assert message_people_ids(messages) == ["1", "2", "3", "4", "1", "5"]

    @pytest.mark.parametrize("message, viewer, expected", [
        ({"sender_steam_id": "1", "kind": "created", "body": ""}, "2", "Alice created the group"),
        ({"sender_steam_id": "1", "kind": "created", "body": ""}, "1", "You created the group"),
        ({"sender_steam_id": "1", "kind": "added", "body": "2,3"}, "4", "Alice added Bob and Carol"),
        ({"sender_steam_id": "1", "kind": "added", "body": "2"}, "2", "Alice added you"),
        ({"sender_steam_id": "2", "kind": "left", "body": ""}, "1", "Bob left the group"),
        ({"sender_steam_id": "1", "kind": "renamed", "body": "Squad"}, "2", "Alice named the group “Squad”"),
        ({"sender_steam_id": "1", "kind": "renamed", "body": ""}, "2", "Alice removed the group name"),
        ({"sender_steam_id": "1", "kind": "mystery", "body": ""}, "2", ""),
    ])
    def test_notice_text(self, message, viewer, expected):
        assert notice_text(message, viewer, PEOPLE) == expected


# ---------------------------------------------------------------------
# availability_routes helpers
# ---------------------------------------------------------------------

class TestUpcomingWeekends:
    @pytest.mark.parametrize("today, first_saturday", [
        (date(2026, 11, 2), date(2026, 11, 7)),   # Monday
        (date(2026, 11, 6), date(2026, 11, 7)),   # Friday
        (date(2026, 11, 7), date(2026, 11, 7)),   # Saturday: this weekend
        (date(2026, 11, 8), date(2026, 11, 7)),   # Sunday: weekend still going
    ])
    def test_first_weekend(self, today, first_saturday):
        weekends = upcoming_weekends(today)
        assert weekends[0] == (first_saturday, first_saturday + timedelta(days=1))

    def test_count_and_spacing(self):
        weekends = upcoming_weekends(date(2026, 11, 2), count=6)
        assert len(weekends) == 6
        for saturday, sunday in weekends:
            assert saturday.weekday() == 5
            assert sunday.weekday() == 6
            assert (sunday - saturday).days == 1
        for (a, _), (b, _) in zip(weekends, weekends[1:]):
            assert (b - a).days == 7

    def test_crosses_year_end(self):
        weekends = upcoming_weekends(date(2026, 12, 28), count=2)
        assert weekends[0] == (date(2027, 1, 2), date(2027, 1, 3))

    def test_weekend_label_same_month(self):
        assert _weekend_label(date(2026, 11, 7), date(2026, 11, 8)) == "Sat 7 – Sun 8 Nov"

    def test_weekend_label_across_months(self):
        assert _weekend_label(date(2026, 10, 31), date(2026, 11, 1)) == "Sat 31 Oct – Sun 1 Nov"

    def test_format_date(self):
        assert _format_date("2026-11-07") == "Saturday 7 November"

    @pytest.mark.parametrize("start, minutes, expected", [
        ("19:00", 60, "19:00–20:00"),
        ("19:00", 90, "19:00–20:30"),
        ("22:00", 120, "22:00–00:00"),
    ])
    def test_format_hour_range(self, start, minutes, expected):
        assert _format_hour_range(start, minutes) == expected


# ---------------------------------------------------------------------
# notifications helpers
# ---------------------------------------------------------------------

class TestNotificationHelpers:
    @pytest.mark.parametrize("text, expected", [
        ("Alice", "Alice"),
        ("  Alice \n Smith ", "Alice Smith"),
        ("", "fallback"),
        ("   ", "fallback"),
        ("x" * 100, "x" * 64),
    ])
    def test_one_line(self, text, expected):
        assert notifications._one_line(text, "fallback") == expected

    def test_viewing_expires(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(notifications.time, "monotonic", lambda: now[0])

        notifications.mark_viewing("a", "b")
        assert notifications.is_viewing("a", "b")
        assert not notifications.is_viewing("b", "a")

        now[0] += notifications.ACTIVE_WINDOW_SECONDS + 1
        assert not notifications.is_viewing("a", "b")

    def test_group_key(self):
        assert notifications.group_key(7) == "group:7"
