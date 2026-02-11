from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from pathlib import Path
import random
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urlencode

from config.settings import MarketConfig


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


@dataclass(frozen=True)
class ResolvedMarket:
    name: str
    reference_symbol: str
    slug_prefix: Optional[str]
    slug: Optional[str]
    condition_id: str
    token_ids: List[str]
    outcomes: List[str]
    outcome_by_token: Dict[str, str]
    token_by_outcome: Dict[str, str]
    question: Optional[str]
    min_tick: float
    min_size: float
    min_price: float
    max_price: float


PathLike = Union[str, Path]

_FETCH_RNG = random.Random(0)
_FETCH_SLEEP = time.sleep


def _is_15m_slug_prefix(prefix: Optional[str]) -> bool:
    if not prefix:
        return False
    return "updown-15m" in prefix


async def resolve_markets(
    markets: List[MarketConfig],
    auto_discover: bool,
    cache_path: Optional[PathLike],
    gamma_base_url: str = GAMMA_BASE_URL,
    markets_data: Optional[List[Dict[str, Any]]] = None,
    now_ts: Optional[int] = None,
    fee_rate_fetcher: Optional[Any] = None,
    discovery_summary: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ResolvedMarket], Dict[str, Dict[str, Any]]]:
    resolved: List[ResolvedMarket] = []
    asset_meta: Dict[str, Dict[str, Any]] = {}

    needs_data = any(_needs_discovery(market) for market in markets)
    needs_slug = markets_data is None and any(
        _needs_discovery(market) and market.reference_symbol.upper() in CRYPTO_SYMBOLS
        for market in markets
    )
    needs_legacy = any(
        _needs_discovery(market) and market.reference_symbol.upper() not in CRYPTO_SYMBOLS
        for market in markets
    )
    if needs_data and auto_discover and needs_legacy:
        if markets_data is None:
            markets_data = await asyncio.to_thread(load_gamma_markets, gamma_base_url, cache_path)
    elif needs_data and not auto_discover:
        raise ValueError("auto_discover_required_for_missing_ids")

    slug_results: Dict[str, List[Dict[str, Any]]] = {}
    if needs_data and auto_discover and needs_slug:
        symbols = sorted({market.reference_symbol for market in markets if _needs_discovery(market)})
        slug_cache_path = None
        if cache_path:
            slug_cache_path = Path(cache_path).with_name("cache_gamma_slug_markets.json")
        summary = discovery_summary if discovery_summary is not None else {}
        slug_results = await discover_15m_crypto_by_slug(
            symbols=symbols,
            now_ts=now_ts,
            gamma_base_url=gamma_base_url,
            cache_path=slug_cache_path,
            cache_ttl_secs=60,
            summary=summary,
        )
        if discovery_summary is not None:
            discovery_summary.update(summary)

    for market in markets:
        if _needs_discovery(market):
            if not auto_discover:
                raise ValueError(_discovery_required_message(market))
            if markets_data is not None and market.reference_symbol not in slug_results:
                if market.slug_prefix:
                    selected = select_latest_by_prefix(markets_data, market.slug_prefix, now_ts=now_ts)
                else:
                    selected = select_latest_by_fee_rate(
                        markets_data,
                        reference_symbol=market.reference_symbol,
                        fee_rate_fetcher=fee_rate_fetcher,
                        now_ts=now_ts,
                    )
            else:
                discovered = slug_results.get(market.reference_symbol) or []
                selected = discovered[-1] if discovered else None
            if selected is None:
                if market.slug_prefix and markets_data is not None:
                    raise ValueError(f"no_markets_found_for_slug_prefix:{market.slug_prefix}")
                raise ValueError(f"no_markets_found_for_symbol:{market.reference_symbol}")
            condition_id = _coerce_str(selected.get("conditionId") or selected.get("condition_id"))
            token_ids = _coerce_list(selected.get("clobTokenIds"))
            outcomes = _coerce_list(selected.get("outcomes"))
            slug = _coerce_str(selected.get("slug"))
            question = _coerce_str(selected.get("question"))
            if not condition_id:
                raise ValueError(f"missing_condition_id_for_slug_prefix:{market.slug_prefix}")
            if not token_ids:
                raise ValueError(f"missing_token_ids_for_slug_prefix:{market.slug_prefix}")
            if not outcomes:
                raise ValueError(f"missing_outcomes_for_slug_prefix:{market.slug_prefix}")
            if len(token_ids) != len(outcomes):
                raise ValueError(f"token_outcome_length_mismatch:{market.slug_prefix}")
        else:
            condition_id = market.condition_id or ""
            token_ids = list(market.token_ids)
            outcomes = []
            slug = None
            question = None

        outcome_by_token = dict(zip(token_ids, outcomes)) if outcomes else {}
        token_by_outcome = {outcome: token for token, outcome in outcome_by_token.items()}
        entry = ResolvedMarket(
            name=market.name,
            reference_symbol=market.reference_symbol,
            slug_prefix=market.slug_prefix,
            slug=slug,
            condition_id=condition_id,
            token_ids=token_ids,
            outcomes=outcomes,
            outcome_by_token=outcome_by_token,
            token_by_outcome=token_by_outcome,
            question=question,
            min_tick=market.min_tick,
            min_size=market.min_size,
            min_price=market.min_price,
            max_price=market.max_price,
        )
        resolved.append(entry)
        meta = {
            "slug": entry.slug,
            "condition_id": entry.condition_id,
            "token_ids": entry.token_ids,
            "outcomes": entry.outcomes,
            "outcome_by_token": entry.outcome_by_token,
            "token_by_outcome": entry.token_by_outcome,
            "question": entry.question,
            "reference_symbol": entry.reference_symbol,
            "name": entry.name,
        }
        for token_id in entry.token_ids:
            token_meta = dict(meta)
            token_meta["token_id"] = token_id
            token_meta["outcome"] = entry.outcome_by_token.get(token_id)
            asset_meta[token_id] = token_meta

    if auto_discover and resolved:
        # Fee metadata is enrichment only; never used to gate discovery.
        try:
            from core.clob_discovery import FeeRateClient

            fee_client = FeeRateClient()
            await _enrich_fee_metadata(asset_meta, fee_client, discovery_summary)
        except Exception:
            pass

    return resolved, asset_meta


