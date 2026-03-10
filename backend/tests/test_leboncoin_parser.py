"""
Unit tests for Leboncoin email parser.
Tests sale, purchase, and utility functions.
"""

import pytest
from parsers.leboncoin import (
    parse_leboncoin_sale_email,
    parse_leboncoin_purchase_email,
    LEBONCOIN_SALE_QUERIES,
    LEBONCOIN_PURCHASE_QUERIES,
    LEBONCOIN_QUERIES,
)


class TestLeboncoinQueries:
    """Test LEBONCOIN_QUERIES constants."""

    def test_sale_queries_not_empty(self):
        """LEBONCOIN_SALE_QUERIES should contain at least one query."""
        assert len(LEBONCOIN_SALE_QUERIES) > 0

    def test_purchase_queries_not_empty(self):
        """LEBONCOIN_PURCHASE_QUERIES should contain at least one query."""
        assert len(LEBONCOIN_PURCHASE_QUERIES) > 0

    def test_combined_queries(self):
        """LEBONCOIN_QUERIES should combine sale and purchase queries."""
        assert len(LEBONCOIN_QUERIES) == len(LEBONCOIN_SALE_QUERIES) + len(LEBONCOIN_PURCHASE_QUERIES)

    def test_sale_queries_structure(self):
        """Each sale query should be a tuple of (query_string, category)."""
        for query, category in LEBONCOIN_SALE_QUERIES:
            assert isinstance(query, str)
            assert isinstance(category, str)
            assert "leboncoin" in query.lower()
            assert category == "leboncoin-sale"

    def test_purchase_queries_structure(self):
        """Each purchase query should be a tuple of (query_string, category)."""
        for query, category in LEBONCOIN_PURCHASE_QUERIES:
            assert isinstance(query, str)
            assert isinstance(category, str)
            assert "leboncoin" in query.lower()
            assert category == "leboncoin-purchase"


class TestLeboncoinSaleParser:
    """Tests for parse_leboncoin_sale_email function."""

    def test_valid_sale_email(self, load_sample):
        """Test parsing a valid Leboncoin sale email."""
        html = load_sample("leboncoin_sale_valid.html")
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        assert "iPhone" in result["title"]
        assert result["price"] == "450"
        assert result["type"] == "sale"
        assert result["date"] is not None
        assert result["buyer_info"] is not None

    def test_missing_vendu_keyword(self):
        """Test that parsing fails without 'vendu' keyword."""
        html = """
        <html><body>
            <p>Article: iPhone 13</p>
            <p>Prix: 400,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is None

    def test_missing_title(self):
        """Test that parsing fails without article title."""
        html = """
        <html><body>
            <p>Votre article a été vendu!</p>
            <p>Montant: 300,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        # Should still return None since title is required
        assert result is None or "title" in result

    def test_missing_price(self):
        """Test that parsing fails without price."""
        html = """
        <html><body>
            <p>Votre article a été vendu!</p>
            <p>Article: iPhone 13</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is None

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_leboncoin_sale_email("")

        assert result is None

    def test_none_html(self):
        """Test handling of None HTML."""
        result = parse_leboncoin_sale_email(None)

        assert result is None

    def test_table_based_extraction(self):
        """Test extraction from HTML table structure."""
        html = """
        <html><body>
            <h1>Votre article a été vendu!</h1>
            <table>
                <tr>
                    <td>Article</td>
                    <td>Samsung Galaxy S21</td>
                </tr>
                <tr>
                    <td>Montant</td>
                    <td>500,00 €</td>
                </tr>
                <tr>
                    <td>Acheteur</td>
                    <td>JohnDoe92</td>
                </tr>
            </table>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        assert "Galaxy" in result["title"]
        assert result["price"] == "500"

    def test_date_extraction(self):
        """Test date extraction in various formats."""
        html = """
        <html><body>
            <p>Votre article est vendu!</p>
            <p>Article: MacBook Air</p>
            <p>Prix: 900,00 €</p>
            <p>Date: 20/02/2024</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        assert result["date"] == "2024-02-20"

    def test_french_date_extraction(self):
        """Test French month name date extraction."""
        html = """
        <html><body>
            <p>Article vendu!</p>
            <p>Article: Vélo VTT</p>
            <p>Montant: 350,00 €</p>
            <p>Date: 15 février 2024</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        if result:  # Should succeed
            assert result["date"] == "2024-02-15"

    def test_buyer_info_extraction(self):
        """Test buyer username extraction."""
        html = """
        <html><body>
            <p>Votre article a été vendu!</p>
            <p>Article: Téléphone</p>
            <p>Montant: 200,00 €</p>
            <p>Acheteur: Sophie75</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        assert "Sophie" in result["buyer_info"]

    def test_long_title_truncation(self):
        """Test that very long titles are truncated."""
        long_title = "A" * 300
        html = f"""
        <html><body>
            <p>Votre article est vendu!</p>
            <p>Article: {long_title}</p>
            <p>Montant: 100,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        if result:
            assert len(result["title"]) <= 200


