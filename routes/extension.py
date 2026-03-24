"""
Extension routes: authentication, token management, config, logging.
"""

import hashlib
import logging
import secrets as _secrets
import hmac as _hmac
import time as _time
from functools import wraps
from flask import Blueprint, request, jsonify, g, session
import database as db
from helpers import login_required

logger = logging.getLogger(__name__)

extension_bp = Blueprint("extension", __name__)

# Rate limiter for extension auth: {ip: [timestamp, ...]}
_ext_auth_attempts: dict[str, list[float]] = {}
_EXT_AUTH_MAX_ATTEMPTS = 10
_EXT_AUTH_WINDOW_SEC = 60


def _ext_auth(f):
    """Decorator: authenticate extension requests via X-Extension-Secret header."""
    @wraps(f)
    def _inner(*args, **kwargs):
        # --- Rate limiting per IP ---
        ip = request.remote_addr or "unknown"
        now = _time.time()
        window = _ext_auth_attempts.setdefault(ip, [])
        # Prune old entries
        _ext_auth_attempts[ip] = [t for t in window if now - t < _EXT_AUTH_WINDOW_SEC]
        if len(_ext_auth_attempts[ip]) >= _EXT_AUTH_MAX_ATTEMPTS:
            return jsonify({"error": "Too many requests, try again later"}), 429
        _ext_auth_attempts[ip].append(now)

        incoming = request.headers.get("X-Extension-Secret", "")
        if not incoming:
            return jsonify({"error": "Missing X-Extension-Secret"}), 401
        # O(1) lookup via SHA-256 hash, then constant-time verify
        incoming_hash = hashlib.sha256(incoming.encode()).hexdigest()
        sb = db.get_db()
        response = sb.table("users").select("id, ext_secret").eq("ext_secret_hash", incoming_hash).execute()
        matched_user_id = None
        if response.data:
            row = response.data[0]
            if _hmac.compare_digest(row["ext_secret"], incoming):
                matched_user_id = row["id"]

        if matched_user_id is None:
            return jsonify({"error": "Invalid extension secret"}), 403
        g.ext_user_id = matched_user_id
        return f(*args, **kwargs)
    return _inner


@extension_bp.route("/api/extension/secret/generate", methods=["POST"])
@login_required
def ext_generate_secret():
    """Generate (or regenerate) the extension secret for the current user."""
    user_id = session.get("user_id")
    new_secret = _secrets.token_hex(32)
    db.update_extension_config(user_id, ext_secret=new_secret)
    return jsonify({"secret": new_secret})


@extension_bp.route("/api/vinted-token", methods=["POST"])
@_ext_auth
def ext_sync_token():
    """Extension posts the Vinted CSRF token here."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    domain = data.get("domain", "fr").strip()
    if not token:
        return jsonify({"error": "token required"}), 400
    db.upsert_vinted_session(g.ext_user_id, token, domain)
    logger.info("Vinted token synced for user_id=%d domain=%s", g.ext_user_id, domain)
    return jsonify({"ok": True, "domain": domain})


@extension_bp.route("/api/vinted-token/status")
@_ext_auth
def ext_token_status():
    """Extension polls this to know if its token is stored."""
    sess = db.get_vinted_session(g.ext_user_id)
    if not sess:
        return jsonify({"connected": False})
    return jsonify({
        "connected": True,
        "domain": sess["domain"],
        "synced_at": sess["synced_at"],
    })


@extension_bp.route("/api/extension/config")
@_ext_auth
def ext_get_config():
    """Return extension config (template, quota, poll interval)."""
    cfg = db.get_extension_config(g.ext_user_id)
    # Don't return the secret itself
    cfg.pop("ext_secret", None)
    return jsonify(cfg)


@extension_bp.route("/api/extension/config", methods=["POST"])
@_ext_auth
def ext_update_config():
    """Extension updates its own config (msg_enabled, etc.)."""
    data = request.get_json(silent=True) or {}
    allowed = {
        "ext_msg_enabled": "msg_enabled",
        "ext_msg_template": "msg_template",
        "ext_msg_quota_daily": "msg_quota_daily",
        "ext_poll_interval_min": "poll_interval_min",
    }
    update = {}
    for db_col, json_key in allowed.items():
        if json_key in data:
            update[db_col] = data[json_key]
    if update:
        db.update_extension_config(g.ext_user_id, **update)
    return jsonify({"ok": True})


@extension_bp.route("/api/extension/log", methods=["POST"])
@_ext_auth
def ext_post_log():
    """Extension posts an activity log entry."""
    data = request.get_json(silent=True) or {}
    db.create_extension_log(
        user_id=g.ext_user_id,
        action_type=data.get("action_type", "unknown"),
        item_id=data.get("item_id"),
        target_user_id=data.get("target_user_id"),
        status=data.get("status", "ok"),
        error=data.get("error"),
    )
    return jsonify({"ok": True})


@extension_bp.route("/api/extension/logs")
@login_required
def ext_get_logs():
    """Dashboard fetches extension logs for the current user."""
    from flask import session
    user_id = session["user_id"]
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50
    logs = db.get_extension_logs(user_id, limit=limit)
    return jsonify({"logs": logs})


@extension_bp.route("/api/extension/config/dashboard", methods=["GET", "POST"])
@login_required
def ext_config_dashboard():
    """Dashboard reads/writes extension config (authenticated via session)."""
    from flask import session
    user_id = session["user_id"]
    if request.method == "GET":
        cfg = db.get_extension_config(user_id)
        sess = db.get_vinted_session(user_id)
        cfg["vinted_connected"] = sess is not None
        cfg["vinted_domain"] = sess["domain"] if sess else None
        cfg["vinted_synced_at"] = sess["synced_at"] if sess else None
        return jsonify(cfg)

    data = request.get_json(silent=True) or {}
    allowed = {
        "ext_msg_enabled": "msg_enabled",
        "ext_msg_template": "msg_template",
        "ext_msg_quota_daily": "msg_quota_daily",
        "ext_poll_interval_min": "poll_interval_min",
    }
    update = {}
    for db_col, json_key in allowed.items():
        if json_key in data:
            update[db_col] = data[json_key]
    if update:
        db.update_extension_config(user_id, **update)
    return jsonify({"ok": True})
