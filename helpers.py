"""
Shared helper functions and decorators for the Monitorz application.
"""

import re
import time as _time_mod
from functools import wraps
from flask import session, redirect, url_for, flash, jsonify
from config import ADMIN_EMAILS


# ============================================
# GOOGLE SHEETS CACHE
# ============================================

_sheets_cache: dict[str, tuple[float, list]] = {}
_SHEETS_CACHE_TTL = 90  # seconds
_SHEETS_CACHE_MAX = 500  # max entries


def _get_sheet_data_cached(sheets_service, spreadsheet_id: str, range_name: str, user_id: int = 0) -> list:
    """Fetch sheet data with a short-lived in-memory cache to reduce API calls."""
    key = f"{user_id}:{spreadsheet_id}:{range_name}"
    now = _time_mod.time()
    if key in _sheets_cache:
        ts, data = _sheets_cache[key]
        if now - ts < _SHEETS_CACHE_TTL:
            return data
    # Evict expired entries if cache is full
    if len(_sheets_cache) >= _SHEETS_CACHE_MAX:
        expired = [k for k, (ts, _) in _sheets_cache.items() if now - ts >= _SHEETS_CACHE_TTL]
        for k in expired:
            del _sheets_cache[k]
        # If still full after eviction, drop oldest entries
        if len(_sheets_cache) >= _SHEETS_CACHE_MAX:
            oldest = sorted(_sheets_cache, key=lambda k: _sheets_cache[k][0])[:100]
            for k in oldest:
                del _sheets_cache[k]
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_name
    ).execute()
    values = result.get("values", [])
    _sheets_cache[key] = (now, values)
    return values


# ============================================
# DECORATORS
# ============================================

def login_required(f):
    """Decorator that redirects to /login if user is not authenticated."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator that checks if user is admin (verified from DB, not session)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        import database as db
        user = db.get_user_by_id(session["user_id"])
        if not user or user.get("email") not in ADMIN_EMAILS:
            flash("Acces reserve aux administrateurs.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


# ============================================
# UTILITIES
# ============================================

def _parse_price(val):
    """Parse a price string like '12,50€' or '12.50' to float."""
    if not val:
        return 0.0
    cleaned = val.replace("\u20ac", "").replace(",", ".").replace("\u00a0", "").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def paginate_list(items: list, page: int = 1, per_page: int = 50) -> dict:
    """
    Paginate a list of items.

    Args:
        items: List of items to paginate
        page: Page number (1-indexed)
        per_page: Number of items per page

    Returns:
        Dict with paginated data and pagination metadata
    """
    # Ensure valid page and per_page values
    page = max(1, int(page) if isinstance(page, (int, str)) else 1)
    per_page = max(1, min(100, int(per_page) if isinstance(per_page, (int, str)) else 50))

    total = len(items)
    total_pages = (total + per_page - 1) // per_page

    # Adjust page if it exceeds total pages
    page = min(page, max(1, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page

    return {
        "data": items[start_idx:end_idx],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        }
    }


# ============================================
# GOOGLE CREDENTIALS HELPER
# ============================================

def build_credentials_from_account(account: dict):
    """Build Google OAuth Credentials from a gmail_account DB row.

    Refreshes the token if expired and updates the DB accordingly.
    Returns Credentials or None on failure.
    """
    import logging
    import database as _db
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SCOPES

    _logger = logging.getLogger(__name__)

    token = account.get("oauth_token")
    refresh_token = account.get("oauth_refresh_token")

    if not token and not refresh_token:
        _logger.warning("No tokens for gmail_account id=%d", account["id"])
        return None

    creds = Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            expiry_str = creds.expiry.isoformat() if creds.expiry else None
            _db.update_gmail_account_tokens(account["id"], creds.token, expiry_str)
            if creds.refresh_token != refresh_token:
                _db.update_gmail_account_refresh_token(account["id"], creds.refresh_token)
            _logger.info("Refreshed token for gmail_account id=%d", account["id"])
        except Exception as exc:
            _logger.error("Token refresh failed for account id=%d: %s", account["id"], exc)
            return None

    return creds


def get_google_credentials(user_id):
    """Get Google OAuth credentials for a user's primary Gmail account.

    Returns (credentials, primary_account, error_response) where error_response
    is None on success, or a Flask (jsonify, status_code) tuple on failure.
    """
    import database as _db

    accounts = _db.get_gmail_accounts(user_id)
    if not accounts:
        return None, None, (jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400)

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = build_credentials_from_account(primary)
    if not creds:
        return None, None, (jsonify({"success": False, "error": "Erreur d'authentification Google"}), 500)

    return creds, primary, None


# ============================================
# DATE PARSING
# ============================================

def _parse_month_year(d):
    """Parse a date string and return (month, year) tuple or (None, None)."""
    if not d:
        return None, None
    d = d.strip()
    m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", d)
    if m:
        return int(m.group(2)), int(m.group(3))
    m2 = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if m2:
        return int(m2.group(2)), int(m2.group(1))
    return None, None
