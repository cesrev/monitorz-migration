"""
Billets & Vinted Monitor MVP - Email Scanner
Scans Gmail accounts via the API, parses orders, writes to Google Sheets.
Uses user OAuth credentials for both Gmail and Sheets access.
"""

import base64
import logging
import re
from datetime import datetime
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import database as db
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SCOPES
from parsers.tickets import (
    parse_ticketmaster_email,
    parse_roland_garros_email,
    parse_stade_de_france_email,
    TICKET_QUERIES,
)
from parsers.vinted import (
    parse_vinted_sale_email,
    parse_vinted_purchase_email,
    parse_vinted_email,
    find_matching_item,
    calculate_benefit,
    calculate_time_in_stock,
    VINTED_SALE_QUERIES,
    VINTED_PURCHASE_QUERIES,
)

logger = logging.getLogger(__name__)


# ============================================
# CREDENTIAL HELPERS
# ============================================

def _build_credentials(account: dict, user: dict) -> Optional[Credentials]:
    """Build google.oauth2 Credentials from a gmail_account row.

    If the token is expired, it is refreshed and the DB is updated.
    """
    token = account.get("oauth_token")
    refresh_token = account.get("oauth_refresh_token")

    if not token and not refresh_token:
        logger.warning("No tokens for gmail_account id=%d", account["id"])
        return None

    creds = Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            expiry_str = creds.expiry.isoformat() if creds.expiry else None
            db.update_gmail_account_tokens(account["id"], creds.token, expiry_str)
            if creds.refresh_token != refresh_token:
                db.update_gmail_account_refresh_token(account["id"], creds.refresh_token)
            logger.info("Refreshed token for gmail_account id=%d", account["id"])
        except Exception as exc:
            logger.error("Token refresh failed for account id=%d: %s", account["id"], exc)
            return None

    return creds


def _get_user_credentials(user_id: int) -> Optional[Credentials]:
    """Get credentials from the user's primary gmail account (for Sheets access)."""
    accounts = db.get_gmail_accounts(user_id)
    if not accounts:
        return None

    user = db.get_user_by_id(user_id)
    if not user:
        return None

    # Use primary account first, fallback to first
    primary = next((a for a in accounts if a["is_primary"]), accounts[0])
    return _build_credentials(primary, user)


# ============================================
# GMAIL HELPERS
# ============================================

def _extract_html_from_payload(payload: dict) -> str:
    """Recursively extract HTML content from a Gmail message payload."""
    html_content = ""

    if "parts" in payload:
        for part in payload["parts"]:
            result = _extract_html_from_payload(part)
            if result:
                html_content = result
    else:
        mime_type = payload.get("mimeType", "")
        if "html" in mime_type:
            body = payload.get("body", {})
            data = body.get("data", "")
            if data:
                html_content = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    return html_content


