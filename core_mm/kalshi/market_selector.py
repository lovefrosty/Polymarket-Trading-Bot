"""Kalshi market discovery — finds tradable markets via Kalshi REST API.

Returns ``MarketCandidate`` objects compatible with ``CoreMMRunner``.
Uses the same dataclass as the Polymarket selector so that all
downstream code (runner, main_loop, telemetry) works unchanged.
"""
from __future__ import annotations

import math
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
    # Reject books that are too wide to be quoteable.
    max_spread: float = 0.10
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
        self._last_selection_report: Dict[str, Any] = {}

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

        # Use paginated fetch to get more markets.
        fetch = getattr(self._client, "get_markets_all", None) or self._client.get_markets
        fetch_attempts: List[Dict[str, Any]] = []
        raw_markets: List[Dict[str, Any]] = []
        request_params = {
            "status": "open",
            "limit": 200,
            "series_ticker": series,
            "event_ticker": self.config.event_ticker,
        }
        raw_markets = list(fetch(**request_params))
        fetch_attempts.append({"params": dict(request_params), "count": len(raw_markets)})
        # Fallback chain: series filter → unfiltered discovery.
        if not raw_markets and series:
            raw_markets = list(fetch(status="open", limit=200, series_ticker=series))
            fetch_attempts.append({"params": {"status": "open", "limit": 200, "series_ticker": series}, "count": len(raw_markets)})
        if not raw_markets and self.config.series_ticker:
            raw_markets = list(fetch(status="open", limit=200))
            fetch_attempts.append({"params": {"status": "open", "limit": 200}, "count": len(raw_markets)})

        evaluations: List[Dict[str, Any]] = []
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        candidates: List[MarketCandidate] = []
        for m in raw_markets:
            evaluation = _evaluate_market(m, self.config, now_ts)
            if evaluation is None:
                continue
            evaluations.append(evaluation)
            summary = evaluation["summary"]
            if evaluation["accepted"] and evaluation["candidate"] is not None:
                candidates.append(evaluation["candidate"])
                accepted.append(summary)
            else:
                rejected.append(summary)

        candidates.sort(key=lambda c: c.score, reverse=True)
        accepted.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        rejected.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        selected = accepted[0] if accepted else None
        self._last_selection_report = {
            "series_ticker": series,
            "event_ticker": self.config.event_ticker,
            "fetch_attempts": fetch_attempts,
            "fetched_count": len(raw_markets),
            "evaluated_count": len(evaluations),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "selected_market": selected,
            "selected_reason": (selected or {}).get("reason") if selected else None,
            "accepted_candidates": accepted[:5],
            "rejected_candidates": rejected[:5],
        }
        return candidates[: self.config.max_results]

    def select_from_events(
        self,
        events: Any = None,
        now_ts: Optional[int] = None,
    ) -> List[MarketCandidate]:
        """Compatibility shim — Kalshi doesn't need pre-fetched events."""
        return self.select_markets(now_ts=now_ts)

    @property
    def last_selection_report(self) -> Dict[str, Any]:
        return dict(self._last_selection_report)


def _to_candidate(
    m: Dict[str, Any],
    config: KalshiSelectorConfig,
    now_ts: int,
) -> Optional[MarketCandidate]:
    """Convert a Kalshi market dict to a MarketCandidate, or None if filtered."""
    evaluation = _evaluate_market(m, config, now_ts)
    if evaluation is None:
        return None
    if not evaluation["active"]:
        return evaluation["candidate"]
    if evaluation["accepted"]:
        return evaluation["candidate"]
    return None


