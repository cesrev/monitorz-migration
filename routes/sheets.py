"""
Google Sheets routes: link, fix formulas, create event sheets.
"""

import logging
import re
from typing import Optional
from flask import Blueprint, session, request, jsonify
from googleapiclient.discovery import build
import database as db
from helpers import login_required, _get_sheet_data_cached, get_google_credentials, build_credentials_from_account

logger = logging.getLogger(__name__)

sheets_bp = Blueprint("sheets", __name__)


def _create_spreadsheet_for_user(user_id: int, monitoring_type: str, plan: str = "starter") -> Optional[dict]:
    """Create a Google Sheet in the user's Drive and register it in DB.

    Uses the primary gmail account's credentials.
    Vinted (starter & pro) and Tickets each get their own column layout with
    pre-seeded formulas for the calculated columns (Bénéfice, ROI %, Temps en stock).
    Returns the spreadsheet dict or None.
    """
    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return None

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = build_credentials_from_account(primary)
    if not creds:
        return None

    sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # ── Column definitions ────────────────────────────────────────────────────
    # Tickets:  A  Événement | B Catégorie | C Lieu | D Date | E Prix Achat
    #           F  N° Commande | G Lien | H Compte | I Prix Vente | J Bénéfice(formula)
    #
    # Vinted (all plans):
    #           A  Article | B Prix Achat | C Date Achat | D Prix Vente | E Date Vente
    #           F  Bénéfice(formula) | G ROI %(formula) | H Temps en stock(formula) | I Compte

    FORMULA_ROWS = 500  # pre-seed this many data rows with formulas

    if monitoring_type == "tickets":
        title   = "Billets Monitor - Commandes"
        headers = [
            "Événement", "Catégorie", "Lieu", "Date", "Prix Achat",
            "N° Commande", "Lien", "Compte", "Prix Vente", "Bénéfice", "ROI %",
        ]
        # J = Bénéfice = Prix Vente (I) - Prix Achat (E)
        # K = ROI % = (I - E) / E
        formula_range  = f"Commandes!J2:K{FORMULA_ROWS + 1}"
        formula_values = [
            [
                f'=IF(OR(E{r}="",I{r}=""),"",I{r}-E{r})',
                f'=IF(OR(E{r}="",I{r}="",E{r}=0),"",(I{r}-E{r})/E{r})',
            ]
            for r in range(2, FORMULA_ROWS + 2)
        ]
        # Column number formats: J = currency, K = percentage
        col_formats = [
            {"col": 9, "pattern": '"€"#,##0.00'},   # J Bénéfice
            {"col": 10, "pattern": '0.0%'},           # K ROI %
        ]
    else:
        # Vinted — same template for starter and pro
        title   = "Vinted Monitor - Achats & Ventes"
        headers = [
            "Article", "Prix Achat", "Date Achat", "Prix Vente", "Date Vente",
            "Bénéfice", "ROI %", "Temps en stock", "Compte",
        ]
        # F2 = ARRAYFORMULA Bénéfice = Prix Vente (D) - Prix Achat (B)
        # G2 = ARRAYFORMULA ROI % = (D - B) / B  (format %)
        # H2 = ARRAYFORMULA Temps en stock
        formula_range  = "Commandes!F2:H2"
        formula_values = [[
            '=ARRAYFORMULA(SI(B2:B="","",D2:D-B2:B))',
            '=ARRAYFORMULA(SI(B2:B="","",(D2:D-B2:B)/B2:B))',
            '=ARRAYFORMULA(SI(C2:C="","",SI(E2:E="",AUJOURDHUI()-C2:C,E2:E-C2:C)))',
        ]]
        # Column number formats
        col_formats = [
            {"col": 5, "pattern": '"€"#,##0.00'},   # F Bénéfice
            {"col": 6, "pattern": '0.0%'},            # G ROI % (format pourcentage)
            {"col": 7, "pattern": '0" j"'},          # H Temps en stock (e.g. "14 j")
        ]

    spreadsheet_body = {
        "properties": {"title": title},
        "sheets": [
            {
                "properties": {"title": "Commandes"},
                "data": [
                    {
                        "startRow": 0,
                        "startColumn": 0,
                        "rowData": [
                            {
                                "values": [
                                    {"userEnteredValue": {"stringValue": col}}
                                    for col in headers
                                ]
                            }
                        ],
                    }
                ],
            }
        ],
    }

    try:
        result = sheets_service.spreadsheets().create(body=spreadsheet_body).execute()
        spreadsheet_id = result["spreadsheetId"]
        sheet_id       = result["sheets"][0]["properties"]["sheetId"]
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"

        # ── 1. Format header row + formula columns ────────────────────────────
        format_requests = [
            # Header row: bold + light grey background
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
                }
            },
        ]
        # Number formats for formula columns
        for fmt in col_formats:
            format_requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": FORMULA_ROWS + 1,
                        "startColumnIndex": fmt["col"],
                        "endColumnIndex":   fmt["col"] + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "NUMBER", "pattern": fmt["pattern"]}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

        try:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": format_requests},
            ).execute()
        except Exception as fmt_exc:
            logger.warning("Failed to format sheet: %s", fmt_exc)

        # ── 2. Pre-seed formula rows ──────────────────────────────────────────
        try:
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=formula_range,
                valueInputOption="USER_ENTERED",
                body={"values": formula_values},
            ).execute()
            logger.info(
                "Pre-seeded %d formula rows in sheet %s (range %s)",
                FORMULA_ROWS, spreadsheet_id, formula_range,
            )
        except Exception as fml_exc:
            logger.warning("Failed to seed formulas: %s", fml_exc)

        row_id = db.create_spreadsheet(
            user_id=user_id,
            spreadsheet_id=spreadsheet_id,
            spreadsheet_url=spreadsheet_url,
            is_auto_created=True,
            monitoring_type=monitoring_type,
        )
        logger.info("Created spreadsheet for user %d type=%s: %s", user_id, monitoring_type, spreadsheet_url)
        return {
            "id": row_id,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": spreadsheet_url,
        }
    except Exception as exc:
        logger.error("Failed to create spreadsheet for user %d: %s", user_id, exc)
        return None


