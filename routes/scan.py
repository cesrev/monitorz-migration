"""
Scan routes: manual scans, background scanner, alert checking.
"""

import logging
import re
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time as _time
from flask import Blueprint, session, request, jsonify
from googleapiclient.discovery import build
import database as db
from helpers import login_required, get_google_credentials
from extensions import limiter

logger = logging.getLogger(__name__)

scan_bp = Blueprint("scan", __name__)

# Background scanner globals
SCAN_INTERVAL_MIN = 480  # 8 hours
_scheduler_running = False


@scan_bp.route("/api/scan-now", methods=["POST"])
@limiter.limit("5 per hour")
@login_required
def scan_now():
    """Trigger a manual scan for the current user."""
    user_id = session["user_id"]

    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    mtype = user["monitoring_type"]
    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    try:
        from scanner import scan_user
        orders_found = scan_user(user_id)

        # Check alerts after manual scan
        try:
            _check_alerts_for_user(user)
        except Exception as alert_exc:
            logger.error("Alert check after manual scan failed: %s", alert_exc)

        return jsonify({
            "success": True,
            "orders_found": orders_found,
            "message": f"{orders_found} nouvelle(s) commande(s) trouvee(s)",
        })
    except Exception as exc:
        logger.error("Manual scan failed for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": "Erreur lors du scan. Veuillez reessayer."}), 500


def _normalize_date(raw: str) -> Optional[str]:
    """Normalize various date formats to YYYY-MM-DD. Returns None if unparseable."""
    if not raw:
        return None
    raw = raw.strip()

    # Already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw

    # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"

    # Try French text dates: "15 mars 2025", "Samedi 15 mars 2025"
    mois_map = {
        "janvier": "01", "fevrier": "02", "février": "02", "mars": "03",
        "avril": "04", "mai": "05", "juin": "06", "juillet": "07",
        "aout": "08", "août": "08", "septembre": "09", "octobre": "10",
        "novembre": "11", "decembre": "12", "décembre": "12",
    }
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw.lower())
    if m:
        day = m.group(1).zfill(2)
        month_str = m.group(2)
        year = m.group(3)
        month = mois_map.get(month_str)
        if month:
            return f"{year}-{month}-{day}"

    return None


def _check_alerts_for_user(user: dict):
    """Check upcoming events and dormant stock, create notifications."""
    user_id = user["id"]
    monitoring_type = user["monitoring_type"]
    plan = user.get("plan", "starter")
    alert_days = user.get("alert_days_before", 7)
    dormant_days = user.get("dormant_days_threshold", 30)

    sheets = db.get_spreadsheets(user_id, monitoring_type=monitoring_type)
    if not sheets:
        return

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]
        now = datetime.utcnow()

        if monitoring_type == "tickets" and plan == "pro":
            # PRO only: alertes evenement a venir
            # Read ticket data: A=Event, B=Cat, C=Lieu, D=Date, E=Prix Achat, I=Prix Vente
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range="Commandes!A:J",
            ).execute()
            rows = result.get("values", [])

            for row in rows[1:]:
                if not row or not row[0]:
                    continue

                event = row[0]
                event_date_raw = row[3] if len(row) > 3 else ""
                prix_vente = row[8] if len(row) > 8 else ""

                # Skip sold tickets
                if prix_vente and prix_vente.strip():
                    continue

                event_date = _normalize_date(event_date_raw)
                if not event_date:
                    continue

                try:
                    event_dt = datetime.strptime(event_date, "%Y-%m-%d")
                    days_until = (event_dt - now).days

                    if days_until < 0:
                        continue  # Event passe, on ignore

                    # Alert urgente : event dans les X prochains jours
                    if days_until <= alert_days:
                        ref_key = f"event_urgent:{event}:{event_date}"
                        db.create_notification(
                            user_id,
                            "event_soon",
                            f"URGENT — {event} dans {days_until}j",
                            f"{event} le {event_date_raw} — billet non vendu, event imminent !",
                            reference_key=ref_key,
                        )
                    else:
                        # Alert info : event a venir (non vendu)
                        ref_key = f"event_upcoming:{event}:{event_date}"
                        db.create_notification(
                            user_id,
                            "event_soon",
                            f"{event} — dans {days_until}j",
                            f"{event} le {event_date_raw} — billet non vendu",
                            reference_key=ref_key,
                        )

                except (ValueError, TypeError):
                    continue

        elif monitoring_type == "vinted":
            # Vinted (Starter + Pro): alertes stock dormant
            # A=Article, B=Prix Achat, C=Date Achat, D=Prix Vente
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range="Commandes!A:I",
            ).execute()
            rows = result.get("values", [])

            for row in rows[1:]:
                if not row or not row[0]:
                    continue

                title = row[0]
                date_achat_raw = row[2] if len(row) > 2 else ""
                prix_vente = row[3] if len(row) > 3 else ""

                # Skip sold items
                if prix_vente and prix_vente.strip():
                    continue

                date_achat = _normalize_date(date_achat_raw)
                if not date_achat:
                    continue

                try:
                    achat_dt = datetime.strptime(date_achat, "%Y-%m-%d")
                    days_in_stock = (now - achat_dt).days

                    if days_in_stock >= dormant_days:
                        ref_key = f"dormant:{title}:{date_achat}"
                        db.create_notification(
                            user_id,
                            "dormant_stock",
                            f"Stock dormant : {title}",
                            f"{title} en stock depuis {days_in_stock} jours (achat {date_achat_raw})",
                            reference_key=ref_key,
                        )
                except (ValueError, TypeError):
                    continue

    except Exception as exc:
        logger.error("Alert check failed for user id=%d: %s", user_id, exc)


