"""
Billets & Vinted Monitor MVP - Parsers
"""

from parsers.tickets import (
    parse_ticketmaster_email,
    parse_roland_garros_email,
    parse_stade_de_france_email,
    TICKET_QUERIES,
)

from parsers.vinted import (
    parse_vinted_email,
    find_matching_item,
    VINTED_QUERIES,
)

from parsers.leboncoin import (
    parse_leboncoin_sale_email,
    parse_leboncoin_purchase_email,
    LEBONCOIN_SALE_QUERIES,
    LEBONCOIN_PURCHASE_QUERIES,
    LEBONCOIN_QUERIES,
)

__all__ = [
    "parse_ticketmaster_email",
    "parse_roland_garros_email",
    "parse_stade_de_france_email",
    "parse_vinted_email",
    "find_matching_item",
    "parse_leboncoin_sale_email",
    "parse_leboncoin_purchase_email",
    "TICKET_QUERIES",
    "VINTED_QUERIES",
    "LEBONCOIN_SALE_QUERIES",
    "LEBONCOIN_PURCHASE_QUERIES",
    "LEBONCOIN_QUERIES",
]
