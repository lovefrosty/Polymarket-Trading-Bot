from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode


CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

_CRYPTO_SYMBOLS = {"BTC": ["BTC", "BITCOIN"], "ETH": ["ETH", "ETHEREUM"], "SOL": ["SOL", "SOLANA"], "XRP": ["XRP", "RIPPLE"]}
_SLUG_RE = re.compile(r"^(btc|eth|sol|xrp)-updown-15m-\\d+$")


@dataclass(frozen=True)
class ClobCandidate:
    condition_id: str
    token_ids: List[str]
    outcomes: List[str]
    prices: List[Optional[float]]
    accepting_orders: Optional[bool]
    active: Optional[bool]
    closed: Optional[bool]
    archived: Optional[bool]


@dataclass(frozen=True)
class FeeRateResult:
    token_id: str
    fee_rate_bps: float


def list_clob_candidates(
    source: str = "sampling-markets",
    fallback: str = "simplified-markets",
    base_url: str = CLOB_BASE_URL,
) -> List[ClobCandidate]:
    candidates: List[ClobCandidate] = []
    cursor: Optional[str] = None
    while True:
        params = {"next_cursor": cursor} if cursor else None
        data = _fetch_clob_endpoint(base_url, source, params=params)
        payload = _extract_payload(data)
        if payload is None and fallback:
            data = _fetch_clob_endpoint(base_url, fallback, params=params)
            payload = _extract_payload(data)
        if payload is None:
            raise ValueError("clob_candidate_payload_missing")
        for entry in payload:
            candidate = _parse_candidate(entry)
            if candidate is None:
                continue
            candidates.append(candidate)
        cursor = _extract_next_cursor(data)
        if not cursor:
            break
    candidates.sort(key=lambda item: item.condition_id)
    return candidates


async def list_clob_candidates_async(
    source: str = "sampling-markets",
    fallback: str = "simplified-markets",
    base_url: str = CLOB_BASE_URL,
) -> List[ClobCandidate]:
    candidates: List[ClobCandidate] = []
    cursor: Optional[str] = None
    while True:
        params = {"next_cursor": cursor} if cursor else None
        data = await _fetch_clob_endpoint_async(base_url, source, params=params)
        payload = _extract_payload(data)
        if payload is None and fallback:
            data = await _fetch_clob_endpoint_async(base_url, fallback, params=params)
            payload = _extract_payload(data)
        if payload is None:
            raise ValueError("clob_candidate_payload_missing")
        for entry in payload:
            candidate = _parse_candidate(entry)
            if candidate is None:
                continue
            candidates.append(candidate)
        cursor = _extract_next_cursor(data)
        if not cursor:
            break
    candidates.sort(key=lambda item: item.condition_id)
    return candidates


class FeeRateClient:
    def __init__(
        self,
        base_url: str = CLOB_BASE_URL,
        ttl_secs: int = 300,
        timeout_secs: int = 5,
        max_concurrency: int = 10,
        fetcher: Optional[Any] = None,
        time_fn: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url
        self.ttl_secs = ttl_secs
        self.timeout_secs = timeout_secs
        self._fetcher = fetcher or _fetch_json
        self._time_fn = time_fn or time.time
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, float]] = {}
        self._meta_cache: Dict[str, Tuple[Optional[float], str, float]] = {}
        self._semaphore = threading.Semaphore(max_concurrency)
        self.invalid_token_id_count = 0

    def get_fee_rate_bps(self, token_id: str) -> Optional[float]:
        now = self._time_fn()
        with self._lock:
            cached = self._cache.get(token_id)
            if cached is not None:
                value, ts = cached
                if now - ts <= self.ttl_secs:
                    return value
        with self._semaphore:
            url = f"{self.base_url.rstrip('/')}/fee-rate?{urlencode({'token_id': token_id})}"
            data = self._fetcher(url, timeout=self.timeout_secs)
        if not isinstance(data, dict):
            return None
        error = data.get("error")
        if isinstance(error, str) and error.strip().lower() == "invalid token id":
            with self._lock:
                self.invalid_token_id_count += 1
            return None
        value = data.get("fee_rate_bps")
        try:
            fee_rate = float(value)
        except (TypeError, ValueError):
            return None
        with self._lock:
            self._cache[token_id] = (fee_rate, now)
        return fee_rate

    async def get_fee_rate_bps_async(self, token_id: str) -> Optional[float]:
        return await asyncio.to_thread(self.get_fee_rate_bps, token_id)

    def get_fee_metadata(self, token_id: str) -> Tuple[str, Optional[float]]:
        now = self._time_fn()
        with self._lock:
            cached = self._meta_cache.get(token_id)
            if cached is not None:
                fee_rate, status, ts = cached
                if now - ts <= self.ttl_secs:
                    return status, fee_rate
        with self._semaphore:
            url = f"{self.base_url.rstrip('/')}/fee-rate?{urlencode({'token_id': token_id})}"
            data = self._fetcher(url, timeout=self.timeout_secs)
        status = "unknown"
        fee_rate: Optional[float] = None
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, str) and error.strip().lower() == "invalid token id":
                status = "not_fee_addressable"
                with self._lock:
                    self.invalid_token_id_count += 1
            else:
                value = data.get("fee_rate_bps")
                try:
                    fee_rate = float(value)
                    status = "ok"
                except (TypeError, ValueError):
                    status = "unknown"
        with self._lock:
            self._meta_cache[token_id] = (fee_rate, status, now)
        return status, fee_rate

    async def get_fee_metadata_async(self, token_id: str) -> Tuple[str, Optional[float]]:
        return await asyncio.to_thread(self.get_fee_metadata, token_id)


