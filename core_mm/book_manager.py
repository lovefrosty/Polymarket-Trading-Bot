from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sortedcontainers import SortedDict


Level = Tuple[float, float]


@dataclass(frozen=True)
class BookView:
    token_id: str
    bids: Tuple[Level, ...]
    asks: Tuple[Level, ...]
    best_bid: Optional[float]
    best_ask: Optional[float]
    best_bid_size: float
    best_ask_size: float
    mid_price: Optional[float]
    last_update_ms: Optional[int]


@dataclass
class _BookState:
    bids: SortedDict
    asks: SortedDict
    last_update_ms: Optional[int] = None


class BookManager:
    """Thread-safe per-token L2 book store for Polymarket market messages."""

    def __init__(self, stale_after_ms: int = 30_000) -> None:
        self._stale_after_ms = int(stale_after_ms)
        self._books: Dict[str, _BookState] = {}
        self._lock = threading.RLock()

    def tracked_token_ids(self) -> List[str]:
        with self._lock:
            return sorted(self._books.keys())

    def process_message(self, message: Mapping[str, Any], recv_wall_ms: Optional[int] = None) -> int:
        view = _payload_view(message)
        recv_ms = int(recv_wall_ms if recv_wall_ms is not None else _now_ms())
        if _is_snapshot(view):
            token_id = _extract_asset_id(view)
            if not token_id:
                return 0
            bids, asks = _parse_snapshot_levels(view)
            self.apply_snapshot(token_id, bids=bids, asks=asks, ts_ms=_extract_event_ts_ms(view, recv_ms))
            return 1
        if _is_price_change(view):
            root_token_id = _extract_asset_id(view)
            changes = view.get("price_changes")
            if not isinstance(changes, list):
                return 0
            applied = 0
            for change in changes:
                if not isinstance(change, Mapping):
                    continue
                token_id = _extract_asset_id(change) or root_token_id
                side = _normalize_side(change.get("side"))
                if not token_id or side is None:
                    continue
                try:
                    price = float(change.get("price", change.get("p", 0.0)))
                    size = float(change.get("size", change.get("s", 0.0)))
                except (TypeError, ValueError):
                    continue
                self.apply_price_change(
                    token_id,
                    side=side,
                    price=price,
                    size=size,
                    ts_ms=_extract_event_ts_ms(change, _extract_event_ts_ms(view, recv_ms)),
                )
                applied += 1
            return applied
        return 0

    def apply_snapshot(
        self,
        token_id: str,
        bids: Iterable[Level],
        asks: Iterable[Level],
        ts_ms: Optional[int] = None,
    ) -> None:
        with self._lock:
            book = self._books.setdefault(str(token_id), _BookState(bids=SortedDict(), asks=SortedDict()))
            book.bids = SortedDict((float(price), float(size)) for price, size in bids if float(size) > 0)
            book.asks = SortedDict((float(price), float(size)) for price, size in asks if float(size) > 0)
            book.last_update_ms = int(ts_ms if ts_ms is not None else _now_ms())

    def apply_price_change(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        ts_ms: Optional[int] = None,
    ) -> None:
        token = str(token_id)
        normalized_side = _normalize_side(side)
        if normalized_side is None:
            raise ValueError(f"unsupported side: {side}")
        with self._lock:
            book = self._books.setdefault(token, _BookState(bids=SortedDict(), asks=SortedDict()))
            levels = book.bids if normalized_side == "buy" else book.asks
            level_price = float(price)
            level_size = float(size)
            if level_size <= 0:
                levels.pop(level_price, None)
            else:
                levels[level_price] = level_size
            book.last_update_ms = int(ts_ms if ts_ms is not None else _now_ms())

    def get_book(self, token_id: str) -> Optional[BookView]:
        with self._lock:
            book = self._books.get(str(token_id))
            if book is None:
                return None
            bids = tuple((float(price), float(size)) for price, size in reversed(book.bids.items()))
            asks = tuple((float(price), float(size)) for price, size in book.asks.items())
            best_bid, best_bid_size = _best_level(book.bids, reverse=True)
            best_ask, best_ask_size = _best_level(book.asks, reverse=False)
            mid = None
            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2.0
            return BookView(
                token_id=str(token_id),
                bids=bids,
                asks=asks,
                best_bid=best_bid,
                best_ask=best_ask,
                best_bid_size=best_bid_size,
                best_ask_size=best_ask_size,
                mid_price=mid,
                last_update_ms=book.last_update_ms,
            )

    def is_stale(self, token_id: str, now_ms: Optional[int] = None) -> bool:
        with self._lock:
            book = self._books.get(str(token_id))
            if book is None or book.last_update_ms is None:
                return True
            current_ms = int(now_ms if now_ms is not None else _now_ms())
            return current_ms - int(book.last_update_ms) > self._stale_after_ms

    def stale_tokens(self, now_ms: Optional[int] = None) -> List[str]:
        with self._lock:
            current_ms = int(now_ms if now_ms is not None else _now_ms())
            return sorted(
                token_id
                for token_id, book in self._books.items()
                if book.last_update_ms is None or current_ms - int(book.last_update_ms) > self._stale_after_ms
            )


