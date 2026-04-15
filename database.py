"""
Billets & Vinted Monitor MVP - Database Layer (Supabase/PostgreSQL)
Drop-in replacement for the SQLite version. Same public API, backed by Supabase.
"""

import os
import uuid
import logging
import secrets
import string
import threading
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client, Client
from crypto import encrypt_token, decrypt_token, is_token_encrypted

logger = logging.getLogger(__name__)

# ============================================
# CLIENT INIT
# ============================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_thread_local = threading.local()


def _get_client() -> Client:
    """Thread-local Supabase client — one instance per thread to avoid httpx concurrency issues."""
    if not hasattr(_thread_local, "client") or _thread_local.client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        _thread_local.client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialized for %s", SUPABASE_URL)
    return _thread_local.client


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


# ============================================
# COMPATIBILITY LAYER
# ============================================

def init_db() -> None:
    """No-op for Supabase — schema is managed via SQL Editor / migrations."""
    _get_client()
    logger.info("Supabase database ready (schema managed externally)")


def get_db():
    """Compatibility shim — returns the Supabase client."""
    return _get_client()


def get_request_db():
    """Compatibility shim — returns the Supabase client."""
    return _get_client()


def close_db(e=None) -> None:
    """No-op for Supabase — connection pooling is handled by the SDK."""
    pass


def migrate_encrypt_tokens() -> int:
    """One-time migration: encrypt any plaintext OAuth tokens in gmail_accounts."""
    from crypto import _encryption_enabled
    if not _encryption_enabled:
        logger.warning("migrate_encrypt_tokens: encryption not enabled, skipping")
        return 0

    sb = _get_client()
    migrated = 0
    response = sb.table("gmail_accounts").select("id, oauth_token, oauth_refresh_token").execute()
    for row in response.data:
        token = row.get("oauth_token") or ""
        refresh = row.get("oauth_refresh_token") or ""
        needs_update = False
        if token and not is_token_encrypted(token):
            token = encrypt_token(token)
            needs_update = True
        if refresh and not is_token_encrypted(refresh):
            refresh = encrypt_token(refresh)
            needs_update = True
        if needs_update:
            sb.table("gmail_accounts").update({
                "oauth_token": token,
                "oauth_refresh_token": refresh,
            }).eq("id", row["id"]).execute()
            migrated += 1

    if migrated:
        logger.info("migrate_encrypt_tokens: encrypted tokens for %d accounts", migrated)
    else:
        logger.info("migrate_encrypt_tokens: all tokens already encrypted")
    return migrated


# ============================================
# USERS
# ============================================

def create_user(email: str, name: str, picture: str, monitoring_type: str,
                plan: str = "starter", billing_period: str = "monthly", conn=None) -> int:
    """Create a new user. Returns the user id."""
    if plan not in ("starter", "pro"):
        plan = "starter"
    if billing_period not in ("monthly", "yearly"):
        billing_period = "monthly"
    sb = _get_client()
    response = sb.table("users").insert({
        "email": email,
        "name": name,
        "picture": picture,
        "monitoring_type": monitoring_type,
        "plan": plan,
        "billing_period": billing_period,
    }).execute()
    user_id = response.data[0]["id"]
    logger.info("Created user id=%d email=%s type=%s plan=%s billing=%s",
                user_id, email, monitoring_type, plan, billing_period)
    return user_id


def get_user_by_id(user_id: int, conn=None) -> Optional[dict]:
    """Get a user by id."""
    sb = _get_client()
    response = sb.table("users").select("*").eq("id", user_id).execute()
    return response.data[0] if response.data else None


def get_user_by_email(email: str, conn=None) -> Optional[dict]:
    """Get a user by email."""
    sb = _get_client()
    response = sb.table("users").select("*").eq("email", email).execute()
    return response.data[0] if response.data else None


def get_all_users(conn=None) -> list[dict]:
    """Get all users (excludes sensitive fields)."""
    sb = _get_client()
    response = sb.table("users").select(
        "id, email, name, picture, monitoring_type, plan, billing_period, scan_frequency, created_at"
    ).order("created_at", desc=True).execute()
    return response.data


def get_all_users_with_stats(conn=None) -> list[dict]:
    """Get all users with aggregated stats via RPC."""
    sb = _get_client()
    response = sb.rpc("get_users_with_stats").execute()
    return response.data


