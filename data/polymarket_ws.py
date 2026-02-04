from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import random
import ssl
import time
from typing import Any, Dict, List, Optional

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
        self.asset_ids = asset_ids
        self.books = books
        self.tape = tape
        self.metrics = metrics
        self.config = config
        self.decision_engine = decision_engine
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
                    MARKET_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    ssl=ssl_context,
                ) as ws:
                    if connected_once:
                        self.metrics.record_reconnect("market")
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
        payload = {"type": "market", "assets_ids": self.asset_ids}
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
            self.metrics.record_message("market", None, recv_wall_ms)
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
            self.metrics.record_message("market", None, recv_wall_ms)
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

        seq_warning = self._check_sequence(msg)
        if seq_warning:
            parse_warnings.append(seq_warning)
        out_of_order = False

        if _is_snapshot(msg):
            asset_id = asset_id or _extract_snapshot_asset_id(msg)
            if asset_id and asset_id in self.books:
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
            else:
                parse_warnings.append("missing_asset_id_for_snapshot")
        elif _is_price_change(msg):
            changes = msg.get("price_changes", [])
            if not isinstance(changes, list):
                parse_warnings.append("price_changes_not_list")
            else:
                for change in changes:
                    change_asset = _extract_asset_id(change) or asset_id
                    if not change_asset or change_asset not in self.books:
                        parse_warnings.append("missing_asset_id_for_change")
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
        else:
            parse_warnings.append("unknown_message_schema")

        self.tape.write(
            channel="market",
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
        self.metrics.record_message("market", t_event_ms, recv_wall_ms)

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
            self.metrics.record_message("user", None, recv_wall_ms)
            return

        parse_warnings: List[str] = []
        t_event_ms = _extract_event_ts_ms(msg, parse_warnings)
        event_type = _extract_event_type(msg)
        market = _extract_market(msg)
        asset_id = _extract_asset_id(msg)

        seq_warning = self._check_sequence(msg)
        if seq_warning:
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
        self.metrics.record_message("user", t_event_ms, recv_wall_ms)

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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ssl_context() -> Optional[ssl.SSLContext]:
    if certifi is None:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None
