from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import ssl
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

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
    attempt_id: Optional[int] = None
    attempt_diag: Dict[str, Any] = field(default_factory=dict)


class MarketWSClient:
    def __init__(
        self,
        asset_ids: List[str],
        books: Dict[str, OrderBook],
        tape: EventTape,
        metrics: Metrics,
        config: WSConfig,
        decision_engine: Optional[DecisionEngine] = None,
        on_invariant_violation: Optional[Callable[[Dict[str, Any]], None]] = None,
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
        self._pending_confirm_first_incr_ms: Optional[int] = None
        self._pending_confirm_last_incr_ms: Optional[int] = None
        self._pending_preclass_first_recv_ms: Optional[int] = None
        self._pending_preclass_last_recv_ms: Optional[int] = None
        self._pending_parse_drop_counts: Dict[str, int] = {}
        self._pending_attempt_id: Optional[int] = None
        self._sub_attempt_seq: int = 1
        self._attempts_by_id: Dict[int, Dict[str, Any]] = {}
        self._completed_subscribe_attempts: List[Dict[str, Any]] = []
        self._invariant_violation_count: int = 0
        self._last_ws_error: Optional[str] = None
        self._on_invariant_violation = on_invariant_violation
        self._preclass_lifetime: Dict[str, Any] = {
            "msgs_total": 0,
            "msgs_by_sub_id": defaultdict(int),
            "msgs_by_asset_id": defaultdict(int),
            "msgs_pending_hits_by_asset_id": defaultdict(int),
            "msgs_active_hits_by_asset_id": defaultdict(int),
            "msgs_unknown_schema": 0,
            "msgs_missing_asset_id": 0,
            "msgs_missing_sub_id": 0,
            "msgs_ignored_old": 0,
            "msgs_unknown_state": 0,
        }
        self._preclass_rolling: Dict[str, Any] = {
            "msgs_total": deque(maxlen=120_000),
            "msgs_by_sub_id": defaultdict(lambda: deque(maxlen=120_000)),
            "msgs_by_asset_id": defaultdict(lambda: deque(maxlen=120_000)),
            "msgs_pending_hits_by_asset_id": defaultdict(lambda: deque(maxlen=120_000)),
            "msgs_active_hits_by_asset_id": defaultdict(lambda: deque(maxlen=120_000)),
            "msgs_unknown_schema": deque(maxlen=120_000),
            "msgs_missing_asset_id": deque(maxlen=120_000),
            "msgs_missing_sub_id": deque(maxlen=120_000),
            "msgs_ignored_old": deque(maxlen=120_000),
            "msgs_unknown_state": deque(maxlen=120_000),
        }
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._diagnostic_clock_ms: int = 0

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
                    self._last_ws_error = None
                    if connected_once:
                        self.metrics.record_reconnect("market")
                    connected_once = True
                    attempt_id = self._start_subscribe_attempt(
                        action="subscribe",
                        asset_ids=self._active_asset_ids,
                        timeout_ms=None,
                        pending_sub_id=int(self._active_subscription_id),
                    )
                    await self._subscribe(ws, self._active_asset_ids, attempt_id=attempt_id)
                    self._finalize_subscribe_attempt(
                        attempt_id=attempt_id,
                        result="SENT",
                        confirm_wait_ms=None,
                        confirm_diag=None,
                    )
                    backoff_ms = self.config.reconnect_base_ms
                    await self._receive_loop(ws)
            except Exception as exc:
                self._last_ws_error = str(exc)
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

    def drain_completed_subscribe_attempts(self) -> List[Dict[str, Any]]:
        drained = [dict(item) for item in self._completed_subscribe_attempts]
        self._completed_subscribe_attempts.clear()
        return drained

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
        attempt_id = self._start_subscribe_attempt(
            action="resubscribe",
            asset_ids=normalized,
            timeout_ms=int(max(0.0, float(first_book_timeout_secs)) * 1000),
            pending_sub_id=pending_sub_id,
        )
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
        self._pending_confirm_first_incr_ms = None
        self._pending_confirm_last_incr_ms = None
        self._pending_preclass_first_recv_ms = None
        self._pending_preclass_last_recv_ms = None
        self._pending_parse_drop_counts = {}
        self._pending_attempt_id = attempt_id
        self._soft_assert(
            self._pending_subscription_id != self._active_subscription_id,
            code="I1_ACTIVE_PENDING_COLLISION",
            payload={
                "active_subscription_id": int(self._active_subscription_id),
                "pending_subscription_id": int(pending_sub_id),
                "attempt_id": int(attempt_id),
            },
        )

        wait_start_ns = time.monotonic_ns()
        await self._subscribe(self._ws, normalized, attempt_id=attempt_id)
        try:
            await asyncio.wait_for(pending_event.wait(), timeout=max(0.5, float(first_book_timeout_secs)))
        except asyncio.TimeoutError:
            confirm_diag = self._pending_confirm_diag()
            confirm_wait_ms = float(max(0.0, (time.monotonic_ns() - wait_start_ns) / 1_000_000.0))
            attempt_diag = self._finalize_subscribe_attempt(
                attempt_id=attempt_id,
                result="TIMEOUT",
                confirm_wait_ms=confirm_wait_ms,
                confirm_diag=confirm_diag,
            )
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
                attempt_id=attempt_id,
                attempt_diag=attempt_diag,
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
        attempt_diag = self._finalize_subscribe_attempt(
            attempt_id=attempt_id,
            result="COMMIT",
            confirm_wait_ms=confirm_wait_ms,
            confirm_diag=confirm_diag,
        )
        return ResubscribeResult(
            status="committed",
            previous_asset_ids=prev_asset_ids,
            new_asset_ids=list(normalized),
            active_subscription_id=int(self._active_subscription_id),
            confirm_diag=confirm_diag,
            confirm_wait_ms=confirm_wait_ms,
            unsubscribe_ms=unsubscribe_ms,
            attempt_id=attempt_id,
            attempt_diag=attempt_diag,
        )

    async def _subscribe(self, ws, asset_ids: List[str], attempt_id: Optional[int] = None) -> None:
        normalized = sorted({str(asset_id) for asset_id in asset_ids if asset_id})
        payload = {"type": "market", "assets_ids": list(normalized)}
        if attempt_id is not None:
            self._record_subscribe_payload(attempt_id=attempt_id, payload=payload)
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
        self._pending_confirm_first_incr_ms = None
        self._pending_confirm_last_incr_ms = None
        self._pending_preclass_first_recv_ms = None
        self._pending_preclass_last_recv_ms = None
        self._pending_parse_drop_counts = {}
        self._pending_attempt_id = None

    def _pending_confirm_diag(self) -> Dict[str, Any]:
        attempt_id = _maybe_int(self._pending_attempt_id)
        attempt = self._attempts_by_id.get(int(attempt_id)) if attempt_id is not None else None
        preclass_pending_hits = {}
        preclass_msgs_by_sub_id = {"pending": 0, "active": 0, "none": 0}
        ack_status = "NONE"
        subscribe_payload_echo: Dict[str, Any] = {}
        if isinstance(attempt, dict):
            raw_hits = attempt.get("preclass_pending_hits_by_asset")
            if isinstance(raw_hits, dict):
                preclass_pending_hits = {
                    str(asset): int(_maybe_int(count) or 0)
                    for asset, count in sorted(raw_hits.items(), key=lambda item: str(item[0]))
                }
            raw_msgs_by_sub_id = attempt.get("preclass_msgs_by_sub_id")
            if isinstance(raw_msgs_by_sub_id, dict):
                pending_sub_id = self._pending_subscription_id
                active_sub_id = self._active_subscription_id
                preclass_msgs_by_sub_id = {
                    "pending": int(_maybe_int(raw_msgs_by_sub_id.get(_sub_counter_key(pending_sub_id))) or 0),
                    "active": int(_maybe_int(raw_msgs_by_sub_id.get(_sub_counter_key(active_sub_id))) or 0),
                    "none": int(_maybe_int(raw_msgs_by_sub_id.get("none")) or 0),
                }
            ack_status = str(attempt.get("ack_status") or "NONE")
            payload_hash = _coerce_nonempty_str(attempt.get("payload_hash")) or ""
            payload_assets = attempt.get("asset_ids") if isinstance(attempt.get("asset_ids"), list) else []
            subscribe_payload_echo = {
                "num_assets": int(len(payload_assets)),
                "first_two_asset_ids": [str(a) for a in payload_assets[:2]],
                "payload_hash": payload_hash,
            }
        return {
            "attempt_id": attempt_id,
            "required_updates_per_token": int(self.config.confirm_min_updates_per_token),
            "counts_by_asset": dict(self._pending_confirm_counts),
            "rejects_by_asset": dict(self._pending_confirm_rejections),
            "first_recv_wall_ms": self._pending_confirm_first_recv_ms,
            "last_recv_wall_ms": self._pending_confirm_last_recv_ms,
            "first_confirm_incr_wall_ms": self._pending_confirm_first_incr_ms,
            "last_confirm_incr_wall_ms": self._pending_confirm_last_incr_ms,
            "first_pending_preclass_recv_wall_ms": self._pending_preclass_first_recv_ms,
            "last_pending_preclass_recv_wall_ms": self._pending_preclass_last_recv_ms,
            "reasons": sorted(set(self._pending_confirm_reasons)),
            "pending_subscription_id": self._pending_subscription_id,
            "pending_asset_ids": list(self._pending_asset_ids),
            "preclass_pending_hits_by_asset": preclass_pending_hits,
            "preclass_msgs_by_sub_id": preclass_msgs_by_sub_id,
            "parse_drop_counts": {
                str(key): int(_maybe_int(value) or 0)
                for key, value in sorted(self._pending_parse_drop_counts.items(), key=lambda item: str(item[0]))
            },
            "ack_status": ack_status,
            "last_ws_error": _coerce_nonempty_str(self._last_ws_error),
            "subscribe_payload_echo": subscribe_payload_echo,
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
            self.metrics.record_message(
                "market",
                None,
                recv_wall_ms,
                asset_id=None,
                sub_state="unknown",
                unknown_class="unknown_schema",
                unknown_signature="non_dict_message",
            )
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
            self.metrics.record_message(
                "market",
                None,
                recv_wall_ms,
                asset_id=None,
                sub_state="unknown",
                unknown_class="unknown_schema",
                unknown_signature="non_dict_message",
            )
            return
        await self._handle_dict_message(msg, recv_mono_ns, recv_wall_ms, recv_wall_iso)

    async def _handle_dict_message(
        self, msg: Dict[str, Any], recv_mono_ns: int, recv_wall_ms: int, recv_wall_iso: str
    ) -> None:
        self._soft_assert(
            self._pending_subscription_id is None or self._pending_subscription_id != self._active_subscription_id,
            code="I1_ACTIVE_PENDING_COLLISION",
            payload={
                "active_subscription_id": int(self._active_subscription_id),
                "pending_subscription_id": _maybe_int(self._pending_subscription_id),
            },
            recv_wall_ms=recv_wall_ms,
            recv_mono_ns=recv_mono_ns,
            recv_wall_iso=recv_wall_iso,
        )
        parse_warnings: List[str] = []
        t_event_ms = _extract_event_ts_ms(msg, parse_warnings)
        event_type = _extract_event_type(msg)
        market = _extract_market(msg)
        asset_id = _extract_asset_id(msg)
        preclass = self._record_preclassification(msg=msg, recv_wall_ms=recv_wall_ms)
        non_book_event = _is_last_trade_price(msg)
        seq_warning_preview = self._check_sequence(msg, update_state=False)
        out_of_order = False
        book_mutated = False
        decision_update_count = 0
        phases_seen: Set[str] = set()
        sub_ids_seen: Set[int] = set()
        active_assets_seen: Set[str] = set()
        pending_assets_seen: Set[str] = set()
        closed_flag = _extract_closed_flag(msg)

        if self._record_subscription_ack(msg=msg, recv_wall_ms=recv_wall_ms):
            raw_payload = dict(msg)
            raw_payload["_sub_state"] = "unknown"
            raw_payload["_subscription_id"] = _extract_raw_subscription_id(msg)
            raw_payload["_active_subscription_id"] = int(self._active_subscription_id)
            self.tape.write(
                channel="market",
                event_type="subscription_ack",
                market=market,
                asset_id=asset_id,
                t_event_ms=t_event_ms,
                raw=raw_payload,
                source="market_ws:sub:control",
                parse_warnings=parse_warnings,
                out_of_order=False,
                t_recv_wall_iso=recv_wall_iso,
                t_recv_mono_ns=recv_mono_ns,
            )
            self.metrics.record_message(
                "market",
                t_event_ms,
                recv_wall_ms,
                asset_id=asset_id,
                sub_state="unknown",
                unknown_class=None,
                unknown_signature=None,
                count_for_health=False,
            )
            return

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
                book_mutated = True
                out_of_order = update_result.recv_out_of_order
                if update_result.event_time_regressed:
                    parse_warnings.append("event_time_regressed")
                if out_of_order:
                    self.metrics.record_out_of_order(asset_id)
                elif self.decision_engine is not None:
                    self.decision_engine.on_book_update(asset_id, recv_mono_ns)
                    decision_update_count += 1

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
                    book_mutated = True
                    if update_result.recv_out_of_order:
                        out_of_order = True
                        self.metrics.record_out_of_order(change_asset)
                    else:
                        if self.decision_engine is not None:
                            self.decision_engine.on_book_update(change_asset, recv_mono_ns)
                            decision_update_count += 1
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
        elif non_book_event:
            sub_id, phase = self._classify_asset(asset_id)
            phases_seen.add(phase)
            if sub_id is not None:
                sub_ids_seen.add(sub_id)
            if phase == "active" and asset_id is not None:
                active_assets_seen.add(asset_id)
            elif phase == "pending" and asset_id is not None:
                pending_assets_seen.add(asset_id)
            elif phase == "ignored_old":
                parse_warnings.append("ignored_old_subscription_asset")
            else:
                parse_warnings.append("unknown_subscription_asset")
        else:
            parse_warnings.append("unknown_message_schema")
            self._record_preclass_warning("msgs_unknown_schema", recv_wall_ms)
            self._increment_attempt_counter("preclass_unknown_schema", 1)

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
        unknown_class = None
        unknown_signature = None
        if sub_state == "unknown":
            warnings = set(parse_warnings)
            if "unknown_message_schema" in warnings:
                unknown_class = "unknown_schema"
            elif "unknown_subscription_asset" in warnings:
                unknown_class = "unknown_channel"
            else:
                unknown_class = "benign_heartbeat_misc"
            warning_sig = ",".join(sorted(warnings)) if warnings else "none"
            unknown_signature = f"{event_type}:{warning_sig}"

        self.metrics.record_message(
            "market",
            t_event_ms,
            recv_wall_ms,
            asset_id=asset_id,
            sub_state=sub_state,
            unknown_class=unknown_class,
            unknown_signature=unknown_signature,
            count_for_health=not non_book_event,
        )
        self._record_pending_parse_warnings(parse_warnings, bool(preclass["pending_hit_assets"]))
        self._soft_assert(
            not (sub_state == "unknown" and book_mutated),
            code="I2_UNKNOWN_MUTATED_BOOK",
            payload={
                "event_type": str(event_type),
                "asset_id": _coerce_nonempty_str(asset_id),
                "parse_warnings": sorted(set(parse_warnings)),
            },
            recv_wall_ms=recv_wall_ms,
            recv_mono_ns=recv_mono_ns,
            recv_wall_iso=recv_wall_iso,
        )
        self._soft_assert(
            not (sub_state == "unknown" and decision_update_count > 0),
            code="I2_UNKNOWN_MUTATED_DECISION",
            payload={
                "event_type": str(event_type),
                "decision_update_count": int(decision_update_count),
                "asset_id": _coerce_nonempty_str(asset_id),
            },
            recv_wall_ms=recv_wall_ms,
            recv_mono_ns=recv_mono_ns,
            recv_wall_iso=recv_wall_iso,
        )

    def _start_subscribe_attempt(
        self,
        *,
        action: str,
        asset_ids: List[str],
        timeout_ms: Optional[int],
        pending_sub_id: Optional[int],
    ) -> int:
        attempt_id = int(self._sub_attempt_seq)
        self._sub_attempt_seq += 1
        normalized_assets = sorted({str(asset_id) for asset_id in asset_ids if asset_id})
        self._attempts_by_id[attempt_id] = {
            "attempt_id": int(attempt_id),
            "created_ts_ms": int(self._next_diag_ts_ms(self._last_active_book_recv_wall_ms)),
            "action": str(action),
            "asset_ids": list(normalized_assets),
            "requested_market": None,
            "timeout_ms": _maybe_int(timeout_ms),
            "ws_url": str(MARKET_WS_URL),
            "active_sub_id_before": int(self._active_subscription_id),
            "pending_sub_id": _maybe_int(pending_sub_id),
            "payload_json": "",
            "payload_hash": "",
            "ack_received": False,
            "ack_ts_ms": None,
            "ack_payload_type": None,
            "ack_error": None,
            "ack_status": "NONE",
            "preclass_msgs_by_sub_id": defaultdict(int),
            "preclass_pending_hits_by_asset": defaultdict(int),
            "preclass_active_hits_by_asset": defaultdict(int),
            "preclass_pending_hits": 0,
            "preclass_active_hits": 0,
            "preclass_unknown_schema": 0,
            "preclass_missing_asset": 0,
            "preclass_missing_sub": 0,
            "parse_drop_counts": defaultdict(int),
            "first_pending_recv_ts_ms": None,
            "last_pending_recv_ts_ms": None,
            "result": None,
            "confirm_wait_ms": None,
            "confirm_required_updates": None,
            "confirm_counts_by_asset": {},
            "confirm_preclass_hits_by_asset": {},
        }
        return int(attempt_id)

    def _record_subscribe_payload(self, *, attempt_id: int, payload: Dict[str, Any]) -> None:
        attempt = self._attempts_by_id.get(int(attempt_id))
        if attempt is None:
            return
        payload_copy = dict(payload)
        asset_ids = payload_copy.get("assets_ids")
        if isinstance(asset_ids, list):
            payload_copy["assets_ids"] = sorted({str(asset_id) for asset_id in asset_ids if asset_id})
        payload_json = _canonical_json(payload_copy)
        attempt["payload_json"] = payload_json
        attempt["payload_hash"] = hashlib.sha1(payload_json.encode("utf-8")).hexdigest()

    def _record_subscription_ack(self, *, msg: Dict[str, Any], recv_wall_ms: int) -> bool:
        ack = _extract_subscription_ack(msg)
        if ack is None:
            return False
        attempt_id = self._match_attempt_for_ack(msg)
        if attempt_id is None:
            return False
        attempt = self._attempts_by_id.get(int(attempt_id))
        if attempt is None:
            return False
        attempt["ack_received"] = True
        attempt["ack_ts_ms"] = int(recv_wall_ms)
        attempt["ack_payload_type"] = str(ack.get("payload_type") or "")
        attempt["ack_error"] = _coerce_nonempty_str(ack.get("error"))
        ack_status = str(ack.get("status") or "UNSUPPORTED").upper()
        if ack_status not in {"SUCCESS", "ERROR", "UNSUPPORTED", "NONE"}:
            ack_status = "UNSUPPORTED"
        attempt["ack_status"] = ack_status
        if ack_status == "ERROR":
            self._last_ws_error = _coerce_nonempty_str(ack.get("error")) or "subscription_ack_error"
        return True

    def _match_attempt_for_ack(self, msg: Dict[str, Any]) -> Optional[int]:
        ack_assets = sorted({str(asset_id) for asset_id in _extract_payload_asset_ids(msg) if asset_id})
        candidates = [int(key) for key in sorted(self._attempts_by_id.keys())]
        if self._pending_attempt_id is not None and int(self._pending_attempt_id) in self._attempts_by_id:
            pending_attempt = self._attempts_by_id.get(int(self._pending_attempt_id)) or {}
            pending_assets = sorted(str(asset) for asset in pending_attempt.get("asset_ids", []))
            if (not ack_assets) or (ack_assets == pending_assets):
                return int(self._pending_attempt_id)
        for attempt_id in reversed(candidates):
            attempt = self._attempts_by_id.get(int(attempt_id)) or {}
            attempt_assets = sorted(str(asset) for asset in attempt.get("asset_ids", []))
            if not ack_assets or ack_assets == attempt_assets:
                return int(attempt_id)
        return None

    def _record_preclassification(self, *, msg: Dict[str, Any], recv_wall_ms: int) -> Dict[str, Any]:
        recv_ms = int(recv_wall_ms)
        raw_sub_id = _extract_raw_subscription_id(msg)
        raw_assets = sorted({str(asset_id) for asset_id in _extract_raw_asset_ids(msg) if asset_id})
        pending_hit_assets = sorted(
            asset_id
            for asset_id in raw_assets
            if self._pending_asset_set is not None and asset_id in self._pending_asset_set
        )
        active_hit_assets = sorted(asset_id for asset_id in raw_assets if asset_id in self._active_asset_set)
        class_states = {self._classify_asset(asset_id)[1] for asset_id in raw_assets}
        ignored_only = bool(class_states) and class_states == {"ignored_old"}
        unknown_only = (not raw_assets) or (class_states == {"unknown"})

        self._record_preclass_counter("msgs_total", recv_ms)
        self._record_preclass_counter("msgs_by_sub_id", recv_ms, _sub_counter_key(raw_sub_id))
        if not raw_assets:
            self._record_preclass_counter("msgs_missing_asset_id", recv_ms)
        for asset_id in raw_assets:
            self._record_preclass_counter("msgs_by_asset_id", recv_ms, asset_id)
        if raw_sub_id is None:
            self._record_preclass_counter("msgs_missing_sub_id", recv_ms)
        for asset_id in pending_hit_assets:
            self._record_preclass_counter("msgs_pending_hits_by_asset_id", recv_ms, asset_id)
        for asset_id in active_hit_assets:
            self._record_preclass_counter("msgs_active_hits_by_asset_id", recv_ms, asset_id)
        if ignored_only:
            self._record_preclass_counter("msgs_ignored_old", recv_ms)
        if unknown_only:
            self._record_preclass_counter("msgs_unknown_state", recv_ms)

        attempt = self._attempts_by_id.get(int(self._pending_attempt_id)) if self._pending_attempt_id is not None else None
        if isinstance(attempt, dict):
            sub_key = _sub_counter_key(raw_sub_id)
            attempt["preclass_msgs_by_sub_id"][sub_key] = int(attempt["preclass_msgs_by_sub_id"].get(sub_key, 0)) + 1
            if raw_sub_id is None:
                attempt["preclass_missing_sub"] = int(attempt.get("preclass_missing_sub", 0)) + 1
            if not raw_assets:
                attempt["preclass_missing_asset"] = int(attempt.get("preclass_missing_asset", 0)) + 1
            for asset_id in pending_hit_assets:
                attempt["preclass_pending_hits_by_asset"][asset_id] = int(
                    attempt["preclass_pending_hits_by_asset"].get(asset_id, 0)
                ) + 1
                attempt["preclass_pending_hits"] = int(attempt.get("preclass_pending_hits", 0)) + 1
            for asset_id in active_hit_assets:
                attempt["preclass_active_hits_by_asset"][asset_id] = int(
                    attempt["preclass_active_hits_by_asset"].get(asset_id, 0)
                ) + 1
                attempt["preclass_active_hits"] = int(attempt.get("preclass_active_hits", 0)) + 1
            if pending_hit_assets:
                if attempt.get("first_pending_recv_ts_ms") is None:
                    attempt["first_pending_recv_ts_ms"] = recv_ms
                attempt["last_pending_recv_ts_ms"] = recv_ms

        if pending_hit_assets:
            if self._pending_preclass_first_recv_ms is None:
                self._pending_preclass_first_recv_ms = recv_ms
            self._pending_preclass_last_recv_ms = recv_ms

        return {
            "raw_sub_id": raw_sub_id,
            "raw_assets": list(raw_assets),
            "pending_hit_assets": list(pending_hit_assets),
            "active_hit_assets": list(active_hit_assets),
        }

    def _record_preclass_counter(self, metric: str, recv_wall_ms: int, key: Optional[str] = None) -> None:
        recv_ms = int(recv_wall_ms)
        if key is None:
            self._preclass_lifetime[metric] = int(self._preclass_lifetime.get(metric, 0)) + 1
            self._append_rolling_ts(self._preclass_rolling[metric], recv_ms)
            return
        key_str = str(key)
        lifetime = self._preclass_lifetime.get(metric)
        rolling = self._preclass_rolling.get(metric)
        if not isinstance(lifetime, dict) or not isinstance(rolling, dict):
            return
        lifetime[key_str] = int(lifetime.get(key_str, 0)) + 1
        self._append_rolling_ts(rolling[key_str], recv_ms)

    @staticmethod
    def _append_rolling_ts(values: Deque[int], ts_ms: int, window_ms: int = 60_000) -> None:
        recv_ms = int(ts_ms)
        values.append(recv_ms)
        cutoff = int(recv_ms - int(window_ms))
        while values and int(values[0]) < cutoff:
            values.popleft()

    def _record_preclass_warning(self, metric: str, recv_wall_ms: int) -> None:
        self._record_preclass_counter(metric, recv_wall_ms)

    def _increment_attempt_counter(self, key: str, delta: int) -> None:
        if self._pending_attempt_id is None:
            return
        attempt = self._attempts_by_id.get(int(self._pending_attempt_id))
        if not isinstance(attempt, dict):
            return
        attempt[key] = int(_maybe_int(attempt.get(key)) or 0) + int(delta)

    def _record_pending_parse_warnings(self, parse_warnings: List[str], has_pending_hits: bool) -> None:
        if not has_pending_hits:
            return
        attempt = self._attempts_by_id.get(int(self._pending_attempt_id)) if self._pending_attempt_id is not None else None
        for warning in parse_warnings:
            key = str(warning).upper()
            self._pending_parse_drop_counts[key] = int(self._pending_parse_drop_counts.get(key, 0)) + 1
            if isinstance(attempt, dict):
                parse_drop_counts = attempt.get("parse_drop_counts")
                if isinstance(parse_drop_counts, dict):
                    parse_drop_counts[key] = int(parse_drop_counts.get(key, 0)) + 1

    def _next_diag_ts_ms(self, preferred_ts_ms: Optional[int] = None) -> int:
        preferred = _maybe_int(preferred_ts_ms)
        if preferred is None:
            preferred = _maybe_int(self._last_active_book_recv_wall_ms)
        if preferred is None:
            preferred = int(self._sub_attempt_seq - 1)
        next_ts = int(preferred)
        if next_ts <= int(self._diagnostic_clock_ms):
            next_ts = int(self._diagnostic_clock_ms) + 1
        self._diagnostic_clock_ms = int(next_ts)
        return int(next_ts)

    def _finalize_subscribe_attempt(
        self,
        *,
        attempt_id: Optional[int],
        result: str,
        confirm_wait_ms: Optional[float],
        confirm_diag: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if attempt_id is None:
            return {}
        attempt = self._attempts_by_id.pop(int(attempt_id), None)
        if not isinstance(attempt, dict):
            return {}
        ack_status = str(attempt.get("ack_status") or "NONE").upper()
        if ack_status == "NONE":
            ack_status = "UNSUPPORTED"
        counts_by_asset: Dict[str, int] = {}
        preclass_hits_by_asset: Dict[str, int] = {
            str(asset): int(_maybe_int(count) or 0)
            for asset, count in sorted((attempt.get("preclass_pending_hits_by_asset") or {}).items(), key=lambda item: str(item[0]))
        }
        confirm_required_updates = None
        first_pending_recv = _maybe_int(attempt.get("first_pending_recv_ts_ms"))
        last_pending_recv = _maybe_int(attempt.get("last_pending_recv_ts_ms"))
        if isinstance(confirm_diag, dict):
            confirm_required_updates = _maybe_int(confirm_diag.get("required_updates_per_token"))
            raw_counts = confirm_diag.get("counts_by_asset")
            if isinstance(raw_counts, dict):
                counts_by_asset = {
                    str(asset): int(_maybe_int(count) or 0)
                    for asset, count in sorted(raw_counts.items(), key=lambda item: str(item[0]))
                }
            raw_hits = confirm_diag.get("preclass_pending_hits_by_asset")
            if isinstance(raw_hits, dict):
                preclass_hits_by_asset = {
                    str(asset): int(_maybe_int(count) or 0)
                    for asset, count in sorted(raw_hits.items(), key=lambda item: str(item[0]))
                }
            first_pending_recv = _maybe_int(confirm_diag.get("first_pending_preclass_recv_wall_ms")) or first_pending_recv
            last_pending_recv = _maybe_int(confirm_diag.get("last_pending_preclass_recv_wall_ms")) or last_pending_recv

        payload_json = str(attempt.get("payload_json") or "")
        payload_hash = _coerce_nonempty_str(attempt.get("payload_hash")) or ""
        finalized_ts_ms = self._next_diag_ts_ms(
            _maybe_int(last_pending_recv)
            or _maybe_int(attempt.get("ack_ts_ms"))
            or _maybe_int(attempt.get("created_ts_ms"))
        )
        finalized = {
            "ts_ms": int(finalized_ts_ms),
            "attempt_id": int(attempt_id),
            "action": str(attempt.get("action") or ""),
            "active_sub_id_before": _maybe_int(attempt.get("active_sub_id_before")),
            "pending_sub_id": _maybe_int(attempt.get("pending_sub_id")),
            "asset_ids_json": _canonical_json(list(attempt.get("asset_ids") or [])),
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "ack_status": ack_status,
            "ack_ts_ms": _maybe_int(attempt.get("ack_ts_ms")),
            "ack_error": _coerce_nonempty_str(attempt.get("ack_error")),
            "preclass_pending_hits": int(_maybe_int(attempt.get("preclass_pending_hits")) or 0),
            "preclass_active_hits": int(_maybe_int(attempt.get("preclass_active_hits")) or 0),
            "preclass_unknown_schema": int(_maybe_int(attempt.get("preclass_unknown_schema")) or 0),
            "preclass_missing_asset": int(_maybe_int(attempt.get("preclass_missing_asset")) or 0),
            "preclass_missing_sub": int(_maybe_int(attempt.get("preclass_missing_sub")) or 0),
            "confirm_required_updates": _maybe_int(confirm_required_updates),
            "confirm_counts_by_asset_json": _canonical_json(counts_by_asset),
            "confirm_preclass_hits_by_asset_json": _canonical_json(preclass_hits_by_asset),
            "first_pending_recv_ts_ms": _maybe_int(first_pending_recv),
            "last_pending_recv_ts_ms": _maybe_int(last_pending_recv),
            "confirm_wait_ms": _maybe_float(confirm_wait_ms),
            "result": str(result),
            "attempt_diag": {
                "ack_payload_type": _coerce_nonempty_str(attempt.get("ack_payload_type")),
                "parse_drop_counts": {
                    str(key): int(_maybe_int(value) or 0)
                    for key, value in sorted((attempt.get("parse_drop_counts") or {}).items(), key=lambda item: str(item[0]))
                },
                "preclass_msgs_by_sub_id": {
                    str(key): int(_maybe_int(value) or 0)
                    for key, value in sorted((attempt.get("preclass_msgs_by_sub_id") or {}).items(), key=lambda item: str(item[0]))
                },
                "ws_url": str(attempt.get("ws_url") or MARKET_WS_URL),
                "requested_market": _coerce_nonempty_str(attempt.get("requested_market")),
                "created_ts_ms": _maybe_int(attempt.get("created_ts_ms")),
                "timeout_ms": _maybe_int(attempt.get("timeout_ms")),
            },
        }
        self._completed_subscribe_attempts.append(finalized)
        return dict(finalized)

    def _soft_assert(
        self,
        condition: bool,
        *,
        code: str,
        payload: Optional[Dict[str, Any]] = None,
        recv_wall_ms: Optional[int] = None,
        recv_mono_ns: Optional[int] = None,
        recv_wall_iso: Optional[str] = None,
    ) -> bool:
        if condition:
            return True
        self._invariant_violation_count += 1
        now_wall_ms = int(recv_wall_ms) if recv_wall_ms is not None else int(self._next_diag_ts_ms(self._last_active_book_recv_wall_ms))
        now_mono_ns = int(recv_mono_ns) if recv_mono_ns is not None else int(time.monotonic_ns())
        now_iso = str(recv_wall_iso) if recv_wall_iso is not None else _utc_iso()
        diag = {
            "code": str(code),
            "violation_count": int(self._invariant_violation_count),
            "active_subscription_id": int(self._active_subscription_id),
            "pending_subscription_id": _maybe_int(self._pending_subscription_id),
            "payload": dict(payload or {}),
        }
        self.tape.write(
            channel="system",
            event_type="WS_INVARIANT_VIOLATION",
            market=None,
            asset_id=None,
            t_event_ms=now_wall_ms,
            raw=diag,
            source="market_ws",
            parse_warnings=[],
            out_of_order=False,
            t_recv_wall_iso=now_iso,
            t_recv_mono_ns=now_mono_ns,
        )
        if self._on_invariant_violation is not None:
            try:
                self._on_invariant_violation(diag)
            except Exception:
                pass
        return False

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
            if self._pending_confirm_first_incr_ms is None:
                self._pending_confirm_first_incr_ms = int(recv_wall_ms)
            self._pending_confirm_last_incr_ms = int(recv_wall_ms)
        else:
            self._pending_confirm_counts[asset_id] = 0
            self._pending_confirm_rejections[asset_id] = int(self._pending_confirm_rejections.get(asset_id, 0)) + 1
            self._pending_confirm_reasons.extend(reasons)
            for reason in reasons:
                key = str(reason)
                self._pending_parse_drop_counts[key] = int(self._pending_parse_drop_counts.get(key, 0)) + 1

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
        base = max(0, int(base_ms))
        if base <= 0:
            return 0
        return int(base + (base // 2))


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
        base = max(0, int(base_ms))
        if base <= 0:
            return 0
        return int(base + (base // 2))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _coerce_nonempty_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    parsed = str(value).strip()
    return parsed if parsed else None


def _maybe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sub_counter_key(sub_id: Optional[int]) -> str:
    if sub_id is None:
        return "none"
    return str(int(sub_id))


def _extract_raw_subscription_id(msg: Dict[str, Any]) -> Optional[int]:
    for key in ("_subscription_id", "subscription_id", "sub_id", "subscriptionId"):
        if key not in msg:
            continue
        parsed = _maybe_int(msg.get(key))
        if parsed is not None:
            return int(parsed)
    return None


def _extract_raw_asset_ids(msg: Dict[str, Any]) -> List[str]:
    assets: Set[str] = set()
    root_asset = _extract_asset_id(msg)
    if root_asset:
        assets.add(str(root_asset))
    changes = msg.get("price_changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            change_asset = _extract_asset_id(change)
            if change_asset:
                assets.add(str(change_asset))
    payload_assets = _extract_payload_asset_ids(msg)
    for asset in payload_assets:
        assets.add(str(asset))
    return sorted(assets)


def _extract_payload_asset_ids(msg: Dict[str, Any]) -> List[str]:
    out: Set[str] = set()
    for key in ("assets_ids", "asset_ids", "assets", "tokens", "token_ids"):
        raw = msg.get(key)
        if not isinstance(raw, list):
            continue
        for value in raw:
            parsed = _coerce_nonempty_str(value)
            if parsed:
                out.add(parsed)
    return sorted(out)


def _extract_subscription_ack(msg: Dict[str, Any]) -> Optional[Dict[str, Optional[str]]]:
    event_type = str(msg.get("event_type") or msg.get("type") or "").strip().lower()
    status = str(msg.get("status") or msg.get("result") or "").strip().lower()
    message = str(msg.get("message") or msg.get("detail") or "").strip()
    error = _coerce_nonempty_str(msg.get("error")) or _coerce_nonempty_str(msg.get("reason"))
    error_code = _coerce_nonempty_str(msg.get("code")) or _coerce_nonempty_str(msg.get("error_code"))
    payload_type = event_type or status or "control"
    if error_code:
        error = f"{error_code}:{error}" if error else error_code
    success_tokens = {"subscribed", "subscribe_ack", "subscription_ack", "ok", "success"}
    error_tokens = {"error", "failed", "failure", "rejected", "reject"}
    success = event_type in success_tokens or status in success_tokens
    failed = event_type in error_tokens or status in error_tokens or bool(error)
    if not success and not failed and message:
        message_lower = message.lower()
        success = "subscribed" in message_lower
        failed = ("reject" in message_lower) or ("error" in message_lower) or ("failed" in message_lower)
    if not success and not failed:
        return None
    if failed:
        return {"status": "ERROR", "payload_type": payload_type, "error": error or message or "subscription_error"}
    return {"status": "SUCCESS", "payload_type": payload_type, "error": None}


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


def _is_last_trade_price(msg: Dict[str, Any]) -> bool:
    event_type = _extract_event_type(msg).strip().lower()
    return event_type == "last_trade_price"


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