def update_user(user_id: int, conn=None, **kwargs) -> bool:
    """Update user fields. Pass field=value pairs."""
    if not kwargs:
        return False
    allowed = {
        "email", "name", "picture", "monitoring_type", "plan",
        "scan_frequency", "alert_days_before", "dormant_days_threshold",
        "company_name", "company_address", "company_phone", "company_email",
        "company_siret", "company_tva_number", "company_iban", "company_bic",
        "company_tva_rate", "invoice_prefix", "invoice_counter", "invoice_footer",
        "trial_started_at", "trial_ends_at", "is_trial_active",
        "referral_code", "referred_by", "referral_count",
        "monitoring_paused", "onboarding_complete", "billing_period", "monthly_costs",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    sb = _get_client()
    sb.table("users").update(fields).eq("id", user_id).execute()
    return True


# ============================================
# TRIAL & REFERRAL MANAGEMENT
# ============================================

def activate_trial(user_id: int, conn=None) -> bool:
    """Activate a 14-day free trial for a user."""
    now = datetime.utcnow()
    trial_end = now + timedelta(days=14)
    sb = _get_client()
    sb.table("users").update({
        "trial_started_at": now.isoformat(),
        "trial_ends_at": trial_end.isoformat(),
        "is_trial_active": 1,
        "plan": "pro",
    }).eq("id", user_id).execute()
    logger.info("Activated trial for user id=%d, expires at %s", user_id, trial_end.isoformat())
    return True


def check_trial_expired(user_id: int, conn=None) -> bool:
    """Check if a user's trial has expired."""
    sb = _get_client()
    response = sb.table("users").select("trial_ends_at, is_trial_active").eq("id", user_id).execute()
    if not response.data:
        return False
    row = response.data[0]
    if not row.get("is_trial_active"):
        return False
    if not row.get("trial_ends_at"):
        return False
    trial_end = datetime.fromisoformat(row["trial_ends_at"].replace("Z", "+00:00").replace("+00:00", ""))
    return datetime.utcnow() > trial_end


def expire_trial(user_id: int, conn=None) -> bool:
    """Expire a user's trial, reverting plan to 'starter'."""
    sb = _get_client()
    sb.table("users").update({
        "is_trial_active": 0,
        "plan": "starter",
    }).eq("id", user_id).execute()
    logger.info("Expired trial for user id=%d", user_id)
    return True


def generate_referral_code(user_id: int, conn=None) -> str:
    """Generate a unique 8-character referral code for a user."""
    sb = _get_client()
    while True:
        code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        existing = sb.table("users").select("id").eq("referral_code", code).execute()
        if not existing.data:
            break
    sb.table("users").update({"referral_code": code}).eq("id", user_id).execute()
    logger.info("Generated referral code for user id=%d: %s", user_id, code)
    return code


def apply_referral(user_id: int, referral_code: str, conn=None) -> bool:
    """Link a new user to a referrer and activate trial for both."""
    sb = _get_client()
    referrer = sb.table("users").select("id").eq("referral_code", referral_code).execute()
    if not referrer.data:
        return False
    referrer_id = referrer.data[0]["id"]
    now = datetime.utcnow()
    sb.table("users").update({
        "referred_by": referral_code,
        "is_trial_active": 1,
        "plan": "pro",
        "trial_started_at": now.isoformat(),
        "trial_ends_at": (now + timedelta(days=14)).isoformat(),
    }).eq("id", user_id).execute()
    sb.rpc("increment_referral_count", {"p_user_id": referrer_id}).execute()
    logger.info("Applied referral code %s to user id=%d, referrer id=%d",
                referral_code, user_id, referrer_id)
    return True


def get_user_by_referral_code(code: str, conn=None) -> Optional[dict]:
    """Look up a user by their referral code."""
    sb = _get_client()
    response = sb.table("users").select("*").eq("referral_code", code).execute()
    return response.data[0] if response.data else None


# ============================================
# GMAIL ACCOUNTS
# ============================================

def create_gmail_account(
    user_id: int, email: str, oauth_token: str, oauth_refresh_token: str,
    token_expiry: Optional[str] = None, is_primary: bool = False, conn=None,
) -> int:
    """Create a gmail account entry. Returns the account id."""
    enc_token = encrypt_token(oauth_token)
    enc_refresh = encrypt_token(oauth_refresh_token)
    sb = _get_client()
    response = sb.table("gmail_accounts").insert({
        "user_id": user_id,
        "email": email,
        "oauth_token": enc_token,
        "oauth_refresh_token": enc_refresh,
        "token_expiry": token_expiry,
        "is_primary": 1 if is_primary else 0,
    }).execute()
    account_id = response.data[0]["id"]
    logger.info("Created gmail_account id=%d user=%d email=%s", account_id, user_id, email)
    return account_id


def _decrypt_account_tokens(account: dict) -> dict:
    """Decrypt oauth_token and oauth_refresh_token in a gmail_account dict."""
    if account.get("oauth_token"):
        account["oauth_token"] = decrypt_token(account["oauth_token"])
    if account.get("oauth_refresh_token"):
        account["oauth_refresh_token"] = decrypt_token(account["oauth_refresh_token"])
    return account


def get_gmail_account_by_id(account_id: int, conn=None) -> Optional[dict]:
    """Get a gmail account by id."""
    sb = _get_client()
    response = sb.table("gmail_accounts").select("*").eq("id", account_id).execute()
    return _decrypt_account_tokens(response.data[0]) if response.data else None


def get_gmail_accounts(user_id: int, conn=None) -> list[dict]:
    """Get all gmail accounts for a user."""
    sb = _get_client()
    response = (sb.table("gmail_accounts")
                .select("*")
                .eq("user_id", user_id)
                .order("is_primary", desc=True)
                .order("created_at")
                .execute())
    return [_decrypt_account_tokens(r) for r in response.data]


def update_gmail_account_tokens(account_id: int, oauth_token: str,
                                 token_expiry: Optional[str] = None,
                                 user_id: int = None, conn=None) -> bool:
    """Update the OAuth token (after refresh)."""
    enc_token = encrypt_token(oauth_token)
    sb = _get_client()
    query = sb.table("gmail_accounts").update({
        "oauth_token": enc_token,
        "token_expiry": token_expiry,
    }).eq("id", account_id)
    if user_id:
        query = query.eq("user_id", user_id)
    query.execute()
    return True


def update_gmail_account_refresh_token(account_id: int, oauth_refresh_token: str,
                                        user_id: int = None, conn=None) -> bool:
    """Update the refresh token."""
    enc_refresh = encrypt_token(oauth_refresh_token)
    sb = _get_client()
    query = sb.table("gmail_accounts").update({
        "oauth_refresh_token": enc_refresh,
    }).eq("id", account_id)
    if user_id:
        query = query.eq("user_id", user_id)
    query.execute()
    return True


def delete_gmail_account(account_id: int, user_id: int = None, conn=None) -> bool:
    """Delete a gmail account."""
    sb = _get_client()
    query = sb.table("gmail_accounts").delete().eq("id", account_id)
    if user_id:
        query = query.eq("user_id", user_id)
    query.execute()
    return True


# ============================================
# SPREADSHEETS
# ============================================

def create_spreadsheet(
    user_id: int, spreadsheet_id: str, spreadsheet_url: str,
    is_auto_created: bool = True, monitoring_type: str = "tickets", conn=None,
) -> int:
    """Create a spreadsheet entry. Returns the row id."""
    sb = _get_client()
    response = sb.table("spreadsheets").insert({
        "user_id": user_id,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
        "is_auto_created": 1 if is_auto_created else 0,
        "monitoring_type": monitoring_type,
    }).execute()
    row_id = response.data[0]["id"]
    logger.info("Created spreadsheet id=%d user=%d type=%s sheet=%s",
                row_id, user_id, monitoring_type, spreadsheet_id)
    return row_id


def get_spreadsheets(user_id: int, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get spreadsheets for a user, optionally filtered by monitoring_type."""
    sb = _get_client()
    query = sb.table("spreadsheets").select("*").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.order("created_at", desc=True).execute()
    return response.data


def get_primary_spreadsheet(user_id: int, monitoring_type: str = None, conn=None) -> Optional[dict]:
    """Get the most recent spreadsheet for a user, optionally filtered by type."""
    sb = _get_client()
    query = sb.table("spreadsheets").select("*").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.order("created_at", desc=True).limit(1).execute()
    return response.data[0] if response.data else None


def delete_spreadsheet(sheet_row_id: int, user_id: int = None, conn=None) -> bool:
    """Delete a spreadsheet entry."""
    sb = _get_client()
    query = sb.table("spreadsheets").delete().eq("id", sheet_row_id)
    if user_id:
        query = query.eq("user_id", user_id)
    query.execute()
    return True


# ============================================
# SCAN LOGS
# ============================================

def create_scan_log(
    user_id: int, scan_type: str, gmail_account_id: Optional[int] = None,
    orders_found: int = 0, status: str = "pending", error_message: Optional[str] = None,
    monitoring_type: str = "tickets", conn=None,
) -> int:
    """Create a scan log entry. Returns the log id."""
    sb = _get_client()
    data = {
        "user_id": user_id,
        "scan_type": scan_type,
        "orders_found": orders_found,
        "status": status,
        "error_message": error_message,
        "monitoring_type": monitoring_type,
    }
    if gmail_account_id is not None:
        data["gmail_account_id"] = gmail_account_id
    response = sb.table("scan_logs").insert(data).execute()
    return response.data[0]["id"]


def update_scan_log(log_id: int, orders_found: int, status: str,
                     error_message: Optional[str] = None, user_id: int = None, conn=None) -> bool:
    """Update a scan log after completion."""
    sb = _get_client()
    query = sb.table("scan_logs").update({
        "orders_found": orders_found,
        "status": status,
        "error_message": error_message,
    }).eq("id", log_id)
    if user_id:
        query = query.eq("user_id", user_id)
    query.execute()
    return True


def get_scan_logs(user_id: int, limit: int = 20, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get recent scan logs for a user."""
    sb = _get_client()
    query = sb.table("scan_logs").select("*").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.order("scanned_at", desc=True).limit(limit).execute()
    return response.data


def get_last_scan(user_id: int, monitoring_type: str = None, conn=None) -> Optional[dict]:
    """Get the most recent scan log for a user."""
    sb = _get_client()
    query = sb.table("scan_logs").select("*").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.order("scanned_at", desc=True).limit(1).execute()
    return response.data[0] if response.data else None


# ============================================
# PROCESSED ORDERS
# ============================================

def create_processed_order(
    user_id: int, order_number: str, source: str, email_id: str,
    monitoring_type: str = "tickets", conn=None,
) -> Optional[int]:
    """Record a processed order. Returns row id or None if duplicate."""
    sb = _get_client()
    # Check for duplicate first (unique index: user_id + email_id + monitoring_type)
    existing = (sb.table("processed_orders")
                .select("id")
                .eq("user_id", user_id)
                .eq("email_id", email_id)
                .eq("monitoring_type", monitoring_type)
                .execute())
    if existing.data:
        return None
    response = sb.table("processed_orders").insert({
        "user_id": user_id,
        "order_number": order_number,
        "source": source,
        "email_id": email_id,
        "monitoring_type": monitoring_type,
    }).execute()
    return response.data[0]["id"]


def is_order_processed(user_id: int, email_id: str, monitoring_type: str = None, conn=None) -> bool:
    """Check if an email has already been processed."""
    sb = _get_client()
    query = sb.table("processed_orders").select("id").eq("user_id", user_id).eq("email_id", email_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.limit(1).execute()
    return bool(response.data)


def get_processed_orders_count(user_id: int, monitoring_type: str = None, conn=None) -> int:
    """Get total processed orders count for a user."""
    sb = _get_client()
    query = sb.table("processed_orders").select("id", count="exact").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.execute()
    return response.count if response.count is not None else len(response.data)


def get_processed_orders(user_id: int, limit: int = 50, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get recent processed orders for a user."""
    sb = _get_client()
    query = sb.table("processed_orders").select("*").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    response = query.order("processed_at", desc=True).limit(limit).execute()
    return response.data


def get_processed_email_ids(user_id: int, monitoring_type: str, conn=None) -> set:
    """Return the set of all email_ids already processed for a user+type."""
    sb = _get_client()
    response = (sb.table("processed_orders")
                .select("email_id")
                .eq("user_id", user_id)
                .eq("monitoring_type", monitoring_type)
                .execute())
    return {r["email_id"] for r in response.data}


# ============================================
# NOTIFICATIONS
# ============================================

def create_notification(
    user_id: int, notif_type: str, title: str, message: str = "",
    reference_key: str = "", monitoring_type: str = "tickets", conn=None,
) -> Optional[int]:
    """Create a notification. reference_key prevents duplicates."""
    sb = _get_client()
    if reference_key:
        existing = (sb.table("notifications")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("reference_key", reference_key)
                    .eq("read", 0)
                    .execute())
        if existing.data:
            return None
    response = sb.table("notifications").insert({
        "user_id": user_id,
        "type": notif_type,
        "title": title,
        "message": message,
        "reference_key": reference_key,
        "monitoring_type": monitoring_type,
    }).execute()
    return response.data[0]["id"]


def get_notifications(user_id: int, limit: int = 30, offset: int = 0,
                       unread_only: bool = False, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get notifications for a user with pagination."""
    sb = _get_client()
    query = sb.table("notifications").select("*").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    if unread_only:
        query = query.eq("read", 0)
    response = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return response.data


def get_notifications_count(user_id: int, unread_only: bool = False,
                             monitoring_type: str = None, conn=None) -> int:
    """Get total count of notifications."""
    sb = _get_client()
    query = sb.table("notifications").select("id", count="exact").eq("user_id", user_id)
    if monitoring_type:
        query = query.eq("monitoring_type", monitoring_type)
    if unread_only:
        query = query.eq("read", 0)
    response = query.execute()
    return response.count if response.count is not None else len(response.data)


def get_unread_notification_count(user_id: int, monitoring_type: str = None, conn=None) -> int:
    """Get count of unread notifications."""
    return get_notifications_count(user_id, unread_only=True, monitoring_type=monitoring_type)


def mark_notification_read(notification_id: int, user_id: int, conn=None) -> bool:
    """Mark a single notification as read."""
    sb = _get_client()
    sb.table("notifications").update({"read": 1}).eq("id", notification_id).eq("user_id", user_id).execute()
    return True


def mark_all_notifications_read(user_id: int, conn=None) -> bool:
    """Mark all notifications as read for a user."""
    sb = _get_client()
    sb.table("notifications").update({"read": 1}).eq("user_id", user_id).eq("read", 0).execute()
    return True


# ============================================
# SERVICES
# ============================================

def get_services(user_email: str, conn=None) -> list[dict]:
    """Get all services for a user, ordered by position."""
    sb = _get_client()
    response = (sb.table("services")
                .select("*")
                .eq("user_email", user_email)
                .order("position")
                .order("created_at")
                .execute())
    return response.data


def create_service(user_email: str, name: str, unit_price_ht: float = 0.0,
                   tva_rate: float = 20.0, description: str = "", conn=None) -> dict:
    """Create a new service. Returns the created service dict."""
    service_id = str(uuid.uuid4())
    sb = _get_client()
    # Get next position
    pos_resp = (sb.table("services")
                .select("position")
                .eq("user_email", user_email)
                .order("position", desc=True)
                .limit(1)
                .execute())
    position = (pos_resp.data[0]["position"] + 1) if pos_resp.data else 0

    response = sb.table("services").insert({
        "id": service_id,
        "user_email": user_email,
        "name": name,
        "unit_price_ht": unit_price_ht,
        "tva_rate": tva_rate,
        "description": description,
        "position": position,
    }).execute()
    logger.info("Created service id=%s for user=%s", service_id, user_email)
    return response.data[0]


def update_service(service_id: str, user_email: str, conn=None, **kwargs) -> bool:
    """Update a service. Only updates allowed fields."""
    allowed = {"name", "unit_price_ht", "tva_rate", "description", "position"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    sb = _get_client()
    sb.table("services").update(fields).eq("id", service_id).eq("user_email", user_email).execute()
    return True


def delete_service(service_id: str, user_email: str, conn=None) -> bool:
    """Delete a service, checking ownership."""
    sb = _get_client()
    response = sb.table("services").delete().eq("id", service_id).eq("user_email", user_email).execute()
    return bool(response.data)


def increment_invoice_counter(user_id: int, conn=None) -> int:
    """Atomically increment and return the new invoice counter via RPC."""
    sb = _get_client()
    response = sb.rpc("increment_invoice_counter", {"p_user_id": user_id}).execute()
    return response.data if isinstance(response.data, int) else 1


# ============================================
# VINTED ACCOUNTS
# ============================================

def create_vinted_account(
    user_id: int,
    label: str,
    refresh_token_encrypted: str,
    vinted_user_id: str,
    vinted_username: str,
    domain: str = "fr",
    conn=None,
) -> dict:
    """Create a new Vinted account entry. Returns the created row."""
    sb = _get_client()
    response = sb.table("vinted_accounts").insert({
        "user_id": user_id,
        "label": label or "",
        "refresh_token": refresh_token_encrypted,
        "vinted_user_id": vinted_user_id,
        "vinted_username": vinted_username,
        "domain": domain,
    }).execute()
    logger.info("Created vinted_account for user=%s (@%s)", user_id, vinted_username)
    return response.data[0]


def get_vinted_accounts(user_id: int, conn=None) -> list[dict]:
    """Get all Vinted accounts for a user."""
    sb = _get_client()
    response = (sb.table("vinted_accounts")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=False)
                .execute())
    return response.data


def get_vinted_account(user_id: int, account_id: int, conn=None) -> Optional[dict]:
    """Get a single Vinted account by id, verifying ownership."""
    sb = _get_client()
    response = (sb.table("vinted_accounts")
                .select("*")
                .eq("id", account_id)
                .eq("user_id", user_id)
                .execute())
    return response.data[0] if response.data else None


def update_vinted_account(user_id: int, account_id: int, conn=None, **kwargs) -> bool:
    """Update allowed fields on a Vinted account."""
    allowed = {"label", "refresh_token", "vinted_username", "domain"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    sb = _get_client()
    sb.table("vinted_accounts").update(fields).eq("id", account_id).eq("user_id", user_id).execute()
    return True


def delete_vinted_account(user_id: int, account_id: int, conn=None) -> bool:
    """Delete a Vinted account, verifying ownership."""
    sb = _get_client()
    response = (sb.table("vinted_accounts")
                .delete()
                .eq("id", account_id)
                .eq("user_id", user_id)
                .execute())
    return bool(response.data)


# ============================================
# EXTENSION — VINTED SESSIONS
# ============================================

def upsert_vinted_session(user_id: int, token: str, domain: str, conn=None) -> bool:
    """Insert or update the Vinted CSRF token for a user."""
    sb = _get_client()
    sb.table("vinted_sessions").upsert({
        "user_id": user_id,
        "token": token,
        "domain": domain,
        "synced_at": _now_iso(),
    }, on_conflict="user_id").execute()
    return True


def get_vinted_session(user_id: int, conn=None) -> Optional[dict]:
    """Get the current Vinted session for a user."""
    sb = _get_client()
    response = sb.table("vinted_sessions").select("*").eq("user_id", user_id).execute()
    return response.data[0] if response.data else None


def delete_vinted_session(user_id: int, conn=None) -> bool:
    """Delete the Vinted session for a user."""
    sb = _get_client()
    sb.table("vinted_sessions").delete().eq("user_id", user_id).execute()
    return True


# ============================================
# EXTENSION — LOGS
# ============================================

def create_extension_log(
    user_id: int, action_type: str, item_id: Optional[str] = None,
    target_user_id: Optional[str] = None, status: str = "ok",
    error: Optional[str] = None, conn=None,
) -> int:
    """Create an extension activity log entry. Returns the log id."""
    sb = _get_client()
    response = sb.table("extension_logs").insert({
        "user_id": user_id,
        "action_type": action_type,
        "item_id": item_id,
        "target_user_id": target_user_id,
        "status": status,
        "error": error,
    }).execute()
    return response.data[0]["id"]


def get_extension_logs(user_id: int, limit: int = 50, conn=None) -> list[dict]:
    """Get recent extension logs for a user."""
    sb = _get_client()
    response = (sb.table("extension_logs")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute())
    return response.data


# ============================================
# EXTENSION — CONFIG
# ============================================

def get_extension_config(user_id: int, conn=None) -> dict:
    """Get extension config for a user."""
    sb = _get_client()
    response = sb.table("users").select(
        "ext_secret, ext_msg_enabled, ext_msg_template, ext_msg_quota_daily, ext_poll_interval_min"
    ).eq("id", user_id).execute()
    if not response.data:
        return {}
    row = response.data[0]
    return {
        "ext_secret": row.get("ext_secret") or "",
        "msg_enabled": bool(row.get("ext_msg_enabled")),
        "msg_template": row.get("ext_msg_template") or "",
        "msg_quota_daily": row.get("ext_msg_quota_daily") or 50,
        "poll_interval_min": row.get("ext_poll_interval_min") or 5,
    }


def update_extension_config(user_id: int, conn=None, **kwargs) -> bool:
    """Update extension config fields on users table."""
    allowed = {
        "ext_secret", "ext_msg_enabled", "ext_msg_template",
        "ext_msg_quota_daily", "ext_poll_interval_min",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    if "ext_secret" in fields and fields["ext_secret"]:
        import hashlib
        fields["ext_secret_hash"] = hashlib.sha256(fields["ext_secret"].encode()).hexdigest()
    sb = _get_client()
    sb.table("users").update(fields).eq("id", user_id).execute()
    return True
