"""
Billets & Vinted Monitor MVP - Flask Application
Google OAuth flow, dashboard, API routes.
"""

import os
import re
import logging
from functools import wraps

# Allow HTTP for local development (OAuth2 requires HTTPS by default)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
from datetime import datetime
from typing import Optional

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    session,
    request,
    jsonify,
    flash,
)
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

import database as db
from config import SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_URL, SCOPES, ADMIN_EMAILS

# ============================================
# CONFIGURATION
# ============================================

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================
# HELPERS
# ============================================

def login_required(f):
    """Decorator that redirects to /login if user is not authenticated."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator that checks if user is admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("user_email") not in ADMIN_EMAILS:
            flash("Acces reserve aux administrateurs.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


def _get_oauth_flow(redirect_uri: str) -> Flow:
    """Build an OAuth2 Flow from environment variables."""
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )


def _credentials_to_dict(credentials: Credentials) -> dict:
    """Serialize credentials to a dict (for session storage)."""
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else [],
    }


def _build_credentials_from_account(account: dict) -> Optional[Credentials]:
    """Build Credentials from a gmail_account DB row. Refreshes if expired."""
    token = account.get("oauth_token")
    refresh_token = account.get("oauth_refresh_token")

    if not token and not refresh_token:
        return None

    creds = Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            expiry_str = creds.expiry.isoformat() if creds.expiry else None
            db.update_gmail_account_tokens(account["id"], creds.token, expiry_str)
            if creds.refresh_token != refresh_token:
                db.update_gmail_account_refresh_token(account["id"], creds.refresh_token)
        except Exception as exc:
            logger.error("Token refresh failed for account id=%d: %s", account["id"], exc)
            return None

    return creds


def _create_spreadsheet_for_user(user_id: int, monitoring_type: str, plan: str = "starter") -> Optional[dict]:
    """Create a Google Sheet in the user's Drive and register it in DB.

    Uses the primary gmail account's credentials.
    Returns the spreadsheet dict or None.
    """
    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return None

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
        return None

    sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    if monitoring_type == "tickets":
        title = "Billets Monitor - Commandes"
        headers = [
            "Événement", "Catégorie", "Lieu", "Date", "Prix Achat",
            "N° Commande", "Lien", "Compte", "Prix Vente", "Bénéfice",
        ]
    elif plan == "pro":
        title = "Vinted Monitor Pro - Achats & Ventes"
        headers = [
            "Article", "Prix Achat", "Date Achat", "Prix Vente", "Date Vente",
            "Benefice", "ROI %", "Temps en stock", "Compte",
        ]
    else:
        title = "Vinted Monitor - Ventes"
        headers = ["Article", "Prix Vente", "Date Vente", "Compte"]

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
        sheet_id = result["sheets"][0]["properties"]["sheetId"]
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"

        # Format row 1: bold + gray background
        format_request = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {
                                    "red": 0.85,
                                    "green": 0.85,
                                    "blue": 0.85,
                                },
                                "textFormat": {"bold": True},
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
                    }
                }
            ]
        }
        try:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body=format_request
            ).execute()
        except Exception as fmt_exc:
            logger.warning("Failed to format header row: %s", fmt_exc)

        row_id = db.create_spreadsheet(
            user_id=user_id,
            spreadsheet_id=spreadsheet_id,
            spreadsheet_url=spreadsheet_url,
            is_auto_created=True,
        )
        logger.info("Created spreadsheet for user %d: %s", user_id, spreadsheet_url)
        return {
            "id": row_id,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": spreadsheet_url,
        }
    except Exception as exc:
        logger.error("Failed to create spreadsheet for user %d: %s", user_id, exc)
        return None


# ============================================
# ROUTES - PAGES
# ============================================

@app.route("/")
def index():
    """Landing page."""
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/login")
def login():
    """Login page."""
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    """Client dashboard."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id)
    last_scan = db.get_last_scan(user_id)
    orders_count = db.get_processed_orders_count(user_id)

    return render_template(
        "dashboard.html",
        user=user,
        accounts=accounts,
        sheets=sheets,
        last_scan=last_scan,
        orders_count=orders_count,
    )


@app.route("/logout")
def logout():
    """Clear session and redirect to landing."""
    session.clear()
    return redirect(url_for("index"))


# ============================================
# ROUTES - OAUTH (first connection)
# ============================================