def _background_scanner():
    """Background thread that scans all users every hour, parallelized."""
    logger.info("Background scanner started (interval=%d min)", SCAN_INTERVAL_MIN)
    while True:
        try:
            users = db.get_all_users()
            now = datetime.utcnow()

            # Filter to eligible users
            eligible_users = []
            for user in users:
                user_id = user["id"]

                # Skip paused users
                if user.get("monitoring_paused"):
                    continue

                # Check if user has gmail accounts and a sheet
                accounts = db.get_gmail_accounts(user_id)
                mtype = user["monitoring_type"]
                sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
                if not accounts or not sheets:
                    continue

                # Check last scan time — skip if scanned less than 1h ago
                last_scan = db.get_last_scan(user_id, monitoring_type=mtype)
                if last_scan and last_scan["scanned_at"]:
                    try:
                        last_dt = datetime.fromisoformat(last_scan["scanned_at"])
                        elapsed_min = (now - last_dt).total_seconds() / 60
                        if elapsed_min < SCAN_INTERVAL_MIN:
                            continue
                    except (ValueError, TypeError):
                        pass

                eligible_users.append(user)

            # Scan eligible users in parallel
            if eligible_users:
                from scanner import scan_user

                def _scan_and_alert(u):
                    uid = u["id"]
                    orders = scan_user(uid)
                    logger.info("Auto-scan user id=%d: %d orders found", uid, orders)
                    try:
                        _check_alerts_for_user(u)
                    except Exception as exc:
                        logger.error("Alert check failed for user id=%d: %s", uid, exc)
                    return orders

                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(_scan_and_alert, u): u for u in eligible_users}
                    for future in as_completed(futures):
                        user = futures[future]
                        try:
                            future.result(timeout=300)
                        except Exception as exc:
                            logger.error("Auto-scan failed for user id=%d: %s", user["id"], exc)

        except Exception as exc:
            logger.error("Background scanner error: %s", exc)

        # Check every 5 minutes (scans only fire if 1h elapsed)
        _time.sleep(300)


def start_background_scanner():
    """Start the background scanner thread (once)."""
    global _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    t = threading.Thread(target=_background_scanner, daemon=True)
    t.start()
    logger.info("Background scanner thread launched")
