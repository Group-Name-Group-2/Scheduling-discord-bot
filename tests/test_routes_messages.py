"""Direct messages (messages_routes.py) and group chats (group_routes.py)."""

import pytest

import database
from tests.conftest import ALICE, BOB, CAROL, DAVE, EVE


@pytest.fixture
def alice(client, login, make_user, steam):
    """Alice signed in, friends with Bob, Carol and Dave."""
    for person, name in [(ALICE, "Alice"), (BOB, "Bob"), (CAROL, "Carol"), (DAVE, "Dave")]:
        steam.profiles[person] = {"personaname": name}
    make_user(ALICE, friends=[BOB, CAROL, DAVE])
    login(client, ALICE, name="Alice")
    return ALICE


# ---------------------------------------------------------------------
# Signed out
# ---------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/messages", f"/messages/{BOB}", "/messages/groups/new", "/messages/groups/1"])
def test_pages_redirect_when_signed_out(client, path):
    response = client.get(path)
    assert response.status_code == 303 and response.headers["location"] == "/"


@pytest.mark.parametrize("path", [f"/messages/{BOB}", "/messages/groups/1"])
def test_sending_needs_sign_in(client, path):
    assert client.post(path, data={"body": "hi"}).status_code == 401


@pytest.mark.parametrize("path", [f"/api/messages/{BOB}", "/api/groups/1/messages"])
def test_polling_needs_sign_in(client, path):
    assert client.get(path).status_code == 401


def test_unread_count_signed_out_is_zero(client):
    assert client.get("/api/messages-unread-count").json() == {"count": 0}


# ---------------------------------------------------------------------
# Direct messages
# ---------------------------------------------------------------------