@app.route("/auth/google")
def auth_google():
    """Start the Google OAuth flow.

    Query params:
        type: 'tickets' or 'vinted' (monitoring type)
    """
    monitoring_type = request.args.get("type", "tickets")
    if monitoring_type not in ("tickets", "vinted"):
        monitoring_type = "tickets"

    plan = request.args.get("plan", "starter")
    if plan not in ("starter", "pro"):
        plan = "starter"

    redirect_uri = f"{APP_URL}/oauth/callback"
    flow = _get_oauth_flow(redirect_uri)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
    )

    session["oauth_state"] = state
    session["oauth_monitoring_type"] = monitoring_type
    session["oauth_plan"] = plan

    return redirect(authorization_url)


@app.route("/oauth/callback")
def oauth_callback():
    """Handle OAuth callback after user grants access.

    Creates or updates user, creates gmail_account, creates spreadsheet.
    """
    monitoring_type = session.pop("oauth_monitoring_type", "tickets")
    plan = session.pop("oauth_plan", "starter")

    redirect_uri = f"{APP_URL}/oauth/callback"
    flow = _get_oauth_flow(redirect_uri)

    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        logger.error("OAuth token exchange failed: %s", exc)
        flash("Erreur d'authentification Google. Veuillez reessayer.", "error")
        return redirect(url_for("login"))

    credentials = flow.credentials

    # Get user profile info
    try:
        oauth2_service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        user_info = oauth2_service.userinfo().get().execute()
    except Exception as exc:
        logger.error("Failed to get user info: %s", exc)
        flash("Impossible de recuperer vos informations Google.", "error")
        return redirect(url_for("login"))

    email = user_info.get("email", "")
    name = user_info.get("name", email)
    picture = user_info.get("picture", "")

    # Find or create user
    user = db.get_user_by_email(email)
    if user:
        user_id = user["id"]
        db.update_user(user_id, name=name, picture=picture, plan=plan)
    else:
        user_id = db.create_user(email, name, picture, monitoring_type, plan)

    # Create or update gmail account
    existing_accounts = db.get_gmail_accounts(user_id)
    existing_account = next((a for a in existing_accounts if a["email"] == email), None)

    token_expiry = credentials.expiry.isoformat() if credentials.expiry else None

    if existing_account:
        db.update_gmail_account_tokens(existing_account["id"], credentials.token, token_expiry)
        if credentials.refresh_token:
            db.update_gmail_account_refresh_token(existing_account["id"], credentials.refresh_token)
    else:
        is_primary = len(existing_accounts) == 0
        db.create_gmail_account(
            user_id=user_id,
            email=email,
            oauth_token=credentials.token,
            oauth_refresh_token=credentials.refresh_token or "",
            token_expiry=token_expiry,
            is_primary=is_primary,
        )

    # Create spreadsheet if user has none
    sheets = db.get_spreadsheets(user_id)
    if not sheets:
        _create_spreadsheet_for_user(user_id, monitoring_type, plan)

    # Set session
    session["user_id"] = user_id
    session["user_email"] = email
    session["user_name"] = name
    session["user_picture"] = picture

    logger.info("User logged in: id=%d email=%s type=%s", user_id, email, monitoring_type)
    return redirect(url_for("dashboard"))


# ============================================
# ROUTES - ADD GMAIL (additional account)
# ============================================

@app.route("/api/add-gmail", methods=["POST"])
@login_required
def add_gmail():
    """Generate an OAuth link for adding a new Gmail account.

    Returns JSON with the authorization URL.
    """
    user_id = session["user_id"]

    # Starter plan: limit to 1 Gmail account
    user = db.get_user_by_id(user_id)
    if user and user.get("plan") == "starter":
        existing_accounts = db.get_gmail_accounts(user_id)
        if len(existing_accounts) >= 1:
            return jsonify({
                "success": False,
                "error": "Le plan Starter est limite a 1 compte Gmail. Passez au Pro pour en ajouter.",
            }), 403

    redirect_uri = f"{APP_URL}/oauth/add-gmail/callback"
    flow = _get_oauth_flow(redirect_uri)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
    )

    session["add_gmail_state"] = state

    return jsonify({"success": True, "auth_url": authorization_url})


