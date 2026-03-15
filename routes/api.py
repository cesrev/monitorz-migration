"""
API routes: stats, analytics, notifications, alerts, planning, onboarding.
"""

import logging
from flask import Blueprint, session, request, jsonify, render_template
from googleapiclient.discovery import build
import database as db
from helpers import login_required, _get_sheet_data_cached, _parse_price, paginate_list, get_google_credentials, _parse_month_year

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


@api_bp.route("/api/stats")
@login_required
def stats():
    """Return stats for the current user."""
    user_id = session["user_id"]

    user = db.get_user_by_id(user_id)
    mtype = user["monitoring_type"] if user else "tickets"
    accounts = db.get_gmail_accounts(user_id)
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    last_scan = db.get_last_scan(user_id, monitoring_type=mtype)
    orders_count = db.get_processed_orders_count(user_id, monitoring_type=mtype)
    recent_logs = db.get_scan_logs(user_id, limit=10, monitoring_type=mtype)

    return jsonify({
        "success": True,
        "user": {
            "email": user["email"] if user else "",
            "name": user["name"] if user else "",
            "monitoring_type": mtype,
            "plan": user["plan"] if user else "starter",
        },
        "gmail_accounts": [
            {"id": a["id"], "email": a["email"], "is_primary": bool(a["is_primary"])}
            for a in accounts
        ],
        "spreadsheets": [
            {
                "id": s["id"],
                "spreadsheet_id": s["spreadsheet_id"],
                "spreadsheet_url": s["spreadsheet_url"],
                "is_auto_created": bool(s["is_auto_created"]),
            }
            for s in sheets
        ],
        "orders_count": orders_count,
        "last_scan": {
            "scanned_at": last_scan["scanned_at"],
            "status": last_scan["status"],
            "orders_found": last_scan["orders_found"],
        } if last_scan else None,
        "recent_scans": [
            {
                "scanned_at": log["scanned_at"],
                "status": log["status"],
                "orders_found": log["orders_found"],
                "error_message": log["error_message"],
            }
            for log in recent_logs
        ],
    })


@api_bp.route("/api/analytics")
@login_required
def analytics():
    """Return monthly analytics: spent, received, profit. Works for both tickets and vinted."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 403

    mtype = user.get("monitoring_type", "tickets")
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": True, "spent": 0, "received": 0, "profit": 0})

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return jsonify({"success": True, "spent": 0, "received": 0, "profit": 0})

    try:
        from datetime import datetime
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:J")

        if len(rows) < 2:
            return jsonify({"success": True, "spent": 0, "received": 0, "profit": 0})

        now = datetime.now()
        current_month = now.month
        current_year = now.year

        total_spent = 0.0
        total_received = 0.0

        if mtype == "tickets":
            # Tickets: A=Événement B=Catégorie C=Lieu D=Date E=Prix Achat
            #          F=N° Commande G=Lien H=Compte I=Prix Vente
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue
                date_str = row[3].strip() if len(row) > 3 else ""
                purchase_price_str = row[4].strip() if len(row) > 4 else ""
                sale_price_str = row[8].strip() if len(row) > 8 else ""

                r_month, r_year = _parse_month_year(date_str)
                if r_month == current_month and r_year == current_year:
                    val = _parse_price(purchase_price_str)
                    if val > 0:
                        total_spent += val
                    val_sale = _parse_price(sale_price_str)
                    if val_sale > 0:
                        total_received += val_sale
        else:
            # Vinted: A=Article B=Prix Achat C=Date Achat D=Prix Vente E=Date Vente
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue
                purchase_price_str = row[1].strip() if len(row) > 1 else ""
                purchase_date_str = row[2].strip() if len(row) > 2 else ""
                sale_price_str = row[3].strip() if len(row) > 3 else ""
                sale_date_str = row[4].strip() if len(row) > 4 else ""

                if purchase_price_str and purchase_date_str:
                    p_month, p_year = _parse_month_year(purchase_date_str)
                    if p_month == current_month and p_year == current_year:
                        val = _parse_price(purchase_price_str)
                        if val > 0:
                            total_spent += val

                if sale_price_str and sale_date_str:
                    s_month, s_year = _parse_month_year(sale_date_str)
                    if s_month == current_month and s_year == current_year:
                        val = _parse_price(sale_price_str)
                        if val > 0:
                            total_received += val

        profit = total_received - total_spent
        monthly_costs = float(user.get("monthly_costs", 0) or 0)
        net_profit = profit - monthly_costs
        roi = round((profit / total_spent * 100), 1) if total_spent > 0 else 0.0

        return jsonify({
            "success": True,
            "spent": round(total_spent, 2),
            "received": round(total_received, 2),
            "profit": round(profit, 2),
            "monthly_costs": monthly_costs,
            "net_profit": round(net_profit, 2),
            "roi": roi,
        })

    except Exception as exc:
        logger.error("Failed to get analytics: %s", exc)
        return jsonify({"success": False, "error": "Erreur de lecture du Sheet"}), 500


@api_bp.route("/api/monthly-costs", methods=["POST"])
@login_required
def update_monthly_costs():
    """Update the user's monthly costs."""
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    try:
        costs = max(0, float(data.get("monthly_costs", 0)))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Montant invalide"}), 400
    db.update_user(user_id, monthly_costs=costs)
    return jsonify({"success": True, "monthly_costs": costs})


