"""
Export routes: CSV export and PDF report generation.
"""

import logging
import csv
import io
from datetime import datetime
from flask import Blueprint, session, request, jsonify, send_file
from googleapiclient.discovery import build
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import database as db
from helpers import login_required, _get_sheet_data_cached, _parse_price, get_google_credentials, _parse_month_year

logger = logging.getLogger(__name__)

export_bp = Blueprint("export", __name__)


@export_bp.route("/api/export/csv")
@login_required
def export_csv():
    """Export user's Google Sheet data as CSV file."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 403

    mtype = user.get("monitoring_type", "tickets")
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:J")

        if len(rows) < 1:
            return jsonify({"success": False, "error": "Aucune donnee a exporter"}), 400

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write all rows from the sheet
        for row in rows:
            writer.writerow(row)

        output.seek(0)

        # Prepare response
        now = datetime.now().strftime("%Y%m%d")
        filename = f"monitorz_export_{now}.csv"

        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name=filename
        )

    except Exception as exc:
        logger.error("Failed to export CSV for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": "Erreur lors de l'export CSV"}), 500


@export_bp.route("/api/export/pdf-report")
@login_required
def export_pdf_report():
    """Generate and export a monthly P&L report as PDF."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({"success": False, "error": "Utilisateur introuvable"}), 403

    mtype = user.get("monitoring_type", "tickets")
    sheets = db.get_spreadsheets(user_id, monitoring_type=mtype)
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        from collections import defaultdict

        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:J")

        if len(rows) < 2:
            return jsonify({"success": False, "error": "Aucune donnee a exporter"}), 400

        # Aggregate monthly data
        monthly_data = defaultdict(lambda: {"purchases": 0.0, "sales": 0.0, "profit": 0.0})
        total_purchases = 0.0
        total_sales = 0.0

        if mtype == "tickets":
            # Tickets: A=Événement B=Catégorie C=Lieu D=Date E=Prix Achat
            #          F=N° Commande G=Lien H=Compte I=Prix Vente
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue
                date_str = row[3].strip() if len(row) > 3 else ""
                purchase_price_str = row[4].strip() if len(row) > 4 else ""
                sale_price_str = row[8].strip() if len(row) > 8 else ""

                month, year = _parse_month_year(date_str)
                if month and year:
                    purchase_val = _parse_price(purchase_price_str)
                    sale_val = _parse_price(sale_price_str)

                    month_key = f"{year}-{month:02d}"
                    monthly_data[month_key]["purchases"] += purchase_val
                    monthly_data[month_key]["sales"] += sale_val
                    monthly_data[month_key]["profit"] += sale_val - purchase_val
                    total_purchases += purchase_val
                    total_sales += sale_val
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
                    if p_month and p_year:
                        purchase_val = _parse_price(purchase_price_str)
                        month_key = f"{p_year}-{p_month:02d}"
                        monthly_data[month_key]["purchases"] += purchase_val
                        total_purchases += purchase_val

                if sale_price_str and sale_date_str:
                    s_month, s_year = _parse_month_year(sale_date_str)
                    if s_month and s_year:
                        sale_val = _parse_price(sale_price_str)
                        purchase_val = _parse_price(purchase_price_str) if purchase_price_str else 0.0
                        month_key = f"{s_year}-{s_month:02d}"
                        monthly_data[month_key]["sales"] += sale_val
                        monthly_data[month_key]["profit"] += sale_val - purchase_val
                        total_sales += sale_val

        total_profit = total_sales - total_purchases

        # Create PDF
        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            pdf_buffer,
            pagesize=letter,
            rightMargin=0.5 * inch,
            leftMargin=0.5 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        # Styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=24,
            textColor=colors.HexColor("#1a1a1a"),
            spaceAfter=12,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold"
        )

        heading_style = ParagraphStyle(
            "CustomHeading",
            parent=styles["Heading2"],
            fontSize=14,
            textColor=colors.HexColor("#333333"),
            spaceAfter=10,
            spaceBefore=12,
            fontName="Helvetica-Bold"
        )

        # Story for PDF content
        story = []

        # Title
        story.append(Paragraph("Monitorz Financial Report", title_style))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
        story.append(Spacer(1, 0.3 * inch))

        # Summary Stats
        story.append(Paragraph("Summary Statistics", heading_style))
        summary_data = [
            ["Metric", "Amount"],
            ["Total Purchases", f"{total_purchases:.2f}€"],
            ["Total Sales", f"{total_sales:.2f}€"],
            ["Total Profit", f"{total_profit:.2f}€"],
            ["ROI", f"{(total_profit / total_purchases * 100) if total_purchases > 0 else 0:.2f}%"]
        ]

        summary_table = Table(summary_data, colWidths=[3 * inch, 2 * inch])
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4CAF50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")])
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 0.3 * inch))

        # Monthly Breakdown
        story.append(Paragraph("Monthly Breakdown", heading_style))

        monthly_breakdown = [["Month", "Purchases", "Sales", "Profit"]]
        for month_key in sorted(monthly_data.keys()):
            data = monthly_data[month_key]
            monthly_breakdown.append([
                month_key,
                f"{data['purchases']:.2f}€",
                f"{data['sales']:.2f}€",
                f"{data['profit']:.2f}€"
            ])

        if len(monthly_breakdown) > 1:
            breakdown_table = Table(monthly_breakdown, colWidths=[1.5 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch])
            breakdown_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2196F3")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 11),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")])
            ]))
            story.append(breakdown_table)

        # Build PDF
        doc.build(story)
        pdf_buffer.seek(0)

        # Prepare response
        now = datetime.now().strftime("%Y%m%d")
        filename = f"monitorz_report_{now}.pdf"

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    except Exception as exc:
        logger.error("Failed to generate PDF report for user id=%d: %s", user_id, exc)
        return jsonify({"success": False, "error": "Erreur lors de la generation du rapport PDF"}), 500
