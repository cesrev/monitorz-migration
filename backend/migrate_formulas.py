#!/usr/bin/env python3
"""
migrate_formulas.py
-------------------
Seeds formula columns (+ number formats) into every existing Monitorz
spreadsheet that was created BEFORE the formula pre-seeding was added.

Safe to re-run: checks if the formula already exists in the target cell
before writing anything.

Usage (from backend/):
    python migrate_formulas.py [--dry-run]
"""

import sys
import argparse
import logging

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import database as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate_formulas")

FORMULA_ROWS = 500  # must match _create_spreadsheet_for_user in app.py


# ─── helpers ──────────────────────────────────────────────────────────────────

def _build_creds(account: dict):
    """Rebuild OAuth credentials from a Gmail account row."""
    token         = account.get("oauth_token")
    refresh_token = account.get("oauth_refresh_token")
    client_id     = account.get("oauth_client_id")
    client_secret = account.get("oauth_client_secret")

    if not all([refresh_token, client_id, client_secret]):
        return None

    creds = Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            log.warning("Token refresh failed: %s", e)
            return None

    return creds


def _already_has_formula(svc, spreadsheet_id: str, cell: str) -> bool:
    """Return True if `cell` already contains a formula (starts with '=')."""
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"Commandes!{cell}",
            valueRenderOption="FORMULA",
        ).execute()
        values = res.get("values", [])
        if values and values[0] and str(values[0][0]).startswith("="):
            return True
    except Exception:
        pass
    return False


def _seed_formulas(svc, spreadsheet_id: str, monitoring_type: str, dry_run: bool):
    """Write formulas + number formats into an existing spreadsheet."""

    if monitoring_type == "tickets":
        formula_range = f"Commandes!J2:J{FORMULA_ROWS + 1}"
        formula_values = [
            [f'=IF(OR(E{r}="",I{r}=""),"",I{r}-E{r})']
            for r in range(2, FORMULA_ROWS + 2)
        ]
        col_formats = [
            {"col": 9, "pattern": '"€"#,##0.00'},
        ]
        check_cell = "J2"

    else:  # vinted
        formula_range = f"Commandes!F2:H{FORMULA_ROWS + 1}"
        formula_values = [
            [
                f'=IF(OR(B{r}="",D{r}=""),"",D{r}-B{r})',
                f'=IF(OR(B{r}=0,F{r}=""),"",ROUND(F{r}/B{r}*100,1))',
                f'=IF(C{r}="","",IF(E{r}="",TODAY()-C{r},E{r}-C{r}))',
            ]
            for r in range(2, FORMULA_ROWS + 2)
        ]
        col_formats = [
            {"col": 5, "pattern": '"€"#,##0.00'},
            {"col": 6, "pattern": '0.0"%"'},
            {"col": 7, "pattern": '0" j"'},
        ]
        check_cell = "F2"

    # ── already done? ─────────────────────────────────────────────────────────
    if _already_has_formula(svc, spreadsheet_id, check_cell):
        log.info("  ↳ formulas already present, skipping")
        return

    if dry_run:
        log.info("  ↳ [DRY-RUN] would seed %d rows of formulas into %s",
                 FORMULA_ROWS, formula_range)
        return

    # ── get sheet id for formatting ────────────────────────────────────────────
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = None
    for sh in meta.get("sheets", []):
        if sh["properties"]["title"] == "Commandes":
            sheet_id = sh["properties"]["sheetId"]
            break

    if sheet_id is None:
        log.warning("  ↳ no 'Commandes' tab found, skipping")
        return

    # ── write formulas ─────────────────────────────────────────────────────────
    svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=formula_range,
        valueInputOption="USER_ENTERED",
        body={"values": formula_values},
    ).execute()
    log.info("  ↳ formulas written (%d rows)", FORMULA_ROWS)

    # ── apply number formats ───────────────────────────────────────────────────
    fmt_requests = []
    for fmt in col_formats:
        c = fmt["col"]
        fmt_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": FORMULA_ROWS + 1,
                    "startColumnIndex": c,
                    "endColumnIndex": c + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": "NUMBER",
                            "pattern": fmt["pattern"],
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": fmt_requests},
    ).execute()
    log.info("  ↳ number formats applied")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed formula columns into existing Monitorz sheets.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing anything")
    args = parser.parse_args()

    if args.dry_run:
        log.info("=== DRY-RUN mode — no changes will be made ===")

    users = db.get_all_users()
    log.info("Found %d user(s) in DB", len(users))

    ok = err = skipped = 0

    for user in users:
        user_id = user["id"]
        email   = user.get("email", "?")
        mtype   = user.get("monitoring_type", "tickets")

        log.info("User %d <%s> type=%s", user_id, email, mtype)

        # get primary gmail account for credentials
        accounts = db.get_gmail_accounts(user_id)
        primary  = next((a for a in accounts if a.get("is_primary")), None) or (accounts[0] if accounts else None)

        if not primary:
            log.warning("  ↳ no Gmail account found, skipping")
            skipped += 1
            continue

        creds = _build_creds(primary)
        if not creds:
            log.warning("  ↳ could not build credentials, skipping")
            skipped += 1
            continue

        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

        sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
        if not sheets:
            log.info("  ↳ no spreadsheets found")
            skipped += 1
            continue

        for sheet in sheets:
            sid = sheet["spreadsheet_id"]
            log.info("  Sheet %s", sid)
            try:
                _seed_formulas(svc, sid, mtype, dry_run=args.dry_run)
                ok += 1
            except Exception as e:
                log.error("  ↳ ERROR: %s", e)
                err += 1

    log.info("─" * 50)
    log.info("Done. ok=%d  skipped=%d  errors=%d", ok, skipped, err)

    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