@sheets_bp.route("/api/link-sheet", methods=["POST"])
@login_required
def link_sheet():
    """Link an existing Google Sheet by URL.

    Expects JSON: { "sheet_url": "https://docs.google.com/spreadsheets/d/..." }
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 403
    data = request.get_json(silent=True)

    if not data or not data.get("sheet_url"):
        return jsonify({"success": False, "error": "URL du Sheet requise"}), 400

    sheet_url = data["sheet_url"].strip()

    # Extract spreadsheet ID from URL
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", sheet_url)
    if not match:
        return jsonify({"success": False, "error": "URL Google Sheets invalide"}), 400

    spreadsheet_id = match.group(1)

    # Verify the user can access this sheet
    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheet_meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_title = sheet_meta.get("properties", {}).get("title", "Sheet")
    except Exception as exc:
        logger.error("Cannot access sheet %s: %s", spreadsheet_id, exc)
        return jsonify({
            "success": False,
            "error": "Impossible d'acceder a ce Sheet. Verifiez les permissions.",
        }), 400

    canonical_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"

    # Check if already linked
    mtype = user["monitoring_type"]
    existing = db.get_spreadsheets(user_id, monitoring_type=mtype)
    for s in existing:
        if s["spreadsheet_id"] == spreadsheet_id:
            return jsonify({"success": False, "error": "Ce Sheet est deja lie"}), 400

    row_id = db.create_spreadsheet(
        user_id=user_id,
        spreadsheet_id=spreadsheet_id,
        spreadsheet_url=canonical_url,
        is_auto_created=False,
        monitoring_type=mtype,
    )

    logger.info("Linked sheet %s for user id=%d", spreadsheet_id, user_id)
    return jsonify({
        "success": True,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": canonical_url,
        "sheet_title": sheet_title,
    })


@sheets_bp.route("/api/fix-formulas", methods=["POST"])
@login_required
def fix_formulas():
    """Re-inject Bénéfice / ROI / Temps en stock formulas into the user's Sheet."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    mtype = user.get("monitoring_type", "tickets")

    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]
        FORMULA_ROWS = 500

        # Step 1: Re-write existing price/date data as USER_ENTERED to fix text→number
        if mtype == "tickets":
            price_range = "Commandes!E2:E501"  # Prix Achat
            price_range2 = "Commandes!I2:I501"  # Prix Vente
        else:
            price_range = "Commandes!B2:B501"   # Prix Achat
            price_range2 = "Commandes!D2:D501"  # Prix Vente

        for pr in [price_range, price_range2]:
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=pr,
            ).execute()
            vals = result.get("values", [])
            if vals:
                # Re-write same values but as USER_ENTERED so Sheets parses numbers
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=pr,
                    valueInputOption="USER_ENTERED",
                    body={"values": vals},
                ).execute()

        # Step 2: Clear old per-row formulas in F:H, then inject ARRAYFORMULA
        if mtype == "tickets":
            formula_range = f"Commandes!J2:J{FORMULA_ROWS + 1}"
            formula_values = [
                [f'=IF(OR(E{r}="",I{r}=""),"",I{r}-E{r})']
                for r in range(2, FORMULA_ROWS + 2)
            ]
        else:
            # Clear old per-row formulas in F3:H501 (keep F2:H2 for ARRAYFORMULA)
            clear_range = f"Commandes!F3:H{FORMULA_ROWS + 1}"
            sheets_service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=clear_range,
            ).execute()

            formula_range = "Commandes!F2:H2"
            formula_values = [[
                '=ARRAYFORMULA(SI(B2:B="","",D2:D-B2:B))',
                '=ARRAYFORMULA(SI(B2:B="","",(D2:D-B2:B)/B2:B))',
                '=ARRAYFORMULA(SI(C2:C="","",SI(E2:E="",AUJOURDHUI()-C2:C,E2:E-C2:C)))',
            ]]

        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=formula_range,
            valueInputOption="USER_ENTERED",
            body={"values": formula_values},
        ).execute()

        # Step 3: Apply number formats (Vinted only)
        if mtype != "tickets":
            # Get sheet ID
            meta = sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties",
            ).execute()
            sheet_id = meta["sheets"][0]["properties"]["sheetId"]

            fmt_requests = []
            vinted_formats = [
                {"col": 5, "pattern": '"€"#,##0.00'},  # F Bénéfice
                {"col": 6, "pattern": "0.0%"},           # G ROI %
                {"col": 7, "pattern": '0" j"'},          # H Temps en stock
            ]
            for fmt in vinted_formats:
                fmt_requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": 502,
                            "startColumnIndex": fmt["col"],
                            "endColumnIndex": fmt["col"] + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": "NUMBER", "pattern": fmt["pattern"]}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                })
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": fmt_requests},
            ).execute()

        return jsonify({"success": True, "message": "Donnees converties et formules injectees"})

    except Exception as exc:
        logger.error("fix-formulas error: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors de la correction des formules"}), 500


