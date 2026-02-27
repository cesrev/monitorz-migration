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

import anthropic
import database as db
from config import SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_URL, SCOPES, ADMIN_EMAILS, ANTHROPIC_API_KEY

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
    """Client dashboard — filtered by active monitoring_type profile."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    mtype = user["monitoring_type"] if user else "tickets"
    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    last_scan = db.get_last_scan(user_id, monitoring_type=mtype)
    orders_count = db.get_processed_orders_count(user_id, monitoring_type=mtype)

    unread_count = db.get_unread_notification_count(user_id, monitoring_type=mtype)

    return render_template(
        "dashboard.html",
        user=user,
        accounts=accounts,
        sheets=sheets,
        last_scan=last_scan,
        orders_count=orders_count,
        unread_count=unread_count,
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
        db.update_user(user_id, name=name, picture=picture, plan=plan, monitoring_type=monitoring_type)
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

    # Create spreadsheet if user has none for THIS monitoring_type
    sheets_for_type = db.get_spreadsheets(user_id, monitoring_type=monitoring_type)
    if not sheets_for_type:
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
    mtype = user["monitoring_type"] if user else "tickets"
    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    last_scan = db.get_last_scan(user_id, monitoring_type=mtype)
    orders_count = db.get_processed_orders_count(user_id, monitoring_type=mtype)
    recent_logs = db.get_scan_logs(user_id, limit=10, monitoring_type=mtype)

    return jsonify({
        "success": True,
        "user": {
            "email": user["email"] if user else "",
            "name": user["name"] if user else "",
            "monitoring_type": mtype,
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
# ROUTES - HASHTAG FARM v2 (Vinted)
# ============================================
import re as _re

# --- COUCHE 1 : Marque + Modele (dictionnaire d'aliases) ---

BRAND_ALIASES = {
    "nike": ["#nike", "#nikeair"],
    "adidas": ["#adidas", "#adidasoriginals"],
    "jordan": ["#jordan", "#nike"],
    "yeezy": ["#yeezy", "#adidas", "#yeezyboost"],
    "travis scott": ["#travisscott", "#cactusjack", "#ts"],
    "cactus jack": ["#cactusjack", "#travisscott"],
    "new balance": ["#newbalance", "#nb"],
    "puma": ["#puma"],
    "reebok": ["#reebok"],
    "converse": ["#converse", "#chucktaylor"],
    "vans": ["#vans", "#offthewall"],
    "salomon": ["#salomon", "#salomonsneakers"],
    "asics": ["#asics", "#gellyte"],
    "saucony": ["#saucony"],
    "on running": ["#onrunning", "#on"],
    "hoka": ["#hoka", "#hokaoneone"],
    "supreme": ["#supreme", "#supremeny"],
    "palace": ["#palace", "#palaceskateboards", "#trifergo"],
    "stussy": ["#stussy"],
    "carhartt": ["#carhartt", "#carharttwip"],
    "the north face": ["#thenorthface", "#tnf"],
    "ralph lauren": ["#ralphlauren", "#polo", "#poloralphlauren", "#polosport"],
    "lacoste": ["#lacoste"],
    "barbour": ["#barbour", "#barbourhomme"],
    "the kooples": ["#thekooples", "#thekooklessport"],
    "sandro": ["#sandro", "#sandroparis"],
    "gucci": ["#gucci"],
    "louis vuitton": ["#louisvuitton", "#lv"],
    "lv": ["#louisvuitton", "#lv"],
    "hermes": ["#hermes"],
    "balenciaga": ["#balenciaga"],
    "dior": ["#dior"],
    "prada": ["#prada"],
    "moncler": ["#moncler"],
    "stone island": ["#stoneisland", "#si"],
    "cp company": ["#cpcompany"],
    "arcteryx": ["#arcteryx"],
    "burberry": ["#burberry"],
    "versace": ["#versace"],
    "fendi": ["#fendi"],
    "off-white": ["#offwhite"],
    "palm angels": ["#palmangels"],
    "essentials": ["#essentials", "#fearofgod", "#fog"],
    "fear of god": ["#fearofgod", "#fog"],
    "gallery dept": ["#gallerydept"],
    "represent": ["#represent"],
    "ami": ["#amiparis", "#ami"],
    "acne studios": ["#acnestudios"],
    "jacquemus": ["#jacquemus"],
    "apc": ["#apc"],
    "celine": ["#celine"],
    "loewe": ["#loewe"],
    "bottega veneta": ["#bottegaveneta", "#bv"],
    "yves saint laurent": ["#yvessaintlaurent", "#yslvintage"],
    "ysl": ["#yvessaintlaurent", "#yslvintage"],
    "pelle pelle": ["#pellepelle", "#pellepelleathletics"],
    "dada": ["#dadasupreme"],
    "marina yachting": ["#marinayachting"],
    "slipknot": ["#slipknot"],
    "shakira": ["#shakira", "#shakiramerch"],
    "tommy hilfiger": ["#tommyhilfiger", "#tommy"],
    "nautica": ["#nautica"],
    "champion": ["#champion"],
    "fila": ["#fila"],
    "ellesse": ["#ellesse"],
    "kappa": ["#kappa"],
    "umbro": ["#umbro"],
    "diadora": ["#diadora"],
    "levi's": ["#levis", "#levi"],
    "levis": ["#levis", "#levi"],
    "diesel": ["#diesel"],
    "wrangler": ["#wrangler"],
    "patagonia": ["#patagonia"],
    "columbia": ["#columbia"],
    "timberland": ["#timberland"],
    "dr martens": ["#drmartens", "#docs"],
    "birkenstock": ["#birkenstock"],
    # --- StockX top brands (ajout v2.1) ---
    "bape": ["#bape", "#abathingape"],
    "kith": ["#kith", "#kithnyc"],
    "new era": ["#newera", "#neweracap"],
    "ugg": ["#ugg"],
    "anti social social club": ["#assc", "#antisocialsocialclub"],
    "assc": ["#assc", "#antisocialsocialclub"],
    "aime leon dore": ["#aimeleondore", "#ald"],
    "ald": ["#aimeleondore", "#ald"],
    "amiri": ["#amiri"],
    "alexander mcqueen": ["#alexandermcqueen", "#mcqueen"],
    "givenchy": ["#givenchy"],
    "crocs": ["#crocs"],
    "comme des garcons": ["#commedesgarcons", "#cdg"],
    "cdg": ["#commedesgarcons", "#cdg"],
    "corteiz": ["#corteiz", "#crtz"],
    "crtz": ["#corteiz", "#crtz"],
    "trapstar": ["#trapstar"],
    "sp5der": ["#sp5der", "#spider"],
    "chrome hearts": ["#chromehearts"],
    "rick owens": ["#rickowens"],
    "under armour": ["#underarmour", "#ua"],
    "human made": ["#humanmade", "#nigo"],
    "valentino": ["#valentino"],
    "chanel": ["#chanel"],
    "eric emanuel": ["#ericemmanuel", "#ee"],
    "mitchell and ness": ["#mitchellandness", "#mitchellness"],
    "mitchell & ness": ["#mitchellandness", "#mitchellness"],
    "sprayground": ["#sprayground"],
    "clarks": ["#clarks"],
    "ferragamo": ["#ferragamo", "#salvatoreferragamo"],
    "marc jacobs": ["#marcjacobs"],
    "yohji yamamoto": ["#yohjiyamamoto", "#yohji"],
    "merrell": ["#merrell"],
    "brooks": ["#brooks"],
    "telfar": ["#telfar", "#telfarbag"],
    "maison mihara yasuhiro": ["#maisonmiharayasuhiro", "#mmy"],
    "mihara": ["#maisonmiharayasuhiro", "#mmy"],
    "drew house": ["#drewhouse", "#drew"],
    "denim tears": ["#denimtears"],
    "ovo": ["#ovo", "#octobersveryown"],
    "evisu": ["#evisu", "#evisujeans"],
    "heron preston": ["#heronpreston"],
    "vetements": ["#vetements"],
    "vivienne westwood": ["#viviennewestwood"],
    "fred perry": ["#fredperry"],
    "gap": ["#gap"],
    "uniqlo": ["#uniqlo"],
    "zara": ["#zara"],
    "h&m": ["#hm"],
    "hm": ["#hm"],
    "cos": ["#cos"],
    "massimo dutti": ["#massimodutti"],
    "schott": ["#schott", "#schottnyc"],
    "lyle and scott": ["#lyleandscott", "#lylescott"],
    "lyle & scott": ["#lyleandscott", "#lylescott"],
    "dickies": ["#dickies"],
    "lee": ["#lee"],
    "north sails": ["#northsails"],
    "gant": ["#gant"],
    "woolrich": ["#woolrich"],
    "napapijri": ["#napapijri"],
    "eastpak": ["#eastpak"],
    "oakley": ["#oakley"],
    "ray ban": ["#rayban"],
    "ray-ban": ["#rayban"],
    # --- Marques niche ---
    "helly hansen": ["#hellyhansen", "#hh"],
    "sergio tacchini": ["#sergiotacchini"],
    "alpha industries": ["#alphaindustries", "#ma1"],
    "mizuno": ["#mizuno"],
    "karhu": ["#karhu"],
    "le coq sportif": ["#lecoqsportif"],
    "kenzo": ["#kenzo"],
    "isabel marant": ["#isabelmarant"],
    "acne": ["#acnestudios"],
    "stone island shadow": ["#stoneisland", "#stoneislandshadow"],
    "canada goose": ["#canadagoose"],
    "marmot": ["#marmot"],
    "mammut": ["#mammut"],
    "fjallraven": ["#fjallraven"],
    "penfield": ["#penfield"],
    "filson": ["#filson"],
    "ben sherman": ["#bensherman"],
    "ted baker": ["#tedbaker"],
    "paul smith": ["#paulsmith"],
    "hugo boss": ["#hugoboss", "#boss"],
    "boss": ["#hugoboss", "#boss"],
    "armani": ["#armani", "#emporioarmani"],
    "emporio armani": ["#armani", "#emporioarmani"],
    "dolce gabbana": ["#dolcegabbana", "#dg"],
    "d&g": ["#dolcegabbana", "#dg"],
    "mcm": ["#mcm"],
    "goyard": ["#goyard"],
    "balmain": ["#balmain"],
    "kolor": ["#kolor"],
    "kapital": ["#kapital"],
    "needles": ["#needles"],
    "wtaps": ["#wtaps"],
    "neighborhood": ["#neighborhood", "#nbhd"],
    "mastermind": ["#mastermind", "#mastermindjapan"],
    # --- Gaming / Collectibles ---
    "nintendo": ["#nintendo"],
    "pokemon": ["#pokemon", "#pokemontcg"],
    "pokémon": ["#pokemon", "#pokemontcg"],
    "yu-gi-oh": ["#yugioh"],
    "yugioh": ["#yugioh"],
    "magic the gathering": ["#mtg", "#magicthegathering"],
    "mtg": ["#mtg", "#magicthegathering"],
    "sony": ["#sony", "#playstation"],
    "playstation": ["#playstation", "#sony"],
    "xbox": ["#xbox", "#microsoft"],
    "sega": ["#sega", "#retrogaming"],
    "lego": ["#lego"],
    "funko": ["#funko", "#funkopop"],
    "bandai": ["#bandai"],
    "digimon": ["#digimon"],
    "one piece": ["#onepiece"],
    "dragon ball": ["#dragonball", "#dbz"],
    "panini": ["#panini"],
    "topps": ["#topps"],
}

MODEL_ALIASES = {
    # Nike sneakers
    "air force": ["#airforce", "#af1", "#airforce1"],
    "air force 1": ["#airforce", "#af1", "#airforce1"],
    "air force one": ["#airforce", "#af1", "#airforce1", "#airmaxone"],
    "air max 1": ["#airmax", "#airmax1", "#am1", "#airmaxone"],
    "air max one": ["#airmax", "#airmax1", "#am1", "#airmaxone"],
    "air max 90": ["#airmax", "#airmax90", "#am90"],
    "air max 95": ["#airmax", "#airmax95", "#am95", "#airmaxplus"],
    "air max 97": ["#airmax", "#airmax97", "#am97"],
    "air max plus": ["#airmax", "#airmaxplus", "#tn", "#requin"],
    "air max tn": ["#airmaxplus", "#tn", "#requin", "#airmax"],
    "tn": ["#tn", "#airmaxplus", "#requin", "#nike"],
    "vapormax": ["#vapormax", "#nikevapormax"],
    "jordan 1": ["#jordan1", "#j1", "#aj1"],
    "jordan 3": ["#jordan3", "#j3"],
    "jordan 4": ["#jordan", "#jordan4", "#j4"],
    "jordan 5": ["#jordan5", "#j5"],
    "jordan 6": ["#jordan6", "#j6"],
    "jordan 11": ["#jordan11", "#j11"],
    "dunk": ["#dunk", "#dunklow", "#nikedunk"],
    "dunk sb": ["#dunksb", "#dunklow", "#nikesb", "#sb"],
    "dunk low": ["#dunklow", "#nikedunk"],
    "dunk high": ["#dunkhigh", "#nikedunk"],
    "nike blazer": ["#blazer", "#nikeblazer"],
    "cortez": ["#cortez", "#nikecortez"],
    "huarache": ["#huarache", "#nikehuarache"],
    "presto": ["#presto", "#nikepresto"],
    "react": ["#react", "#nikereact"],
    # Adidas sneakers
    "yeezy 350": ["#yeezy350", "#v2"],
    "yeezy 500": ["#yeezy500"],
    "yeezy 700": ["#yeezy700"],
    "stan smith": ["#stansmith"],
    "superstar": ["#superstar", "#adidassuperstar"],
    "gazelle": ["#gazelle", "#adidasgazelle"],
    "samba": ["#samba", "#adidassamba"],
    "campus": ["#campus", "#adidascampus"],
    "forum": ["#forum", "#adidasforum"],
    "spezial": ["#spezial", "#adidasspezial"],
    # NB
    "550": ["#nb550", "#550"],
    "990": ["#nb990", "#990"],
    "2002r": ["#nb2002r", "#2002r"],
    "530": ["#nb530", "#530"],
    # Salomon
    "acs": ["#acspro", "#salomonacs"],
    "acs pro": ["#acspro", "#salomonacs"],
    "xt-6": ["#xt6", "#salomonxt6"],
    "speedcross": ["#speedcross"],
    # Jordan shorthand aliases (j1, j4, etc.)
    "j1": ["#jordan1", "#j1", "#aj1", "#jordan", "#nike"],
    "j2": ["#jordan2", "#j2", "#jordan", "#nike"],
    "j3": ["#jordan3", "#j3", "#jordan", "#nike"],
    "j4": ["#jordan4", "#j4", "#jordan", "#nike"],
    "j5": ["#jordan5", "#j5", "#jordan", "#nike"],
    "j6": ["#jordan6", "#j6", "#jordan", "#nike"],
    "j11": ["#jordan11", "#j11", "#jordan", "#nike"],
    "aj1": ["#jordan1", "#j1", "#aj1", "#jordan", "#nike"],
    "aj4": ["#jordan4", "#j4", "#jordan", "#nike"],
    # Asics models
    "gel-kayano": ["#gelkayano", "#asicskayano"],
    "gel kayano": ["#gelkayano", "#asicskayano"],
    "gel lyte iii": ["#gellyteiii", "#gellyte3"],
    "gel lyte v": ["#gellytev", "#gellyte5"],
    # Reebok models
    "reebok classic leather": ["#classicleather", "#reebokclassic"],
    "reebok classic": ["#reebokclassic", "#classicleather"],
    "club c": ["#clubc", "#clubc85"],
    "instapump": ["#instapump", "#instapumpfury"],
    # Travis Scott / Collab models
    "jumpman jack": ["#jumpmanjack", "#jumpman", "#cactusjack"],
    "jumpman": ["#jumpman"],
    # Clothing models
    "harrington": ["#harrington"],
    "half zip": ["#halfzip", "#sweathalfzip"],
    "box logo": ["#boxlogo", "#bogo"],
    "patchwork": ["#patchwork"],
}

# --- COUCHE 2 : Style / Epoque / Univers ---

# Sneaker keywords -> article is a sneaker
SNEAKER_KEYWORDS = [
    "air max", "airmax", "dunk", "vapormax", "air force",
    "yeezy", "new balance", "nb550", "990", "salomon", "acs",
    "xt-6", "speedcross", "stan smith", "superstar", "gazelle",
    "samba", "campus", "forum", "spezial", "huarache", "presto",
    "cortez", "nike blazer", "react", "sneaker", "basket", "chaussure",
    "converse", "vans old skool", "sk8", "puma suede", "gel lyte",
    "gel-kayano", "gel kayano", "gel-lyte", "asics",
    "hoka", "on running", "crocs", "ugg",
    "reebok", "reebok classic",
    # Jordan sneaker patterns (not bare "jordan" to avoid "Short Jordan")
    "jordan 1", "jordan 2", "jordan 3", "jordan 4", "jordan 5",
    "jordan 6", "jordan 7", "jordan 8", "jordan 9", "jordan 10",
    "jordan 11", "jordan 12", "jordan 13", "jordan retro",
    "j1 ", "j2 ", "j3 ", "j4 ", "j5 ", "j6 ", "j11 ",
    "aj1", "aj4", "aj6", "aj11",
    "air jordan",
    # Extra sneaker brands from StockX
    "mihara", "rick owens ramones", "clarks wallabee",
    "alexander mcqueen", "mcqueen oversized",
    "brooks", "merrell",
    # Niche sneaker brands
    "saucony", "shadow 6000", "shadow 5000",
    "mizuno", "wave rider", "wave prophecy",
    "karhu", "fusion 2.0", "aria 95",
    "diadora", "n9000",
    "le coq sportif",
]

UNIVERSE_TAGS = {
    "streetwear": ["#streetwear", "#urbanwear", "#streetculture"],
    "vintage": ["#vintage", "#retro", "#oldschool"],
    "y2k": ["#y2k", "#2000s", "#annee2000"],
    "90s": ["#90s", "#vintage90s"],
    "techwear": ["#techwear", "#technical", "#functional", "#gorpcore"],
    "oldmoney": ["#oldmoney", "#oldmoneystyle", "#classicfit", "#preppy"],
    "hiphop": ["#hiphopstyle", "#hiphopfashion"],
    "sportswear": ["#sportswear"],
    "outdoor": ["#outdoor", "#gorpcore", "#hiking"],
    "luxe": ["#luxevintage", "#vintageluxury", "#designer"],
    "skate": ["#skateculture", "#skatewear"],
    "british": ["#stylebritannique", "#british"],
    "merch": ["#merch", "#bandmerch", "#concert", "#collector"],
    "casual": ["#casual", "#casualstyle", "#cleanfit"],
    "menswear": ["#menswear", "#modehomme"],
    "preppy": ["#preppy", "#preppystyle", "#collegestyle"],
    "sneakers": ["#sneakers", "#kicks", "#retro"],
    "japanese": ["#japanesestyle", "#japanfashion"],
    "uk": ["#ukstreetwear", "#ukfashion"],
    "gaming": ["#gaming", "#gamer", "#jeuxvideo"],
    "tcg": ["#tcg", "#tradingcards", "#collector"],
    "retrogaming": ["#retrogaming", "#retro", "#nostalgia"],
}

# Auto-detect universe from title keywords
UNIVERSE_DETECTION = {
    "vintage": ["vintage", "retro", "old", "90s", "80s", "70s", "archive"],
    "y2k": ["y2k", "2000", "millenium"],
    "streetwear": ["supreme", "palace", "stussy", "carhartt", "bape", "off-white"],
    "techwear": ["gore-tex", "goretex", "therma", "tech", "utility", "waterproof",
                 "coupe-vent", "windbreaker", "salomon", "arcteryx"],
    "oldmoney": ["ralph lauren", "barbour", "lacoste", "polo", "preppy",
                 "marina yachting", "nautica", "gant"],
    "hiphop": ["pelle pelle", "dada", "fubu", "ecko", "rocawear", "sean john"],
    "outdoor": ["patagonia", "columbia", "barbour", "north face"],
    "luxe": ["gucci", "louis vuitton", "hermes", "balenciaga", "dior", "prada",
             "celine", "loewe", "bottega", "ysl", "yves saint laurent", "fendi"],
    "skate": ["sb", "palace", "dunk sb", "vans"],
    "british": ["barbour", "burberry", "fred perry", "harrington", "tartan"],
    "merch": ["merch", "concert", "tour", "band", "slipknot", "metallica",
              "shakira", "kanye"],
    "preppy": ["ralph lauren", "polo", "lacoste", "gant", "half zip", "col v"],
    "sportswear": ["tracksuit", "jogging", "track", "jersey", "maillot",
                   "survetement", "windbreaker"],
    "japanese": ["japanese", "japan", "evisu", "bape", "comme des garcons", "cdg",
                 "yohji", "mihara"],
    "uk": ["corteiz", "crtz", "trapstar"],
    "gaming": ["nintendo", "switch", "playstation", "ps5", "ps4", "xbox",
               "gameboy", "wii", "sega", "n64", "manette", "controller"],
    "tcg": ["pokemon", "pokémon", "yu-gi-oh", "yugioh", "magic the gathering",
            "mtg", "trading card", "carte", "booster", "etb", "display",
            "coffret", "tin", "blister", "ev9", "ev8", "ev7", "ev6",
            "dilga", "151", "ecarlate", "scarlet"],
    "retrogaming": ["gameboy", "game boy", "sega", "n64", "snes", "nes",
                    "megadrive", "game cube", "gamecube", "retrogaming"],
}

# --- COUCHE 3 : Couleurs ---

COLOR_ALIASES = {
    "noir": ["#black", "#noir"], "black": ["#black", "#noir"],
    "blanc": ["#white", "#blanc"], "white": ["#white", "#blanc"],
    "bleu": ["#blue", "#bleu"], "blue": ["#blue", "#bleu"],
    "bleu marine": ["#bleumarine", "#navy"],
    "navy": ["#navy", "#bleumarine"],
    "rouge": ["#red", "#rouge"], "red": ["#red", "#rouge"],
    "vert": ["#green", "#vert"], "green": ["#green", "#vert"],
    "kaki": ["#kaki", "#militarystyle"], "khaki": ["#kaki", "#militarystyle"],
    "rose": ["#pink", "#rose"], "pink": ["#pink", "#rose"],
    "gris": ["#gris", "#grey"], "grey": ["#gris", "#grey"], "gray": ["#gris", "#grey"],
    "orange": ["#orange"],
    "jaune": ["#jaune", "#yellow"], "yellow": ["#jaune", "#yellow"],
    "beige": ["#beige", "#cream"], "cream": ["#beige", "#cream"],
    "marron": ["#marron", "#brown"], "brown": ["#marron", "#brown"],
    "bordeaux": ["#bordeaux", "#burgundy"], "burgundy": ["#bordeaux", "#burgundy"],
    "turquoise": ["#turquoise"],
    "violet": ["#violet", "#purple"], "purple": ["#violet", "#purple"],
}

# --- BLOCKLIST : tags interdits ---

_BLOCKED_PATTERN = _re.compile(
    r"(taille|size|\bxs\b|\bxxs\b|\bs\b|\bm\b|\bl\b|\bxl\b|\bxxl\b|\bxxxl\b"
    r"|\d{2,}cm|etat|neuf|occasion|tbe|vnds|ttbe|vinted|resell|depop"
    r"|friperie|achat|vente|promo|solde|forsale|secondhand|wts|wtb)",
    _re.IGNORECASE,
)

MAX_HASHTAGS = 15
MIN_HASHTAGS = 10

# Fallback tags when not enough tags are generated (by article type)
FALLBACK_SNEAKER_TAGS = [
    "#sneakers", "#kicks", "#sneakerstyle", "#sneakerhead",
    "#streetwear", "#hype", "#classic", "#retro", "#vintage",
    "#sportswear", "#style", "#fashion", "#collection",
]

FALLBACK_CLOTHING_TAGS = [
    "#mode", "#style", "#fashion", "#vintage", "#streetwear",
    "#modehomme", "#outfitoftheday", "#lookdujour", "#streetstyle",
    "#casual", "#urbanwear", "#trend", "#wardrobe",
]

FALLBACK_ACCESSORY_TAGS = [
    "#accessoire", "#style", "#fashion", "#mode", "#vintage",
    "#designer", "#collection", "#luxury", "#trend",
    "#classic", "#premium", "#detail",
]

FALLBACK_GAMING_TAGS = [
    "#gaming", "#gamer", "#collector", "#collection",
    "#retrogaming", "#videogames", "#jeuxvideo", "#geek",
    "#tradingcards", "#tcg", "#rare", "#limited",
    "#hobby",
]


def _word_match(keyword: str, text: str) -> bool:
    """Check if keyword appears as a whole word (not substring) in text.
    For short keywords (<=3 chars), use word boundary matching.
    For longer keywords, simple 'in' check is fine."""
    if len(keyword) <= 3:
        pattern = r'(?:^|[\s,/\-])' + _re.escape(keyword) + r'(?:$|[\s,/\-])'
        return bool(_re.search(pattern, text))
    return keyword in text


def _is_sneaker(title_lower: str) -> bool:
    """Detect if the article is a sneaker based on title keywords."""
    # Add trailing space for end-of-string matching (e.g. "j1 " matches "j1 chicago")
    padded = title_lower + " "
    return any(kw in padded for kw in SNEAKER_KEYWORDS)


def _detect_universes(title_lower: str) -> list[str]:
    """Detect style/era universes from title."""
    found = []
    for universe, keywords in UNIVERSE_DETECTION.items():
        if any(kw in title_lower for kw in keywords):
            found.append(universe)
    return found


def _detect_article_type(title_lower: str) -> str:
    """Detect broad article type: sneaker, clothing, accessory, or gaming."""
    if _is_sneaker(title_lower):
        return "sneaker"
    gaming_kw = [
        # --- Consoles & Gaming ---
        "nintendo", "switch", "playstation", "ps5", "ps4", "ps3", "ps2",
        "xbox", "gameboy", "game boy", "wii", "sega", "n64",
        "manette", "controller", "amiibo", "jeux", "jeu video",
        # --- TCG generique ---
        "yu-gi-oh", "yugioh", "magic the gathering", "mtg",
        "trading card", "carte", "booster", "etb", "display",
        "tcg", "coffret", "tin", "blister",
        "digimon", "one piece card", "dragon ball",
        # --- Pokemon extensions / sets ---
        "pokemon", "pokémon",
        "ev9", "ev8", "ev7", "ev6", "ev5", "ev4", "ev3", "ev2", "ev1",
        "151", "ecarlate", "scarlet", "paldea", "obsidienne",
        "tempete argentee", "origine perdue", "couronne zenith",
        "astres radieux", "evolutions", "soleil et lune", "epee et bouclier",
        "flammes obsidiennes", "forces temporelles", "faille paradoxe",
        "mascarade crepusculaire", "destinees de paldea",
        # --- Collectibles ---
        "funko", "figurine", "lego", "bearbrick",
        # --- Pokemon top 100 (FR + EN) ---
        "pikachu", "dracaufeu", "charizard", "tortank", "blastoise",
        "florizarre", "venusaur", "mewtwo", "mew", "evoli", "eevee",
        "rondoudou", "jigglypuff", "ronflex", "snorlax", "leviator", "gyarados",
        "dracolosse", "dragonite", "electhor", "zapdos", "artikodin", "articuno",
        "sulfura", "moltres", "lokhlass", "lapras", "metamorph", "ditto",
        "magicarpe", "magikarp", "carapuce", "squirtle", "salameche", "charmander",
        "bulbizarre", "bulbasaur", "alakazam", "ectoplasma", "gengar",
        "mackogneur", "machamp", "scarabrute", "pinsir", "insecateur", "scyther",
        "arcanin", "arcanine", "feunard", "ninetales", "nidoking", "nidoqueen",
        "sulfureux", "groudon", "kyogre", "rayquaza", "deoxys",
        "lucario", "gardevoir", "gallame", "gallade", "absol",
        "lugia", "ho-oh", "celebi", "latias", "latios",
        "dialga", "dilga", "palkia", "giratina", "arceus", "darkrai",
        "reshiram", "zekrom", "kyurem",
        "xerneas", "yveltal", "zygarde",
        "solgaleo", "lunala", "necrozma", "marshadow",
        "zacian", "zamazenta", "eternatus",
        "koraidon", "miraidon",
        "pichu", "togepi", "marill", "tyranocif", "tyranitar",
        "demolosse", "houndoom", "mentali", "espeon", "noctali", "umbreon",
        "leviator", "suicune", "entei", "raikou",
        "brasegali", "blaziken", "jungko", "sceptile", "laggron", "swampert",
        "libegon", "flygon", "milotic", "metalosse", "metagross",
        "carchacrok", "garchomp", "luxray", "staraptor", "roserade",
        "amphinobi", "greninja", "felinferno", "incineroar",
        "mimiqui", "mimikyu", "nymphali", "sylveon",
        "dracaufeu", "tortank", "florizarre",
        "zarude", "urshifu", "spectrier", "glastrier",
        "palafin", "gholdengo", "kingambit", "baxcalibur",
        "roaring moon", "iron valiant", "walking wake",
    ]
    if any(kw in title_lower for kw in gaming_kw):
        return "gaming"
    accessory_kw = ["sac", "bag", "lunettes", "sunglasses", "casquette", "cap",
                    "bonnet", "beanie", "ceinture", "belt", "montre", "watch",
                    "echarpe", "scarf", "banner", "drapeau", "bijou"]
    if any(kw in title_lower for kw in accessory_kw):
        return "accessory"
    return "clothing"


def _generate_hashtags_for_item(title: str, custom_tags: list = None) -> list[str]:
    """Generate hashtags v2 for a Vinted item. 3-layer system, 10 min / 15 max."""
    title_lower = title.lower().strip()
    layer1 = []  # Marque + Modele (priority)
    layer2 = []  # Style / Epoque / Univers
    layer3 = []  # Descripteurs specifiques (couleur, type)

    # === COUCHE 1 : Marque + Modele ===
    matched_brand = None
    # Sort by length desc to match "ralph lauren" before "ralph"
    for brand in sorted(BRAND_ALIASES.keys(), key=len, reverse=True):
        if _word_match(brand, title_lower):
            layer1.extend(BRAND_ALIASES[brand])
            matched_brand = brand
            break

    # Fallback: if no known brand, extract first word as raw brand tag
    if not matched_brand:
        words = title.strip().split()
        if words:
            raw_brand = words[0].lower().strip()
            # Only use if it looks like a brand (not a generic word)
            generic_words = {"le", "la", "les", "un", "une", "des", "de", "du",
                            "lot", "pack", "set", "paire", "pair", "air", "t-shirt",
                            "tee", "veste", "pantalon", "pull", "sac", "short",
                            "jean", "chemise", "hoodie", "sweat", "jogging",
                            "casquette", "bonnet", "maillot", "chaussure",
                            "lunettes", "robe", "manteau", "doudoune", "polo",
                            "cardigan", "bermuda", "cargo", "col", "blouson",
                            "gilet", "parka", "trench", "survetement",
                            "debardeur", "polaire", "coffret", "cap"}
            if raw_brand not in generic_words and len(raw_brand) > 2:
                layer1.append(f"#{raw_brand}")
                # Also try brand + second word combo
                if len(words) > 1:
                    second = words[1].lower().strip()
                    if second not in generic_words and len(second) > 2:
                        layer1.append(f"#{raw_brand}{second}")

    # Model detection (match all models, longest first, max 2)
    model_matches = 0
    for model in sorted(MODEL_ALIASES.keys(), key=len, reverse=True):
        if model in title_lower:
            layer1.extend(MODEL_ALIASES[model])
            model_matches += 1
            if model_matches >= 2:
                break

    # === COUCHE 2 : Style / Epoque / Univers ===
    article_type = _detect_article_type(title_lower)
    is_sneaker = article_type == "sneaker"
    universes = _detect_universes(title_lower)

    if is_sneaker and "sneakers" not in universes:
        universes.insert(0, "sneakers")

    # Check for vintage indicators
    if any(w in title_lower for w in ["vintage", "retro", "old", "90s", "80s"]):
        if "vintage" not in universes:
            universes.append("vintage")

    for universe in universes[:3]:  # Max 3 universes
        tags = UNIVERSE_TAGS.get(universe, [])
        layer2.extend(tags[:3])  # Max 3 tags per universe

    # === COUCHE 3 : Descripteurs (couleur, type article, specifiques) ===
    # Couleur (max 2 couleurs)
    color_count = 0
    for color in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        if _word_match(color, title_lower):
            layer3.extend(COLOR_ALIASES[color])
            color_count += 1
            if color_count >= 2:
                break

    # Sneaker-specific: extract colorway/collab name
    if is_sneaker:
        colorways = [
            "cement", "bred", "chicago", "shadow", "royal", "obsidian",
            "mocha", "travis", "og", "anatomy", "koston", "safari",
            "panda", "university", "denim", "infrared", "neon",
        ]
        for cw in colorways:
            if cw in title_lower:
                layer3.append(f"#{cw}")
    else:
        # Clothing/Accessory: descriptive compound tags
        clothing_descriptors = {
            "veste": ["#veste"], "jacket": ["#jacket"],
            "hoodie": ["#hoodie"], "sweat": ["#sweat"],
            "t-shirt": ["#tshirt"], "tee": ["#tee"],
            "pantalon": ["#pantalon"], "pants": ["#pants"],
            "jogging": ["#jogger", "#trackpants"],
            "jogger": ["#jogger", "#trackpants"],
            "chemise": ["#chemise"], "shirt": ["#shirt"],
            "pull": ["#pull", "#sweater"],
            "col roule": ["#colroule"],
            "half zip": ["#halfzip", "#sweathalfzip"],
            "zip": ["#zipup"],
            "maillot": ["#maillot"],
            "jersey": ["#jersey"],
            "short": ["#short"],
            "jean": ["#denim", "#jeans"],
            "cargo": ["#cargo"],
            "sac": ["#sac", "#backpack"],
            "lunettes": ["#lunettes", "#sunglasses"],
            "casquette": ["#cap"],
            "bonnet": ["#beanie"],
            "doudoune": ["#puffer"],
            "manteau": ["#manteau", "#coat"],
            "polo": ["#polo"],
            "cardigan": ["#cardigan"],
            "robe": ["#robe", "#dress"],
            "banner": ["#banner", "#drapeau"],
            "matelasse": ["#vestehiver", "#vesteautomne"],
            "imperme": ["#vesteimpermeable"],
            "col velours": ["#colvelourscotele"],
            "tartan": ["#tartan"],
            "brode": ["#logobrodé"],
            "blouson": ["#blouson", "#jacket"],
            "cuir": ["#cuir", "#leather"],
            "polaire": ["#polaire", "#fleece"],
            "survetement": ["#survetement", "#tracksuit"],
            "gilet": ["#gilet", "#vest"],
            "parka": ["#parka"],
            "trench": ["#trench", "#trenchcoat"],
            "crop": ["#croptop"],
            "debardeur": ["#debardeur", "#tanktop"],
            "bomber": ["#bomber", "#bomberjacket"],
            "blazer": ["#blazer"],
            "cravate": ["#cravate", "#tie"],
            "mocassin": ["#mocassin", "#loafer"],
        }
        for kw in sorted(clothing_descriptors.keys(), key=len, reverse=True):
            if _word_match(kw, title_lower):
                layer3.extend(clothing_descriptors[kw])

    # === POKEMON RULES ===
    _pokemon_names = {
        "pikachu", "dracaufeu", "charizard", "tortank", "blastoise",
        "florizarre", "venusaur", "mewtwo", "mew", "evoli", "eevee",
        "rondoudou", "jigglypuff", "ronflex", "snorlax", "leviator", "gyarados",
        "dracolosse", "dragonite", "electhor", "zapdos", "artikodin", "articuno",
        "sulfura", "moltres", "lokhlass", "lapras", "metamorph", "ditto",
        "lucario", "gardevoir", "ectoplasma", "gengar", "arcanin", "arcanine",
        "rayquaza", "dialga", "palkia", "giratina", "arceus", "darkrai",
        "deoxys", "celebi", "jirachi", "groudon", "kyogre", "reshiram",
        "zekrom", "kyurem", "xerneas", "yveltal", "zygarde", "lunala",
        "solgaleo", "necrozma", "eternatus", "zacian", "zamazenta",
        "amphinobi", "greninja", "noctali", "umbreon", "mentali", "espeon",
        "phyllali", "leafeon", "givrali", "glaceon", "nymphali", "sylveon",
        "aquali", "vaporeon", "voltali", "jolteon", "pyroli", "flareon",
        "tyranocif", "tyranitar", "carchacrok", "garchomp", "libegon", "flygon",
        "drattak", "salamence", "metalosse", "metagross", "brasegali", "blaziken",
        "laggron", "swampert", "jungko", "sceptile", "suicune", "entei", "raikou",
        "latias", "latios", "feunard", "ninetales", "alakazam",
        "mackogneur", "machamp", "demolosse", "houndoom", "absol",
        "torterra", "infernape", "empoleon", "zoroark",
    }
    _pokemon_tcg_triggers = {
        "etb", "booster", "coffret", "display", "tin", "blister",
        "ev9", "ev8", "ev7", "ev6", "ev5", "ev4", "ev3", "ev2", "ev1",
        "151", "ecarlate", "scarlet", "paldea", "obsidienne",
        "tempete argentee", "origine perdue", "couronne zenith",
        "astres radieux", "flammes obsidiennes", "forces temporelles",
        "faille paradoxe", "mascarade crepusculaire", "destinees de paldea",
        "destinees rivales", "zenith supreme", "vmax", "vstar", "gx", "ex",
    }
    _is_pokemon_article = (
        "pokemon" in title_lower or "pokémon" in title_lower
        or any(name in title_lower for name in _pokemon_names)
        or any(kw in title_lower for kw in _pokemon_tcg_triggers)
    )
    if _is_pokemon_article:
        # Rule 1: #pokemon obligatoire
        if "#pokemon" not in [t.lower() for t in layer1 + layer2 + layer3]:
            layer1.insert(0, "#pokemon")
        # Rule 2: premier mot du titre en hashtag
        first_word = title.strip().split()[0].lower() if title.strip() else ""
        first_tag = f"#{first_word}"
        if first_word and len(first_word) > 1 and first_tag not in [t.lower() for t in layer1]:
            layer1.insert(0, first_tag)

    # === ASSEMBLAGE ===
    all_tags = layer1 + layer2 + layer3

    # Add custom user tags
    if custom_tags:
        for tag in custom_tags:
            tag = tag.strip()
            if tag and not tag.startswith("#"):
                tag = f"#{tag}"
            if tag:
                all_tags.append(tag)

    # Deduplicate (case-insensitive), preserve order
    seen = set()
    unique = []
    for h in all_tags:
        h_clean = h.lower().strip()
        if not h_clean or h_clean == "#":
            continue
        # Blocklist filter
        tag_text = h_clean.lstrip("#")
        if _BLOCKED_PATTERN.search(tag_text):
            continue
        if h_clean not in seen:
            seen.add(h_clean)
            unique.append(h_clean)

    # === MINIMUM 10 TAGS : fallback si pas assez ===
    if len(unique) < MIN_HASHTAGS:
        if article_type == "sneaker":
            fallback_pool = FALLBACK_SNEAKER_TAGS
        elif article_type == "gaming":
            fallback_pool = FALLBACK_GAMING_TAGS
        elif article_type == "accessory":
            fallback_pool = FALLBACK_ACCESSORY_TAGS
        else:
            fallback_pool = FALLBACK_CLOTHING_TAGS

        for fb in fallback_pool:
            if len(unique) >= MIN_HASHTAGS:
                break
            fb_clean = fb.lower().strip()
            tag_text = fb_clean.lstrip("#")
            if fb_clean not in seen and not _BLOCKED_PATTERN.search(tag_text):
                seen.add(fb_clean)
                unique.append(fb_clean)

    # Trim to MAX_HASHTAGS
    return unique[:MAX_HASHTAGS]


@app.route("/api/generate-hashtags", methods=["POST"])
@login_required
def generate_hashtags():
    """Generate hashtags v2 for Vinted items.

    Expects JSON:
    {
        "title": "Air Max One Black",               (required)
        "custom_tags": ["MonShop", "Paris"]          (optional)
    }

    Or for batch generation from sheet:
    {
        "from_sheet": true,
        "custom_tags": ["MonShop"]
    }
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "vinted":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs Vinted"}), 403

    data = request.get_json(silent=True) or {}
    custom_tags = data.get("custom_tags", [])

    # Single item mode
    if not data.get("from_sheet"):
        title = data.get("title", "").strip()
        if not title:
            return jsonify({"success": False, "error": "Titre de l'article requis"}), 400

        hashtags = _generate_hashtags_for_item(title, custom_tags)
        return jsonify({
            "success": True,
            "items": [{
                "title": title,
                "hashtags": hashtags,
                "text": " ".join(hashtags),
            }],
        })

    # Batch mode: read from Google Sheet
    mtype = user["monitoring_type"]
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
        return jsonify({"success": False, "error": "Erreur d'authentification Google"}), 500

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:I",
        ).execute()
        rows = result.get("values", [])

        if len(rows) < 2:
            return jsonify({"success": True, "items": [], "message": "Aucun article dans le Sheet"})

        headers = rows[0]
        items = []

        for row in rows[1:]:
            if not row:
                continue
            title = row[0] if len(row) > 0 else ""
            if not title:
                continue

            sale_price_idx = 3 if len(headers) > 4 else 1
            sale_price = row[sale_price_idx] if len(row) > sale_price_idx else ""

            hashtags = _generate_hashtags_for_item(title, custom_tags)
            items.append({
                "title": title,
                "hashtags": hashtags,
                "text": " ".join(hashtags),
                "sold": bool(sale_price and sale_price.strip()),
            })

        return jsonify({"success": True, "items": items})

    except Exception as exc:
        logger.error("Failed to read sheet for hashtags: %s", exc)
        return jsonify({"success": False, "error": "Erreur de lecture du Sheet"}), 500


