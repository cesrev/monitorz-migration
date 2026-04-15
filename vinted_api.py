"""
Vinted API client — authenticates with refresh_token_web and calls Vinted internal API.

Authentication flow:
  1. POST /oauth/token with grant_type=refresh_token to exchange refresh_token_web
     for a short-lived access_token_web (~2h)
  2. Use access_token as Bearer in subsequent /api/v2 calls

Sources: reverse-engineered community wrappers (Pawikoski, vincenzoAiello, Giglium, Gertje823)
Anti-bot: Vinted uses Datadome — uses cloudscraper to spoof TLS fingerprint when available.

All public methods return a dict. On error: {'error': message, 'code': error_code}.
"""

import logging
import requests

logger = logging.getLogger(__name__)

# Try to use cloudscraper for Datadome bypass, fall back to plain requests
try:
    import cloudscraper
    _USE_SCRAPER = True
    logger.info("cloudscraper available — using it for Vinted API calls")
except ImportError:
    _USE_SCRAPER = False
    logger.warning("cloudscraper not installed — Vinted API calls may be blocked by Datadome")

_VINTED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}

_REQUEST_TIMEOUT = 12  # seconds


class VintedAuthError(Exception):
    """Raised when the refresh token is invalid or expired."""


class VintedAPIError(Exception):
    """Raised on unexpected API errors (network, 5xx, unexpected payload)."""


