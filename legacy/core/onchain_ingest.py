from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.event_tape import EventTape
from core.metrics import Metrics
from core.onchain_signals import OnchainSignalState


CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEGRISK_CTF_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"


@dataclass(frozen=True)
class OnchainIngestConfig:
    rpc_http_url: Optional[str]
    rpc_ws_url: Optional[str]
    ctf_exchange_abi_path: str = "abis/ctf_exchange.json"
    ctf_abi_path: str = "abis/ctf.json"
    negrisk_abi_path: str = "abis/negrisk_ctf_exchange.json"
    use_ws: bool = True
    mode: Optional[str] = None
    poll_reconcile_secs: float = 30.0
    ws_loop_sleep_secs: float = 0.2
    heartbeat_secs: float = 2.0
    dedupe_lru_size: int = 5000
    reconcile_block_lookback: int = 20
    recreate_filter_after_secs: float = 30.0
    source: str = "rpc"
    log_level: str = "INFO"


@dataclass
class _FilterHandle:
    event_name: str
    event: Any
    filt: Any


class _LRUDeduper:
    def __init__(self, max_size: int) -> None:
        self._max_size = max(1, max_size)
        self._data: OrderedDict[str, None] = OrderedDict()

    def add(self, key: Optional[str]) -> bool:
        if key is None:
            return True
        if key in self._data:
            self._data.move_to_end(key)
            return False
        self._data[key] = None
        if len(self._data) > self._max_size:
            self._data.popitem(last=False)
        return True


