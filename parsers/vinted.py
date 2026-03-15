"""
Billets & Vinted Monitor MVP - Vinted Email Parser
Adapted from vinted_to_sheets.py to use Gmail API instead of IMAP.

Supports:
- Sale emails (transaction finalisée) → extract sale price
- Purchase emails (commande confirmée) → extract purchase price [PRO]
- Benefit & ROI calculation [PRO]
- Time in stock tracking [PRO]
"""

import re
import logging
from datetime import datetime
from typing import Optional
from difflib import SequenceMatcher

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Gmail API search queries for Vinted
VINTED_SALE_QUERIES: list[tuple[str, str]] = [
    ("from:vinted subject:transaction", "vinted-sale"),
]

VINTED_PURCHASE_QUERIES: list[tuple[str, str]] = [
    ("from:vinted subject:commande", "vinted-purchase"),
    ("from:vinted subject:achat", "vinted-purchase"),
]

# All queries (starter uses only SALE, pro uses both)
VINTED_QUERIES = VINTED_SALE_QUERIES  # backward compat


def _normalize_title(title: str) -> str:
    """Normalize a title for fuzzy comparison."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def _extract_date_from_text(text: str) -> Optional[str]:
    """Extract a date from email text. Returns ISO format string."""
    # Try DD/MM/YYYY
    match = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", text)
    if match:
        day, month, year = match.group(1), match.group(2), match.group(3)
        try:
            dt = datetime(int(year), int(month), int(day))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Try "DD mois YYYY" (French)
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


def parse_vinted_sale_email(html: str) -> Optional[dict]:
    """Parse a Vinted finalized transaction (SALE) email.

    Returns a dict with keys: title, price, date, type -- or None if not valid.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    # Must contain "transaction est finalisée"
    if "transaction est finalisée" not in text.lower():
        return None

    # --- Title ---
    title: Optional[str] = None
    match = re.search(r"La vente de\s+(.+?)\s+a été réalisée", text, re.IGNORECASE)
    if match:
        title = match.group(1).strip()
        title = re.sub(r"\s+", " ", title)

    # --- Price (rounded down to the euro) ---
    price: Optional[str] = None
    match = re.search(
        r"Viré sur ton compte Vinted\s*:\s*(\d+)[,\.]\d{2}\s*€", text, re.IGNORECASE
    )
    if match:
        price = match.group(1)
    else:
        match = re.search(
            r"Montant de la commande\s*:\s*(\d+)[,\.]\d{2}\s*€", text, re.IGNORECASE
        )
        if match:
            price = match.group(1)

    # --- Date ---
    date = _extract_date_from_text(text)

    if title and price:
        logger.info("Parsed Vinted sale: '%s' for %s EUR", title, price)
        return {
            "title": title,
            "price": price,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "type": "sale",
        }

    return None


