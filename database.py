import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone

from fake_accounts import dev_login_enabled, is_test_account, test_id_sql

# Store the database in the project root.
DATABASE_PATH = Path(__file__).resolve().parent / "gamenight.db"

# The schema only needs creating/migrating once per process; after that,
# init_db() is a cheap flag check.
_db_initialized = False
_db_init_lock = threading.Lock()


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    global _db_initialized

    if _db_initialized:
        return

    with _db_init_lock:
        if not _db_initialized:
            _create_schema()
            _db_initialized = True


def _create_schema():
    with get_connection() as connection:
        # Default rollback journal, not WAL: measured on Windows, WAL made
        # every write ~1.8x slower, and this app rarely has concurrent
        # writers. Switch to WAL if "database is locked" errors show up.
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS steam_users (
                steam_id TEXT PRIMARY KEY,
                last_synced TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                display_name TEXT,
                avatar_path TEXT
            );

            CREATE TABLE IF NOT EXISTS games (
                app_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );

            -- Games admins have hidden: never shown and never stored in
            -- user_games (or games, unless an existing event needs the name).
            -- No foreign key, so a game can be ignored before anyone owns it.
            CREATE TABLE IF NOT EXISTS ignored_games (
                app_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                ignored_at TEXT NOT NULL,
                reason TEXT              -- NULL = hidden by an admin
            );

            -- Admin-editable settings (key/value); defaults live in code.
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Steam's mature-content flags per game (see content_filter.py).
            -- One row per game ever checked, so each game is looked up once.
            CREATE TABLE IF NOT EXISTS content_ratings (
                app_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,    -- 'sfw', 'nsfw' or 'unknown'
                descriptor_ids TEXT,     -- comma-separated Steam descriptor IDs
                required_age INTEGER,
                checked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_games (
                steam_id TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                playtime_forever INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (steam_id, app_id),

                FOREIGN KEY (steam_id)
                    REFERENCES steam_users(steam_id),

                FOREIGN KEY (app_id)
                    REFERENCES games(app_id)
            );
            
            CREATE TABLE IF NOT EXISTS steam_friends (
                steam_id TEXT NOT NULL,
                friend_steam_id TEXT NOT NULL,
                relationship TEXT NOT NULL DEFAULT 'friend',
                friend_since INTEGER,

                PRIMARY KEY (steam_id, friend_steam_id),

                FOREIGN KEY (steam_id)
                    REFERENCES steam_users(steam_id)
            );
            
            CREATE TABLE IF NOT EXISTS game_night_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_steam_id TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL DEFAULT 120,
                created_at TEXT NOT NULL,
            
                FOREIGN KEY (creator_steam_id)
                    REFERENCES steam_users(steam_id),
            
                FOREIGN KEY (app_id)
                    REFERENCES games(app_id)
            );
            
            CREATE TABLE IF NOT EXISTS event_participants (
                event_id INTEGER NOT NULL,
                participant_steam_id TEXT NOT NULL,
            
                PRIMARY KEY (event_id, participant_steam_id),
            
                FOREIGN KEY (event_id)
                    REFERENCES game_night_events(event_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_steam_id TEXT NOT NULL,
                recipient_steam_id TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                -- Set when an admin sent this message as sender_steam_id.
                sent_by_steam_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_pair
                ON messages (sender_steam_id, recipient_steam_id, message_id);

            CREATE INDEX IF NOT EXISTS idx_messages_recipient_unread
                ON messages (recipient_steam_id, read_at);

            -- Group conversations (3+ people). One-to-one chats stay in
            -- the messages table above.
            CREATE TABLE IF NOT EXISTS group_chats (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,                    -- NULL: show member names
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                -- Newest group message this member has seen.
                last_read_id INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (group_id, steam_id),

                FOREIGN KEY (group_id)
                    REFERENCES group_chats(group_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_group_members_user
                ON group_members (steam_id);

            CREATE TABLE IF NOT EXISTS group_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                sender_steam_id TEXT NOT NULL,
                -- 'message' for normal messages; 'created', 'added',
                -- 'left' and 'renamed' for notices shown in the chat.
                kind TEXT NOT NULL DEFAULT 'message',
                -- Message text, the added steam IDs (comma separated)
                -- or the new group name, depending on kind.
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                -- Set when an admin sent this message as sender_steam_id.
                sent_by_steam_id TEXT,

                FOREIGN KEY (group_id)
                    REFERENCES group_chats(group_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_group_messages_group
                ON group_messages (group_id, message_id);

            CREATE TABLE IF NOT EXISTS availability (
                steam_id TEXT NOT NULL,
                slot_date TEXT NOT NULL,      -- YYYY-MM-DD
                slot_hour INTEGER NOT NULL,   -- 0-23: free from HH:00 to HH+1:00

                PRIMARY KEY (steam_id, slot_date, slot_hour)
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                steam_id TEXT PRIMARY KEY,
                email TEXT,
                notify_messages INTEGER NOT NULL DEFAULT 1,
                pending_email TEXT,
                verify_token_hash TEXT,
                verify_token_expires TEXT,
                verify_sent_at TEXT
            );

        """)

        # Databases created before is_admin existed won't get it from
        # CREATE TABLE IF NOT EXISTS, so add the column if it's missing.
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(steam_users)")
        }

        if "is_admin" not in columns:
            connection.execute(
                "ALTER TABLE steam_users "
                "ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )

        # Admin overrides for the Steam display name and avatar, and when
        # the library was last downloaded from Steam (separate from
        # last_synced, which the dashboard shows as the last sign-in).
        for column in ("display_name", "avatar_path", "library_synced_at"):
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE steam_users ADD COLUMN {column} TEXT"
                )

        ignored_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ignored_games)")
        }

        if "reason" not in ignored_columns:
            connection.execute("ALTER TABLE ignored_games ADD COLUMN reason TEXT")

        message_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(messages)")
        }

        if "sent_by_steam_id" not in message_columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN sent_by_steam_id TEXT"
            )


def is_admin(steam_id: str) -> bool:
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT is_admin FROM steam_users WHERE steam_id = ?",
            (steam_id,),
        ).fetchone()

    return bool(row and row[0])


def set_admin(steam_id: str, admin: bool = True) -> bool:
    """Grant or remove admin. Returns False if the user doesn't exist yet."""
    init_db()

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE steam_users SET is_admin = ? WHERE steam_id = ?",
            (1 if admin else 0, steam_id),
        )

    return cursor.rowcount == 1


def save_owned_games(steam_id: str, games: list, mark_synced: bool = True):
    """
    Save a user's Steam library. mark_synced=False leaves last_synced
    alone (the admin "refresh all" uses it so the dashboard's last
    sign-in column isn't overwritten for everyone).
    """
    init_db()

    synced_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        ignored = _ignored_app_ids(connection)

        # Register the user if they do not already exist.
        if mark_synced:
            connection.execute("""
                INSERT INTO steam_users (steam_id, last_synced)
                VALUES (?, ?)
                ON CONFLICT(steam_id)
                DO UPDATE SET last_synced = excluded.last_synced
            """, (steam_id, synced_at))
        else:
            connection.execute(
                "INSERT OR IGNORE INTO steam_users (steam_id) VALUES (?)",
                (steam_id,),
            )

        # Always record when the library itself was downloaded, so later
        # syncs can skip Steam while it's recent (see library_service.py).
        connection.execute(
            "UPDATE steam_users SET library_synced_at = ? WHERE steam_id = ?",
            (synced_at, steam_id),
        )

        kept = [
            game for game in games
            if game.get("appid") is not None and game.get("appid") not in ignored
        ]

        # Save the games themselves.
        connection.executemany("""
            INSERT INTO games (app_id, name)
            VALUES (?, ?)
            ON CONFLICT(app_id)
            DO UPDATE SET name = excluded.name
        """, [
            (game["appid"], game.get("name") or "Unknown Game")
            for game in kept
        ])

        # Save the user's ownership and playtime.
        connection.executemany("""
            INSERT INTO user_games (
                steam_id,
                app_id,
                playtime_forever
            )
            VALUES (?, ?, ?)
            ON CONFLICT(steam_id, app_id)
            DO UPDATE SET
                playtime_forever = excluded.playtime_forever
        """, [
            (steam_id, game["appid"], game.get("playtime_forever", 0))
            for game in kept
        ])


def get_saved_games(steam_id: str):
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT
                games.app_id,
                games.name,
                user_games.playtime_forever
            FROM user_games
            JOIN games ON user_games.app_id = games.app_id
            WHERE user_games.steam_id = ?
            ORDER BY games.name COLLATE NOCASE
        """, (steam_id,)).fetchall()

    return [
        {
            "appid": row[0],
            "name": row[1],
            "playtime_forever": row[2],
        }
        for row in rows
    ]

def get_shared_games(steam_ids: list[str]) -> list[dict]:
    """
    Return games owned by every Steam user in steam_ids.
    """

    if not steam_ids:
        return []

    placeholders = ",".join("?" for _ in steam_ids)

    query = f"""
        SELECT
            g.app_id AS appid,
            g.name,
            MIN(ug.playtime_forever) AS min_playtime
        FROM games g
        JOIN user_games ug ON g.app_id = ug.app_id
        WHERE ug.steam_id IN ({placeholders})
        GROUP BY g.app_id, g.name
        HAVING COUNT(DISTINCT ug.steam_id) = ?
        ORDER BY g.name COLLATE NOCASE
    """

    with get_connection() as connection:
        rows = connection.execute(
            query,
            (*steam_ids, len(set(steam_ids)))
        ).fetchall()

    return [
        {
            "appid": row[0],
            "name": row[1],
            "min_playtime_forever": row[2],
        }
        for row in rows
    ]


def save_friends(steam_id: str, friends: list):
    """
    Replace steam_id's saved friends with the list Steam returned.
    Test-account friendships are never stored (get_saved_friends works
    them out), so a test account's list isn't saved and test accounts
    in `friends` are skipped.
    """
    init_db()

    if is_test_account(steam_id):
        return

    with get_connection() as connection:
        # Replace the previous list only after Steam
        # has successfully returned a valid list.
        connection.execute(
            "DELETE FROM steam_friends WHERE steam_id = ?",
            (steam_id,),
        )

        connection.executemany("""
            INSERT INTO steam_friends (
                steam_id,
                friend_steam_id,
                relationship,
                friend_since
            )
            VALUES (?, ?, ?, ?)
        """, [
            (
                steam_id,
                friend["steamid"],
                friend.get("relationship", "friend"),
                friend.get("friend_since"),
            )
            for friend in friends
            if friend.get("steamid") and not is_test_account(friend["steamid"])
        ])


def get_saved_friends(steam_id: str):
    """
    steam_id's friends: the list saved from Steam, plus, while DEV_LOGIN=1,
    the test-account friendships. Test accounts are friends with every
    other user who has signed in (real or test), and every user is friends
    with every test account. Nothing about this is stored, so users who
    sign up later are included automatically. This is the only place the
    rule lives.
    """
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT friend_steam_id, relationship, friend_since
            FROM steam_friends
            WHERE steam_id = ?
        """, (steam_id,)).fetchall()

        friends = [
            {
                "steamid": row[0],
                "relationship": row[1],
                "friend_since": row[2],
            }
            for row in rows
        ]

        if dev_login_enabled():
            # last_synced is what marks someone as having signed in; rows
            # without it are friends whose libraries were only looked up.
            if is_test_account(steam_id):
                extra = connection.execute(
                    "SELECT steam_id FROM steam_users "
                    "WHERE last_synced IS NOT NULL AND steam_id != ?",
                    (steam_id,),
                ).fetchall()
            else:
                extra = connection.execute(
                    f"SELECT steam_id FROM steam_users "
                    f"WHERE {test_id_sql('steam_id')} AND last_synced IS NOT NULL"
                ).fetchall()

            known = {friend["steamid"] for friend in friends}
            friends += [
                {"steamid": row[0], "relationship": "friend", "friend_since": None}
                for row in extra
                if row[0] not in known
            ]

    return sorted(friends, key=lambda friend: friend["steamid"])

def save_game_night_event(
    creator_steam_id: str,
    app_id: int,
    event_date: str,
    event_time: str,
    duration_minutes: int,
    participant_steam_ids: list[str],
) -> int:
    init_db()

    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO game_night_events (
                creator_steam_id,
                app_id,
                event_date,
                event_time,
                duration_minutes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                creator_steam_id,
                app_id,
                event_date,
                event_time,
                duration_minutes,
                created_at,
            ),
        )

        event_id = cursor.lastrowid

        connection.executemany(
            """
            INSERT INTO event_participants (
                event_id,
                participant_steam_id
            )
            VALUES (?, ?)
            """,
            [(event_id, p) for p in set(participant_steam_ids)],
        )

    return event_id

def get_calendar_events(steam_id: str) -> list[dict]:
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT
                e.event_id,
                e.creator_steam_id,
                e.app_id,
                g.name AS game_name,
                e.event_date,
                e.event_time,
                e.duration_minutes
            FROM game_night_events e
            JOIN games g ON g.app_id = e.app_id
            WHERE
                e.creator_steam_id = ?
                OR EXISTS (
                    SELECT 1
                    FROM event_participants ep
                    WHERE ep.event_id = e.event_id
                    AND ep.participant_steam_id = ?
                )
            ORDER BY e.event_date, e.event_time
            """,
            (steam_id, steam_id),
        ).fetchall()

        return [dict(row) for row in rows]


def get_event_details_for_user(
    event_id: int,
    steam_id: str,
) -> dict | None:
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        event = connection.execute(
            """
            SELECT
                e.event_id,
                e.creator_steam_id,
                e.app_id,
                g.name AS game_name,
                e.event_date,
                e.event_time,
                e.duration_minutes
            FROM game_night_events e
            JOIN games g ON g.app_id = e.app_id
            WHERE
                e.event_id = ?
                AND (
                    e.creator_steam_id = ?
                    OR EXISTS (
                        SELECT 1
                        FROM event_participants ep
                        WHERE ep.event_id = e.event_id
                        AND ep.participant_steam_id = ?
                    )
                )
            """,
            (event_id, steam_id, steam_id),
        ).fetchone()

        if event is None:
            return None

        participants = connection.execute(
            """
            SELECT participant_steam_id
            FROM event_participants
            WHERE event_id = ?
            ORDER BY participant_steam_id
            """,
            (event_id,),
        ).fetchall()

        result = dict(event)
        result["participant_steam_ids"] = [
            row["participant_steam_id"]
            for row in participants
        ]

        return result

# =========================
# Direct messages
# =========================

MAX_MESSAGE_LENGTH = 2000


def _message_row_to_dict(row, include_audit: bool = False) -> dict:
    message = {
        "message_id": row[0],
        "sender_steam_id": row[1],
        "recipient_steam_id": row[2],
        "body": row[3],
        "created_at": row[4],
        "read_at": row[5],
    }

    # Who really sent it is only for admin views, never regular users.
    if include_audit:
        message["sent_by_steam_id"] = row[6]

    return message


def save_message(
    sender_steam_id: str,
    recipient_steam_id: str,
    body: str,
    sent_by_steam_id: str | None = None,
) -> dict:
    """
    Save a message from sender_steam_id. sent_by_steam_id records the
    admin who actually sent it when sending on someone's behalf.
    """
    init_db()

    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO messages (
                sender_steam_id, recipient_steam_id, body, created_at,
                sent_by_steam_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (sender_steam_id, recipient_steam_id, body, created_at, sent_by_steam_id),
        )
        message_id = cursor.lastrowid

    return {
        "message_id": message_id,
        "sender_steam_id": sender_steam_id,
        "recipient_steam_id": recipient_steam_id,
        "body": body,
        "created_at": created_at,
        "read_at": None,
    }


def get_conversation(
    steam_id: str,
    other_steam_id: str,
    after_id: int = 0,
    limit: int = 200,
    include_audit: bool = False,
) -> list[dict]:
    """
    Messages between two users, oldest first. With after_id, only
    messages newer than that ID (used for polling). include_audit adds
    sent_by_steam_id (admin views only).
    """
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT
                    message_id, sender_steam_id, recipient_steam_id,
                    body, created_at, read_at, sent_by_steam_id
                FROM messages
                WHERE (
                    (sender_steam_id = ? AND recipient_steam_id = ?)
                    OR (sender_steam_id = ? AND recipient_steam_id = ?)
                )
                AND message_id > ?
                ORDER BY message_id DESC
                LIMIT ?
            )
            ORDER BY message_id ASC
            """,
            (steam_id, other_steam_id, other_steam_id, steam_id, after_id, limit),
        ).fetchall()

    return [_message_row_to_dict(row, include_audit) for row in rows]


def mark_conversation_read(steam_id: str, other_steam_id: str) -> None:
    """Mark every message other_steam_id sent to steam_id as read."""
    init_db()

    read_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE messages
            SET read_at = ?
            WHERE sender_steam_id = ?
              AND recipient_steam_id = ?
              AND read_at IS NULL
            """,
            (read_at, other_steam_id, steam_id),
        )


def get_conversation_summaries(steam_id: str) -> list[dict]:
    """
    One entry per person steam_id has exchanged messages with:
    the latest message and how many unread messages they sent.
    Most recent conversation first.
    """
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            WITH mine AS (
                SELECT
                    message_id, sender_steam_id, recipient_steam_id,
                    body, created_at, read_at,
                    CASE WHEN sender_steam_id = ?
                         THEN recipient_steam_id
                         ELSE sender_steam_id
                    END AS other_id
                FROM messages
                WHERE sender_steam_id = ? OR recipient_steam_id = ?
            )
            SELECT
                m.other_id,
                m.body,
                m.created_at,
                m.sender_steam_id,
                (
                    SELECT COUNT(*) FROM mine u
                    WHERE u.other_id = m.other_id
                      AND u.recipient_steam_id = ?
                      AND u.read_at IS NULL
                ) AS unread_count
            FROM mine m
            WHERE m.message_id = (
                SELECT MAX(message_id) FROM mine x WHERE x.other_id = m.other_id
            )
            ORDER BY m.message_id DESC
            """,
            (steam_id, steam_id, steam_id, steam_id),
        ).fetchall()

    return [
        {
            "steamid": row[0],
            "last_body": row[1],
            "last_created_at": row[2],
            "last_from_me": row[3] == steam_id,
            "unread_count": row[4],
        }
        for row in rows
    ]


