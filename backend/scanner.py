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
    parse_ticketmaster_us_email,
    parse_ticketmaster_uk_email,
    parse_accor_arena_email,
    parse_axs_email,
    TICKET_QUERIES,
)
from parsers.vinted import (
    parse_vinted_sale_email,
    parse_vinted_purchase_email,
    parse_vinted_email,
    find_matching_item,
    VINTED_SALE_QUERIES,
    VINTED_PURCHASE_QUERIES,
)
from parsers.leboncoin import (
    parse_leboncoin_sale_email,
    parse_leboncoin_purchase_email,
    LEBONCOIN_SALE_QUERIES,
    LEBONCOIN_PURCHASE_QUERIES,
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
    """Recursively extract first HTML content from a Gmail message payload."""
    if "parts" in payload:
        for part in payload["parts"]:
            result = _extract_html_from_payload(part)
            if result:
                return result  # Return immediately on first HTML match
    else:
        mime_type = payload.get("mimeType", "")
        if "html" in mime_type:
            body = payload.get("body", {})
            data = body.get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""


def _headers_to_dict(headers: list[dict]) -> dict[str, str]:
    """Convert Gmail headers list to a lowercase-key dict (first occurrence wins)."""
    result = {}
    for h in headers:
        key = h.get("name", "").lower()
        if key and key not in result:
            result[key] = h.get("value", "")
    return result


def _get_header(headers, name: str) -> str:
    """Extract a header value. Accepts list[dict] or pre-built dict."""
    if isinstance(headers, dict):
        return headers.get(name.lower(), "")
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


# ============================================
# SHEET WRITING
# ============================================


def _validate_order_data(order: dict) -> dict:
    """Ensure numeric fields are properly formatted for Sheets.

    Converts string prices (with currency symbols, commas, etc.) to floats.
    """
    for key in ('prix', 'price', 'prix_achat', 'prix_vente', 'amount'):
        if key in order:
            val = order[key]
            if isinstance(val, str):
                cleaned = val.replace('€', '').replace(',', '.').replace('\xa0', '').strip()
                try:
                    order[key] = float(cleaned)
                except (ValueError, TypeError):
                    pass
    return order


TICKET_HEADERS = [
    "Événement", "Catégorie", "Lieu", "Date", "Prix Achat",
    "N° Commande", "Lien", "Compte", "Prix Vente", "Bénéfice",
]

# Same template for starter and pro — Bénéfice(F), ROI %(G), Temps en stock(H) are formula cols
VINTED_HEADERS = [
    "Article", "Prix Achat", "Date Achat", "Prix Vente", "Date Vente",
    "Bénéfice", "ROI %", "Temps en stock", "Compte",
]

# Keep legacy alias for any existing references
VINTED_HEADERS_PRO     = VINTED_HEADERS
VINTED_HEADERS_STARTER = VINTED_HEADERS


def _ensure_sheet_headers(sheets_service, spreadsheet_id: str, monitoring_type: str, plan: str = "starter") -> None:
    """Ensure the first row has correct headers."""
    headers = TICKET_HEADERS if monitoring_type == "tickets" else VINTED_HEADERS

    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A1:J1",
        ).execute()
        existing = result.get("values", [[]])[0]
        if existing and existing[0] == headers[0]:
            return  # headers already set
    except Exception:
        pass

    end_col = chr(ord("A") + len(headers) - 1)
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"Commandes!A1:{end_col}1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def _next_empty_row(sheets_service, spreadsheet_id: str, col: str = "A") -> int:
    """Return the row number of the next empty cell in `col` (1-indexed).

    Scans only the given column — formula columns that return '' are invisible
    to values().get(), so only rows with actual data are counted.
    """
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"Commandes!{col}:{col}",
        ).execute()
        return len(result.get("values", [])) + 1
    except Exception:
        return 2  # fallback: first data row


