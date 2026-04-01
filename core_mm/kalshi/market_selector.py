"""Kalshi market discovery — finds tradable markets via Kalshi REST API.

Returns ``MarketCandidate`` objects compatible with ``CoreMMRunner``.
Uses the same dataclass as the Polymarket selector so that all
downstream code (runner, main_loop, telemetry) works unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from core_mm.market_selector import MarketCandidate


_REPO_ROOT = Path(__file__).resolve().parents[2]
_KALSHI_FAMILY_PATH = _REPO_ROOT / "config" / "kalshi_market_families.json"
_EASTERN = ZoneInfo("America/New_York")


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
    # Explicit market-family filtering
    market_families_path: Optional[str] = None
    # Liquidity gate
    min_liquidity_score: float = 0.60
    liquidity_volume_cap: float = 50_000.0
    liquidity_spread_cap: float = 0.10
    liquidity_depth_cap: float = 200.0
    liquidity_volume_weight: float = 0.5
    liquidity_spread_weight: float = 0.3
    liquidity_depth_weight: float = 0.2
    transition_risk_gap: float = 0.15


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

        series_meta = _fetch_series_metadata(self._client, raw_markets)
        if series_meta:
            enriched_markets: List[Dict[str, Any]] = []
            for market in raw_markets:
                row = dict(market)
                series_ticker = str(row.get("series_ticker") or row.get("seriesTicker") or "")
                meta = series_meta.get(series_ticker)
                if meta:
                    if row.get("series_fee_type") in (None, ""):
                        row["series_fee_type"] = meta.get("fee_type")
                    if row.get("series_fee_multiplier") in (None, ""):
                        row["series_fee_multiplier"] = meta.get("fee_multiplier")
                enriched_markets.append(row)
            raw_markets = enriched_markets

        families = _load_market_families(self.config.market_families_path)
        active_families = _enabled_families_for_symbol(families, self.config.series_ticker, series)
        transition_context = _build_transition_context(raw_markets, self.config)
        evaluations: List[Dict[str, Any]] = []
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        candidates: List[MarketCandidate] = []
        for m in raw_markets:
            evaluation = _evaluate_market(
                m,
                self.config,
                now_ts,
                active_families,
                transition_context.get(str(m.get("ticker") or ""), {}),
            )
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
            "enabled_market_families": [_family_summary(family) for family in active_families],
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
    evaluation = _evaluate_market(m, config, now_ts, [], {})
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
    active_families: Sequence[Dict[str, Any]],
    transition_context: Dict[str, Any],
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
    touch_depth = _extract_touch_depth(m, volume=volume, open_interest=open_interest)

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
    liquidity_score = _liquidity_score(
        volume_24h=volume,
        spread=spread,
        depth_at_touch=touch_depth,
        config=config,
    )
    transition_risk = float(transition_context.get("transition_risk") or 0.0)
    proximity_score = max(0.0, 1.0 - transition_risk)

    family_match = _match_market_family(m, active_families)

    rejected_reason: Optional[str] = None
    accepted = False
    if is_active:
        if active_families and family_match is None:
            rejected_reason = "family_not_enabled"
        elif not quoteable_book:
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
        elif liquidity_score < float(config.min_liquidity_score):
            rejected_reason = "liquidity_score_too_low"
        else:
            accepted = True
    else:
        rejected_reason = "inactive_market"

    # Score priority:
    #   1. liquid and active
    #   2. buckets closest to the live transition zone
    #   3. then near 50c
    spread_penalty = 0.0
    if spread is not None:
        spread_penalty = max(0.05, 1.0 - min(spread / max(config.max_spread, 1e-9), 1.0))
    midpoint_score = 1.0
    if mid is not None:
        midpoint_score = max(0.1, 1.0 - min(abs(mid - 0.5) * 2.0, 0.9))
    liquidity_component = float(liquidity_score) * float(spread_penalty)
    score = (
        0.65 * liquidity_component
        + 0.20 * float(proximity_score)
        + 0.15 * float(midpoint_score)
    )

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
        "family_name": family_match.get("name") if family_match else None,
        "contract_type": _infer_contract_type(m),
        "book_valid_both_sides": bool(quoteable_book),
        "bid": yes_bid_d,
        "ask": yes_ask_d,
        "mid": mid,
        "spread": spread,
        "liquidity_score": float(liquidity_score),
        "touch_depth": float(touch_depth),
        "transition_risk": float(transition_risk),
        "proximity_score": float(proximity_score),
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
            "family_name": family_match.get("name") if family_match else None,
            "contract_type": _infer_contract_type(m),
            "book_valid_both_sides": bool(quoteable_book),
            "bid": yes_bid_d,
            "ask": yes_ask_d,
            "mid": mid,
            "spread": spread,
            "liquidity_score": float(liquidity_score),
            "touch_depth": float(touch_depth),
            "transition_risk": float(transition_risk),
            "proximity_score": float(proximity_score),
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


def _load_market_families(path_str: Optional[str]) -> List[Dict[str, Any]]:
    path = Path(path_str) if path_str else _KALSHI_FAMILY_PATH
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return []
    families = payload.get("families") if isinstance(payload, dict) else None
    if not isinstance(families, list):
        return []
    return [family for family in families if isinstance(family, dict)]


def _fetch_series_metadata(client: object, markets: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    get_series = getattr(client, "get_series", None)
    if get_series is None:
        return {}
    series_tickers = sorted(
        {
            str(market.get("series_ticker") or market.get("seriesTicker") or "").strip()
            for market in markets
            if str(market.get("series_ticker") or market.get("seriesTicker") or "").strip()
        }
    )
    if not series_tickers:
        return {}
    results: Dict[str, Dict[str, Any]] = {}
    for series_ticker in series_tickers:
        try:
            raw = get_series(series_ticker)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        fee_type = raw.get("fee_type")
        fee_multiplier = raw.get("fee_multiplier")
        if fee_type in (None, "") and fee_multiplier in (None, ""):
            continue
        results[series_ticker] = {
            "fee_type": fee_type,
            "fee_multiplier": fee_multiplier,
        }
    return results


def _enabled_families_for_symbol(
    families: Sequence[Dict[str, Any]],
    requested_symbol: Optional[str],
    resolved_series: Optional[str],
) -> List[Dict[str, Any]]:
    req = str(requested_symbol or "").upper()
    series = str(resolved_series or "").upper()
    selected: List[Dict[str, Any]] = []
    for family in families:
        if family.get("enabled") is False:
            continue
        symbol = str(family.get("symbol") or "").upper()
        series_ticker = str(family.get("series_ticker") or "").upper()
        if req and symbol and symbol != req:
            continue
        if series and series_ticker and series_ticker != series:
            continue
        selected.append(family)
    return selected


def _family_summary(family: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": family.get("name"),
        "symbol": family.get("symbol"),
        "series_ticker": family.get("series_ticker"),
        "contract_types": family.get("contract_types") or [],
        "close_time_et": family.get("close_time_et"),
        "min_hours_to_expiry": family.get("min_hours_to_expiry"),
        "max_hours_to_expiry": family.get("max_hours_to_expiry"),
    }


def _match_market_family(m: Dict[str, Any], families: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not families:
        return None
    contract_type = _infer_contract_type(m)
    expiry_ts = _parse_ts(m.get("expiration_time") or m.get("close_time"))
    expiry_dt_et = datetime_from_ts(expiry_ts) if expiry_ts is not None else None
    hours_to_expiry = max(0.0, (expiry_ts - time.time()) / 3600.0) if expiry_ts is not None else None
    title = str(m.get("title") or "")
    ticker = str(m.get("ticker") or "")
    for family in families:
        allowed_types = [str(item).lower() for item in family.get("contract_types") or []]
        if allowed_types and contract_type not in allowed_types:
            continue
        close_time_et = str(family.get("close_time_et") or "").strip()
        if close_time_et and expiry_dt_et is not None:
            hhmm = expiry_dt_et.strftime("%H:%M")
            if hhmm != close_time_et:
                continue
        min_hours = _coerce_float(family.get("min_hours_to_expiry"))
        if min_hours is not None and hours_to_expiry is not None and hours_to_expiry < min_hours:
            continue
        max_hours = _coerce_float(family.get("max_hours_to_expiry"))
        if max_hours is not None and hours_to_expiry is not None and hours_to_expiry > max_hours:
            continue
        if family.get("title_contains"):
            if str(family.get("title_contains")).lower() not in title.lower():
                continue
        if family.get("ticker_contains"):
            if str(family.get("ticker_contains")).upper() not in ticker.upper():
                continue
        return family
    return None


def datetime_from_ts(ts: float) -> Any:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_EASTERN)


def _infer_contract_type(m: Dict[str, Any]) -> str:
    ticker = str(m.get("ticker") or "").upper()
    title = str(m.get("title") or "").lower()
    floor_strike = m.get("floor_strike")
    cap_strike = m.get("cap_strike")
    if floor_strike not in (None, "") and cap_strike not in (None, ""):
        return "range"
    if "-B" in ticker:
        return "range"
    if " or above" in title or title.startswith("will ") and " above " in title:
        return "above"
    if " or below" in title or title.startswith("will ") and " below " in title:
        return "below"
    if "-T" in ticker:
        return "above_or_below"
    return "other"


def _extract_touch_depth(m: Dict[str, Any], *, volume: float, open_interest: float) -> float:
    direct_keys = (
        "yes_bid_size",
        "yes_ask_size",
        "best_bid_size",
        "best_ask_size",
        "bid_size",
        "ask_size",
    )
    numeric: List[float] = []
    for key in direct_keys:
        value = _coerce_float(m.get(key))
        if value is not None and value > 0:
            numeric.append(value)
    if len(numeric) >= 2:
        return max(0.0, min(numeric))
    if numeric:
        return max(0.0, numeric[0])
    return max(0.0, min(float(open_interest), max(0.0, float(volume) / 10.0)))


def _liquidity_score(
    *,
    volume_24h: float,
    spread: Optional[float],
    depth_at_touch: float,
    config: KalshiSelectorConfig,
) -> float:
    components: List[tuple[float, float]] = []
    volume_cap = max(1.0, float(config.liquidity_volume_cap))
    spread_cap = max(1e-6, float(config.liquidity_spread_cap))
    depth_cap = max(1.0, float(config.liquidity_depth_cap))

    volume_norm = min(1.0, math.log1p(max(0.0, float(volume_24h))) / math.log1p(volume_cap))
    components.append((float(config.liquidity_volume_weight), volume_norm))

    if spread is not None:
        spread_norm = max(0.0, min(1.0, 1.0 - (float(spread) / spread_cap)))
        components.append((float(config.liquidity_spread_weight), spread_norm))

    if depth_at_touch > 0.0:
        depth_norm = min(1.0, float(depth_at_touch) / depth_cap)
        components.append((float(config.liquidity_depth_weight), depth_norm))

    total_weight = sum(weight for weight, _ in components)
    if total_weight <= 0:
        return 0.0
    return sum(weight * value for weight, value in components) / total_weight


def _build_transition_context(
    markets: Sequence[Dict[str, Any]],
    config: KalshiSelectorConfig,
) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for market in markets:
        ticker = str(market.get("ticker") or "")
        if not ticker:
            continue
        if _range_floor_strike(market) is None:
            continue
        groups.setdefault(_range_group_key(market), []).append(market)

    context: Dict[str, Dict[str, Any]] = {}
    for members in groups.values():
        ordered = sorted(members, key=lambda item: (_range_floor_strike(item) or 0.0, str(item.get("ticker") or "")))
        for idx, market in enumerate(ordered):
            ticker = str(market.get("ticker") or "")
            mid = _market_mid_price(market)
            neighbor_mids: List[float] = []
            for neighbor_idx in (idx - 1, idx + 1):
                if 0 <= neighbor_idx < len(ordered):
                    neighbor_mid = _market_mid_price(ordered[neighbor_idx])
                    if neighbor_mid is not None:
                        neighbor_mids.append(neighbor_mid)
            risk = 0.0
            if mid is not None and neighbor_mids:
                strongest_neighbor = max(neighbor_mids)
                gap = float(mid) - float(strongest_neighbor)
                risk = max(0.0, min(1.0, 1.0 - (gap / max(float(config.transition_risk_gap), 1e-6))))
            context[ticker] = {
                "transition_risk": float(risk),
                "neighbor_mid_max": max(neighbor_mids) if neighbor_mids else None,
            }
    return context


def _range_group_key(m: Dict[str, Any]) -> str:
    expiry = str(m.get("expiration_time") or m.get("close_time") or "")
    event = str(m.get("event_ticker") or "")
    if event:
        return event
    ticker = str(m.get("ticker") or "")
    if "-B" in ticker:
        return f"{ticker.split('-B')[0]}::{expiry}"
    return f"{ticker}::{expiry}"


def _range_floor_strike(m: Dict[str, Any]) -> Optional[float]:
    direct = _coerce_float(m.get("floor_strike"))
    if direct is not None:
        return direct
    ticker = str(m.get("ticker") or "").upper()
    if "-B" not in ticker:
        return None
    try:
        return float(ticker.rsplit("-B", 1)[1])
    except ValueError:
        return None


def _market_mid_price(m: Dict[str, Any]) -> Optional[float]:
    bid = _first_price(
        _normalize_price(m.get("yes_bid_dollars")),
        _normalize_price(m.get("yes_bid")),
    )
    ask = _first_price(
        _normalize_price(m.get("yes_ask_dollars")),
        _normalize_price(m.get("yes_ask")),
    )
    if bid is not None and ask is not None and ask > bid:
        return (bid + ask) / 2.0
    return _first_price(
        _normalize_price(m.get("last_price_dollars")),
        _normalize_price(m.get("last_price")),
    )


def _first_price(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None and value > 0:
            return float(value)
    return None
