# Creates 8 test users with about 14 games each (see dev_seed.py).
#
#   .\create-test-users.ps1                       # 8 users, ~14 games each
#   .\create-test-users.ps1 -Count 10 -Games 20   # different amounts
#   .\create-test-users.ps1 -Remove               # delete all test users
#
# Safe to run again: it resets the test users to the same starting data.
# Then sign in as one at http://127.0.0.1:8000/dev/login (needs DEV_LOGIN=1 in .env).

param(
    [int]$Count = 8,
    [int]$Games = 14,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# Run from the project folder, wherever the script is started from.
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Couldn't find $python. Create the virtual environment first (see README.md)."
}

if ($Remove) {
    & $python dev_seed.py --remove
} else {
    & $python dev_seed.py --count $Count --games $Games
}

exit $LASTEXITCODE
