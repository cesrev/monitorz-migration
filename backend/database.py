"""
Billets & Vinted Monitor MVP - Database Layer
SQLite database with full CRUD operations.
"""

import sqlite3
import os
import uuid
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.db")


def get_db() -> sqlite3.Connection:
    """Get a database connection with row_factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            picture TEXT DEFAULT '',
            monitoring_type TEXT NOT NULL CHECK(monitoring_type IN ('tickets', 'vinted')),
            plan TEXT NOT NULL DEFAULT 'starter' CHECK(plan IN ('starter', 'pro')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS gmail_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            oauth_token TEXT,
            oauth_refresh_token TEXT,
            token_expiry TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS spreadsheets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            spreadsheet_id TEXT NOT NULL,
            spreadsheet_url TEXT NOT NULL,
            is_auto_created INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            gmail_account_id INTEGER,
            scan_type TEXT NOT NULL,
            orders_found INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (gmail_account_id) REFERENCES gmail_accounts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS processed_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_number TEXT NOT NULL,
            source TEXT NOT NULL,
            email_id TEXT NOT NULL,
            processed_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('event_soon', 'dormant_stock', 'scan_result', 'info')),
            title TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            read INTEGER NOT NULL DEFAULT 0,
            reference_key TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS services (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            name TEXT NOT NULL,
            unit_price_ht REAL DEFAULT 0.0,
            tva_rate REAL DEFAULT 20.0,
            description TEXT DEFAULT '',
            position INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_orders_unique
            ON processed_orders(user_id, email_id);

        CREATE INDEX IF NOT EXISTS idx_gmail_accounts_user
            ON gmail_accounts(user_id);

        CREATE INDEX IF NOT EXISTS idx_spreadsheets_user
            ON spreadsheets(user_id);

        CREATE INDEX IF NOT EXISTS idx_scan_logs_user
            ON scan_logs(user_id);

        CREATE INDEX IF NOT EXISTS idx_notifications_user
            ON notifications(user_id, read);

        CREATE INDEX IF NOT EXISTS idx_services_user
            ON services(user_email);

        -- Extension: Vinted session token per user (one row per user)
        CREATE TABLE IF NOT EXISTS vinted_sessions (
            user_id INTEGER PRIMARY KEY,
            token TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT 'fr',
            synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- Extension: activity log (messages sent, labels downloaded, errors)
        CREATE TABLE IF NOT EXISTS extension_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            item_id TEXT,
            target_user_id TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_extension_logs_user
            ON extension_logs(user_id, created_at);
    """)

    conn.commit()

    # --- Migrations: add columns if missing ---
    cursor = conn.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    if "scan_frequency" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN scan_frequency INTEGER NOT NULL DEFAULT 10")
        conn.commit()
        logger.info("Migration: added scan_frequency column to users")

    if "alert_days_before" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN alert_days_before INTEGER NOT NULL DEFAULT 7")
        conn.commit()
        logger.info("Migration: added alert_days_before column to users")

    if "dormant_days_threshold" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN dormant_days_threshold INTEGER NOT NULL DEFAULT 30")
        conn.commit()
        logger.info("Migration: added dormant_days_threshold column to users")

    # --- Migration: add monitoring_type to data tables for profile isolation ---
    for table in ("spreadsheets", "processed_orders", "scan_logs", "notifications"):
        cols_cursor = conn.execute(f"PRAGMA table_info({table})")
        table_cols = {row[1] for row in cols_cursor.fetchall()}
        if "monitoring_type" not in table_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN monitoring_type TEXT NOT NULL DEFAULT 'tickets'")
            conn.commit()
            logger.info("Migration: added monitoring_type column to %s", table)

    # --- Migration: add company & invoice columns to users ---
    company_invoice_cols = {
        "company_name": "TEXT DEFAULT ''",
        "company_address": "TEXT DEFAULT ''",
        "company_phone": "TEXT DEFAULT ''",
        "company_email": "TEXT DEFAULT ''",
        "company_siret": "TEXT DEFAULT ''",
        "company_tva_number": "TEXT DEFAULT ''",
        "company_iban": "TEXT DEFAULT ''",
        "company_bic": "TEXT DEFAULT ''",
        "company_tva_rate": "REAL DEFAULT 20.0",
        "invoice_prefix": "TEXT DEFAULT 'INV'",
        "invoice_counter": "INTEGER DEFAULT 0",
        "invoice_footer": "TEXT DEFAULT ''",
    }
    for col_name, col_def in company_invoice_cols.items():
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            conn.commit()
            logger.info("Migration: added %s column to users", col_name)

    # Rebuild unique index for processed_orders to include monitoring_type
    try:
        conn.execute("DROP INDEX IF EXISTS idx_processed_orders_unique")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_orders_unique
                        ON processed_orders(user_id, email_id, monitoring_type)""")
        conn.commit()
    except Exception:
        pass  # Index already correct

    # --- Migration: extension config columns ---
    cursor = conn.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    ext_cols = {
        "ext_secret":           "TEXT DEFAULT ''",
        "ext_msg_enabled":      "INTEGER DEFAULT 1",
        "ext_msg_template":     "TEXT DEFAULT ''",
        "ext_msg_quota_daily":  "INTEGER DEFAULT 50",
        "ext_poll_interval_min":"INTEGER DEFAULT 5",
    }
    for col_name, col_def in ext_cols.items():
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            conn.commit()
            logger.info("Migration: added %s column to users", col_name)

    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


# ============================================
# USERS
# ============================================

def create_user(email: str, name: str, picture: str, monitoring_type: str, plan: str = "starter") -> int:
    """Create a new user. Returns the user id."""
    if plan not in ("starter", "pro"):
        plan = "starter"
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, name, picture, monitoring_type, plan, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email, name, picture, monitoring_type, plan, datetime.utcnow().isoformat())
        )
        conn.commit()
        user_id = cursor.lastrowid
        logger.info("Created user id=%d email=%s type=%s plan=%s", user_id, email, monitoring_type, plan)
        return user_id
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Get a user by id."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    """Get a user by email."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users() -> list[dict]:
    """Get all users."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_user(user_id: int, **kwargs) -> bool:
    """Update user fields. Pass field=value pairs."""
    if not kwargs:
        return False
    allowed = {
        "email", "name", "picture", "monitoring_type", "plan",
        "scan_frequency", "alert_days_before", "dormant_days_threshold",
        "company_name", "company_address", "company_phone", "company_email",
        "company_siret", "company_tva_number", "company_iban", "company_bic",
        "company_tva_rate", "invoice_prefix", "invoice_counter", "invoice_footer",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]

    conn = get_db()
    try:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================
# GMAIL ACCOUNTS
# ============================================

def create_gmail_account(
    user_id: int,
    email: str,
    oauth_token: str,
    oauth_refresh_token: str,
    token_expiry: Optional[str] = None,
    is_primary: bool = False,
) -> int:
    """Create a gmail account entry. Returns the account id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO gmail_accounts
               (user_id, email, oauth_token, oauth_refresh_token, token_expiry, is_primary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, email, oauth_token, oauth_refresh_token,
             token_expiry, 1 if is_primary else 0, datetime.utcnow().isoformat())
        )
        conn.commit()
        account_id = cursor.lastrowid
        logger.info("Created gmail_account id=%d user=%d email=%s", account_id, user_id, email)
        return account_id
    finally:
        conn.close()


