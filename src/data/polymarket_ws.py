from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from src.book.order_book import OrderBook


@dataclass(frozen=True)
class L2Update:
    price: float
    size: float


class PolymarketWS:
    def __init__(self, book: OrderBook) -> None:
        self.book = book

    def apply_snapshot(
        self,
        bids: Iterable[Tuple[float, float]],
        asks: Iterable[Tuple[float, float]],
        ts: int,
    ) -> None:
        self.book.set_snapshot(list(bids), list(asks), ts)

    def apply_delta(
        self,
        bid_updates: Iterable[L2Update],
        ask_updates: Iterable[L2Update],
        ts: int,
    ) -> None:
        self.book.update_l2(list(bid_updates), list(ask_updates), ts)
