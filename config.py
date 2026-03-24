"""
Billets & Vinted Monitor MVP - Shared Configuration
Centralized config to avoid circular imports between app.py and scanner.py.
"""

import os
import secrets
import secrets as _secrets
from dotenv import load_dotenv

load_dotenv()

_secret = os.getenv("SECRET_KEY", "")
if not _secret:
    raise RuntimeError("SECRET_KEY env var is required (use: python -c 'import secrets; print(secrets.token_hex(32))')")
SECRET_KEY = _secret.strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
APP_URL = os.getenv("APP_URL", "http://localhost:5050").strip().rstrip("/")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

_admin_raw = os.getenv("ADMIN_EMAILS", "")
if not _admin_raw:
    raise RuntimeError("ADMIN_EMAILS env var is required (comma-separated list)")
ADMIN_EMAILS = [e.strip() for e in _admin_raw.split(",") if e.strip()]

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", _secrets.token_hex(32)).strip()
JWT_EXPIRY_DAYS = 90  # long-lived tokens for mobile

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