@app.route("/oauth/add-gmail/callback")
@login_required
def add_gmail_callback():
    """Handle OAuth callback for adding an additional Gmail account."""
    user_id = session["user_id"]

    redirect_uri = f"{APP_URL}/oauth/add-gmail/callback"
    flow = _get_oauth_flow(redirect_uri)

    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        logger.error("Add-Gmail OAuth failed: %s", exc)
        flash("Erreur d'authentification. Veuillez reessayer.", "error")
        return redirect(url_for("dashboard"))

    credentials = flow.credentials

    # Get the email of the new account
    try:
        oauth2_service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        user_info = oauth2_service.userinfo().get().execute()
    except Exception as exc:
        logger.error("Failed to get user info for add-gmail: %s", exc)
        flash("Impossible de recuperer les informations du compte.", "error")
        return redirect(url_for("dashboard"))

    new_email = user_info.get("email", "")

    # Check if this account already exists for this user
    existing_accounts = db.get_gmail_accounts(user_id)
    existing_account = next((a for a in existing_accounts if a["email"] == new_email), None)

    token_expiry = credentials.expiry.isoformat() if credentials.expiry else None

    if existing_account:
        db.update_gmail_account_tokens(existing_account["id"], credentials.token, token_expiry)
        if credentials.refresh_token:
            db.update_gmail_account_refresh_token(existing_account["id"], credentials.refresh_token)
        flash(f"Compte {new_email} mis a jour.", "info")
    else:
        db.create_gmail_account(
            user_id=user_id,
            email=new_email,
            oauth_token=credentials.token,
            oauth_refresh_token=credentials.refresh_token or "",
            token_expiry=token_expiry,
            is_primary=False,
        )
        flash(f"Compte {new_email} ajoute avec succes.", "success")

    logger.info("Added gmail account %s for user id=%d", new_email, user_id)
    return redirect(url_for("dashboard"))


@app.route("/api/gmail-accounts/<int:account_id>", methods=["DELETE"])
@login_required
def delete_gmail_account_route(account_id):
    """Remove a secondary Gmail account."""
    user_id = session["user_id"]

    account = db.get_gmail_account_by_id(account_id)
    if not account or account["user_id"] != user_id:
        return jsonify({"success": False, "error": "Compte introuvable"}), 404

    if account["is_primary"]:
        return jsonify({"success": False, "error": "Impossible de retirer le compte principal"}), 403

    db.delete_gmail_account(account_id)
    logger.info("Deleted gmail account id=%d for user id=%d", account_id, user_id)
    return jsonify({"success": True, "message": "Compte Gmail retire"})


# ============================================
# ROUTES - LINK EXISTING SHEET
# ============================================

@app.route("/api/link-sheet", methods=["POST"])
@login_required
def link_sheet():
    """Link an existing Google Sheet by URL.

    Expects JSON: { "sheet_url": "https://docs.google.com/spreadsheets/d/..." }
    """
    user_id = session["user_id"]
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
    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
        return jsonify({"success": False, "error": "Impossible de verifier l'acces au Sheet"}), 500

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
    existing = db.get_spreadsheets(user_id)
    for s in existing:
        if s["spreadsheet_id"] == spreadsheet_id:
            return jsonify({"success": False, "error": "Ce Sheet est deja lie"}), 400

    row_id = db.create_spreadsheet(
        user_id=user_id,
        spreadsheet_id=spreadsheet_id,
        spreadsheet_url=canonical_url,
        is_auto_created=False,
    )

    logger.info("Linked sheet %s for user id=%d", spreadsheet_id, user_id)
    return jsonify({
        "success": True,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": canonical_url,
        "sheet_title": sheet_title,
    })


# ============================================
# ROUTES - SCAN
# ============================================