def get_gmail_account_by_id(account_id: int) -> Optional[dict]:
    """Get a gmail account by id."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM gmail_accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_gmail_accounts(user_id: int) -> list[dict]:
    """Get all gmail accounts for a user."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM gmail_accounts WHERE user_id = ? ORDER BY is_primary DESC, created_at ASC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_gmail_account_tokens(account_id: int, oauth_token: str, token_expiry: Optional[str] = None) -> bool:
    """Update the OAuth token (after refresh)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE gmail_accounts SET oauth_token = ?, token_expiry = ? WHERE id = ?",
            (oauth_token, token_expiry, account_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def update_gmail_account_refresh_token(account_id: int, oauth_refresh_token: str) -> bool:
    """Update the refresh token."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE gmail_accounts SET oauth_refresh_token = ? WHERE id = ?",
            (oauth_refresh_token, account_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_gmail_account(account_id: int) -> bool:
    """Delete a gmail account."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM gmail_accounts WHERE id = ?", (account_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================
# SPREADSHEETS
# ============================================

def create_spreadsheet(
    user_id: int,
    spreadsheet_id: str,
    spreadsheet_url: str,
    is_auto_created: bool = True,
    monitoring_type: str = "tickets",
) -> int:
    """Create a spreadsheet entry. Returns the row id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO spreadsheets
               (user_id, spreadsheet_id, spreadsheet_url, is_auto_created, monitoring_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, spreadsheet_id, spreadsheet_url,
             1 if is_auto_created else 0, monitoring_type, datetime.utcnow().isoformat())
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.info("Created spreadsheet id=%d user=%d type=%s sheet=%s", row_id, user_id, monitoring_type, spreadsheet_id)
        return row_id
    finally:
        conn.close()


def get_spreadsheets(user_id: int, monitoring_type: str = None) -> list[dict]:
    """Get spreadsheets for a user, optionally filtered by monitoring_type."""
    conn = get_db()
    try:
        if monitoring_type:
            rows = conn.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? AND monitoring_type = ? ORDER BY created_at DESC",
                (user_id, monitoring_type)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_primary_spreadsheet(user_id: int, monitoring_type: str = None) -> Optional[dict]:
    """Get the most recent spreadsheet for a user, optionally filtered by type."""
    conn = get_db()
    try:
        if monitoring_type:
            row = conn.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? AND monitoring_type = ? ORDER BY created_at DESC LIMIT 1",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_spreadsheet(sheet_row_id: int) -> bool:
    """Delete a spreadsheet entry."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM spreadsheets WHERE id = ?", (sheet_row_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================
# SCAN LOGS
# ============================================

def create_scan_log(
    user_id: int,
    scan_type: str,
    gmail_account_id: Optional[int] = None,
    orders_found: int = 0,
    status: str = "pending",
    error_message: Optional[str] = None,
    monitoring_type: str = "tickets",
) -> int:
    """Create a scan log entry. Returns the log id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO scan_logs
               (user_id, gmail_account_id, scan_type, orders_found, status, error_message, monitoring_type, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, gmail_account_id, scan_type, orders_found,
             status, error_message, monitoring_type, datetime.utcnow().isoformat())
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_scan_log(log_id: int, orders_found: int, status: str, error_message: Optional[str] = None) -> bool:
    """Update a scan log after completion."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE scan_logs SET orders_found = ?, status = ?, error_message = ? WHERE id = ?",
            (orders_found, status, error_message, log_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_scan_logs(user_id: int, limit: int = 20, monitoring_type: str = None) -> list[dict]:
    """Get recent scan logs for a user, optionally filtered by monitoring_type."""
    conn = get_db()
    try:
        if monitoring_type:
            rows = conn.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? AND monitoring_type = ? ORDER BY scanned_at DESC LIMIT ?",
                (user_id, monitoring_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? ORDER BY scanned_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_last_scan(user_id: int, monitoring_type: str = None) -> Optional[dict]:
    """Get the most recent scan log for a user, optionally filtered by type."""
    conn = get_db()
    try:
        if monitoring_type:
            row = conn.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? AND monitoring_type = ? ORDER BY scanned_at DESC LIMIT 1",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? ORDER BY scanned_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================
# PROCESSED ORDERS
# ============================================

def create_processed_order(
    user_id: int,
    order_number: str,
    source: str,
    email_id: str,
    monitoring_type: str = "tickets",
) -> Optional[int]:
    """Record a processed order. Returns row id or None if duplicate."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO processed_orders
               (user_id, order_number, source, email_id, monitoring_type, processed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, order_number, source, email_id, monitoring_type, datetime.utcnow().isoformat())
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None  # duplicate
        return cursor.lastrowid
    finally:
        conn.close()


def is_order_processed(user_id: int, email_id: str, monitoring_type: str = None) -> bool:
    """Check if an email has already been processed for a user (and monitoring_type)."""
    conn = get_db()
    try:
        if monitoring_type:
            row = conn.execute(
                "SELECT 1 FROM processed_orders WHERE user_id = ? AND email_id = ? AND monitoring_type = ?",
                (user_id, email_id, monitoring_type)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM processed_orders WHERE user_id = ? AND email_id = ?",
                (user_id, email_id)
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_processed_orders_count(user_id: int, monitoring_type: str = None) -> int:
    """Get total processed orders count for a user, optionally filtered by type."""
    conn = get_db()
    try:
        if monitoring_type:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM processed_orders WHERE user_id = ? AND monitoring_type = ?",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM processed_orders WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_processed_orders(user_id: int, limit: int = 50, monitoring_type: str = None) -> list[dict]:
    """Get recent processed orders for a user, optionally filtered by type."""
    conn = get_db()
    try:
        if monitoring_type:
            rows = conn.execute(
                "SELECT * FROM processed_orders WHERE user_id = ? AND monitoring_type = ? ORDER BY processed_at DESC LIMIT ?",
                (user_id, monitoring_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM processed_orders WHERE user_id = ? ORDER BY processed_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================
# NOTIFICATIONS
# ============================================

def create_notification(
    user_id: int,
    notif_type: str,
    title: str,
    message: str = "",
    reference_key: str = "",
    monitoring_type: str = "tickets",
) -> Optional[int]:
    """Create a notification. reference_key prevents duplicates for the same event."""
    conn = get_db()
    try:
        # If reference_key provided, skip if already exists (unread)
        if reference_key:
            existing = conn.execute(
                "SELECT 1 FROM notifications WHERE user_id = ? AND reference_key = ? AND read = 0",
                (user_id, reference_key)
            ).fetchone()
            if existing:
                return None

        cursor = conn.execute(
            """INSERT INTO notifications (user_id, type, title, message, reference_key, monitoring_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, notif_type, title, message, reference_key, monitoring_type, datetime.utcnow().isoformat())
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_notifications(user_id: int, limit: int = 30, unread_only: bool = False, monitoring_type: str = None) -> list[dict]:
    """Get notifications for a user, optionally filtered by monitoring_type."""
    conn = get_db()
    try:
        if monitoring_type and unread_only:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND monitoring_type = ? AND read = 0 ORDER BY created_at DESC LIMIT ?",
                (user_id, monitoring_type, limit)
            ).fetchall()
        elif monitoring_type:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND monitoring_type = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, monitoring_type, limit)
            ).fetchall()
        elif unread_only:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND read = 0 ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unread_notification_count(user_id: int, monitoring_type: str = None) -> int:
    """Get count of unread notifications, optionally filtered by type."""
    conn = get_db()
    try:
        if monitoring_type:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND monitoring_type = ? AND read = 0",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND read = 0",
                (user_id,)
            ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def mark_notification_read(notification_id: int, user_id: int) -> bool:
    """Mark a single notification as read."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, user_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_all_notifications_read(user_id: int) -> bool:
    """Mark all notifications as read for a user."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0",
            (user_id,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================
# SERVICES
# ============================================

def get_services(user_email: str) -> list[dict]:
    """Get all services for a user, ordered by position."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM services WHERE user_email = ? ORDER BY position ASC, created_at ASC",
            (user_email,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_service(user_email: str, name: str, unit_price_ht: float = 0.0,
                   tva_rate: float = 20.0, description: str = "") -> dict:
    """Create a new service. Returns the created service dict."""
    service_id = str(uuid.uuid4())
    # Get next position
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM services WHERE user_email = ?",
            (user_email,)
        ).fetchone()
        position = row["next_pos"] if row else 0

        conn.execute(
            """INSERT INTO services (id, user_email, name, unit_price_ht, tva_rate, description, position, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (service_id, user_email, name, unit_price_ht, tva_rate, description, position, datetime.utcnow().isoformat())
        )
        conn.commit()
        logger.info("Created service id=%s for user=%s", service_id, user_email)
        return {
            "id": service_id, "user_email": user_email, "name": name,
            "unit_price_ht": unit_price_ht, "tva_rate": tva_rate,
            "description": description, "position": position,
        }
    finally:
        conn.close()


def update_service(service_id: str, user_email: str, **kwargs) -> bool:
    """Update a service. Only updates allowed fields."""
    allowed = {"name", "unit_price_ht", "tva_rate", "description", "position"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [service_id, user_email]

    conn = get_db()
    try:
        conn.execute(
            f"UPDATE services SET {set_clause} WHERE id = ? AND user_email = ?",
            values
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_service(service_id: str, user_email: str) -> bool:
    """Delete a service, checking ownership."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM services WHERE id = ? AND user_email = ?",
            (service_id, user_email)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def increment_invoice_counter(user_id: int) -> int:
    """Increment and return the new invoice counter for a user."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET invoice_counter = invoice_counter + 1 WHERE id = ?",
            (user_id,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT invoice_counter FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return row["invoice_counter"] if row else 1
    finally:
        conn.close()


# ============================================
# EXTENSION — VINTED SESSIONS
# ============================================

def upsert_vinted_session(user_id: int, token: str, domain: str) -> bool:
    """Insert or update the Vinted CSRF token for a user."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO vinted_sessions (user_id, token, domain, synced_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 token = excluded.token,
                 domain = excluded.domain,
                 synced_at = excluded.synced_at""",
            (user_id, token, domain, datetime.utcnow().isoformat())
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_vinted_session(user_id: int) -> Optional[dict]:
    """Get the current Vinted session for a user."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM vinted_sessions WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_vinted_session(user_id: int) -> bool:
    """Delete the Vinted session for a user."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM vinted_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================
# EXTENSION — LOGS
# ============================================

def create_extension_log(
    user_id: int,
    action_type: str,
    item_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    status: str = "ok",
    error: Optional[str] = None,
) -> int:
    """Create an extension activity log entry. Returns the log id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO extension_logs
               (user_id, action_type, item_id, target_user_id, status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, action_type, item_id, target_user_id, status, error,
             datetime.utcnow().isoformat())
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_extension_logs(user_id: int, limit: int = 50) -> list[dict]:
    """Get recent extension logs for a user."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM extension_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================
# EXTENSION — CONFIG
# ============================================

def get_extension_config(user_id: int) -> dict:
    """Get extension config for a user (from users table extra cols)."""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT ext_secret, ext_msg_enabled, ext_msg_template,
                      ext_msg_quota_daily, ext_poll_interval_min
               FROM users WHERE id = ?""",
            (user_id,)
        ).fetchone()
        if not row:
            return {}
        return {
            "ext_secret": row["ext_secret"] or "",
            "msg_enabled": bool(row["ext_msg_enabled"]),
            "msg_template": row["ext_msg_template"] or "",
            "msg_quota_daily": row["ext_msg_quota_daily"] or 50,
            "poll_interval_min": row["ext_poll_interval_min"] or 5,
        }
    finally:
        conn.close()


def update_extension_config(user_id: int, **kwargs) -> bool:
    """Update extension config fields on users table."""
    allowed = {
        "ext_secret", "ext_msg_enabled", "ext_msg_template",
        "ext_msg_quota_daily", "ext_poll_interval_min",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn = get_db()
    try:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    finally:
        conn.close()