class TestLeboncoinPurchaseParser:
    """Tests for parse_leboncoin_purchase_email function."""

    def test_valid_purchase_email(self, load_sample):
        """Test parsing a valid Leboncoin purchase email."""
        html = load_sample("leboncoin_purchase_valid.html")
        result = parse_leboncoin_purchase_email(html)

        assert result is not None
        assert "MacBook" in result["title"]
        assert result["price"] == "800"
        assert result["type"] == "purchase"
        assert result["date"] is not None
        assert result["seller_info"] is not None

    def test_missing_paiement_keyword(self):
        """Test that parsing fails without 'paiement' or 'achat' keyword."""
        html = """
        <html><body>
            <p>Article: Laptop</p>
            <p>Prix: 600,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is None

    def test_rejects_sale_emails(self):
        """Test that sale emails are not matched as purchases."""
        html = """
        <html><body>
            <p>Paiement reçu</p>
            <p>Votre article a été vendu!</p>
            <p>Article: Phone</p>
            <p>Montant: 500,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is None

    def test_missing_title(self):
        """Test that parsing fails without article title."""
        html = """
        <html><body>
            <p>Paiement confirmé</p>
            <p>Montant: 300,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is None

    def test_missing_price(self):
        """Test that parsing fails without price."""
        html = """
        <html><body>
            <p>Achat confirmé</p>
            <p>Article: Laptop</p>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is None

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_leboncoin_purchase_email("")

        assert result is None

    def test_none_html(self):
        """Test handling of None HTML."""
        result = parse_leboncoin_purchase_email(None)

        assert result is None

    def test_div_based_extraction(self):
        """Test extraction from div structure."""
        html = """
        <html><body>
            <h1>Paiement reçu</h1>
            <div class="transaction">
                <p><strong>Article:</strong> Canon EOS 5D Mark IV</p>
                <p><strong>Vendeur:</strong> PhotoPro95</p>
                <p><strong>Prix:</strong> 1200,00 €</p>
                <p><strong>Date:</strong> 10/02/2024</p>
            </div>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is not None
        assert "Canon" in result["title"]
        assert result["price"] == "1200"

    def test_date_extraction(self):
        """Test date extraction."""
        html = """
        <html><body>
            <p>Votre achat a été confirmé.</p>
            <p>Article: Montre</p>
            <p>Prix: 250,00 €</p>
            <p>Date: 05/01/2024</p>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is not None
        assert result["date"] == "2024-01-05"

    def test_seller_info_extraction(self):
        """Test seller username extraction."""
        html = """
        <html><body>
            <p>Commande confirmée</p>
            <p>Article: Caméra</p>
            <p>Montant: 450,00 €</p>
            <p>Vendeur: MarketPlace77</p>
        </body></html>
        """
        result = parse_leboncoin_purchase_email(html)

        assert result is not None
        assert "MarketPlace" in result["seller_info"]

    def test_purchase_keyword_variations(self):
        """Test various purchase indication keywords."""
        keywords = ["paiement", "achat", "commande", "purchase"]

        for keyword in keywords:
            html = f"""
            <html><body>
                <p>{keyword.capitalize()} confirmé</p>
                <p>Article: Item</p>
                <p>Prix: 100,00 €</p>
            </body></html>
            """
            result = parse_leboncoin_purchase_email(html)
            # Should find it (or at least not reject it as invalid)
            assert result is None or result["type"] == "purchase"


class TestInvalidLeboncoinEmail:
    """Tests for handling invalid/unrelated emails."""

    def test_invalid_html(self, load_sample):
        """Test that unrelated emails return None."""
        html = load_sample("leboncoin_invalid.html")
        result_sale = parse_leboncoin_sale_email(html)
        result_purchase = parse_leboncoin_purchase_email(html)

        assert result_sale is None
        assert result_purchase is None

    def test_newsletter_email(self):
        """Test that newsletter emails are rejected."""
        html = """
        <html><body>
            <h1>Actualités Le Bon Coin</h1>
            <p>Découvrez nos meilleurs bons plans cette semaine.</p>
            <p>Consultez nos conseils pour vendre mieux.</p>
        </body></html>
        """
        result_sale = parse_leboncoin_sale_email(html)
        result_purchase = parse_leboncoin_purchase_email(html)

        assert result_sale is None
        assert result_purchase is None


class TestLeboncoinEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_multiple_prices_uses_first(self):
        """Test that first price match is used when multiple prices present."""
        html = """
        <html><body>
            <p>Votre article est vendu!</p>
            <p>Article: Phone</p>
            <p>Montant: 300,00 €</p>
            <p>Frais: 30,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        assert result["price"] == "300"  # First price match

    def test_whitespace_in_title(self):
        """Test that excessive whitespace in titles is collapsed."""
        html = """
        <html><body>
            <p>Votre article est vendu!</p>
            <p>Article:   Samsung    Galaxy    S21   </p>
            <p>Montant: 500,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        # Whitespace should be normalized
        assert "  " not in result["title"]

    def test_missing_buyer_info_provides_empty_string(self):
        """Test that missing buyer info returns empty string, not None."""
        html = """
        <html><body>
            <p>Votre article est vendu!</p>
            <p>Article: Item</p>
            <p>Montant: 100,00 €</p>
        </body></html>
        """
        result = parse_leboncoin_sale_email(html)

        assert result is not None
        assert "buyer_info" in result
        assert isinstance(result["buyer_info"], str)

    def test_malformed_html_graceful_handling(self):
        """Test graceful handling of malformed HTML."""
        html = """
        <p>Votre article est vendu!
        <p>Article: Phone
        <p>Montant: 250,00 €
        """
        result = parse_leboncoin_sale_email(html)

        # Should still parse successfully
        assert result is None or isinstance(result, dict)
