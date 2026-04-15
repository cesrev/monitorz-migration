"""
Billets & Vinted Monitor MVP - Database Layer
SQLite database with full CRUD operations.
"""

import sqlite3
import os
import uuid
import logging
import time
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager
from functools import wraps

from crypto import encrypt_token, decrypt_token, is_token_encrypted

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.db"))


def _configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Apply standard configuration to a SQLite connection."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # timeout is set via sqlite3.connect() parameter
    return conn


def retry_on_locked(max_retries: int = 3, base_delay: float = 0.1):
    """
    Decorator to retry write operations on SQLite "database is locked" errors.
    Uses exponential backoff: delay = base_delay * (2 ^ attempt).
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower():
                        last_exception = e
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(
                                f"Database locked in {func.__name__}, retry {attempt + 1}/{max_retries} "
                                f"after {delay:.3f}s"
                            )
                            time.sleep(delay)
                        else:
                            logger.error(
                                f"Database locked in {func.__name__}, max retries exhausted"
                            )
                    else:
                        raise
            raise last_exception
        return wrapper
    return decorator


@contextmanager
def get_db_context():
    """
    Context manager for database connections.
    Usage:
        with get_db_context() as conn:
            cursor = conn.cursor()
            ...
    """
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _use_conn(conn: Optional[sqlite3.Connection] = None):
    """Internal helper: use the provided connection or create a new one.

    When a connection is provided (request-scoped), it is yielded as-is
    and NOT closed — Flask's teardown_appcontext handles that.
    When conn is None (scanner / background thread), a fresh connection
    is created and closed after the block.
    """
    if conn is not None:
        yield conn
    else:
        _conn = get_db()
        try:
            yield _conn
        finally:
            _conn.close()


def get_db() -> sqlite3.Connection:
    """Get a database connection for non-request contexts (scanner, init).
    Caller is responsible for closing."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    return _configure_connection(conn)


def get_request_db() -> sqlite3.Connection:
    """Get a request-scoped database connection stored in Flask's g object.
    Reuses the same connection within a single request."""
    from flask import g
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        _configure_connection(g.db)
    return g.db


