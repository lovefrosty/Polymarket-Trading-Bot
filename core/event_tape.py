from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading
import time


@dataclass(frozen=True)
class EventRecord:
    run_id: str
    channel: str
    event_type: str
    market: Optional[str]
    asset_id: Optional[str]
    source: Optional[str]
    t_event_ms: Optional[int]
    t_recv_wall_ms: int
    t_recv_wall_iso: str
    t_recv_mono_ns: int
    raw: Any
    parse_warnings: List[str]
    out_of_order: bool


class EventTape:
    def __init__(self, log_dir: str, run_id: str) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._handles: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def write(
        self,
        channel: str,
        event_type: str,
        market: Optional[str],
        asset_id: Optional[str],
        t_event_ms: Optional[int],
        raw: Any,
        source: Optional[str] = None,
        parse_warnings: Optional[List[str]] = None,
        out_of_order: bool = False,
        t_recv_wall_iso: Optional[str] = None,
        t_recv_wall_ms: Optional[int] = None,
        t_recv_mono_ns: Optional[int] = None,
    ) -> None:
        wall_iso = t_recv_wall_iso or _utc_iso()
        wall_ms = t_recv_wall_ms if t_recv_wall_ms is not None else _wall_ms_from_iso(wall_iso)
        mono_ns = t_recv_mono_ns or time.monotonic_ns()
        record = EventRecord(
            run_id=self.run_id,
            channel=channel,
            event_type=event_type,
            market=market,
            asset_id=asset_id,
            source=source,
            t_event_ms=t_event_ms,
            t_recv_wall_ms=wall_ms,
            t_recv_wall_iso=wall_iso,
            t_recv_mono_ns=mono_ns,
            raw=raw,
            parse_warnings=parse_warnings or [],
            out_of_order=out_of_order,
        )
        line = json.dumps(asdict(record), separators=(",", ":"), ensure_ascii=True)
        with self._lock:
            handle = self._get_handle(channel)
            handle.write(line + "\n")
            handle.flush()

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                handle.close()
            self._handles.clear()

    def _get_handle(self, channel: str):
        date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"{channel}_{date_key}.jsonl"
        path = self.log_dir / filename
        handle = self._handles.get(path.as_posix())
        if handle is None or handle.closed:
            handle = path.open("a", encoding="utf-8")
            self._handles[path.as_posix()] = handle
        return handle


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _wall_ms_from_iso(value: str) -> int:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return int(time.time() * 1000)
