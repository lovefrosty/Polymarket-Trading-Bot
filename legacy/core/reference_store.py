from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import bisect
from typing import Any, Deque, Dict, List, Optional, Tuple
from collections import deque


@dataclass(frozen=True)
class ReferencePoint:
    t_event_ms: int
    mid: float
    t_recv_wall_ms: int
    t_recv_mono_ns: int
    venue: Optional[str]


@dataclass(frozen=True)
class ReferenceIngestResult:
    recv_out_of_order: bool
    event_after_recv: bool
    missing_event_ts: bool


@dataclass(frozen=True)
class ReferenceAsOf:
    mid: Optional[float]
    t_event_ms: Optional[int]
    t_recv_wall_ms: Optional[int]
    t_recv_mono_ns: Optional[int]
    latency_ms: Optional[int]
    blockers: List[str]


class ReferenceStore:
    def __init__(self, max_history_ms: int = 6 * 60 * 60 * 1000) -> None:
        self._points: Dict[str, List[ReferencePoint]] = {}
        self._last_recv_mono_ns: Dict[str, int] = {}
        self._max_history_ms = max_history_ms

    def ingest_record(self, record: Dict[str, Any]) -> ReferenceIngestResult:
        raw = record.get("raw") or {}
        symbol = raw.get("symbol") or record.get("market")
        if symbol is None:
            return ReferenceIngestResult(False, False, True)
        symbol = str(symbol)

        t_event_ms = record.get("t_event_ms")
        missing_event_ts = t_event_ms is None
        if t_event_ms is not None:
            try:
                t_event_ms = int(t_event_ms)
            except (TypeError, ValueError):
                t_event_ms = None
                missing_event_ts = True

        t_recv_mono_ns = record.get("t_recv_mono_ns")
        try:
            t_recv_mono_ns = int(t_recv_mono_ns)
        except (TypeError, ValueError):
            return ReferenceIngestResult(False, False, True)

        t_recv_wall_ms = record.get("t_recv_wall_ms")
        if t_recv_wall_ms is None:
            t_recv_wall_ms = _wall_ms_from_iso(record.get("t_recv_wall_iso"))
        try:
            t_recv_wall_ms = int(t_recv_wall_ms)
        except (TypeError, ValueError):
            t_recv_wall_ms = _wall_ms_from_iso(record.get("t_recv_wall_iso"))

        recv_out_of_order = False
        last_recv = self._last_recv_mono_ns.get(symbol)
        if last_recv is not None and t_recv_mono_ns < last_recv:
            recv_out_of_order = True
        if recv_out_of_order:
            return ReferenceIngestResult(True, False, missing_event_ts)

        event_after_recv = False
        if t_event_ms is not None and t_event_ms > t_recv_wall_ms:
            event_after_recv = True
            return ReferenceIngestResult(False, True, missing_event_ts)

        mid = _parse_float(raw.get("mid"))
        if mid is None:
            bid = _parse_float(raw.get("bid"))
            ask = _parse_float(raw.get("ask"))
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            else:
                mid = _parse_float(raw.get("value"))
        if mid is None or t_event_ms is None:
            return ReferenceIngestResult(False, event_after_recv, True)

        venue = raw.get("venue")
        point = ReferencePoint(
            t_event_ms=t_event_ms,
            mid=mid,
            t_recv_wall_ms=t_recv_wall_ms,
            t_recv_mono_ns=t_recv_mono_ns,
            venue=str(venue) if venue is not None else None,
        )
        self._insert_point(symbol, point)
        self._last_recv_mono_ns[symbol] = t_recv_mono_ns
        return ReferenceIngestResult(False, event_after_recv, missing_event_ts)

    def asof(
        self,
        symbol: str,
        decision_ts_ms: int,
        lag_guard_ms: int,
        staleness_ms: int,
    ) -> ReferenceAsOf:
        blockers: List[str] = []
        points = self._points.get(symbol) or []
        if not points:
            return ReferenceAsOf(None, None, None, None, None, ["REF_MISSING"])

        cutoff_ts = decision_ts_ms - lag_guard_ms
        ts_list = [point.t_event_ms for point in points]
        idx = bisect.bisect_left(ts_list, cutoff_ts) - 1
        if idx < 0:
            return ReferenceAsOf(None, None, None, None, None, ["REF_TOO_NEW"])
        point = points[idx]
        age_ms = decision_ts_ms - point.t_event_ms
        if age_ms > staleness_ms:
            blockers.append("REF_STALE")
        latency_ms = point.t_recv_wall_ms - point.t_event_ms
        return ReferenceAsOf(
            mid=point.mid,
            t_event_ms=point.t_event_ms,
            t_recv_wall_ms=point.t_recv_wall_ms,
            t_recv_mono_ns=point.t_recv_mono_ns,
            latency_ms=latency_ms,
            blockers=blockers,
        )

    def event_rate(self, symbol: str, window_ms: int, now_ts_ms: int) -> float:
        points = self._points.get(symbol) or []
        if not points:
            return 0.0
        cutoff = now_ts_ms - window_ms
        ts_list = [point.t_event_ms for point in points]
        idx = bisect.bisect_right(ts_list, cutoff)
        count = len(points) - idx
        if window_ms <= 0:
            return 0.0
        return count / (window_ms / 1000.0)

    def _insert_point(self, symbol: str, point: ReferencePoint) -> None:
        points = self._points.setdefault(symbol, [])
        ts_list = [entry.t_event_ms for entry in points]
        idx = bisect.bisect_right(ts_list, point.t_event_ms)
        points.insert(idx, point)
        self._prune(points, point.t_event_ms)

    def _prune(self, points: List[ReferencePoint], max_ts: int) -> None:
        cutoff = max_ts - self._max_history_ms
        ts_list = [entry.t_event_ms for entry in points]
        idx = bisect.bisect_left(ts_list, cutoff)
        if idx > 0:
            del points[:idx]


def _parse_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wall_ms_from_iso(value: Optional[str]) -> int:
    if not value:
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0
