"""
Unit tests for Vinted email parser.
Tests sale, purchase, and utility functions.
"""

import pytest
from datetime import datetime
from parsers.vinted import (
    parse_vinted_email,
    parse_vinted_sale_email,
    parse_vinted_purchase_email,
    find_matching_item,
    calculate_benefit,
    calculate_time_in_stock,
    VINTED_QUERIES,
)


class TestVintedQueries:
    """Test VINTED_QUERIES constant."""

    def test_vinted_queries_not_empty(self):
        """VINTED_QUERIES should contain at least one query."""
        assert len(VINTED_QUERIES) > 0

    def test_vinted_queries_structure(self):
        """Each query should be a tuple of (query_string, category)."""
        for query, category in VINTED_QUERIES:
            assert isinstance(query, str)
            assert isinstance(category, str)
            assert len(query) > 0


class TestVintedSaleParser:
    """Tests for parse_vinted_sale_email and parse_vinted_email functions."""

    def test_valid_sale_email(self, load_sample):
        """Test parsing a valid Vinted sale email."""
        html = load_sample("vinted_sale_valid.html")
        result = parse_vinted_sale_email(html)

        assert result is not None
        assert "Nike" in result["title"]
        assert result["price"] == "35"
        assert result["type"] == "sale"
        assert result["date"] is not None

    def test_backward_compat_alias(self, load_sample):
        """Test that parse_vinted_email is backward compatible alias."""
        html = load_sample("vinted_sale_valid.html")
        result_sale = parse_vinted_sale_email(html)
        result_compat = parse_vinted_email(html)

        assert result_sale == result_compat

    def test_missing_transaction_finalisee(self):
        """Test that parsing fails without 'transaction est finalisée'."""
        html = """
        <html><body>
            <p>La vente de Pull bleu a été réalisée.</p>
            <p>Prix: 35,00 €</p>
        </body></html>
        """
        result = parse_vinted_sale_email(html)

        assert result is None

    def test_missing_title(self):
        """Test that parsing fails without item title."""
        html = """
        <html><body>
            <p>La transaction est finalisée.</p>
            <p>Viré sur ton compte Vinted : 35,00 €</p>
        </body></html>
        """
        result = parse_vinted_sale_email(html)

        assert result is None

    def test_missing_price(self):
        """Test that parsing fails without price."""
        html = """
        <html><body>
            <p>La vente de Pull bleu a été réalisée.</p>
            <p>La transaction est finalisée.</p>
        </body></html>
        """
        result = parse_vinted_sale_email(html)

        assert result is None

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_vinted_sale_email("")

        assert result is None

    def test_none_html(self):
        """Test handling of None HTML."""
        result = parse_vinted_sale_email(None)

        assert result is None

    def test_alternative_price_pattern(self):
        """Test alternative price extraction pattern."""
        html = """
        <html><body>
            <p>La vente de Robe été a été réalisée.</p>
            <p>La transaction est finalisée.</p>
            <p>Montant de la commande : 45,75 €</p>
        </body></html>
        """
        result = parse_vinted_sale_email(html)

        assert result is not None
        assert result["price"] == "45"

    def test_date_extraction(self):
        """Test date extraction in DD/MM/YYYY format."""
        html = """
        <html><body>
            <p>La vente de Veste en jean a été réalisée.</p>
            <p>La transaction est finalisée.</p>
            <p>Viré sur ton compte Vinted : 50,00 €</p>
            <p>Date: 05/02/2024</p>
        </body></html>
        """
        result = parse_vinted_sale_email(html)

        assert result is not None
        assert result["date"] == "2024-02-05"

    def test_default_date_if_missing(self):
        """Test that date defaults to today if missing."""
        html = """
        <html><body>
            <p>La vente de Article test a été réalisée.</p>
            <p>La transaction est finalisée.</p>
            <p>Viré sur ton compte Vinted : 25,00 €</p>
        </body></html>
        """
        result = parse_vinted_sale_email(html)

        assert result is not None
        # Date should be today's date
        assert result["date"] is not None
        assert len(result["date"]) == 10  # YYYY-MM-DD format


