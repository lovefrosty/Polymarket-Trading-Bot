from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
except ModuleNotFoundError:  # pragma: no cover
    certifi = None


GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
_CLOB_BASE_URL = "https://clob.polymarket.com"


# ---------------------------------------------------------------------------
# CLOB candidate discovery (inlined from core.clob_discovery)
# Only the sync path is needed here; async/FeeRateClient are not used.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ClobCandidate:
    condition_id: str
    token_ids: List[str]
    outcomes: List[str]
    prices: List[Optional[float]]
    accepting_orders: Optional[bool]
    active: Optional[bool]
    closed: Optional[bool]
    archived: Optional[bool]


def _list_clob_candidates(
    source: str = "sampling-markets",
    fallback: str = "simplified-markets",
    base_url: str = _CLOB_BASE_URL,
) -> List[_ClobCandidate]:
    candidates: List[_ClobCandidate] = []
    cursor: Optional[str] = None
    while True:
        params = {"next_cursor": cursor} if cursor else None
        data = _fetch_clob_endpoint(base_url, source, params=params)
        payload = _extract_clob_payload(data)
        if payload is None and fallback:
            data = _fetch_clob_endpoint(base_url, fallback, params=params)
            payload = _extract_clob_payload(data)
        if payload is None:
            raise ValueError("clob_candidate_payload_missing")
        for entry in payload:
            candidate = _parse_clob_candidate(entry)
            if candidate is None:
                continue
            candidates.append(candidate)
        cursor = _extract_clob_next_cursor(data)
        if not cursor:
            break
    candidates.sort(key=lambda item: item.condition_id)
    return candidates