@app.route("/api/scan-now", methods=["POST"])
@login_required
def scan_now():
    """Trigger a manual scan for the current user."""
    user_id = session["user_id"]

    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    sheets = db.get_spreadsheets(user_id)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    try:
        from scanner import scan_user
        orders_found = scan_user(user_id)
        return jsonify({
            "success": True,
            "orders_found": orders_found,
            "message": f"{orders_found} nouvelle(s) commande(s) trouvee(s)",
        })
    except Exception as exc:
        logger.error("Manual scan failed for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ============================================
# ROUTES - STATS
# ============================================

@app.route("/api/stats")
@login_required
def stats():
    """Return stats for the current user."""
    user_id = session["user_id"]

    user = db.get_user_by_id(user_id)
    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id)
    last_scan = db.get_last_scan(user_id)
    orders_count = db.get_processed_orders_count(user_id)
    recent_logs = db.get_scan_logs(user_id, limit=10)

    return jsonify({
        "success": True,
        "user": {
            "email": user["email"] if user else "",
            "name": user["name"] if user else "",
            "monitoring_type": user["monitoring_type"] if user else "",
            "plan": user["plan"] if user else "starter",
        },
        "gmail_accounts": [
            {"id": a["id"], "email": a["email"], "is_primary": bool(a["is_primary"])}
            for a in accounts
        ],
        "spreadsheets": [
            {
                "id": s["id"],
                "spreadsheet_id": s["spreadsheet_id"],
                "spreadsheet_url": s["spreadsheet_url"],
                "is_auto_created": bool(s["is_auto_created"]),
            }
            for s in sheets
        ],
        "orders_count": orders_count,
        "last_scan": {
            "scanned_at": last_scan["scanned_at"],
            "status": last_scan["status"],
            "orders_found": last_scan["orders_found"],
        } if last_scan else None,
        "recent_scans": [
            {
                "scanned_at": log["scanned_at"],
                "status": log["status"],
                "orders_found": log["orders_found"],
                "error_message": log["error_message"],
            }
            for log in recent_logs
        ],
    })


# ============================================
# ROUTES - PLAN
# ============================================

@app.route("/api/update-plan", methods=["POST"])
@login_required
def update_plan():
    """Update the user's plan (starter/pro)."""
    user_id = session["user_id"]
    data = request.get_json(silent=True)

    if not data or not data.get("plan"):
        return jsonify({"success": False, "error": "Plan requis"}), 400

    new_plan = data["plan"]
    if new_plan not in ("starter", "pro"):
        return jsonify({"success": False, "error": "Plan invalide"}), 400

    db.update_user(user_id, plan=new_plan)
    logger.info("User id=%d updated plan to %s", user_id, new_plan)
    return jsonify({"success": True, "plan": new_plan})


# ============================================
# ROUTES - ADMIN
# ============================================

@app.route("/admin")
@admin_required
def admin_panel():
    """Admin panel showing all clients."""
    users = db.get_all_users()

    clients = []
    total_gmail = 0
    total_orders = 0

    for u in users:
        accounts = db.get_gmail_accounts(u["id"])
        sheets = db.get_spreadsheets(u["id"])
        orders = db.get_processed_orders_count(u["id"])
        last_scan = db.get_last_scan(u["id"])

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


@app.route("/api/admin/clients")
@admin_required
def admin_clients_api():
    """API endpoint returning all clients data."""
    users = db.get_all_users()

    result = []
    for u in users:
        accounts = db.get_gmail_accounts(u["id"])
        orders = db.get_processed_orders_count(u["id"])
        last_scan = db.get_last_scan(u["id"])

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


# ============================================
# BACKGROUND SCANNER — 1 scan / hour for all users
# ============================================

import threading
import time as _time

SCAN_INTERVAL_MIN = 60  # 1 hour

_scheduler_running = False


def _background_scanner():
    """Background thread that scans all users every hour."""
    logger.info("Background scanner started (interval=%d min)", SCAN_INTERVAL_MIN)
    while True:
        try:
            users = db.get_all_users()
            now = datetime.utcnow()

            for user in users:
                user_id = user["id"]

                # Check if user has gmail accounts and a sheet
                accounts = db.get_gmail_accounts(user_id)
                sheets = db.get_spreadsheets(user_id)
                if not accounts or not sheets:
                    continue

                # Check last scan time — skip if scanned less than 1h ago
                last_scan = db.get_last_scan(user_id)
                if last_scan and last_scan["scanned_at"]:
                    try:
                        last_dt = datetime.fromisoformat(last_scan["scanned_at"])
                        elapsed_min = (now - last_dt).total_seconds() / 60
                        if elapsed_min < SCAN_INTERVAL_MIN:
                            continue
                    except (ValueError, TypeError):
                        pass

                # Time to scan
                try:
                    from scanner import scan_user
                    orders = scan_user(user_id)
                    logger.info("Auto-scan user id=%d: %d orders found", user_id, orders)
                except Exception as exc:
                    logger.error("Auto-scan failed for user id=%d: %s", user_id, exc)

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


# ============================================
# INIT & RUN
# ============================================

db.init_db()
start_background_scanner()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