def has_messaged(sender_steam_id: str, recipient_steam_id: str) -> bool:
    """True if sender_steam_id has ever sent recipient_steam_id a message."""
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM messages
            WHERE sender_steam_id = ? AND recipient_steam_id = ?
            LIMIT 1
            """,
            (sender_steam_id, recipient_steam_id),
        ).fetchone()

    return row is not None


def get_unread_message_count(steam_id: str) -> int:
    """Unread direct messages plus unread group messages."""
    init_db()

    with get_connection() as connection:
        direct = connection.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE recipient_steam_id = ? AND read_at IS NULL
            """,
            (steam_id,),
        ).fetchone()[0]

        group = connection.execute(
            """
            SELECT COUNT(*)
            FROM group_members gm
            JOIN group_messages m ON m.group_id = gm.group_id
            WHERE gm.steam_id = ?
              AND m.message_id > gm.last_read_id
              AND m.sender_steam_id != gm.steam_id
              AND m.kind = 'message'
            """,
            (steam_id,),
        ).fetchone()[0]

    return direct + group


# =========================
# Group conversations
# =========================

MAX_GROUP_MEMBERS = 25
MAX_GROUP_NAME_LENGTH = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_group_notice(connection, group_id: int, actor: str, kind: str, body: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO group_messages (group_id, sender_steam_id, kind, body, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (group_id, actor, kind, body, _now()),
    )
    return cursor.lastrowid


def create_group(creator_steam_id: str, member_ids: list[str], name: str | None = None) -> int:
    """Create a group with the creator plus member_ids. Returns its ID."""
    init_db()

    members = list(dict.fromkeys([creator_steam_id, *member_ids]))[:MAX_GROUP_MEMBERS]
    created_at = _now()

    with get_connection() as connection:
        group_id = connection.execute(
            "INSERT INTO group_chats (name, created_by, created_at) VALUES (?, ?, ?)",
            (name, creator_steam_id, created_at),
        ).lastrowid

        notice_id = _add_group_notice(connection, group_id, creator_steam_id, "created", "")

        connection.executemany(
            """
            INSERT INTO group_members (group_id, steam_id, joined_at, last_read_id)
            VALUES (?, ?, ?, ?)
            """,
            [(group_id, member, created_at, notice_id) for member in members],
        )

    return group_id


def get_group(group_id: int) -> dict | None:
    """The group's details and member IDs (in the order they joined)."""
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT group_id, name, created_by, created_at FROM group_chats WHERE group_id = ?",
            (group_id,),
        ).fetchone()

        if row is None:
            return None

        members = connection.execute(
            """
            SELECT steam_id FROM group_members
            WHERE group_id = ?
            ORDER BY joined_at, rowid
            """,
            (group_id,),
        ).fetchall()

    return {
        "group_id": row[0],
        "name": row[1],
        "created_by": row[2],
        "created_at": row[3],
        "member_ids": [m[0] for m in members],
    }


