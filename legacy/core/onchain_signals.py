from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set


@dataclass(frozen=True)
class TradeEvent:
    t_recv_mono_ns: int
    asset_id: str
    side: str
    size: float
    whale: Optional[str]


@dataclass(frozen=True)
class FlowEvent:
    t_recv_mono_ns: int
    condition_id: str
    amount: float


class OnchainSignalState:
    def __init__(self, window_secs: float = 60.0, whales: Optional[Set[str]] = None) -> None:
        self._window_ns = int(window_secs * 1_000_000_000)
        self._trade_events: Dict[str, Deque[TradeEvent]] = defaultdict(deque)
        self._flow_events: Dict[str, Deque[FlowEvent]] = defaultdict(deque)
        self._whales = {addr.lower() for addr in whales or set()}
        self._last_mono_ns = 0
        self._out_of_order = 0

    def ingest_record(self, record: Dict[str, Any]) -> None:
        mono_ns = int(record.get("t_recv_mono_ns", 0))
        if mono_ns < self._last_mono_ns:
            self._out_of_order += 1
            return
        self._last_mono_ns = mono_ns
        event_type = record.get("event_type")
        raw = record.get("raw") or {}
        if event_type in {"OrderFilled", "OrdersMatched"}:
            self._ingest_trade_event(raw, mono_ns)
        elif event_type in {"PositionsSplit", "PositionsMerge"}:
            self._ingest_flow_event(raw, mono_ns, event_type)

    def snapshot(
        self,
        asset_id: str,
        condition_id: Optional[str],
        now_mono_ns: int,
    ) -> Optional[Dict[str, Any]]:
        if not asset_id:
            return None
        self._prune_trades(asset_id, now_mono_ns)
        trade_events = self._trade_events.get(asset_id)
        if not trade_events and not condition_id:
            return None
        buy = 0.0
        sell = 0.0
        whale_activity: List[Dict[str, Any]] = []
        for event in trade_events or []:
            if event.side == "buy":
                buy += event.size
            elif event.side == "sell":
                sell += event.size
            if event.whale:
                whale_activity.append(
                    {
                        "whale": event.whale,
                        "direction": event.side.upper(),
                        "size": event.size,
                        "t_recv_mono_ns": event.t_recv_mono_ns,
                    }
                )
        imbalance = None
        total = buy + sell
        if total > 0:
            imbalance = (buy - sell) / total
        flow_payload = None
        if condition_id:
            self._prune_flows(condition_id, now_mono_ns)
            flows = self._flow_events.get(condition_id, deque())
            if flows:
                net_amount = sum(event.amount for event in flows)
                if net_amount > 0:
                    signal = "BULLISH"
                elif net_amount < 0:
                    signal = "BEARISH"
                else:
                    signal = "NEUTRAL"
                flow_payload = {
                    "net_amount": net_amount,
                    "signal": signal,
                    "window_secs": self._window_ns / 1_000_000_000.0,
                }
        if total == 0 and flow_payload is None and not whale_activity:
            return None
        return {
            "window_secs": self._window_ns / 1_000_000_000.0,
            "buy_volume": buy,
            "sell_volume": sell,
            "imbalance": imbalance,
            "whale_activity": whale_activity,
            "capital_flow": flow_payload,
            "out_of_order": self._out_of_order,
        }

    def _ingest_trade_event(self, raw: Dict[str, Any], mono_ns: int) -> None:
        args = raw.get("args") if isinstance(raw, dict) else {}
        if not isinstance(args, dict):
            return
        maker_asset = _coerce_asset_id(args.get("makerAssetId") or args.get("maker_asset_id"))
        taker_asset = _coerce_asset_id(args.get("takerAssetId") or args.get("taker_asset_id"))
        maker_amount = _coerce_float(args.get("makerAmountFilled") or args.get("maker_amount_filled"))
        taker_amount = _coerce_float(args.get("takerAmountFilled") or args.get("taker_amount_filled"))
        maker_addr = _coerce_addr(args.get("maker"))
        taker_addr = _coerce_addr(args.get("taker"))
        if taker_asset and taker_amount is not None:
            whale = taker_addr if taker_addr in self._whales else None
            self._trade_events[taker_asset].append(
                TradeEvent(mono_ns, taker_asset, "buy", taker_amount, whale)
            )
        if maker_asset and maker_amount is not None:
            whale = maker_addr if maker_addr in self._whales else None
            self._trade_events[maker_asset].append(
                TradeEvent(mono_ns, maker_asset, "sell", maker_amount, whale)
            )

    def _ingest_flow_event(self, raw: Dict[str, Any], mono_ns: int, event_type: str) -> None:
        args = raw.get("args") if isinstance(raw, dict) else {}
        if not isinstance(args, dict):
            return
        condition_id = args.get("conditionId") or args.get("condition_id")
        if not condition_id:
            return
        amount = _coerce_float(args.get("amount"))
        if amount is None:
            return
        signed_amount = amount if event_type == "PositionsSplit" else -amount
        self._flow_events[str(condition_id)].append(
            FlowEvent(mono_ns, str(condition_id), signed_amount)
        )

    def _prune_trades(self, asset_id: str, now_mono_ns: int) -> None:
        window_start = now_mono_ns - self._window_ns
        events = self._trade_events.get(asset_id)
        if not events:
            return
        while events and events[0].t_recv_mono_ns < window_start:
            events.popleft()

    def _prune_flows(self, condition_id: str, now_mono_ns: int) -> None:
        window_start = now_mono_ns - self._window_ns
        events = self._flow_events.get(condition_id)
        if not events:
            return
        while events and events[0].t_recv_mono_ns < window_start:
            events.popleft()


def _coerce_asset_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    value_str = str(value)
    return value_str if value_str else None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_addr(value: Any) -> Optional[str]:
    if value is None:
        return None
    value_str = str(value).lower()
    return value_str if value_str else None


def load_whales(path: str) -> Set[str]:
    whale_path = Path(path)
    if not whale_path.exists():
        return set()
    try:
        data = json.loads(whale_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(entry).lower() for entry in data if str(entry)}
