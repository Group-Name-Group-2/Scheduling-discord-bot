import sqlite3

DATABASE = "steamgames.db"


def get_connection():
    return sqlite3.connect(DATABASE)


def initialize_database():
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                steam_id TEXT NOT NULL,
                display_name TEXT
            )
        """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS games (
                appid INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS user_games (
                discord_id TEXT NOT NULL,
                appid INTEGER NOT NULL,
                playtime INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (discord_id, appid),

                FOREIGN KEY (discord_id)
                    REFERENCES users(discord_id),

                FOREIGN KEY (appid)
                    REFERENCES games(appid)
            )
        """)

        conn.commit()

    print("Database initialized successfully")


def save_user(discord_id, steam_id, display_name=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (
                discord_id,
                steam_id,
                display_name
            )
            VALUES (?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                steam_id = excluded.steam_id,
                display_name = excluded.display_name
        """, (discord_id, steam_id, display_name))

        conn.commit()


def replace_user_games(discord_id, games):
    with get_connection() as conn:
        cursor = conn.cursor()

        # Remove the user's previous library.
        cursor.execute("""
            DELETE FROM user_games
            WHERE discord_id = ?
        """, (discord_id,))

        for game in games:
            appid = game["appid"]
            name = game.get("name", "Unknown Game")
            playtime = game.get("playtime_forever", 0)

            cursor.execute("""
                INSERT INTO games (appid, name)
                VALUES (?, ?)
                ON CONFLICT(appid) DO UPDATE SET
                    name = excluded.name
            """, (appid, name))

            cursor.execute("""
                INSERT INTO user_games (
                    discord_id,
                    appid,
                    playtime
                )
                VALUES (?, ?, ?)
            """, (discord_id, appid, playtime))

        conn.commit()

    print(
        f"Saved {len(games)} games "
        f"for Discord user {discord_id}"
    )

def get_user_games(discord_id):
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT g.appid, g.name, ug.playtime
            FROM user_games ug
            JOIN games g ON ug.appid = g.appid
            WHERE ug.discord_id = ?
        """, (discord_id,))

        return cursor.fetchall()