def is_group_member(group_id: int, steam_id: str) -> bool:
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND steam_id = ?",
            (group_id, steam_id),
        ).fetchone()

    return row is not None


def add_group_members(group_id: int, actor_steam_id: str, member_ids: list[str]) -> list[str]:
    """
    Add people to a group (skipping existing members and anyone over the
    size limit). New members can read the history but start with nothing
    unread. Returns the IDs actually added.
    """
    init_db()

    with get_connection() as connection:
        existing = {
            row[0] for row in connection.execute(
                "SELECT steam_id FROM group_members WHERE group_id = ?", (group_id,)
            )
        }

        room = max(MAX_GROUP_MEMBERS - len(existing), 0)
        to_add = [m for m in dict.fromkeys(member_ids) if m not in existing][:room]

        if not to_add:
            return []

        notice_id = _add_group_notice(
            connection, group_id, actor_steam_id, "added", ",".join(to_add)
        )
        joined_at = _now()

        connection.executemany(
            """
            INSERT INTO group_members (group_id, steam_id, joined_at, last_read_id)
            VALUES (?, ?, ?, ?)
            """,
            [(group_id, member, joined_at, notice_id) for member in to_add],
        )

    return to_add


def leave_group(group_id: int, steam_id: str) -> None:
    """Remove a member. The group is deleted when its last member leaves."""
    init_db()

    with get_connection() as connection:
        removed = connection.execute(
            "DELETE FROM group_members WHERE group_id = ? AND steam_id = ?",
            (group_id, steam_id),
        ).rowcount

        if not removed:
            return

        remaining = connection.execute(
            "SELECT COUNT(*) FROM group_members WHERE group_id = ?", (group_id,)
        ).fetchone()[0]

        if remaining:
            _add_group_notice(connection, group_id, steam_id, "left", "")
        else:
            connection.execute("DELETE FROM group_messages WHERE group_id = ?", (group_id,))
            connection.execute("DELETE FROM group_chats WHERE group_id = ?", (group_id,))


