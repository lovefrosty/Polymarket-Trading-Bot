from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import threading


@dataclass(frozen=True)
class DecisionRecord:
    schema_version: str
    engine_version: Optional[str]
    run_id: str
    t_decision_wall_iso: str
    t_decision_wall_ms: int
    t_decision_mono_ns: int
    asset_id: str
    market_slug: Optional[str]
    condition_id: Optional[str]
    token_id: str
    outcome: Optional[str]
    outcome_by_token: Optional[Dict[str, str]]
    book: Dict[str, Any]
    p_market_mid: Optional[float]
    p_market_exec_buy: Optional[float]
    p_market_exec_sell: Optional[float]
    p_market: Optional[float]
    p_fair: Optional[float]
    edge_net_buy: Optional[float]
    edge_net_sell: Optional[float]
    p_star: Optional[Dict[str, Any]]
    labels: Optional[Dict[str, Any]]
    features_raw: Optional[Dict[str, Any]]
    features_ortho: Optional[Dict[str, Any]]
    whitening: Optional[Dict[str, Any]]
    gates: Dict[str, Any]
    exec_cost: Dict[str, Any]
    notes: Dict[str, Any]
    as_of_ts_ms: Optional[int] = None
    pstar_diag: Optional[Dict[str, Any]] = None
    policy_codes: Optional[List[str]] = None
    latency: Optional[Dict[str, Any]] = None
    fsm_state: Optional[str] = None


class DecisionTape:
    def __init__(
        self,
        log_dir: str,
        run_id: str,
        date_key_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._date_key_provider = date_key_provider or _default_date_key_provider
        self._handles: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def write(self, record: DecisionRecord) -> None:
        payload = json.dumps(
            record.__dict__,
            separators=(",", ":"),
            ensure_ascii=True,
            sort_keys=True,
        )
        with self._lock:
            handle = self._get_handle()
            handle.write(payload + "\n")
            handle.flush()

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                handle.close()
            self._handles.clear()

    def _get_handle(self):
        date_key = str(self._date_key_provider())
        filename = f"decision_{date_key}.jsonl"
        path = self.log_dir / filename
        handle = self._handles.get(path.as_posix())
        if handle is None or handle.closed:
            handle = path.open("a", encoding="utf-8")
            self._handles[path.as_posix()] = handle
        return handle


def _default_date_key_provider() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


class TimeMapper:
    def __init__(self, wall_ms_offset: float) -> None:
        self.wall_ms_offset = wall_ms_offset

    @classmethod
    def from_wall_and_mono(cls, wall_ms: float, mono_ns: int) -> "TimeMapper":
        return cls(wall_ms - mono_ns / 1_000_000.0)

    def wall_ms(self, mono_ns: int) -> int:
        return int(mono_ns / 1_000_000.0 + self.wall_ms_offset)

    def wall_iso(self, mono_ns: int) -> str:
        wall_ms = self.wall_ms(mono_ns)
        return _iso_from_wall_ms(wall_ms)

    def mono_ns_from_wall_ms(self, wall_ms: int) -> int:
        return int((float(wall_ms) - self.wall_ms_offset) * 1_000_000.0)


def _iso_from_wall_ms(wall_ms: int) -> str:
    return datetime.fromtimestamp(wall_ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
