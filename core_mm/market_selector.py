from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import urlopen


GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"


@dataclass(frozen=True)
class MarketSelectionConfig:
    symbol: str = "BTC"
    horizon: str = "15m"
    api_url: str = GAMMA_EVENTS_URL
    timeout_secs: float = 5.0
    page_limit: int = 100
    max_volatility_sum: float = 20.0
    max_spread: float = 0.10
    min_price: float = 0.10
    max_price: float = 0.90


@dataclass(frozen=True)
class MarketCandidate:
    slug: str
    condition_id: str
    token_ids: tuple[str, ...]
    outcomes: tuple[str, ...]
    reward_per_100: float
    volatility_sum: float
    spread: float
    mid_price: Optional[float]
    active: Optional[bool]
    closed: Optional[bool]
    accepting_orders: Optional[bool]
    tick_size: Optional[float]
    max_incentive_spread: Optional[float]
    min_incentive_size: Optional[float]
    score: float
    raw: Dict[str, Any]


class MarketSelector:
    def __init__(
        self,
        *,
        config: Optional[MarketSelectionConfig] = None,
        fetcher: Optional[Callable[[str, float], Any]] = None,
    ) -> None:
        self.config = config or MarketSelectionConfig()
        self._fetcher = fetcher or _fetch_json

    def fetch_active_events(self) -> List[Dict[str, Any]]:
        offset = 0
        collected: List[Dict[str, Any]] = []
        while True:
            params = {
                "active": "true",
                "closed": "false",
                "limit": str(self.config.page_limit),
                "offset": str(offset),
            }
            url = f"{self.config.api_url}?{urlencode(params)}"
            payload = self._fetcher(url, self.config.timeout_secs)
            if not isinstance(payload, list):
                raise ValueError("gamma_events_not_list")
            if not payload:
                break
            collected.extend(entry for entry in payload if isinstance(entry, dict))
            if len(payload) < self.config.page_limit:
                break
            offset += self.config.page_limit
        return collected

    def select_markets(self) -> List[MarketCandidate]:
        return self.select_from_events(self.fetch_active_events())

    def select_from_events(self, events: Iterable[Dict[str, Any]]) -> List[MarketCandidate]:
        candidates: List[MarketCandidate] = []
        for event in events:
            candidate = self._to_candidate(event)
            if candidate is not None:
                candidates.append(candidate)
        return sorted(candidates, key=lambda item: item.score, reverse=True)

    def _to_candidate(self, event: Dict[str, Any]) -> Optional[MarketCandidate]:
        slug = str(event.get("slug") or "")
        if not slug:
            return None
        slug_lower = slug.lower()
        if self.config.symbol.lower() not in slug_lower or self.config.horizon.lower() not in slug_lower:
            return None

        token_ids = tuple(_coerce_list(event.get("clobTokenIds")))
        if len(token_ids) < 2:
            return None
        condition_id = str(event.get("conditionId") or event.get("condition_id") or "")
        if not condition_id:
            return None

        active = _coerce_bool(event.get("active"))
        closed = _coerce_bool(event.get("closed"))
        accepting_orders = _coerce_bool(event.get("accepting_orders") or event.get("acceptingOrders"))
        if active is False:
            return None
        if closed is True and active is not True:
            return None
        if accepting_orders is False and active is not True:
            return None

        volatility_sum = _coerce_float(
            event.get("volatility_sum")
            or event.get("volatilitySum")
            or event.get("volatility")
            or 0.0
        )
        if volatility_sum > self.config.max_volatility_sum:
            return None

        spread = _coerce_float(
            event.get("spread")
            or event.get("spread_decimal")
            or event.get("spreadPct")
            or event.get("spread_pct")
            or event.get("spread_fraction")
            or 0.0
        )
        if spread > self.config.max_spread:
            return None

        mid_price = _extract_mid_price(event)
        if mid_price is not None and not (self.config.min_price <= mid_price <= self.config.max_price):
            return None

        reward_per_100 = _coerce_reward_per_100(event)
        score = reward_per_100 / (volatility_sum + 1.0)
        return MarketCandidate(
            slug=slug,
            condition_id=condition_id,
            token_ids=token_ids,
            outcomes=tuple(_coerce_list(event.get("outcomes"))),
            reward_per_100=reward_per_100,
            volatility_sum=volatility_sum,
            spread=spread,
            mid_price=mid_price,
            active=active,
            closed=closed,
            accepting_orders=accepting_orders,
            tick_size=_coerce_float_or_none(event.get("tick_size") or event.get("tickSize")),
            max_incentive_spread=_coerce_float_or_none(
                event.get("max_incentive_spread") or event.get("maxIncentiveSpread")
            ),
            min_incentive_size=_coerce_float_or_none(
                event.get("min_incentive_size") or event.get("minIncentiveSize")
            ),
            score=score,
            raw=dict(event),
        )


def _fetch_json(url: str, timeout: float) -> Any:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _coerce_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _coerce_float_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return _coerce_float(value)


def _extract_mid_price(event: Dict[str, Any]) -> Optional[float]:
    explicit = event.get("mid_price") or event.get("midPrice")
    if explicit not in (None, ""):
        return _coerce_float(explicit)
    prices = event.get("prices")
    if isinstance(prices, list) and prices:
        numeric = [float(item) for item in prices[:2]]
        if not numeric:
            return None
        return sum(numeric) / len(numeric)
    outcomes = event.get("outcomePrices")
    if isinstance(outcomes, list) and outcomes:
        numeric = [float(item) for item in outcomes[:2]]
        if not numeric:
            return None
        return sum(numeric) / len(numeric)
    return None


def _coerce_reward_per_100(event: Dict[str, Any]) -> float:
    direct = event.get("reward_per_100") or event.get("rewardPer100") or event.get("gm_reward_per_100")
    if direct not in (None, ""):
        return _coerce_float(direct)
    rewards = event.get("rewards")
    if isinstance(rewards, dict):
        for key in ("daily", "daily_reward", "maxDailyRewards", "max_daily_rewards"):
            if rewards.get(key) not in (None, ""):
                return _coerce_float(rewards.get(key))
    for key in ("rewardsDailyRate", "rewards_daily_rate", "liquidityRewards", "liquidity_rewards"):
        if event.get(key) not in (None, ""):
            return _coerce_float(event.get(key))
    return 0.0