class TestDirectMessages:
    def test_inbox_loads(self, client, alice):
        database.save_message(BOB, ALICE, "hello alice")
        database.create_group(CAROL, [ALICE, DAVE], "Squad")
        response = client.get("/messages")
        assert response.status_code == 200
        assert "hello alice" in response.text
        assert "Squad" in response.text

    def test_inbox_survives_steam_down(self, client, alice, steam):
        database.save_message(BOB, ALICE, "hello")
        steam.down = True
        steam_api_cache_clear()
        assert client.get("/messages").status_code == 200

    def test_send_to_friend(self, client, alice):
        response = client.post(f"/messages/{BOB}", data={"body": "  hi bob  "})
        assert response.status_code == 200
        assert response.json()["message"]["body"] == "hi bob"
        assert [m["body"] for m in database.get_conversation(ALICE, BOB)] == ["hi bob"]

    def test_cannot_message_stranger(self, client, alice):
        assert client.post(f"/messages/{EVE}", data={"body": "hi"}).status_code == 403

    def test_can_reply_to_stranger_who_messaged(self, client, alice):
        database.save_message(EVE, ALICE, "hi, I'm not your friend")
        assert client.post(f"/messages/{EVE}", data={"body": "hi back"}).status_code == 200

    @pytest.mark.parametrize("other", ["not-a-number", ALICE])
    def test_cannot_message_invalid_or_self(self, client, alice, other):
        assert client.post(f"/messages/{other}", data={"body": "hi"}).status_code == 403

    @pytest.mark.parametrize("body, error", [
        ("", "can't be empty"),
        ("    ", "can't be empty"),
        ("x" * (database.MAX_MESSAGE_LENGTH + 1), "at most"),
    ])
    def test_bad_bodies(self, client, alice, body, error):
        response = client.post(f"/messages/{BOB}", data={"body": body})
        assert response.status_code == 400 and error in response.json()["error"]

    def test_max_length_ok(self, client, alice):
        body = "x" * database.MAX_MESSAGE_LENGTH
        assert client.post(f"/messages/{BOB}", data={"body": body}).status_code == 200

    def test_conversation_page_marks_read(self, client, alice):
        database.save_message(BOB, ALICE, "unread")
        assert client.get("/api/messages-unread-count").json() == {"count": 1}

        response = client.get(f"/messages/{BOB}")
        assert response.status_code == 200 and "unread" in response.text
        assert client.get("/api/messages-unread-count").json() == {"count": 0}

    def test_conversation_with_stranger_redirects(self, client, alice):
        response = client.get(f"/messages/{EVE}")
        assert response.status_code == 303 and response.headers["location"] == "/messages"

    def test_polling(self, client, alice):
        first = database.save_message(ALICE, BOB, "1")
        database.save_message(BOB, ALICE, "2")
        response = client.get(f"/api/messages/{BOB}", params={"after": first["message_id"]})
        assert [m["body"] for m in response.json()["messages"]] == ["2"]
        assert database.get_unread_message_count(ALICE) == 0

    def test_polling_stranger_404(self, client, alice):
        assert client.get(f"/api/messages/{EVE}").status_code == 404

    def test_recipient_gets_email(self, client, alice, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        client.post(f"/messages/{BOB}", data={"body": "hi"})
        assert [m["To"] for m in outbox] == ["bob@example.com"]
        assert outbox[0]["Subject"].startswith("Alice ")


def steam_api_cache_clear():
    import steam_api
    steam_api._profile_cache.clear()


# ---------------------------------------------------------------------
# Group chats
# ---------------------------------------------------------------------

class TestGroupChats:
    def create(self, client, members, name=""):
        return client.post("/messages/groups/new", data={"member_ids": members, "name": name})

    def test_new_group_page(self, client, alice):
        response = client.get("/messages/groups/new")
        assert response.status_code == 200 and "Carol" in response.text

    def test_create_group(self, client, alice):
        response = self.create(client, [BOB, CAROL], "  Friday   Squad ")
        assert response.status_code == 303
        group_id = int(response.headers["location"].rsplit("/", 1)[1])

        group = database.get_group(group_id)
        assert group["name"] == "Friday Squad"
        assert group["member_ids"] == [ALICE, BOB, CAROL]

    @pytest.mark.parametrize("members, name, error", [
        ([BOB], "", "at least two friends"),
        ([BOB, EVE], "", "at least two friends"),          # Eve isn't a friend, so dropped
        ([BOB, ALICE], "", "at least two friends"),        # yourself doesn't count
        ([BOB, CAROL], "x" * (database.MAX_GROUP_NAME_LENGTH + 1), "at most"),
    ])
    def test_create_group_errors(self, client, alice, members, name, error):
        response = self.create(client, members, name)
        assert response.status_code == 200
        assert error in response.text
        assert database.get_group_summaries(ALICE) == []

    def test_group_page_and_messages(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        database.save_group_message(group_id, BOB, "hey all")

        response = client.get(f"/messages/groups/{group_id}")
        assert response.status_code == 200
        assert "hey all" in response.text
        assert database.count_group_unread(group_id, ALICE) == 0

    def test_non_member_redirected(self, client, alice):
        group_id = database.create_group(BOB, [CAROL, DAVE])
        response = client.get(f"/messages/groups/{group_id}")
        assert response.status_code == 303 and response.headers["location"] == "/messages"
        assert client.post(f"/messages/groups/{group_id}", data={"body": "hi"}).status_code == 403
        assert client.get(f"/api/groups/{group_id}/messages").status_code == 404

    def test_send_and_poll(self, client, alice, outbox):
        database.admin_set_email(BOB, "bob@example.com")
        group_id = database.create_group(ALICE, [BOB, CAROL])

        response = client.post(f"/messages/groups/{group_id}", data={"body": "game tonight?"})
        assert response.status_code == 200
        assert response.json()["message"]["sender_name"] == "Alice"
        assert [m["To"] for m in outbox] == ["bob@example.com"]

        polled = client.get(f"/api/groups/{group_id}/messages").json()["messages"]
        assert polled[0]["text"] == "You created the group"
        assert polled[-1]["body"] == "game tonight?"

    @pytest.mark.parametrize("body", ["", "x" * (database.MAX_MESSAGE_LENGTH + 1)])
    def test_send_bad_body(self, client, alice, body):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        assert client.post(f"/messages/groups/{group_id}", data={"body": body}).status_code == 400

    def test_add_members(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        response = client.post(f"/messages/groups/{group_id}/members", data={"member_ids": [DAVE, EVE]})
        assert response.headers["location"].endswith("status=added")
        assert database.get_group(group_id)["member_ids"] == [ALICE, BOB, CAROL, DAVE]

    def test_add_nobody(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        response = client.post(f"/messages/groups/{group_id}/members", data={"member_ids": [EVE]})
        assert response.headers["location"].endswith("status=none_added")

    def test_add_members_full(self, client, alice, monkeypatch):
        import group_routes
        monkeypatch.setattr(group_routes, "MAX_GROUP_MEMBERS", 3)
        group_id = database.create_group(ALICE, [BOB, CAROL])
        response = client.post(f"/messages/groups/{group_id}/members", data={"member_ids": [DAVE]})
        assert response.headers["location"].endswith("status=full")

    def test_rename(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        response = client.post(f"/messages/groups/{group_id}/rename", data={"name": "New"})
        assert response.headers["location"].endswith("status=renamed")
        assert database.get_group(group_id)["name"] == "New"

    def test_rename_same_name_adds_no_notice(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL], "Same")
        client.post(f"/messages/groups/{group_id}/rename", data={"name": "Same"})
        assert len(database.get_group_messages(group_id)) == 1

    def test_rename_too_long(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        response = client.post(
            f"/messages/groups/{group_id}/rename",
            data={"name": "x" * (database.MAX_GROUP_NAME_LENGTH + 1)},
        )
        assert response.headers["location"].endswith("status=name_long")

    def test_leave(self, client, alice):
        group_id = database.create_group(ALICE, [BOB, CAROL])
        response = client.post(f"/messages/groups/{group_id}/leave")
        assert response.headers["location"] == "/messages"
        assert not database.is_group_member(group_id, ALICE)

    def test_group_route_does_not_clash_with_direct_messages(self, client, alice):
        """/messages/groups/new must not be treated as a DM with user 'groups'."""
        assert client.get("/messages/groups/new").status_code == 200