def _best_level(levels: SortedDict, reverse: bool) -> Tuple[Optional[float], float]:
    if not levels:
        return None, 0.0
    index = -1 if reverse else 0
    price, size = levels.peekitem(index)
    return float(price), float(size)


def _payload_view(message: Mapping[str, Any]) -> Mapping[str, Any]:
    data = message.get("data") if isinstance(message, Mapping) else None
    return data if isinstance(data, Mapping) else message


def _extract_asset_id(message: Mapping[str, Any]) -> Optional[str]:
    view = _payload_view(message)
    for key in ("asset_id", "assetId", "token_id", "tokenId"):
        value = view.get(key)
        if value:
            return str(value)
    return None


def _is_snapshot(message: Mapping[str, Any]) -> bool:
    view = _payload_view(message)
    return any(key in view for key in ("buys", "sells", "bids", "asks"))


def _is_price_change(message: Mapping[str, Any]) -> bool:
    view = _payload_view(message)
    return view.get("event_type") == "price_change" or "price_changes" in view


def _parse_snapshot_levels(message: Mapping[str, Any]) -> Tuple[List[Level], List[Level]]:
    view = _payload_view(message)
    buys = view.get("buys") or view.get("bids") or []
    sells = view.get("sells") or view.get("asks") or []
    return _parse_levels(buys), _parse_levels(sells)


def _parse_levels(levels: Any) -> List[Level]:
    parsed: List[Level] = []
    if not isinstance(levels, Sequence) or isinstance(levels, (str, bytes)):
        return parsed
    for level in levels:
        if isinstance(level, Mapping):
            try:
                price = float(level.get("price", level.get("p", 0.0)))
                size = float(level.get("size", level.get("s", 0.0)))
            except (TypeError, ValueError):
                continue
            parsed.append((price, size))
        elif isinstance(level, Sequence) and not isinstance(level, (str, bytes)) and len(level) >= 2:
            try:
                parsed.append((float(level[0]), float(level[1])))
            except (TypeError, ValueError):
                continue
    return parsed


def _normalize_side(side: Any) -> Optional[str]:
    if side is None:
        return None
    side_value = str(side).strip().lower()
    if side_value in {"buy", "bid"}:
        return "buy"
    if side_value in {"sell", "ask"}:
        return "sell"
    return None


def _extract_event_ts_ms(message: Mapping[str, Any], fallback_ms: int) -> int:
    view = _payload_view(message)
    for key in ("t", "ts", "timestamp", "time", "t_event_ms"):
        value = view.get(key)
        if value is None:
            continue
        try:
            value_int = int(float(value))
        except (TypeError, ValueError):
            continue
        if value_int > 0:
            return value_int
    return int(fallback_ms)


def _now_ms() -> int:
    return int(time.time() * 1000)
