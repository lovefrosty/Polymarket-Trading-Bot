from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Deque, Dict, List, Optional

from core.order_book import OrderBook


@dataclass
class MetricsSnapshot:
    reconnects: Dict[str, int]
    messages_per_sec: Dict[str, float]
    messages_per_sec_by_sub_state: Dict[str, float]
    rejection_top: Dict[str, float]
    ws_lag_ms_avg: Optional[float]
    ws_lag_ms_p95: Optional[float]
    book_stale_pct: Dict[str, float]
    out_of_order_counts: Dict[str, int]


@dataclass(frozen=True)
class ReliabilityScoreRow:
    source: str
    score: float
    status: str
    reasons: List[str]


class Metrics:
    def __init__(self) -> None:
        self._reconnects = Counter()
        self._message_counts = Counter()
        self._message_counts_by_sub_state = Counter()
        self._message_counts_total = Counter()
        self._message_counts_total_by_sub_state = Counter()
        self._last_report_ts = time.monotonic()
        self._rejections = Counter()
        self._decision_count = 0
        self._ws_lag_samples: Deque[float] = deque(maxlen=500)
        self._out_of_order = Counter()
        self._market_unknown_recv_ts_ms: Deque[int] = deque(maxlen=20_000)
        self._market_ignored_old_recv_ts_ms: Deque[int] = deque(maxlen=20_000)
        self._market_active_recv_ts_ms: Deque[int] = deque(maxlen=20_000)
        self._sequence_gap_recv_ts_ms: Deque[int] = deque(maxlen=20_000)
        self._sequence_out_of_order_recv_ts_ms: Deque[int] = deque(maxlen=20_000)

    def record_reconnect(self, channel: str) -> None:
        self._reconnects[channel] += 1

    def record_message(
        self,
        channel: str,
        t_event_ms: Optional[int],
        t_recv_wall_ms: Optional[int],
        asset_id: Optional[str] = None,
        sub_state: Optional[str] = None,
    ) -> None:
        self._message_counts[channel] += 1
        self._message_counts_total[channel] += 1
        state = str(sub_state or "unknown")
        self._message_counts_by_sub_state[f"{channel}:{state}"] += 1
        self._message_counts_total_by_sub_state[f"{channel}:{state}"] += 1
        if channel == "market" and t_recv_wall_ms is not None:
            recv_ms = int(t_recv_wall_ms)
            if state == "unknown":
                self._market_unknown_recv_ts_ms.append(recv_ms)
            elif state == "ignored_old":
                self._market_ignored_old_recv_ts_ms.append(recv_ms)
            elif state == "active":
                self._market_active_recv_ts_ms.append(recv_ms)
        # Only active market traffic contributes to lag health.
        include_lag = channel != "market" or state == "active"
        if include_lag and t_event_ms is not None and t_recv_wall_ms is not None:
            self._ws_lag_samples.append(t_recv_wall_ms - t_event_ms)

    def market_unknown_count(self) -> int:
        return int(self._message_counts_total_by_sub_state.get("market:unknown", 0))

    def market_ignored_old_count(self) -> int:
        return int(self._message_counts_total_by_sub_state.get("market:ignored_old", 0))

    def market_unknown_rate_per_min(self, now_wall_ms: int) -> float:
        now_ms = int(now_wall_ms)
        self._prune_recent(self._market_unknown_recv_ts_ms, now_ms)
        return float(len(self._market_unknown_recv_ts_ms))

    def market_ignored_old_rate_per_min(self, now_wall_ms: int) -> float:
        now_ms = int(now_wall_ms)
        self._prune_recent(self._market_ignored_old_recv_ts_ms, now_ms)
        return float(len(self._market_ignored_old_recv_ts_ms))

    def market_active_rate_per_min(self, now_wall_ms: int) -> float:
        now_ms = int(now_wall_ms)
        self._prune_recent(self._market_active_recv_ts_ms, now_ms)
        return float(len(self._market_active_recv_ts_ms))

    def record_sequence_warning(self, warning_code: str, recv_wall_ms: int) -> None:
        code = str(warning_code or "").lower()
        ts_ms = int(recv_wall_ms)
        if code == "sequence_gap":
            self._sequence_gap_recv_ts_ms.append(ts_ms)
        elif code == "sequence_out_of_order":
            self._sequence_out_of_order_recv_ts_ms.append(ts_ms)

    def sequence_gap_count(self, now_wall_ms: int) -> int:
        now_ms = int(now_wall_ms)
        self._prune_recent(self._sequence_gap_recv_ts_ms, now_ms)
        return int(len(self._sequence_gap_recv_ts_ms))

    def sequence_gap_rate_per_min(self, now_wall_ms: int) -> float:
        return float(self.sequence_gap_count(now_wall_ms))

    def sequence_out_of_order_count(self, now_wall_ms: int) -> int:
        now_ms = int(now_wall_ms)
        self._prune_recent(self._sequence_out_of_order_recv_ts_ms, now_ms)
        return int(len(self._sequence_out_of_order_recv_ts_ms))

    def total_messages_by_sub_state(self, channel: str, sub_state: str) -> int:
        key = f"{str(channel)}:{str(sub_state)}"
        return int(self._message_counts_total_by_sub_state.get(key, 0))

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
        messages_per_sec_by_sub_state = {
            key: count / elapsed for key, count in self._message_counts_by_sub_state.items()
        }
        self._message_counts.clear()
        self._message_counts_by_sub_state.clear()
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
            messages_per_sec_by_sub_state=messages_per_sec_by_sub_state,
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
                {
                    "by_channel": _fmt_rates(snapshot.messages_per_sec),
                    "by_sub_state": _fmt_rates(snapshot.messages_per_sec_by_sub_state),
                },
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
            "messages_per_sec_by_sub_state": snapshot.messages_per_sec_by_sub_state,
            "ws_lag_ms_avg": snapshot.ws_lag_ms_avg,
            "ws_lag_ms_p95": snapshot.ws_lag_ms_p95,
            "book_stale_pct": snapshot.book_stale_pct,
            "rejections": snapshot.rejection_top,
            "out_of_order": snapshot.out_of_order_counts,
        }
        status_path = Path(path)
        status_path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=True))

    @staticmethod
    def _prune_recent(values: Deque[int], now_ms: int, window_ms: int = 60_000) -> None:
        cutoff = int(now_ms - int(window_ms))
        while values and int(values[0]) < cutoff:
            values.popleft()


