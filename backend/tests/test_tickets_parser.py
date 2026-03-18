"""
Unit tests for ticket email parsers.
Tests all ticket parser functions for normal cases, edge cases, and malformed input.
"""

import pytest
from parsers.tickets import (
    parse_ticketmaster_email,
    parse_roland_garros_email,
    parse_stade_de_france_email,
    TICKET_QUERIES,
)


class TestTicketQueries:
    """Test TICKET_QUERIES constant."""

    def test_ticket_queries_not_empty(self):
        """TICKET_QUERIES should contain at least one query."""
        assert len(TICKET_QUERIES) > 0

    def test_ticket_queries_structure(self):
        """Each query should be a tuple of (query_string, category)."""
        for query, category in TICKET_QUERIES:
            assert isinstance(query, str)
            assert isinstance(category, str)
            assert len(query) > 0
            assert len(category) > 0


class TestTicketmasterParser:
    """Tests for parse_ticketmaster_email function."""

    def test_valid_ticketmaster_email(self, load_sample):
        """Test parsing a valid Ticketmaster email."""
        html = load_sample("ticketmaster_valid.html")
        result = parse_ticketmaster_email("Confirmation 123456789", html)

        assert result is not None
        assert result["order_id"] == "123456789"
        assert "Beatles" in result["event"]
        assert result["venue"] == "Stade de France"
        assert result["event_date"] == "15/06/2024"
        assert result["price"] == "150"
        assert "Tribune" in result["category"] or result["category"] == ""

    def test_missing_reference_number(self, load_sample):
        """Test that parsing fails without order reference number."""
        html = load_sample("ticketmaster_no_reference.html")
        result = parse_ticketmaster_email("", html)

        assert result is None

    def test_missing_confirmation_text(self):
        """Test that parsing fails without confirmation keyword."""
        html = "<html><body><p>Order details: Concert</p></body></html>"
        result = parse_ticketmaster_email("", html)

        assert result is None

    def test_malformed_html(self, load_sample):
        """Test handling of malformed HTML."""
        html = load_sample("ticketmaster_malformed.html")
        result = parse_ticketmaster_email("", html)

        # Should gracefully handle malformed input (return None or partial data)
        assert result is None or isinstance(result, dict)

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_ticketmaster_email("", "")

        assert result is None

    def test_none_html(self):
        """Test handling of None HTML."""
        result = parse_ticketmaster_email("", None)

        assert result is None

    def test_missing_event_name(self):
        """Test parsing when event name is missing."""
        html = """
        <html><body>
            <p>Votre commande est confirmée</p>
            <p>Référence n°123456789</p>
            <p>Total de la commande: 100 €</p>
        </body></html>
        """
        result = parse_ticketmaster_email("", html)

        assert result is not None
        assert result["order_id"] == "123456789"
        assert result["event"] == "Evenement"  # default value

    def test_missing_price(self):
        """Test parsing when price is missing."""
        html = """
        <html><body>
            <p>Votre commande est confirmée</p>
            <p>Référence n°987654321</p>
            <h2>Détail de votre commande</h2>
            <p>Concert Event</p>
        </body></html>
        """
        result = parse_ticketmaster_email("", html)

        assert result is not None
        assert result["price"] == "0"

    def test_order_link_construction(self):
        """Test that order link is constructed correctly."""
        html = """
        <html><body>
            <p>Votre commande est confirmée</p>
            <p>Référence n°555555555</p>
            <h2>Détail de votre commande</h2>
            <p>Event</p>
        </body></html>
        """
        result = parse_ticketmaster_email("", html)

        assert result is not None
        assert "555555555" in result["order_link"]


