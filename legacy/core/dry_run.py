from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Dict, List
import time

from core.order_book import OrderBook
from core.validators import HypotheticalOrder, OrderConstraints, SimBalances, validate_hypothetical_order
from core.metrics import Metrics


@dataclass(frozen=True)
class DryRunConfig:
    strategy_id: str
    interval_secs: float
    order_size: float


class DryRunLogger:
    def __init__(self, log_dir: str, run_id: str) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._handle = None
        self._current_date = None

    def write(self, record: Dict[str, object]) -> None:
        handle = self._get_handle()
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n")
        handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _get_handle(self):
        date_key = datetime.utcnow().strftime("%Y%m%d")
        if self._handle is None or self._current_date != date_key:
            if self._handle is not None:
                self._handle.close()
            filename = f"dryrun_{date_key}.jsonl"
            path = self.log_dir / filename
            self._handle = path.open("a", encoding="utf-8")
            self._current_date = date_key
        return self._handle


def _utc_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class DryRunGenerator:
    def __init__(
        self,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        balances: SimBalances,
        metrics: Metrics,
        logger: DryRunLogger,
        config: DryRunConfig,
    ) -> None:
        self.books = books
        self.constraints = constraints
        self.balances = balances
        self.metrics = metrics
        self.logger = logger
        self.config = config
        self._side_toggle: Dict[str, str] = {asset_id: "BUY" for asset_id in books}

    async def run(self) -> None:
        while True:
            for asset_id, book in self.books.items():
                side = self._side_toggle.get(asset_id, "BUY")
                self._side_toggle[asset_id] = "SELL" if side == "BUY" else "BUY"
                constraint = self.constraints[asset_id]
                exec_price = book.executable_price(side.lower(), self.config.order_size)
                price = exec_price if exec_price is not None else 0.0
                now_wall = _utc_iso()
                now_mono_ns = time.monotonic_ns()
                event_ts = book.last_event_ts_ms or 0
                order = HypotheticalOrder(
                    asset_id=asset_id,
                    side=side,
                    price=price,
                    size=self.config.order_size,
                    t_decision_wall=now_wall,
                    t_decision_mono_ns=now_mono_ns,
                    t_decision_event_ts_ms=event_ts,
                )
                ok, reasons, metrics = validate_hypothetical_order(
                    order,
                    book,
                    constraint,
                    self.balances,
                    now_mono_ns,
                )
                self.metrics.record_decision(ok, reasons)
                snapshot = {
                    "best_bid": book.best_bid(),
                    "best_ask": book.best_ask(),
                    "mid": book.mid(),
                }
                record = {
                    "run_id": self.logger.run_id,
                    "strategy_id": self.config.strategy_id,
                    "asset_id": asset_id,
                    "side": side,
                    "price": price,
                    "size": self.config.order_size,
                    "ok": ok,
                    "reasons": reasons,
                    "metrics": metrics,
                    "snapshot": snapshot,
                    "t_decision_wall": now_wall,
                    "t_decision_mono_ns": now_mono_ns,
                    "t_decision_event_ts_ms": event_ts,
                }
                self.logger.write(record)
            await _sleep(self.config.interval_secs)

    def _pick_price(self, book: OrderBook, side: str, constraint: OrderConstraints) -> float:
        if side == "BUY":
            bid = book.best_bid()
            if bid is None:
                return constraint.min_price
            return bid + constraint.min_tick
        ask = book.best_ask()
        if ask is None:
            return constraint.max_price
        return ask - constraint.min_tick


def _sleep(seconds: float):
    import asyncio

    return asyncio.sleep(seconds)