def rename_group(group_id: int, actor_steam_id: str, name: str | None) -> None:
    """Set the group's name (None clears it, showing member names)."""
    init_db()

    with get_connection() as connection:
        connection.execute(
            "UPDATE group_chats SET name = ? WHERE group_id = ?", (name, group_id)
        )
        _add_group_notice(connection, group_id, actor_steam_id, "renamed", name or "")


def _group_message_row_to_dict(row, include_audit: bool = False) -> dict:
    message = {
        "message_id": row[0],
        "group_id": row[1],
        "sender_steam_id": row[2],
        "kind": row[3],
        "body": row[4],
        "created_at": row[5],
    }

    # Who really sent it is only for admin views, never regular users.
    if include_audit:
        message["sent_by_steam_id"] = row[6]

    return message


def save_group_message(
    group_id: int,
    sender_steam_id: str,
    body: str,
    sent_by_steam_id: str | None = None,
) -> dict:
    init_db()

    created_at = _now()

    with get_connection() as connection:
        message_id = connection.execute(
            """
            INSERT INTO group_messages (
                group_id, sender_steam_id, kind, body, created_at, sent_by_steam_id
            )
            VALUES (?, ?, 'message', ?, ?, ?)
            """,
            (group_id, sender_steam_id, body, created_at, sent_by_steam_id),
        ).lastrowid

        # Sending counts as having read the conversation, unless an admin
        # sent it on the member's behalf.
        if sent_by_steam_id is None:
            connection.execute(
                """
                UPDATE group_members SET last_read_id = ?
                WHERE group_id = ? AND steam_id = ?
                """,
                (message_id, group_id, sender_steam_id),
            )

    return {
        "message_id": message_id,
        "group_id": group_id,
        "sender_steam_id": sender_steam_id,
        "kind": "message",
        "body": body,
        "created_at": created_at,
    }


