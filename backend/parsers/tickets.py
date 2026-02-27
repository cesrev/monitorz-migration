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
    # France
    ("from:ticketmaster subject:confirmation", "ticketmaster"),
    ("from:fft.fr subject:confirmation", "roland-garros"),
    ("from:rolandgarros subject:confirmation", "roland-garros"),
    ("from:stadefrance subject:confirmation", "stade-de-france"),
    # International
    ("from:ticketmaster.com subject:order confirmation", "ticketmaster-us"),
    ("from:ticketmaster.co.uk subject:order confirmation", "ticketmaster-uk"),
    # Salles / Plateformes
    ("from:accorarena subject:confirmation", "accor-arena"),
    ("from:accor-arena subject:confirmation", "accor-arena"),
    ("from:axs.com subject:order", "axs"),
    ("from:axs subject:confirmation", "axs"),
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


# ============================================
# TICKETMASTER US
# ============================================

def parse_ticketmaster_us_email(subject: str, html: str) -> Optional[dict]:
    """Parse a Ticketmaster US/International confirmation email."""
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Must contain order confirmation keywords
    if not any(kw in text.lower() for kw in ["order confirmed", "order confirmation", "your order"]):
        return None

    # --- Order ID ---
    order_id: Optional[str] = None
    if subject:
        m = re.search(r"(\d{9,})", subject)
        if m:
            order_id = m.group(1)
    if not order_id:
        m = re.search(r"order\s*(?:number|#|no\.?)\s*:?\s*(\d+)", text, re.IGNORECASE)
        if m:
            order_id = m.group(1)
    if not order_id:
        m = re.search(r"confirmation\s*(?:number|#|no\.?)\s*:?\s*([A-Z0-9-]+)", text, re.IGNORECASE)
        if m:
            order_id = m.group(1)
    if not order_id:
        return None

    # --- Event name ---
    event = "Event"
    for i, line in enumerate(lines):
        if any(kw in line.lower() for kw in ["order detail", "event detail", "your tickets"]) and i + 1 < len(lines):
            event = lines[i + 1]
            break
    if event == "Event":
        # Fallback: look for bold text or h2/h3
        for tag in soup.find_all(["h2", "h3", "strong", "b"]):
            txt = tag.get_text(strip=True)
            if len(txt) > 5 and "order" not in txt.lower() and "ticket" not in txt.lower():
                event = txt[:80]
                break

    # --- Date (US format: Mon DD, YYYY or MM/DD/YYYY) ---
    event_date = ""
    us_months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    for line in lines:
        # Month DD, YYYY
        m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", line)
        if m and m.group(1).lower() in us_months:
            month = us_months[m.group(1).lower()]
            event_date = f"{m.group(2).zfill(2)}/{month}/{m.group(3)}"
            break
        # MM/DD/YYYY
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", line)
        if m:
            event_date = f"{m.group(2).zfill(2)}/{m.group(1).zfill(2)}/{m.group(3)}"
            break

    # --- Venue ---
    venue = ""
    for line in lines:
        if any(kw in line.lower() for kw in ["arena", "stadium", "center", "centre", "garden", "theater", "theatre", "amphitheatre", "hall", "field", "park"]):
            venue = line.strip()[:60]
            break

    # --- Price ---
    price = "0"
    m = re.search(r"(?:total|amount)\s*:?\s*\$?([\d,.]+)", text, re.IGNORECASE)
    if m:
        price = m.group(1).replace(",", "")

    # --- Category ---
    category = ""
    for pattern in [
        r"(Section\s*\w+)",
        r"(Row\s*\w+)",
        r"(Floor\s*\w*)",
        r"(General\s*Admission)",
        r"(GA\b)",
        r"(VIP\s*\w*)",
        r"(Pit\b)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = m.group(1).strip()
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "ticketmaster" in href.lower() and any(
            x in href.lower() for x in ["order", "account", "myevent"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://www.ticketmaster.com/member/order/{order_id}"

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
# TICKETMASTER UK
# ============================================

def parse_ticketmaster_uk_email(subject: str, html: str) -> Optional[dict]:
    """Parse a Ticketmaster UK confirmation email.
    
    Very similar to US format but with GBP currency and DD/MM/YYYY dates.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if not any(kw in text.lower() for kw in ["order confirmed", "order confirmation", "booking confirmation"]):
        return None

    # --- Order ID ---
    order_id: Optional[str] = None
    if subject:
        m = re.search(r"(\d{9,})", subject)
        if m:
            order_id = m.group(1)
    if not order_id:
        m = re.search(r"(?:order|booking|reference)\s*(?:number|#|no\.?)\s*:?\s*([A-Z0-9-]+)", text, re.IGNORECASE)
        if m:
            order_id = m.group(1)
    if not order_id:
        return None

    # --- Event name ---
    event = "Event"
    for tag in soup.find_all(["h2", "h3", "strong", "b"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 5 and not any(kw in txt.lower() for kw in ["order", "ticket", "confirmation", "booking"]):
            event = txt[:80]
            break

    # --- Date (UK format: DD/MM/YYYY or DD Month YYYY) ---
    event_date = ""
    uk_months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    for line in lines:
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", line)
        if m and m.group(2).lower() in uk_months:
            event_date = f"{m.group(1).zfill(2)}/{uk_months[m.group(2).lower()]}/{m.group(3)}"
            break
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", line)
        if m:
            event_date = f"{m.group(1).zfill(2)}/{m.group(2).zfill(2)}/{m.group(3)}"
            break

    # --- Venue ---
    venue = ""
    uk_venues = {
        "O2 ARENA": "The O2 Arena",
        "WEMBLEY": "Wembley",
        "MANCHESTER ARENA": "Manchester Arena",
        "AO ARENA": "AO Arena",
        "CARDIFF": "Cardiff",
        "SSE": "SSE Arena",
        "HYDRO": "OVO Hydro",
        "ARENA BIRMINGHAM": "Arena Birmingham",
    }
    for line in lines:
        for key, value in uk_venues.items():
            if key in line.upper():
                venue = value
                break
        if venue:
            break
    if not venue:
        for line in lines:
            if any(kw in line.lower() for kw in ["arena", "stadium", "theatre", "hall"]):
                venue = line.strip()[:60]
                break

    # --- Price (GBP) ---
    price = "0"
    m = re.search(r"(?:total|amount)\s*:?\s*\xa3?([\d,.]+)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\xa3([\d,.]+)", text)
    if m:
        price = m.group(1).replace(",", "")

    # --- Category ---
    category = ""
    for pattern in [
        r"(Block\s*\w+)",
        r"(Section\s*\w+)",
        r"(Row\s*\w+)",
        r"(Standing\s*\w*)",
        r"(General\s*Admission)",
        r"(VIP\s*\w*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = m.group(1).strip()
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "ticketmaster.co.uk" in href.lower() and any(
            x in href.lower() for x in ["order", "account", "myevent"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://www.ticketmaster.co.uk/member/order/{order_id}"

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
# ACCOR ARENA
# ============================================

def parse_accor_arena_email(subject: str, html: str) -> Optional[dict]:
    """Parse an Accor Arena confirmation email."""
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if not any(kw in text.lower() for kw in ["confirmation", "commande", "billet", "reservation"]):
        return None

    # --- Order ID ---
    order_id: Optional[str] = None
    m = re.search(r"(?:commande|reservation|reference)\s*(?:n[o°]|#)?\s*:?\s*([A-Z0-9-]+)", text, re.IGNORECASE)
    if m:
        order_id = m.group(1)
    if not order_id:
        if subject:
            m = re.search(r"(\d{6,})", subject)
            if m:
                order_id = m.group(1)
    if not order_id:
        return None

    # --- Event name ---
    event = "Evenement Accor Arena"
    for tag in soup.find_all(["h2", "h3", "strong", "b"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 5 and not any(kw in txt.lower() for kw in ["commande", "confirmation", "billet", "accor"]):
            event = txt[:80]
            break
    if event == "Evenement Accor Arena":
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in ["detail", "evenement", "spectacle"]) and i + 1 < len(lines):
                event = lines[i + 1][:80]
                break

    # --- Date ---
    event_date = ""
    fr_months = {
        "janvier": "01", "fevrier": "02", "mars": "03", "avril": "04",
        "mai": "05", "juin": "06", "juillet": "07", "aout": "08",
        "septembre": "09", "octobre": "10", "novembre": "11", "decembre": "12",
    }
    for line in lines:
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", line)
        if m and m.group(2).lower() in fr_months:
            event_date = f"{m.group(1).zfill(2)}/{fr_months[m.group(2).lower()]}/{m.group(3)}"
            break
        m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$", line)
        if m:
            event_date = f"{m.group(1).zfill(2)}/{m.group(2).zfill(2)}/{m.group(3)}"
            break

    # --- Venue ---
    venue = "Accor Arena"

    # --- Price ---
    price = "0"
    m = re.search(r"(?:total|montant)\s*:?\s*(\d+)", text, re.IGNORECASE)
    if m:
        price = m.group(1)
    else:
        total = sum(int(m.group(1)) for m in re.finditer(r"(\d+)[,.]\d{2}\s*(?:EUR|\u20ac)", text))
        if total > 0:
            price = str(total)

    # --- Category ---
    category = ""
    for pattern in [
        r"(Cat[ee]gorie\s*\d+)",
        r"(Carre\s*Or\s*\w*)",
        r"(Fosse\s*\w*)",
        r"(Tribune\s*\w+)",
        r"(VIP\s*\w*)",
        r"(Premium\s*\w*)",
        r"(Balcon\s*\w*)",
        r"(Parterre\s*\w*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = _format_category(m.group(1))
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "accor" in href.lower() and any(
            x in href.lower() for x in ["order", "commande", "account", "billet", "reservation"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://www.accorarena.com/account/orders/{order_id}"

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
# AXS
# ============================================

def parse_axs_email(subject: str, html: str) -> Optional[dict]:
    """Parse an AXS ticket confirmation email.
    
    AXS is used for many venues worldwide including O2 Arena, AO Arena, etc.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if not any(kw in text.lower() for kw in ["order confirmed", "order confirmation", "your tickets", "confirmation"]):
        return None

    # --- Order ID ---
    order_id: Optional[str] = None
    m = re.search(r"(?:order|confirmation|reference)\s*(?:number|#|no\.?|id)?\s*:?\s*([A-Z0-9-]{5,})", text, re.IGNORECASE)
    if m:
        order_id = m.group(1)
    if not order_id:
        if subject:
            m = re.search(r"(\d{7,})", subject)
            if m:
                order_id = m.group(1)
    if not order_id:
        return None

    # --- Event name ---
    event = "Event AXS"
    for tag in soup.find_all(["h1", "h2", "h3", "strong"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 5 and not any(kw in txt.lower() for kw in ["order", "confirmation", "axs", "thank"]):
            event = txt[:80]
            break

    # --- Date (flexible: DD/MM/YYYY, MM/DD/YYYY, Month DD YYYY, DD Month YYYY) ---
    event_date = ""
    months_map = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        "janvier": "01", "fevrier": "02", "mars": "03", "avril": "04",
        "mai": "05", "juin": "06", "juillet": "07", "aout": "08",
        "septembre": "09", "octobre": "10", "novembre": "11", "decembre": "12",
    }
    for line in lines:
        # DD Month YYYY
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", line)
        if m and m.group(2).lower() in months_map:
            event_date = f"{m.group(1).zfill(2)}/{months_map[m.group(2).lower()]}/{m.group(3)}"
            break
        # Month DD, YYYY
        m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", line)
        if m and m.group(1).lower() in months_map:
            event_date = f"{m.group(2).zfill(2)}/{months_map[m.group(1).lower()]}/{m.group(3)}"
            break

    # --- Venue ---
    venue = ""
    for line in lines:
        if any(kw in line.lower() for kw in ["arena", "stadium", "theatre", "theater", "hall", "centre", "center"]):
            venue = line.strip()[:60]
            break

    # --- Price ---
    price = "0"
    m = re.search(r"(?:total|amount|prix)\s*:?\s*[\$\xa3\u20ac]?([\d,.]+)", text, re.IGNORECASE)
    if m:
        price = m.group(1).replace(",", "")

    # --- Category ---
    category = ""
    for pattern in [
        r"(Section\s*\w+)",
        r"(Block\s*\w+)",
        r"(Row\s*\w+)",
        r"(Standing\s*\w*)",
        r"(General\s*Admission)",
        r"(GA\b)",
        r"(VIP\s*\w*)",
        r"(Floor\s*\w*)",
        r"(Cat[ee]gorie\s*\d+)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            category = m.group(1).strip()
            break

    # --- Order link ---
    order_link = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "axs" in href.lower() and any(
            x in href.lower() for x in ["order", "account", "ticket", "event"]
        ):
            order_link = href
            break
    if not order_link:
        order_link = f"https://www.axs.com/orders/{order_id}"

    return {
        "order_id": order_id,
        "event": event,
        "category": category,
        "venue": venue,
        "event_date": event_date,
        "price": price,
        "order_link": order_link,
    }
