"""
Billets & Vinted Monitor MVP - Leboncoin Email Parser
Handles Leboncoin sale and purchase confirmation emails.

Supports:
- Sale emails (article vendu) → extract sale details
- Purchase emails (paiement reçu) → extract purchase details
"""

import re
import logging
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Gmail API search queries for Leboncoin
LEBONCOIN_SALE_QUERIES: list[tuple[str, str]] = [
    ("from:leboncoin subject:vendu", "leboncoin-sale"),
    ("from:leboncoin.fr subject:vendu", "leboncoin-sale"),
]

LEBONCOIN_PURCHASE_QUERIES: list[tuple[str, str]] = [
    ("from:leboncoin subject:paiement", "leboncoin-purchase"),
    ("from:leboncoin.fr subject:paiement", "leboncoin-purchase"),
    ("from:leboncoin subject:achat", "leboncoin-purchase"),
]

# All queries (combined)
LEBONCOIN_QUERIES = LEBONCOIN_SALE_QUERIES + LEBONCOIN_PURCHASE_QUERIES


def _extract_date_from_text(text: str) -> Optional[str]:
    """Extract a date from email text. Returns ISO format string (YYYY-MM-DD).

    Tries multiple date formats:
    - DD/MM/YYYY or DD-MM-YYYY
    - DD month YYYY (French months)
    """
    # Try DD/MM/YYYY or DD-MM-YYYY
    match = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", text)
    if match:
        day, month, year = match.group(1), match.group(2), match.group(3)
        try:
            dt = datetime(int(year), int(month), int(day))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Try "DD mois YYYY" (French months)
    months_fr = {
        "janvier": 1, "février": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
        "fevrier": 2, "aout": 8,
    }
    match = re.search(
        r"(\d{1,2})\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre)\s+(\d{4})",
        text, re.IGNORECASE
    )
    if match:
        day = int(match.group(1))
        month = months_fr.get(match.group(2).lower(), 0)
        year = int(match.group(3))
        if month:
            try:
                dt = datetime(year, month, day)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    return None


