"""Kalshi order book feed — polls REST API and feeds BookManager.

Kalshi orderbook format:
    {"yes": [[price_cents, qty], ...], "no": [[price_cents, qty], ...]}

We convert to BookManager's format using virtual token IDs:
    ``{ticker}:yes`` — bids are YES buy orders, asks derived from NO buys
    ``{ticker}:no``  — bids are NO buy orders, asks derived from YES buys

Prices are converted from cents (0-100) to dollars (0.00-1.00) at the
feed boundary so the rest of the bot sees consistent dollar prices.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from core_mm.book_manager import BookManager

Level = Tuple[float, float]


@dataclass(frozen=True)
class KalshiFeedStatus:
    connected: bool
    subscribed_tickers: tuple[str, ...]
    poll_count: int
    applied_snapshots: int


class KalshiMarketFeed:
    """Polls Kalshi orderbook REST endpoint and feeds into BookManager.

    Parameters:
        client: ``KalshiClient`` instance for API calls.
        book_manager: Shared ``BookManager`` the bot reads from.
        tickers: Initial list of Kalshi tickers to track.
        poll_interval_secs: Seconds between orderbook polls (default 1.0).
            Kalshi rate limit is 20 reads/s — with 5 tickers at 1s interval
            that's 5 req/s, well within limits.
        on_applied_update: Optional callback fired after each snapshot apply.
        depth: Number of levels to request (default 20).
    """

    def __init__(
        self,
        *,
        client: object,
        book_manager: BookManager,
        tickers: Sequence[str] = (),
        poll_interval_secs: float = 1.0,
        on_applied_update: Optional[Callable[[], None]] = None,
        depth: int = 20,
    ) -> None:
        self._client = client
        self._book_manager = book_manager
        self._on_applied_update = on_applied_update
        self._depth = int(depth)
        self._desired_tickers = tuple(sorted({str(t) for t in tickers if t}))
        self._active_tickers: tuple[str, ...] = ()
        self._poll_interval = max(0.1, float(poll_interval_secs))
        self._stop_event = asyncio.Event()
        self._poll_count = 0
        self._applied_snapshots = 0

    def set_token_ids(self, token_ids: Sequence[str]) -> bool:
        """Accept virtual token IDs (``{ticker}:yes``, ``{ticker}:no``)
        and extract unique tickers."""
        tickers: set[str] = set()
        for tid in token_ids:
            parts = str(tid).rsplit(":", 1)
            tickers.add(parts[0])
        normalized = tuple(sorted(tickers))
        if normalized == self._desired_tickers:
            return False
        self._desired_tickers = normalized
        return True

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> KalshiFeedStatus:
        return KalshiFeedStatus(
            connected=bool(self._active_tickers),
            subscribed_tickers=self._active_tickers,
            poll_count=self._poll_count,
            applied_snapshots=self._applied_snapshots,
        )

    async def run(self) -> None:
        """Main poll loop — runs until ``stop()`` is called."""
        while not self._stop_event.is_set():
            if not self._desired_tickers:
                await asyncio.sleep(0.25)
                continue
            self._active_tickers = self._desired_tickers
            for ticker in self._active_tickers:
                if self._stop_event.is_set():
                    break
                try:
                    ob = self._client.get_orderbook(ticker, depth=self._depth)
                    self._poll_count += 1
                    applied = _apply_kalshi_book(self._book_manager, ticker, ob)
                    self._applied_snapshots += applied
                    if applied > 0 and self._on_applied_update is not None:
                        self._on_applied_update()
                except Exception:
                    pass  # Transient errors — retry next cycle
            await asyncio.sleep(self._poll_interval)


def _apply_kalshi_book(book_manager: BookManager, ticker: str, ob: object) -> int:
    """Convert Kalshi orderbook to BookManager snapshots.

    Kalshi book: ``{"yes": [[price_cents, qty], ...], "no": [[price_cents, qty], ...]}``.

    We create two virtual books:
    - ``{ticker}:yes`` — YES token perspective:
        - bids = YES buy orders (from ``yes`` side)
        - asks = NO buy orders flipped (buy NO at X → sell YES at 1-X)
    - ``{ticker}:no`` — NO token perspective:
        - bids = NO buy orders (from ``no`` side)
        - asks = YES buy orders flipped (buy YES at X → sell NO at 1-X)
    """
    if not isinstance(ob, dict):
        return 0

    # Handle both formats:
    # Legacy/test: {"yes": [[cents, qty], ...], "no": [...]}
    # Live API:    {"orderbook_fp": {"yes_dollars": [["0.45", "100"], ...], "no_dollars": [...]}}
    inner = ob.get("orderbook_fp") or ob.get("orderbook") or ob
    yes_dollars = inner.get("yes_dollars")
    no_dollars = inner.get("no_dollars")

    if yes_dollars is not None or no_dollars is not None:
        # Dollar-string format from live API: [["0.45", "100.00"], ...]
        yes_parsed = _parse_dollar_levels(yes_dollars or [])
        no_parsed = _parse_dollar_levels(no_dollars or [])
    else:
        # Cent format from tests: [[45, 100], ...]
        yes_levels = inner.get("yes") or []
        no_levels = inner.get("no") or []
        yes_parsed = _parse_cent_levels(yes_levels)
        no_parsed = _parse_cent_levels(no_levels)

    applied = 0

    # YES token book
    yes_bids = yes_parsed  # People buying YES → bids in YES market
    yes_asks = _flip_levels(no_parsed)  # People buying NO → asks in YES market
    if yes_bids or yes_asks:
        book_manager.apply_snapshot(f"{ticker}:yes", bids=yes_bids, asks=yes_asks)
        applied += 1

    # NO token book
    no_bids = no_parsed  # People buying NO → bids in NO market
    no_asks = _flip_levels(yes_parsed)  # People buying YES → asks in NO market
    if no_bids or no_asks:
        book_manager.apply_snapshot(f"{ticker}:no", bids=no_bids, asks=no_asks)
        applied += 1

    return applied


def _parse_cent_levels(raw: object) -> List[Level]:
    """Parse [[price_cents, qty], ...] → [(price_dollars, qty), ...]."""
    if not isinstance(raw, (list, tuple)):
        return []
    parsed: List[Level] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            price_cents = float(item[0])
            qty = float(item[1])
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price_cents <= 0:
            continue
        parsed.append((price_cents / 100.0, qty))
    return parsed


def _parse_dollar_levels(raw: object) -> List[Level]:
    """Parse [["0.45", "100.00"], ...] → [(0.45, 100.0), ...].

    Kalshi's live API returns ``orderbook_fp.yes_dollars`` and
    ``orderbook_fp.no_dollars`` as lists of string pairs already in
    dollar denomination.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    parsed: List[Level] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            price = float(item[0])
            qty = float(item[1])
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        parsed.append((price, qty))
    return parsed


def _flip_levels(levels: List[Level]) -> List[Level]:
    """Flip price levels: (p, qty) → (1-p, qty).

    Used to derive the ask side from the counterpart's bids.
    Buy NO at 0.40 = Sell YES at 0.60.
    """
    flipped: List[Level] = []
    for price, qty in levels:
        complement = round(1.0 - price, 4)
        if 0.0 < complement < 1.0:
            flipped.append((complement, qty))
    return flipped
