"""
Create (or reset) fake test accounts so you can test with several users.

    python dev_seed.py                  # 6 test users
    python dev_seed.py --count 10       # more test users
    python dev_seed.py --games 20       # about 20 games each (default 14)
    python dev_seed.py --remove         # delete all test accounts and their data

Test accounts are always friends with every user, including ones who sign
up later; that's built into the app (database.get_saved_friends), not
stored by this script.

Safe to run again: it resets the test users' names, profile pictures,
libraries and availability to the same starting data. Then set DEV_LOGIN=1 in .env,
restart the server and go to http://127.0.0.1:8000/dev/login.

Test accounts use Steam IDs no real account can have, and the app never
asks Steam about them (see test_accounts.py).
"""

import argparse
import random
from datetime import date
from pathlib import Path

from database import (
    admin_set_avatar,
    admin_set_display_name,
    get_connection,
    init_db,
    save_owned_games,
)
from availability_routes import upcoming_weekends
from recommender import EARLIEST_HOUR, LATEST_END_HOUR
from test_accounts import TEST_ID_LIMIT, is_test_account, test_id_sql, test_steam_id

NAMES = [
    "Alice", "Bob", "Carmen", "Dev", "Ellie",
    "Finn", "Grace", "Hiro", "Ivy", "Jonah",
]

# Real Steam app IDs, so names and cover images load like normal games.
GAMES = [
    (730, "Counter-Strike 2"),
    (570, "Dota 2"),
    (440, "Team Fortress 2"),
    (252490, "Rust"),
    (105600, "Terraria"),
    (413150, "Stardew Valley"),
    (892970, "Valheim"),
    (1172470, "Apex Legends"),
    (548430, "Deep Rock Galactic"),
    (945360, "Among Us"),
    (322330, "Don't Starve Together"),
    (381210, "Dead by Daylight"),
    (1086940, "Baldur's Gate 3"),
    (230410, "Warframe"),
    (359550, "Tom Clancy's Rainbow Six Siege"),
    (1245620, "Elden Ring"),
    (271590, "Grand Theft Auto V"),
    (578080, "PUBG: BATTLEGROUNDS"),
    (1938090, "Call of Duty"),
    (2357570, "Overwatch 2"),
    (1085660, "Destiny 2"),
    (444090, "Paladins"),
    (304930, "Unturned"),
    (346110, "ARK: Survival Evolved"),
    (526870, "Satisfactory"),
    (427520, "Factorio"),
    (394360, "Hearts of Iron IV"),
    (289070, "Sid Meier's Civilization VI"),
    (1145360, "Hades"),
    (262060, "Darkest Dungeon"),
    (1966720, "Lethal Company"),
    (739630, "Phasmophobia"),
    (1097150, "Fall Guys"),
    (1599340, "Lost Ark"),
    (391540, "Undertale"),
    (367520, "Hollow Knight"),
]

# Everyone owns these, so every pair of test users has shared games.
COMMON_GAMES = 3
DEFAULT_GAMES_PER_USER = 14

# Profile pictures: a coloured circle with the user's initial, written as
# SVG files the site serves from /static/test-avatars/.
AVATAR_DIR = Path(__file__).resolve().parent / "static" / "test-avatars"
AVATAR_URL = "/static/test-avatars"
AVATAR_COLORS = [
    "#e4572e", "#2e86ab", "#3bb273", "#a23b72", "#f18f01",
    "#7768ae", "#d64161", "#1b998b", "#c5a100", "#5c6bc0",
]


def write_avatar(steam_id: str, name: str, color: str) -> str:
    """Write a picture for this test user and return its URL."""
    initial = (name.strip()[:1] or "?").upper()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="184" height="184" viewBox="0 0 184 184">'
        f'<rect width="184" height="184" fill="{color}"/>'
        '<text x="50%" y="50%" dy=".35em" text-anchor="middle" fill="#ffffff" '
        'font-family="Inter, Segoe UI, Arial, sans-serif" font-size="96" font-weight="700">'
        f"{initial}</text></svg>"
    )
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    (AVATAR_DIR / f"{steam_id}.svg").write_text(svg, encoding="utf-8")
    return f"{AVATAR_URL}/{steam_id}.svg"


