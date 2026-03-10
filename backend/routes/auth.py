"""
Authentication routes: OAuth, Gmail accounts, session management.
"""

import logging
from typing import Optional
from flask import Blueprint, session, redirect, url_for, request, jsonify, flash
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import database as db
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_URL, SCOPES
from helpers import login_required

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


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


@auth_bp.route("/auth/google")
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

    billing = request.args.get("billing", "monthly")
    if billing not in ("monthly", "yearly"):
        billing = "monthly"

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
    session["oauth_billing"] = billing

    return redirect(authorization_url)


@auth_bp.route("/oauth/callback")
def oauth_callback():
    """Handle OAuth callback after user grants access.

    Creates or updates user, creates gmail_account, creates spreadsheet.
    """
    # Validate OAuth state to prevent CSRF
    stored_state = session.pop("oauth_state", None)
    received_state = request.args.get("state")
    if not stored_state or stored_state != received_state:
        flash("Session invalide. Veuillez reessayer.", "error")
        return redirect(url_for("login"))

    monitoring_type = session.pop("oauth_monitoring_type", "tickets")
    plan = session.pop("oauth_plan", "starter")
    billing = session.pop("oauth_billing", "monthly")

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
    is_new_user = False
    if user:
        user_id = user["id"]
        # Update profile info + monitoring_type/plan/billing when user switches
        db.update_user(user_id, name=name, picture=picture,
                       monitoring_type=monitoring_type, plan=plan)
    else:
        user_id = db.create_user(email, name, picture, monitoring_type, plan, billing_period=billing)
        is_new_user = True

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

    # For new users: activate 14-day trial and generate referral code
    if is_new_user:
        db.activate_trial(user_id)
        db.generate_referral_code(user_id)
        logger.info("New user id=%d: trial activated and referral code generated", user_id)

    # Create spreadsheet if user has none for THIS monitoring_type
    from routes.sheets import _create_spreadsheet_for_user
    sheets_for_type = db.get_spreadsheets(user_id, monitoring_type=monitoring_type)
    if not sheets_for_type:
        _create_spreadsheet_for_user(user_id, monitoring_type, plan)

    # Regenerate session to prevent session fixation
    session.clear()
    session["user_id"] = user_id
    session["user_email"] = email
    session["user_name"] = name
    session["user_picture"] = picture

    logger.info("User logged in: id=%d email=%s type=%s", user_id, email, monitoring_type)
    return redirect(url_for("dashboard"))


@auth_bp.route("/api/add-gmail", methods=["POST"])
@login_required
def add_gmail():
    """Generate an OAuth link for adding a new Gmail account.

    Returns JSON with the authorization URL.
    """
    user_id = session["user_id"]

    # Plan limits: Starter = 1 Gmail, Pro = 4 Gmail
    user = db.get_user_by_id(user_id)
    existing_accounts = db.get_gmail_accounts(user_id)
    if user and user.get("plan") == "starter":
        if len(existing_accounts) >= 1:
            return jsonify({
                "success": False,
                "error": "Le plan Starter est limite a 1 compte Gmail. Passez au Pro pour en ajouter.",
            }), 403
    elif user and user.get("plan") == "pro":
        if len(existing_accounts) >= 4:
            return jsonify({
                "success": False,
                "error": "Le plan Pro est limite a 4 comptes Gmail.",
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


@auth_bp.route("/oauth/add-gmail/callback")
@login_required
def add_gmail_callback():
    """Handle OAuth callback for adding an additional Gmail account."""
    # Validate OAuth state to prevent CSRF
    stored_state = session.pop("add_gmail_state", None)
    received_state = request.args.get("state")
    if not stored_state or stored_state != received_state:
        flash("Session invalide. Veuillez reessayer.", "error")
        return redirect(url_for("dashboard"))

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


@auth_bp.route("/api/gmail-accounts/<int:account_id>", methods=["DELETE"])
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


@auth_bp.route("/logout")
def logout():
    """Clear session and redirect to landing."""
    session.clear()
    return redirect(url_for("index"))
