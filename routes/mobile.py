"""
Mobile API Blueprint — JWT-authenticated endpoints for the React Native app.
All routes prefixed with /api/mobile/
"""
import logging
from flask import Blueprint, request, jsonify
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import database as db
from jwt_auth import mobile_auth_required, generate_jwt
from helpers import build_credentials_from_account

logger = logging.getLogger(__name__)
mobile_bp = Blueprint("mobile", __name__, url_prefix="/api/mobile")


# ── Auth ──────────────────────────────────────────────────────────────────────

@mobile_bp.route("/auth/google", methods=["POST"])
def mobile_auth_google():
    """
    Exchange a Google OAuth access_token (from Expo AuthSession) for a Monitorz JWT.
    Body: { access_token: string, monitoring_type?: string }
    """
    payload = request.get_json(silent=True) or {}
    access_token = (payload.get("access_token") or "").strip()
    monitoring_type = payload.get("monitoring_type", "tickets")
    if monitoring_type not in ("tickets", "vinted"):
        monitoring_type = "tickets"

    if not access_token:
        return jsonify({"success": False, "error": "access_token requis"}), 400

    # Use the access token to get user info from Google
    try:
        creds = Credentials(token=access_token)
        oauth2_service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        user_info = oauth2_service.userinfo().get().execute()
    except Exception as exc:
        logger.error("Google userinfo failed: %s", exc)
        return jsonify({"success": False, "error": "Token Google invalide"}), 401

    email = user_info.get("email", "")
    name = user_info.get("name", email)
    picture = user_info.get("picture", "")

    if not email:
        return jsonify({"success": False, "error": "Email introuvable"}), 400

    # Find or create user
    user = db.get_user_by_email(email)
    is_new_user = False
    if user:
        user_id = user["id"]
        db.update_user(user_id, name=name, picture=picture, monitoring_type=monitoring_type)
    else:
        user_id = db.create_user(email, name, picture, monitoring_type, "starter")
        is_new_user = True
        try:
            db.activate_trial(user_id)
            db.generate_referral_code(user_id)
        except Exception as exc:
            logger.error("Trial setup failed for user id=%d: %s", user_id, exc)

    token = generate_jwt(user_id)
    user = db.get_user_by_id(user_id)

    return jsonify({
        "success": True,
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "plan": user.get("plan", "starter"),
            "monitoring_type": user.get("monitoring_type", "tickets"),
        },
    })


@mobile_bp.route("/auth/me", methods=["GET"])
@mobile_auth_required
def mobile_me():
    """Return current authenticated user info."""
    user = request.mobile_user
    return jsonify({"success": True, "user": {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user.get("picture", ""),
        "plan": user.get("plan", "starter"),
        "monitoring_type": user.get("monitoring_type", "tickets"),
        "is_trial_active": user.get("is_trial_active", False),
        "trial_ends_at": user.get("trial_ends_at"),
    }})


# ── Dashboard ─────────────────────────────────────────────────────────────────

@mobile_bp.route("/dashboard", methods=["GET"])
@mobile_auth_required
def mobile_dashboard():
    """Return all data needed for the mobile dashboard."""
    user_id = request.mobile_user_id
    user = request.mobile_user
    mtype = user.get("monitoring_type", "tickets")

    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    last_scan = db.get_last_scan(user_id, monitoring_type=mtype)
    orders_count = db.get_processed_orders_count(user_id, monitoring_type=mtype)
    unread_count = db.get_unread_notification_count(user_id, monitoring_type=mtype)

    return jsonify({
        "success": True,
        "monitoring_type": mtype,
        "plan": user.get("plan", "starter"),
        "accounts": accounts,
        "sheets": [{"id": s["id"], "name": s.get("name", ""), "spreadsheet_id": s["spreadsheet_id"]} for s in sheets],
        "last_scan": last_scan,
        "orders_count": orders_count,
        "unread_count": unread_count,
    })


# ── Scan ──────────────────────────────────────────────────────────────────────