def _fmt_rates(values: Dict[str, float]) -> Dict[str, float]:
    return {key: round(value, 3) for key, value in values.items()}


def _fmt_opt(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 3)


def _sleep(seconds: float):
    import asyncio

    return asyncio.sleep(seconds)


def classify_reliability_rows(values: Dict[str, Dict[str, float]]) -> List[ReliabilityScoreRow]:
    rows: List[ReliabilityScoreRow] = []
    for source, raw in values.items():
        score = 0.0
        reasons: List[str] = []
        ws_lag = float(raw.get("ws_lag_ms", 0.0))
        ack = float(raw.get("ack_ms", 0.0))
        invalid_ratio = float(raw.get("invalid_ratio", 0.0))
        mismatch_ratio = float(raw.get("mismatch_ratio", 0.0))
        freeze_ratio = float(raw.get("freeze_ratio", 0.0))

        if ws_lag > 0.0:
            score += min(35.0, max(0.0, (ws_lag - 1000.0) / 100.0))
            if ws_lag > 1500:
                reasons.append("ws_lag_high")
        if ack > 0.0:
            score += min(25.0, max(0.0, (ack - 300.0) / 40.0))
            if ack > 500:
                reasons.append("ack_high")
        if invalid_ratio > 0.0:
            score += min(20.0, invalid_ratio * 100.0)
            if invalid_ratio > 0.05:
                reasons.append("invalid_ratio_high")
        if mismatch_ratio > 0.0:
            score += min(15.0, mismatch_ratio * 80.0)
            if mismatch_ratio > 0.05:
                reasons.append("reconciliation_mismatch")
        if freeze_ratio > 0.0:
            score += min(15.0, freeze_ratio * 120.0)
            if freeze_ratio > 0.05:
                reasons.append("freeze_rate_high")

        score = max(0.0, min(100.0, score))
        status = "OK"
        if score >= 70.0:
            status = "CRITICAL"
        elif score >= 35.0:
            status = "WARN"
        rows.append(
            ReliabilityScoreRow(
                source=str(source),
                score=round(score, 2),
                status=status,
                reasons=reasons,
            )
        )
    rows.sort(key=lambda row: row.score, reverse=True)
    return rows
