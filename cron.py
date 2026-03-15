#!/usr/bin/env python3
"""
Billets & Vinted Monitor MVP - Background Scanner Process

In production (Railway): runs as a persistent worker, scans every 8 hours.
Locally: not used (the background thread in app.py handles it).

Railway Procfile entry:
    scanner: python cron.py
"""

import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("cron")

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", str(8 * 60 * 60)))  # 8h default


def run_scan() -> None:
    logger.info("=== Scan started at %s ===", datetime.utcnow().isoformat())
    try:
        import database as db
        db.init_db()
        from scanner import scan_all_users
        results = scan_all_users()
        total = sum(results.values())
        logger.info("=== Scan finished: %d user(s), %d order(s) found ===", len(results), total)
    except Exception as exc:
        logger.error("Scan failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    logger.info("Scanner worker started (interval=%dh)", SCAN_INTERVAL_SECONDS // 3600)
    while True:
        run_scan()
        logger.info("Next scan in %dh", SCAN_INTERVAL_SECONDS // 3600)
        time.sleep(SCAN_INTERVAL_SECONDS)