def _evaluate_market(
    m: Dict[str, Any],
    config: KalshiSelectorConfig,
    now_ts: int,
) -> Optional[Dict[str, Any]]:
    """Evaluate a market and return a structured selection summary."""
    ticker = str(m.get("ticker") or "")
    if not ticker:
        return None

    status = str(m.get("status") or "").lower()
    result = str(m.get("result") or "").lower()
    is_active = status in ("open", "active", "trading") and result == ""

    # Price checks — prefer dollar fields from production API.
    yes_bid_d = _normalize_price(m.get("yes_bid_dollars"))
    yes_ask_d = _normalize_price(m.get("yes_ask_dollars"))
    if yes_bid_d is None:
        yes_bid_d = _normalize_price(m.get("yes_bid"))
    if yes_ask_d is None:
        yes_ask_d = _normalize_price(m.get("yes_ask"))
    last_price_d = _normalize_price(m.get("last_price_dollars"))
    if last_price_d is None:
        last_price_d = _normalize_price(m.get("last_price"))

    quoteable_book = (
        yes_bid_d is not None
        and yes_ask_d is not None
        and yes_bid_d > 0.0
        and yes_ask_d > 0.0
        and yes_ask_d > yes_bid_d
    )

    # Expiry check
    expiration_ts = _parse_ts(m.get("expiration_time") or m.get("close_time"))
    end_ts_ms = int(expiration_ts * 1000) if expiration_ts else None
    if is_active and expiration_ts and (expiration_ts - now_ts) < config.min_time_to_expiry_secs:
        return None

    # Volume / open interest — prefer _fp fields from production
    volume = _coerce_float(m.get("volume_fp") or m.get("volume") or m.get("volume_24h") or 0.0)
    open_interest = _coerce_float(m.get("open_interest_fp") or m.get("open_interest") or 0.0)
    if volume is None:
        volume = 0.0
    if open_interest is None:
        open_interest = 0.0

    # Build virtual token IDs
    token_ids = (f"{ticker}:yes", f"{ticker}:no")

    # Tick size: Kalshi standard is 1 cent = 0.01 dollars
    tick_size = _coerce_float(m.get("tick_size")) or 0.01
    if tick_size >= 1.0:
        tick_size = tick_size / 100.0  # cents → dollars

    # Spread from bid/ask.
    spread: Optional[float] = None
    mid: Optional[float] = None
    if quoteable_book:
        assert yes_bid_d is not None and yes_ask_d is not None
        spread = max(0.0, yes_ask_d - yes_bid_d)
        mid = (yes_bid_d + yes_ask_d) / 2.0
    elif last_price_d is not None:
        mid = last_price_d

    rejected_reason: Optional[str] = None
    accepted = False
    if is_active:
        if not quoteable_book:
            rejected_reason = "one_sided_book"
        elif mid is None:
            rejected_reason = "missing_mid"
        elif mid < config.min_price or mid > config.max_price:
            rejected_reason = "price_out_of_range"
        elif spread is not None and spread > config.max_spread:
            rejected_reason = "spread_too_wide"
        elif volume < config.min_volume_24hr:
            rejected_reason = "insufficient_volume"
        elif open_interest < config.min_open_interest:
            rejected_reason = "insufficient_open_interest"
        else:
            accepted = True
    else:
        rejected_reason = "inactive_market"

    # Score: favor liquidity, tight spread, and mid-range pricing.
    spread_penalty = 0.0
    if spread is not None:
        spread_penalty = max(0.05, 1.0 - min(spread / max(config.max_spread, 1e-9), 1.0))
    midpoint_penalty = 1.0
    if mid is not None:
        midpoint_penalty = max(0.1, 1.0 - min(abs(mid - 0.5) * 2.0, 0.9))
    liquidity_score = math.log1p(max(0.0, float(volume))) + (2.0 * math.log1p(max(0.0, float(open_interest))))
    score = liquidity_score * spread_penalty * midpoint_penalty

    # Subtitle / outcomes
    yes_sub = str(m.get("yes_sub_title") or "Yes")
    no_sub = str(m.get("no_sub_title") or "No")

    # Extract reference symbol from title or series
    title = str(m.get("title") or m.get("event_ticker") or ticker)
    ref_symbol = _extract_symbol(title, ticker)

    candidate = MarketCandidate(
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
        tradable=bool(accepted),
        clob_candidate=True,
        score=score,
        raw=dict(m),
    )

    return {
        "ticker": ticker,
        "title": title,
        "reference_symbol": ref_symbol,
        "status": status,
        "result": result,
        "active": is_active,
        "accepted": accepted,
        "reason": "quoteable_book" if accepted else rejected_reason,
        "quoteability_state": "quoteable" if accepted else (rejected_reason or "rejected"),
        "book_valid_both_sides": bool(quoteable_book),
        "bid": yes_bid_d,
        "ask": yes_ask_d,
        "mid": mid,
        "spread": spread,
        "volume": float(volume),
        "open_interest": float(open_interest),
        "tick_size": float(tick_size),
        "score": float(score),
        "candidate": candidate,
        "summary": {
            "ticker": ticker,
            "title": title,
            "reference_symbol": ref_symbol,
            "status": status,
            "result": result,
            "accepted": accepted,
            "reason": "quoteable_book" if accepted else rejected_reason,
            "quoteability_state": "quoteable" if accepted else (rejected_reason or "rejected"),
            "book_valid_both_sides": bool(quoteable_book),
            "bid": yes_bid_d,
            "ask": yes_ask_d,
            "mid": mid,
            "spread": spread,
            "volume": float(volume),
            "open_interest": float(open_interest),
            "tick_size": float(tick_size),
            "score": float(score),
            "active": is_active,
            "tradable": bool(accepted),
        },
    }


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_price(value: Any) -> Optional[float]:
    raw = _coerce_float(value)
    if raw is None:
        return None
    if raw > 1.0:
        return raw / 100.0
    return raw


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