def get_group_messages(
    group_id: int,
    after_id: int = 0,
    limit: int = 200,
    include_audit: bool = False,
) -> list[dict]:
    """Messages and notices in a group, oldest first."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT message_id, group_id, sender_steam_id, kind, body,
                       created_at, sent_by_steam_id
                FROM group_messages
                WHERE group_id = ? AND message_id > ?
                ORDER BY message_id DESC
                LIMIT ?
            )
            ORDER BY message_id ASC
            """,
            (group_id, after_id, limit),
        ).fetchall()

    return [_group_message_row_to_dict(row, include_audit) for row in rows]


def mark_group_read(group_id: int, steam_id: str) -> None:
    init_db()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE group_members
            SET last_read_id = COALESCE(
                (SELECT MAX(message_id) FROM group_messages WHERE group_id = ?),
                last_read_id
            )
            WHERE group_id = ? AND steam_id = ?
            """,
            (group_id, group_id, steam_id),
        )


def count_group_unread(group_id: int, steam_id: str) -> int:
    """Unread messages (not notices) from others in a group."""
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM group_members gm
            JOIN group_messages m ON m.group_id = gm.group_id
            WHERE gm.group_id = ? AND gm.steam_id = ?
              AND m.message_id > gm.last_read_id
              AND m.sender_steam_id != gm.steam_id
              AND m.kind = 'message'
            """,
            (group_id, steam_id),
        ).fetchone()

    return row[0]


def get_group_summaries(steam_id: str) -> list[dict]:
    """
    One entry per group steam_id is in: members, the latest message or
    notice, and the unread count. Most recent activity first.
    """
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                g.group_id,
                g.name,
                last.sender_steam_id,
                last.kind,
                last.body,
                last.created_at,
                (
                    SELECT COUNT(*) FROM group_messages u
                    WHERE u.group_id = g.group_id
                      AND u.message_id > gm.last_read_id
                      AND u.sender_steam_id != gm.steam_id
                      AND u.kind = 'message'
                ) AS unread_count,
                (
                    SELECT GROUP_CONCAT(steam_id) FROM (
                        SELECT steam_id FROM group_members
                        WHERE group_id = g.group_id
                        ORDER BY joined_at, rowid
                    )
                ) AS member_ids
            FROM group_members gm
            JOIN group_chats g ON g.group_id = gm.group_id
            JOIN group_messages last ON last.message_id = (
                SELECT MAX(message_id) FROM group_messages WHERE group_id = g.group_id
            )
            WHERE gm.steam_id = ?
            ORDER BY last.message_id DESC
            """,
            (steam_id,),
        ).fetchall()

    return [
        {
            "group_id": row[0],
            "name": row[1],
            "last_sender": row[2],
            "last_kind": row[3],
            "last_body": row[4],
            "last_created_at": row[5],
            "unread_count": row[6],
            "member_ids": row[7].split(",") if row[7] else [],
        }
        for row in rows
    ]


# =========================
# User settings (email + notifications)
# =========================

def get_user_settings(steam_id: str) -> dict:
    """
    Settings for a Steam user. `email` is only set once verified;
    `pending_email` is an address waiting for its verification link.
    """
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT email, notify_messages, pending_email,
                   verify_token_expires, verify_sent_at
            FROM user_settings
            WHERE steam_id = ?
            """,
            (steam_id,),
        ).fetchone()

    if row is None:
        return {
            "email": None,
            "notify_messages": True,
            "pending_email": None,
            "verify_token_expires": None,
            "verify_sent_at": None,
        }

    return {
        "email": row[0],
        "notify_messages": bool(row[1]),
        "pending_email": row[2],
        "verify_token_expires": row[3],
        "verify_sent_at": row[4],
    }


