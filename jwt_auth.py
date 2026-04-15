"""JWT authentication helpers for the mobile API."""
import jwt
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify
import database as db
from config import JWT_SECRET_KEY, JWT_EXPIRY_DAYS

logger = logging.getLogger(__name__)


def generate_jwt(user_id: int) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")


def decode_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        logger.warning("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("JWT invalid: %s", e)
        return None


def mobile_auth_required(f):
    """Decorator for mobile API routes — verifies Bearer JWT token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "error": "Token manquant"}), 401
        token = auth_header[7:]
        payload = decode_jwt(token)
        if not payload:
            return jsonify({"success": False, "error": "Token invalide ou expire"}), 401
        user = db.get_user_by_id(payload["sub"])
        if not user:
            return jsonify({"success": False, "error": "Utilisateur introuvable"}), 401
        request.mobile_user = user
        request.mobile_user_id = user["id"]
        return f(*args, **kwargs)
    return decorated
