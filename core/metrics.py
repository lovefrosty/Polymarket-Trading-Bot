from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Deque, Dict, Optional

from core.order_book import OrderBook


@dataclass
class MetricsSnapshot:
    reconnects: Dict[str, int]
    messages_per_sec: Dict[str, float]
    rejection_top: Dict[str, float]
    ws_lag_ms_avg: Optional[float]
    ws_lag_ms_p95: Optional[float]
    book_stale_pct: Dict[str, float]
    out_of_order_counts: Dict[str, int]


class Metrics:
    def __init__(self) -> None:
        self._reconnects = Counter()
        self._message_counts = Counter()
        self._last_report_ts = time.monotonic()
        self._rejections = Counter()
        self._decision_count = 0
        self._ws_lag_samples: Deque[float] = deque(maxlen=500)
        self._out_of_order = Counter()

    def record_reconnect(self, channel: str) -> None:
        self._reconnects[channel] += 1

    def record_message(self, channel: str, t_event_ms: Optional[int], t_recv_wall_ms: Optional[int]) -> None:
        self._message_counts[channel] += 1
        if t_event_ms is not None and t_recv_wall_ms is not None:
            self._ws_lag_samples.append(t_recv_wall_ms - t_event_ms)

    def record_decision(self, ok: bool, reasons: list[str]) -> None:
        self._decision_count += 1
        if not ok:
            for reason in reasons:
                self._rejections[reason] += 1

    def record_out_of_order(self, asset_id: str) -> None:
        self._out_of_order[asset_id] += 1

    async def periodic_report(
        self,
        books: Dict[str, OrderBook],
        staleness_ms: int,
        interval_secs: float = 10.0,
        status_path: Optional[str] = None,
    ) -> None:
        while True:
            await _sleep(interval_secs)
            snapshot = self._snapshot(books, staleness_ms)
            self._print(snapshot)
            if status_path:
                self._write_status(status_path, snapshot)

    def _snapshot(self, books: Dict[str, OrderBook], staleness_ms: int) -> MetricsSnapshot:
        now = time.monotonic()
        elapsed = max(now - self._last_report_ts, 1e-6)
        messages_per_sec = {
            channel: count / elapsed for channel, count in self._message_counts.items()
        }
        self._message_counts.clear()
        self._last_report_ts = now

        ws_lag_avg = None
        ws_lag_p95 = None
        if self._ws_lag_samples:
            samples = sorted(self._ws_lag_samples)
            ws_lag_avg = sum(samples) / len(samples)
            ws_lag_p95 = samples[int(0.95 * (len(samples) - 1))]

        book_stale_pct = {}
        now_ns = time.monotonic_ns()
        for asset_id, book in books.items():
            stale = 1.0 if book.book_is_stale(now_ns, staleness_ms) else 0.0
            book_stale_pct[asset_id] = stale * 100.0

        rejection_top = dict(self._rejections.most_common(10))
        if self._decision_count > 0:
            rejection_rates = {
                reason: count / self._decision_count for reason, count in rejection_top.items()
            }
        else:
            rejection_rates = {}
        return MetricsSnapshot(
            reconnects=dict(self._reconnects),
            messages_per_sec=messages_per_sec,
            rejection_top=rejection_rates,
            ws_lag_ms_avg=ws_lag_avg,
            ws_lag_ms_p95=ws_lag_p95,
            book_stale_pct=book_stale_pct,
            out_of_order_counts=dict(self._out_of_order),
        )

    def _print(self, snapshot: MetricsSnapshot) -> None:
        print(
            "metrics reconnects=%s msg_rate=%s ws_lag_avg_ms=%s ws_lag_p95_ms=%s stale_pct=%s rejections=%s"
            % (
                snapshot.reconnects,
                _fmt_rates(snapshot.messages_per_sec),
                _fmt_opt(snapshot.ws_lag_ms_avg),
                _fmt_opt(snapshot.ws_lag_ms_p95),
                snapshot.book_stale_pct,
                {"rejections": snapshot.rejection_top, "out_of_order": snapshot.out_of_order_counts},
            )
        )

    def _write_status(self, path: str, snapshot: MetricsSnapshot) -> None:
        data = {
            "reconnects": snapshot.reconnects,
            "messages_per_sec": snapshot.messages_per_sec,
            "ws_lag_ms_avg": snapshot.ws_lag_ms_avg,
            "ws_lag_ms_p95": snapshot.ws_lag_ms_p95,
            "book_stale_pct": snapshot.book_stale_pct,
            "rejections": snapshot.rejection_top,
            "out_of_order": snapshot.out_of_order_counts,
        }
        status_path = Path(path)
        status_path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=True))


def _fmt_rates(values: Dict[str, float]) -> Dict[str, float]:
    return {key: round(value, 3) for key, value in values.items()}


def _fmt_opt(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 3)


def _sleep(seconds: float):
    import asyncio

    return asyncio.sleep(seconds)