@sheets_bp.route("/api/create-event-sheet", methods=["POST"])
@login_required
def create_event_sheet():
    """Create a new sheet tab for a specific event with its tickets."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    data = request.get_json(silent=True)
    event_name = (data or {}).get("event", "").strip()

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

        # Read all data from Commandes
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:J",
        ).execute()
        rows = result.get("values", [])

        logger.info("create_event_sheet: total rows=%d, event_name='%s'", len(rows), event_name)

        if len(rows) < 2:
            return jsonify({"success": False, "error": "Aucune donnee dans le Sheet"}), 400

        headers = rows[0]
        logger.info("create_event_sheet: headers=%s", headers)

        # Filter rows for this event (column 0 = Evenement)
        event_rows = [row for row in rows[1:] if row and row[0] and row[0].strip().lower() == event_name.lower()]

        # Debug: log all unique event names from sheet
        unique_events = set()
        for row in rows[1:]:
            if row and row[0]:
                unique_events.add(row[0].strip())
        logger.info("create_event_sheet: unique events in sheet=%s", unique_events)
        logger.info("create_event_sheet: matched %d rows for '%s'", len(event_rows), event_name)

        if not event_rows:
            return jsonify({"success": False, "error": f"Aucun billet trouve pour '{event_name}'"}), 404

        # Sanitize tab name (max 100 chars, no special sheet chars)
        tab_name = event_name[:100].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "(").replace("]", ")")

        # Check if tab already exists
        meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing_tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]

        if tab_name in existing_tabs:
            # Tab exists — clear and rewrite
            sheets_service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_name}'!A:J",
            ).execute()
        else:
            # Create new tab
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()

        # Write headers + event rows
        write_data = [headers] + event_rows
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption="RAW",
            body={"values": write_data},
        ).execute()

        logger.info("Created event sheet tab '%s' with %d rows for user id=%d", tab_name, len(event_rows), user_id)
        return jsonify({
            "success": True,
            "tab_name": tab_name,
            "rows_count": len(event_rows),
            "message": f"Onglet '{tab_name}' cree avec {len(event_rows)} billet(s)",
        })
    except Exception as exc:
        logger.error("Failed to create event sheet: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors de la creation de l'onglet evenement"}), 500
