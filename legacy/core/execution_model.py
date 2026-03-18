from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FeeModel:
    fee_rate: float
    mode: Literal["taker", "maker"]
    model_version: str = "v1_flat"

    def estimate_fee(self, notional: float) -> float:
        return max(0.0, notional * self.fee_rate)