@app.route("/api/hashtag-categories")
@login_required
def hashtag_categories():
    """Return available hashtag preset categories (kept for backward compat)."""
    categories = [
        {"id": "default", "label": "General"},
    ]
    return jsonify({"success": True, "categories": categories})


# ============================================
# ROUTES - WTS TEMPLATE (Tickets)
# ============================================

@app.route("/api/generate-wts")
@login_required
def generate_wts():
    """Generate a WTS (Want To Sell) template from unsold tickets in the Sheet.

    Format:
    WTS
    Artiste
    Date - Categorie x1 - prix EUR/place

    One line per sheet row where "Prix Vente" column is empty.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs Tickets"}), 403

    if user.get("plan") != "pro":
        return jsonify({"success": False, "error": "Feature reservee au plan Pro"}), 403

    mtype = user["monitoring_type"]
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
        return jsonify({"success": False, "error": "Erreur d'authentification Google"}), 500

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        # Tickets headers: Evenement | Categorie | Lieu | Date | Prix Achat | N Commande | Lien | Compte | Prix Vente | Benefice
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:J",
        ).execute()
        rows = result.get("values", [])

        if len(rows) < 2:
            return jsonify({"success": True, "wts_text": "", "items": [], "message": "Aucun billet dans le Sheet"})

        # Parse unsold tickets
        unsold_items = []
        for row in rows[1:]:
            if not row or not row[0]:
                continue

            event = row[0] if len(row) > 0 else ""
            category = row[1] if len(row) > 1 else ""
            lieu = row[2] if len(row) > 2 else ""
            date = row[3] if len(row) > 3 else ""
            prix_achat = row[4] if len(row) > 4 else ""
            # Index 8 = Prix Vente
            prix_vente = row[8] if len(row) > 8 else ""

            # Skip sold tickets (prix_vente is filled)
            if prix_vente and prix_vente.strip():
                continue

            unsold_items.append({
                "event": event,
                "category": category,
                "lieu": lieu,
                "date": date,
                "prix_achat": prix_achat,
            })

        if not unsold_items:
            return jsonify({
                "success": True,
                "wts_text": "",
                "items": [],
                "message": "Toutes les places sont vendues !",
            })

        # Build WTS text: one line per ticket row
        lines = ["WTS"]
        for item in unsold_items:
            # Format: Artiste
            #         Date - Categorie x1 - prix EUR/place
            price_str = f"{item['prix_achat']}EUR/place" if item["prix_achat"] else ""
            cat_str = item["category"] if item["category"] else ""
            date_str = item["date"] if item["date"] else ""

            parts = []
            if date_str:
                parts.append(date_str)
            if cat_str:
                parts.append(f"{cat_str} x1")
            if price_str:
                parts.append(price_str)

            line = f"{item['event']}\n{' - '.join(parts)}" if parts else item["event"]
            lines.append(line)

        wts_text = "\n\n".join(lines)

        return jsonify({
            "success": True,
            "wts_text": wts_text,
            "items": unsold_items,
            "unsold_count": len(unsold_items),
        })

    except Exception as exc:
        logger.error("Failed to generate WTS: %s", exc)
        return jsonify({"success": False, "error": "Erreur de lecture du Sheet"}), 500


@app.route("/api/generate-wts-ai")
@login_required
def generate_wts_ai():
    """Generate an AI-enhanced WTS post using Claude API.

    Reads unsold tickets from Google Sheets, sends them to Claude to produce
    a compelling, emoji-rich Twitter/X WTS post in French.
    Restricted to monitoring_type='tickets' and plan='pro'.
    Falls back to the basic template if Claude API fails.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs Tickets"}), 403

    if user.get("plan") != "pro":
        return jsonify({"success": False, "error": "Feature reservee au plan Pro"}), 403

    mtype = user["monitoring_type"]
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
        return jsonify({"success": False, "error": "Erreur d'authentification Google"}), 500

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:J",
        ).execute()
        rows = result.get("values", [])

        if len(rows) < 2:
            return jsonify({"success": True, "wts_text": "", "items": [], "message": "Aucun billet dans le Sheet"})

        # Parse unsold tickets
        unsold_items = []
        for row in rows[1:]:
            if not row or not row[0]:
                continue

            event = row[0] if len(row) > 0 else ""
            category = row[1] if len(row) > 1 else ""
            lieu = row[2] if len(row) > 2 else ""
            date = row[3] if len(row) > 3 else ""
            prix_achat = row[4] if len(row) > 4 else ""
            prix_vente = row[8] if len(row) > 8 else ""

            if prix_vente and prix_vente.strip():
                continue

            unsold_items.append({
                "event": event,
                "category": category,
                "lieu": lieu,
                "date": date,
                "prix_achat": prix_achat,
            })

        if not unsold_items:
            return jsonify({
                "success": True,
                "wts_text": "",
                "items": [],
                "message": "Toutes les places sont vendues !",
            })

        # Build ticket list for the prompt
        ticket_lines = []
        for item in unsold_items:
            parts = [f"Evenement: {item['event']}"]
            if item["date"]:
                parts.append(f"Date: {item['date']}")
            if item["category"]:
                parts.append(f"Categorie: {item['category']}")
            if item["lieu"]:
                parts.append(f"Lieu: {item['lieu']}")
            if item["prix_achat"]:
                parts.append(f"Prix: {item['prix_achat']}EUR")
            ticket_lines.append(" | ".join(parts))

        tickets_text = "\n".join(ticket_lines)

        prompt = (
            "Tu es un expert en revente de billets. "
            "Genere un post WTS (Want To Sell) professionnel et accrocheur pour Twitter/X en francais.\n\n"
            f"Voici mes billets non vendus:\n{tickets_text}\n\n"
            "Regles:\n"
            "- Format concis pour Twitter/X (max 280 caracteres si possible, sinon reste court)\n"
            "- Utilise des emojis pertinents (🎟, 📍, 📅, 💰)\n"
            "- Groupe par artiste/evenement si plusieurs billets pour le meme evenement\n"
            "- Mentionne le prix, la date et la categorie\n"
            "- Ajoute \"DM pour info\" a la fin\n"
            "- Commence par \"WTS 🎟\"\n"
            "- Ne mets pas de hashtags\n"
            "- Reponds UNIQUEMENT avec le texte du post, sans explication"
        )

        # Attempt Claude API call
        ai_generated = True
        try:
            if not ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY is not configured")

            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            wts_text = message.content[0].text.strip()
        except Exception as ai_exc:
            logger.warning("Claude API call failed, falling back to basic template: %s", ai_exc)
            ai_generated = False

            # Fallback: build basic WTS text (same logic as /api/generate-wts)
            lines = ["WTS"]
            for item in unsold_items:
                price_str = f"{item['prix_achat']}EUR/place" if item["prix_achat"] else ""
                cat_str = item["category"] if item["category"] else ""
                date_str = item["date"] if item["date"] else ""

                parts = []
                if date_str:
                    parts.append(date_str)
                if cat_str:
                    parts.append(f"{cat_str} x1")
                if price_str:
                    parts.append(price_str)

                line = f"{item['event']}\n{' - '.join(parts)}" if parts else item["event"]
                lines.append(line)

            wts_text = "\n\n".join(lines)

        return jsonify({
            "success": True,
            "wts_text": wts_text,
            "items": unsold_items,
            "unsold_count": len(unsold_items),
            "ai_generated": ai_generated,
        })

    except Exception as exc:
        logger.error("Failed to generate WTS AI: %s", exc)
        return jsonify({"success": False, "error": "Erreur de lecture du Sheet"}), 500