class VintedAPI:
    """Stateless Vinted API client. Pass refresh_token_web on each call."""

    # ─────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────

    def _session(self):
        if _USE_SCRAPER:
            return cloudscraper.create_scraper()
        return requests.Session()

    def _base_url(self, domain: str) -> str:
        if domain == "uk":
            return "https://www.vinted.co.uk"
        return f"https://www.vinted.{domain}"

    def _get_access_token(self, refresh_token: str, domain: str = "fr") -> str:
        """Exchange refresh_token_web for a short-lived access_token_web."""
        url = f"{self._base_url(domain)}/oauth/token"
        headers = {
            **_VINTED_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "web",
        }
        sess = self._session()
        try:
            resp = sess.post(url, headers=headers, data=data, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.error("Vinted OAuth request failed: %s", exc)
            raise VintedAPIError(f"Network error: {exc}") from exc

        if resp.status_code == 401:
            raise VintedAuthError("Refresh token invalid or expired (401)")
        if not resp.ok:
            logger.warning("Vinted OAuth non-200: %s — %s", resp.status_code, resp.text[:200])
            raise VintedAPIError(f"OAuth error {resp.status_code}")

        try:
            payload = resp.json()
        except Exception:
            raise VintedAPIError("OAuth response is not JSON")

        token = payload.get("access_token")
        if not token:
            raise VintedAuthError("No access_token in OAuth response")
        return token

    def _get(
        self,
        refresh_token: str,
        domain: str,
        path: str,
        params: dict | None = None,
    ) -> dict:
        """Authenticated GET. Exchanges refresh_token, then calls the endpoint."""
        access_token = self._get_access_token(refresh_token, domain)
        url = f"{self._base_url(domain)}/api/v2{path}"
        headers = {
            **_VINTED_HEADERS,
            "Authorization": f"Bearer {access_token}",
        }
        sess = self._session()
        try:
            resp = sess.get(url, headers=headers, params=params or {}, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.error("Vinted API GET %s failed: %s", path, exc)
            raise VintedAPIError(f"Network error: {exc}") from exc

        if resp.status_code == 401:
            raise VintedAuthError("Access token rejected by Vinted API (401)")
        if not resp.ok:
            logger.warning("Vinted API %s → %s", path, resp.status_code)
            raise VintedAPIError(f"API error {resp.status_code} on {path}")

        try:
            return resp.json()
        except Exception:
            raise VintedAPIError(f"Non-JSON response from {path}")

    # ─────────────────────────────────────────
    # Public methods
    # ─────────────────────────────────────────

    def get_user_profile(self, refresh_token: str, domain: str = "fr") -> dict:
        """Fetch the authenticated user's profile."""
        try:
            data = self._get(refresh_token, domain, "/users/current")
            user = data.get("user", data)
            return {
                "id": str(user.get("id", "")),
                "username": user.get("login", ""),
                "real_name": user.get("real_name", ""),
                "photo_url": ((user.get("photo") or {}).get("full_size_url")
                              or (user.get("photo") or {}).get("url", "")),
                "item_count": user.get("given_item_count", 0),
                "positive_feedback_count": user.get("positive_feedback_count", 0),
                "negative_feedback_count": user.get("negative_feedback_count", 0),
                "followers_count": user.get("followers_count", 0),
                "following_count": user.get("following_count", 0),
                "is_online": user.get("is_online", False),
                "city": user.get("city", ""),
                "country": user.get("country_title", ""),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_wallet(self, refresh_token: str, domain: str = "fr") -> dict:
        """Fetch the authenticated user's wallet balance."""
        try:
            data = self._get(refresh_token, domain, "/wallet/invoices/current")
            invoice = data.get("invoice", data)
            balance = invoice.get("balance", invoice)
            return {
                "available": balance.get("available_for_payout", balance.get("amount", 0)),
                "reserved": balance.get("reserved", balance.get("blocked_funds", 0)),
                "pending": balance.get("pending", 0),
                "currency": balance.get("currency", "EUR"),
                "raw": invoice,
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_conversations(
        self,
        refresh_token: str,
        vinted_user_id: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch inbox conversations (msg_threads)."""
        try:
            data = self._get(
                refresh_token, domain,
                f"/users/{vinted_user_id}/msg_threads",
                params={"page": page, "per_page": per_page},
            )
            threads = data.get("msg_threads", [])
            return {
                "conversations": [
                    {
                        "id": t.get("id"),
                        "with_user": {
                            "id": (t.get("opposite_user") or {}).get("id"),
                            "username": (t.get("opposite_user") or {}).get("login", ""),
                            "photo_url": ((t.get("opposite_user") or {}).get("photo") or {}).get(
                                "full_size_url",
                                ((t.get("opposite_user") or {}).get("photo") or {}).get("url", ""),
                            ),
                        },
                        "last_message": (t.get("messages") or [{}])[-1].get("entity", {}).get("body", ""),
                        "unread_count": t.get("unread_message_count", t.get("msg_count", 0)),
                        "updated_at": t.get("updated_at", ""),
                        "item": {
                            "id": (t.get("item") or {}).get("id"),
                            "title": (t.get("item") or {}).get("title", ""),
                            "price": (t.get("item") or {}).get("price", ""),
                            "photo_url": (
                                ((t.get("item") or {}).get("photos") or [{}])[0].get("url", "")
                            ),
                        },
                    }
                    for t in threads
                ],
                "pagination": data.get("pagination", {}),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_favorites(
        self,
        refresh_token: str,
        vinted_user_id: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch the user's favorited items."""
        try:
            data = self._get(
                refresh_token, domain,
                f"/users/{vinted_user_id}/items/favourites",
                params={"page": page, "per_page": per_page},
            )
            items = data.get("items", data.get("favourite_items", []))
            return {
                "items": [_format_item(i) for i in items],
                "pagination": data.get("pagination", {}),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_user_items(
        self,
        refresh_token: str,
        vinted_user_id: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch items listed for sale by the user."""
        try:
            data = self._get(
                refresh_token, domain,
                f"/users/{vinted_user_id}/items",
                params={"page": page, "per_page": per_page},
            )
            items = data.get("items", [])
            return {
                "items": [_format_item(i) for i in items],
                "pagination": data.get("pagination", {}),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_purchases(
        self,
        refresh_token: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch purchase orders (items bought by the user)."""
        try:
            data = self._get(
                refresh_token, domain,
                "/my_orders/as_buyer",
                params={"page": page, "per_page": per_page},
            )
            orders = data.get("orders", [])
            return {
                "orders": [_format_order(o) for o in orders],
                "pagination": data.get("pagination", {}),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_sales(
        self,
        refresh_token: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch sale orders (items sold by the user)."""
        try:
            data = self._get(
                refresh_token, domain,
                "/my_orders/as_seller",
                params={"page": page, "per_page": per_page},
            )
            orders = data.get("orders", [])
            return {
                "orders": [_format_order(o) for o in orders],
                "pagination": data.get("pagination", {}),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_transaction_detail(
        self,
        refresh_token: str,
        transaction_id: str,
        domain: str = "fr",
    ) -> dict:
        """Fetch detailed info for a single transaction/order."""
        try:
            data = self._get(refresh_token, domain, f"/my_orders/{transaction_id}")
            order = data.get("order", data)
            return _format_order(order)
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_shipment_journey(
        self,
        refresh_token: str,
        transaction_id: str,
        domain: str = "fr",
    ) -> dict:
        """Fetch shipment tracking events for an order."""
        try:
            data = self._get(
                refresh_token, domain,
                f"/my_orders/{transaction_id}/tracking_events",
            )
            events = data.get("tracking_events", data.get("events", []))
            return {
                "tracking_number": data.get("tracking_code", data.get("tracking_number", "")),
                "carrier": data.get("carrier", ""),
                "pickup_point": data.get("pickup_point", {}),
                "events": [
                    {
                        "status": e.get("status", ""),
                        "description": e.get("description", e.get("message", "")),
                        "occurred_at": e.get("occurred_at", e.get("timestamp", "")),
                        "location": e.get("location", ""),
                    }
                    for e in events
                ],
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_notifications(
        self,
        refresh_token: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch the Vinted notification feed."""
        try:
            data = self._get(
                refresh_token, domain,
                "/notifications",
                params={"page": page, "per_page": per_page},
            )
            notifs = data.get("notifications", [])
            unread = data.get("unread_count", 0)
            return {
                "notifications": [
                    {
                        "id": n.get("id"),
                        "type": n.get("type", ""),
                        "title": n.get("title", ""),
                        "body": n.get("body", n.get("message", "")),
                        "is_read": n.get("is_read", n.get("read", False)),
                        "created_at": n.get("created_at", ""),
                    }
                    for n in notifs
                ],
                "unread_count": unread,
                "pagination": data.get("pagination", {}),
            }
        except VintedAuthError as exc:
            return {"error": str(exc), "code": "AUTH_ERROR"}
        except VintedAPIError as exc:
            return {"error": str(exc), "code": "API_ERROR"}

    def get_transactions(
        self,
        refresh_token: str,
        domain: str = "fr",
        page: int = 1,
        per_page: int = 20,
    ) -> dict:
        """Fetch transaction messages (legacy alias, proxies to get_sales)."""
        return self.get_sales(refresh_token, domain=domain, page=page, per_page=per_page)


# ─────────────────────────────────────────
# Shared formatters
# ─────────────────────────────────────────

def _format_order(order: dict) -> dict:
    """Normalize an order/transaction dict (handles as_buyer and as_seller shapes)."""
    item = order.get("item") or {}
    photos = item.get("photos") or []
    photo_url = ""
    if photos:
        photo_url = photos[0].get("full_size_url") or photos[0].get("url", "")
    buyer = order.get("buyer") or {}
    seller = order.get("seller") or {}
    return {
        "id": order.get("id"),
        "status": order.get("status", ""),
        "amount": order.get("total_item_price", order.get("amount", "")),
        "currency": order.get("currency", "EUR"),
        "created_at": order.get("created_at", ""),
        "updated_at": order.get("updated_at", ""),
        "item": {
            "id": item.get("id"),
            "title": item.get("title", ""),
            "price": item.get("price", ""),
            "photo_url": photo_url,
        },
        "buyer_login": buyer.get("login", ""),
        "seller_login": seller.get("login", ""),
        "shipment": {
            "tracking_code": order.get("shipment_tracking_code", ""),
            "carrier": order.get("shipment_type", ""),
        },
    }


def _format_item(item: dict) -> dict:
    photos = item.get("photos") or []
    if photos:
        photo_url = photos[0].get("full_size_url") or photos[0].get("url", "")
    else:
        photo_url = ""
    return {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "price": item.get("price", ""),
        "currency": item.get("currency", "EUR"),
        "status": item.get("status", ""),
        "brand_title": item.get("brand_title", ""),
        "size_title": item.get("size_title", ""),
        "photo_url": photo_url,
        "url": item.get("url", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


# Singleton
vinted_api = VintedAPI()
