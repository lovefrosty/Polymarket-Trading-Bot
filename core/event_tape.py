from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
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
    def __init__(
        self,
        log_dir: str,
        run_id: str,
        wall_ms_provider: Optional[Callable[[], int]] = None,
        mono_ns_provider: Optional[Callable[[], int]] = None,
        date_key_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._wall_ms_provider = wall_ms_provider or _default_wall_ms_provider
        self._mono_ns_provider = mono_ns_provider or time.monotonic_ns
        self._date_key_provider = date_key_provider or _default_date_key_provider
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
        warnings = list(parse_warnings or [])
        if t_recv_wall_iso is not None:
            wall_iso = t_recv_wall_iso
        elif t_recv_wall_ms is not None:
            wall_iso = _iso_from_wall_ms(int(t_recv_wall_ms))
        else:
            wall_iso = _iso_from_wall_ms(int(self._wall_ms_provider()))
        if t_recv_wall_ms is not None:
            wall_ms = int(t_recv_wall_ms)
        else:
            parsed_wall_ms = _wall_ms_from_iso(wall_iso)
            if parsed_wall_ms is None:
                wall_ms = int(self._wall_ms_provider())
                if "INVALID_RECV_WALL_ISO_FALLBACK" not in warnings:
                    warnings.append("INVALID_RECV_WALL_ISO_FALLBACK")
            else:
                wall_ms = parsed_wall_ms
        mono_ns = int(t_recv_mono_ns) if t_recv_mono_ns is not None else int(self._mono_ns_provider())
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
            parse_warnings=warnings,
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
        date_key = str(self._date_key_provider())
        filename = f"{channel}_{date_key}.jsonl"
        path = self.log_dir / filename
        handle = self._handles.get(path.as_posix())
        if handle is None or handle.closed:
            handle = path.open("a", encoding="utf-8")
            self._handles[path.as_posix()] = handle
        return handle


def _default_wall_ms_provider() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _default_date_key_provider() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _iso_from_wall_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _wall_ms_from_iso(value: str) -> Optional[int]:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None
