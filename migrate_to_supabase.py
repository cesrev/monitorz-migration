"""
Migration SQLite → Supabase
Tables: users, gmail_accounts, spreadsheets, scan_logs,
        processed_orders, notifications, vinted_sessions
"""

import sqlite3
import os
import logging
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
CHUNK = 100


def insert_chunks(table, rows):
    if not rows:
        log.info(f"  {table}: vide, skip")
        return
    for i in range(0, len(rows), CHUNK):
        sb.table(table).insert(rows[i:i + CHUNK]).execute()
    log.info(f"  {table}: {len(rows)} lignes ✓")


def main():
    conn = sqlite3.connect("monitor.db")
    conn.row_factory = sqlite3.Row

    log.info("=== Migration SQLite → Supabase ===\n")

    tables = [
        "users",
        "gmail_accounts",
        "spreadsheets",
        "scan_logs",
        "processed_orders",
        "notifications",
        "vinted_sessions",
    ]

    for table in tables:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        insert_chunks(table, rows)

    conn.close()

    log.info("\n=== Migration terminée ===")
    log.info("Lance maintenant ce SQL dans Supabase pour resynchroniser les séquences :")
    log.info("""
SELECT setval(pg_get_serial_sequence('users', 'id'), MAX(id)) FROM users;
SELECT setval(pg_get_serial_sequence('gmail_accounts', 'id'), MAX(id)) FROM gmail_accounts;
SELECT setval(pg_get_serial_sequence('spreadsheets', 'id'), MAX(id)) FROM spreadsheets;
SELECT setval(pg_get_serial_sequence('scan_logs', 'id'), MAX(id)) FROM scan_logs;
SELECT setval(pg_get_serial_sequence('processed_orders', 'id'), MAX(id)) FROM processed_orders;
SELECT setval(pg_get_serial_sequence('notifications', 'id'), MAX(id)) FROM notifications;
""")


if __name__ == "__main__":
    main()