def _ensure_settings_row(connection, steam_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO user_settings (steam_id) VALUES (?)",
        (steam_id,),
    )


def set_pending_email(
    steam_id: str,
    email: str,
    token_hash: str,
    expires_at: str,
) -> None:
    init_db()

    sent_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        _ensure_settings_row(connection, steam_id)
        connection.execute(
            """
            UPDATE user_settings
            SET pending_email = ?,
                verify_token_hash = ?,
                verify_token_expires = ?,
                verify_sent_at = ?
            WHERE steam_id = ?
            """,
            (email, token_hash, expires_at, sent_at, steam_id),
        )


def confirm_pending_email(token_hash: str) -> dict | None:
    """
    If token_hash matches an unexpired verification, make the pending
    address the account's email. Returns {steam_id, email} or None.
    """
    init_db()

    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT steam_id, pending_email
            FROM user_settings
            WHERE verify_token_hash = ?
              AND verify_token_expires > ?
              AND pending_email IS NOT NULL
            """,
            (token_hash, now),
        ).fetchone()

        if row is None:
            return None

        connection.execute(
            """
            UPDATE user_settings
            SET email = pending_email,
                pending_email = NULL,
                verify_token_hash = NULL,
                verify_token_expires = NULL
            WHERE steam_id = ?
            """,
            (row[0],),
        )

    return {"steam_id": row[0], "email": row[1]}


def remove_user_email(steam_id: str) -> None:
    init_db()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE user_settings
            SET email = NULL,
                pending_email = NULL,
                verify_token_hash = NULL,
                verify_token_expires = NULL
            WHERE steam_id = ?
            """,
            (steam_id,),
        )


def set_notify_messages(steam_id: str, enabled: bool) -> None:
    init_db()

    with get_connection() as connection:
        _ensure_settings_row(connection, steam_id)
        connection.execute(
            "UPDATE user_settings SET notify_messages = ? WHERE steam_id = ?",
            (1 if enabled else 0, steam_id),
        )


def count_unread_from(sender_steam_id: str, recipient_steam_id: str) -> int:
    """Unread messages sender_steam_id has sent to recipient_steam_id."""
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE sender_steam_id = ?
              AND recipient_steam_id = ?
              AND read_at IS NULL
            """,
            (sender_steam_id, recipient_steam_id),
        ).fetchone()

    return row[0]


# =========================
# Availability + data for event recommendations
# =========================

def get_availability(steam_id: str, start_date: str, end_date: str) -> list[tuple[str, int]]:
    """(date, hour) slots steam_id is free, for start_date..end_date inclusive."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT slot_date, slot_hour
            FROM availability
            WHERE steam_id = ? AND slot_date BETWEEN ? AND ?
            ORDER BY slot_date, slot_hour
            """,
            (steam_id, start_date, end_date),
        ).fetchall()

    return [(row[0], row[1]) for row in rows]


def replace_availability(
    steam_id: str,
    dates: list[str],
    slots: list[tuple[str, int]],
) -> None:
    """Replace steam_id's availability on `dates` with `slots`."""
    init_db()

    with get_connection() as connection:
        connection.executemany(
            "DELETE FROM availability WHERE steam_id = ? AND slot_date = ?",
            [(steam_id, date) for date in dates],
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO availability (steam_id, slot_date, slot_hour)
            VALUES (?, ?, ?)
            """,
            [(steam_id, date, hour) for date, hour in slots],
        )


def get_availability_for_users(
    steam_ids: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, set[tuple[str, int]]]:
    """steam_id -> set of (date, hour) free slots. Users with none are omitted."""
    init_db()

    if not steam_ids:
        return {}

    placeholders = ",".join("?" for _ in steam_ids)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT steam_id, slot_date, slot_hour
            FROM availability
            WHERE steam_id IN ({placeholders})
              AND slot_date BETWEEN ? AND ?
            """,
            (*steam_ids, start_date, end_date),
        ).fetchall()

    result: dict[str, set[tuple[str, int]]] = {}
    for steam_id, slot_date, slot_hour in rows:
        result.setdefault(steam_id, set()).add((slot_date, slot_hour))

    return result


def get_events_for_users(
    steam_ids: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """
    Events between start_date and end_date that any of steam_ids is in
    (as creator or participant). Each has `members`: everyone in it.
    """
    init_db()

    if not steam_ids:
        return []

    placeholders = ",".join("?" for _ in steam_ids)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                e.event_id, e.event_date, e.event_time, e.duration_minutes,
                e.creator_steam_id,
                GROUP_CONCAT(ep.participant_steam_id)
            FROM game_night_events e
            LEFT JOIN event_participants ep ON ep.event_id = e.event_id
            WHERE e.event_date BETWEEN ? AND ?
            GROUP BY e.event_id
            """,
            (start_date, end_date),
        ).fetchall()

    wanted = set(steam_ids)
    events = []

    for event_id, date, time, duration, creator, participants in rows:
        members = {creator, *(participants.split(",") if participants else [])}

        if members & wanted:
            events.append({
                "event_id": event_id,
                "event_date": date,
                "event_time": time,
                "duration_minutes": duration,
                "members": members,
            })

    return events


def get_game_ownership(steam_ids: list[str]) -> dict[int, dict]:
    """
    app_id -> {"name": str, "playtime": {steam_id: minutes}} for every game
    owned by at least one of steam_ids.
    """
    init_db()

    if not steam_ids:
        return {}

    placeholders = ",".join("?" for _ in steam_ids)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT ug.app_id, g.name, ug.steam_id, ug.playtime_forever
            FROM user_games ug
            JOIN games g ON g.app_id = ug.app_id
            WHERE ug.steam_id IN ({placeholders})
            """,
            tuple(steam_ids),
        ).fetchall()

    games: dict[int, dict] = {}
    for app_id, name, steam_id, playtime in rows:
        game = games.setdefault(app_id, {"name": name, "playtime": {}})
        game["playtime"][steam_id] = playtime or 0

    return games