class OnchainIngestor:
    def __init__(
        self,
        tape: EventTape,
        config: OnchainIngestConfig,
        signal_state: Optional[OnchainSignalState] = None,
        metrics: Optional[Metrics] = None,
    ) -> None:
        self._tape = tape
        self._config = config
        self._signal_state = signal_state
        self._metrics = metrics
        self._deduper = _LRUDeduper(config.dedupe_lru_size)
        self._heartbeat_secs = max(1.0, config.heartbeat_secs)
        self._last_event_mono_ns = time.monotonic_ns()
        self._last_event_wall_ms: Optional[int] = None
        self._last_block_number: Optional[int] = None
        self._block_hashes: OrderedDict[int, str] = OrderedDict()
        self._ws_connected = False
        self._chain_id: Optional[int] = None
        self._events_ingested = 0
        self._duplicates_dropped = 0
        self._reconnects = 0
        self._errors = 0
        self._abi_missing_logged: Set[Tuple[str, str]] = set()

    async def run(self, stop_event: asyncio.Event) -> None:
        self._tape.write(
            channel="onchain",
            event_type="startup",
            market=None,
            asset_id=None,
            t_event_ms=None,
            raw={
                "code": "onchain_start",
                "use_ws": self._config.use_ws,
                "rpc_http_set": bool(self._config.rpc_http_url),
                "rpc_ws_set": bool(self._config.rpc_ws_url),
            },
            source=self._config.source,
        )
        try:
            ws_web3 = await self._init_web3(self._config.rpc_ws_url)
            http_web3 = await self._init_web3(self._config.rpc_http_url)
            if ws_web3 is None and http_web3 is None:
                self._write_error("web3_init_failed", "missing_rpc_clients")
                return

            ws_contracts = self._build_contracts(ws_web3) if ws_web3 else []
            http_contracts = self._build_contracts(http_web3) if http_web3 else []
            if not ws_contracts and not http_contracts:
                self._write_error("filter_create_failed", "no_contracts_loaded")
                return

            mode = (self._config.mode or "").lower()
            use_ws = self._config.use_ws
            if mode == "poll":
                use_ws = False
            elif mode == "ws":
                use_ws = True

            if not use_ws:
                if http_web3 is None:
                    self._write_error("web3_init_failed", "poll_mode_requires_http")
                    return
                await self._polling_loop(http_web3, http_contracts, stop_event)
                return

            if ws_web3 is None:
                self._write_error("web3_init_failed", "ws_unavailable_falling_back")
                if http_web3 is not None:
                    await self._polling_loop(http_web3, http_contracts, stop_event)
                return

            reconcile_task = None
            if http_web3 is not None:
                reconcile_task = asyncio.create_task(
                    self._reconcile_loop(http_web3, http_contracts, stop_event)
                )

            fallback_to_poll = False
            try:
                fallback_to_poll = await self._ws_loop(ws_web3, ws_contracts, stop_event)
            finally:
                if reconcile_task is not None:
                    reconcile_task.cancel()
                    await asyncio.gather(reconcile_task, return_exceptions=True)

            if fallback_to_poll and http_web3 is not None and not stop_event.is_set():
                await self._polling_loop(http_web3, http_contracts, stop_event)
        except Exception as exc:
            self._write_error("onchain_loop_error", f"run_exception:{exc}")

    async def _ws_loop(
        self, web3: Any, contracts: List[Tuple[str, Any]], stop_event: asyncio.Event
    ) -> bool:
        filters = await asyncio.to_thread(self._create_filters, contracts)
        if not filters:
            self._write_error("filter_create_failed", "no_filters_created")
            return False
        self._ws_connected = True
        last_recreate = time.monotonic()
        error_streak = 0
        outage_start: Optional[float] = None

        while not stop_event.is_set():
            processed, had_error = await asyncio.to_thread(self._drain_filters_once, filters)
            now = time.monotonic()
            sleep_secs = self._config.ws_loop_sleep_secs

            if had_error:
                error_streak += 1
                if outage_start is None:
                    outage_start = now
                backoff = min(10.0, 2.0 ** max(0, error_streak - 1))
                sleep_secs = max(sleep_secs, backoff)
                self._reconnects += 1
                self._ws_connected = False
                web3 = await self._init_web3(self._config.rpc_ws_url)
                if web3 is None:
                    self._write_error("web3_init_failed", "ws_reconnect_failed")
                else:
                    contracts = self._build_contracts(web3)
                    filters = await asyncio.to_thread(self._create_filters, contracts)
                    if not filters:
                        self._write_error("filter_create_failed", "reconnect_failed")
                    else:
                        self._ws_connected = True
                        last_recreate = now

                if outage_start is not None and now - outage_start >= self._config.recreate_filter_after_secs:
                    self._write_error("subscription_loop_error", "ws_outage_fallback_to_poll")
                    self._ws_connected = False
                    return True
            else:
                error_streak = 0
                outage_start = None

            if now - last_recreate >= self._config.recreate_filter_after_secs:
                self._reconnects += 1
                self._log("INFO", "recreating_filters")
                self._ws_connected = False
                web3 = await self._init_web3(self._config.rpc_ws_url)
                if web3 is None:
                    self._write_error("web3_init_failed", "ws_reconnect_failed")
                    return True
                contracts = self._build_contracts(web3)
                filters = await asyncio.to_thread(self._create_filters, contracts)
                if not filters:
                    self._write_error("filter_create_failed", "recreate_failed")
                    return True
                self._ws_connected = True
                last_recreate = now
                error_streak = 0

            self._emit_heartbeat_if_needed()
            await asyncio.sleep(sleep_secs)
        return False

    async def _polling_loop(self, web3: Any, contracts: List[Tuple[str, Any]], stop_event: asyncio.Event) -> None:
        try:
            last_block = await asyncio.to_thread(lambda: web3.eth.block_number)
        except Exception as exc:
            self._write_error("web3_init_failed", f"block_number_failed:{exc}")
            return

        while not stop_event.is_set():
            try:
                latest_block = await asyncio.to_thread(lambda: web3.eth.block_number)
            except Exception as exc:
                self._write_error("reconcile_error", f"block_number_failed:{exc}")
                await asyncio.sleep(self._config.poll_reconcile_secs)
                continue

            if latest_block > last_block:
                await self._ingest_range(contracts, last_block + 1, latest_block)
                last_block = latest_block

            self._emit_heartbeat_if_needed()
            await asyncio.sleep(self._config.poll_reconcile_secs)

    async def _reconcile_loop(self, web3: Any, contracts: List[Tuple[str, Any]], stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(self._config.poll_reconcile_secs)
            try:
                latest_block = await asyncio.to_thread(lambda: web3.eth.block_number)
            except Exception as exc:
                self._write_error("reconcile_error", f"block_number_failed:{exc}")
                continue
            lookback = max(1, int(self._config.reconcile_block_lookback))
            start = max(0, latest_block - lookback + 1)
            max_span = 250
            span = latest_block - start
            if span > max_span:
                original_start = start
                start = max(0, latest_block - max_span)
                self._write_warning(
                    "reconcile_window_clamped",
                    {
                        "original_from_block": original_start,
                        "clamped_from_block": start,
                        "latest_block": latest_block,
                        "max_span": max_span,
                    },
                )
            for event_name, event in contracts:
                logs = await self._safe_get_logs(
                    event_name,
                    event,
                    start,
                    latest_block,
                    max_splits=5,
                )
                for entry in logs:
                    self._handle_event(event_name, entry)

    async def _ingest_range(
        self,
        contracts: List[Tuple[str, Any]],
        start_block: int,
        end_block: int,
    ) -> None:
        if start_block > end_block:
            return
        max_range = max(1, self._config.reconcile_block_lookback)
        block = start_block
        while block <= end_block:
            chunk_end = min(end_block, block + max_range - 1)
            for event_name, event in contracts:
                logs = await self._safe_get_logs(
                    event_name,
                    event,
                    block,
                    chunk_end,
                    max_splits=3,
                )
                for entry in logs:
                    self._handle_event(event_name, entry)
            block = chunk_end + 1

    async def _safe_get_logs(
        self,
        event_name: str,
        event: Any,
        start_block: int,
        end_block: int,
        max_splits: int,
    ) -> List[Any]:
        if start_block > end_block:
            return []
        try:
            return await asyncio.to_thread(
                event.get_logs, from_block=start_block, to_block=end_block
            )
        except Exception as exc:
            if max_splits <= 0 or start_block == end_block:
                self._write_error(
                    "reconcile_error",
                    f"{event_name}:from_block={start_block} to_block={end_block} err={exc}",
                )
                return []
            mid = (start_block + end_block) // 2
            self._write_warning(
                "reconcile_split",
                {
                    "event": event_name,
                    "from_block": start_block,
                    "to_block": end_block,
                    "split_at": mid,
                    "error": str(exc),
                },
            )
            left = await self._safe_get_logs(
                event_name,
                event,
                start_block,
                mid,
                max_splits=max_splits - 1,
            )
            right = await self._safe_get_logs(
                event_name,
                event,
                mid + 1,
                end_block,
                max_splits=max_splits - 1,
            )
            return left + right

    def _create_filters(self, contracts: List[Tuple[str, Any]]) -> List[_FilterHandle]:
        filters: List[_FilterHandle] = []
        for event_name, event in contracts:
            try:
                filt = event.create_filter(fromBlock="latest")
            except Exception as exc:
                self._write_error("filter_create_failed", f"{event_name}:{exc}")
                continue
            filters.append(_FilterHandle(event_name=event_name, event=event, filt=filt))
            self._log("INFO", f"filter_created:{event_name}")
        return filters

    def _drain_filters_once(self, filters: List[_FilterHandle]) -> Tuple[int, bool]:
        processed = 0
        had_error = False
        for handle in list(filters):
            try:
                entries = handle.filt.get_new_entries()
            except Exception as exc:
                had_error = True
                self._write_error("subscription_loop_error", f"{handle.event_name}:{exc}")
                try:
                    handle.filt = handle.event.create_filter(fromBlock="latest")
                except Exception as exc2:
                    self._write_error("filter_create_failed", f"{handle.event_name}:{exc2}")
                continue
            if entries:
                for idx in range(0, len(entries), 10):
                    for entry in entries[idx : idx + 10]:
                        self._handle_event(handle.event_name, entry)
                        processed += 1
        return processed, had_error

    def _handle_event(self, event_name: str, entry: Any) -> None:
        mono_ns = time.monotonic_ns()
        wall_ms = int(time.time() * 1000)
        wall_iso = _utc_iso_from_wall_ms(wall_ms)
        raw = _normalize_event(entry, event_name)
        meta = raw.setdefault("meta", {})
        meta.update(
            {
                "chain_id": self._chain_id,
                "contract_address": raw.get("address"),
                "event_name": event_name,
                "block_number": raw.get("blockNumber"),
                "block_hash": raw.get("blockHash"),
                "tx_hash": raw.get("transactionHash"),
                "log_index": raw.get("logIndex"),
                "chain_timestamp": raw.get("chain_timestamp"),
                "observed_ts": wall_ms,
                "as_of_ts": wall_ms,
                "market_slug": None,
                "event_age_ms": _event_age_ms(wall_ms, raw.get("chain_timestamp")),
            }
        )

        key = _dedupe_key(raw)
        if not self._deduper.add(key):
            self._duplicates_dropped += 1
            return

        block_number = raw.get("blockNumber")
        block_hash = raw.get("blockHash")
        self._track_block_hash(block_number, block_hash)

        self._tape.write(
            channel="onchain",
            event_type=event_name,
            market=raw.get("conditionId") if isinstance(raw, dict) else None,
            asset_id=None,
            t_event_ms=raw.get("chain_timestamp"),
            raw=raw,
            source=self._config.source,
            t_recv_wall_iso=wall_iso,
            t_recv_wall_ms=wall_ms,
            t_recv_mono_ns=mono_ns,
            parse_warnings=raw.get("parse_warnings") if isinstance(raw, dict) else None,
        )
        if self._metrics is not None:
            self._metrics.record_message("onchain", raw.get("chain_timestamp"), wall_ms)
        if self._signal_state is not None:
            record = {
                "event_type": event_name,
                "t_recv_mono_ns": mono_ns,
                "raw": raw,
            }
            self._signal_state.ingest_record(record)
        self._events_ingested += 1
        self._last_event_mono_ns = mono_ns
        self._last_event_wall_ms = wall_ms
        if isinstance(block_number, int):
            self._last_block_number = block_number

    def _track_block_hash(self, block_number: Any, block_hash: Any) -> None:
        if block_number is None or block_hash is None:
            return
        try:
            number = int(block_number)
        except (TypeError, ValueError):
            return
        value = str(block_hash)
        prior = self._block_hashes.get(number)
        if prior and prior != value:
            self._write_reorg(number, prior, value)
        self._block_hashes[number] = value
        if len(self._block_hashes) > 2000:
            self._block_hashes.popitem(last=False)

    def _emit_heartbeat_if_needed(self) -> None:
        now_ns = time.monotonic_ns()
        if now_ns - self._last_event_mono_ns < int(self._heartbeat_secs * 1_000_000_000):
            return
        wall_ms = int(time.time() * 1000)
        wall_iso = _utc_iso_from_wall_ms(wall_ms)
        if os.getenv("ONCHAIN_DEBUG"):
            print(
                "[%s] On-chain heartbeat - no events in %.1fs"
                % (_utc_iso_from_wall_ms(wall_ms), self._heartbeat_secs)
            )
        payload = {
            "last_event_observed_ts": self._last_event_wall_ms,
            "last_block_number_seen": self._last_block_number,
            "ws_connected": self._ws_connected,
            "events_ingested": self._events_ingested,
            "duplicates_dropped": self._duplicates_dropped,
            "reconnects": self._reconnects,
            "errors": self._errors,
        }
        self._tape.write(
            channel="onchain",
            event_type="heartbeat",
            market=None,
            asset_id=None,
            t_event_ms=None,
            raw=payload,
            source=self._config.source,
            t_recv_wall_iso=wall_iso,
            t_recv_wall_ms=wall_ms,
            t_recv_mono_ns=now_ns,
            parse_warnings=[],
        )
        self._last_event_mono_ns = now_ns

    async def _init_web3(self, url: Optional[str]) -> Any:
        if not url:
            return None
        try:
            from web3 import Web3
        except Exception as exc:
            self._write_error("web3_init_failed", f"import_error:{exc}")
            return None

        geth_poa_middleware = None
        try:
            from web3.middleware import geth_poa_middleware as _poa

            geth_poa_middleware = _poa
        except Exception:
            try:
                from web3.middleware.geth_poa import geth_poa_middleware as _poa  # type: ignore

                geth_poa_middleware = _poa
            except Exception:
                geth_poa_middleware = None

        if url.startswith("ws"):
            try:
                from web3.providers.websocket import WebsocketProvider  # type: ignore
            except Exception:
                WebsocketProvider = getattr(Web3, "WebsocketProvider", None)
            if WebsocketProvider is None:
                self._write_error("web3_init_failed", "ws_provider_missing")
                return None
            provider = WebsocketProvider(url)
        else:
            provider = Web3.HTTPProvider(url)
        web3 = Web3(provider)
        if geth_poa_middleware is not None:
            try:
                web3.middleware_onion.inject(geth_poa_middleware, layer=0)
            except Exception as exc:
                self._write_error("web3_init_failed", f"poa_inject_failed:{exc}")
        if self._chain_id is None:
            try:
                self._chain_id = await asyncio.wait_for(
                    asyncio.to_thread(lambda: web3.eth.chain_id),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                self._write_error("web3_init_failed", "chain_id_timeout")
            except Exception as exc:
                self._write_error("web3_init_failed", f"chain_id_failed:{exc}")
        return web3

    def _build_contracts(self, web3: Any) -> List[Tuple[str, Any]]:
        contracts: List[Tuple[str, Any]] = []
        exchange_abi = _load_abi(Path(self._config.ctf_exchange_abi_path))
        ctf_abi = _load_abi(Path(self._config.ctf_abi_path))
        if exchange_abi is None or ctf_abi is None or web3 is None:
            return contracts
        exchange = web3.eth.contract(address=CTF_EXCHANGE_ADDRESS, abi=exchange_abi)
        ctf = web3.eth.contract(address=CTF_ADDRESS, abi=ctf_abi)
        exchange_label = "ctf_exchange"
        ctf_label = "ctf"
        order_filled = self._safe_event(exchange, "OrderFilled", exchange_label)
        if order_filled is not None:
            contracts.append(("OrderFilled", order_filled))
        orders_matched = self._safe_event(exchange, "OrdersMatched", exchange_label)
        if orders_matched is not None:
            contracts.append(("OrdersMatched", orders_matched))
        positions_split = self._safe_event(ctf, "PositionsSplit", ctf_label)
        if positions_split is not None:
            contracts.append(("PositionsSplit", positions_split))
        positions_merge = self._safe_event(ctf, "PositionsMerge", ctf_label)
        if positions_merge is not None:
            contracts.append(("PositionsMerge", positions_merge))
        negrisk_abi = _load_abi(Path(self._config.negrisk_abi_path))
        if negrisk_abi is not None:
            negrisk = web3.eth.contract(address=NEGRISK_CTF_EXCHANGE_ADDRESS, abi=negrisk_abi)
            negrisk_label = "negrisk_exchange"
            negrisk_order_filled = self._safe_event(negrisk, "OrderFilled", negrisk_label)
            if negrisk_order_filled is not None:
                contracts.append(("OrderFilled", negrisk_order_filled))
            negrisk_orders_matched = self._safe_event(negrisk, "OrdersMatched", negrisk_label)
            if negrisk_orders_matched is not None:
                contracts.append(("OrdersMatched", negrisk_orders_matched))
        return contracts

    def _safe_event(self, contract: Any, event_name: str, contract_label: str) -> Optional[Any]:
        event = getattr(contract.events, event_name, None)
        if event is None:
            key = (contract_label, event_name)
            if key not in self._abi_missing_logged:
                self._abi_missing_logged.add(key)
                self._write_warning(
                    "abi_event_missing",
                    {"contract": contract_label, "address": str(contract.address), "event": event_name},
                )
            return None
        return event

    def _write_error(self, code: str, message: str) -> None:
        self._errors += 1
        wall_ms = int(time.time() * 1000)
        wall_iso = _utc_iso_from_wall_ms(wall_ms)
        self._tape.write(
            channel="onchain",
            event_type="error",
            market=None,
            asset_id=None,
            t_event_ms=None,
            raw={"code": code, "message": message},
            source=self._config.source,
            t_recv_wall_iso=wall_iso,
            t_recv_wall_ms=wall_ms,
            t_recv_mono_ns=time.monotonic_ns(),
            parse_warnings=[],
        )

    def _write_warning(self, code: str, payload: Dict[str, Any]) -> None:
        wall_ms = int(time.time() * 1000)
        wall_iso = _utc_iso_from_wall_ms(wall_ms)
        self._tape.write(
            channel="onchain",
            event_type="warning",
            market=None,
            asset_id=None,
            t_event_ms=None,
            raw={"code": code, **payload},
            source=self._config.source,
            t_recv_wall_iso=wall_iso,
            t_recv_wall_ms=wall_ms,
            t_recv_mono_ns=time.monotonic_ns(),
            parse_warnings=[],
        )

    def _write_reorg(self, block_number: int, old_hash: str, new_hash: str) -> None:
        wall_ms = int(time.time() * 1000)
        wall_iso = _utc_iso_from_wall_ms(wall_ms)
        self._tape.write(
            channel="onchain",
            event_type="reorg_detected",
            market=None,
            asset_id=None,
            t_event_ms=None,
            raw={
                "block_number": block_number,
                "old_hash": old_hash,
                "new_hash": new_hash,
            },
            source=self._config.source,
            t_recv_wall_iso=wall_iso,
            t_recv_wall_ms=wall_ms,
            t_recv_mono_ns=time.monotonic_ns(),
            parse_warnings=[],
        )

    def _log(self, level: str, message: str) -> None:
        levels = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
        current = levels.get(self._config.log_level.upper(), 20)
        if levels.get(level.upper(), 20) < current:
            return
        print(f"[onchain:{level.upper()}] {message}")


def _load_abi(path: Path) -> Optional[List[Dict[str, Any]]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _normalize_event(entry: Any, event_name: str) -> Dict[str, Any]:
    raw: Dict[str, Any] = {"event": event_name, "args": {}}
    try:
        raw["args"] = dict(entry.get("args", {}))
        raw["conditionId"] = raw["args"].get("conditionId") or raw["args"].get("condition_id")
        raw["transactionHash"] = _hex(entry.get("transactionHash"))
        raw["blockNumber"] = entry.get("blockNumber")
        raw["blockHash"] = _hex(entry.get("blockHash"))
        raw["logIndex"] = entry.get("logIndex")
        raw["address"] = entry.get("address")
        raw["chain_timestamp"] = entry.get("blockTimestamp") or entry.get("timestamp")
    except Exception as exc:
        raw["parse_warnings"] = [f"decode_error:{exc}"]
    return raw


def _dedupe_key(raw: Dict[str, Any]) -> Optional[str]:
    tx_hash = raw.get("transactionHash")
    log_index = raw.get("logIndex")
    block_hash = raw.get("blockHash")
    block_number = raw.get("blockNumber")
    if tx_hash and log_index is not None:
        return f"{tx_hash}:{log_index}"
    if block_hash and log_index is not None:
        return f"{block_hash}:{log_index}"
    if block_number is not None and log_index is not None:
        return f"{block_number}:{log_index}"
    return None


def _hex(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.hex()
    except AttributeError:
        return str(value)


def _utc_iso_from_wall_ms(wall_ms: int) -> str:
    return datetime.fromtimestamp(wall_ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _event_age_ms(observed_ms: int, chain_ts: Any) -> Optional[int]:
    try:
        if chain_ts is None:
            return None
        value = int(chain_ts)
        return observed_ms - value
    except (TypeError, ValueError):
        return None
