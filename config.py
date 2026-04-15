"""
Billets & Vinted Monitor MVP - Shared Configuration
Centralized config to avoid circular imports between app.py and scanner.py.
"""

import os
import secrets
from dotenv import load_dotenv

load_dotenv()

_secret = os.getenv("SECRET_KEY", "")
if not _secret:
    raise RuntimeError("SECRET_KEY env var is required (use: python -c 'import secrets; print(secrets.token_hex(32))')")
SECRET_KEY = _secret
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_URL = os.getenv("APP_URL", "http://localhost:5050")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# JWT auth (mobile API) — falls back to SECRET_KEY if not explicitly set
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "30"))

_admin_raw = os.getenv("ADMIN_EMAILS", "")
if not _admin_raw:
    raise RuntimeError("ADMIN_EMAILS env var is required (comma-separated list)")
ADMIN_EMAILS = [e.strip() for e in _admin_raw.split(",") if e.strip()]

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
