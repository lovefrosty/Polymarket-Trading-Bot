from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class HedgeState(str, Enum):
    IDLE = "idle"
    PRIMARY_SUBMITTED = "primary_submitted"
    PRIMARY_FILLED = "primary_filled"
    HEDGE_SUBMITTED = "hedge_submitted"
    COMPLETE = "complete"
    UNWINDING = "unwinding"
    UNWOUND = "unwound"


@dataclass(frozen=True)
class BrokerState:
    primary_position: float
    hedge_position: float
    ts: int


@dataclass
class HedgeStatus:
    state: HedgeState = HedgeState.IDLE
    primary_filled_qty: float = 0.0
    hedge_filled_qty: float = 0.0
    primary_fill_ts: Optional[int] = None
    hedge_deadline_ts: Optional[int] = None

    def net_exposure(self, broker_state: BrokerState) -> float:
        return broker_state.primary_position + broker_state.hedge_position


class HedgeStateMachine:
    def __init__(self, hedge_timeout_ms: int = 5000, flat_exposure_epsilon: float = 1e-9) -> None:
        self.hedge_timeout_ms = hedge_timeout_ms
        self.flat_exposure_epsilon = flat_exposure_epsilon
        self.status = HedgeStatus()

    def submit_primary(self, ts: int) -> None:
        self.status.state = HedgeState.PRIMARY_SUBMITTED
        self.status.primary_fill_ts = None
        self.status.hedge_deadline_ts = None
        self.status.primary_filled_qty = 0.0
        self.status.hedge_filled_qty = 0.0

    def primary_filled(self, filled_qty: float, ts: int, broker_state: BrokerState) -> None:
        self.status.state = HedgeState.PRIMARY_FILLED
        self.status.primary_filled_qty = filled_qty
        self.status.primary_fill_ts = ts
        self.status.hedge_deadline_ts = ts + self.hedge_timeout_ms
        _ = broker_state  # Broker state is the source of truth for exposure.

    def submit_hedge(self, ts: int) -> None:
        if self.status.state not in {HedgeState.PRIMARY_FILLED, HedgeState.HEDGE_SUBMITTED}:
            return
        self.status.state = HedgeState.HEDGE_SUBMITTED

    def hedge_filled(self, filled_qty: float, ts: int, broker_state: BrokerState) -> None:
        self.status.hedge_filled_qty = filled_qty
        _ = broker_state
        if filled_qty >= self.status.primary_filled_qty and self.status.primary_filled_qty > 0:
            self.status.state = HedgeState.COMPLETE

    def tick(self, ts: int, broker_state: BrokerState) -> Optional["UnwindIntent"]:
        unwind_intent: Optional[UnwindIntent] = None
        if self.status.state in {HedgeState.PRIMARY_FILLED, HedgeState.HEDGE_SUBMITTED}:
            deadline = self.status.hedge_deadline_ts
            if deadline is not None and ts >= deadline and self.status.hedge_filled_qty < self.status.primary_filled_qty:
                self.status.state = HedgeState.UNWINDING
                unwind_intent = self._build_unwind_intent(broker_state)

        if self.status.state == HedgeState.UNWINDING and self._is_flat(broker_state):
            self.status.state = HedgeState.UNWOUND

        return unwind_intent

    def unwind_complete(self, ts: int, broker_state: BrokerState) -> None:
        _ = ts
        _ = broker_state
        self.status.state = HedgeState.UNWOUND

    def block_new_exposure(self, broker_state: BrokerState) -> bool:
        if self.status.state in {HedgeState.PRIMARY_FILLED, HedgeState.HEDGE_SUBMITTED, HedgeState.UNWINDING}:
            return True
        if self.status.state == HedgeState.UNWOUND and not self._is_flat(broker_state):
            return True
        return False

    def _is_flat(self, broker_state: BrokerState) -> bool:
        return abs(self.status.net_exposure(broker_state)) <= self.flat_exposure_epsilon

    def _build_unwind_intent(self, broker_state: BrokerState) -> Optional["UnwindIntent"]:
        net = self.status.net_exposure(broker_state)
        if abs(net) <= self.flat_exposure_epsilon:
            return None
        side = "sell" if net > 0 else "buy"
        return UnwindIntent(side=side, qty=abs(net), reason="hedge_timeout_unwind")


@dataclass(frozen=True)
class UnwindIntent:
    side: str
    qty: float
    reason: str