def load_resolved_markets(path: Path) -> Tuple[List[ResolvedMarket], Dict[str, Dict[str, Any]]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and data.get("schema_version") == "resolved_markets_v1":
        return _load_resolved_markets_v1(data)
    return _load_resolved_markets_legacy(data)


def load_gamma_markets(
    base_url: str,
    cache_path: Optional[PathLike],
    active: bool = True,
    limit: int = 1000,
    offset: int = 0,
    cache_ttl_secs: int = 60,
) -> List[Dict[str, Any]]:
    cached = _load_cache(cache_path, cache_ttl_secs)
    if cached is not None:
        return cached

    results: List[Dict[str, Any]] = []
    while True:
        params = {
            "active": str(active).lower(),
            "limit": str(limit),
            "offset": str(offset),
        }
        url = f"{base_url.rstrip('/')}/events?{urlencode(params)}"
        data = _fetch_json(url)
        if isinstance(data, dict):
            markets = data.get("events") or data.get("markets") or data.get("data") or []
        elif isinstance(data, list):
            markets = data
        else:
            raise ValueError(f"gamma_unexpected_shape:{type(data)}")
        if not isinstance(markets, list):
            raise ValueError("gamma_markets_not_list")
        results.extend(_flatten_event_markets(markets))
        if len(markets) < limit:
            break
        offset += limit

    _write_cache(cache_path, results)
    return results


WINDOW_SEC_15M = 900
DEFAULT_BACK_WINDOWS = 2
DEFAULT_FORWARD_WINDOWS = 16
CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "XRP"}