def parse_vinted_purchase_email(html: str) -> Optional[dict]:
    """Parse a Vinted purchase confirmation email. [PRO feature]

    Returns a dict with keys: title, price, date, type -- or None if not valid.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    # Look for purchase indicators
    purchase_indicators = [
        "commande confirmée",
        "achat confirmé",
        "tu as acheté",
        "ta commande",
        "commande est confirmée",
    ]
    text_lower = text.lower()
    if not any(indicator in text_lower for indicator in purchase_indicators):
        return None

    # Skip if this is a SALE email (not a purchase)
    if "transaction est finalisée" in text_lower:
        return None
    if "la vente de" in text_lower:
        return None

    # --- Title ---
    title: Optional[str] = None
    # Try various patterns Vinted uses for purchase confirmations
    patterns = [
        r"(?:tu as acheté|achat de)\s+(.+?)(?:\s+pour|\s+au prix)",
        r"Article\s*:\s*(.+?)(?:\n|$)",
        r"commande.*?:\s*(.+?)(?:\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"\s+", " ", title)
            break

    # --- Price ---
    price: Optional[str] = None
    price_patterns = [
        r"Prix\s*:\s*(\d+)[,\.]\d{2}\s*€",
        r"Total\s*:\s*(\d+)[,\.]\d{2}\s*€",
        r"(\d+)[,\.]\d{2}\s*€",
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            price = match.group(1)
            break

    # --- Date ---
    date = _extract_date_from_text(text)

    if title and price:
        logger.info("Parsed Vinted purchase: '%s' for %s EUR", title, price)
        return {
            "title": title,
            "price": price,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "type": "purchase",
        }

    return None


# Keep backward compat alias
def parse_vinted_email(html: str) -> Optional[dict]:
    """Parse a Vinted sale email (backward compat with Starter plan)."""
    return parse_vinted_sale_email(html)


def find_matching_item(
    vinted_title: str,
    sheet_items: list[dict],
    threshold: float = 0.6,
) -> Optional[dict]:
    """Find the best matching item from the sheet using fuzzy matching.

    Args:
        vinted_title: The item title from the Vinted email.
        sheet_items: List of dicts with at least a 'title' key and a 'row' key.
        threshold: Minimum similarity score (0.0 to 1.0).

    Returns:
        The best matching item dict, or None.
    """
    vinted_normalized = _normalize_title(vinted_title)
    best_match: Optional[dict] = None
    best_score: float = 0.0

    for item in sheet_items:
        item_normalized = _normalize_title(item.get("title", ""))

        # Substring match gets a high score
        if vinted_normalized in item_normalized or item_normalized in vinted_normalized:
            score = 0.9
        else:
            seq_score = SequenceMatcher(None, vinted_normalized, item_normalized).ratio()
            # Token overlap: how many words from the query appear in the candidate
            query_tokens = set(vinted_normalized.split())
            item_tokens = set(item_normalized.split())
            if query_tokens:
                token_score = len(query_tokens & item_tokens) / len(query_tokens)
            else:
                token_score = 0.0
            score = max(seq_score, token_score)

        if score > best_score and score >= threshold:
            best_score = score
            best_match = item

    if best_match:
        logger.info(
            "Fuzzy match: '%s' -> '%s' (score: %.0f%%)",
            vinted_title,
            best_match.get("title", ""),
            best_score * 100,
        )

    return best_match


def calculate_benefit(purchase_price: float, sale_price: float) -> dict:
    """Calculate benefit, ROI, and format results. [PRO feature]

    Returns dict with: benefit, roi_percent, is_profitable
    """
    benefit = sale_price - purchase_price
    roi_percent = ((sale_price - purchase_price) / purchase_price * 100) if purchase_price > 0 else 0.0

    return {
        "benefit": round(benefit, 2),
        "roi_percent": round(roi_percent, 1),
        "is_profitable": benefit > 0,
    }


def calculate_time_in_stock(purchase_date: str, sale_date: str) -> dict:
    """Calculate how long an item was in stock. [PRO feature]

    Args:
        purchase_date: ISO format date string (YYYY-MM-DD)
        sale_date: ISO format date string (YYYY-MM-DD)

    Returns dict with: days, display (human readable)
    """
    try:
        d_purchase = datetime.strptime(purchase_date, "%Y-%m-%d")
        d_sale = datetime.strptime(sale_date, "%Y-%m-%d")
        delta = d_sale - d_purchase
        days = max(delta.days, 0)

        if days == 0:
            display = "Meme jour"
        elif days == 1:
            display = "1 jour"
        elif days < 7:
            display = f"{days} jours"
        elif days < 30:
            weeks = days // 7
            display = f"{weeks} sem." if weeks > 1 else "1 sem."
        elif days < 365:
            months = days // 30
            display = f"{months} mois"
        else:
            years = days // 365
            display = f"{years} an{'s' if years > 1 else ''}"

        return {"days": days, "display": display}

    except (ValueError, TypeError):
        return {"days": 0, "display": "N/A"}
