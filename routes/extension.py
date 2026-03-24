"""
Extension routes v2: JWT authentication, token sync, config, logs.
"""

import logging
from flask import Blueprint, request, jsonify, session
import database as db
from helpers import login_required
from jwt_auth import generate_jwt, mobile_auth_required

logger = logging.getLogger(__name__)

extension_bp = Blueprint("extension", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# AUTH — Generate extension JWT from dashboard session
# ─────────────────────────────────────────────────────────────────────────────

@extension_bp.route("/api/extension/auth/token", methods=["POST"])
@login_required
def ext_auth_token():
    """Generate a JWT for the extension. Called from the connect page."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Only Pro users can use the extension
    if user.get("plan") not in ("pro", "Pro", "PRO"):
        return jsonify({"error": "Extension reserved for Pro plan", "code": "NOT_PRO"}), 403

    token = generate_jwt(user_id)
    return jsonify({
        "token": token,
        "email": user.get("email", ""),
        "name":  user.get("name", ""),
    })


@extension_bp.route("/api/extension/auth/refresh", methods=["POST"])
@mobile_auth_required
def ext_auth_refresh():
    """Refresh an extension JWT."""
    new_token = generate_jwt(request.mobile_user_id)
    return jsonify({"token": new_token})


# ─────────────────────────────────────────────────────────────────────────────
# CONNECT PAGE — Bridge between dashboard and extension
# ─────────────────────────────────────────────────────────────────────────────

@extension_bp.route("/extension/connect")
@login_required
def ext_connect_page():
    """
    Page that the extension opens for one-click auth.
    bridge.js listens for the CustomEvent dispatched here.
    """
    from flask import render_template
    user_id = session["user_id"]
    user    = db.get_user_by_id(user_id)
    return render_template("extension_connect.html", user=user)


# ─────────────────────────────────────────────────────────────────────────────
# VINTED TOKEN SYNC
# ─────────────────────────────────────────────────────────────────────────────

@extension_bp.route("/api/extension/vinted-token", methods=["POST"])
@mobile_auth_required
def ext_vinted_token():
    """Extension posts Vinted CSRF token here."""
    data   = request.get_json(silent=True) or {}
    token  = data.get("token", "").strip()
    domain = data.get("domain", "fr").strip()
    if not token:
        return jsonify({"error": "token required"}), 400
    db.upsert_vinted_session(request.mobile_user_id, token, domain)
    logger.info("Vinted token synced uid=%d domain=%s", request.mobile_user_id, domain)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@extension_bp.route("/api/extension/config")
@mobile_auth_required
def ext_get_config():
    """Extension fetches its config (template, quota, interval)."""
    cfg = db.get_extension_config(request.mobile_user_id)
    cfg.pop("ext_secret", None)
    return jsonify(cfg)


@extension_bp.route("/api/extension/config", methods=["POST"])
@mobile_auth_required
def ext_update_config():
    """Extension updates config."""
    data = request.get_json(silent=True) or {}
    allowed = {
        "ext_msg_enabled":     "msg_enabled",
        "ext_msg_template":    "msg_template",
        "ext_msg_quota_daily": "msg_quota_daily",
        "ext_poll_interval_min": "poll_interval_min",
    }
    update = {db_col: data[k] for db_col, k in allowed.items() if k in data}
    if update:
        db.update_extension_config(request.mobile_user_id, **update)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# LOGS
# ─────────────────────────────────────────────────────────────────────────────

@extension_bp.route("/api/extension/log", methods=["POST"])
@mobile_auth_required
def ext_post_log():
    """Extension posts an activity log entry."""
    data = request.get_json(silent=True) or {}
    db.create_extension_log(
        user_id=request.mobile_user_id,
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
    """Dashboard fetches extension logs."""
    user_id = session["user_id"]
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50
    logs = db.get_extension_logs(user_id, limit=limit)
    return jsonify({"logs": logs})


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD CONFIG (session-authenticated — for the dashboard UI)
# ─────────────────────────────────────────────────────────────────────────────

@extension_bp.route("/api/extension/config/dashboard", methods=["GET", "POST"])
@login_required
def ext_config_dashboard():
    """Dashboard reads/writes extension config via session."""
    user_id = session["user_id"]
    if request.method == "GET":
        cfg  = db.get_extension_config(user_id)
        sess = db.get_vinted_session(user_id)
        cfg["vinted_connected"] = sess is not None
        cfg["vinted_domain"]    = sess["domain"] if sess else None
        cfg["vinted_synced_at"] = sess["synced_at"] if sess else None
        return jsonify(cfg)

    data = request.get_json(silent=True) or {}
    allowed = {
        "ext_msg_enabled":     "msg_enabled",
        "ext_msg_template":    "msg_template",
        "ext_msg_quota_daily": "msg_quota_daily",
        "ext_poll_interval_min": "poll_interval_min",
    }
    update = {db_col: data[k] for db_col, k in allowed.items() if k in data}
    if update:
        db.update_extension_config(user_id, **update)
    return jsonify({"ok": True})