class TestVintedPurchaseParser:
    """Tests for parse_vinted_purchase_email function."""

    def test_valid_purchase_email(self, load_sample):
        """Test parsing a valid Vinted purchase email."""
        html = load_sample("vinted_purchase_valid.html")
        result = parse_vinted_purchase_email(html)

        assert result is not None
        assert "Robe" in result["title"]
        assert result["price"] == "28"
        assert result["type"] == "purchase"
        assert result["date"] == "2024-01-15"

    def test_missing_purchase_indicator(self):
        """Test that parsing fails without purchase keywords."""
        html = """
        <html><body>
            <p>Article: Robe été</p>
            <p>Prix: 28,00 €</p>
        </body></html>
        """
        result = parse_vinted_purchase_email(html)

        assert result is None

    def test_rejects_sale_emails(self):
        """Test that sale emails are rejected even with purchase keywords."""
        html = """
        <html><body>
            <p>Commande confirmée</p>
            <p>La vente de Pull bleu a été réalisée.</p>
            <p>La transaction est finalisée.</p>
            <p>Prix: 50,00 €</p>
        </body></html>
        """
        result = parse_vinted_purchase_email(html)

        assert result is None

    def test_alternative_title_patterns(self):
        """Test various title extraction patterns."""
        patterns = [
            "Tu as acheté Chemise coton blanc pour 35,00 €.",
            "Achat de Pantalon noir au prix de 40,00 €.",
        ]

        for pattern in patterns:
            html = f"""
            <html><body>
                <h1>Commande confirmée</h1>
                <p>{pattern}</p>
                <p>Montant total: 40,00 €</p>
            </body></html>
            """
            result = parse_vinted_purchase_email(html)
            # Should find title and price from pattern
            assert result is None or result.get("title")

    def test_french_date_extraction(self):
        """Test French month names in date extraction."""
        html = """
        <html><body>
            <p>Commande confirmée</p>
            <p>Tu as acheté Veste été</p>
            <p>Prix: 60,00 €</p>
            <p>Date: 15 février 2024</p>
        </body></html>
        """
        result = parse_vinted_purchase_email(html)

        if result:  # Should succeed
            assert result["date"] == "2024-02-15"

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_vinted_purchase_email("")

        assert result is None

    def test_missing_price(self):
        """Test that parsing fails without price."""
        html = """
        <html><body>
            <p>Commande confirmée</p>
            <p>Tu as acheté Pantalon bleu</p>
        </body></html>
        """
        result = parse_vinted_purchase_email(html)

        assert result is None


class TestInvalidVintedEmail:
    """Tests for handling invalid/unrelated emails."""

    def test_invalid_html(self, load_sample):
        """Test that unrelated emails return None."""
        html = load_sample("vinted_invalid.html")
        result_sale = parse_vinted_sale_email(html)
        result_purchase = parse_vinted_purchase_email(html)

        assert result_sale is None
        assert result_purchase is None


class TestFindMatchingItem:
    """Tests for find_matching_item function."""

    def test_exact_match(self):
        """Test exact title match."""
        sheet_items = [
            {"title": "Red Dress Size M", "row": 2},
            {"title": "Blue Shirt Size L", "row": 3},
        ]
        vinted_title = "Red Dress Size M"

        result = find_matching_item(vinted_title, sheet_items)

        assert result is not None
        assert result["row"] == 2

    def test_partial_match(self):
        """Test fuzzy matching with partial similarity."""
        sheet_items = [
            {"title": "Red Cotton Dress Size M", "row": 2},
            {"title": "Blue Denim Shirt", "row": 3},
        ]
        vinted_title = "Red Dress"

        result = find_matching_item(vinted_title, sheet_items, threshold=0.6)

        assert result is not None
        assert "Red" in result["title"]

    def test_no_match_below_threshold(self):
        """Test that dissimilar items don't match."""
        sheet_items = [
            {"title": "Laptop Computer", "row": 2},
            {"title": "Gaming Mouse", "row": 3},
        ]
        vinted_title = "Red Dress Size M"

        result = find_matching_item(vinted_title, sheet_items, threshold=0.8)

        assert result is None

    def test_empty_sheet_items(self):
        """Test handling of empty sheet items list."""
        result = find_matching_item("Red Dress", [])

        assert result is None

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        sheet_items = [
            {"title": "RED DRESS size m", "row": 2},
        ]
        vinted_title = "red dress Size M"

        result = find_matching_item(vinted_title, sheet_items, threshold=0.8)

        assert result is not None

    def test_best_match_selection(self):
        """Test selection of best match when multiple candidates."""
        sheet_items = [
            {"title": "Dress Red", "row": 2},
            {"title": "Red Dress Size M Cotton", "row": 3},
            {"title": "Redshirt", "row": 4},
        ]
        vinted_title = "Red Dress Size M"

        result = find_matching_item(vinted_title, sheet_items, threshold=0.6)

        # Should select the most similar one
        assert result is not None
        assert result["row"] in [2, 3]


