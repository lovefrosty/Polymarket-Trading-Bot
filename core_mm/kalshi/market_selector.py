"""Kalshi market discovery — finds tradable markets via Kalshi REST API.

Returns ``MarketCandidate`` objects compatible with ``CoreMMRunner``.
Uses the same dataclass as the Polymarket selector so that all
downstream code (runner, main_loop, telemetry) works unchanged.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from core_mm.market_selector import MarketCandidate


@dataclass(frozen=True)
class KalshiSelectorConfig:
    """Filter/sort configuration for Kalshi market discovery."""
    # Series or event filter (e.g., "KXBTC" for Bitcoin markets)
    series_ticker: Optional[str] = None
    event_ticker: Optional[str] = None
    # Quality gates
    min_volume_24hr: float = 0.0
    min_open_interest: float = 0.0
    # Price filter (avoid extreme outcomes)
    min_price: float = 0.10
    max_price: float = 0.90
    # Max markets to return
    max_results: int = 10
    # Exclude near-expiry (seconds)
    min_time_to_expiry_secs: int = 120


class KalshiMarketSelector:
    """Discovers tradable Kalshi markets.

    Parameters:
        client: ``KalshiClient`` instance.
        config: Optional ``KalshiSelectorConfig`` for filtering.
    """

    def __init__(
        self,
        *,
        client: object,
        config: Optional[KalshiSelectorConfig] = None,
    ) -> None:
        self._client = client
        self.config = config or KalshiSelectorConfig()

    # Map user-facing symbols to Kalshi series tickers
    SYMBOL_TO_SERIES: Dict[str, str] = {
        "BTC": "KXBTC",
        "ETH": "KXETH",
        "SOL": "KXSOL",
        "SPX": "KXINX",
        "GDP": "KXGDP",
        "FED": "KXFEDRATE",
        "NASDAQ": "KXNDX",
    }

    def select_markets(self, now_ts: Optional[int] = None) -> List[MarketCandidate]:
        """Fetch open markets from Kalshi and return scored candidates."""
        now_ts = now_ts or int(time.time())
        # Translate user symbol (e.g. "BTC") to Kalshi series (e.g. "KXBTC")
        series = self.config.series_ticker
        if series and series in self.SYMBOL_TO_SERIES:
            series = self.SYMBOL_TO_SERIES[series]

        # Use paginated fetch to get more markets
        fetch = getattr(self._client, "get_markets_all", None) or self._client.get_markets
        raw_markets = fetch(
            status="open",
            limit=200,
            series_ticker=series,
            event_ticker=self.config.event_ticker,
        )
        # Fallback: try Kalshi prefix, then unfiltered
        if not raw_markets and series:
            raw_markets = fetch(status="open", limit=200, series_ticker=series)
        if not raw_markets and self.config.series_ticker:
            raw_markets = fetch(status="open", limit=200)
        candidates: List[MarketCandidate] = []
        for m in raw_markets:
            candidate = _to_candidate(m, self.config, now_ts)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: self.config.max_results]

    def select_from_events(
        self,
        events: Any = None,
        now_ts: Optional[int] = None,
    ) -> List[MarketCandidate]:
        """Compatibility shim — Kalshi doesn't need pre-fetched events."""
        return self.select_markets(now_ts=now_ts)


def _to_candidate(
    m: Dict[str, Any],
    config: KalshiSelectorConfig,
    now_ts: int,
) -> Optional[MarketCandidate]:
    """Convert a Kalshi market dict to a MarketCandidate, or None if filtered."""
    ticker = str(m.get("ticker") or "")
    if not ticker:
        return None

    # Price checks (last YES price or yes_bid/yes_ask)
    yes_price = _coerce_float(m.get("last_price") or m.get("yes_bid"))
    if yes_price is not None:
        yes_price_dollars = yes_price / 100.0 if yes_price > 1.0 else yes_price
    else:
        yes_price_dollars = None

    if yes_price_dollars is not None:
        if yes_price_dollars < config.min_price or yes_price_dollars > config.max_price:
            return None

    # Expiry check
    expiration_ts = _parse_ts(m.get("expiration_time") or m.get("close_time"))
    end_ts_ms = int(expiration_ts * 1000) if expiration_ts else None
    if expiration_ts and (expiration_ts - now_ts) < config.min_time_to_expiry_secs:
        return None

    # Volume / open interest
    volume = _coerce_float(m.get("volume") or m.get("volume_24h") or 0.0)
    open_interest = _coerce_float(m.get("open_interest") or 0.0)
    if volume < config.min_volume_24hr:
        return None
    if open_interest < config.min_open_interest:
        return None

    # Status
    status = str(m.get("status") or "").lower()
    result = str(m.get("result") or "").lower()
    is_active = status in ("open", "active", "trading") and result == ""

    # Score: simple volume-based for now
    score = float(volume) + float(open_interest) * 0.5

    # Build virtual token IDs
    token_ids = (f"{ticker}:yes", f"{ticker}:no")

    # Tick size: Kalshi standard is 1 cent = 0.01 dollars
    tick_size = _coerce_float(m.get("tick_size")) or 0.01
    if tick_size >= 1.0:
        tick_size = tick_size / 100.0  # cents → dollars

    # Spread from yes_bid/yes_ask if available
    yes_bid = _coerce_float(m.get("yes_bid"))
    yes_ask = _coerce_float(m.get("yes_ask"))
    spread = 0.0
    mid = yes_price_dollars
    if yes_bid is not None and yes_ask is not None:
        bid_d = yes_bid / 100.0 if yes_bid > 1.0 else yes_bid
        ask_d = yes_ask / 100.0 if yes_ask > 1.0 else yes_ask
        spread = max(0.0, ask_d - bid_d)
        mid = (bid_d + ask_d) / 2.0 if bid_d > 0 and ask_d > 0 else mid

    # Subtitle / outcomes
    yes_sub = str(m.get("yes_sub_title") or "Yes")
    no_sub = str(m.get("no_sub_title") or "No")

    # Extract reference symbol from title or series
    title = str(m.get("title") or m.get("event_ticker") or ticker)
    ref_symbol = _extract_symbol(title, ticker)

    return MarketCandidate(
        reference_symbol=ref_symbol,
        slug=ticker,
        condition_id=ticker,
        token_ids=token_ids,
        outcomes=(yes_sub, no_sub),
        reward_per_100=0.0,  # Kalshi doesn't have liquidity rewards
        volatility_sum=0.0,
        spread=spread,
        mid_price=mid,
        active=is_active,
        closed=not is_active,
        accepting_orders=is_active,
        tick_size=tick_size,
        max_incentive_spread=None,
        min_incentive_size=None,
        end_ts_ms=end_ts_ms,
        end_ts_source="kalshi_expiration_time",
        active_now=is_active,
        tradable=is_active,
        clob_candidate=True,
        score=score,
        raw=dict(m),
    )


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> Optional[float]:
    """Parse ISO timestamp or unix timestamp to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    try:
        from datetime import datetime, timezone
        # Try ISO format
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt.timestamp()
    except Exception:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_symbol(title: str, ticker: str) -> str:
    """Best-effort extraction of underlying symbol from market title/ticker."""
    upper = (title + " " + ticker).upper()
    for sym in ("BTC", "ETH", "SOL", "XRP", "SPX", "AAPL", "TSLA"):
        if sym in upper:
            return sym
    # Fall back to first part of ticker before hyphen
    parts = ticker.split("-")
    return parts[0] if parts else ticker
