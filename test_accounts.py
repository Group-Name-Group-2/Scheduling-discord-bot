"""
Fake accounts for testing the site with several users, no real Steam
accounts needed.

Test accounts get Steam IDs from a range no real account can have (real
individual accounts start at 76561197960265728), so they can never clash
with a real user. steam_api.py checks is_test_account() and answers from
the database for them instead of asking Steam, so their names, pictures
and libraries are whatever dev_seed.py stored. They're friends with every
user; database.get_saved_friends adds them, nothing is stored.

Create them with:   .\create-test-users.ps1   (or python dev_seed.py)
Sign in as them at: /dev/login   (needs DEV_LOGIN=1 in .env)
"""

import os

# Test IDs are TEST_ID_BASE + 1 ... TEST_ID_BASE + TEST_ID_LIMIT.
TEST_ID_BASE = 76561190000000000
TEST_ID_LIMIT = 9999


def test_steam_id(number: int) -> str:
    """The steam ID of test account number `number` (1, 2, 3, ...)."""
    if not 1 <= number <= TEST_ID_LIMIT:
        raise ValueError(f"test account number must be 1-{TEST_ID_LIMIT}")
    return str(TEST_ID_BASE + number)


def is_test_account(steam_id) -> bool:
    try:
        value = int(steam_id)
    except (TypeError, ValueError):
        return False
    return TEST_ID_BASE < value <= TEST_ID_BASE + TEST_ID_LIMIT


def test_id_sql(column: str) -> str:
    """SQL condition that is true when `column` holds a test-account ID."""
    return (
        f"(length({column}) = 17 AND CAST({column} AS INTEGER) "
        f"BETWEEN {TEST_ID_BASE + 1} AND {TEST_ID_BASE + TEST_ID_LIMIT})"
    )


def dev_login_enabled() -> bool:
    """The /dev/login page only exists when DEV_LOGIN=1 is in .env."""
    return os.getenv("DEV_LOGIN", "").strip() == "1"