# ============================================
# ROUTES - VINTED SELL TIME STATS
# ============================================

@app.route("/api/vinted-sell-times")
@login_required
def vinted_sell_times():
    """Return sell time stats for Vinted items (date achat -> date vente).

    Reads from the Google Sheet and calculates the delta for each sold item.
    Only works for Pro users who have both purchase and sale dates.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "vinted":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs Vinted"}), 403

    if user.get("plan") != "pro":
        return jsonify({"success": False, "error": "Feature reservee au plan Pro (necessite dates d'achat)"}), 403

    mtype = user["monitoring_type"]
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return jsonify({"success": False, "error": "Aucun compte Gmail connecte"}), 400

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
        return jsonify({"success": False, "error": "Erreur d'authentification Google"}), 500

    try:
        from parsers.vinted import calculate_time_in_stock

        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:I",
        ).execute()
        rows = result.get("values", [])

        if len(rows) < 2:
            return jsonify({"success": True, "items": [], "stats": {}})

        headers = rows[0]
        items = []
        total_days = 0
        sold_count = 0
        fastest = None
        slowest = None

        # Detect column layout
        # Pro: Article | Prix Achat | Date Achat | Prix Vente | Date Vente | Benefice | ROI % | Temps en stock | Compte
        # Starter: Article | Prix Vente | Date Vente | Compte
        is_pro = len(headers) >= 7

        for row in rows[1:]:
            if not row or not row[0]:
                continue

            title = row[0]
            purchase_date = ""
            sale_date = ""
            sale_price = ""

            if is_pro:
                purchase_date = row[2] if len(row) > 2 else ""
                sale_price = row[3] if len(row) > 3 else ""
                sale_date = row[4] if len(row) > 4 else ""
            else:
                sale_price = row[1] if len(row) > 1 else ""
                sale_date = row[2] if len(row) > 2 else ""

            # Normalize dates (DD/MM/YYYY -> YYYY-MM-DD)
            def normalize_date(d):
                if not d:
                    return ""
                d = d.strip()
                m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", d)
                if m:
                    return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
                if re.match(r"\d{4}-\d{2}-\d{2}", d):
                    return d
                return ""

            purchase_date = normalize_date(purchase_date)
            sale_date = normalize_date(sale_date)

            item_data = {
                "title": title,
                "purchase_date": purchase_date,
                "sale_date": sale_date,
                "sold": bool(sale_price and sale_price.strip()),
                "sell_time": None,
            }

            if purchase_date and sale_date and item_data["sold"]:
                time_data = calculate_time_in_stock(purchase_date, sale_date)
                item_data["sell_time"] = time_data
                days = time_data["days"]
                total_days += days
                sold_count += 1
                if fastest is None or days < fastest:
                    fastest = days
                if slowest is None or days > slowest:
                    slowest = days

            items.append(item_data)

        avg_days = round(total_days / sold_count, 1) if sold_count > 0 else 0

        stats = {
            "total_items": len(items),
            "sold_count": sold_count,
            "unsold_count": len(items) - sold_count,
            "avg_sell_days": avg_days,
            "fastest_days": fastest or 0,
            "slowest_days": slowest or 0,
        }

        return jsonify({"success": True, "items": items, "stats": stats})

    except Exception as exc:
        logger.error("Failed to get sell times: %s", exc)
        return jsonify({"success": False, "error": "Erreur de lecture du Sheet"}), 500


# ============================================
# ROUTES - NOTIFICATIONS
# ============================================

@app.route("/api/notifications")
@login_required
def get_notifications():
    """Get notifications for the current user (tickets only)."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": True, "notifications": [], "unread_count": 0})

    unread_only = request.args.get("unread") == "1"
    notifications = db.get_notifications(user_id, limit=30, unread_only=unread_only)
    unread_count = db.get_unread_notification_count(user_id)
    return jsonify({
        "success": True,
        "notifications": notifications,
        "unread_count": unread_count,
    })