async def discover_15m_crypto_by_slug(
    symbols: List[str],
    now_ts: Optional[int],
    gamma_base_url: str,
    cache_path: Optional[Path],
    cache_ttl_secs: int,
    summary: Dict[str, Any],
    back_windows: int = DEFAULT_BACK_WINDOWS,
    forward_windows: int = DEFAULT_FORWARD_WINDOWS,
    gamma_markets: Optional[List[Dict[str, Any]]] = None,
    clob_candidates: Optional[List["ClobCandidate"]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    from core.clob_discovery import list_clob_candidates_async

    symbols = [symbol.upper() for symbol in symbols if symbol]
    if now_ts is None:
        now_ts = int(time.time())

    slugs_by_symbol = {
        symbol: _generate_15m_slugs(symbol, now_ts, back_windows, forward_windows)
        for symbol in symbols
        if symbol in CRYPTO_SYMBOLS
    }
    all_slugs = sorted({slug for slugs in slugs_by_symbol.values() for slug in slugs})

    summary.setdefault("backend", "slug")
    summary.setdefault("by_symbol", [])
    summary["windows_generated"] = len(all_slugs)
    summary["slugs_queried"] = len(all_slugs)

    markets = gamma_markets
    if markets is None:
        cached = _load_slug_cache(cache_path, cache_ttl_secs, all_slugs)
        if cached is not None:
            markets = cached
        else:
            markets = await _fetch_gamma_markets_for_slugs(
                all_slugs,
                gamma_base_url,
                batch_size=50,
                max_concurrency=4,
                param_name="slug[]",
            )
            if all_slugs and not _filter_markets_by_slugs(markets, all_slugs):
                summary["slug_query_fallback"] = "slug"
                markets = await _fetch_gamma_markets_for_slugs(
                    all_slugs,
                    gamma_base_url,
                    batch_size=50,
                    max_concurrency=4,
                    param_name="slug",
                )
            if all_slugs and not _filter_markets_by_slugs(markets, all_slugs):
                summary["slug_query_fallback"] = "events"
                event_cache_path = cache_path.with_name("cache_gamma_markets.json") if cache_path else None
                markets = await asyncio.to_thread(
                    load_gamma_markets, gamma_base_url, event_cache_path, True, 1000, 0, cache_ttl_secs
                )
            _write_slug_cache(cache_path, all_slugs, markets)

    markets = _filter_markets_by_slugs(markets or [], all_slugs)
    summary["gamma_markets_found"] = len(markets)

    if clob_candidates is None:
        clob_candidates = await list_clob_candidates_async()
    summary["clob_candidates"] = len(clob_candidates)
    clob_by_condition = {candidate.condition_id: candidate for candidate in clob_candidates}

    rejected_counts: Dict[str, int] = {}
    selected_by_symbol: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol in symbols}
    by_condition: Dict[str, Dict[str, Any]] = {}

    for market in markets:
        condition_id = _coerce_str(market.get("conditionId") or market.get("condition_id"))
        if not condition_id:
            _count_reject(rejected_counts, "missing_condition_id")
            continue
        slug = _coerce_str(market.get("slug"))
        if not slug or slug not in all_slugs:
            _count_reject(rejected_counts, "slug_not_in_window")
            continue
        token_ids = _coerce_list(market.get("clobTokenIds"))
        outcomes = _coerce_list(market.get("outcomes"))
        candidate = clob_by_condition.get(condition_id)
        if (len(token_ids) != 2 or len(outcomes) != 2) and candidate is not None:
            if len(token_ids) != 2:
                token_ids = list(candidate.token_ids)
                market["clobTokenIds"] = token_ids
            if len(outcomes) != 2:
                outcomes = list(candidate.outcomes)
                market["outcomes"] = outcomes
        if len(token_ids) != 2:
            _count_reject(rejected_counts, "missing_clob_tokens")
            continue
        if len(outcomes) != 2:
            _count_reject(rejected_counts, "missing_outcomes")
            continue
        if candidate is None:
            _count_reject(rejected_counts, "missing_clob_candidate")
        existing = by_condition.get(condition_id)
        if existing is None or _market_sort_key(market) > _market_sort_key(existing):
            market["clob_candidate"] = candidate is not None
            by_condition[condition_id] = market

    for condition_id, market in by_condition.items():
        symbol = _symbol_from_slug(market.get("slug"))
        if symbol is None or symbol not in symbols:
            _count_reject(rejected_counts, "symbol_mismatch")
            continue
        selected_by_symbol.setdefault(symbol, []).append(market)

    tradable_markets = sum(len(items) for items in selected_by_symbol.values())
    summary["tradable_markets"] = tradable_markets
    summary["identified_15m_crypto"] = tradable_markets
    summary["selected_markets"] = tradable_markets
    summary["rejected_reason_counts"] = rejected_counts

    summary["by_symbol"] = [
        {
            "reference_symbol": symbol,
            "windows_generated": len(slugs_by_symbol.get(symbol, [])),
            "slugs_queried": len(slugs_by_symbol.get(symbol, [])),
            "gamma_markets_found": len(markets),
            "tradable_markets": len(selected_by_symbol.get(symbol, [])),
            "selected_markets": len(selected_by_symbol.get(symbol, [])),
        }
        for symbol in symbols
    ]

    for symbol, markets_list in selected_by_symbol.items():
        markets_list.sort(key=lambda item: _market_sort_key(item))
    return selected_by_symbol


def _generate_15m_slugs(
    symbol: str,
    now_ts: int,
    back_windows: int,
    forward_windows: int,
    window_sec: int = WINDOW_SEC_15M,
) -> List[str]:
    base = (now_ts // window_sec) * window_sec
    slugs = []
    for offset in range(-back_windows, forward_windows + 1):
        ts = base + offset * window_sec
        slugs.append(f"{symbol.lower()}-updown-15m-{ts}")
    return slugs


def _symbol_from_slug(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    prefix = slug.split("-", 1)[0].upper()
    return prefix if prefix in CRYPTO_SYMBOLS else None


async def _fetch_gamma_markets_for_slugs(
    slugs: List[str],
    gamma_base_url: str,
    batch_size: int,
    max_concurrency: int,
    param_name: str,
) -> List[Dict[str, Any]]:
    if not slugs:
        return []
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch(batch: List[str]) -> List[Dict[str, Any]]:
        query = urlencode({param_name: batch}, doseq=True)
        url = f"{gamma_base_url.rstrip('/')}/markets?{query}"
        async with semaphore:
            data = await _fetch_json_async(url)
        return _extract_gamma_markets(data)

    tasks = []
    for idx in range(0, len(slugs), batch_size):
        batch = slugs[idx : idx + batch_size]
        tasks.append(asyncio.create_task(_fetch(batch)))
    results: List[Dict[str, Any]] = []
    for batch_result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(batch_result, Exception):
            continue
        results.extend(batch_result)
    return results


def _extract_gamma_markets(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        markets = data.get("markets") or data.get("data") or []
        return markets if isinstance(markets, list) else []
    if isinstance(data, list):
        return data
    return []


def _filter_markets_by_slugs(
    markets: List[Dict[str, Any]],
    slugs: List[str],
) -> List[Dict[str, Any]]:
    if not markets or not slugs:
        return []
    slug_set = set(slugs)
    filtered: List[Dict[str, Any]] = []
    for market in markets:
        slug = _coerce_str(market.get("slug"))
        if slug and slug in slug_set:
            filtered.append(market)
    return filtered


def _load_slug_cache(
    cache_path: Optional[Path],
    cache_ttl_secs: int,
    slugs: List[str],
) -> Optional[List[Dict[str, Any]]]:
    if not cache_path:
        return None
    if not cache_path.exists():
        return None
    if cache_ttl_secs > 0:
        age = time.time() - cache_path.stat().st_mtime
        if age > cache_ttl_secs:
            return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    cached_slugs = data.get("slugs")
    markets = data.get("markets")
    if not isinstance(cached_slugs, list) or not isinstance(markets, list):
        return None
    if not set(slugs).issubset(set(str(item) for item in cached_slugs)):
        return None
    return markets


def _write_slug_cache(cache_path: Optional[Path], slugs: List[str], markets: List[Dict[str, Any]]) -> None:
    if not cache_path:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": time.time(), "slugs": slugs, "markets": markets}
    cache_path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")


def _count_reject(counter: Dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


async def _fetch_json_async(url: str) -> Any:
    return await asyncio.to_thread(_fetch_json, url)


async def _enrich_fee_metadata(
    asset_meta: Dict[str, Dict[str, Any]],
    fee_client: "FeeRateClient",
    summary: Optional[Dict[str, Any]],
) -> None:
    fee_ok = 0
    fee_unknown = 0
    for token_id, meta in asset_meta.items():
        status, fee_rate = await fee_client.get_fee_metadata_async(token_id)
        meta["fee"] = {"status": status, "fee_rate_bps": fee_rate}
        if status == "ok":
            fee_ok += 1
        elif status == "not_fee_addressable":
            pass
        else:
            fee_unknown += 1
    if summary is not None:
        summary["fee_ok_count"] = fee_ok
        summary["fee_invalid_token_count"] = fee_client.invalid_token_id_count
        summary["fee_unknown_count"] = fee_unknown

def select_latest_by_prefix(
    markets: Iterable[Dict[str, Any]],
    prefix: str,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    if now_ts is None:
        now_ts = int(time.time())
    for market in markets:
        if not _is_active_event(market, now_ts):
            continue
        slug = _coerce_str(market.get("slug"))
        if not slug or not slug.startswith(prefix):
            continue
        token_ids = _coerce_list(market.get("clobTokenIds"))
        if not token_ids:
            continue
        slug_ts = _parse_slug_ts(slug)
        fallback_ts = _parse_market_timestamp(market)
        sort_key = slug_ts or fallback_ts or 0
        candidates.append((sort_key, market))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def select_latest_by_fee_rate(
    markets: Iterable[Dict[str, Any]],
    reference_symbol: Optional[str],
    fee_rate_fetcher: Optional[Any] = None,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if fee_rate_fetcher is None:
        fee_rate_fetcher = _fetch_fee_rate_bps
    if now_ts is None:
        now_ts = int(time.time())
    candidates: List[Tuple[Tuple[int, str], Dict[str, Any]]] = []
    for market in _sorted_markets(markets):
        if not _is_active_event(market, now_ts):
            continue
        if reference_symbol and not _matches_reference_symbol(market, reference_symbol):
            continue
        token_ids = _coerce_list(market.get("clobTokenIds"))
        if not token_ids:
            continue
        fee_rates = []
        for token_id in token_ids:
            fee_rate_bps = fee_rate_fetcher(token_id)
            if fee_rate_bps is None:
                continue
            if fee_rate_bps > 0:
                fee_rates.append((token_id, fee_rate_bps))
                _log_fee_rate_hit(market, token_id, fee_rate_bps)
        if not fee_rates:
            continue
        sort_key = _market_sort_key(market)
        candidates.append((sort_key, market))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _parse_slug_ts(slug: str) -> Optional[int]:
    if not slug:
        return None
    last = slug.rsplit("-", 1)[-1]
    if last.isdigit():
        return int(last)
    return None


def _parse_market_timestamp(market: Dict[str, Any]) -> Optional[int]:
    for key in ("startDate", "start_date", "createdAt", "created_at"):
        if key in market:
            value = market.get(key)
            parsed = _parse_timestamp_value(value)
            if parsed is not None:
                return parsed
    return None


def _flatten_event_markets(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    saw_event = False
    for item in items:
        markets = item.get("markets")
        if isinstance(markets, list):
            saw_event = True
            for market in markets:
                if not isinstance(market, dict):
                    continue
                entry = dict(market)
                if "closed" not in entry and "closed" in item:
                    entry["closed"] = item.get("closed")
                if "endDate" not in entry and "endDate" in item:
                    entry["endDate"] = item.get("endDate")
                if "endTime" not in entry and "endTime" in item:
                    entry["endTime"] = item.get("endTime")
                flattened.append(entry)
        else:
            flattened.append(item)
    return flattened if saw_event else items


def _matches_reference_symbol(market: Dict[str, Any], symbol: str) -> bool:
    target = symbol.strip().upper()
    if not target:
        return True
    for key in ("question", "title", "name"):
        value = market.get(key)
        if value and target in str(value).upper():
            return True
    return False


def _sorted_markets(markets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        list(markets),
        key=lambda item: _market_sort_key(item),
    )


def _market_sort_key(market: Dict[str, Any]) -> Tuple[int, str]:
    slug = _coerce_str(market.get("slug")) or ""
    condition_id = _coerce_str(market.get("conditionId") or market.get("condition_id")) or ""
    end_ts = _parse_event_end_ts(market) or 0
    slug_ts = _parse_slug_ts(slug) or 0
    created_ts = _parse_market_timestamp(market) or 0
    return (max(end_ts, slug_ts, created_ts), condition_id)


def _log_fee_rate_hit(market: Dict[str, Any], token_id: str, fee_rate_bps: float) -> None:
    slug = _coerce_str(market.get("slug"))
    condition_id = _coerce_str(market.get("conditionId") or market.get("condition_id"))
    print(
        "fee_rate_market",
        {
            "slug": slug,
            "conditionId": condition_id,
            "token_id": token_id,
            "fee_rate_bps": fee_rate_bps,
        },
    )


def _is_active_event(event: Dict[str, Any], now_ts: int) -> bool:
    closed = event.get("closed")
    if closed is not None and bool(closed):
        return False
    end_ts = _parse_event_end_ts(event)
    if end_ts is None:
        return False
    return end_ts > now_ts


def _parse_event_end_ts(event: Dict[str, Any]) -> Optional[int]:
    for key in ("endTime", "end_time", "endDate", "end_date", "resolutionTime", "resolution_time"):
        if key in event:
            parsed = _parse_timestamp_value(event.get(key))
            if parsed is not None:
                return parsed
    return None


def _parse_timestamp_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 1_000_000_000_000:
            return int(value)
        return int(value / 1000)
    if isinstance(value, str):
        if value.isdigit():
            return _parse_timestamp_value(int(value))
        try:
            from datetime import datetime

            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def _load_resolved_markets_v1(
    data: Dict[str, Any],
) -> Tuple[List[ResolvedMarket], Dict[str, Dict[str, Any]]]:
    markets_data = data.get("markets", []) if isinstance(data, dict) else []
    resolved: List[ResolvedMarket] = []
    asset_meta: Dict[str, Dict[str, Any]] = {}
    for entry in markets_data:
        tokens = entry.get("tokens", []) or []
        token_ids = [str(token.get("token_id")) for token in tokens if token.get("token_id")]
        outcomes = [str(outcome) for outcome in entry.get("outcomes", [])]
        outcome_by_token = {str(token.get("token_id")): str(token.get("outcome")) for token in tokens if token}
        token_by_outcome = {outcome: token for token, outcome in outcome_by_token.items()}
        constraints = entry.get("constraints") or {}
        market = ResolvedMarket(
            name=str(entry.get("name", "")),
            reference_symbol=str(entry.get("reference_symbol", "")),
            slug_prefix=None,
            slug=entry.get("market_slug"),
            condition_id=str(entry.get("condition_id", "")),
            token_ids=token_ids,
            outcomes=outcomes,
            outcome_by_token=outcome_by_token,
            token_by_outcome=token_by_outcome,
            question=entry.get("question"),
            min_tick=float(constraints.get("min_tick", 0.01)),
            min_size=float(constraints.get("min_size", 1.0)),
            min_price=float(constraints.get("min_price", 0.01)),
            max_price=float(constraints.get("max_price", 0.99)),
        )
        resolved.append(market)
        meta = {
            "slug": market.slug,
            "condition_id": market.condition_id,
            "token_ids": market.token_ids,
            "outcomes": market.outcomes,
            "outcome_by_token": market.outcome_by_token,
            "token_by_outcome": market.token_by_outcome,
            "question": market.question,
            "reference_symbol": market.reference_symbol,
            "name": market.name,
        }
        for token_id in market.token_ids:
            token_meta = dict(meta)
            token_meta["token_id"] = token_id
            token_meta["outcome"] = market.outcome_by_token.get(token_id)
            asset_meta[token_id] = token_meta
    return resolved, asset_meta


def _load_resolved_markets_legacy(
    data: Any,
) -> Tuple[List[ResolvedMarket], Dict[str, Dict[str, Any]]]:
    markets_data = data.get("markets", []) if isinstance(data, dict) else []
    resolved: List[ResolvedMarket] = []
    asset_meta: Dict[str, Dict[str, Any]] = {}
    for entry in markets_data:
        token_ids = [str(token) for token in entry.get("token_ids", [])]
        outcomes = [str(outcome) for outcome in entry.get("outcomes", [])]
        outcome_by_token = entry.get("outcome_by_token") or dict(zip(token_ids, outcomes))
        token_by_outcome = entry.get("token_by_outcome") or {
            outcome: token for token, outcome in outcome_by_token.items()
        }
        market = ResolvedMarket(
            name=str(entry.get("name", "")),
            reference_symbol=str(entry.get("reference_symbol", "")),
            slug_prefix=entry.get("slug_prefix"),
            slug=entry.get("slug"),
            condition_id=str(entry.get("condition_id", "")),
            token_ids=token_ids,
            outcomes=outcomes,
            outcome_by_token=outcome_by_token,
            token_by_outcome=token_by_outcome,
            question=entry.get("question"),
            min_tick=float(entry.get("min_tick", 0.01)),
            min_size=float(entry.get("min_size", 1.0)),
            min_price=float(entry.get("min_price", 0.01)),
            max_price=float(entry.get("max_price", 0.99)),
        )
        resolved.append(market)
        meta = {
            "slug": market.slug,
            "condition_id": market.condition_id,
            "token_ids": market.token_ids,
            "outcomes": market.outcomes,
            "outcome_by_token": market.outcome_by_token,
            "token_by_outcome": market.token_by_outcome,
            "question": market.question,
            "reference_symbol": market.reference_symbol,
            "name": market.name,
        }
        for token_id in market.token_ids:
            token_meta = dict(meta)
            token_meta["token_id"] = token_id
            token_meta["outcome"] = market.outcome_by_token.get(token_id)
            asset_meta[token_id] = token_meta
    return resolved, asset_meta


def _load_cache(cache_path: Optional[PathLike], cache_ttl_secs: int) -> Optional[List[Dict[str, Any]]]:
    if not cache_path:
        return None

    path = cache_path if isinstance(cache_path, Path) else Path(cache_path)
    if not path.exists():
        return None

    if cache_ttl_secs > 0:
        age = time.time() - path.stat().st_mtime
        if age > cache_ttl_secs:
            return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if isinstance(data, dict):
        markets = data.get("events") or data.get("markets") or data.get("data")
        if not isinstance(markets, list):
            return None
        return _flatten_event_markets(markets)
    if isinstance(data, list):
        return _flatten_event_markets(data)
    return None


def _write_cache(cache_path: Optional[PathLike], markets: List[Dict[str, Any]]) -> None:
    if not cache_path:
        return
    path = cache_path if isinstance(cache_path, Path) else Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": time.time(), "markets": markets}
    path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )


def _fetch_fee_rate_bps(token_id: str) -> Optional[float]:
    url = f"https://clob.polymarket.com/fee-rate?token_id={token_id}"
    data = _fetch_json(url)
    if not isinstance(data, dict):
        return None
    value = data.get("fee_rate_bps")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_json(url: str):
    import json
    import ssl
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()

    max_attempts = 4
    base_backoff = 0.25
    for attempt in range(max_attempts):
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=10, context=ctx) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as exc:
            status = getattr(exc, "code", None)
            if status in {403, 429} or (status is not None and 500 <= status < 600):
                if attempt < max_attempts - 1:
                    backoff = base_backoff * (2**attempt)
                    jitter = _FETCH_RNG.uniform(0.0, backoff * 0.2)
                    _FETCH_SLEEP(backoff + jitter)
                    continue
                raise ValueError(f"gamma_fetch_failed status={status} url={url}") from exc
            raise ValueError(f"gamma_fetch_failed status={status} url={url}") from exc
        except Exception as exc:
            if attempt < max_attempts - 1:
                backoff = base_backoff * (2**attempt)
                jitter = _FETCH_RNG.uniform(0.0, backoff * 0.2)
                _FETCH_SLEEP(backoff + jitter)
                continue
            raise ValueError(f"gamma_fetch_failed status=unknown url={url}") from exc



def _needs_discovery(market: MarketConfig) -> bool:
    token_ids = [token for token in market.token_ids if token]
    return not market.condition_id or not token_ids


def _discovery_required_message(market: MarketConfig) -> str:
    name = market.name or "<unnamed>"
    return (
        f"market:{name} missing condition_id/token_ids. "
        "Provide token_ids/condition_id or enable --auto_discover."
    )


def _slug_prefix_missing_message(market: MarketConfig) -> str:
    name = market.name or "<unnamed>"
    return f"market:{name} missing slug_prefix required for prefix-based auto_discover"


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    value_str = str(value)
    return value_str if value_str else None


def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return []
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item)]
    return []
