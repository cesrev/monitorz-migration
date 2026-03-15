"""
Billets & Vinted Monitor MVP - Flask Application
Google OAuth flow, dashboard, API routes.
"""

import os
import logging

# Allow HTTP for local development only (OAuth2 requires HTTPS by default)
if os.getenv("FLASK_ENV") in ("development", "dev"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from flask import Flask, render_template, redirect, url_for, session
from flask_compress import Compress
from extensions import limiter
import database as db
from config import SECRET_KEY, APP_URL

# ============================================
# LOGGING
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP SETUP
# ============================================

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"

limiter.init_app(app)
Compress(app)

# ============================================
# SECURITY & MIDDLEWARE
# ============================================

@app.before_request
def _csrf_check():
    """Reject cross-origin POST/PUT/DELETE requests (Origin/Referer validation)."""
    from flask import request, jsonify
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # Skip CSRF for extension API (authenticated via X-Extension-Secret header)
    if request.headers.get("X-Extension-Secret"):
        return
    # Skip for OAuth callback
    if request.path == "/oauth/callback":
        return
    origin = request.headers.get("Origin") or ""
    referer = request.headers.get("Referer") or ""
    allowed = APP_URL.rstrip("/")
    if origin:
        if not origin.startswith(allowed):
            logger.warning("CSRF blocked: origin=%s expected=%s", origin, allowed)
            return jsonify({"error": "Cross-origin request blocked"}), 403
    elif referer:
        if not referer.startswith(allowed):
            logger.warning("CSRF blocked: referer=%s expected=%s", referer, allowed)
            return jsonify({"error": "Cross-origin request blocked"}), 403
    else:
        # Both Origin and Referer are missing — block the request
        logger.warning("CSRF blocked: no Origin or Referer header from %s", request.remote_addr)
        return jsonify({"error": "Cross-origin request blocked"}), 403


@app.before_request
def _check_trial_expiry():
    """Check and expire trials on each authenticated request."""
    from routes.trial import check_and_expire_trials
    check_and_expire_trials()


@app.after_request
def set_security_headers(response):
    """Inject standard security headers into every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' https: data:; "
        "connect-src 'self'"
    )
    if os.getenv("FLASK_ENV") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ============================================
# ROUTES - PAGES (not in blueprints)
# ============================================

@app.route("/")
def index():
    """Landing page."""
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/login")
def login():
    """Login page."""
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    """Client dashboard — filtered by active monitoring_type profile."""
    from helpers import login_required as login_required_decorator
    @login_required_decorator
    def _dashboard():
        user_id = session["user_id"]
        user = db.get_user_by_id(user_id)
        mtype = user["monitoring_type"] if user else "tickets"
        accounts = db.get_gmail_accounts(user_id)
        sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
        last_scan = db.get_last_scan(user_id, monitoring_type=mtype)
        orders_count = db.get_processed_orders_count(user_id, monitoring_type=mtype)
        unread_count = db.get_unread_notification_count(user_id, monitoring_type=mtype)

        return render_template(
            "dashboard.html",
            user=user,
            accounts=accounts,
            sheets=sheets,
            last_scan=last_scan,
            orders_count=orders_count,
            unread_count=unread_count,
        )
    return _dashboard()

# ============================================
# REGISTER BLUEPRINTS
# ============================================

from routes.auth import auth_bp
from routes.sheets import sheets_bp
from routes.scan import scan_bp
from routes.tickets import tickets_bp
from routes.vinted import vinted_bp
from routes.invoice import invoice_bp
from routes.admin import admin_bp
from routes.api import api_bp
from routes.extension import extension_bp
from routes.trial import trial_bp
from routes.export import export_bp

app.register_blueprint(auth_bp)
app.register_blueprint(sheets_bp)
app.register_blueprint(scan_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(vinted_bp)
app.register_blueprint(invoice_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)
app.register_blueprint(extension_bp)
app.register_blueprint(trial_bp)
app.register_blueprint(export_bp)

# ============================================
# INITIALIZATION
# ============================================

db.init_db()

# Only start background scanner in dev (single-process) mode.
# In production, use cron.py as a separate process.
if os.getenv("FLASK_ENV") != "production":
    from routes.scan import start_background_scanner
    start_background_scanner()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_ENV") != "production", port=5050)