# =========================
# Admin
# =========================

def list_users() -> list[dict]:
    """Every user who has signed in, with their email and admin status."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                u.steam_id,
                u.last_synced,
                u.is_admin,
                s.email,
                s.pending_email,
                u.display_name,
                u.avatar_path
            FROM steam_users u
            LEFT JOIN user_settings s ON s.steam_id = u.steam_id
            ORDER BY u.last_synced DESC
            """
        ).fetchall()

    return [
        {
            "steam_id": row[0],
            "last_synced": row[1],
            "is_admin": bool(row[2]),
            "email": row[3],
            "pending_email": row[4],
            "has_custom_name": row[5] is not None,
            "has_custom_avatar": row[6] is not None,
        }
        for row in rows
    ]


def user_exists(steam_id: str) -> bool:
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM steam_users WHERE steam_id = ?", (steam_id,)
        ).fetchone()

    return row is not None


def get_profile_overrides(steam_ids: list[str]) -> dict[str, dict]:
    """
    steam_id -> {"display_name", "avatar_path"} for users an admin has
    renamed or given a new picture. Users with no overrides are left out.
    """
    if not steam_ids:
        return {}

    init_db()
    overrides = {}

    with get_connection() as connection:
        ids = list(dict.fromkeys(steam_ids))
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ",".join("?" * len(batch))
            rows = connection.execute(
                f"""
                SELECT steam_id, display_name, avatar_path
                FROM steam_users
                WHERE steam_id IN ({placeholders})
                  AND (display_name IS NOT NULL OR avatar_path IS NOT NULL)
                """,
                batch,
            ).fetchall()

            for steam_id, display_name, avatar_path in rows:
                overrides[steam_id] = {
                    "display_name": display_name,
                    "avatar_path": avatar_path,
                }

    return overrides


def admin_set_display_name(steam_id: str, display_name: str | None) -> None:
    """Override a user's Steam name (None goes back to their Steam name)."""
    init_db()

    with get_connection() as connection:
        connection.execute(
            "UPDATE steam_users SET display_name = ? WHERE steam_id = ?",
            (display_name, steam_id),
        )


def admin_set_avatar(steam_id: str, avatar_path: str | None) -> None:
    """Override a user's Steam avatar (None goes back to their Steam one)."""
    init_db()

    with get_connection() as connection:
        connection.execute(
            "UPDATE steam_users SET avatar_path = ? WHERE steam_id = ?",
            (avatar_path, steam_id),
        )


def admin_set_email(steam_id: str, email: str | None) -> None:
    """
    Set (already verified) or remove a user's email, skipping the
    verification link. Clears any pending verification.
    """
    init_db()

    with get_connection() as connection:
        _ensure_settings_row(connection, steam_id)
        connection.execute(
            """
            UPDATE user_settings
            SET email = ?,
                pending_email = NULL,
                verify_token_hash = NULL,
                verify_token_expires = NULL
            WHERE steam_id = ?
            """,
            (email, steam_id),
        )


def get_setting(key: str) -> str | None:
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()

    return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    init_db()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_library_synced_at(steam_id: str) -> datetime | None:
    """When steam_id's library was last downloaded from Steam (UTC)."""
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT library_synced_at FROM steam_users WHERE steam_id = ?",
            (steam_id,),
        ).fetchone()

    if not row or not row[0]:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def mark_signed_in(steam_id: str) -> None:
    """Update last_synced (the dashboard's last sign-in) without a library sync."""
    init_db()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO steam_users (steam_id, last_synced) VALUES (?, ?)
            ON CONFLICT(steam_id) DO UPDATE SET last_synced = excluded.last_synced
            """,
            (steam_id, datetime.now(timezone.utc).isoformat()),
        )


def _ignored_app_ids(connection) -> set[int]:
    return {row[0] for row in connection.execute("SELECT app_id FROM ignored_games")}


def list_ignored_games() -> list[dict]:
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT app_id, name, ignored_at, reason FROM ignored_games "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall()

    return [
        {"app_id": row[0], "name": row[1], "ignored_at": row[2], "reason": row[3]}
        for row in rows
    ]


def list_known_games() -> list[dict]:
    """Every game in someone's library, for picking one to ignore."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT g.app_id, g.name
            FROM games g
            WHERE EXISTS (SELECT 1 FROM user_games ug WHERE ug.app_id = g.app_id)
            ORDER BY g.name COLLATE NOCASE
            """
        ).fetchall()

    return [{"appid": row[0], "name": row[1]} for row in rows]


def ignore_game(
    app_id: int,
    name: str | None = None,
    reason: str | None = None,
) -> tuple[str, int]:
    """
    Hide a game everywhere: add it to ignored_games and remove it from
    every user's library. The games row is kept only if an event still
    points at it (so that event keeps its name). Library syncs skip
    ignored games, so it doesn't come back.

    Returns (stored name, number of user libraries it was removed from).
    """
    init_db()

    with get_connection() as connection:
        if not name:
            row = connection.execute(
                "SELECT name FROM games WHERE app_id = ?", (app_id,)
            ).fetchone()
            name = row[0] if row else f"App {app_id}"

        connection.execute(
            """
            INSERT INTO ignored_games (app_id, name, ignored_at, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id) DO UPDATE SET name = excluded.name
            """,
            (app_id, name, datetime.now(timezone.utc).isoformat(), reason),
        )
        # No steam_id filter: this clears the game from every user's library.
        removed = connection.execute(
            "DELETE FROM user_games WHERE app_id = ?", (app_id,)
        ).rowcount
        connection.execute(
            """
            DELETE FROM games
            WHERE app_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM game_night_events e WHERE e.app_id = games.app_id
              )
            """,
            (app_id,),
        )

    return name, removed


def restore_game_ownership(app_id: int, name: str, owners: dict[str, int]) -> int:
    """
    Put an un-hidden game back into the libraries of owners
    ({steam_id: playtime_forever}). Doesn't touch last_synced, since only
    this one game was re-checked. Returns how many libraries got it back.
    """
    init_db()

    if not owners:
        return 0

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO games (app_id, name) VALUES (?, ?)
            ON CONFLICT(app_id) DO UPDATE SET name = excluded.name
            """,
            (app_id, name),
        )
        connection.executemany(
            """
            INSERT INTO user_games (steam_id, app_id, playtime_forever)
            VALUES (?, ?, ?)
            ON CONFLICT(steam_id, app_id)
            DO UPDATE SET playtime_forever = excluded.playtime_forever
            """,
            [(steam_id, app_id, playtime) for steam_id, playtime in owners.items()],
        )

    return len(owners)


