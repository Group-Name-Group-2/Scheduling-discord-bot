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
                last_synced TEXT
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
        
        """)


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