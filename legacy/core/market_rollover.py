from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from core.market_discovery import ResolvedMarket
from core.market_time import window_start_end_ms


@dataclass(frozen=True)
class MarketState:
    market_slug: Optional[str]
    condition_id: Optional[str]
    token_ids: List[str]
    market_end_ts_ms: Optional[int]
    market_end_source: str
    selection_key: Optional[str] = None


@dataclass(frozen=True)
class MarketRolloverConfig:
    prefetch_ms: int = 90_000
    stale_ms: int = 15_000
    discovery_period_ms: int = 30_000
    grace_ms: int = 60_000


class MarketRolloverManager:
    def __init__(self, current: MarketState, config: Optional[MarketRolloverConfig] = None) -> None:
        self.current = current
        self.config = config or MarketRolloverConfig()
        self.last_discovery_ts_ms: int = 0
        self.rollover_count: int = 0

    def evaluate_reasons(
        self,
        now_ms: int,
        last_book_recv_wall_ms: Optional[int],
        market_closed: bool = False,
    ) -> List[str]:
        reasons: List[str] = []
        if self.current.market_end_ts_ms is not None and now_ms >= (self.current.market_end_ts_ms - self.config.prefetch_ms):
            reasons.append("TIME_WINDOW_END")
        if last_book_recv_wall_ms is None or (now_ms - int(last_book_recv_wall_ms) >= self.config.stale_ms):
            reasons.append("NO_MESSAGES_STALE")
        if market_closed:
            reasons.append("MARKET_CLOSED")
        return sorted(set(reasons))

    def should_attempt_discovery(self, now_ms: int, reasons: Iterable[str]) -> bool:
        reason_set = set(reasons)
        if not reason_set:
            return False
        return (now_ms - self.last_discovery_ts_ms) >= int(self.config.discovery_period_ms)

    def mark_discovery_attempt(self, now_ms: int) -> None:
        self.last_discovery_ts_ms = int(now_ms)

    def can_commit_switch(self, now_ms: int, trigger_reasons: Iterable[str]) -> bool:
        reasons = set(trigger_reasons)
        if "NO_MESSAGES_STALE" in reasons or "MARKET_CLOSED" in reasons:
            return True
        end_ts_ms = self.current.market_end_ts_ms
        if end_ts_ms is None:
            return True
        return now_ms >= int(end_ts_ms)

    def escape_hatch_open(self, now_ms: int) -> bool:
        end_ts_ms = self.current.market_end_ts_ms
        if end_ts_ms is None:
            return False
        grace_ms = max(0, int(self.config.grace_ms))
        return int(now_ms) >= int(end_ts_ms + grace_ms)

    def has_market_changed(self, candidate: MarketState) -> bool:
        current_tokens = sorted(str(token) for token in self.current.token_ids if token)
        candidate_tokens = sorted(str(token) for token in candidate.token_ids if token)
        if (candidate.market_slug or "") != (self.current.market_slug or ""):
            return True
        if (candidate.condition_id or "") != (self.current.condition_id or ""):
            return True
        return candidate_tokens != current_tokens

    def commit(self, candidate: MarketState) -> MarketState:
        previous = self.current
        self.current = candidate
        self.rollover_count += 1
        return previous


def market_state_from_resolved(
    market: ResolvedMarket,
    asset_meta: Dict[str, Dict[str, Any]],
) -> MarketState:
    token_ids = [str(token) for token in market.token_ids if token]
    end_ts_ms: Optional[int] = None
    end_source = "unknown"

    for token_id in token_ids:
        token_meta = asset_meta.get(token_id) or {}
        raw_end = token_meta.get("end_ts_ms")
        try:
            if raw_end is not None:
                parsed = int(raw_end)
                if parsed > 0:
                    end_ts_ms = parsed
                    break
        except (TypeError, ValueError):
            continue
    if end_ts_ms is not None:
        end_source = "metadata"
    else:
        window = window_start_end_ms(market.slug or "")
        if window is not None:
            _, parsed_end = window
            end_ts_ms = int(parsed_end)
            end_source = "slug_fallback"

    return MarketState(
        market_slug=market.slug,
        condition_id=market.condition_id,
        token_ids=token_ids,
        market_end_ts_ms=end_ts_ms,
        market_end_source=end_source,
        selection_key=_selection_key_from_asset_meta(asset_meta, token_ids),
    )


def _selection_key_from_asset_meta(asset_meta: Dict[str, Dict[str, Any]], token_ids: List[str]) -> Optional[str]:
    for token_id in token_ids:
        token_meta = asset_meta.get(token_id) or {}
        value = token_meta.get("selection_key")
        if value:
            return str(value)
    return None