def remove_test_accounts() -> None:
    t = test_id_sql
    with get_connection() as connection:
        connection.execute(f"DELETE FROM event_participants WHERE {t('participant_steam_id')}")
        connection.execute(f"DELETE FROM game_night_events WHERE {t('creator_steam_id')}")
        connection.execute(f"DELETE FROM messages WHERE {t('sender_steam_id')} OR {t('recipient_steam_id')}")
        connection.execute(f"DELETE FROM group_messages WHERE {t('sender_steam_id')}")
        connection.execute(f"DELETE FROM group_members WHERE {t('steam_id')}")
        connection.execute(f"DELETE FROM availability WHERE {t('steam_id')}")
        connection.execute(f"DELETE FROM user_settings WHERE {t('steam_id')}")
        connection.execute(f"DELETE FROM user_games WHERE {t('steam_id')}")
        connection.execute(f"DELETE FROM steam_friends WHERE {t('steam_id')} OR {t('friend_steam_id')}")
        connection.execute(f"DELETE FROM steam_users WHERE {t('steam_id')}")

    for picture in AVATAR_DIR.glob("*.svg"):
        if is_test_account(picture.stem):
            picture.unlink()


def seed(count: int, games_per_user: int = DEFAULT_GAMES_PER_USER) -> list[str]:
    rng = random.Random(42)  # same data every run
    test_ids = [test_steam_id(n) for n in range(1, count + 1)]

    for number, steam_id in enumerate(test_ids, start=1):
        # About games_per_user each (give or take 2), the common ones included.
        total = rng.randint(games_per_user - 2, games_per_user + 2)
        total = max(COMMON_GAMES, min(total, len(GAMES)))
        owned = GAMES[:COMMON_GAMES] + rng.sample(GAMES[COMMON_GAMES:], total - COMMON_GAMES)
        games = [
            {"appid": app_id, "name": name, "playtime_forever": rng.randint(0, 6000)}
            for app_id, name in owned
        ]

        with get_connection() as connection:
            connection.execute(f"DELETE FROM user_games WHERE steam_id = ?", (steam_id,))
        save_owned_games(steam_id, games)

        name = NAMES[number - 1] if number <= len(NAMES) else f"User {number}"
        admin_set_display_name(steam_id, name)
        admin_set_avatar(steam_id, write_avatar(steam_id, name, AVATAR_COLORS[(number - 1) % len(AVATAR_COLORS)]))

    with get_connection() as connection:
        # Friendships stored by older versions of this script; the app now
        # makes test accounts friends with everyone without storing it.
        connection.execute(
            f"DELETE FROM steam_friends WHERE {test_id_sql('steam_id')} "
            f"OR {test_id_sql('friend_steam_id')}"
        )

        # Weekend availability for every weekend the Availability page
        # shows (it only has Saturdays and Sundays), so scheduling and
        # recommended times have something to work with.
        days = [day for weekend in upcoming_weekends(date.today()) for day in weekend]
        connection.execute(
            f"DELETE FROM availability WHERE {test_id_sql('steam_id')}"
        )
        slots = []
        for steam_id in test_ids:
            for day in days:
                if rng.random() < 0.3:
                    continue  # busy that day
                start = rng.randint(max(EARLIEST_HOUR, 10), 20)
                for hour in range(start, min(LATEST_END_HOUR, start + rng.randint(2, 6))):
                    slots.append((steam_id, day.isoformat(), hour))
        connection.executemany(
            "INSERT OR IGNORE INTO availability (steam_id, slot_date, slot_hour) VALUES (?, ?, ?)",
            slots,
        )

    return test_ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=6, help="number of test users (default 6)")
    parser.add_argument("--games", type=int, default=DEFAULT_GAMES_PER_USER,
                        help=f"about how many games each user owns, give or take 2 (default {DEFAULT_GAMES_PER_USER})")
    parser.add_argument("--remove", action="store_true", help="delete all test accounts and their data")
    args = parser.parse_args()

    init_db()

    if args.remove:
        remove_test_accounts()
        print("Removed all test accounts.")
        return

    if not 1 <= args.count <= TEST_ID_LIMIT:
        parser.error(f"--count must be 1-{TEST_ID_LIMIT}")

    if not 1 <= args.games <= len(GAMES):
        parser.error(f"--games must be 1-{len(GAMES)}")

    test_ids = seed(args.count, args.games)

    print(f"Created {len(test_ids)} test accounts ({test_ids[0]} to {test_ids[-1]}).")
    print("They're friends with every user, including anyone who signs up later.")
    print("Set DEV_LOGIN=1 in .env, restart the server, then open http://127.0.0.1:8000/dev/login")


if __name__ == "__main__":
    main()
