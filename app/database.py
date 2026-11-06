import sqlite3
from pathlib import Path
from datetime import datetime, timezone

# Store the database in the project root, next to app/.
DATABASE_PATH = Path(__file__).resolve().parent.parent / "gamenight.db"


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_connection() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS steam_users (
                steam_id TEXT PRIMARY KEY,
                last_synced TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS games (
                app_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
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
                read_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_pair
                ON messages (sender_steam_id, recipient_steam_id, message_id);

            CREATE INDEX IF NOT EXISTS idx_messages_recipient_unread
                ON messages (recipient_steam_id, read_at);

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


def save_owned_games(steam_id: str, games: list):
    init_db()

    synced_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        # Register the user if they do not already exist.
        connection.execute("""
            INSERT INTO steam_users (steam_id, last_synced)
            VALUES (?, ?)
            ON CONFLICT(steam_id)
            DO UPDATE SET last_synced = excluded.last_synced
        """, (steam_id, synced_at))

        for game in games:
            app_id = game.get("appid")
            name = game.get("name") or "Unknown Game"
            playtime = game.get("playtime_forever", 0)

            if app_id is None:
                continue

            # Save the game itself.
            connection.execute("""
                INSERT INTO games (app_id, name)
                VALUES (?, ?)
                ON CONFLICT(app_id)
                DO UPDATE SET name = excluded.name
            """, (app_id, name))

            # Save the user's ownership and playtime.
            connection.execute("""
                INSERT INTO user_games (
                    steam_id,
                    app_id,
                    playtime_forever
                )
                VALUES (?, ?, ?)
                ON CONFLICT(steam_id, app_id)
                DO UPDATE SET
                    playtime_forever = excluded.playtime_forever
            """, (steam_id, app_id, playtime))


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
    init_db()

    with get_connection() as connection:
        # Replace the previous list only after Steam
        # has successfully returned a valid list.
        connection.execute(
            "DELETE FROM steam_friends WHERE steam_id = ?",
            (steam_id,),
        )

        for friend in friends:
            friend_id = friend.get("steamid")

            if not friend_id:
                continue

            connection.execute("""
                INSERT INTO steam_friends (
                    steam_id,
                    friend_steam_id,
                    relationship,
                    friend_since
                )
                VALUES (?, ?, ?, ?)
            """, (
                steam_id,
                friend_id,
                friend.get("relationship", "friend"),
                friend.get("friend_since"),
            ))


def get_saved_friends(steam_id: str):
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT friend_steam_id, relationship, friend_since
            FROM steam_friends
            WHERE steam_id = ?
            ORDER BY friend_steam_id
        """, (steam_id,)).fetchall()

    return [
        {
            "steamid": row[0],
            "relationship": row[1],
            "friend_since": row[2],
        }
        for row in rows
    ]

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

        for participant_id in set(participant_steam_ids):
            connection.execute(
                """
                INSERT INTO event_participants (
                    event_id,
                    participant_steam_id
                )
                VALUES (?, ?)
                """,
                (event_id, participant_id),
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


def _message_row_to_dict(row) -> dict:
    return {
        "message_id": row[0],
        "sender_steam_id": row[1],
        "recipient_steam_id": row[2],
        "body": row[3],
        "created_at": row[4],
        "read_at": row[5],
    }


def save_message(sender_steam_id: str, recipient_steam_id: str, body: str) -> dict:
    init_db()

    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO messages (
                sender_steam_id, recipient_steam_id, body, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (sender_steam_id, recipient_steam_id, body, created_at),
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
) -> list[dict]:
    """
    Messages between two users, oldest first. With after_id, only
    messages newer than that ID (used for polling).
    """
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT
                    message_id, sender_steam_id, recipient_steam_id,
                    body, created_at, read_at
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

    return [_message_row_to_dict(row) for row in rows]


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
    init_db()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE recipient_steam_id = ? AND read_at IS NULL
            """,
            (steam_id,),
        ).fetchone()

    return row[0]


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
                s.pending_email
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


def list_all_events() -> list[dict]:
    """Every game night, newest date first, with its participants."""
    init_db()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                e.event_id, e.creator_steam_id, e.app_id, g.name,
                e.event_date, e.event_time, e.duration_minutes,
                GROUP_CONCAT(ep.participant_steam_id)
            FROM game_night_events e
            LEFT JOIN games g ON g.app_id = e.app_id
            LEFT JOIN event_participants ep ON ep.event_id = e.event_id
            GROUP BY e.event_id
            ORDER BY e.event_date DESC, e.event_time DESC
            """
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


def get_event(event_id: int) -> dict | None:
    """One event with its participants, regardless of who's asking."""
    for event in list_all_events():
        if event["event_id"] == event_id:
            return event
    return None


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