def _get_existing_order_ids(sheets_service, spreadsheet_id: str) -> set[str]:
    """Return set of N° Commande values already in the sheet (column F = index 5)."""
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
    """Write ticket orders to the sheet. Returns count of rows written.

    Columns A-I are data; J (Bénéfice) is a pre-seeded formula — we do NOT write to it.
    Uses targeted update() instead of append() to avoid overwriting formula rows.
    """
    if not orders:
        return 0

    _ensure_sheet_headers(sheets_service, spreadsheet_id, "tickets")

    # Deduplicate by N° Commande (col F)
    existing_ids = _get_existing_order_ids(sheets_service, spreadsheet_id)
    new_orders = [o for o in orders if o.get("order_id") and o["order_id"] not in existing_ids]

    if not new_orders:
        return 0

    start_row = _next_empty_row(sheets_service, spreadsheet_id, col="A")

    batch = []
    for i, order in enumerate(new_orders):
        r = start_row + i
        # Validate order data before writing
        validated_order = _validate_order_data(order.copy())
        # Write A-I only (J = Bénéfice is a formula, leave untouched)
        batch.append({
            "range": f"Commandes!A{r}:I{r}",
            "values": [[
                validated_order.get("event", ""),
                validated_order.get("category", ""),
                validated_order.get("venue", ""),
                validated_order.get("event_date", ""),
                validated_order.get("price", ""),
                validated_order.get("order_id", ""),
                validated_order.get("order_link", ""),
                validated_order.get("account", ""),
                "",  # Prix Vente (I) — filled in later by user
            ]],
        })

    sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": batch},
    ).execute()

    logger.info("Wrote %d ticket orders to sheet %s", len(new_orders), spreadsheet_id)
    return len(new_orders)


