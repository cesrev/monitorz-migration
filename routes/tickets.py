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

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:K")

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

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:K")

        if len(rows) < 2:
            return jsonify({"success": True, "event": event_name, "billets": [], "total_achat": 0, "total_revente": 0, "benefice": 0, "roi": "N/A", "count": 0})

        # Columns: 0=Evenement, 1=Categorie, 2=Lieu, 3=Date, 4=Prix Achat, 5=N Commande, 6=Lien, 7=Compte, 8=Prix Vente, 9=Benefice, 10=PAS
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
            pas = _parse_price(row[10] if len(row) > 10 else "")

            # Fallback server-side benefice si col J vide mais prix_vente renseigne
            if benefice == 0.0 and prix_vente > 0:
                benefice_calc = prix_vente - pas - prix_achat
            else:
                benefice_calc = benefice

            total_achat += prix_achat
            total_revente += prix_vente
            total_benefice += benefice_calc

            billets.append({
                "categorie": row[1].strip() if len(row) > 1 and row[1] else "",
                "lieu": row[2].strip() if len(row) > 2 and row[2] else "",
                "date": row[3].strip() if len(row) > 3 and row[3] else "",
                "prix_achat": prix_achat,
                "prix_vente": prix_vente,
                "benefice": benefice_calc,
                "pas": pas,
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


@tickets_bp.route("/api/external-sources", methods=["GET"])
@login_required
def get_external_sources():
    """Return the list of external Gmail sources configured in the Config sheet."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs tickets"}), 403

    sheets = db.get_spreadsheets(user_id, monitoring_type="tickets")
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Config!A:A",
        ).execute()
        rows = result.get("values", [])
        emails = [row[0].strip() for row in rows[2:] if row and row[0] and "@" in row[0]]

        return jsonify({"success": True, "emails": emails})
    except Exception as exc:
        logger.error("Failed to get external sources: %s", exc)
        return jsonify({"success": True, "emails": []})  # Tab may not exist yet


@tickets_bp.route("/api/external-sources", methods=["POST"])
@login_required
def save_external_sources():
    """Save external Gmail source addresses to the Config sheet.

    Body: {"emails": ["x@gmail.com", "y@gmail.com"]}
    Creates the Config tab if it doesn't exist.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs tickets"}), 403

    data = request.get_json()
    if not data or "emails" not in data:
        return jsonify({"success": False, "error": "Parametre emails requis"}), 400

    emails = [e.strip().lower() for e in data["emails"] if e and "@" in e]

    sheets = db.get_spreadsheets(user_id, monitoring_type="tickets")
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        # Ensure Config tab exists
        spreadsheet_meta = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties.title",
        ).execute()
        existing_tabs = {s["properties"]["title"] for s in spreadsheet_meta.get("sheets", [])}

        if "Config" not in existing_tabs:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": "Config"}}}]},
            ).execute()

        # Clear existing content then write headers + emails
        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range="Config!A:A",
        ).execute()

        rows = [
            ["📧 Sources Externes"],
            ["Email"],
        ] + [[e] for e in emails]

        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Config!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

        return jsonify({"success": True, "saved": len(emails)})
    except Exception as exc:
        logger.error("Failed to save external sources: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors de la sauvegarde"}), 500