@mobile_bp.route("/scan", methods=["POST"])
@mobile_auth_required
def mobile_scan():
    """Trigger a manual scan for the authenticated user."""
    user_id = request.mobile_user_id
    try:
        from scanner import scan_user
        orders_found = scan_user(user_id)
        return jsonify({"success": True, "orders_found": orders_found})
    except Exception as exc:
        logger.error("Mobile scan failed for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Tickets ───────────────────────────────────────────────────────────────────

@mobile_bp.route("/tickets", methods=["GET"])
@mobile_auth_required
def mobile_tickets():
    """Return recent ticket orders."""
    user_id = request.mobile_user_id
    limit = int(request.args.get("limit", 50))
    try:
        orders = db.get_processed_orders(user_id, monitoring_type="tickets", limit=limit)
        return jsonify({"success": True, "orders": orders})
    except Exception as exc:
        logger.error("Mobile tickets failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@mobile_bp.route("/tickets/<int:order_id>/wts", methods=["POST"])
@mobile_auth_required
def mobile_generate_wts(order_id: int):
    """Generate WTS template for a ticket order."""
    user_id = request.mobile_user_id
    payload = request.get_json(silent=True) or {}
    try:
        order = db.get_processed_order_by_id(order_id)
        if not order or order.get("user_id") != user_id:
            return jsonify({"success": False, "error": "Ordre introuvable"}), 404
        from parsers.wts import generate_wts_text
        text = generate_wts_text(order, template=payload.get("template", "B"))
        return jsonify({"success": True, "text": text})
    except Exception as exc:
        logger.error("Mobile WTS failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Vinted ────────────────────────────────────────────────────────────────────

@mobile_bp.route("/vinted/hashtags", methods=["POST"])
@mobile_auth_required
def mobile_hashtags():
    """Generate hashtags for a Vinted article title."""
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    custom_tags = payload.get("custom_tags", [])
    if not title:
        return jsonify({"success": False, "error": "title requis"}), 400
    try:
        from routes.vinted import generate_hashtags
        tags = generate_hashtags(title, custom_tags)
        return jsonify({"success": True, "hashtags": tags, "text": " ".join(tags)})
    except Exception as exc:
        logger.error("Mobile hashtags failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@mobile_bp.route("/vinted/orders", methods=["GET"])
@mobile_auth_required
def mobile_vinted_orders():
    """Return Vinted orders."""
    user_id = request.mobile_user_id
    limit = int(request.args.get("limit", 50))
    try:
        orders = db.get_processed_orders(user_id, monitoring_type="vinted", limit=limit)
        return jsonify({"success": True, "orders": orders})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Gmail accounts ────────────────────────────────────────────────────────────

@mobile_bp.route("/gmail-accounts", methods=["GET"])
@mobile_auth_required
def mobile_gmail_accounts():
    user_id = request.mobile_user_id
    accounts = db.get_gmail_accounts(user_id)
    return jsonify({"success": True, "accounts": accounts})


# ── Sheets ────────────────────────────────────────────────────────────────────

@mobile_bp.route("/sheets", methods=["GET"])
@mobile_auth_required
def mobile_sheets():
    user_id = request.mobile_user_id
    user = request.mobile_user
    mtype = user.get("monitoring_type", "tickets")
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    return jsonify({"success": True, "sheets": sheets})


# ── Notifications ─────────────────────────────────────────────────────────────

@mobile_bp.route("/notifications", methods=["GET"])
@mobile_auth_required
def mobile_notifications():
    user_id = request.mobile_user_id
    user = request.mobile_user
    mtype = user.get("monitoring_type", "tickets")
    limit = int(request.args.get("limit", 30))
    try:
        notifs = db.get_notifications(user_id, monitoring_type=mtype, limit=limit)
        return jsonify({"success": True, "notifications": notifs})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@mobile_bp.route("/notifications/read-all", methods=["POST"])
@mobile_auth_required
def mobile_mark_all_read():
    user_id = request.mobile_user_id
    user = request.mobile_user
    mtype = user.get("monitoring_type", "tickets")
    try:
        db.mark_all_notifications_read(user_id, monitoring_type=mtype)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