@app.route("/api/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    """Mark notification(s) as read (tickets only)."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Non disponible"}), 403

    data = request.get_json(silent=True) or {}

    notif_id = data.get("id")
    if notif_id:
        db.mark_notification_read(int(notif_id), user_id)
    else:
        db.mark_all_notifications_read(user_id)

    return jsonify({"success": True})


@app.route("/api/update-alert-settings", methods=["POST"])
@login_required
def update_alert_settings():
    """Update alert thresholds (days before event, dormant stock days)."""
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}

    updates = {}

    if "alert_days_before" in data:
        val = int(data["alert_days_before"])
        if 1 <= val <= 60:
            updates["alert_days_before"] = val

    if "dormant_days_threshold" in data:
        val = int(data["dormant_days_threshold"])
        if 1 <= val <= 365:
            updates["dormant_days_threshold"] = val

    if updates:
        db.update_user(user_id, **updates)
        logger.info("User id=%d updated alert settings: %s", user_id, updates)
        return jsonify({"success": True, **updates})

    return jsonify({"success": False, "error": "Aucun parametre valide"}), 400


@app.route("/api/organize-tabs", methods=["POST"])
@login_required
def organize_tabs():
    """Organize tickets into per-artist/event Sheet tabs (Pro only)."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs Tickets"}), 403

    if user.get("plan") != "pro":
        return jsonify({"success": False, "error": "Feature reservee au plan Pro"}), 403

    try:
        from scanner import organize_ticket_tabs
        result = organize_ticket_tabs(user_id)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 400
        return jsonify({"success": True, **result})
    except Exception as exc:
        logger.error("organize_tabs failed for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


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


@app.route("/api/admin/clients")
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


# ============================================
# ALERT HELPERS
# ============================================

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

    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id, monitoring_type=monitoring_type)
    if not accounts or not sheets:
        return

    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    creds = _build_credentials_from_account(primary)
    if not creds:
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

                # Time to scan
                try:
                    from scanner import scan_user
                    orders = scan_user(user_id)
                    logger.info("Auto-scan user id=%d: %d orders found", user_id, orders)
                except Exception as exc:
                    logger.error("Auto-scan failed for user id=%d: %s", user_id, exc)

                # Check alerts after scan
                try:
                    _check_alerts_for_user(user)
                except Exception as exc:
                    logger.error("Alert check failed for user id=%d: %s", user_id, exc)

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
