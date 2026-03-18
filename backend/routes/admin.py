"""
Admin routes: admin panel and client management.
"""

import logging
from flask import Blueprint, session, render_template, jsonify
import database as db
from helpers import admin_required

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin")
@admin_required
def admin_panel():
    """Admin panel showing all clients."""
    users = db.get_all_users()

    clients = []
    total_gmail = 0
    total_orders = 0

    for u in users:
        accounts = db.get_gmail_accounts(u["id"])
        sheets = db.get_spreadsheets(u["id"], monitoring_type=u["monitoring_type"])
        orders = db.get_processed_orders_count(u["id"], monitoring_type=u["monitoring_type"])
        last_scan = db.get_last_scan(u["id"], monitoring_type=u["monitoring_type"])

        total_gmail += len(accounts)
        total_orders += orders

        clients.append({
            "user": u,
            "accounts": accounts,
            "sheets": sheets,
            "orders_count": orders,
            "last_scan": last_scan,
        })

    return render_template(
        "admin.html",
        clients=clients,
        total_clients=len(users),
        total_gmail=total_gmail,
        total_orders=total_orders,
    )


@admin_bp.route("/api/admin/clients")
@admin_required
def admin_clients_api():
    """API endpoint returning all clients data."""
    users = db.get_all_users()

    result = []
    for u in users:
        accounts = db.get_gmail_accounts(u["id"])
        orders = db.get_processed_orders_count(u["id"], monitoring_type=u["monitoring_type"])
        last_scan = db.get_last_scan(u["id"], monitoring_type=u["monitoring_type"])

        result.append({
            "id": u["id"],
            "email": u["email"],
            "name": u["name"],
            "monitoring_type": u["monitoring_type"],
            "plan": u["plan"],
            "created_at": u["created_at"],
            "gmail_accounts": [{"email": a["email"], "is_primary": bool(a["is_primary"])} for a in accounts],
            "orders_count": orders,
            "last_scan": last_scan["scanned_at"] if last_scan else None,
        })

    return jsonify({"success": True, "clients": result})