def unignore_game(app_id: int) -> bool:
    """Stop ignoring a game. Use restore_game_ownership to re-add it to libraries."""
    init_db()

    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM ignored_games WHERE app_id = ?", (app_id,)
        )

    return cursor.rowcount == 1


def list_unrated_games() -> list[dict]:
    """Owned, non-hidden games that haven't had their content checked yet."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT g.app_id, g.name
            FROM games g
            WHERE EXISTS (SELECT 1 FROM user_games ug WHERE ug.app_id = g.app_id)
              AND NOT EXISTS (SELECT 1 FROM content_ratings r WHERE r.app_id = g.app_id)
              AND NOT EXISTS (SELECT 1 FROM ignored_games i WHERE i.app_id = g.app_id)
            ORDER BY g.app_id
            """
        ).fetchall()

    return [{"appid": row[0], "name": row[1]} for row in rows]


def save_content_rating(
    app_id: int,
    status: str,
    descriptor_ids: list[int],
    required_age: int | None,
) -> None:
    init_db()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO content_ratings
                (app_id, status, descriptor_ids, required_age, checked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(app_id) DO UPDATE SET
                status = excluded.status,
                descriptor_ids = excluded.descriptor_ids,
                required_age = excluded.required_age,
                checked_at = excluded.checked_at
            """,
            (
                app_id,
                status,
                ",".join(str(i) for i in descriptor_ids),
                required_age,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def content_rating_counts() -> dict[str, int]:
    """status -> number of games, plus 'unrated' for games still to check."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) FROM content_ratings GROUP BY status"
        ).fetchall()

    counts = {"sfw": 0, "nsfw": 0, "unknown": 0, **dict(rows)}
    counts["unrated"] = len(list_unrated_games())
    return counts


def list_unknown_rated_games() -> list[dict]:
    """Visible games Steam returned no content data for, for admin review."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT g.app_id, g.name
            FROM content_ratings r
            JOIN games g ON g.app_id = r.app_id
            WHERE r.status = 'unknown'
              AND EXISTS (SELECT 1 FROM user_games ug WHERE ug.app_id = g.app_id)
            ORDER BY g.name COLLATE NOCASE
            """
        ).fetchall()

    return [{"appid": row[0], "name": row[1]} for row in rows]


def _query_events(where: str = "", params: tuple = ()) -> list[dict]:
    """Events (optionally filtered by `where`), newest date first."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                e.event_id, e.creator_steam_id, e.app_id, g.name,
                e.event_date, e.event_time, e.duration_minutes,
                GROUP_CONCAT(ep.participant_steam_id)
            FROM game_night_events e
            LEFT JOIN games g ON g.app_id = e.app_id
            LEFT JOIN event_participants ep ON ep.event_id = e.event_id
            {where}
            GROUP BY e.event_id
            ORDER BY e.event_date DESC, e.event_time DESC
            """,
            params,
        ).fetchall()

    return [
        {
            "event_id": row[0],
            "creator_steam_id": row[1],
            "app_id": row[2],
            "game_name": row[3] or f"App {row[2]}",
            "event_date": row[4],
            "event_time": row[5],
            "duration_minutes": row[6],
            "participant_steam_ids": row[7].split(",") if row[7] else [],
        }
        for row in rows
    ]


def list_all_events() -> list[dict]:
    """Every game night, newest date first, with its participants."""
    return _query_events()


def get_event(event_id: int) -> dict | None:
    """One event with its participants, regardless of who's asking."""
    events = _query_events("WHERE e.event_id = ?", (event_id,))
    return events[0] if events else None


def update_event(
    event_id: int,
    app_id: int,
    event_date: str,
    event_time: str,
    duration_minutes: int,
    participant_steam_ids: list[str],
) -> bool:
    """Replace an event's details and participants. False if it doesn't exist."""
    init_db()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE game_night_events
            SET app_id = ?, event_date = ?, event_time = ?, duration_minutes = ?
            WHERE event_id = ?
            """,
            (app_id, event_date, event_time, duration_minutes, event_id),
        )

        if cursor.rowcount != 1:
            return False

        connection.execute(
            "DELETE FROM event_participants WHERE event_id = ?", (event_id,)
        )
        connection.executemany(
            """
            INSERT INTO event_participants (event_id, participant_steam_id)
            VALUES (?, ?)
            """,
            [(event_id, p) for p in dict.fromkeys(participant_steam_ids)],
        )

    return True


def delete_event(event_id: int) -> bool:
    init_db()

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM event_participants WHERE event_id = ?", (event_id,)
        )
        cursor = connection.execute(
            "DELETE FROM game_night_events WHERE event_id = ?", (event_id,)
        )

    return cursor.rowcount == 1
