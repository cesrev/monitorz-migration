"""
Billets Monitor MVP - Ticket Email Parsers
Extracted and adapted from monitor_oauth.py.
Handles Ticketmaster, Roland-Garros, and Stade de France confirmation emails.
"""

import re
import logging
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Gmail API search queries for ticket confirmation emails
TICKET_QUERIES: list[tuple[str, str]] = [
    ("from:ticketmaster subject:confirmation", "ticketmaster"),
    ("from:fft.fr subject:confirmation", "roland-garros"),
    ("from:rolandgarros subject:confirmation", "roland-garros"),
    ("from:stadefrance subject:confirmation", "stade-de-france"),
]


def _format_category(raw: str) -> str:
    """Normalize a category string."""
    cat = raw.strip().lower()
    cat = cat.replace(" nord", " N").replace(" sud", " S")
    cat = cat.replace(" est", " E").replace(" ouest", " O")
    cat = cat.replace("categorie", "cat").replace("catégorie", "cat")
    return cat


# ============================================
# TICKETMASTER
# ============================================

def parse_ticketmaster_email(subject: str, html: str) -> Optional[dict]:
    """Parse a Ticketmaster confirmation email.

    Returns a dict with keys: order_id, event, category, venue, event_date,
    price, order_link -- or None if not a valid confirmation.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Must contain "commande est confirmée"
    if "commande est confirmée" not in text.lower():
        return None

    # --- Order ID ---
    order_id: Optional[str] = None
    if subject:
        m = re.search(r"(\d{9,})", subject)
        if m:
            order_id = m.group(1)
    if not order_id:
        m = re.search(r"référence n°(\d+)", text)
        if m:
            order_id = m.group(1)
    if not order_id:
        return None

    # --- Event name ---
    event = "Evenement"
    for i, line in enumerate(lines):
        if "détail de votre commande" in line.lower() and i + 1 < len(lines):
            event = lines[i + 1]
            break

    # --- Date ---
    event_date = ""
    months_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    for line in lines:
        m = re.match(r"^(\d{1,2})\s+(\w+)\s+(\d{4})", line)
        if m and m.group(2) in months_map:
            event_date = f"{m.group(1).zfill(2)}/{months_map[m.group(2)]}/{m.group(3)}"
            break

    # --- Venue ---
    venue = ""
    venues_map = {
        "STADE DE FRANCE": "Stade de France",
        "VELODROME": "Velodrome",
        "ACCOR": "Accor Arena",
        "BERCY": "Bercy",
        "ZENITH": "Zenith",
        "OLYMPIA": "Olympia",
        "PARC DES PRINCES": "Parc des Princes",
    }
    for line in lines:
        for key, value in venues_map.items():
            if key in line.upper():
                venue = value
                break
        if venue:
            break

    # --- Price ---
    price = "0"
    m = re.search(r"Total de la commande\s*(\d+)", text, re.IGNORECASE)
    if m:
        price = m.group(1)

    # --- Category ---
    category = ""
    category_patterns = [
        r"(Cat[ée]gorie\s*\d+)",
        r"(CAT\s*\d+)",
        r"(Carr[ée]\s*Or\s*\w*)",
        r"(Pelouse\s*\w*)",
        r"(Fosse\s*\w*)",
        r"(VIP\s*\w*)",
        r"(Tribune\s*\w+)",
        r"(Premium\s*\w*)",
        r"(Gold\s*\w*)",
        r"(Silver\s*\w*)",
        r"(Placement\s*Libre)",
    ]
    for pattern in category_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = _format_category(m.group(1))
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "ticketmaster" in href.lower() and any(
            x in href.lower() for x in ["order", "commande", "moncompte", "member"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://my.ticketmaster.fr/orders/{order_id}"

    return {
        "order_id": order_id,
        "event": event,
        "category": category,
        "venue": venue,
        "event_date": event_date,
        "price": price,
        "order_link": order_link,
    }


# ============================================
# ROLAND-GARROS
# ============================================

def parse_roland_garros_email(subject: str, html: str) -> Optional[dict]:
    """Parse a Roland-Garros confirmation email."""
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if "confirmation" not in text.lower():
        return None

    # --- Order ID ---
    order_id: Optional[str] = None
    m = re.search(r"Numéro de commande\s*:?\s*(\d+)", text)
    if m:
        order_id = m.group(1)
    if not order_id:
        return None

    # --- Date ---
    event_date = ""
    for line in lines:
        m = re.match(r"^(\d{2}/\d{2}/\d{4})$", line)
        if m:
            event_date = m.group(1)
            break

    # --- Venue ---
    venue = "Roland-Garros"
    for line in lines:
        if "Philippe-Chatrier" in line:
            venue = "Philippe-Chatrier"
            break
        elif "Suzanne-Lenglen" in line:
            venue = "Suzanne-Lenglen"
            break

    # --- Price ---
    price = "0"
    total = sum(int(m.group(1)) for m in re.finditer(r"(\d+)[,\.]\d{2}\s*€", text))
    if total > 0:
        price = str(total)

    # --- Category ---
    category = ""
    for pattern in [
        r"(Cat[ée]gorie\s*\d+)",
        r"(Tribune\s*\w+)",
        r"(Loge\s*\w*)",
        r"(VIP\s*\w*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = _format_category(m.group(1))
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "rolandgarros" in href.lower() and any(
            x in href.lower() for x in ["order", "commande", "account", "billet"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://billetterie.rolandgarros.com/fr/account/orders/{order_id}"

    return {
        "order_id": order_id,
        "event": "Roland-Garros",
        "category": category,
        "venue": venue,
        "event_date": event_date,
        "price": price,
        "order_link": order_link,
    }


# ============================================
# STADE DE FRANCE
# ============================================

def parse_stade_de_france_email(subject: str, html: str) -> Optional[dict]:
    """Parse a Stade de France confirmation email."""
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # --- Order ID ---
    order_id: Optional[str] = None
    m = re.search(r"commande\s*[n°#:]*\s*(\d+)", text, re.IGNORECASE)
    if m:
        order_id = m.group(1)
    if not order_id:
        return None

    # --- Event name ---
    event = "Evenement SDF"
    for line in lines:
        if len(line) > 5 and line[0].isupper() and "commande" not in line.lower():
            event = line[:50]
            break

    # --- Date ---
    event_date = ""
    m = re.search(r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})", text)
    if m:
        event_date = m.group(1)

    # --- Price ---
    price = "0"
    m = re.search(r"total\s*:?\s*(\d+)", text, re.IGNORECASE)
    if m:
        price = m.group(1)

    # --- Category ---
    category = ""
    for pattern in [
        r"(Cat[ée]gorie\s*\d+)",
        r"(Pelouse\s*\w*)",
        r"(Fosse\s*\w*)",
        r"(VIP\s*\w*)",
        r"(Tribune\s*\w+)",
        r"(Carr[ée]\s*Or\s*\w*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = _format_category(m.group(1))
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "stadefrance" in href.lower() and any(
            x in href.lower() for x in ["order", "commande", "account", "billet"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://billetterie.stadefrance.com/account/orders/{order_id}"

    return {
        "order_id": order_id,
        "event": event,
        "category": category,
        "venue": "Stade de France",
        "event_date": event_date,
        "price": price,
        "order_link": order_link,
    }
