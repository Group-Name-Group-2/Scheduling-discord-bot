"""
permissions.py

Admin checks.

- `steam_users.is_admin` in the database says who is *allowed* to be an admin.
- `admin_mode` in the session says whether they've *switched it on* right now
  (Settings > Admin mode), so admins can use the site as a regular user.

Use `admin_mode_active(request)` to guard admin-only features.
"""

from fastapi import Request

from database import is_admin


def admin_mode_active(request: Request) -> bool:
    """True only if the signed-in user is an admin AND has admin mode on."""
    steam_id = request.session.get("steam_id")

    if not steam_id or not request.session.get("admin_mode"):
        return False

    # Re-check the database so removing someone's admin flag takes
    # effect immediately, even if their session still says admin_mode.
    if not is_admin(steam_id):
        request.session.pop("admin_mode", None)
        return False

    return True