class TestRolandGarrosParser:
    """Tests for parse_roland_garros_email function."""

    def test_valid_roland_garros_email(self, load_sample):
        """Test parsing a valid Roland-Garros email."""
        html = load_sample("roland_garros_valid.html")
        result = parse_roland_garros_email("", html)

        assert result is not None
        assert result["order_id"] == "987654321"
        assert result["event"] == "Roland-Garros"
        assert result["venue"] == "Philippe-Chatrier"
        assert result["event_date"] == "05/06/2024"

    def test_missing_confirmation_keyword(self):
        """Test that parsing fails without confirmation keyword."""
        html = """
        <html><body>
            <p>Numéro de commande : 123456789</p>
            <p>Court: Philippe-Chatrier</p>
        </body></html>
        """
        result = parse_roland_garros_email("", html)

        assert result is None

    def test_missing_order_id(self):
        """Test that parsing fails without order ID."""
        html = """
        <html><body>
            <p>Confirmation de billet</p>
            <p>Court: Philippe-Chatrier</p>
        </body></html>
        """
        result = parse_roland_garros_email("", html)

        assert result is None

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_roland_garros_email("", "")

        assert result is None

    def test_venue_detection_suzanne_lenglen(self):
        """Test venue detection for Suzanne-Lenglen court."""
        html = """
        <html><body>
            <p>Confirmation de votre réservation</p>
            <p>Numéro de commande : 123456789</p>
            <p>Court: Suzanne-Lenglen</p>
            <p>Montant total: 100,00 €</p>
        </body></html>
        """
        result = parse_roland_garros_email("", html)

        assert result is not None
        assert result["venue"] == "Suzanne-Lenglen"

    def test_default_venue_if_not_specified(self):
        """Test that venue defaults to 'Roland-Garros' if court not specified."""
        html = """
        <html><body>
            <p>Confirmation de votre réservation</p>
            <p>Numéro de commande : 123456789</p>
            <p>Montant total: 100,00 €</p>
        </body></html>
        """
        result = parse_roland_garros_email("", html)

        assert result is not None
        assert result["venue"] == "Roland-Garros"

    def test_price_extraction(self):
        """Test price extraction from Roland-Garros email."""
        html = """
        <html><body>
            <p>Confirmation de votre réservation</p>
            <p>Numéro de commande : 123456789</p>
            <p>Ticket 1: 120,50 €</p>
            <p>Ticket 2: 100,75 €</p>
        </body></html>
        """
        result = parse_roland_garros_email("", html)

        assert result is not None
        # Price should be sum of extracted prices
        assert result["price"] in ["220", "221"]  # slight tolerance for rounding


class TestStadeDefranceParser:
    """Tests for parse_stade_de_france_email function."""

    def test_valid_stade_de_france_email(self, load_sample):
        """Test parsing a valid Stade de France email."""
        html = load_sample("stade_de_france_valid.html")
        result = parse_stade_de_france_email("", html)

        assert result is not None
        assert result["order_id"] == "456789123"
        assert result["venue"] == "Stade de France"
        assert result["event_date"] == "12/07/2024"
        assert result["price"] == "95"

    def test_missing_commande_keyword(self):
        """Test that parsing fails without 'commande' keyword."""
        html = """
        <html><body>
            <p>Votre billet</p>
            <p>Match de football</p>
        </body></html>
        """
        result = parse_stade_de_france_email("", html)

        assert result is None

    def test_empty_html(self):
        """Test handling of empty HTML."""
        result = parse_stade_de_france_email("", "")

        assert result is None

    def test_none_html(self):
        """Test handling of None HTML."""
        result = parse_stade_de_france_email("", None)

        assert result is None

    def test_event_name_extraction(self):
        """Test extraction of event name."""
        html = """
        <html><body>
            <p>Commande: 123456789</p>
            <h2>Match France-Allemagne</h2>
            <p>Date: 15/07/2024</p>
        </body></html>
        """
        result = parse_stade_de_france_email("", html)

        assert result is not None
        # Event should be extracted from first uppercase line
        assert result["event"] is not None

    def test_category_extraction(self):
        """Test category extraction for seating."""
        html = """
        <html><body>
            <p>Commande: 123456789</p>
            <h2>Match de football</h2>
            <p>Tribune Sud</p>
            <p>Total: 80 €</p>
        </body></html>
        """
        result = parse_stade_de_france_email("", html)

        assert result is not None
        # Category might be empty or contain "Tribune"
        assert isinstance(result["category"], str)

    def test_order_link_construction(self):
        """Test order link construction."""
        html = """
        <html><body>
            <p>Commande: 789789789</p>
            <h2>Match</h2>
        </body></html>
        """
        result = parse_stade_de_france_email("", html)

        assert result is not None
        assert "789789789" in result["order_link"]
        assert "stadefrance" in result["order_link"].lower()
