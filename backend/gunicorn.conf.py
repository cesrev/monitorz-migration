"""Gunicorn configuration for production deployment."""
import os

bind = f"0.0.0.0:{os.getenv('PORT', '5050')}"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
threads = 2
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