def parse_leboncoin_sale_email(html: str) -> Optional[dict]:
    """Parse a Leboncoin sale confirmation email (article vendu).

    Returns a dict with keys: title, price, date, buyer_info, type
    or None if not a valid sale email.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    text_lower = text.lower()

    # Check for sale-related keywords
    if not any(kw in text_lower for kw in ["vendu", "article a été vendu", "article est vendu"]):
        return None

    # --- Title (article name) ---
    title: Optional[str] = None
    # Try to find "Article:" or "Titre:" patterns
    patterns = [
        r"Article\s*:?\s*(.+?)(?:\n|$)",
        r"Titre\s*:?\s*(.+?)(?:\n|$)",
        r"Votre\s+(?:article|annonce)\s*:?\s*(.+?)(?:\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"\s+", " ", title)
            if len(title) > 200:  # Truncate very long titles
                title = title[:200]
            break

    # If not found via regex, try to extract from table
    if not title:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                cell_text = cells[0].get_text(strip=True).lower()
                if "article" in cell_text or "titre" in cell_text:
                    title = cells[1].get_text(strip=True)
                    break

    # --- Price ---
    price: Optional[str] = None
    # Try various patterns for price extraction
    price_patterns = [
        r"Montant\s*:?\s*(\d+)[,\.]\d{2}\s*€",
        r"Prix\s*:?\s*(\d+)[,\.]\d{2}\s*€",
        r"(\d+)[,\.]\d{2}\s*€",
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            price = match.group(1)
            break

    # Try to extract from table if not found
    if not price:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                cell_text = cells[0].get_text(strip=True).lower()
                if "montant" in cell_text or "prix" in cell_text:
                    price_text = cells[1].get_text(strip=True)
                    m = re.search(r"(\d+)", price_text)
                    if m:
                        price = m.group(1)
                    break

    # --- Date ---
    date = _extract_date_from_text(text)

    # --- Buyer info ---
    buyer_info: Optional[str] = None
    # Try to find buyer username/name
    buyer_patterns = [
        r"Acheteur\s*:?\s*(.+?)(?:\n|$)",
        r"Vendeur\s*:?\s*(.+?)(?:\n|$)",
        r"Utilisateur\s*:?\s*(.+?)(?:\n|$)",
    ]
    for pattern in buyer_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            buyer_info = match.group(1).strip()
            if len(buyer_info) > 100:
                buyer_info = buyer_info[:100]
            break

    # Try to extract from table if not found
    if not buyer_info:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                cell_text = cells[0].get_text(strip=True).lower()
                if any(kw in cell_text for kw in ["acheteur", "vendeur", "utilisateur"]):
                    buyer_info = cells[1].get_text(strip=True)
                    break

    if title and price:
        logger.info("Parsed Leboncoin sale: '%s' for %s EUR", title, price)
        return {
            "title": title,
            "price": price,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "buyer_info": buyer_info or "",
            "type": "sale",
        }

    return None


def parse_leboncoin_purchase_email(html: str) -> Optional[dict]:
    """Parse a Leboncoin purchase confirmation email (paiement reçu).

    Returns a dict with keys: title, price, date, seller_info, type
    or None if not a valid purchase email.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    text_lower = text.lower()

    # Check for purchase-related keywords
    if not any(kw in text_lower for kw in ["paiement", "achat", "commande", "purchase"]):
        return None

    # Avoid matching sale emails
    if any(kw in text_lower for kw in ["vendu", "article a été vendu"]):
        return None

    # --- Title (article name) ---
    title: Optional[str] = None
    # Try to find "Article:" or "Titre:" patterns
    patterns = [
        r"Article\s*:?\s*(.+?)(?:\n|$)",
        r"Titre\s*:?\s*(.+?)(?:\n|$)",
        r"(?:Vous avez acheté|Achat de)\s+(.+?)(?:\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"\s+", " ", title)
            if len(title) > 200:
                title = title[:200]
            break

    # If not found via regex, try to extract from table
    if not title:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                cell_text = cells[0].get_text(strip=True).lower()
                if "article" in cell_text or "titre" in cell_text or "produit" in cell_text:
                    title = cells[1].get_text(strip=True)
                    break

    # Try extracting from divs with class/structure
    if not title:
        for div in soup.find_all("div", class_=re.compile("transaction|article|product", re.I)):
            text_content = div.get_text(strip=True)
            if len(text_content) > 5 and len(text_content) < 200:
                title = text_content
                break

    # --- Price ---
    price: Optional[str] = None
    # Try various patterns for price extraction
    price_patterns = [
        r"(?:Montant|Prix)\s*:?\s*(\d+)[,\.]\d{2}\s*€",
        r"(\d+)[,\.]\d{2}\s*€",
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            price = match.group(1)
            break

    # Try to extract from table if not found
    if not price:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                cell_text = cells[0].get_text(strip=True).lower()
                if "montant" in cell_text or "prix" in cell_text:
                    price_text = cells[1].get_text(strip=True)
                    m = re.search(r"(\d+)", price_text)
                    if m:
                        price = m.group(1)
                    break

    # --- Date ---
    date = _extract_date_from_text(text)

    # --- Seller info ---
    seller_info: Optional[str] = None
    # Try to find seller username/name
    seller_patterns = [
        r"Vendeur\s*:?\s*(.+?)(?:\n|$)",
        r"Venduse\s*:?\s*(.+?)(?:\n|$)",
        r"Utilisateur\s*:?\s*(.+?)(?:\n|$)",
    ]
    for pattern in seller_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            seller_info = match.group(1).strip()
            if len(seller_info) > 100:
                seller_info = seller_info[:100]
            break

    # Try to extract from table if not found
    if not seller_info:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                cell_text = cells[0].get_text(strip=True).lower()
                if any(kw in cell_text for kw in ["vendeur", "venduse", "utilisateur"]):
                    seller_info = cells[1].get_text(strip=True)
                    break

    if title and price:
        logger.info("Parsed Leboncoin purchase: '%s' for %s EUR", title, price)
        return {
            "title": title,
            "price": price,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "seller_info": seller_info or "",
            "type": "purchase",
        }

    return None
