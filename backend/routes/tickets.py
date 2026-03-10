"""
Tickets routes: event listing, stats, event sheet creation.
"""

import logging
from flask import Blueprint, session, request, jsonify
from googleapiclient.discovery import build
import database as db
from helpers import login_required, _parse_price, _get_sheet_data_cached, paginate_list, get_google_credentials

logger = logging.getLogger(__name__)

tickets_bp = Blueprint("tickets", __name__)


@tickets_bp.route("/api/events-list")
@login_required
def events_list():
    """Return distinct events from the tickets Sheet with count and pagination support."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs tickets"}), 403

    mtype = user["monitoring_type"]
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:J")

        if len(rows) < 2:
            return jsonify({
                "success": True,
                "data": [],
                "pagination": {
                    "page": 1,
                    "per_page": 50,
                    "total": 0,
                    "total_pages": 0,
                    "has_next": False,
                    "has_prev": False
                }
            })

        # Column 0 = Evenement
        event_counts = {}
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            event_name = row[0].strip()
            event_counts[event_name] = event_counts.get(event_name, 0) + 1

        events = [{"name": name, "count": count} for name, count in sorted(event_counts.items())]

        # Get pagination parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)

        # Apply pagination
        paginated = paginate_list(events, page, per_page)

        return jsonify({
            "success": True,
            "data": paginated["data"],
            "pagination": paginated["pagination"]
        })
    except Exception as exc:
        logger.error("Failed to load events list: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors du chargement de la liste des evenements"}), 500


@tickets_bp.route("/api/event-stats")
@login_required
def event_stats():
    """Return financial stats for a specific event."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    event_name = request.args.get("event", "").strip()

    if not event_name:
        return jsonify({"success": False, "error": "Parametre event requis"}), 400
    if len(event_name) > 500:
        return jsonify({"success": False, "error": "Nom d'evenement trop long (max 500 car.)"}), 400

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs tickets"}), 403

    mtype = user["monitoring_type"]
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:J")

        if len(rows) < 2:
            return jsonify({"success": True, "event": event_name, "billets": [], "total_achat": 0, "total_revente": 0, "benefice": 0, "roi": "N/A", "count": 0})

        # Columns: 0=Evenement, 1=Categorie, 2=Lieu, 3=Date, 4=Prix Achat, 5=N Commande, 6=Lien, 7=Compte, 8=Prix Vente, 9=Benefice
        billets = []
        total_achat = 0.0
        total_revente = 0.0
        total_benefice = 0.0

        for row in rows[1:]:
            if not row or not row[0]:
                continue
            if row[0].strip().lower() != event_name.lower():
                continue

            prix_achat = _parse_price(row[4] if len(row) > 4 else "")
            prix_vente = _parse_price(row[8] if len(row) > 8 else "")
            benefice = _parse_price(row[9] if len(row) > 9 else "")

            total_achat += prix_achat
            total_revente += prix_vente
            total_benefice += benefice

            billets.append({
                "categorie": row[1].strip() if len(row) > 1 and row[1] else "",
                "lieu": row[2].strip() if len(row) > 2 and row[2] else "",
                "date": row[3].strip() if len(row) > 3 and row[3] else "",
                "prix_achat": prix_achat,
                "prix_vente": prix_vente,
                "benefice": benefice,
                "numero": row[5].strip() if len(row) > 5 and row[5] else "",
                "compte": row[7].strip() if len(row) > 7 and row[7] else "",
            })

        roi = "N/A"
        if total_achat > 0:
            roi = round(((total_revente - total_achat) / total_achat) * 100, 1)

        return jsonify({
            "success": True,
            "event": event_name,
            "billets": billets,
            "total_achat": round(total_achat, 2),
            "total_revente": round(total_revente, 2),
            "benefice": round(total_benefice, 2),
            "roi": roi,
            "count": len(billets),
        })
    except Exception as exc:
        logger.error("Failed to load event stats: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors du chargement des statistiques"}), 500
