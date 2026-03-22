"""Kalshi authenticated REST client.

Handles PSS-RSA request signing and all Kalshi Trade API v2 calls.
Implements the interface that ExecutionAdapter expects (create_order,
post_order, cancel, cancel_all, get_orders, get_positions) plus
market-data helpers (get_orderbook, get_markets, get_fills).

Requires: ``cryptography`` and ``requests``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


try:
    import requests as _requests
except ModuleNotFoundError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as _padding
except ModuleNotFoundError:  # pragma: no cover
    hashes = None  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    _padding = None  # type: ignore[assignment]


DEMO_BASE_URL = "https://demo-api.kalshi.co"
LIVE_BASE_URL = "https://api.kalshi.com"


@dataclass(frozen=True)
class KalshiOrderArgs:
    """Order arguments in our generic format (pre-translation)."""
    token_id: str
    price: float
    size: float
    side: str  # "BUY" or "SELL"


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------

def load_private_key_from_path(path: str) -> Any:
    """Load a PEM-encoded RSA private key from disk."""
    if serialization is None:
        raise RuntimeError("cryptography package required: pip install cryptography")
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def load_private_key_from_string(pem_text: str) -> Any:
    """Load a PEM-encoded RSA private key from a string."""
    if serialization is None:
        raise RuntimeError("cryptography package required: pip install cryptography")
    return serialization.load_pem_private_key(pem_text.encode("utf-8"), password=None)


def sign_pss(private_key: Any, message: str) -> str:
    """Produce a base64-encoded PSS-RSA signature of *message*."""
    if _padding is None or hashes is None:
        raise RuntimeError("cryptography package required: pip install cryptography")
    import base64
    signature = private_key.sign(
        message.encode("utf-8"),
        _padding.PSS(
            mgf=_padding.MGF1(hashes.SHA256()),
            salt_length=_padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class KalshiClient:
    """Authenticated Kalshi REST client.

    Parameters:
        api_key_id: Public API key ID from Kalshi dashboard.
        private_key: RSA private key object (from ``load_private_key_*``).
        base_url: ``https://demo-api.kalshi.co`` or ``https://api.kalshi.com``.
    """

    def __init__(
        self,
        api_key_id: str,
        private_key: Any,
        base_url: str = DEMO_BASE_URL,
    ) -> None:
        if _requests is None:
            raise RuntimeError("requests package required: pip install requests")
        self._api_key_id = str(api_key_id)
        self._private_key = private_key
        self._base_url = str(base_url).rstrip("/")
        self._session = _requests.Session()

    # -- Auth headers -------------------------------------------------------

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        path_no_qs = path.split("?")[0]
        message = timestamp + method.upper() + path_no_qs
        sig = sign_pss(self._private_key, message)
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Any:
        headers = self._auth_headers(method, path) if auth else {"Content-Type": "application/json"}
        url = self._base_url + path
        resp = self._session.request(
            method, url, headers=headers, params=params, json=json_body, timeout=10,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- Market Data (public) -----------------------------------------------

    def get_markets(
        self,
        *,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """GET /trade-api/v2/markets → list of market dicts.

        Returns a single page of results.  Use ``get_markets_all`` for
        automatic cursor-based pagination across all pages.
        """
        params: Dict[str, Any] = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        data = self._request("GET", "/trade-api/v2/markets", params=params, auth=True)
        return data.get("markets") or []

    def get_markets_all(
        self,
        *,
        status: str = "open",
        limit: int = 200,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        max_pages: int = 10,
    ) -> List[Dict[str, Any]]:
        """Paginate through all markets using cursor-based pagination."""
        all_markets: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params: Dict[str, Any] = {"limit": limit, "status": status}
            if cursor:
                params["cursor"] = cursor
            if series_ticker:
                params["series_ticker"] = series_ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            data = self._request("GET", "/trade-api/v2/markets", params=params, auth=True)
            page = data.get("markets") or []
            all_markets.extend(page)
            cursor = data.get("cursor")
            if not cursor or not page:
                break
            import time as _time
            _time.sleep(0.05)
        return all_markets

    def get_market(self, ticker: str) -> Dict[str, Any]:
        """GET /trade-api/v2/markets/{ticker} → single market dict."""
        data = self._request("GET", f"/trade-api/v2/markets/{ticker}", auth=True)
        return data.get("market") or data

    def get_events(self, *, status: str = "open", limit: int = 100) -> List[Dict[str, Any]]:
        """GET /trade-api/v2/events → list of event dicts."""
        params: Dict[str, Any] = {"limit": limit, "status": status}
        data = self._request("GET", "/trade-api/v2/events", params=params, auth=True)
        return data.get("events") or []

    def get_orderbook(self, ticker: str, *, depth: int = 20) -> Dict[str, Any]:
        """GET /trade-api/v2/markets/{ticker}/orderbook → {yes: [...], no: [...]}."""
        params: Dict[str, Any] = {"depth": depth}
        return self._request("GET", f"/trade-api/v2/markets/{ticker}/orderbook", params=params, auth=True)

    def get_trades(self, *, ticker: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """GET /trade-api/v2/trades → list of public trades."""
        params: Dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/trade-api/v2/trades", params=params, auth=True)
        return data.get("trades") or []

    # -- Order Management (authenticated) -----------------------------------

    def create_order(self, order_args: KalshiOrderArgs) -> Dict[str, Any]:
        """Translate generic order args to Kalshi payload. Does NOT post yet.

        This mirrors the Polymarket pattern where ``create_order`` prepares
        a signed order object and ``post_order`` actually submits it.
        For Kalshi there is no separate signing step, so we just build the
        payload dict here.
        """
        ticker, is_yes = _parse_virtual_token(order_args.token_id)
        action, yes_price_cents = _translate_order(
            is_yes=is_yes,
            side=order_args.side,
            price=order_args.price,
        )
        return {
            "ticker": ticker,
            "action": action,
            "side": "yes",
            "count": max(1, int(order_args.size)),
            "type": "limit",
            "yes_price": yes_price_cents,
            "client_order_id": str(uuid.uuid4()),
        }

    def post_order(self, order_payload: Dict[str, Any], tif: Any = None) -> Dict[str, Any]:
        """POST /trade-api/v2/portfolio/orders → place the order."""
        data = self._request("POST", "/trade-api/v2/portfolio/orders", json_body=order_payload)
        return data.get("order") or data

    def cancel(self, *, order_id: str) -> Dict[str, Any]:
        """DELETE /trade-api/v2/portfolio/orders/{order_id}."""
        return self._request("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}")

    def cancel_all(self) -> Dict[str, Any]:
        """Cancel all open orders (cancel each individually via get_orders)."""
        orders = self.get_orders()
        results = []
        for order in orders:
            oid = order.get("order_id") or order.get("id")
            if oid:
                results.append(self.cancel(order_id=str(oid)))
        return {"cancelled": len(results)}

    def get_orders(self, *, status: str = "resting") -> List[Dict[str, Any]]:
        """GET /trade-api/v2/portfolio/orders → list of open orders."""
        params: Dict[str, Any] = {"status": status}
        data = self._request("GET", "/trade-api/v2/portfolio/orders", params=params)
        return data.get("orders") or []

    def get_positions(self) -> List[Dict[str, Any]]:
        """GET /trade-api/v2/portfolio/positions → list of positions."""
        data = self._request("GET", "/trade-api/v2/portfolio/positions")
        return data.get("market_positions") or data.get("positions") or []

    # -- Fills --------------------------------------------------------------

    def get_fills(self, *, ticker: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """GET /trade-api/v2/portfolio/fills → list of fills."""
        params: Dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/trade-api/v2/portfolio/fills", params=params)
        return data.get("fills") or []


# ---------------------------------------------------------------------------
# Virtual token ID helpers
# ---------------------------------------------------------------------------

def _parse_virtual_token(token_id: str) -> tuple[str, bool]:
    """Parse ``'{ticker}:yes'`` or ``'{ticker}:no'`` → (ticker, is_yes)."""
    parts = str(token_id).rsplit(":", 1)
    if len(parts) == 2 and parts[1].lower() in ("yes", "no"):
        return parts[0], parts[1].lower() == "yes"
    # Bare ticker → assume YES
    return str(token_id), True


def _translate_order(*, is_yes: bool, side: str, price: float) -> tuple[str, int]:
    """Convert our generic (is_yes, side, price) to Kalshi (action, yes_price_cents).

    Kalshi convention:
    - ``action="buy", side="yes"``  → buying YES contracts
    - ``action="sell", side="yes"`` → selling YES contracts (= buying NO)

    Our convention:
    - token ``{ticker}:yes``, side ``BUY`` → buy YES   → action="buy",  yes_price = price * 100
    - token ``{ticker}:yes``, side ``SELL`` → sell YES  → action="sell", yes_price = price * 100
    - token ``{ticker}:no``,  side ``BUY`` → buy NO    → action="sell", yes_price = (1-price) * 100
    - token ``{ticker}:no``,  side ``SELL`` → sell NO   → action="buy",  yes_price = (1-price) * 100
    """
    side_upper = str(side).upper()
    if is_yes:
        action = "buy" if side_upper == "BUY" else "sell"
        yes_price_cents = max(1, min(99, round(price * 100)))
    else:
        # NO token: buying NO = selling YES, price flips
        action = "sell" if side_upper == "BUY" else "buy"
        yes_price_cents = max(1, min(99, round((1.0 - price) * 100)))
    return action, yes_price_cents
