"""
Vinted account management routes.

Allows users to connect multiple Vinted accounts via refresh_token_web,
then fetch wallet, messages, favorites, items, and transactions.
"""

import logging
from flask import Blueprint, session, request, jsonify
import database as db
from helpers import login_required
from crypto import encrypt_token, decrypt_token
from vinted_api import vinted_api, VintedAuthError, VintedAPIError

logger = logging.getLogger(__name__)

vinted_accounts_bp = Blueprint("vinted_accounts", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Account management
# ─────────────────────────────────────────────────────────────────────────────

@vinted_accounts_bp.route("/vinted/accounts", methods=["POST"])
@login_required
def add_vinted_account():
    """Connect a new Vinted account via refresh_token_web."""
    user_id = session["user_id"]
    body = request.get_json(silent=True) or {}

    refresh_token = (body.get("refresh_token") or "").strip()
    label = (body.get("label") or "").strip()
    domain = (body.get("domain") or "fr").strip().lower()

    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400
    if domain not in ("fr", "be", "es", "it", "nl", "pl", "pt", "de", "at", "lu", "cz", "sk", "hu", "ro"):
        domain = "fr"

    # Validate token by fetching the user profile
    profile = vinted_api.get_user_profile(refresh_token, domain)
    if "error" in profile:
        code = profile.get("code", "API_ERROR")
        if code == "AUTH_ERROR":
            return jsonify({"error": "Token invalide ou expire. Verifiez le refresh_token_web.", "code": code}), 401
        return jsonify({"error": profile["error"], "code": code}), 502

    vinted_user_id = profile.get("id", "")
    vinted_username = profile.get("username", "")

    if not vinted_user_id:
        return jsonify({"error": "Impossible de recuperer le profil Vinted"}), 502

    # Encrypt before storing
    encrypted_token = encrypt_token(refresh_token)

    account = db.create_vinted_account(
        user_id=user_id,
        label=label,
        refresh_token_encrypted=encrypted_token,
        vinted_user_id=vinted_user_id,
        vinted_username=vinted_username,
        domain=domain,
    )

    return jsonify({
        "id": account["id"],
        "label": account["label"],
        "vinted_username": account["vinted_username"],
        "domain": account["domain"],
        "created_at": account["created_at"],
        "profile": profile,
    }), 201


@vinted_accounts_bp.route("/vinted/accounts", methods=["GET"])
@login_required
def list_vinted_accounts():
    """List all Vinted accounts connected by the user (tokens never exposed)."""
    user_id = session["user_id"]
    accounts = db.get_vinted_accounts(user_id)
    safe = [
        {
            "id": a["id"],
            "label": a.get("label", ""),
            "vinted_username": a.get("vinted_username", ""),
            "vinted_user_id": a.get("vinted_user_id", ""),
            "domain": a.get("domain", "fr"),
            "created_at": a.get("created_at", ""),
        }
        for a in accounts
    ]
    return jsonify({"accounts": safe})


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>", methods=["DELETE"])
@login_required
def remove_vinted_account(account_id: int):
    """Remove a Vinted account."""
    user_id = session["user_id"]
    deleted = db.delete_vinted_account(user_id, account_id)
    if not deleted:
        return jsonify({"error": "Compte introuvable"}), 404
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Data endpoints (proxied through Vinted API)
# ─────────────────────────────────────────────────────────────────────────────

def _get_account_and_token(user_id: int, account_id: int):
    """Helper: fetch account row and return (account, decrypted_token) or raise."""
    account = db.get_vinted_account(user_id, account_id)
    if not account:
        return None, None
    token = decrypt_token(account["refresh_token"])
    return account, token


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/profile", methods=["GET"])
@login_required
def account_profile(account_id: int):
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    data = vinted_api.get_user_profile(token, account.get("domain", "fr"))
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/wallet", methods=["GET"])
@login_required
def account_wallet(account_id: int):
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    data = vinted_api.get_wallet(token, account.get("domain", "fr"))
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/messages", methods=["GET"])
@login_required
def account_messages(account_id: int):
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_conversations(
        token,
        vinted_user_id=account["vinted_user_id"],
        domain=account.get("domain", "fr"),
        page=page,
    )
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/favorites", methods=["GET"])
@login_required
def account_favorites(account_id: int):
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_favorites(
        token,
        vinted_user_id=account["vinted_user_id"],
        domain=account.get("domain", "fr"),
        page=page,
    )
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/items", methods=["GET"])
@login_required
def account_items(account_id: int):
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_user_items(
        token,
        vinted_user_id=account["vinted_user_id"],
        domain=account.get("domain", "fr"),
        page=page,
    )
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/transactions", methods=["GET"])
@login_required
def account_transactions(account_id: int):
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_transactions(token, account.get("domain", "fr"), page=page)
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/purchases", methods=["GET"])
@login_required
def account_purchases(account_id: int):
    """Fetch purchase orders (as buyer)."""
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_purchases(token, account.get("domain", "fr"), page=page)
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/sales", methods=["GET"])
@login_required
def account_sales(account_id: int):
    """Fetch sale orders (as seller)."""
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_sales(token, account.get("domain", "fr"), page=page)
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route("/vinted/accounts/<int:account_id>/notifications", methods=["GET"])
@login_required
def account_notifications(account_id: int):
    """Fetch Vinted notification feed."""
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    page = request.args.get("page", 1, type=int)
    data = vinted_api.get_notifications(token, account.get("domain", "fr"), page=page)
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route(
    "/vinted/accounts/<int:account_id>/transactions/<transaction_id>", methods=["GET"]
)
@login_required
def account_transaction_detail(account_id: int, transaction_id: str):
    """Fetch a single transaction detail."""
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    data = vinted_api.get_transaction_detail(token, transaction_id, account.get("domain", "fr"))
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)


@vinted_accounts_bp.route(
    "/vinted/accounts/<int:account_id>/transactions/<transaction_id>/shipment", methods=["GET"]
)
@login_required
def account_shipment_journey(account_id: int, transaction_id: str):
    """Fetch shipment tracking events for an order."""
    user_id = session["user_id"]
    account, token = _get_account_and_token(user_id, account_id)
    if not account:
        return jsonify({"error": "Compte introuvable"}), 404

    data = vinted_api.get_shipment_journey(token, transaction_id, account.get("domain", "fr"))
    if "error" in data:
        return jsonify(data), 502
    return jsonify(data)
