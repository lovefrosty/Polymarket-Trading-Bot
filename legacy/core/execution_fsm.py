from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExecutionState(str, Enum):
    FROZEN = "FROZEN"
    QUOTING_BOTH = "QUOTING_BOTH"
    ONE_SIDE_FILLED = "ONE_SIDE_FILLED"
    REBALANCING = "REBALANCING"
    UNWINDING = "UNWINDING"


@dataclass(frozen=True)
class FSMStatus:
    state: ExecutionState
    net_qty: float
    one_leg_since_ms: Optional[int]
    reason: Optional[str]


class ExecutionFSM:
    def __init__(self, rebalance_timeout_ms: int = 5000, flat_epsilon: float = 1e-9) -> None:
        self.rebalance_timeout_ms = int(rebalance_timeout_ms)
        self.flat_epsilon = float(flat_epsilon)
        self._state = ExecutionState.QUOTING_BOTH
        self._net_qty = 0.0
        self._one_leg_since_ms: Optional[int] = None
        self._reason: Optional[str] = None

    def freeze(self, reason: str) -> None:
        self._state = ExecutionState.FROZEN
        self._reason = str(reason)

    def unfreeze(self) -> None:
        if self._state == ExecutionState.FROZEN and self._is_flat():
            self._state = ExecutionState.QUOTING_BOTH
            self._reason = None
            self._one_leg_since_ms = None

    def on_fill(self, side: str, qty: float, ts_ms: int) -> None:
        signed = float(qty) if side.lower() in {"buy", "bid"} else -float(qty)
        self._net_qty += signed
        if self._is_flat():
            self._state = ExecutionState.QUOTING_BOTH
            self._one_leg_since_ms = None
            self._reason = None
            return
        if self._state not in {ExecutionState.UNWINDING, ExecutionState.FROZEN}:
            self._state = ExecutionState.ONE_SIDE_FILLED
            if self._one_leg_since_ms is None:
                self._one_leg_since_ms = int(ts_ms)

    def mark_rebalancing(self) -> None:
        if self._state in {ExecutionState.ONE_SIDE_FILLED, ExecutionState.REBALANCING}:
            self._state = ExecutionState.REBALANCING

    def mark_unwinding(self, reason: str = "hedge_timeout") -> None:
        self._state = ExecutionState.UNWINDING
        self._reason = str(reason)

    def on_rebalance_tick(self, now_ms: int) -> bool:
        if self._state not in {ExecutionState.ONE_SIDE_FILLED, ExecutionState.REBALANCING}:
            return False
        if self._one_leg_since_ms is None:
            return False
        if int(now_ms) - self._one_leg_since_ms > self.rebalance_timeout_ms:
            self.mark_unwinding("hedge_timeout")
            return True
        return False

    def reset_if_flat(self) -> None:
        if self._is_flat():
            self._state = ExecutionState.QUOTING_BOTH
            self._one_leg_since_ms = None
            self._reason = None

    def apply_external_position(self, net_qty: float) -> None:
        self._net_qty = float(net_qty)
        self.reset_if_flat()

    def status(self) -> FSMStatus:
        return FSMStatus(
            state=self._state,
            net_qty=self._net_qty,
            one_leg_since_ms=self._one_leg_since_ms,
            reason=self._reason,
        )

    def _is_flat(self) -> bool:
        return abs(self._net_qty) <= self.flat_epsilon

