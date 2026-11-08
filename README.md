# Scheduling-discord-bot
A discord bot which allows for the coordination of users and finding games to play

### Scrum Roles:
* Scrum Master - Gokul Nair
* Product Owner - Brandon Potter
* Developer - Akhil Bollepally, Tristan Gebeaux, and Zea Hanson

### Instructions


#outdated
```
clone the repository
open terminal and cd to Bot
install requirements.txt
in bot folder create a .env text file.
add your discord bot token
install discord bot to server

```

#run
pip install uvicorn
winget install --id astral-sh.uv -e
restart shell
uv venv
#allow scripts
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
uvicorn main:app --reload
go to 127.0.0.1:8000 in browser

### Test accounts
Test the site with several users without real Steam accounts.

**Setup**

Make sure `.env` contains `DEV_LOGIN=1`, then restart the server.

**Create test users (PowerShell, from the project folder)**
```
.\create-test-users.ps1
```
This creates 8 test users with about 14 games each (12 to 16, with 3 that everyone owns), profile pictures and weekend availability for the next 6 weekends (the weekends the Availability page shows). You don't need to activate the venv first. Running it again resets the test users to the same starting data. While `DEV_LOGIN=1` is set, test users are friends with everyone who has signed in, including anyone who signs up later, so there's no need to re-run it after new sign-ins.

Options:
```
.\create-test-users.ps1 -Count 10 -Games 20   # 10 users, about 20 games each
.\create-test-users.ps1 -Remove               # delete all test users and everything they created
```

If PowerShell says running scripts is disabled, run this once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

The script runs `dev_seed.py`, which you can also run directly with the venv active:
```
python dev_seed.py --count 8 --games 14
python dev_seed.py --remove
```
- `python dev_seed.py` creates 6 test users with about 14 games each.
- `--count <n>` sets how many test users to create (default 6; the PowerShell script uses 8).
- `--games <n>` sets about how many games each test user owns, give or take 2 (default 14).
- `--remove` deletes all test users and everything they created (games, pictures, messages, events, availability).

**Sign in as a test user**
- Open http://127.0.0.1:8000/dev/login and click "Sign in" next to a user. The same page is linked from the bottom of the home page and from the Admin page, where each test user also has a "Sign in as" button.
- Signing in as a test user signs you out of your own account. Sign in with Steam again to come back.
- To be signed in as several users at once, use a different browser or a private window for each, or open one at `127.0.0.1:8000` and another at `localhost:8000`.

Test accounts use Steam IDs no real account can have, and the app never asks Steam about them (see `test_accounts.py`). They show a "Test account" badge on the Admin page. Remove `DEV_LOGIN=1` from `.env` before deploying.