def _get_header(headers: list[dict], name: str) -> str:
    """Extract a header value from Gmail message headers."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


# ============================================
# SHEET WRITING
# ============================================

TICKET_HEADERS = [
    "Événement", "Catégorie", "Lieu", "Date", "Prix Achat",
    "N° Commande", "Lien", "Compte", "Prix Vente", "Bénéfice",
]

VINTED_HEADERS_STARTER = [
    "Article", "Prix Vente", "Date Vente", "Compte",
]

VINTED_HEADERS_PRO = [
    "Article", "Prix Achat", "Date Achat", "Prix Vente", "Date Vente",
    "Benefice", "ROI %", "Temps en stock", "Compte",
]


def _ensure_sheet_headers(sheets_service, spreadsheet_id: str, monitoring_type: str, plan: str = "starter") -> None:
    """Ensure the first row has correct headers."""
    if monitoring_type == "tickets":
        headers = TICKET_HEADERS
    elif plan == "pro":
        headers = VINTED_HEADERS_PRO
    else:
        headers = VINTED_HEADERS_STARTER

    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A1:J1",
        ).execute()
        existing = result.get("values", [[]])[0]
        if existing and existing[0] == headers[0]:
            return  # headers already set
    except Exception:
        pass  # sheet might not exist yet, we'll write anyway

    end_col = chr(ord("A") + len(headers) - 1)
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"Commandes!A1:{end_col}1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def _get_existing_order_ids(sheets_service, spreadsheet_id: str) -> set[str]:
    """Get the set of order IDs already in the sheet (column F for tickets)."""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!F:F",
        ).execute()
        rows = result.get("values", [])
        return {row[0] for row in rows[1:] if row and row[0]}
    except Exception:
        return set()


def _write_ticket_orders(sheets_service, spreadsheet_id: str, orders: list[dict]) -> int:
    """Write ticket orders to the sheet. Returns count of rows written."""
    if not orders:
        return 0

    _ensure_sheet_headers(sheets_service, spreadsheet_id, "tickets")

    # Deduplicate against existing sheet rows
    existing_ids = _get_existing_order_ids(sheets_service, spreadsheet_id)
    new_orders = [o for o in orders if o.get("order_id") and o["order_id"] not in existing_ids]

    if not new_orders:
        return 0

    rows = []
    for order in new_orders:
        rows.append([
            order.get("event", ""),
            order.get("category", ""),
            order.get("venue", ""),
            order.get("event_date", ""),
            order.get("price", ""),
            order.get("order_id", ""),
            order.get("order_link", ""),
            order.get("account", ""),
            "",  # Prix Vente
            "",  # Benefice
        ])

    sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="Commandes!A:J",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

    logger.info("Wrote %d ticket orders to sheet %s", len(rows), spreadsheet_id)
    return len(rows)


def _write_vinted_orders_starter(sheets_service, spreadsheet_id: str, orders: list[dict]) -> int:
    """Write Vinted sale orders to the sheet (Starter plan). Returns count of rows written."""
    if not orders:
        return 0

    _ensure_sheet_headers(sheets_service, spreadsheet_id, "vinted", "starter")

    appended_rows = []
    for order in orders:
        if order.get("type") != "sale":
            continue
        appended_rows.append([
            order.get("title", ""),
            order.get("price", ""),
            order.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
            order.get("account", ""),
        ])

    if appended_rows:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:D",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": appended_rows},
        ).execute()

    logger.info("Wrote %d Vinted orders (starter) to sheet %s", len(appended_rows), spreadsheet_id)
    return len(appended_rows)


def _write_vinted_orders_pro(sheets_service, spreadsheet_id: str, orders: list[dict]) -> int:
    """Write Vinted orders to the sheet (Pro plan).

    Pro columns: Article | Prix Achat | Date Achat | Prix Vente | Date Vente | Benefice | ROI % | Temps en stock | Compte

    Logic:
    - Purchase emails → write Article + Prix Achat + Date Achat
    - Sale emails → fuzzy match to existing purchase row, fill Prix Vente + Date Vente + auto-calc Benefice/ROI/Temps
    """
    if not orders:
        return 0

    _ensure_sheet_headers(sheets_service, spreadsheet_id, "vinted", "pro")

    # Separate purchases and sales
    purchases = [o for o in orders if o.get("type") == "purchase"]
    sales = [o for o in orders if o.get("type") == "sale"]

    written = 0

    # 1. Append purchase rows
    purchase_rows = []
    for order in purchases:
        purchase_rows.append([
            order.get("title", ""),           # Article
            order.get("price", ""),            # Prix Achat
            order.get("date", ""),             # Date Achat
            "",                                # Prix Vente
            "",                                # Date Vente
            "",                                # Benefice
            "",                                # ROI %
            "",                                # Temps en stock
            order.get("account", ""),          # Compte
        ])

    if purchase_rows:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": purchase_rows},
        ).execute()
        written += len(purchase_rows)

    # 2. Match sales to existing purchase rows (fill in vente columns + calc benefice/ROI/temps)
    if sales:
        try:
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range="Commandes!A:I",
            ).execute()
            existing_rows = result.get("values", [])
        except Exception:
            existing_rows = []

        # Build list of items without a sale price (column D = Prix Vente)
        items_without_sale: list[dict] = []
        for i, row in enumerate(existing_rows[1:], start=2):
            if len(row) >= 1:
                title = row[0].strip()
                prix_vente = row[3].strip() if len(row) > 3 else ""
                prix_achat = row[1].strip() if len(row) > 1 else ""
                date_achat = row[2].strip() if len(row) > 2 else ""
                if title and not prix_vente:
                    items_without_sale.append({
                        "title": title,
                        "row": i,
                        "prix_achat": prix_achat,
                        "date_achat": date_achat,
                    })

        for order in sales:
            vinted_title = order.get("title", "")
            sale_price = order.get("price", "")
            sale_date = order.get("date", "")

            match = find_matching_item(vinted_title, items_without_sale) if items_without_sale else None

            if match:
                row_num = match["row"]
                purchase_price_str = match.get("prix_achat", "0")
                purchase_date = match.get("date_achat", "")

                # Calculate benefit & ROI
                try:
                    purchase_price = float(purchase_price_str)
                    sale_price_f = float(sale_price)
                    calc = calculate_benefit(purchase_price, sale_price_f)
                    benefit_str = f"{calc['benefit']}€"
                    roi_str = f"{calc['roi_percent']}%"
                except (ValueError, TypeError):
                    benefit_str = ""
                    roi_str = ""

                # Calculate time in stock
                time_stock = calculate_time_in_stock(purchase_date, sale_date)
                time_str = time_stock["display"]

                # Update columns D-H (Prix Vente, Date Vente, Benefice, ROI, Temps en stock)
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"Commandes!D{row_num}:H{row_num}",
                    valueInputOption="RAW",
                    body={"values": [[sale_price, sale_date, benefit_str, roi_str, time_str]]},
                ).execute()

                items_without_sale = [it for it in items_without_sale if it["row"] != row_num]
                written += 1
            else:
                # No matching purchase found — append as sale-only row
                sheets_service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range="Commandes!A:I",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [[
                        vinted_title, "", "", sale_price, sale_date,
                        "", "", "", order.get("account", ""),
                    ]]},
                ).execute()
                written += 1

    logger.info("Wrote %d Vinted orders (pro) to sheet %s", written, spreadsheet_id)
    return written


def _write_vinted_orders(sheets_service, spreadsheet_id: str, orders: list[dict], plan: str = "starter") -> int:
    """Write Vinted orders — dispatches to starter or pro logic."""
    if plan == "pro":
        return _write_vinted_orders_pro(sheets_service, spreadsheet_id, orders)
    return _write_vinted_orders_starter(sheets_service, spreadsheet_id, orders)


# ============================================
# SCAN LOGIC
# ============================================

def _scan_gmail_account(
    gmail_service,
    user_id: int,
    account: dict,
    monitoring_type: str,
    plan: str = "starter",
) -> list[dict]:
    """Scan a single Gmail account for relevant emails.

    Returns a list of parsed order dicts.
    """
    if monitoring_type == "tickets":
        queries = TICKET_QUERIES
    elif plan == "pro":
        # Pro scans both sales AND purchases
        queries = VINTED_SALE_QUERIES + VINTED_PURCHASE_QUERIES
    else:
        queries = VINTED_SALE_QUERIES

    all_orders: list[dict] = []
    account_email = account.get("email", "")

    for query_str, source in queries:
        try:
            results = gmail_service.users().messages().list(
                userId="me",
                q=query_str,
                maxResults=200,
            ).execute()

            messages = results.get("messages", [])
            logger.info("Account %s query '%s': %d messages", account_email, query_str, len(messages))

            for msg_info in messages:
                msg_id = msg_info["id"]

                # Deduplication at DB level
                if db.is_order_processed(user_id, msg_id):
                    continue

                # Fetch full message
                msg = gmail_service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()

                payload = msg.get("payload", {})
                headers = payload.get("headers", [])
                subject = _get_header(headers, "subject")
                delivered_to = _get_header(headers, "delivered-to") or _get_header(headers, "to")

                html_content = _extract_html_from_payload(payload)

                # Parse based on source
                order = None
                if monitoring_type == "tickets":
                    if source == "ticketmaster":
                        order = parse_ticketmaster_email(subject, html_content)
                    elif source == "roland-garros":
                        order = parse_roland_garros_email(subject, html_content)
                    elif source == "stade-de-france":
                        order = parse_stade_de_france_email(subject, html_content)
                elif monitoring_type == "vinted":
                    if source == "vinted-sale":
                        order = parse_vinted_sale_email(html_content)
                    elif source == "vinted-purchase":
                        order = parse_vinted_purchase_email(html_content)

                if order:
                    order["account"] = delivered_to or account_email
                    order["msg_id"] = msg_id
                    order["source"] = source

                    # Record in processed_orders
                    order_number = order.get("order_id", order.get("title", msg_id))
                    db.create_processed_order(user_id, order_number, source, msg_id)

                    all_orders.append(order)
                    logger.info(
                        "Parsed order: %s | %s | type=%s",
                        order.get("event", order.get("title", "?")),
                        order.get("order_id", "n/a"),
                        order.get("type", "unknown"),
                    )

        except Exception as exc:
            logger.error("Error scanning account %s query '%s': %s", account_email, query_str, exc)

    return all_orders


def scan_user(user_id: int) -> int:
    """Scan all Gmail accounts for a user and write results to their sheet.

    Returns the total number of new orders found.
    """
    user = db.get_user_by_id(user_id)
    if not user:
        logger.error("User id=%d not found", user_id)
        return 0

    monitoring_type = user["monitoring_type"]
    plan = user.get("plan", "starter")
    accounts = db.get_gmail_accounts(user_id)
    sheet = db.get_primary_spreadsheet(user_id)

    if not accounts:
        logger.warning("User id=%d has no gmail accounts", user_id)
        return 0

    if not sheet:
        logger.warning("User id=%d has no spreadsheet", user_id)
        return 0

    # Get Sheets credentials from primary account
    sheets_creds = _get_user_credentials(user_id)
    if not sheets_creds:
        logger.error("Could not build Sheets credentials for user id=%d", user_id)
        return 0

    sheets_service = build("sheets", "v4", credentials=sheets_creds, cache_discovery=False)
    spreadsheet_id = sheet["spreadsheet_id"]

    total_orders = 0

    for account in accounts:
        log_id = db.create_scan_log(
            user_id=user_id,
            scan_type=monitoring_type,
            gmail_account_id=account["id"],
            status="running",
        )

        try:
            creds = _build_credentials(account, user)
            if not creds:
                db.update_scan_log(log_id, 0, "error", "Could not build credentials")
                continue

            gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)

            orders = _scan_gmail_account(gmail_service, user_id, account, monitoring_type, plan)

            # Write to sheet
            if orders:
                if monitoring_type == "tickets":
                    written = _write_ticket_orders(sheets_service, spreadsheet_id, orders)
                else:
                    written = _write_vinted_orders(sheets_service, spreadsheet_id, orders, plan)
                total_orders += written
            else:
                written = 0

            db.update_scan_log(log_id, written, "success")
            logger.info(
                "Scan complete: user=%d account=%s orders=%d",
                user_id, account["email"], written,
            )

        except Exception as exc:
            logger.error("Scan failed: user=%d account=%s error=%s", user_id, account["email"], exc)
            db.update_scan_log(log_id, 0, "error", str(exc)[:500])

    return total_orders


def scan_all_users() -> dict[int, int]:
    """Scan all active users.

    Returns a dict mapping user_id -> orders_found.
    """
    users = db.get_all_users()
    results: dict[int, int] = {}

    logger.info("Starting scan for %d user(s)", len(users))

    for user in users:
        user_id = user["id"]
        try:
            count = scan_user(user_id)
            results[user_id] = count
        except Exception as exc:
            logger.error("Scan failed for user id=%d: %s", user_id, exc)
            results[user_id] = 0

    logger.info("Scan complete. Results: %s", results)
    return results
