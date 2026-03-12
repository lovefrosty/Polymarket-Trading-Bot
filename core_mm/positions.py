from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class TokenPosition:
    size: float = 0.0
    avg_price: float = 0.0


@dataclass(frozen=True)
class MergeDecision:
    executed: bool
    amount_to_merge: float
    freed_usdc: float


class PositionTracker:
    def __init__(self) -> None:
        self._positions: Dict[str, TokenPosition] = {}

    def set_position(self, token_id: str, *, size: float, avg_price: float) -> None:
        self._positions[str(token_id)] = TokenPosition(size=max(0.0, float(size)), avg_price=max(0.0, float(avg_price)))

    def get_position(self, token_id: str) -> TokenPosition:
        return self._positions.get(str(token_id), TokenPosition())

    def snapshot(self) -> Dict[str, TokenPosition]:
        return dict(self._positions)

    def apply_fill(self, *, token_id: str, side: str, size: float, price: float) -> TokenPosition:
        token = str(token_id)
        current = self.get_position(token)
        fill_size = max(0.0, float(size))
        fill_price = max(0.0, float(price))
        side_value = str(side).lower()

        if side_value == "buy":
            new_size = current.size + fill_size
            if new_size <= 0:
                updated = TokenPosition()
            else:
                new_avg = ((current.size * current.avg_price) + (fill_size * fill_price)) / new_size
                updated = TokenPosition(size=new_size, avg_price=new_avg)
        elif side_value == "sell":
            new_size = max(0.0, current.size - fill_size)
            updated = TokenPosition(size=new_size, avg_price=(current.avg_price if new_size > 0 else 0.0))
        else:
            raise ValueError(f"unsupported side: {side}")

        self._positions[token] = updated
        return updated

    def merge_positions(self, token_a: str, token_b: str, *, min_merge_size: float = 20.0) -> MergeDecision:
        pos_a = self.get_position(token_a)
        pos_b = self.get_position(token_b)
        amount = min(pos_a.size, pos_b.size)
        if amount < float(min_merge_size):
            return MergeDecision(False, 0.0, 0.0)

        remaining_a = max(0.0, pos_a.size - amount)
        remaining_b = max(0.0, pos_b.size - amount)
        self._positions[str(token_a)] = TokenPosition(size=remaining_a, avg_price=(pos_a.avg_price if remaining_a > 0 else 0.0))
        self._positions[str(token_b)] = TokenPosition(size=remaining_b, avg_price=(pos_b.avg_price if remaining_b > 0 else 0.0))
        return MergeDecision(True, float(amount), float(amount))
