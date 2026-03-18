from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class RiskConfig:
    stop_loss_threshold_pct: float = -5.0
    stop_loss_max_spread_bps: float = 300.0
    take_profit_pct: float = 5.0
    sleep_hours: float = 2.0
    volatility_threshold: float = 0.0
    hard_position_cap: float = 250.0
    # Aggregate multi-market limits (0 = disabled)
    max_total_position_notional: float = 0.0
    max_markets_with_position: int = 0


@dataclass(frozen=True)
class RiskDecision:
    action: str
    allow_buy: bool
    allow_sell: bool
    reasons: List[str]
    exit_price: Optional[float] = None
    exit_size: float = 0.0
    sleep_until_ms: Optional[int] = None


class RiskManager:
    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or RiskConfig()
        self._sleep_until_by_market: Dict[str, int] = {}

    def evaluate(
        self,
        *,
        market_id: str,
        now_ms: int,
        position_size: float,
        avg_price: float,
        current_mid: Optional[float],
        best_bid: Optional[float],
        best_ask: Optional[float],
        spread_bps: Optional[float],
        three_hour_volatility: float = 0.0,
    ) -> RiskDecision:
        reasons: List[str] = []
        allow_buy = True
        allow_sell = True
        exit_price: Optional[float] = None
        exit_size = 0.0
        sleep_until_ms = self._sleep_until_by_market.get(str(market_id))

        if sleep_until_ms is not None and int(now_ms) < int(sleep_until_ms):
            reasons.append("sleep_active")
            allow_buy = False
        elif sleep_until_ms is not None and int(now_ms) >= int(sleep_until_ms):
            self._sleep_until_by_market.pop(str(market_id), None)
            sleep_until_ms = None

        if float(position_size) >= float(self.config.hard_position_cap):
            reasons.append("position_cap")
            allow_buy = False

        if float(self.config.volatility_threshold) > 0 and float(three_hour_volatility) > float(self.config.volatility_threshold):
            reasons.append("volatility_block")
            return RiskDecision("VOLATILITY_BLOCK", False, False, reasons, sleep_until_ms=sleep_until_ms)

        if float(position_size) > 0 and float(avg_price) > 0 and current_mid is not None:
            pnl_pct = ((float(current_mid) - float(avg_price)) / float(avg_price)) * 100.0
            if (
                pnl_pct < float(self.config.stop_loss_threshold_pct)
                and spread_bps is not None
                and float(spread_bps) <= float(self.config.stop_loss_max_spread_bps)
                and best_bid is not None
            ):
                sleep_until_ms = int(now_ms + self.config.sleep_hours * 3600 * 1000)
                self._sleep_until_by_market[str(market_id)] = sleep_until_ms
                reasons.append("stop_loss")
                return RiskDecision(
                    "STOP_LOSS",
                    False,
                    True,
                    reasons,
                    exit_price=float(best_bid),
                    exit_size=float(position_size),
                    sleep_until_ms=sleep_until_ms,
                )

            take_profit_price = float(avg_price) + (float(avg_price) * float(self.config.take_profit_pct) / 100.0)
            if best_ask is not None and float(current_mid) >= take_profit_price:
                reasons.append("take_profit")
                allow_buy = False
                exit_price = max(float(best_ask), take_profit_price)
                exit_size = float(position_size)
                return RiskDecision(
                    "TAKE_PROFIT",
                    allow_buy,
                    True,
                    reasons,
                    exit_price=exit_price,
                    exit_size=exit_size,
                    sleep_until_ms=sleep_until_ms,
                )

        if reasons:
            return RiskDecision("LIMIT_BUYS", allow_buy, allow_sell, reasons, sleep_until_ms=sleep_until_ms)
        return RiskDecision("NORMAL", allow_buy, allow_sell, [], sleep_until_ms=sleep_until_ms)
