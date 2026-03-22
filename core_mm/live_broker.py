from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional

from core_mm.execution import ExecutionAdapter, ExecutionResult
from core_mm.positions import PositionTracker


@dataclass(frozen=True)
class StartupReconciliationReport:
    ok: bool
    status: str
    reason: Optional[str]
    checked_at_ms: int
    open_order_count: int
    position_count: int
    open_orders: List[Dict[str, Any]]
    positions: List[Dict[str, Any]]


class LiveBroker:
    """Wraps ExecutionAdapter with fill/stats tracking and pre-trade risk checks.

    Matches PaperBroker's public interface so the rest of the system
    (runner, main_loop, telemetry) works identically in LIVE mode.
    """

    def __init__(
        self,
        *,
        execution_adapter: ExecutionAdapter,
        position_tracker: Optional[PositionTracker] = None,
        fee_bps: float = 25.0,
        max_order_notional: float = 5.0,
        max_position_notional: float = 10.0,
        max_daily_loss: float = 3.0,
    ) -> None:
        self._exec = execution_adapter
        self._positions = position_tracker or PositionTracker()
        self._fee_bps = float(fee_bps)
        self._max_order_notional = float(max_order_notional)
        self._max_position_notional = float(max_position_notional)
        self._max_daily_loss = float(max_daily_loss)
        self._fills: List[Dict[str, Any]] = []
        self._fill_cursor = 0
        self._stats: Dict[str, float] = {
            "realized_gross_pnl": 0.0,
            "realized_net_pnl": 0.0,
            "cumulative_fees": 0.0,
            "turnover": 0.0,
            "win_count": 0.0,
            "loss_count": 0.0,
        }
        # FIFO inventory duration tracking (same as PaperBroker)
        self._fifo_entries: Dict[str, List[List[float]]] = defaultdict(list)
        self._duration_total_weighted_ms: float = 0.0
        self._duration_total_closed_qty: float = 0.0
        self._last_startup_reconciliation: Dict[str, Any] = {}

    @property
    def position_tracker(self) -> PositionTracker:
        return self._positions

    # ── Pre-trade risk checks ────────────────────────────────────────

    def _check_risk(self, *, price: float, size: float, token_id: str) -> Optional[str]:
        """Return an error string if the order should be rejected, else None."""
        if price < 0.01 or price > 0.99:
            return f"price_out_of_bounds: {price}"
        if size < 1:
            return f"size_too_small: {size}"
        notional = price * size
        if notional > self._max_order_notional:
            return f"order_notional_exceeded: ${notional:.2f} > ${self._max_order_notional:.2f}"
        current_pos = self._positions.get_position(str(token_id))
        projected_notional = (current_pos.size + size) * price
        if projected_notional > self._max_position_notional:
            return f"position_notional_exceeded: ${projected_notional:.2f} > ${self._max_position_notional:.2f}"
        net_pnl = self._stats["realized_net_pnl"]
        if net_pnl <= -abs(self._max_daily_loss):
            return f"daily_loss_exceeded: ${net_pnl:.2f} <= -${self._max_daily_loss:.2f}"
        return None

    # ── Order interface (matches PaperBroker) ────────────────────────

    def place_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: float,
        client_order_id: Optional[str] = None,
        quote_group_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        neg_risk: bool = False,
        time_in_force: Optional[str] = None,
    ) -> ExecutionResult:
        # Only risk-check buys (sells reduce risk)
        if str(side).lower() == "buy":
            risk_err = self._check_risk(price=float(price), size=float(size), token_id=str(token_id))
            if risk_err is not None:
                return ExecutionResult(False, payload={}, error=f"risk_check_failed: {risk_err}")
        return self._exec.place_order(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            client_order_id=client_order_id,
            quote_group_id=quote_group_id,
            metadata=metadata,
            neg_risk=neg_risk,
            time_in_force=time_in_force,
        )

    def cancel_order(self, order_id: str) -> ExecutionResult:
        return self._exec.cancel_order(order_id)

    def cancel_all(self) -> ExecutionResult:
        return self._exec.cancel_all()

    def get_open_orders(self) -> ExecutionResult:
        return self._exec.get_open_orders()

    def get_positions(self) -> ExecutionResult:
        return self._exec.get_positions()

    @property
    def last_startup_reconciliation(self) -> Dict[str, Any]:
        return dict(self._last_startup_reconciliation)

    def startup_reconcile(self) -> Dict[str, Any]:
        checked_at_ms = _now_ms()
        orders_result = self.get_open_orders()
        if not orders_result.success:
            report = StartupReconciliationReport(
                ok=False,
                status="blocked",
                reason=f"open_orders_fetch_failed: {orders_result.error or 'unknown_error'}",
                checked_at_ms=checked_at_ms,
                open_order_count=-1,
                position_count=-1,
                open_orders=[],
                positions=[],
            )
            self._last_startup_reconciliation = _reconciliation_to_dict(report)
            return self.last_startup_reconciliation

        positions_result = self.get_positions()
        if not positions_result.success:
            report = StartupReconciliationReport(
                ok=False,
                status="blocked",
                reason=f"positions_fetch_failed: {positions_result.error or 'unknown_error'}",
                checked_at_ms=checked_at_ms,
                open_order_count=len(_extract_orders(orders_result.payload)),
                position_count=-1,
                open_orders=_extract_orders(orders_result.payload),
                positions=[],
            )
            self._last_startup_reconciliation = _reconciliation_to_dict(report)
            return self.last_startup_reconciliation

        open_orders = _extract_orders(orders_result.payload)
        positions = _extract_positions(positions_result.payload)
        nonflat_positions = [position for position in positions if _position_is_nonflat(position)]
        if open_orders:
            report = StartupReconciliationReport(
                ok=False,
                status="blocked",
                reason="resting_orders_present",
                checked_at_ms=checked_at_ms,
                open_order_count=len(open_orders),
                position_count=len(nonflat_positions),
                open_orders=open_orders,
                positions=nonflat_positions,
            )
        elif nonflat_positions:
            report = StartupReconciliationReport(
                ok=False,
                status="blocked",
                reason="nonflat_positions_present",
                checked_at_ms=checked_at_ms,
                open_order_count=0,
                position_count=len(nonflat_positions),
                open_orders=[],
                positions=nonflat_positions,
            )
        else:
            report = StartupReconciliationReport(
                ok=True,
                status="reconciled",
                reason=None,
                checked_at_ms=checked_at_ms,
                open_order_count=0,
                position_count=0,
                open_orders=[],
                positions=[],
            )
        self._last_startup_reconciliation = _reconciliation_to_dict(report)
        return self.last_startup_reconciliation

    # ── Fill tracking (fed from user WebSocket) ──────────────────────

    def record_fill(self, fill_event: Dict[str, Any]) -> None:
        """Ingest a fill from the user WebSocket and update stats/positions."""
        token_id = str(fill_event.get("token_id") or fill_event.get("asset_id") or "")
        side = str(fill_event.get("side") or "").lower()
        size = float(fill_event.get("size") or 0.0)
        price = float(fill_event.get("price") or 0.0)
        if not token_id or not side or size <= 0 or price <= 0:
            return

        ts_ms = int(fill_event.get("ts_ms") or fill_event.get("timestamp") or _now_ms())
        order_id = str(fill_event.get("order_id") or fill_event.get("orderID") or "")

        # Compute PnL for sells
        prior = self._positions.get_position(token_id)
        closed_qty = 0.0
        realized_gross_pnl = 0.0
        if side == "sell" and prior.size > 0:
            closed_qty = min(size, prior.size)
            realized_gross_pnl = (price - prior.avg_price) * closed_qty

        gross_notional = size * price
        fee_usdc = gross_notional * self._fee_bps / 10_000.0
        realized_net_pnl = realized_gross_pnl - fee_usdc

        # Update position
        updated = self._positions.apply_fill(token_id=token_id, side=side, size=size, price=price)

        fill = {
            "order_id": order_id,
            "token_id": token_id,
            "side": side,
            "size": size,
            "price": price,
            "ts_ms": ts_ms,
            "gross_notional": gross_notional,
            "fee_bps": self._fee_bps,
            "fee_usdc": fee_usdc,
            "net_notional": gross_notional - fee_usdc,
            "realized_gross_pnl_delta": realized_gross_pnl,
            "realized_net_pnl_delta": realized_net_pnl,
            "inventory_after_fill": {
                "size": updated.size,
                "avg_price": updated.avg_price,
            },
            "mid_at_fill": float(fill_event.get("mid_at_fill") or price),
        }
        self._stats["realized_gross_pnl"] += realized_gross_pnl
        self._stats["realized_net_pnl"] += realized_net_pnl
        self._stats["cumulative_fees"] += fee_usdc
        self._stats["turnover"] += gross_notional
        if realized_net_pnl > 0:
            self._stats["win_count"] += 1
        elif realized_net_pnl < 0:
            self._stats["loss_count"] += 1
        self._fills.append(fill)

        # FIFO duration tracking
        if side == "buy":
            self._fifo_entries[token_id].append([float(ts_ms), float(size)])
        elif side == "sell":
            self._consume_fifo(token_id, size, ts_ms)

    def fills(self) -> List[Dict[str, Any]]:
        return list(self._fills)

    def drain_new_fills(self) -> List[Dict[str, Any]]:
        if self._fill_cursor >= len(self._fills):
            return []
        out = self._fills[self._fill_cursor:]
        self._fill_cursor = len(self._fills)
        return list(out)

    def stats(self) -> Dict[str, float]:
        result = dict(self._stats)
        result["avg_duration_ms"] = self.avg_duration_ms
        return result

    def sweep_fills(self, token_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """No-op for live — fills come from user WebSocket, not book sweeps."""
        return []

    # ── FIFO duration tracking (same as PaperBroker) ─────────────────

    def _consume_fifo(self, token_id: str, qty: float, exit_ts_ms: int) -> None:
        remaining = float(qty)
        entries = self._fifo_entries.get(str(token_id), [])
        while remaining > 1e-9 and entries:
            entry_ts, entry_qty = float(entries[0][0]), float(entries[0][1])
            consume = min(remaining, entry_qty)
            duration_ms = max(0.0, float(exit_ts_ms) - entry_ts)
            self._duration_total_weighted_ms += duration_ms * consume
            self._duration_total_closed_qty += consume
            remaining -= consume
            entries[0][1] -= consume
            if entries[0][1] <= 1e-9:
                entries.pop(0)

    def consume_fifo_for_merge(self, token_id: str, qty: float, merge_ts_ms: int) -> None:
        self._consume_fifo(token_id, qty, merge_ts_ms)

    @property
    def avg_duration_ms(self) -> float:
        if self._duration_total_closed_qty <= 0:
            return 0.0
        return self._duration_total_weighted_ms / self._duration_total_closed_qty


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extract_orders(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    orders = payload.get("orders") if isinstance(payload, dict) else []
    if not isinstance(orders, list):
        return []
    return [order for order in orders if isinstance(order, dict)]


def _extract_positions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    positions = payload.get("positions") if isinstance(payload, dict) else []
    if not isinstance(positions, list):
        return []
    return [position for position in positions if isinstance(position, dict)]


def _position_is_nonflat(position: Dict[str, Any]) -> bool:
    numeric_keys = (
        "size",
        "count",
        "position",
        "quantity",
        "remaining_count",
        "yes_count",
        "no_count",
        "net_position",
    )
    observed = False
    for key in numeric_keys:
        value = position.get(key)
        if value in (None, ""):
            continue
        observed = True
        try:
            if abs(float(value)) > 1e-9:
                return True
        except (TypeError, ValueError):
            return True
    if not observed:
        return bool(position)
    return False


def _reconciliation_to_dict(report: StartupReconciliationReport) -> Dict[str, Any]:
    return {
        "ok": bool(report.ok),
        "status": report.status,
        "reason": report.reason,
        "checked_at_ms": int(report.checked_at_ms),
        "open_order_count": int(report.open_order_count),
        "position_count": int(report.position_count),
        "open_orders": list(report.open_orders),
        "positions": list(report.positions),
    }