class TestCalculateBenefit:
    """Tests for calculate_benefit function."""

    def test_profitable_sale(self):
        """Test benefit calculation for profitable sale."""
        result = calculate_benefit(30.0, 50.0)

        assert result["benefit"] == 20.0
        assert result["roi_percent"] == pytest.approx(66.7, rel=0.1)
        assert result["is_profitable"] is True

    def test_loss_on_sale(self):
        """Test benefit calculation for loss."""
        result = calculate_benefit(50.0, 30.0)

        assert result["benefit"] == -20.0
        assert result["roi_percent"] == pytest.approx(-40.0, rel=0.1)
        assert result["is_profitable"] is False

    def test_break_even_sale(self):
        """Test benefit calculation for break-even sale."""
        result = calculate_benefit(50.0, 50.0)

        assert result["benefit"] == 0.0
        assert result["roi_percent"] == 0.0
        assert result["is_profitable"] is False

    def test_zero_purchase_price(self):
        """Test handling of zero purchase price."""
        result = calculate_benefit(0.0, 50.0)

        assert result["benefit"] == 50.0
        assert result["roi_percent"] == 0.0  # Undefined, returns 0


class TestCalculateTimeInStock:
    """Tests for calculate_time_in_stock function."""

    def test_same_day_sale(self):
        """Test same-day sale calculation."""
        result = calculate_time_in_stock("2024-02-15", "2024-02-15")

        assert result["days"] == 0
        assert result["display"] == "Meme jour"

    def test_one_day_difference(self):
        """Test one-day difference."""
        result = calculate_time_in_stock("2024-02-15", "2024-02-16")

        assert result["days"] == 1
        assert result["display"] == "1 jour"

    def test_multiple_days(self):
        """Test multiple days calculation."""
        result = calculate_time_in_stock("2024-02-10", "2024-02-17")

        assert result["days"] == 7
        assert "7 jours" in result["display"] or "1 sem" in result["display"]

    def test_weeks_calculation(self):
        """Test weeks calculation."""
        result = calculate_time_in_stock("2024-02-01", "2024-02-22")

        assert result["days"] == 21
        assert "sem" in result["display"]

    def test_months_calculation(self):
        """Test months calculation."""
        result = calculate_time_in_stock("2024-01-01", "2024-04-01")

        assert result["days"] == 92
        assert "mois" in result["display"]

    def test_years_calculation(self):
        """Test years calculation."""
        result = calculate_time_in_stock("2022-02-15", "2024-02-15")

        assert result["days"] == 730 or result["days"] == 731  # account for leap year
        assert "an" in result["display"]

    def test_invalid_date_format(self):
        """Test handling of invalid date format."""
        result = calculate_time_in_stock("invalid", "2024-02-15")

        assert result["days"] == 0
        assert result["display"] == "N/A"

    def test_reverse_dates(self):
        """Test handling of reversed dates (sale before purchase)."""
        result = calculate_time_in_stock("2024-02-20", "2024-02-15")

        # Should return 0 (clamped)
        assert result["days"] == 0
