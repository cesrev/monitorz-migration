"""
Invoice and services routes: company profile, services management, PDF generation.
"""

import logging
import io
from datetime import datetime
from flask import Blueprint, session, request, jsonify, send_file
import database as db
from helpers import login_required

logger = logging.getLogger(__name__)

invoice_bp = Blueprint("invoice", __name__)


@invoice_bp.route("/api/user/company-profile")
@login_required
def get_company_profile():
    """Return company profile fields for the current user."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    company_fields = {
        "company_name": user.get("company_name", ""),
        "company_address": user.get("company_address", ""),
        "company_phone": user.get("company_phone", ""),
        "company_email": user.get("company_email", ""),
        "company_siret": user.get("company_siret", ""),
        "company_tva_number": user.get("company_tva_number", ""),
        "company_iban": user.get("company_iban", ""),
        "company_bic": user.get("company_bic", ""),
        "company_tva_rate": user.get("company_tva_rate", 20.0),
        "invoice_prefix": user.get("invoice_prefix", "INV"),
        "invoice_footer": user.get("invoice_footer", ""),
    }
    return jsonify({"success": True, **company_fields})


@invoice_bp.route("/api/user/company-profile", methods=["PATCH"])
@login_required
def update_company_profile():
    """Update company profile fields (partial update)."""
    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}

    allowed = {
        "company_name", "company_address", "company_phone", "company_email",
        "company_siret", "company_tva_number", "company_iban", "company_bic",
        "company_tva_rate", "invoice_prefix", "invoice_footer",
    }
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"success": False, "error": "Aucun champ valide"}), 400

    # Convert tva_rate to float
    if "company_tva_rate" in fields:
        try:
            fields["company_tva_rate"] = float(fields["company_tva_rate"])
        except (ValueError, TypeError):
            fields["company_tva_rate"] = 20.0

    db.update_user(user_id, **fields)
    return jsonify({"success": True})


@invoice_bp.route("/api/services")
@login_required
def get_services_route():
    """Get all services for the current user."""
    user_email = session["user_email"]
    services = db.get_services(user_email)
    return jsonify({"success": True, "services": services})


@invoice_bp.route("/api/services", methods=["POST"])
@login_required
def create_service_route():
    """Create a new service."""
    user_email = session["user_email"]
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Le nom du service est requis"}), 400

    try:
        unit_price_ht = float(data.get("unit_price_ht", 0))
        tva_rate = float(data.get("tva_rate", 20))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Prix ou taux TVA invalide"}), 400
    description = (data.get("description") or "").strip()

    service = db.create_service(user_email, name, unit_price_ht, tva_rate, description)
    return jsonify({"success": True, "service": service})


@invoice_bp.route("/api/services/<service_id>", methods=["PATCH"])
@login_required
def update_service_route(service_id):
    """Update a service."""
    user_email = session["user_email"]
    data = request.get_json(silent=True) or {}

    try:
        unit_price_ht = float(data.get("unit_price_ht", 0)) if "unit_price_ht" in data else None
        tva_rate = float(data.get("tva_rate", 20)) if "tva_rate" in data else None
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Prix ou taux TVA invalide"}), 400

    name = (data.get("name") or "").strip() if "name" in data else None
    description = (data.get("description") or "").strip() if "description" in data else None

    ok = db.update_service(service_id, user_email, name=name, unit_price_ht=unit_price_ht,
                           tva_rate=tva_rate, description=description)
    if not ok:
        return jsonify({"success": False, "error": "Service introuvable"}), 404
    return jsonify({"success": True})


@invoice_bp.route("/api/services/<service_id>", methods=["DELETE"])
@login_required
def delete_service_route(service_id):
    """Delete a service."""
    user_email = session["user_email"]
    ok = db.delete_service(service_id, user_email)
    if not ok:
        return jsonify({"success": False, "error": "Service introuvable"}), 404
    return jsonify({"success": True})


def _generate_invoice_pdf(invoice_data: dict, user: dict) -> bytes:
    """Generate a PDF invoice in memory using reportlab. Returns PDF bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4  # 595.27, 841.89

    # Colors
    dark = HexColor("#1a1a22")
    gray = HexColor("#6e6a68")
    light_gray = HexColor("#a8a3a0")
    accent = HexColor("#f0804e")
    green = HexColor("#34d399")
    red = HexColor("#f87171")
    white = HexColor("#ffffff")
    bg_light = HexColor("#f8f7f5")

    margin = 40
    col_w = (w - 2 * margin)

    y = h - margin

    # --- Header: mntrz branding ---
    c.setFont("Helvetica", 7)
    c.setFillColor(light_gray)
    c.drawString(margin, y, "mntrz")
    c.setStrokeColor(HexColor("#e0ddd8"))
    c.setLineWidth(0.5)
    y -= 8
    c.line(margin, y, w - margin, y)
    y -= 30

    # --- FACTURE title + number ---
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(dark)
    c.drawString(margin, y, "FACTURE")

    invoice_number = invoice_data.get("invoice_number", "INV-001")
    c.setFont("Helvetica", 10)
    c.setFillColor(gray)
    c.drawString(margin + 130, y + 4, invoice_number)
    y -= 25

    # --- Dates (right-aligned) ---
    emission_date = invoice_data.get("emission_date", datetime.utcnow().strftime("%d/%m/%Y"))
    due_date = invoice_data.get("due_date", "")

    c.setFont("Helvetica", 9)
    c.setFillColor(gray)
    c.drawRightString(w - margin, y + 40, f"Date d'emission : {emission_date}")
    if due_date:
        c.drawRightString(w - margin, y + 26, f"Date d'echeance : {due_date}")
    y -= 10

    # --- Emitter / Client blocks side by side ---
    block_w = (col_w - 30) / 2

    # Emitter (left)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(dark)
    c.drawString(margin, y, "EMETTEUR")
    y -= 14

    c.setFont("Helvetica", 9)
    c.setFillColor(gray)
    emitter_lines = []
    if user.get("company_name"):
        emitter_lines.append(user["company_name"])
    if user.get("company_address"):
        for addr_line in user["company_address"].split("\n"):
            emitter_lines.append(addr_line.strip())
    if user.get("company_phone"):
        emitter_lines.append(user["company_phone"])
    if user.get("company_email"):
        emitter_lines.append(user["company_email"])
    if user.get("company_siret"):
        emitter_lines.append(f"SIRET : {user['company_siret']}")
    if user.get("company_tva_number"):
        emitter_lines.append(f"TVA : {user['company_tva_number']}")

    ey = y
    for line in emitter_lines:
        c.drawString(margin, ey, line)
        ey -= 13

    # Client (right)
    client_x = margin + block_w + 30
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(dark)
    c.drawString(client_x, y + 14, "CLIENT")

    c.setFont("Helvetica", 9)
    c.setFillColor(gray)
    client_lines = []
    if invoice_data.get("client_name"):
        client_lines.append(invoice_data["client_name"])
    if invoice_data.get("client_email"):
        client_lines.append(invoice_data["client_email"])
    if invoice_data.get("client_address"):
        for addr_line in invoice_data["client_address"].split("\n"):
            client_lines.append(addr_line.strip())

    cy = y
    for line in client_lines:
        c.drawString(client_x, cy, line)
        cy -= 13

    y = min(ey, cy) - 20

    # --- Event reference (if provided) ---
    event_name = invoice_data.get("event_name", "")
    if event_name:
        c.setStrokeColor(HexColor("#e0ddd8"))
        c.setFillColor(bg_light)
        c.roundRect(margin, y - 25, col_w, 30, 4, fill=1, stroke=1)
        c.setFont("Helvetica", 9)
        c.setFillColor(dark)
        c.drawString(margin + 10, y - 16, f"Evenement : {event_name}")
        y -= 40

    # --- Table header ---
    table_y = y
    col_desc_w = col_w * 0.38
    col_qty_w = col_w * 0.10
    col_unit_w = col_w * 0.18
    col_ht_w = col_w * 0.17
    col_tva_w = col_w * 0.17

    # Header background
    c.setFillColor(HexColor("#f0ece6"))
    c.rect(margin, table_y - 4, col_w, 18, fill=1, stroke=0)

    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(dark)
    cx = margin + 4
    c.drawString(cx, table_y, "Description")
    cx += col_desc_w
    c.drawString(cx, table_y, "Qte")
    cx += col_qty_w
    c.drawString(cx, table_y, "Prix unit. HT")
    cx += col_unit_w
    c.drawString(cx, table_y, "Montant HT")
    cx += col_ht_w
    c.drawString(cx, table_y, "TVA")

    table_y -= 20

    # --- Table rows ---
    lines = invoice_data.get("lines", [])
    c.setFont("Helvetica", 9)

    total_ht = 0.0
    total_tva = 0.0

    for i, line in enumerate(lines):
        desc = line.get("description", "")
        qty = float(line.get("quantity", 1))
        unit_ht = float(line.get("unit_price_ht", 0))
        tva_rate = float(line.get("tva_rate", 0))
        montant_ht = qty * unit_ht
        montant_tva = montant_ht * tva_rate / 100

        total_ht += montant_ht
        total_tva += montant_tva

        # Alternate row background
        if i % 2 == 0:
            c.setFillColor(HexColor("#fafaf8"))
            c.rect(margin, table_y - 4, col_w, 16, fill=1, stroke=0)

        c.setFillColor(dark)
        cx = margin + 4
        # Truncate long descriptions
        display_desc = desc[:50] + "..." if len(desc) > 50 else desc
        c.drawString(cx, table_y, display_desc)
        cx += col_desc_w
        c.drawString(cx, table_y, str(int(qty) if qty == int(qty) else qty))
        cx += col_qty_w
        c.drawString(cx, table_y, f"{unit_ht:.2f} EUR")
        cx += col_unit_w
        c.drawString(cx, table_y, f"{montant_ht:.2f} EUR")
        cx += col_ht_w
        c.drawString(cx, table_y, f"{tva_rate:.0f}%")

        table_y -= 18

    # --- Table bottom line ---
    c.setStrokeColor(HexColor("#e0ddd8"))
    c.setLineWidth(0.5)
    c.line(margin, table_y + 4, w - margin, table_y + 4)
    table_y -= 20

    # --- Totals ---
    total_ttc = total_ht + total_tva
    totals_x = w - margin - 180

    c.setFont("Helvetica", 9)
    c.setFillColor(gray)
    c.drawString(totals_x, table_y, "Total HT")
    c.setFillColor(dark)
    c.drawRightString(w - margin, table_y, f"{total_ht:.2f} EUR")
    table_y -= 16

    # TVA line
    c.setFillColor(gray)
    if total_tva > 0:
        c.drawString(totals_x, table_y, "TVA")
        c.setFillColor(dark)
        c.drawRightString(w - margin, table_y, f"{total_tva:.2f} EUR")
    else:
        c.drawString(totals_x, table_y, "TVA non applicable - art. 293B du CGI")
    table_y -= 20

    # TTC
    c.setFillColor(accent)
    c.rect(totals_x - 10, table_y - 6, w - margin - totals_x + 20, 24, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(white)
    c.drawString(totals_x, table_y, "Total TTC")
    c.drawRightString(w - margin, table_y, f"{total_ttc:.2f} EUR")
    table_y -= 30

    # --- Payment status ---
    status = invoice_data.get("status", "en_attente")
    status_labels = {
        "payee": ("Payee", green),
        "en_attente": ("En attente", HexColor("#fbbf24")),
        "en_retard": ("En retard", red),
    }
    label, color = status_labels.get(status, ("En attente", HexColor("#fbbf24")))

    c.setFillColor(color)
    c.circle(margin + 5, table_y + 3, 4, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin + 14, table_y, f"Statut : {label}")
    table_y -= 25

    # --- Notes ---
    notes = invoice_data.get("notes", "")
    if notes:
        c.setFont("Helvetica", 8)
        c.setFillColor(gray)
        c.drawString(margin, table_y, "Notes :")
        table_y -= 13
        for note_line in notes.split("\n")[:5]:
            c.drawString(margin, table_y, note_line.strip()[:90])
            table_y -= 12
        table_y -= 5

    # --- Banking info ---
    if user.get("company_iban"):
        c.setFont("Helvetica", 8)
        c.setFillColor(gray)
        c.drawString(margin, table_y, "Informations bancaires :")
        table_y -= 13
        c.drawString(margin, table_y, f"IBAN : {user['company_iban']}")
        table_y -= 12
        if user.get("company_bic"):
            c.drawString(margin, table_y, f"BIC : {user['company_bic']}")
            table_y -= 12

    # --- Footer ---
    c.setFont("Helvetica", 7)
    c.setFillColor(light_gray)
    c.drawCentredString(w / 2, 30, "Document genere via mntrz")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


@invoice_bp.route("/api/invoices/generate", methods=["POST"])
@login_required
def generate_invoice():
    """Generate a PDF invoice and return it as a downloadable file."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 404

    if not user.get("company_name"):
        return jsonify({"success": False, "error": "Configure ton profil entreprise dans Parametres"}), 400

    data = request.get_json(silent=True) or {}

    # Validate required fields
    client_name = (data.get("client_name") or "").strip()
    if not client_name:
        return jsonify({"success": False, "error": "Le nom du client est requis"}), 400

    lines = data.get("lines", [])
    if not lines:
        return jsonify({"success": False, "error": "Au moins une ligne est requise"}), 400

    # Increment counter and build invoice number
    counter = db.increment_invoice_counter(user_id)
    prefix = user.get("invoice_prefix", "INV") or "INV"
    invoice_number = f"{prefix}-{counter:04d}"

    # Build invoice data
    invoice_data = {
        "invoice_number": invoice_number,
        "emission_date": datetime.utcnow().strftime("%d/%m/%Y"),
        "due_date": data.get("due_date", ""),
        "client_name": client_name,
        "client_email": data.get("client_email", ""),
        "client_address": data.get("client_address", ""),
        "event_name": data.get("event_name", ""),
        "lines": lines,
        "status": data.get("status", "en_attente"),
        "notes": data.get("notes", ""),
    }

    try:
        pdf_bytes = _generate_invoice_pdf(invoice_data, user)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{invoice_number}.pdf",
        )
    except Exception as exc:
        logger.error("Failed to generate invoice PDF: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors de la generation du PDF"}), 500