async def discover_fee_enabled_markets(
    reference_symbol: str,
    selection_regex: Optional[str],
    allow_unknown_symbol: bool,
    gamma_base_url: str = GAMMA_BASE_URL,
    fee_client: Optional[FeeRateClient] = None,
    candidates: Optional[List[ClobCandidate]] = None,
    gamma_markets: Optional[List[Dict[str, Any]]] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    fee_client = fee_client or FeeRateClient()
    if candidates is None:
        candidates = await list_clob_candidates_async()
    if not candidates:
        raise ValueError("no_clob_candidates_found")
    condition_ids = [candidate.condition_id for candidate in candidates if candidate.condition_id]
    if gamma_markets is None:
        gamma_by_condition = await _fetch_gamma_by_condition_ids(condition_ids, gamma_base_url)
    else:
        gamma_by_condition = _index_gamma_markets(gamma_markets)

    if summary is not None:
        summary["reference_symbol"] = reference_symbol
        summary["clob_candidates"] = len(candidates)
        summary["fee_enabled"] = 0
        summary["identified_15m_crypto"] = 0
        summary["selected_markets"] = 0
        summary["rejected_unknown_symbol"] = 0
        summary["invalid_token_ids"] = 0

    fee_enabled: List[Tuple[ClobCandidate, List[FeeRateResult], Dict[str, Any]]] = []
    for candidate in candidates:
        gamma = gamma_by_condition.get(candidate.condition_id)
        gamma_token_ids = _extract_clob_token_ids(gamma)
        if not gamma_token_ids:
            continue
        fee_results: List[FeeRateResult] = []
        for token_id in gamma_token_ids:
            fee_rate = await fee_client.get_fee_rate_bps_async(token_id)
            if fee_rate is None:
                continue
            if fee_rate > 0:
                fee_results.append(FeeRateResult(token_id=token_id, fee_rate_bps=fee_rate))
        if fee_results:
            fee_enabled.append((candidate, fee_results, gamma))
            _log_fee_rate(candidate, fee_results)

    if summary is not None:
        summary["fee_enabled"] = len(fee_enabled)
        summary["invalid_token_ids"] = fee_client.invalid_token_id_count

    if not fee_enabled:
        raise ValueError("no_fee_enabled_markets_found")

    discovered: List[Dict[str, Any]] = []
    identified_count = 0
    for candidate, fee_results, gamma in fee_enabled:
        meta = _merge_candidate(candidate, gamma)
        market_symbol = _extract_symbol(meta.get("slug"), meta.get("question"))
        if market_symbol is None:
            if allow_unknown_symbol:
                market_symbol = reference_symbol
            else:
                if summary is not None:
                    summary["rejected_unknown_symbol"] += 1
                continue
        if market_symbol != reference_symbol:
            continue
        if not _is_15m_crypto(meta, selection_regex):
            continue
        identified_count += 1
        meta["reference_symbol"] = market_symbol
        meta["fee_rate_bps"] = max(entry.fee_rate_bps for entry in fee_results)
        discovered.append(meta)

    if summary is not None:
        summary["identified_15m_crypto"] = identified_count
        summary["selected_markets"] = len(discovered)

    if not discovered:
        raise ValueError(f"no_markets_found_for_reference_symbol:{reference_symbol}")

    discovered.sort(key=lambda item: _market_sort_key(item))
    return discovered


def enrich_with_gamma(condition_id: str, gamma_base_url: str = GAMMA_BASE_URL) -> Optional[Dict[str, Any]]:
    if not condition_id:
        return None
    url = f"{gamma_base_url.rstrip('/')}/markets?{urlencode({'condition_ids[]': condition_id})}"
    try:
        data = _fetch_json(url, timeout=10)
    except Exception:
        return None
    markets = _extract_gamma_markets(data)
    if not markets:
        return None
    return markets[0]


async def enrich_with_gamma_async(
    condition_id: str, gamma_base_url: str = GAMMA_BASE_URL
) -> Optional[Dict[str, Any]]:
    if not condition_id:
        return None
    url = f"{gamma_base_url.rstrip('/')}/markets?{urlencode({'condition_ids[]': condition_id})}"
    try:
        data = await _fetch_json_async(url, timeout=10)
    except Exception:
        return None
    markets = _extract_gamma_markets(data)
    if not markets:
        return None
    return markets[0]


async def _fetch_gamma_by_condition_ids(
    condition_ids: List[str],
    gamma_base_url: str,
    batch_size: int = 50,
) -> Dict[str, Dict[str, Any]]:
    unique_ids = [cid for cid in dict.fromkeys(condition_ids) if cid]
    results: Dict[str, Dict[str, Any]] = {}
    for idx in range(0, len(unique_ids), batch_size):
        batch = unique_ids[idx : idx + batch_size]
        query = urlencode({"condition_ids[]": batch}, doseq=True)
        url = f"{gamma_base_url.rstrip('/')}/markets?{query}"
        try:
            data = await _fetch_json_async(url, timeout=10)
        except Exception:
            continue
        markets = _extract_gamma_markets(data)
        for market in markets:
            condition_id = _extract_condition_id(market)
            if condition_id and condition_id not in results:
                results[condition_id] = market
    return results


def _index_gamma_markets(markets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for market in markets:
        condition_id = _extract_condition_id(market)
        if condition_id and condition_id not in results:
            results[condition_id] = market
    return results


def _extract_condition_id(market: Dict[str, Any]) -> Optional[str]:
    condition_id = market.get("conditionId") or market.get("condition_id")
    if not condition_id:
        return None
    return str(condition_id)


def _extract_gamma_markets(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        markets = data.get("markets") or data.get("data") or []
        return markets if isinstance(markets, list) else []
    if isinstance(data, list):
        return data
    return []


def _merge_candidate(candidate: ClobCandidate, gamma: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entry = {
        "conditionId": candidate.condition_id,
        "clobTokenIds": candidate.token_ids,
        "outcomes": candidate.outcomes,
        "question": None,
        "slug": None,
        "startDate": None,
        "endDate": None,
    }
    if not gamma:
        return entry
    entry["question"] = gamma.get("question") or gamma.get("title")
    entry["slug"] = gamma.get("slug")
    entry["startDate"] = gamma.get("startDate") or gamma.get("start_date")
    entry["endDate"] = gamma.get("endDate") or gamma.get("end_date")
    gamma_tokens = gamma.get("clobTokenIds")
    if gamma_tokens and isinstance(gamma_tokens, list):
        entry["clobTokenIds"] = [str(token) for token in gamma_tokens if str(token)]
    gamma_outcomes = gamma.get("outcomes")
    if gamma_outcomes and isinstance(gamma_outcomes, list):
        entry["outcomes"] = [str(outcome) for outcome in gamma_outcomes if str(outcome)]
    if gamma_tokens and gamma_tokens != candidate.token_ids:
        print(
            "gamma_token_mismatch",
            {"conditionId": candidate.condition_id, "clobTokenIds": gamma_tokens, "clob_discovery": candidate.token_ids},
        )
    return entry


def _is_15m_crypto(meta: Dict[str, Any], selection_regex: Optional[str]) -> bool:
    slug = meta.get("slug")
    question = meta.get("question")
    if slug and _SLUG_RE.match(str(slug)):
        return True
    if question and _matches_question_pattern(str(question)):
        return True
    if _matches_duration(meta):
        return True
    if selection_regex:
        target = f"{slug or ''} {question or ''} {meta.get('conditionId') or ''}"
        return re.search(selection_regex, target) is not None
    return False


def _matches_question_pattern(question: str) -> bool:
    upper = question.upper()
    if "UP" not in upper or "DOWN" not in upper:
        return False
    if "15" not in upper:
        return False
    for keywords in _CRYPTO_SYMBOLS.values():
        if any(word in upper for word in keywords):
            return True
    return False


def _matches_duration(meta: Dict[str, Any]) -> bool:
    start = _parse_timestamp_value(meta.get("startDate"))
    end = _parse_timestamp_value(meta.get("endDate"))
    if start is None or end is None or end <= start:
        return False
    duration = end - start
    return 14 * 60 <= duration <= 16 * 60


def _extract_symbol(slug: Optional[str], question: Optional[str]) -> Optional[str]:
    if slug:
        prefix = slug.split("-", 1)[0].upper()
        if prefix in _CRYPTO_SYMBOLS:
            return prefix
    if question:
        upper = question.upper()
        for symbol, keywords in _CRYPTO_SYMBOLS.items():
            if any(word in upper for word in keywords):
                return symbol
    return None


def _market_sort_key(meta: Dict[str, Any]) -> Tuple[int, str]:
    end_ts = _parse_timestamp_value(meta.get("endDate")) or 0
    slug_ts = _parse_slug_ts(meta.get("slug") or "") or 0
    return (max(end_ts, slug_ts), meta.get("conditionId") or "")


def _parse_candidate(entry: Dict[str, Any]) -> Optional[ClobCandidate]:
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
    token_ids = []
    outcomes = []
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
    return ClobCandidate(
        condition_id=str(condition_id),
        token_ids=token_ids,
        outcomes=outcomes,
        prices=prices,
        accepting_orders=accepting_orders if accepting_orders is not None else None,
        active=active if active is not None else None,
        closed=closed if closed is not None else None,
        archived=archived if archived is not None else None,
    )


def _extract_payload(data: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(data, dict):
        payload = data.get("data")
        return payload if isinstance(payload, list) else None
    if isinstance(data, list):
        return data
    return None


def _extract_next_cursor(data: Any) -> Optional[str]:
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

        decoded = base64.b64decode(cursor_str).decode("utf-8")
        if decoded == "-1":
            return None
    except Exception:
        pass
    return cursor_str


def _extract_clob_token_ids(market: Optional[Dict[str, Any]]) -> List[str]:
    if not market:
        return []
    tokens = market.get("clobTokenIds")
    if isinstance(tokens, list):
        return [str(token) for token in tokens if str(token)]
    return []


def _fetch_clob_endpoint(
    base_url: str, endpoint: str, params: Optional[Dict[str, Any]] = None
) -> Any:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    if params:
        query = urlencode({key: str(value) for key, value in params.items() if value is not None})
        if query:
            url = f"{url}?{query}"
    return _fetch_json(url, timeout=5)


async def _fetch_clob_endpoint_async(
    base_url: str, endpoint: str, params: Optional[Dict[str, Any]] = None
) -> Any:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    if params:
        query = urlencode({key: str(value) for key, value in params.items() if value is not None})
        if query:
            url = f"{url}?{query}"
    return await _fetch_json_async(url, timeout=5)


def _fetch_json(url: str, timeout: int = 5) -> Any:
    import ssl
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
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


async def _fetch_json_async(url: str, timeout: int = 5) -> Any:
    return await asyncio.to_thread(_fetch_json, url, timeout)


def _parse_slug_ts(slug: str) -> Optional[int]:
    last = slug.rsplit("-", 1)[-1]
    if last.isdigit():
        return int(last)
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


def _log_fee_rate(candidate: ClobCandidate, fee_results: List[FeeRateResult]) -> None:
    for entry in fee_results:
        print(
            "fee_rate_market",
            {
                "conditionId": candidate.condition_id,
                "token_id": entry.token_id,
                "fee_rate_bps": entry.fee_rate_bps,
            },
        )
