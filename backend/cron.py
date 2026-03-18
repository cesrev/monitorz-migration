#!/usr/bin/env python3
"""
Billets & Vinted Monitor MVP - Cron Script
Run this script every hour via crontab to scan all users.

Example crontab entry:
    0 * * * * cd /path/to/backend && /path/to/venv/bin/python cron.py >> /var/log/monitor-cron.log 2>&1
"""

import logging
import os
import sys
from datetime import datetime

# Ensure the backend directory is in the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging before any imports that use it
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("cron")


def main() -> None:
    """Run the hourly scan for all users."""
    logger.info("=== Cron scan started at %s ===", datetime.utcnow().isoformat())

    try:
        import database as db
        db.init_db()

        from scanner import scan_all_users
        results = scan_all_users()

        total_orders = sum(results.values())
        logger.info(
            "=== Cron scan finished: %d user(s) scanned, %d total order(s) found ===",
            len(results),
            total_orders,
        )

    except Exception as exc:
        logger.error("Cron scan failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