def close_db(e=None) -> None:
    """Teardown function to close the request-scoped DB connection.
    Register with app.teardown_appcontext(close_db)."""
    from flask import g
    db = g.pop("db", None)
    if db is not None:
        db.close()


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
            billing_period TEXT NOT NULL DEFAULT 'monthly' CHECK(billing_period IN ('monthly', 'yearly')),
            trial_started_at TEXT DEFAULT NULL,
            trial_ends_at TEXT DEFAULT NULL,
            is_trial_active INTEGER NOT NULL DEFAULT 0,
            referral_code TEXT DEFAULT '',
            referred_by TEXT DEFAULT '',
            referral_count INTEGER DEFAULT 0,
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
            user_id INTEGER,
            name TEXT NOT NULL,
            unit_price_ht REAL DEFAULT 0.0,
            tva_rate REAL DEFAULT 20.0,
            description TEXT DEFAULT '',
            position INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_orders_unique
            ON processed_orders(user_id, email_id);

        CREATE INDEX IF NOT EXISTS idx_gmail_accounts_user
            ON gmail_accounts(user_id);

        CREATE INDEX IF NOT EXISTS idx_spreadsheets_user
            ON spreadsheets(user_id);

        CREATE INDEX IF NOT EXISTS idx_scan_logs_user
            ON scan_logs(user_id);

        CREATE INDEX IF NOT EXISTS idx_scan_logs_user_type
            ON scan_logs(user_id, monitoring_type, scanned_at);

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

        CREATE INDEX IF NOT EXISTS idx_processed_orders_user_type
            ON processed_orders(user_id, monitoring_type);

        CREATE INDEX IF NOT EXISTS idx_notifications_user_type_read
            ON notifications(user_id, monitoring_type, read);

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

    # --- Migration: add billing_period ---
    if "billing_period" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN billing_period TEXT NOT NULL DEFAULT 'monthly'")
        conn.commit()
        logger.info("Migration: added billing_period column to users")

    # --- Migration: add user_id to services table ---
    svc_cols_cursor = conn.execute("PRAGMA table_info(services)")
    svc_cols = {row[1] for row in svc_cols_cursor.fetchall()}
    if "user_id" not in svc_cols:
        conn.execute("ALTER TABLE services ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        conn.commit()
        logger.info("Migration: added user_id column to services")
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_services_user_id ON services(user_id)")
        conn.commit()
    except Exception:
        pass

    # --- Migration: add monitoring_paused ---
    if "monitoring_paused" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN monitoring_paused INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        existing_cols.add("monitoring_paused")
        logger.info("Migration: added monitoring_paused column to users")

    # --- Migration: add trial and referral columns ---

    trial_referral_cols = {
        "trial_started_at": "TEXT DEFAULT NULL",
        "trial_ends_at": "TEXT DEFAULT NULL",
        "is_trial_active": "INTEGER NOT NULL DEFAULT 0",
        "referral_code": "TEXT DEFAULT ''",
        "referred_by": "TEXT DEFAULT ''",
        "referral_count": "INTEGER DEFAULT 0",
    }
    for col_name, col_def in trial_referral_cols.items():
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            conn.commit()
            existing_cols.add(col_name)
            logger.info("Migration: added %s column to users", col_name)

    # --- Migration: add onboarding_complete ---
    if "onboarding_complete" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        existing_cols.add("onboarding_complete")
        logger.info("Migration: added onboarding_complete column to users")

    # --- Migration: add ext_secret_hash for O(1) lookup ---
    if "ext_secret_hash" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN ext_secret_hash TEXT DEFAULT ''")
        conn.commit()
        logger.info("Migration: added ext_secret_hash column to users")
        # Backfill hashes for existing secrets
        import hashlib
        rows = conn.execute("SELECT id, ext_secret FROM users WHERE ext_secret != '' AND ext_secret IS NOT NULL").fetchall()
        for row in rows:
            h = hashlib.sha256(row["ext_secret"].encode()).hexdigest()
            conn.execute("UPDATE users SET ext_secret_hash = ? WHERE id = ?", (h, row["id"]))
        conn.commit()
        if rows:
            logger.info("Migration: backfilled ext_secret_hash for %d users", len(rows))

    # --- Migration: add monthly_costs ---
    if "monthly_costs" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN monthly_costs REAL NOT NULL DEFAULT 0")
        conn.commit()
        existing_cols.add("monthly_costs")
        logger.info("Migration: added monthly_costs column to users")

    # --- Create indices for trial/referral queries ---
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_is_trial_active ON users(is_trial_active)")
        conn.commit()
    except Exception:
        pass  # Indices already exist

    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


# ============================================
# USERS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_user(email: str, name: str, picture: str, monitoring_type: str, plan: str = "starter", billing_period: str = "monthly", conn=None) -> int:
    """Create a new user. Returns the user id."""
    if plan not in ("starter", "pro"):
        plan = "starter"
    if billing_period not in ("monthly", "yearly"):
        billing_period = "monthly"
    with _use_conn(conn) as c:
        cursor = c.execute(
            "INSERT INTO users (email, name, picture, monitoring_type, plan, billing_period, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (email, name, picture, monitoring_type, plan, billing_period, datetime.utcnow().isoformat())
        )
        c.commit()
        user_id = cursor.lastrowid
        logger.info("Created user id=%d email=%s type=%s plan=%s billing=%s", user_id, email, monitoring_type, plan, billing_period)
        return user_id


def get_user_by_id(user_id: int, conn=None) -> Optional[dict]:
    """Get a user by id."""
    with _use_conn(conn) as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str, conn=None) -> Optional[dict]:
    """Get a user by email."""
    with _use_conn(conn) as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_all_users(conn=None) -> list[dict]:
    """Get all users (excludes sensitive fields like ext_secret)."""
    with _use_conn(conn) as c:
        rows = c.execute(
            "SELECT id, email, name, picture, monitoring_type, plan, billing_period, "
            "scan_frequency, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_users_with_stats(conn=None) -> list[dict]:
    """Get all users with aggregated stats in a single query (avoids N+1)."""
    with _use_conn(conn) as c:
        rows = c.execute("""
            SELECT u.*,
                (SELECT COUNT(*) FROM gmail_accounts WHERE user_id = u.id) as gmail_count,
                (SELECT COUNT(*) FROM processed_orders
                 WHERE user_id = u.id AND monitoring_type = u.monitoring_type) as orders_count,
                (SELECT scanned_at FROM scan_logs
                 WHERE user_id = u.id AND monitoring_type = u.monitoring_type
                 ORDER BY scanned_at DESC LIMIT 1) as last_scan
            FROM users u
            ORDER BY u.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


@retry_on_locked(max_retries=3, base_delay=0.1)
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
        "monitoring_paused", "onboarding_complete", "billing_period",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]

    with _use_conn(conn) as c:
        c.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        c.commit()
        return True


# ============================================
# TRIAL & REFERRAL MANAGEMENT
# ============================================

def activate_trial(user_id: int, conn=None) -> bool:
    """Activate a 14-day free trial for a user. Sets plan to 'pro' during trial."""
    now = datetime.utcnow()
    trial_end = now + timedelta(days=14)
    with _use_conn(conn) as c:
        c.execute(
            """UPDATE users SET
               trial_started_at = ?, trial_ends_at = ?, is_trial_active = 1, plan = 'pro'
               WHERE id = ?""",
            (now.isoformat(), trial_end.isoformat(), user_id)
        )
        c.commit()
        logger.info("Activated trial for user id=%d, expires at %s", user_id, trial_end.isoformat())
        return True


def check_trial_expired(user_id: int, conn=None) -> bool:
    """Check if a user's trial has expired. Returns True if expired."""
    with _use_conn(conn) as c:
        row = c.execute(
            "SELECT trial_ends_at, is_trial_active FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row or not row["is_trial_active"]:
            return False
        if row["trial_ends_at"] is None:
            return False
        trial_end = datetime.fromisoformat(row["trial_ends_at"])
        return datetime.utcnow() > trial_end


@retry_on_locked(max_retries=3, base_delay=0.1)
def expire_trial(user_id: int, conn=None) -> bool:
    """Expire a user's trial, reverting plan to 'starter'."""
    with _use_conn(conn) as c:
        c.execute(
            "UPDATE users SET is_trial_active = 0, plan = 'starter' WHERE id = ?",
            (user_id,)
        )
        c.commit()
        logger.info("Expired trial for user id=%d", user_id)
        return True


def generate_referral_code(user_id: int, conn=None) -> str:
    """Generate a unique 8-character referral code for a user."""
    with _use_conn(conn) as c:
        # Generate random code until unique
        while True:
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            existing = c.execute(
                "SELECT 1 FROM users WHERE referral_code = ?",
                (code,)
            ).fetchone()
            if not existing:
                break

        c.execute("UPDATE users SET referral_code = ? WHERE id = ?", (code, user_id))
        c.commit()
        logger.info("Generated referral code for user id=%d: %s", user_id, code)
        return code


@retry_on_locked(max_retries=3, base_delay=0.1)
def apply_referral(user_id: int, referral_code: str, conn=None) -> bool:
    """
    Link a new user to a referrer and activate trial for both.
    Returns True if successful, False if referral code invalid.
    """
    with _use_conn(conn) as c:
        # Find referrer
        referrer = c.execute(
            "SELECT id FROM users WHERE referral_code = ?",
            (referral_code,)
        ).fetchone()
        if not referrer:
            return False

        referrer_id = referrer["id"]

        # Update new user: set referred_by and activate trial
        c.execute(
            """UPDATE users SET referred_by = ?, is_trial_active = 1, plan = 'pro',
               trial_started_at = ?, trial_ends_at = ?
               WHERE id = ?""",
            (
                referral_code,
                datetime.utcnow().isoformat(),
                (datetime.utcnow() + timedelta(days=14)).isoformat(),
                user_id
            )
        )

        # Increment referrer's count
        c.execute(
            "UPDATE users SET referral_count = referral_count + 1 WHERE id = ?",
            (referrer_id,)
        )

        c.commit()
        logger.info("Applied referral code %s to user id=%d, referrer id=%d",
                   referral_code, user_id, referrer_id)
        return True


def get_user_by_referral_code(code: str, conn=None) -> Optional[dict]:
    """Look up a user by their referral code."""
    with _use_conn(conn) as c:
        row = c.execute(
            "SELECT * FROM users WHERE referral_code = ?",
            (code,)
        ).fetchone()
        return dict(row) if row else None


# ============================================
# GMAIL ACCOUNTS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_gmail_account(
    user_id: int,
    email: str,
    oauth_token: str,
    oauth_refresh_token: str,
    token_expiry: Optional[str] = None,
    is_primary: bool = False,
    conn=None,
) -> int:
    """Create a gmail account entry. Returns the account id."""
    enc_token = encrypt_token(oauth_token)
    enc_refresh = encrypt_token(oauth_refresh_token)
    with _use_conn(conn) as c:
        cursor = c.execute(
            """INSERT INTO gmail_accounts
               (user_id, email, oauth_token, oauth_refresh_token, token_expiry, is_primary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, email, enc_token, enc_refresh,
             token_expiry, 1 if is_primary else 0, datetime.utcnow().isoformat())
        )
        c.commit()
        account_id = cursor.lastrowid
        logger.info("Created gmail_account id=%d user=%d email=%s", account_id, user_id, email)
        return account_id


def _decrypt_account_tokens(account: dict) -> dict:
    """Decrypt oauth_token and oauth_refresh_token in a gmail_account dict."""
    if "oauth_token" in account and account["oauth_token"]:
        account["oauth_token"] = decrypt_token(account["oauth_token"])
    if "oauth_refresh_token" in account and account["oauth_refresh_token"]:
        account["oauth_refresh_token"] = decrypt_token(account["oauth_refresh_token"])
    return account


def get_gmail_account_by_id(account_id: int, conn=None) -> Optional[dict]:
    """Get a gmail account by id."""
    with _use_conn(conn) as c:
        row = c.execute("SELECT * FROM gmail_accounts WHERE id = ?", (account_id,)).fetchone()
        return _decrypt_account_tokens(dict(row)) if row else None


def get_gmail_accounts(user_id: int, conn=None) -> list[dict]:
    """Get all gmail accounts for a user."""
    with _use_conn(conn) as c:
        rows = c.execute(
            "SELECT * FROM gmail_accounts WHERE user_id = ? ORDER BY is_primary DESC, created_at ASC",
            (user_id,)
        ).fetchall()
        return [_decrypt_account_tokens(dict(r)) for r in rows]


@retry_on_locked(max_retries=3, base_delay=0.1)
def update_gmail_account_tokens(account_id: int, oauth_token: str, token_expiry: Optional[str] = None, user_id: int = None, conn=None) -> bool:
    """Update the OAuth token (after refresh). user_id adds defense-in-depth."""
    enc_token = encrypt_token(oauth_token)
    with _use_conn(conn) as c:
        if user_id:
            c.execute(
                "UPDATE gmail_accounts SET oauth_token = ?, token_expiry = ? WHERE id = ? AND user_id = ?",
                (enc_token, token_expiry, account_id, user_id)
            )
        else:
            c.execute(
                "UPDATE gmail_accounts SET oauth_token = ?, token_expiry = ? WHERE id = ?",
                (enc_token, token_expiry, account_id)
            )
        c.commit()
        return True


@retry_on_locked(max_retries=3, base_delay=0.1)
def update_gmail_account_refresh_token(account_id: int, oauth_refresh_token: str, user_id: int = None, conn=None) -> bool:
    """Update the refresh token. user_id adds defense-in-depth."""
    enc_refresh = encrypt_token(oauth_refresh_token)
    with _use_conn(conn) as c:
        if user_id:
            c.execute(
                "UPDATE gmail_accounts SET oauth_refresh_token = ? WHERE id = ? AND user_id = ?",
                (enc_refresh, account_id, user_id)
            )
        else:
            c.execute(
                "UPDATE gmail_accounts SET oauth_refresh_token = ? WHERE id = ?",
                (enc_refresh, account_id)
            )
        c.commit()
        return True


@retry_on_locked(max_retries=3, base_delay=0.1)
def delete_gmail_account(account_id: int, user_id: int = None, conn=None) -> bool:
    """Delete a gmail account. user_id adds defense-in-depth."""
    with _use_conn(conn) as c:
        if user_id:
            c.execute("DELETE FROM gmail_accounts WHERE id = ? AND user_id = ?", (account_id, user_id))
        else:
            c.execute("DELETE FROM gmail_accounts WHERE id = ?", (account_id,))
        c.commit()
        return True


def migrate_encrypt_tokens() -> int:
    """One-time migration: encrypt any plaintext OAuth tokens in gmail_accounts.

    Detects plaintext tokens (those without the 'enc::' prefix) and encrypts them.
    Safe to run multiple times — already-encrypted tokens are skipped.
    Returns the number of rows migrated.
    """
    from crypto import _encryption_enabled
    if not _encryption_enabled:
        logger.warning("migrate_encrypt_tokens: encryption not enabled, skipping")
        return 0

    conn = get_db()
    migrated = 0
    try:
        rows = conn.execute("SELECT id, oauth_token, oauth_refresh_token FROM gmail_accounts").fetchall()
        for row in rows:
            row = dict(row)
            token = row["oauth_token"] or ""
            refresh = row["oauth_refresh_token"] or ""
            needs_update = False

            if token and not is_token_encrypted(token):
                token = encrypt_token(token)
                needs_update = True
            if refresh and not is_token_encrypted(refresh):
                refresh = encrypt_token(refresh)
                needs_update = True

            if needs_update:
                conn.execute(
                    "UPDATE gmail_accounts SET oauth_token = ?, oauth_refresh_token = ? WHERE id = ?",
                    (token, refresh, row["id"])
                )
                migrated += 1

        conn.commit()
        if migrated:
            logger.info("migrate_encrypt_tokens: encrypted tokens for %d accounts", migrated)
        else:
            logger.info("migrate_encrypt_tokens: all tokens already encrypted")
        return migrated
    finally:
        conn.close()


# ============================================
# SPREADSHEETS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_spreadsheet(
    user_id: int,
    spreadsheet_id: str,
    spreadsheet_url: str,
    is_auto_created: bool = True,
    monitoring_type: str = "tickets",
    conn=None,
) -> int:
    """Create a spreadsheet entry. Returns the row id."""
    with _use_conn(conn) as c:
        cursor = c.execute(
            """INSERT INTO spreadsheets
               (user_id, spreadsheet_id, spreadsheet_url, is_auto_created, monitoring_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, spreadsheet_id, spreadsheet_url,
             1 if is_auto_created else 0, monitoring_type, datetime.utcnow().isoformat())
        )
        c.commit()
        row_id = cursor.lastrowid
        logger.info("Created spreadsheet id=%d user=%d type=%s sheet=%s", row_id, user_id, monitoring_type, spreadsheet_id)
        return row_id


def get_spreadsheets(user_id: int, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get spreadsheets for a user, optionally filtered by monitoring_type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            rows = c.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? AND monitoring_type = ? ORDER BY created_at DESC",
                (user_id, monitoring_type)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_primary_spreadsheet(user_id: int, monitoring_type: str = None, conn=None) -> Optional[dict]:
    """Get the most recent spreadsheet for a user, optionally filtered by type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            row = c.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? AND monitoring_type = ? ORDER BY created_at DESC LIMIT 1",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM spreadsheets WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()
        return dict(row) if row else None


@retry_on_locked(max_retries=3, base_delay=0.1)
def delete_spreadsheet(sheet_row_id: int, user_id: int = None, conn=None) -> bool:
    """Delete a spreadsheet entry. user_id adds defense-in-depth."""
    with _use_conn(conn) as c:
        if user_id:
            c.execute("DELETE FROM spreadsheets WHERE id = ? AND user_id = ?", (sheet_row_id, user_id))
        else:
            c.execute("DELETE FROM spreadsheets WHERE id = ?", (sheet_row_id,))
        c.commit()
        return True


# ============================================
# SCAN LOGS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_scan_log(
    user_id: int,
    scan_type: str,
    gmail_account_id: Optional[int] = None,
    orders_found: int = 0,
    status: str = "pending",
    error_message: Optional[str] = None,
    monitoring_type: str = "tickets",
    conn=None,
) -> int:
    """Create a scan log entry. Returns the log id."""
    with _use_conn(conn) as c:
        cursor = c.execute(
            """INSERT INTO scan_logs
               (user_id, gmail_account_id, scan_type, orders_found, status, error_message, monitoring_type, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, gmail_account_id, scan_type, orders_found,
             status, error_message, monitoring_type, datetime.utcnow().isoformat())
        )
        c.commit()
        return cursor.lastrowid


@retry_on_locked(max_retries=3, base_delay=0.1)
def update_scan_log(log_id: int, orders_found: int, status: str, error_message: Optional[str] = None, user_id: int = None, conn=None) -> bool:
    """Update a scan log after completion. user_id adds defense-in-depth."""
    with _use_conn(conn) as c:
        if user_id:
            c.execute(
                "UPDATE scan_logs SET orders_found = ?, status = ?, error_message = ? WHERE id = ? AND user_id = ?",
                (orders_found, status, error_message, log_id, user_id)
            )
        else:
            c.execute(
                "UPDATE scan_logs SET orders_found = ?, status = ?, error_message = ? WHERE id = ?",
                (orders_found, status, error_message, log_id)
            )
        c.commit()
        return True


def get_scan_logs(user_id: int, limit: int = 20, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get recent scan logs for a user, optionally filtered by monitoring_type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            rows = c.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? AND monitoring_type = ? ORDER BY scanned_at DESC LIMIT ?",
                (user_id, monitoring_type, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? ORDER BY scanned_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]


def get_last_scan(user_id: int, monitoring_type: str = None, conn=None) -> Optional[dict]:
    """Get the most recent scan log for a user, optionally filtered by type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            row = c.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? AND monitoring_type = ? ORDER BY scanned_at DESC LIMIT 1",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM scan_logs WHERE user_id = ? ORDER BY scanned_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()
        return dict(row) if row else None


# ============================================
# PROCESSED ORDERS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_processed_order(
    user_id: int,
    order_number: str,
    source: str,
    email_id: str,
    monitoring_type: str = "tickets",
    conn=None,
) -> Optional[int]:
    """Record a processed order. Returns row id or None if duplicate."""
    with _use_conn(conn) as c:
        cursor = c.execute(
            """INSERT OR IGNORE INTO processed_orders
               (user_id, order_number, source, email_id, monitoring_type, processed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, order_number, source, email_id, monitoring_type, datetime.utcnow().isoformat())
        )
        c.commit()
        if cursor.rowcount == 0:
            return None  # duplicate
        return cursor.lastrowid


def is_order_processed(user_id: int, email_id: str, monitoring_type: str = None, conn=None) -> bool:
    """Check if an email has already been processed for a user (and monitoring_type)."""
    with _use_conn(conn) as c:
        if monitoring_type:
            row = c.execute(
                "SELECT 1 FROM processed_orders WHERE user_id = ? AND email_id = ? AND monitoring_type = ?",
                (user_id, email_id, monitoring_type)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT 1 FROM processed_orders WHERE user_id = ? AND email_id = ?",
                (user_id, email_id)
            ).fetchone()
        return row is not None


def get_processed_orders_count(user_id: int, monitoring_type: str = None, conn=None) -> int:
    """Get total processed orders count for a user, optionally filtered by type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            row = c.execute(
                "SELECT COUNT(*) as cnt FROM processed_orders WHERE user_id = ? AND monitoring_type = ?",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COUNT(*) as cnt FROM processed_orders WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        return row["cnt"] if row else 0


def get_processed_orders(user_id: int, limit: int = 50, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get recent processed orders for a user, optionally filtered by type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            rows = c.execute(
                "SELECT * FROM processed_orders WHERE user_id = ? AND monitoring_type = ? ORDER BY processed_at DESC LIMIT ?",
                (user_id, monitoring_type, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM processed_orders WHERE user_id = ? ORDER BY processed_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]


def get_processed_email_ids(user_id: int, monitoring_type: str, conn=None) -> set:
    """Return the set of all email_ids already processed for a user+type.
    Used by scanner for bulk dedup instead of per-message is_order_processed calls."""
    with _use_conn(conn) as c:
        rows = c.execute(
            "SELECT email_id FROM processed_orders WHERE user_id = ? AND monitoring_type = ?",
            (user_id, monitoring_type)
        ).fetchall()
        return {r["email_id"] for r in rows}


# ============================================
# NOTIFICATIONS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_notification(
    user_id: int,
    notif_type: str,
    title: str,
    message: str = "",
    reference_key: str = "",
    monitoring_type: str = "tickets",
    conn=None,
) -> Optional[int]:
    """Create a notification. reference_key prevents duplicates for the same event."""
    with _use_conn(conn) as c:
        # If reference_key provided, skip if already exists (unread)
        if reference_key:
            existing = c.execute(
                "SELECT 1 FROM notifications WHERE user_id = ? AND reference_key = ? AND read = 0",
                (user_id, reference_key)
            ).fetchone()
            if existing:
                return None

        cursor = c.execute(
            """INSERT INTO notifications (user_id, type, title, message, reference_key, monitoring_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, notif_type, title, message, reference_key, monitoring_type, datetime.utcnow().isoformat())
        )
        c.commit()
        return cursor.lastrowid


def get_notifications(user_id: int, limit: int = 30, offset: int = 0, unread_only: bool = False, monitoring_type: str = None, conn=None) -> list[dict]:
    """Get notifications for a user with SQL-level pagination."""
    with _use_conn(conn) as c:
        where = ["user_id = ?"]
        params: list = [user_id]
        if monitoring_type:
            where.append("monitoring_type = ?")
            params.append(monitoring_type)
        if unread_only:
            where.append("read = 0")
        sql = f"SELECT * FROM notifications WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_notifications_count(user_id: int, unread_only: bool = False, monitoring_type: str = None, conn=None) -> int:
    """Get total count of notifications (for pagination metadata)."""
    with _use_conn(conn) as c:
        where = ["user_id = ?"]
        params: list = [user_id]
        if monitoring_type:
            where.append("monitoring_type = ?")
            params.append(monitoring_type)
        if unread_only:
            where.append("read = 0")
        sql = f"SELECT COUNT(*) as cnt FROM notifications WHERE {' AND '.join(where)}"
        row = c.execute(sql, params).fetchone()
        return row["cnt"] if row else 0


def get_unread_notification_count(user_id: int, monitoring_type: str = None, conn=None) -> int:
    """Get count of unread notifications, optionally filtered by type."""
    with _use_conn(conn) as c:
        if monitoring_type:
            row = c.execute(
                "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND monitoring_type = ? AND read = 0",
                (user_id, monitoring_type)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND read = 0",
                (user_id,)
            ).fetchone()
        return row["cnt"] if row else 0


@retry_on_locked(max_retries=3, base_delay=0.1)
def mark_notification_read(notification_id: int, user_id: int, conn=None) -> bool:
    """Mark a single notification as read."""
    with _use_conn(conn) as c:
        c.execute(
            "UPDATE notifications SET read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, user_id)
        )
        c.commit()
        return True


@retry_on_locked(max_retries=3, base_delay=0.1)
def mark_all_notifications_read(user_id: int, conn=None) -> bool:
    """Mark all notifications as read for a user."""
    with _use_conn(conn) as c:
        c.execute(
            "UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0",
            (user_id,)
        )
        c.commit()
        return True


# ============================================
# SERVICES
# ============================================

def get_services(user_email: str, conn=None) -> list[dict]:
    """Get all services for a user, ordered by position."""
    with _use_conn(conn) as c:
        rows = c.execute(
            "SELECT * FROM services WHERE user_email = ? ORDER BY position ASC, created_at ASC",
            (user_email,)
        ).fetchall()
        return [dict(r) for r in rows]


@retry_on_locked(max_retries=3, base_delay=0.1)
def create_service(user_email: str, name: str, unit_price_ht: float = 0.0,
                   tva_rate: float = 20.0, description: str = "", conn=None) -> dict:
    """Create a new service. Returns the created service dict."""
    service_id = str(uuid.uuid4())
    with _use_conn(conn) as c:
        row = c.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM services WHERE user_email = ?",
            (user_email,)
        ).fetchone()
        position = row["next_pos"] if row else 0

        c.execute(
            """INSERT INTO services (id, user_email, name, unit_price_ht, tva_rate, description, position, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (service_id, user_email, name, unit_price_ht, tva_rate, description, position, datetime.utcnow().isoformat())
        )
        c.commit()
        logger.info("Created service id=%s for user=%s", service_id, user_email)
        return {
            "id": service_id, "user_email": user_email, "name": name,
            "unit_price_ht": unit_price_ht, "tva_rate": tva_rate,
            "description": description, "position": position,
        }


@retry_on_locked(max_retries=3, base_delay=0.1)
def update_service(service_id: str, user_email: str, conn=None, **kwargs) -> bool:
    """Update a service. Only updates allowed fields."""
    allowed = {"name", "unit_price_ht", "tva_rate", "description", "position"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [service_id, user_email]

    with _use_conn(conn) as c:
        c.execute(
            f"UPDATE services SET {set_clause} WHERE id = ? AND user_email = ?",
            values
        )
        c.commit()
        return True


@retry_on_locked(max_retries=3, base_delay=0.1)
def delete_service(service_id: str, user_email: str, conn=None) -> bool:
    """Delete a service, checking ownership."""
    with _use_conn(conn) as c:
        cursor = c.execute(
            "DELETE FROM services WHERE id = ? AND user_email = ?",
            (service_id, user_email)
        )
        c.commit()
        return cursor.rowcount > 0


@retry_on_locked(max_retries=3, base_delay=0.1)
def increment_invoice_counter(user_id: int, conn=None) -> int:
    """Atomically increment and return the new invoice counter for a user."""
    with _use_conn(conn) as c:
        # Single atomic UPDATE + SELECT before commit to prevent race conditions
        c.execute(
            "UPDATE users SET invoice_counter = invoice_counter + 1 WHERE id = ?",
            (user_id,)
        )
        row = c.execute(
            "SELECT invoice_counter FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        c.commit()
        return row["invoice_counter"] if row else 1


# ============================================
# EXTENSION — VINTED SESSIONS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def upsert_vinted_session(user_id: int, token: str, domain: str, conn=None) -> bool:
    """Insert or update the Vinted CSRF token for a user."""
    with _use_conn(conn) as c:
        c.execute(
            """INSERT INTO vinted_sessions (user_id, token, domain, synced_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 token = excluded.token,
                 domain = excluded.domain,
                 synced_at = excluded.synced_at""",
            (user_id, token, domain, datetime.utcnow().isoformat())
        )
        c.commit()
        return True


def get_vinted_session(user_id: int, conn=None) -> Optional[dict]:
    """Get the current Vinted session for a user."""
    with _use_conn(conn) as c:
        row = c.execute(
            "SELECT * FROM vinted_sessions WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


@retry_on_locked(max_retries=3, base_delay=0.1)
def delete_vinted_session(user_id: int, conn=None) -> bool:
    """Delete the Vinted session for a user."""
    with _use_conn(conn) as c:
        c.execute("DELETE FROM vinted_sessions WHERE user_id = ?", (user_id,))
        c.commit()
        return True


# ============================================
# EXTENSION — LOGS
# ============================================

@retry_on_locked(max_retries=3, base_delay=0.1)
def create_extension_log(
    user_id: int,
    action_type: str,
    item_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    status: str = "ok",
    error: Optional[str] = None,
    conn=None,
) -> int:
    """Create an extension activity log entry. Returns the log id."""
    with _use_conn(conn) as c:
        cursor = c.execute(
            """INSERT INTO extension_logs
               (user_id, action_type, item_id, target_user_id, status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, action_type, item_id, target_user_id, status, error,
             datetime.utcnow().isoformat())
        )
        c.commit()
        return cursor.lastrowid


def get_extension_logs(user_id: int, limit: int = 50, conn=None) -> list[dict]:
    """Get recent extension logs for a user."""
    with _use_conn(conn) as c:
        rows = c.execute(
            "SELECT * FROM extension_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================
# EXTENSION — CONFIG
# ============================================

def get_extension_config(user_id: int, conn=None) -> dict:
    """Get extension config for a user (from users table extra cols)."""
    with _use_conn(conn) as c:
        row = c.execute(
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


@retry_on_locked(max_retries=3, base_delay=0.1)
def update_extension_config(user_id: int, conn=None, **kwargs) -> bool:
    """Update extension config fields on users table."""
    allowed = {
        "ext_secret", "ext_msg_enabled", "ext_msg_template",
        "ext_msg_quota_daily", "ext_poll_interval_min",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    # Auto-compute ext_secret_hash when ext_secret changes
    if "ext_secret" in fields and fields["ext_secret"]:
        import hashlib
        fields["ext_secret_hash"] = hashlib.sha256(fields["ext_secret"].encode()).hexdigest()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    with _use_conn(conn) as c:
        c.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        c.commit()
        return True
