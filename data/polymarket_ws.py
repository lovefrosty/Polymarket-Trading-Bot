from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import random
import ssl
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover - optional dependency for live WS
    websockets = None
try:  # pragma: no cover - optional dependency
    import certifi
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    certifi = None

from core.decision_engine import DecisionEngine
from core.event_tape import EventTape
from core.metrics import Metrics
from core.order_book import OrderBook


MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


@dataclass
class WSConfig:
    reconnect_base_ms: int
    reconnect_max_ms: int
    heartbeat_timeout_secs: float = 30.0
    confirm_min_updates_per_token: int = 2
    confirm_book_freshness_ms: int = 5_000


@dataclass
class ResubscribeResult:
    status: str
    previous_asset_ids: List[str]
    new_asset_ids: List[str]
    active_subscription_id: int
    confirm_diag: Dict[str, Any] = field(default_factory=dict)
    abort_reason: Optional[str] = None
    confirm_wait_ms: Optional[float] = None
    unsubscribe_ms: Optional[float] = None


class MarketWSClient:
    def __init__(
        self,
        asset_ids: List[str],
        books: Dict[str, OrderBook],
        tape: EventTape,
        metrics: Metrics,
        config: WSConfig,
        decision_engine: Optional[DecisionEngine] = None,
    ) -> None:
        self.books = books
        self.tape = tape
        self.metrics = metrics
        self.config = config
        self.decision_engine = decision_engine
        self._stop_event = asyncio.Event()
        self._last_sequence: Optional[int] = None
        self.asset_ids = sorted({str(asset_id) for asset_id in asset_ids if asset_id})
        self._active_asset_ids: List[str] = list(self.asset_ids)
        self._active_asset_set: Set[str] = set(self._active_asset_ids)
        self._active_subscription_id: int = 1
        self._active_market_closed: bool = False
        self._last_active_book_recv_mono_ns: int = 0
        self._last_active_book_recv_wall_ms: Optional[int] = None
        self._ignored_asset_set: Set[str] = set()
        self._pending_asset_set: Optional[Set[str]] = None
        self._pending_asset_ids: List[str] = []
        self._pending_subscription_id: Optional[int] = None
        self._pending_first_book_event: Optional[asyncio.Event] = None
        self._pending_confirm_counts: Dict[str, int] = {}
        self._pending_confirm_rejections: Dict[str, int] = {}
        self._pending_confirm_first_recv_ms: Optional[int] = None
        self._pending_confirm_last_recv_ms: Optional[int] = None
        self._pending_confirm_reasons: List[str] = []
        self._ws = None
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets_not_installed")
        backoff_ms = self.config.reconnect_base_ms
        connected_once = False
        while not self._stop_event.is_set():
            try:
                ssl_context = _ssl_context()
                async with websockets.connect(
                    MARKET_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    ssl=ssl_context,
                ) as ws:
                    self._ws = ws
                    if connected_once:
                        self.metrics.record_reconnect("market")
                    connected_once = True
                    await self._subscribe(ws, self._active_asset_ids)
                    backoff_ms = self.config.reconnect_base_ms
                    await self._receive_loop(ws)
            except Exception:
                await asyncio.sleep(self._jitter(backoff_ms) / 1000.0)
                backoff_ms = min(backoff_ms * 2, self.config.reconnect_max_ms)
            finally:
                self._ws = None

    def stop(self) -> None:
        self._stop_event.set()

    def set_books(self, books: Dict[str, OrderBook]) -> None:
        self.books = books

    def active_last_book_recv_mono_ns(self) -> int:
        return int(self._last_active_book_recv_mono_ns)

    def active_last_book_recv_wall_ms(self) -> Optional[int]:
        return self._last_active_book_recv_wall_ms

    def active_market_closed(self) -> bool:
        return bool(self._active_market_closed)

    def active_subscription_id(self) -> int:
        return int(self._active_subscription_id)

    async def resubscribe(self, new_asset_ids: List[str], first_book_timeout_secs: float = 5.0) -> ResubscribeResult:
        normalized = sorted({str(asset_id) for asset_id in new_asset_ids if asset_id})
        if not normalized:
            return ResubscribeResult(
                status="abort_invalid_assets",
                previous_asset_ids=list(self._active_asset_ids),
                new_asset_ids=[],
                active_subscription_id=int(self._active_subscription_id),
                abort_reason="INVALID_ASSET_IDS",
            )
        if normalized == self._active_asset_ids:
            return ResubscribeResult(
                status="noop_same_market",
                previous_asset_ids=list(self._active_asset_ids),
                new_asset_ids=list(normalized),
                active_subscription_id=int(self._active_subscription_id),
            )
        if self._ws is None:
            return ResubscribeResult(
                status="abort_not_connected",
                previous_asset_ids=list(self._active_asset_ids),
                new_asset_ids=list(normalized),
                active_subscription_id=int(self._active_subscription_id),
                abort_reason="WS_NOT_CONNECTED",
            )

        prev_asset_ids = list(self._active_asset_ids)
        pending_sub_id = int(self._active_subscription_id + 1)
        pending_event = asyncio.Event()
        self._pending_subscription_id = pending_sub_id
        self._pending_asset_set = set(normalized)
        self._pending_asset_ids = list(normalized)
        self._pending_first_book_event = pending_event
        self._pending_confirm_counts = {asset_id: 0 for asset_id in normalized}
        self._pending_confirm_rejections = {asset_id: 0 for asset_id in normalized}
        self._pending_confirm_first_recv_ms = None
        self._pending_confirm_last_recv_ms = None
        self._pending_confirm_reasons = []

        wait_start_ns = time.monotonic_ns()
        await self._subscribe(self._ws, normalized)
        try:
            await asyncio.wait_for(pending_event.wait(), timeout=max(0.5, float(first_book_timeout_secs)))
        except asyncio.TimeoutError:
            confirm_diag = self._pending_confirm_diag()
            confirm_wait_ms = float(max(0.0, (time.monotonic_ns() - wait_start_ns) / 1_000_000.0))
            if self._pending_subscription_id == pending_sub_id:
                self._clear_pending_subscription()
            return ResubscribeResult(
                status="abort_timeout_waiting_confirmation",
                previous_asset_ids=prev_asset_ids,
                new_asset_ids=list(normalized),
                active_subscription_id=int(self._active_subscription_id),
                confirm_diag=confirm_diag,
                abort_reason="CONFIRM_TIMEOUT",
                confirm_wait_ms=confirm_wait_ms,
            )

        self.asset_ids = list(normalized)
        self._active_asset_ids = list(normalized)
        self._active_asset_set = set(normalized)
        self._ignored_asset_set = set(prev_asset_ids)
        self._active_subscription_id = pending_sub_id
        self._active_market_closed = False
        self._last_sequence = None
        confirm_diag = self._pending_confirm_diag()
        confirm_wait_ms = float(max(0.0, (time.monotonic_ns() - wait_start_ns) / 1_000_000.0))
        self._clear_pending_subscription()

        unsubscribe_start_ns = time.monotonic_ns()
        await self._best_effort_unsubscribe(prev_asset_ids)
        unsubscribe_ms = float(max(0.0, (time.monotonic_ns() - unsubscribe_start_ns) / 1_000_000.0))
        return ResubscribeResult(
            status="committed",
            previous_asset_ids=prev_asset_ids,
            new_asset_ids=list(normalized),
            active_subscription_id=int(self._active_subscription_id),
            confirm_diag=confirm_diag,
            confirm_wait_ms=confirm_wait_ms,
            unsubscribe_ms=unsubscribe_ms,
        )

    async def _subscribe(self, ws, asset_ids: List[str]) -> None:
        payload = {"type": "market", "assets_ids": list(asset_ids)}
        await self._send_json(ws, payload)

    async def _best_effort_unsubscribe(self, asset_ids: List[str]) -> None:
        if not asset_ids or self._ws is None:
            return
        payloads = [
            {"type": "unsubscribe", "assets_ids": list(asset_ids)},
            {"type": "market", "assets_ids": list(asset_ids), "unsubscribe": True},
        ]
        for payload in payloads:
            try:
                await self._send_json(self._ws, payload)
                return
            except Exception:
                continue

    async def _send_json(self, ws, payload: Dict[str, Any]) -> None:
        async with self._send_lock:
            await ws.send(json.dumps(payload))

    def _clear_pending_subscription(self) -> None:
        self._pending_subscription_id = None
        self._pending_asset_set = None
        self._pending_asset_ids = []
        self._pending_first_book_event = None
        self._pending_confirm_counts = {}
        self._pending_confirm_rejections = {}
        self._pending_confirm_first_recv_ms = None
        self._pending_confirm_last_recv_ms = None
        self._pending_confirm_reasons = []

    def _pending_confirm_diag(self) -> Dict[str, Any]:
        return {
            "required_updates_per_token": int(self.config.confirm_min_updates_per_token),
            "counts_by_asset": dict(self._pending_confirm_counts),
            "rejects_by_asset": dict(self._pending_confirm_rejections),
            "first_recv_wall_ms": self._pending_confirm_first_recv_ms,
            "last_recv_wall_ms": self._pending_confirm_last_recv_ms,
            "reasons": sorted(set(self._pending_confirm_reasons)),
            "pending_subscription_id": self._pending_subscription_id,
            "pending_asset_ids": list(self._pending_asset_ids),
        }

    async def _receive_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.config.heartbeat_timeout_secs)
            except asyncio.TimeoutError:
                pong = await ws.ping()
                await asyncio.wait_for(pong, timeout=10)
                continue
            recv_mono_ns = time.monotonic_ns()
            recv_wall_iso = _utc_iso()
            recv_wall_ms = int(time.time() * 1000)
            await self._handle_message(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)

    async def _handle_message(self, raw: str, recv_mono_ns: int, recv_wall_ms: int, recv_wall_iso: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(msg, list):
            if all(isinstance(item, dict) for item in msg):
                for item in msg:
                    await self._handle_dict_message(item, recv_mono_ns, recv_wall_ms, recv_wall_iso)
                return
            self.tape.write(
                channel="market",
                event_type="unknown",
                market=None,
                asset_id=None,
                t_event_ms=None,
                raw=msg,
                parse_warnings=["non_dict_message"],
                out_of_order=False,
                t_recv_wall_iso=recv_wall_iso,
                t_recv_mono_ns=recv_mono_ns,
            )
            self.metrics.record_message("market", None, recv_wall_ms, asset_id=None, sub_state="unknown")
            return
        if not isinstance(msg, dict):
            self.tape.write(
                channel="market",
                event_type="unknown",
                market=None,
                asset_id=None,
                t_event_ms=None,
                raw=msg,
                parse_warnings=["non_dict_message"],
                out_of_order=False,
                t_recv_wall_iso=recv_wall_iso,
                t_recv_mono_ns=recv_mono_ns,
            )
            self.metrics.record_message("market", None, recv_wall_ms, asset_id=None, sub_state="unknown")
            return
        await self._handle_dict_message(msg, recv_mono_ns, recv_wall_ms, recv_wall_iso)

    async def _handle_dict_message(
        self, msg: Dict[str, Any], recv_mono_ns: int, recv_wall_ms: int, recv_wall_iso: str
    ) -> None:
        parse_warnings: List[str] = []
        t_event_ms = _extract_event_ts_ms(msg, parse_warnings)
        event_type = _extract_event_type(msg)
        market = _extract_market(msg)
        asset_id = _extract_asset_id(msg)
        seq_warning_preview = self._check_sequence(msg, update_state=False)
        out_of_order = False
        phases_seen: Set[str] = set()
        sub_ids_seen: Set[int] = set()
        active_assets_seen: Set[str] = set()
        pending_assets_seen: Set[str] = set()
        closed_flag = _extract_closed_flag(msg)

        if _is_snapshot(msg):
            asset_id = asset_id or _extract_snapshot_asset_id(msg)
            sub_id, phase = self._classify_asset(asset_id)
            phases_seen.add(phase)
            if sub_id is not None:
                sub_ids_seen.add(sub_id)
            should_apply = phase in {"active", "pending"}
            if phase == "active" and asset_id is not None:
                active_assets_seen.add(asset_id)
            elif phase == "pending" and asset_id is not None:
                pending_assets_seen.add(asset_id)
            elif phase == "ignored_old":
                parse_warnings.append("ignored_old_subscription_asset")
            else:
                parse_warnings.append("unknown_subscription_asset")

            if should_apply and asset_id and asset_id in self.books:
                bids, asks = _parse_snapshot_levels(msg)
                update_result = self.books[asset_id].apply_snapshot(
                    bids,
                    asks,
                    t_event_ms,
                    recv_mono_ns,
                    last_hash=msg.get("hash"),
                )
                out_of_order = update_result.recv_out_of_order
                if update_result.event_time_regressed:
                    parse_warnings.append("event_time_regressed")
                if out_of_order:
                    self.metrics.record_out_of_order(asset_id)
                elif self.decision_engine is not None:
                    self.decision_engine.on_book_update(asset_id, recv_mono_ns)

                if phase == "pending":
                    confirm_ok, reasons = self._valid_confirmation_update(
                        asset_id=asset_id,
                        t_event_ms=t_event_ms,
                        recv_wall_ms=recv_wall_ms,
                        out_of_order=out_of_order,
                        seq_warning=seq_warning_preview,
                    )
                    self._record_pending_confirmation(asset_id, confirm_ok, reasons, recv_wall_ms)
            else:
                if should_apply:
                    parse_warnings.append("missing_asset_id_for_snapshot")
        elif _is_price_change(msg):
            changes = msg.get("price_changes", [])
            if not isinstance(changes, list):
                parse_warnings.append("price_changes_not_list")
            else:
                for change in changes:
                    change_asset = _extract_asset_id(change) or asset_id
                    sub_id, phase = self._classify_asset(change_asset)
                    phases_seen.add(phase)
                    if sub_id is not None:
                        sub_ids_seen.add(sub_id)
                    should_apply = phase in {"active", "pending"}
                    if phase == "active" and change_asset is not None:
                        active_assets_seen.add(change_asset)
                    elif phase == "pending" and change_asset is not None:
                        pending_assets_seen.add(change_asset)
                    elif phase == "ignored_old":
                        parse_warnings.append("ignored_old_subscription_asset")
                    else:
                        parse_warnings.append("unknown_subscription_asset")

                    if should_apply and (not change_asset or change_asset not in self.books):
                        parse_warnings.append("missing_asset_id_for_change")
                        continue
                    if not should_apply:
                        continue
                    side = _normalize_side(change.get("side"))
                    if side is None:
                        parse_warnings.append("unknown_side")
                        continue
                    price = float(change.get("price", 0))
                    size = float(change.get("size", 0))
                    update_result = self.books[change_asset].apply_update(
                        side,
                        price,
                        size,
                        t_event_ms,
                        recv_mono_ns,
                        last_hash=change.get("hash"),
                    )
                    if update_result.recv_out_of_order:
                        out_of_order = True
                        self.metrics.record_out_of_order(change_asset)
                    else:
                        if self.decision_engine is not None:
                            self.decision_engine.on_book_update(change_asset, recv_mono_ns)
                    if update_result.event_time_regressed:
                        parse_warnings.append("event_time_regressed")
                    if phase == "pending":
                        confirm_ok, reasons = self._valid_confirmation_update(
                            asset_id=change_asset,
                            t_event_ms=t_event_ms,
                            recv_wall_ms=recv_wall_ms,
                            out_of_order=bool(update_result.recv_out_of_order),
                            seq_warning=seq_warning_preview,
                        )
                        self._record_pending_confirmation(change_asset, confirm_ok, reasons, recv_wall_ms)
        else:
            parse_warnings.append("unknown_message_schema")

        if pending_assets_seen and self._pending_first_book_event is not None and self._pending_confirmation_complete():
            self._pending_first_book_event.set()
        if active_assets_seen:
            self._last_active_book_recv_mono_ns = max(self._last_active_book_recv_mono_ns, int(recv_mono_ns))
            self._last_active_book_recv_wall_ms = int(recv_wall_ms)
            if closed_flag:
                self._active_market_closed = True
        should_track_sequence = bool(active_assets_seen or pending_assets_seen)
        if should_track_sequence:
            seq_warning = self._check_sequence(msg, update_state=True)
            if seq_warning:
                self.metrics.record_sequence_warning(seq_warning, recv_wall_ms)
                parse_warnings.append(seq_warning)

        sub_state = _market_sub_state(phases_seen)
        source = _market_source_tag(sub_state=sub_state, sub_ids=sub_ids_seen)
        raw_payload = dict(msg)
        raw_payload["_sub_state"] = sub_state
        raw_payload["_subscription_id"] = next(iter(sub_ids_seen)) if len(sub_ids_seen) == 1 else None
        raw_payload["_active_subscription_id"] = int(self._active_subscription_id)
        self.tape.write(
            channel="market",
            event_type=event_type,
            market=market,
            asset_id=asset_id,
            t_event_ms=t_event_ms,
            raw=raw_payload,
            source=source,
            parse_warnings=parse_warnings,
            out_of_order=out_of_order,
            t_recv_wall_iso=recv_wall_iso,
            t_recv_mono_ns=recv_mono_ns,
        )
        self.metrics.record_message("market", t_event_ms, recv_wall_ms, asset_id=asset_id, sub_state=sub_state)

    def _classify_asset(self, asset_id: Optional[str]) -> Tuple[Optional[int], str]:
        if asset_id is not None and asset_id in self._active_asset_set:
            return self._active_subscription_id, "active"
        if (
            asset_id is not None
            and self._pending_asset_set is not None
            and self._pending_subscription_id is not None
            and asset_id in self._pending_asset_set
        ):
            return self._pending_subscription_id, "pending"
        if asset_id is not None and asset_id in self._ignored_asset_set:
            return None, "ignored_old"
        return None, "unknown"

    def _valid_confirmation_update(
        self,
        asset_id: str,
        t_event_ms: Optional[int],
        recv_wall_ms: int,
        out_of_order: bool,
        seq_warning: Optional[str],
    ) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if out_of_order:
            reasons.append("OUT_OF_ORDER")
        if seq_warning:
            reasons.append(str(seq_warning).upper())
        if t_event_ms is None:
            reasons.append("MISSING_EVENT_TS")
        else:
            if int(recv_wall_ms) - int(t_event_ms) > int(self.config.confirm_book_freshness_ms):
                reasons.append("STALE_EVENT_TS")
        book = self.books.get(asset_id)
        best_bid = book.best_bid() if book is not None else None
        best_ask = book.best_ask() if book is not None else None
        if best_bid is None or best_ask is None:
            reasons.append("MISSING_TOP_OF_BOOK")
        return not reasons, reasons

    def _record_pending_confirmation(
        self,
        asset_id: str,
        is_valid: bool,
        reasons: List[str],
        recv_wall_ms: int,
    ) -> None:
        if self._pending_asset_set is None or asset_id not in self._pending_asset_set:
            return
        if self._pending_confirm_first_recv_ms is None:
            self._pending_confirm_first_recv_ms = int(recv_wall_ms)
        self._pending_confirm_last_recv_ms = int(recv_wall_ms)
        if is_valid:
            self._pending_confirm_counts[asset_id] = int(self._pending_confirm_counts.get(asset_id, 0)) + 1
        else:
            self._pending_confirm_counts[asset_id] = 0
            self._pending_confirm_rejections[asset_id] = int(self._pending_confirm_rejections.get(asset_id, 0)) + 1
            self._pending_confirm_reasons.extend(reasons)

    def _pending_confirmation_complete(self) -> bool:
        if not self._pending_confirm_counts:
            return False
        required = max(1, int(self.config.confirm_min_updates_per_token))
        for asset_id in self._pending_asset_ids:
            if int(self._pending_confirm_counts.get(asset_id, 0)) < required:
                return False
        return True

    def _check_sequence(self, msg: Dict[str, Any], update_state: bool = True) -> Optional[str]:
        seq_fields = ["sequence", "sequence_number", "seq"]
        seq_val = None
        for field in seq_fields:
            if field in msg:
                seq_val = msg.get(field)
                break
        if seq_val is None:
            return None
        try:
            seq_int = int(seq_val)
        except (TypeError, ValueError):
            return "sequence_not_int"
        if self._last_sequence is None:
            if update_state:
                self._last_sequence = seq_int
            return None
        if seq_int <= self._last_sequence:
            warning = "sequence_out_of_order"
        elif seq_int > self._last_sequence + 1:
            warning = "sequence_gap"
        else:
            warning = None
        if update_state:
            self._last_sequence = max(self._last_sequence, seq_int)
        return warning

    @staticmethod
    def _jitter(base_ms: int) -> int:
        return int(base_ms + random.random() * base_ms)


class UserWSClient:
    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        condition_ids: List[str],
        tape: EventTape,
        metrics: Metrics,
        config: WSConfig,
    ) -> None:
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.condition_ids = condition_ids
        self.tape = tape
        self.metrics = metrics
        self.config = config
        self._stop_event = asyncio.Event()
        self._last_sequence: Optional[int] = None

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets_not_installed")
        backoff_ms = self.config.reconnect_base_ms
        connected_once = False
        while not self._stop_event.is_set():
            try:
                ssl_context = _ssl_context()
                async with websockets.connect(
                    USER_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    ssl=ssl_context,
                ) as ws:
                    if connected_once:
                        self.metrics.record_reconnect("user")
                    connected_once = True
                    await self._subscribe(ws)
                    backoff_ms = self.config.reconnect_base_ms
                    await self._receive_loop(ws)
            except Exception:
                await asyncio.sleep(self._jitter(backoff_ms) / 1000.0)
                backoff_ms = min(backoff_ms * 2, self.config.reconnect_max_ms)

    def stop(self) -> None:
        self._stop_event.set()

    async def _subscribe(self, ws) -> None:
        payload = {
            "type": "user",
            "auth": {
                "apiKey": self.api_key,
                "secret": self.secret,
                "passphrase": self.passphrase,
            },
        }
        if self.condition_ids:
            payload["markets"] = self.condition_ids
        await ws.send(json.dumps(payload))

    async def _receive_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.config.heartbeat_timeout_secs)
            except asyncio.TimeoutError:
                pong = await ws.ping()
                await asyncio.wait_for(pong, timeout=10)
                continue
            recv_mono_ns = time.monotonic_ns()
            recv_wall_iso = _utc_iso()
            recv_wall_ms = int(time.time() * 1000)
            await self._handle_message(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)

    async def _handle_message(self, raw: str, recv_mono_ns: int, recv_wall_ms: int, recv_wall_iso: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            self.tape.write(
                channel="user",
                event_type="unknown",
                market=None,
                asset_id=None,
                t_event_ms=None,
                raw=msg,
                parse_warnings=["non_dict_message"],
                out_of_order=False,
                t_recv_wall_iso=recv_wall_iso,
                t_recv_mono_ns=recv_mono_ns,
            )
            self.metrics.record_message("user", None, recv_wall_ms, asset_id=None, sub_state="unknown")
            return

        parse_warnings: List[str] = []
        t_event_ms = _extract_event_ts_ms(msg, parse_warnings)
        event_type = _extract_event_type(msg)
        market = _extract_market(msg)
        asset_id = _extract_asset_id(msg)

        seq_warning = self._check_sequence(msg)
        if seq_warning:
            self.metrics.record_sequence_warning(seq_warning, recv_wall_ms)
            parse_warnings.append(seq_warning)
        out_of_order = False

        self.tape.write(
            channel="user",
            event_type=event_type,
            market=market,
            asset_id=asset_id,
            t_event_ms=t_event_ms,
            raw=msg,
            parse_warnings=parse_warnings,
            out_of_order=out_of_order,
            t_recv_wall_iso=recv_wall_iso,
            t_recv_mono_ns=recv_mono_ns,
        )
        self.metrics.record_message("user", t_event_ms, recv_wall_ms, asset_id=asset_id, sub_state="active")

    def _check_sequence(self, msg: Dict[str, Any]) -> Optional[str]:
        seq_fields = ["sequence", "sequence_number", "seq"]
        seq_val = None
        for field in seq_fields:
            if field in msg:
                seq_val = msg.get(field)
                break
        if seq_val is None:
            return None
        try:
            seq_int = int(seq_val)
        except (TypeError, ValueError):
            return "sequence_not_int"
        if self._last_sequence is None:
            self._last_sequence = seq_int
            return None
        if seq_int <= self._last_sequence:
            warning = "sequence_out_of_order"
        elif seq_int > self._last_sequence + 1:
            warning = "sequence_gap"
        else:
            warning = None
        self._last_sequence = max(self._last_sequence, seq_int)
        return warning

    @staticmethod
    def _jitter(base_ms: int) -> int:
        return int(base_ms + random.random() * base_ms)


def _extract_event_ts_ms(msg: Dict[str, Any], warnings: List[str]) -> Optional[int]:
    for key in ("timestamp", "ts", "time", "event_time"):
        if key in msg:
            raw = msg.get(key)
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                warnings.append("invalid_timestamp")
                return None
            if value < 1_000_000_000_000:
                return value * 1000
            return value
    return None


def _extract_event_type(msg: Dict[str, Any]) -> str:
    return str(msg.get("event_type") or msg.get("type") or "unknown")


def _extract_market(msg: Dict[str, Any]) -> Optional[str]:
    return msg.get("condition_id") or msg.get("conditionId") or msg.get("market")


def _extract_asset_id(msg: Dict[str, Any]) -> Optional[str]:
    return msg.get("asset_id") or msg.get("assetId") or msg.get("token_id") or msg.get("tokenId")


def _is_snapshot(msg: Dict[str, Any]) -> bool:
    return any(key in msg for key in ("buys", "sells", "bids", "asks"))


def _is_price_change(msg: Dict[str, Any]) -> bool:
    return msg.get("event_type") == "price_change" or "price_changes" in msg


def _parse_snapshot_levels(msg: Dict[str, Any]) -> tuple[List[tuple[float, float]], List[tuple[float, float]]]:
    buys = msg.get("buys") or msg.get("bids") or []
    sells = msg.get("sells") or msg.get("asks") or []
    return _parse_levels(buys), _parse_levels(sells)


def _parse_levels(levels: Any) -> List[tuple[float, float]]:
    parsed: List[tuple[float, float]] = []
    if isinstance(levels, list):
        for level in levels:
            if isinstance(level, dict):
                price = float(level.get("price", level.get("p", 0)))
                size = float(level.get("size", level.get("s", 0)))
                parsed.append((price, size))
            elif isinstance(level, (list, tuple)) and len(level) >= 2:
                parsed.append((float(level[0]), float(level[1])))
    return parsed


def _normalize_side(side: Optional[str]) -> Optional[str]:
    if side is None:
        return None
    side_lower = str(side).lower()
    if side_lower in {"buy", "bid"}:
        return "buy"
    if side_lower in {"sell", "ask"}:
        return "sell"
    return None


def _extract_snapshot_asset_id(msg: Dict[str, Any]) -> Optional[str]:
    return msg.get("asset_id") or msg.get("assetId")


def _extract_closed_flag(msg: Dict[str, Any]) -> bool:
    for key in ("closed", "is_closed", "market_closed", "inactive"):
        if key not in msg:
            continue
        value = msg.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "closed"}
    return False


def _market_sub_state(phases: Set[str]) -> str:
    if not phases:
        return "unknown"
    if phases == {"active"}:
        return "active"
    if phases == {"pending"}:
        return "pending"
    if phases == {"ignored_old"}:
        return "ignored_old"
    if phases == {"unknown"}:
        return "unknown"
    if "active" in phases:
        return "active"
    if "pending" in phases:
        return "pending"
    if "ignored_old" in phases:
        return "ignored_old"
    return "unknown"


def _market_source_tag(sub_state: str, sub_ids: Set[int]) -> str:
    if sub_state == "active" and len(sub_ids) == 1:
        sub_id = next(iter(sub_ids))
        return f"market_ws:sub:active:{sub_id}"
    if sub_state == "pending" and len(sub_ids) == 1:
        sub_id = next(iter(sub_ids))
        return f"market_ws:sub:pending:{sub_id}"
    if sub_state == "ignored_old":
        return "market_ws:sub:ignored_old"
    return "market_ws:sub:unknown"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ssl_context() -> Optional[ssl.SSLContext]:
    if certifi is None:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None