def _write_vinted_orders(sheets_service, spreadsheet_id: str, orders: list[dict], plan: str = "starter") -> int:
    """Write Vinted orders to the sheet (starter & pro use the same template).

    Columns layout: Article(A) | Prix Achat(B) | Date Achat(C) | Prix Vente(D) |
                    Date Vente(E) | Bénéfice(F-formula) | ROI %(G-formula) |
                    Temps en stock(H-formula) | Compte(I)

    Strategy:
    - Purchases  → write A-C + I; leave D-E empty, F-H are pre-seeded formulas.
    - Sales match → write D-E only; F-H auto-calculate from the formula.
    - Sale-only  → write A + D-E + I; B-C empty (no purchase data), F-H auto-calc.

    Uses targeted update() / batchUpdate() instead of append() so the pre-seeded
    formula rows in F-H are never overwritten with empty strings.
    """
    if not orders:
        return 0

    _ensure_sheet_headers(sheets_service, spreadsheet_id, "vinted")

    purchases = [o for o in orders if o.get("type") == "purchase"]
    sales     = [o for o in orders if o.get("type") == "sale"]
    written   = 0

    # ── 1. Purchase rows (write A-C + I; skip D-H which are formula cols) ────
    if purchases:
        start_row = _next_empty_row(sheets_service, spreadsheet_id, col="A")
        batch = []
        for i, order in enumerate(purchases):
            r = start_row + i
            # Validate order data before writing
            validated_order = _validate_order_data(order.copy())
            batch.append({
                "range": f"Commandes!A{r}:C{r}",
                "values": [[
                    validated_order.get("title", ""),
                    validated_order.get("price", ""),
                    validated_order.get("date", ""),
                ]],
            })
            batch.append({
                "range": f"Commandes!I{r}",
                "values": [[validated_order.get("account", "")]],
            })

        sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": batch},
        ).execute()
        written += len(purchases)

    # ── 2. Match sales → find purchase row, write D-E only (F-H auto-calc) ──
    if sales:
        try:
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range="Commandes!A:E",
            ).execute()
            existing_rows = result.get("values", [])
        except Exception:
            existing_rows = []

        # Items with Article name but no Prix Vente yet (col D)
        items_without_sale: list[dict] = []
        for i, row in enumerate(existing_rows[1:], start=2):
            title      = row[0].strip() if len(row) > 0 else ""
            prix_vente = row[3].strip() if len(row) > 3 else ""
            if title and not prix_vente:
                items_without_sale.append({"title": title, "row": i})

        sale_updates = []
        for order in sales:
            # Validate order data before writing
            validated_order = _validate_order_data(order.copy())
            vinted_title = validated_order.get("title", "")
            sale_price   = validated_order.get("price", "")
            sale_date    = validated_order.get("date", "")

            match = find_matching_item(vinted_title, items_without_sale) if items_without_sale else None

            if match:
                r = match["row"]
                # Write Prix Vente (D) + Date Vente (E) — Bénéfice/ROI/Temps auto-calc
                sale_updates.append({
                    "range": f"Commandes!D{r}:E{r}",
                    "values": [[sale_price, sale_date]],
                })
                items_without_sale = [it for it in items_without_sale if it["row"] != r]
                written += 1
            else:
                # No matching purchase → new row with sale data only
                r = _next_empty_row(sheets_service, spreadsheet_id, col="A")
                sale_updates.append({
                    "range": f"Commandes!A{r}",
                    "values": [[vinted_title]],
                })
                sale_updates.append({
                    "range": f"Commandes!D{r}:E{r}",
                    "values": [[sale_price, sale_date]],
                })
                sale_updates.append({
                    "range": f"Commandes!I{r}",
                    "values": [[validated_order.get("account", "")]],
                })
                written += 1

        if sale_updates:
            sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": sale_updates},
            ).execute()

    logger.info("Wrote %d Vinted orders to sheet %s", written, spreadsheet_id)
    return written


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
        # Pro scans both sales AND purchases (Vinted + Leboncoin)
        queries = VINTED_SALE_QUERIES + VINTED_PURCHASE_QUERIES + LEBONCOIN_SALE_QUERIES + LEBONCOIN_PURCHASE_QUERIES
    else:
        # Starter scans sales only (Vinted + Leboncoin)
        queries = VINTED_SALE_QUERIES + LEBONCOIN_SALE_QUERIES

    all_orders: list[dict] = []
    account_email = account.get("email", "")

    # Bulk-load processed email IDs to avoid per-message DB queries
    processed_ids = db.get_processed_email_ids(user_id, monitoring_type=monitoring_type)

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

                # Deduplication via bulk set lookup (avoids per-message DB query)
                if msg_id in processed_ids:
                    continue

                # Fetch full message
                msg = gmail_service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()

                payload = msg.get("payload", {})
                headers = _headers_to_dict(payload.get("headers", []))
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
                    elif source == "ticketmaster-us":
                        order = parse_ticketmaster_us_email(subject, html_content)
                    elif source == "ticketmaster-uk":
                        order = parse_ticketmaster_uk_email(subject, html_content)
                    elif source == "accor-arena":
                        order = parse_accor_arena_email(subject, html_content)
                    elif source == "axs":
                        order = parse_axs_email(subject, html_content)
                elif monitoring_type == "vinted":
                    if source == "vinted-sale":
                        order = parse_vinted_sale_email(html_content)
                    elif source == "vinted-purchase":
                        order = parse_vinted_purchase_email(html_content)
                    elif source == "leboncoin-sale":
                        order = parse_leboncoin_sale_email(html_content)
                    elif source == "leboncoin-purchase":
                        order = parse_leboncoin_purchase_email(html_content)

                if order:
                    order["account"] = delivered_to or account_email
                    order["msg_id"] = msg_id
                    order["source"] = source

                    # Record in processed_orders (scoped to monitoring_type)
                    order_number = order.get("order_id", order.get("title", msg_id))
                    db.create_processed_order(user_id, order_number, source, msg_id, monitoring_type=monitoring_type)

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
    sheet = db.get_primary_spreadsheet(user_id, monitoring_type=monitoring_type)

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
            monitoring_type=monitoring_type,
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
            # Sanitize error for storage — no internal paths or token details
            err_str = str(exc)
            if "invalid_grant" in err_str:
                sanitized = "Authentication error: token expired or revoked"
            elif "HttpError" in err_str:
                sanitized = f"Google API error: {type(exc).__name__}"
            elif "database" in err_str.lower() or "locked" in err_str.lower():
                sanitized = "Database error: temporary lock"
            else:
                sanitized = f"{type(exc).__name__}: {err_str[:200]}"
            db.update_scan_log(log_id, 0, "error", sanitized)

    return total_orders