@api_bp.route("/api/analytics/dashboard")
@login_required
def analytics_dashboard():
    """Return comprehensive dashboard analytics with revenue, inventory, and performance metrics."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 403

    mtype = user.get("monitoring_type", "tickets")
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({
            "success": True,
            "revenue": {
                "total_purchases": 0,
                "total_sales": 0,
                "total_profit": 0,
                "roi_percent": 0,
                "this_month": {"purchases": 0, "sales": 0, "profit": 0},
                "last_month": {"purchases": 0, "sales": 0, "profit": 0},
                "monthly_trend": []
            },
            "inventory": {
                "total_items": 0,
                "sold_items": 0,
                "unsold_items": 0,
                "avg_days_in_stock": 0,
                "sell_rate_percent": 0
            },
            "performance": {
                "avg_profit_per_item": 0,
                "best_month": None,
                "worst_month": None,
                "avg_sell_time_days": 0
            }
        })

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return jsonify({
            "success": True,
            "revenue": {
                "total_purchases": 0,
                "total_sales": 0,
                "total_profit": 0,
                "roi_percent": 0,
                "this_month": {"purchases": 0, "sales": 0, "profit": 0},
                "last_month": {"purchases": 0, "sales": 0, "profit": 0},
                "monthly_trend": []
            },
            "inventory": {
                "total_items": 0,
                "sold_items": 0,
                "unsold_items": 0,
                "avg_days_in_stock": 0,
                "sell_rate_percent": 0
            },
            "performance": {
                "avg_profit_per_item": 0,
                "best_month": None,
                "worst_month": None,
                "avg_sell_time_days": 0
            }
        })

    def _parse_date(d):
        """Parse date string to datetime object."""
        if not d:
            return None
        from datetime import datetime
        d = d.strip()
        # Try DD/MM/YYYY format
        try:
            return datetime.strptime(d, "%d/%m/%Y")
        except ValueError:
            pass
        # Try YYYY-MM-DD format
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            pass
        return None

    try:
        from datetime import datetime, timedelta
        from collections import defaultdict

        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:J")

        if len(rows) < 2:
            return jsonify({
                "success": True,
                "revenue": {
                    "total_purchases": 0,
                    "total_sales": 0,
                    "total_profit": 0,
                    "roi_percent": 0,
                    "this_month": {"purchases": 0, "sales": 0, "profit": 0},
                    "last_month": {"purchases": 0, "sales": 0, "profit": 0},
                    "monthly_trend": []
                },
                "inventory": {
                    "total_items": 0,
                    "sold_items": 0,
                    "unsold_items": 0,
                    "avg_days_in_stock": 0,
                    "sell_rate_percent": 0
                },
                "performance": {
                    "avg_profit_per_item": 0,
                    "best_month": None,
                    "worst_month": None,
                    "avg_sell_time_days": 0
                }
            })

        now = datetime.now()
        current_month = now.month
        current_year = now.year
        last_month_date = now - timedelta(days=30)
        last_month_num = last_month_date.month
        last_month_year = last_month_date.year

        # Storage for metrics
        monthly_data = defaultdict(lambda: {"purchases": 0.0, "sales": 0.0, "profit": 0.0})
        total_purchases = 0.0
        total_sales = 0.0
        this_month_purchases = 0.0
        this_month_sales = 0.0
        last_month_purchases = 0.0
        last_month_sales = 0.0

        inventory_items = []
        sold_count = 0
        unsold_count = 0
        sell_times = []

        if mtype == "tickets":
            # Tickets: A=Événement B=Catégorie C=Lieu D=Date E=Prix Achat
            #          F=N° Commande G=Lien H=Compte I=Prix Vente
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue

                date_str = row[3].strip() if len(row) > 3 else ""
                purchase_price_str = row[4].strip() if len(row) > 4 else ""
                sale_price_str = row[8].strip() if len(row) > 8 else ""

                r_month, r_year = _parse_month_year(date_str)
                if r_month and r_year:
                    purchase_val = _parse_price(purchase_price_str)
                    sale_val = _parse_price(sale_price_str)
                    profit_val = sale_val - purchase_val

                    month_key = f"{r_year}-{r_month:02d}"
                    if purchase_val > 0:
                        monthly_data[month_key]["purchases"] += purchase_val
                        total_purchases += purchase_val
                        if r_month == current_month and r_year == current_year:
                            this_month_purchases += purchase_val
                        elif r_month == last_month_num and r_year == last_month_year:
                            last_month_purchases += purchase_val

                    if sale_val > 0:
                        monthly_data[month_key]["sales"] += sale_val
                        total_sales += sale_val
                        monthly_data[month_key]["profit"] += profit_val
                        if r_month == current_month and r_year == current_year:
                            this_month_sales += sale_val
                        elif r_month == last_month_num and r_year == last_month_year:
                            last_month_sales += sale_val
                        sold_count += 1
                    else:
                        unsold_count += 1

                    inventory_items.append({
                        "purchase_price": purchase_val,
                        "sale_price": sale_val,
                        "purchase_date": date_str
                    })
        else:
            # Vinted: A=Article B=Prix Achat C=Date Achat D=Prix Vente E=Date Vente
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue

                purchase_price_str = row[1].strip() if len(row) > 1 else ""
                purchase_date_str = row[2].strip() if len(row) > 2 else ""
                sale_price_str = row[3].strip() if len(row) > 3 else ""
                sale_date_str = row[4].strip() if len(row) > 4 else ""

                # Handle purchase
                if purchase_price_str and purchase_date_str:
                    p_month, p_year = _parse_month_year(purchase_date_str)
                    if p_month and p_year:
                        purchase_val = _parse_price(purchase_price_str)
                        purchase_date_obj = _parse_date(purchase_date_str)

                        if purchase_val > 0:
                            month_key = f"{p_year}-{p_month:02d}"
                            monthly_data[month_key]["purchases"] += purchase_val
                            total_purchases += purchase_val
                            if p_month == current_month and p_year == current_year:
                                this_month_purchases += purchase_val
                            elif p_month == last_month_num and p_year == last_month_year:
                                last_month_purchases += purchase_val

                            inventory_items.append({
                                "purchase_price": purchase_val,
                                "sale_price": 0.0,
                                "purchase_date": purchase_date_str,
                                "sale_date": sale_date_str,
                                "purchase_date_obj": purchase_date_obj
                            })

                # Handle sale
                if sale_price_str and sale_date_str:
                    s_month, s_year = _parse_month_year(sale_date_str)
                    if s_month and s_year:
                        sale_val = _parse_price(sale_price_str)
                        sale_date_obj = _parse_date(sale_date_str)

                        if sale_val > 0:
                            purchase_val = _parse_price(purchase_price_str) if purchase_price_str else 0.0
                            profit_val = sale_val - purchase_val

                            month_key = f"{s_year}-{s_month:02d}"
                            monthly_data[month_key]["sales"] += sale_val
                            monthly_data[month_key]["profit"] += profit_val
                            total_sales += sale_val

                            if s_month == current_month and s_year == current_year:
                                this_month_sales += sale_val
                            elif s_month == last_month_num and s_year == last_month_year:
                                last_month_sales += sale_val

                            sold_count += 1

                            # Calculate sell time
                            if purchase_date_obj and sale_date_obj:
                                days_to_sell = (sale_date_obj - purchase_date_obj).days
                                if days_to_sell >= 0:
                                    sell_times.append(days_to_sell)

        # Calculate derived metrics
        total_profit = total_sales - total_purchases
        this_month_profit = this_month_sales - this_month_purchases
        last_month_profit = last_month_sales - last_month_purchases

        roi_percent = 0.0
        if total_purchases > 0:
            roi_percent = (total_profit / total_purchases) * 100

        avg_profit_per_item = 0.0
        if sold_count > 0:
            avg_profit_per_item = total_profit / sold_count

        avg_sell_time_days = 0.0
        if sell_times:
            avg_sell_time_days = sum(sell_times) / len(sell_times)

        # Calculate average days in stock
        avg_days_in_stock = 0.0
        if inventory_items:
            total_days = 0
            count = 0
            for item in inventory_items:
                if item.get("purchase_date_obj") and item.get("purchase_date"):
                    if item.get("sale_date"):
                        sale_date_obj = _parse_date(item["sale_date"])
                        if sale_date_obj:
                            days = (sale_date_obj - item["purchase_date_obj"]).days
                            if days >= 0:
                                total_days += days
                                count += 1
                    else:
                        days = (now - item["purchase_date_obj"]).days
                        if days >= 0:
                            total_days += days
                            count += 1
            if count > 0:
                avg_days_in_stock = total_days / count

        # Find best and worst months
        best_month = None
        worst_month = None
        if monthly_data:
            monthly_items = list(monthly_data.items())
            monthly_items.sort(key=lambda x: x[1]["profit"], reverse=True)
            if monthly_items[0][1]["profit"] > 0:
                best_month = {"month": monthly_items[0][0], "profit": round(monthly_items[0][1]["profit"], 2)}
            if monthly_items[-1][1]["profit"] < 0:
                worst_month = {"month": monthly_items[-1][0], "profit": round(monthly_items[-1][1]["profit"], 2)}

        # Build monthly trend
        monthly_trend = []
        for month_key in sorted(monthly_data.keys()):
            data = monthly_data[month_key]
            monthly_trend.append({
                "month": month_key,
                "purchases": round(data["purchases"], 2),
                "sales": round(data["sales"], 2),
                "profit": round(data["profit"], 2)
            })

        total_items = sold_count + unsold_count
        sell_rate_percent = 0.0
        if total_items > 0:
            sell_rate_percent = (sold_count / total_items) * 100

        return jsonify({
            "success": True,
            "revenue": {
                "total_purchases": round(total_purchases, 2),
                "total_sales": round(total_sales, 2),
                "total_profit": round(total_profit, 2),
                "roi_percent": round(roi_percent, 2),
                "this_month": {
                    "purchases": round(this_month_purchases, 2),
                    "sales": round(this_month_sales, 2),
                    "profit": round(this_month_profit, 2)
                },
                "last_month": {
                    "purchases": round(last_month_purchases, 2),
                    "sales": round(last_month_sales, 2),
                    "profit": round(last_month_profit, 2)
                },
                "monthly_trend": monthly_trend
            },
            "inventory": {
                "total_items": total_items,
                "sold_items": sold_count,
                "unsold_items": unsold_count,
                "avg_days_in_stock": round(avg_days_in_stock, 1),
                "sell_rate_percent": round(sell_rate_percent, 2)
            },
            "performance": {
                "avg_profit_per_item": round(avg_profit_per_item, 2),
                "best_month": best_month,
                "worst_month": worst_month,
                "avg_sell_time_days": round(avg_sell_time_days, 1)
            }
        })

    except Exception as exc:
        logger.error("Failed to get dashboard analytics: %s", exc)
        return jsonify({"success": False, "error": "Erreur de lecture du Sheet"}), 500


@api_bp.route("/api/notifications")
@login_required
def get_notifications():
    """Get notifications for the current user (tickets only) with pagination support."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": True, "data": [], "unread_count": 0, "pagination": {
            "page": 1,
            "per_page": 50,
            "total": 0,
            "total_pages": 0,
            "has_next": False,
            "has_prev": False
        }})

    unread_only = request.args.get("unread") == "1"
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(100, request.args.get("per_page", 50, type=int)))
    offset = (page - 1) * per_page

    # SQL-level pagination (no more fetching 500 rows to slice in Python)
    notifications = db.get_notifications(user_id, limit=per_page, offset=offset, unread_only=unread_only)
    total = db.get_notifications_count(user_id, unread_only=unread_only)
    unread_count = db.get_unread_notification_count(user_id)
    total_pages = (total + per_page - 1) // per_page

    return jsonify({
        "success": True,
        "notifications": [dict(n) for n in notifications],
        "unread_count": unread_count,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        }
    })


