"""
Trial and referral routes: 14-day trial activation, referral codes, status.
"""

import logging
from datetime import datetime
from flask import Blueprint, session, request, jsonify
import database as db
from helpers import login_required

logger = logging.getLogger(__name__)

trial_bp = Blueprint("trial", __name__)


# ============================================
# TRIAL ROUTES
# ============================================

@trial_bp.route("/api/activate-trial", methods=["POST"])
@login_required
def activate_trial_route():
    """Activate a 14-day Pro trial for the current user.

    Checks if user already has an active trial to prevent abuse.
    Returns trial_ends_at timestamp on success.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    # Check if user already has an active trial
    if user.get("is_trial_active"):
        return jsonify({
            "success": False,
            "error": "Vous avez deja un essai actif",
            "trial_ends_at": user.get("trial_ends_at")
        }), 403

    # Activate trial
    success = db.activate_trial(user_id)

    if not success:
        return jsonify({
            "success": False,
            "error": "Erreur lors de l'activation de l'essai"
        }), 500

    # Fetch updated user
    updated_user = db.get_user_by_id(user_id)

    logger.info("Trial activated for user id=%d", user_id)
    return jsonify({
        "success": True,
        "message": "Essai Pro activate pour 14 jours",
        "trial_ends_at": updated_user.get("trial_ends_at"),
        "plan": "pro"
    }), 200


@trial_bp.route("/api/trial-status", methods=["GET"])
@login_required
def trial_status():
    """Get current trial status for the user.

    Returns:
        - is_trial_active (bool)
        - trial_ends_at (str, ISO format)
        - days_remaining (int)
        - plan (str)
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    is_trial_active = bool(user.get("is_trial_active", 0))
    trial_ends_at = user.get("trial_ends_at")
    plan = user.get("plan", "starter")

    days_remaining = None
    if is_trial_active and trial_ends_at:
        try:
            trial_end = datetime.fromisoformat(trial_ends_at)
            now = datetime.utcnow()
            days_remaining = max(0, (trial_end - now).days)
        except (ValueError, TypeError):
            days_remaining = None

    return jsonify({
        "success": True,
        "is_trial_active": is_trial_active,
        "trial_ends_at": trial_ends_at,
        "days_remaining": days_remaining,
        "plan": plan
    }), 200


# ============================================
# REFERRAL ROUTES
# ============================================

@trial_bp.route("/api/referral/generate", methods=["POST"])
@login_required
def generate_referral_code_route():
    """Generate a unique referral code for the current user.

    Returns the generated code.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    # Check if user already has a referral code
    existing_code = user.get("referral_code")
    if existing_code:
        return jsonify({
            "success": True,
            "code": existing_code,
            "message": "Code de parrainage deja genere"
        }), 200

    # Generate new code
    try:
        code = db.generate_referral_code(user_id)
        logger.info("Generated referral code for user id=%d: %s", user_id, code)
        return jsonify({
            "success": True,
            "code": code,
            "message": "Code de parrainage genere"
        }), 200
    except Exception as exc:
        logger.error("Failed to generate referral code for user id=%d: %s", user_id, exc)
        return jsonify({
            "success": False,
            "error": "Erreur lors de la generation du code"
        }), 500


@trial_bp.route("/api/referral/status", methods=["GET"])
@login_required
def referral_status():
    """Get referral status for the current user.

    Returns:
        - referral_code (str)
        - referral_count (int)
        - referred_by (str or null)
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    return jsonify({
        "success": True,
        "referral_code": user.get("referral_code"),
        "referral_count": user.get("referral_count", 0),
        "referred_by": user.get("referred_by")
    }), 200


@trial_bp.route("/api/referral/apply", methods=["POST"])
@login_required
def apply_referral_code():
    """Apply a referral code to the current user.

    Validates the code, applies it, and activates trial for the new user.
    Also extends/activates trial for the referrer.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    # Check if user already used a referral code
    if user.get("referred_by"):
        return jsonify({
            "success": False,
            "error": "Vous avez deja utilise un code de parrainage"
        }), 403

    data = request.get_json(silent=True) or {}
    referral_code = data.get("referral_code", "").strip()

    if not referral_code:
        return jsonify({
            "success": False,
            "error": "Code de parrainage requis"
        }), 400

    # Validate code exists
    referrer = db.get_user_by_referral_code(referral_code)
    if not referrer:
        return jsonify({
            "success": False,
            "error": "Code de parrainage invalide"
        }), 404

    if referrer["id"] == user_id:
        return jsonify({
            "success": False,
            "error": "Vous ne pouvez pas vous parrainer vous-meme"
        }), 403

    # Apply referral
    try:
        success = db.apply_referral(user_id, referral_code)

        if not success:
            return jsonify({
                "success": False,
                "error": "Erreur lors de l'application du code"
            }), 500

        # Optionally extend referrer's trial
        # (This could be implemented separately as a reward system)

        logger.info("Referral code %s applied to user id=%d by referrer id=%d",
                   referral_code, user_id, referrer["id"])

        return jsonify({
            "success": True,
            "message": "Code de parrainage applique. Vous avez 14 jours gratuits!",
            "trial_activated": True,
            "plan": "pro"
        }), 200

    except Exception as exc:
        logger.error("Failed to apply referral code for user id=%d: %s", user_id, exc)
        return jsonify({
            "success": False,
            "error": "Erreur lors de l'application du code"
        }), 500


# ============================================
# MIDDLEWARE - Trial Checker
# ============================================

def check_and_expire_trials():
    """Check if authenticated user's trial has expired and expire if needed.

    Call this from app.before_request or as a middleware.
    Caches result in session to avoid DB queries on every request.
    """
    import time as _time
    from flask import session

    user_id = session.get("user_id")
    if not user_id:
        return

    # Skip check if already verified within the last 5 minutes
    last_check = session.get("_trial_checked_at", 0)
    if _time.time() - last_check < 300:
        return

    user = db.get_user_by_id(user_id)
    if not user or not user.get("is_trial_active"):
        session["_trial_checked_at"] = _time.time()
        return

    # Check if trial has expired
    if db.check_trial_expired(user_id):
        db.expire_trial(user_id)
        logger.info("Trial expired for user id=%d, reverted to starter plan", user_id)
        session["trial_expired"] = True

    session["_trial_checked_at"] = _time.time()