def organize_ticket_tabs(user_id: int) -> dict:
    """Organize ticket data into per-artist/event Google Sheet tabs.

    PRO feature for ticket users. Reads the main 'Commandes' sheet,
    groups rows by event name, and creates/updates one tab per event.

    Returns: {"tabs_created": int, "tabs_updated": int, "events": list[str]}
    """
    user = db.get_user_by_id(user_id)
    if not user or user["monitoring_type"] != "tickets" or user.get("plan") != "pro":
        return {"error": "Feature reservee au plan Pro Tickets"}

    sheets_creds = _get_user_credentials(user_id)
    if not sheets_creds:
        return {"error": "Erreur d'authentification"}

    sheet = db.get_primary_spreadsheet(user_id, monitoring_type="tickets")
    if not sheet:
        return {"error": "Aucun Google Sheet configure"}

    sheets_service = build("sheets", "v4", credentials=sheets_creds, cache_discovery=False)
    spreadsheet_id = sheet["spreadsheet_id"]

    try:
        # Read all ticket data from main sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Commandes!A:J",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return {"tabs_created": 0, "tabs_updated": 0, "events": []}

        headers = rows[0]

        # Group rows by event name (column A)
        events: dict[str, list] = {}
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            event_name = row[0].strip()
            if event_name not in events:
                events[event_name] = []
            events[event_name].append(row)

        if not events:
            return {"tabs_created": 0, "tabs_updated": 0, "events": []}

        # Get existing sheet tabs
        spreadsheet_meta = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties.title"
        ).execute()
        existing_tabs = {s["properties"]["title"] for s in spreadsheet_meta.get("sheets", [])}

        tabs_created = 0
        tabs_updated = 0

        for event_name, event_rows in events.items():
            # Sanitize tab name: keep the original name, only replace
            # characters forbidden in Google Sheets tab names
            tab_name = event_name[:100].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "(").replace("]", ")")
            tab_name = tab_name.strip()
            if not tab_name:
                tab_name = "Sans nom"

            if tab_name not in existing_tabs:
                # Create new tab
                try:
                    sheets_service.spreadsheets().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
                    ).execute()
                    tabs_created += 1
                    existing_tabs.add(tab_name)
                except Exception as exc:
                    logger.warning("Could not create tab '%s': %s", tab_name, exc)
                    continue
            else:
                # Clear existing tab data before rewriting
                try:
                    sheets_service.spreadsheets().values().clear(
                        spreadsheetId=spreadsheet_id,
                        range=f"'{tab_name}'!A:J",
                    ).execute()
                except Exception as exc:
                    logger.warning("Could not clear tab '%s': %s", tab_name, exc)
                tabs_updated += 1

            # Write headers + data to this tab
            all_rows = [headers] + event_rows
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_name}'!A1",
                valueInputOption="RAW",
                body={"values": all_rows},
            ).execute()

        logger.info("Organized %d event tabs for user id=%d", len(events), user_id)
        return {
            "tabs_created": tabs_created,
            "tabs_updated": tabs_updated,
            "events": list(events.keys()),
        }

    except Exception as exc:
        logger.error("organize_ticket_tabs failed for user id=%d: %s", user_id, exc)
        return {"error": str(exc)}


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