@api_bp.route("/api/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    """Mark notification(s) as read (tickets only)."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Non disponible"}), 403

    data = request.get_json(silent=True) or {}

    notif_id = data.get("id")
    if notif_id:
        db.mark_notification_read(int(notif_id), user_id)
    else:
        db.mark_all_notifications_read(user_id)

    return jsonify({"success": True})


@api_bp.route("/api/update-alert-settings", methods=["POST"])
@login_required
def update_alert_settings():
    """Update alert thresholds (days before event, dormant stock days)."""
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}

    updates = {}

    if "alert_days_before" in data:
        try:
            val = int(data["alert_days_before"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Valeur invalide pour alert_days_before"}), 400
        if 1 <= val <= 60:
            updates["alert_days_before"] = val

    if "dormant_days_threshold" in data:
        try:
            val = int(data["dormant_days_threshold"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Valeur invalide pour dormant_days_threshold"}), 400
        if 1 <= val <= 365:
            updates["dormant_days_threshold"] = val

    if updates:
        db.update_user(user_id, **updates)
        logger.info("User id=%d updated alert settings: %s", user_id, updates)
        return jsonify({"success": True, **updates})

    return jsonify({"success": False, "error": "Aucun parametre valide"}), 400


@api_bp.route("/api/organize-tabs", methods=["POST"])
@login_required
def organize_tabs():
    """Organize tickets into per-artist/event Sheet tabs (Pro only)."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs Tickets"}), 403

    if user.get("plan") != "pro":
        return jsonify({"success": False, "error": "Feature reservee au plan Pro"}), 403

    try:
        from scanner import organize_ticket_tabs
        result = organize_ticket_tabs(user_id)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 400
        return jsonify({"success": True, **result})
    except Exception as exc:
        logger.error("organize_tabs failed for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": "Erreur lors de l'organisation des onglets"}), 500


@api_bp.route("/api/update-plan", methods=["POST"])
@login_required
def update_plan():
    """Update the user's plan (starter/pro)."""
    user_id = session["user_id"]
    data = request.get_json(silent=True)

    if not data or not data.get("plan"):
        return jsonify({"success": False, "error": "Plan requis"}), 400

    new_plan = data["plan"]
    if new_plan not in ("starter", "pro"):
        return jsonify({"success": False, "error": "Plan invalide"}), 400

    db.update_user(user_id, plan=new_plan)
    logger.info("User id=%d updated plan to %s", user_id, new_plan)
    return jsonify({"success": True, "plan": new_plan})


@api_bp.route("/api/toggle-monitoring", methods=["POST"])
@login_required
def toggle_monitoring():
    """Pause or resume monitoring for the current user."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 403
    new_state = 0 if user.get("monitoring_paused", 0) else 1
    db.update_user(user_id, monitoring_paused=new_state)
    label = "pause" if new_state else "actif"
    logger.info("User id=%d monitoring now %s", user_id, label)
    return jsonify({"success": True, "paused": bool(new_state)})


# ============================================
# ONBOARDING
# ============================================

@api_bp.route("/onboarding", methods=["GET"])
@login_required
def onboarding_page():
    """Render the onboarding wizard for new users."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    # Check if user already completed onboarding
    if user.get("onboarding_complete"):
        return jsonify({
            "success": False,
            "error": "Vous avez deja complete l'onboarding"
        }), 403

    # Get user's trial and referral info
    trial_status = {
        "is_trial_active": bool(user.get("is_trial_active", 0)),
        "trial_ends_at": user.get("trial_ends_at"),
        "plan": user.get("plan", "starter")
    }

    referral_info = {
        "referral_code": user.get("referral_code"),
        "referred_by": user.get("referred_by")
    }

    return render_template(
        "onboarding.html",
        user=user,
        trial_status=trial_status,
        referral_info=referral_info
    )


@api_bp.route("/api/complete-onboarding", methods=["POST"])
@login_required
def complete_onboarding():
    """Mark onboarding as complete for the current user."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    # Check if already completed
    if user.get("onboarding_complete"):
        return jsonify({
            "success": False,
            "error": "Onboarding deja complete"
        }), 403

    # Mark as complete
    try:
        db.update_user(user_id, onboarding_complete=1)
        logger.info("Onboarding completed for user id=%d", user_id)
        return jsonify({
            "success": True,
            "message": "Onboarding complete"
        }), 200
    except Exception as exc:
        logger.error("Failed to complete onboarding for user id=%d: %s", user_id, exc)
        return jsonify({
            "success": False,
            "error": "Erreur lors de la finalisation de l'onboarding"
        }), 500
