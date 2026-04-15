"""
Billing routes — mock mode (Stripe not yet integrated).
Returns stub responses so the frontend does not break.
"""

from flask import Blueprint, jsonify, session
from helpers import login_required

billing_bp = Blueprint("billing", __name__)


@billing_bp.route("/billing/checkout", methods=["POST"])
@login_required
def checkout():
    """Stub: Stripe Checkout Session creation (not yet live)."""
    return jsonify({
        "success": False,
        "mock": True,
        "message": "Paiement non encore disponible — bientot.",
    }), 503


@billing_bp.route("/billing/portal", methods=["POST"])
@login_required
def portal():
    """Stub: Stripe Customer Portal (not yet live)."""
    return jsonify({
        "success": False,
        "mock": True,
        "message": "Portail client non encore disponible.",
    }), 503


@billing_bp.route("/stripe/webhook", methods=["POST"])
def webhook():
    """Stub: Stripe webhook endpoint (not yet live)."""
    return jsonify({"received": True, "mock": True}), 200