def _fetch_clob_endpoint(base_url: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    if params:
        query = urlencode({k: str(v) for k, v in params.items() if v is not None})
        if query:
            url = f"{url}?{query}"
    return _fetch_json(url, timeout=5.0)


def _parse_clob_candidate(entry: Dict[str, Any]) -> Optional[_ClobCandidate]:
    condition_id = entry.get("condition_id") or entry.get("conditionId")
    if not condition_id:
        return None
    accepting_orders = entry.get("accepting_orders")
    active = entry.get("active")
    closed = entry.get("closed")
    archived = entry.get("archived")
    if accepting_orders is not None and not accepting_orders:
        return None
    if active is not None and not active:
        return None
    if closed is not None and closed:
        return None
    if archived is not None and archived:
        return None
    tokens = entry.get("tokens") or []
    if not isinstance(tokens, list) or len(tokens) != 2:
        return None
    token_ids: List[str] = []
    outcomes: List[str] = []
    prices: List[Optional[float]] = []
    for token in tokens:
        token_id = token.get("token_id") or token.get("tokenId")
        outcome = token.get("outcome")
        price = token.get("price")
        if not token_id:
            return None
        token_ids.append(str(token_id))
        outcomes.append(str(outcome) if outcome is not None else "")
        try:
            prices.append(float(price) if price is not None else None)
        except (TypeError, ValueError):
            prices.append(None)
    return _ClobCandidate(
        condition_id=str(condition_id),
        token_ids=token_ids,
        outcomes=outcomes,
        prices=prices,
        accepting_orders=accepting_orders if accepting_orders is not None else None,
        active=active if active is not None else None,
        closed=closed if closed is not None else None,
        archived=archived if archived is not None else None,
    )


def _extract_clob_payload(data: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(data, dict):
        payload = data.get("data")
        return payload if isinstance(payload, list) else None
    if isinstance(data, list):
        return data
    return None


def _extract_clob_next_cursor(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    cursor = data.get("next_cursor") or data.get("nextCursor")
    if not cursor:
        return None
    cursor_str = str(cursor)
    if cursor_str == "LTE=":
        return None
    try:
        import base64
        if base64.b64decode(cursor_str).decode("utf-8") == "-1":
            return None
    except Exception:
        pass
    return cursor_str


@dataclass(frozen=True)
class MarketSelectionConfig:
    symbol: str = "BTC"
    symbols: tuple[str, ...] = ()
    horizon: str = "15m"
    api_url: str = GAMMA_EVENTS_URL
    markets_url: str = GAMMA_MARKETS_URL
    timeout_secs: float = 5.0
    page_limit: int = 100
    max_volatility_sum: float = 20.0
    max_spread: float = 0.10
    min_price: float = 0.10
    max_price: float = 0.90
    slug_back_windows: int = 4
    slug_forward_windows: int = 8
    require_accepting_orders: bool = True
    require_clob_candidate: bool = True
    current_window_only: bool = True
    clob_cache_secs: float = 60.0


@dataclass(frozen=True)
class MarketCandidate:
    reference_symbol: str
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
    end_ts_ms: Optional[int]
    end_ts_source: Optional[str]
    active_now: bool
    tradable: bool
    clob_candidate: bool
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
        self._clob_condition_ids_cache: Optional[set[str]] = None
        self._clob_condition_ids_cache_ts = 0.0

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

    def fetch_slug_window_markets(self, *, now_ts: Optional[int] = None) -> List[Dict[str, Any]]:
        slugs: List[str] = []
        for symbol in _candidate_symbols(self.config):
            slugs.extend(
                _generate_15m_slugs(
                    symbol,
                    back_windows=self.config.slug_back_windows,
                    forward_windows=self.config.slug_forward_windows,
                    now_ts=now_ts,
                )
            )
        if not slugs:
            return []
        query = urlencode({"slug": slugs}, doseq=True)
        url = f"{self.config.markets_url}?{query}"
        payload = self._fetcher(url, self.config.timeout_secs)
        if not isinstance(payload, list):
            raise ValueError("gamma_markets_not_list")
        return [entry for entry in payload if isinstance(entry, dict)]

    def select_markets(self, *, now_ts: Optional[int] = None) -> List[MarketCandidate]:
        if self.config.horizon.lower() == "15m":
            selected = self.select_from_markets(
                self.fetch_slug_window_markets(now_ts=now_ts),
                now_ts=now_ts,
                require_clob_candidate=False,
            )
            if selected:
                return selected
        selected = self.select_from_events(self.fetch_active_events(), now_ts=now_ts)
        if selected:
            return selected
        return []

    def select_from_events(
        self,
        events: Iterable[Dict[str, Any]],
        *,
        now_ts: Optional[int] = None,
    ) -> List[MarketCandidate]:
        candidates: List[MarketCandidate] = []
        clob_condition_ids = self._known_clob_condition_ids()
        for event in events:
            candidate = self._to_candidate(event, now_ts=now_ts, clob_condition_ids=clob_condition_ids)
            if candidate is not None:
                candidates.append(candidate)
            markets = event.get("markets")
            if isinstance(markets, list):
                for market in markets:
                    if isinstance(market, dict):
                        candidate = self._to_candidate(market, now_ts=now_ts, clob_condition_ids=clob_condition_ids)
                        if candidate is not None:
                            candidates.append(candidate)
        return self._rank_candidates(candidates)

    def select_from_markets(
        self,
        markets: Iterable[Dict[str, Any]],
        *,
        now_ts: Optional[int] = None,
        require_clob_candidate: Optional[bool] = None,
    ) -> List[MarketCandidate]:
        candidates: List[MarketCandidate] = []
        clob_condition_ids = self._known_clob_condition_ids() if require_clob_candidate is not False else None
        for market in markets:
            candidate = self._to_candidate(
                market,
                now_ts=now_ts,
                clob_condition_ids=clob_condition_ids,
                require_clob_candidate=require_clob_candidate,
            )
            if candidate is not None:
                candidates.append(candidate)
        return self._rank_candidates(candidates)

    def _to_candidate(
        self,
        event: Dict[str, Any],
        *,
        now_ts: Optional[int] = None,
        clob_condition_ids: Optional[set[str]] = None,
        require_clob_candidate: Optional[bool] = None,
    ) -> Optional[MarketCandidate]:
        slug = str(event.get("slug") or "")
        if not slug:
            return None
        slug_lower = slug.lower()
        if not _matches_symbol_horizon(slug_lower, self.config):
            return None

        token_ids = tuple(_coerce_list(event.get("clobTokenIds")))
        if len(token_ids) < 2:
            return None
        condition_id = str(event.get("conditionId") or event.get("condition_id") or "")
        if not condition_id:
            return None
        require_clob = self.config.require_clob_candidate if require_clob_candidate is None else bool(require_clob_candidate)
        if require_clob and clob_condition_ids is not None and condition_id not in clob_condition_ids:
            return None

        active = _coerce_bool(event.get("active"))
        closed = _coerce_bool(event.get("closed"))
        accepting_orders = _coerce_bool(event.get("accepting_orders") or event.get("acceptingOrders"))
        if active is False:
            return None
        if closed is True:
            return None
        if self.config.require_accepting_orders and accepting_orders is False:
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

        reference_symbol = _extract_reference_symbol(slug)
        end_ts_ms, end_ts_source = _market_end_ts_ms_and_source(event, slug)
        now_ms = _resolve_now_ms(now_ts)
        reward_per_100 = _coerce_reward_per_100(event)
        tradable = active is not False and closed is not True and (
            (not self.config.require_accepting_orders) or accepting_orders is not False
        )
        # Spread-adjusted score: spread is gross edge for a market maker.
        # Half-spread in bps captures per-side edge, reward adds incentive bonus.
        spread_edge_bps = (spread * 10_000.0) / 2.0
        reward_bps_equiv = reward_per_100 * 100.0
        score = (spread_edge_bps + reward_bps_equiv) / (volatility_sum + 1.0)
        return MarketCandidate(
            reference_symbol=reference_symbol,
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
            tick_size=_coerce_float_or_none(
                event.get("tick_size") or event.get("tickSize") or event.get("orderPriceMinTickSize")
            ),
            max_incentive_spread=_coerce_float_or_none(
                event.get("max_incentive_spread") or event.get("maxIncentiveSpread")
            ),
            min_incentive_size=_coerce_float_or_none(
                event.get("min_incentive_size") or event.get("minIncentiveSize") or event.get("orderMinSize")
            ),
            end_ts_ms=end_ts_ms,
            end_ts_source=end_ts_source,
            active_now=_is_candidate_active_now(end_ts_ms, now_ms) if self.config.horizon.lower() == "15m" else False,
            tradable=tradable,
            clob_candidate=condition_id in clob_condition_ids if clob_condition_ids is not None else False,
            score=score,
            raw=dict(event),
        )

    def _known_clob_condition_ids(self) -> Optional[set[str]]:
        if not self.config.require_clob_candidate:
            return None
        now = time.time()
        if (
            self._clob_condition_ids_cache is not None
            and (now - self._clob_condition_ids_cache_ts) <= float(self.config.clob_cache_secs)
        ):
            return set(self._clob_condition_ids_cache)
        try:
            self._clob_condition_ids_cache = {
                str(candidate.condition_id)
                for candidate in _list_clob_candidates()
                if getattr(candidate, "condition_id", None)
            }
            self._clob_condition_ids_cache_ts = now
            return set(self._clob_condition_ids_cache)
        except Exception:
            return None

    def _rank_candidates(self, candidates: List[MarketCandidate]) -> List[MarketCandidate]:
        if not candidates:
            return []
        if self.config.current_window_only:
            timed = [candidate for candidate in candidates if candidate.end_ts_ms is not None]
            if timed:
                active_now = [candidate for candidate in timed if candidate.active_now]
                if active_now:
                    candidates = active_now
                else:
                    return []
        candidates = [candidate for candidate in candidates if candidate.tradable]
        if not candidates:
            return []
        return sorted(candidates, key=_candidate_sort_key, reverse=True)


def _fetch_json(url: str, timeout: float) -> Any:
    request = Request(url, headers={"User-Agent": "PolymarketBot/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _coerce_list(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        value = decoded
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
    prices = event.get("prices") or event.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            prices = None
    if isinstance(prices, list) and prices:
        numeric = [float(item) for item in prices[:2]]
        if numeric:
            # Gamma returns binary outcome prices as [YES, NO]. For selection
            # gates we care about the quoted outcome price, not the mean of
            # complementary outcomes, which is ~0.5 by construction.
            return numeric[0]
    best_bid = _coerce_float_or_none(event.get("bestBid") or event.get("best_bid"))
    best_ask = _coerce_float_or_none(event.get("bestAsk") or event.get("best_ask"))
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2.0
    if best_bid is not None:
        return best_bid
    if best_ask is not None:
        return best_ask
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
    for key in ("rewardsDailyRate", "rewards_daily_rate", "liquidityRewards", "liquidity_rewards", "umaReward"):
        if event.get(key) not in (None, ""):
            return _coerce_float(event.get(key))
    return 0.0


def _candidate_symbols(config: MarketSelectionConfig) -> tuple[str, ...]:
    configured = tuple(str(symbol).strip().upper() for symbol in config.symbols if str(symbol).strip())
    if configured:
        return configured
    return (str(config.symbol).strip().upper(),)


def _matches_symbol_horizon(slug_lower: str, config: MarketSelectionConfig) -> bool:
    if config.horizon.lower() not in slug_lower:
        return False
    return any(symbol.lower() in slug_lower for symbol in _candidate_symbols(config))


def _extract_reference_symbol(slug: str) -> str:
    return slug.split("-", 1)[0].strip().upper()


def _candidate_sort_key(candidate: MarketCandidate) -> tuple[Any, ...]:
    return (
        float(candidate.score),
        int(bool(candidate.active_now)),
        int(bool(candidate.tradable)),
        int(bool(candidate.clob_candidate)),
        int(candidate.end_ts_ms or 0),
        candidate.slug,
        candidate.condition_id,
        ",".join(sorted(candidate.token_ids)),
    )


def _resolve_now_ms(now_ts: Optional[int]) -> int:
    if now_ts is None:
        return int(time.time() * 1000)
    return int(now_ts) * 1000


def _is_candidate_active_now(end_ts_ms: Optional[int], now_ms: int) -> bool:
    if end_ts_ms is None:
        return False
    start_ts_ms = int(end_ts_ms) - 900_000
    return start_ts_ms <= int(now_ms) < int(end_ts_ms)


def _market_end_ts_ms_and_source(event: Dict[str, Any], slug: str) -> tuple[Optional[int], Optional[str]]:
    for key in ("endDate", "end_date", "endTime", "end_time"):
        parsed = _parse_timestamp_value(event.get(key))
        if parsed is not None and parsed > 0:
            return int(parsed * 1000), "metadata"
    slug_ts = _parse_slug_ts(slug)
    if slug_ts is not None and slug_ts > 0:
        return int(slug_ts * 1000), "slug_fallback"
    return None, None


def _parse_timestamp_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if float(value) < 1_000_000_000_000:
            return int(value)
        return int(float(value) / 1000.0)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_timestamp_value(int(text))
        try:
            from datetime import datetime

            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.fromisoformat(text).timestamp())
        except ValueError:
            return None
    return None


def _parse_slug_ts(slug: str) -> Optional[int]:
    if not slug:
        return None
    tail = slug.rsplit("-", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return None


def _generate_15m_slugs(symbol: str, *, back_windows: int, forward_windows: int, now_ts: Optional[int] = None) -> List[str]:
    base_ts = int(now_ts if now_ts is not None else time.time())
    bucket_ts = (base_ts // 900) * 900
    return [f"{symbol.lower()}-updown-15m-{bucket_ts + (offset * 900)}" for offset in range(-back_windows, forward_windows + 1)]
