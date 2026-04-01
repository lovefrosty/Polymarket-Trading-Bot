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
    # Safe-first equity-scaled ladder
    per_trade_loss_pct: float = 0.02
    per_event_loss_pct: float = 0.04
    per_day_loss_pct: float = 0.06
    max_order_notional_pct: float = 0.005
    max_market_exposure_pct: float = 0.03
    max_event_exposure_pct: float = 0.05
    stale_duration_scale: float = 10.0 / 3600.0
    maker_exit_grace_secs: float = 3.0
    cross_escalation_drawdown_pct: float = 0.005
    stop_open_before_expiry_secs: float = 180.0
    force_flat_before_expiry_secs: float = 90.0
    reentry_cooldown_scale: float = 3.0
    strategy_allocated_equity: Optional[float] = None
    use_allocated_equity_for_risk: bool = True
    risk_based_share_sizing: bool = True
    pre_kill_warning_fraction: float = 0.60
    negative_pnl_reduce_only_enabled: bool = True
    negative_pnl_unwind_requires_worsening: bool = True
    negative_pnl_unwind_requires_stale_or_worsening: bool = True


@dataclass(frozen=True)
class RiskDecision:
    action: str
    allow_buy: bool
    allow_sell: bool
    reasons: List[str]
    exit_price: Optional[float] = None
    exit_size: float = 0.0
    sleep_until_ms: Optional[int] = None
    risk_state: str = "normal"
    stale_state: str = "flat"
    exit_mode: Optional[str] = None
    exit_escalation_reason: Optional[str] = None
    event_id: Optional[str] = None
    current_equity: Optional[float] = None
    reference_equity: Optional[float] = None
    max_buy_size: Optional[float] = None
    market_exposure_notional: Optional[float] = None
    event_exposure_notional: Optional[float] = None
    market_unrealized_pnl: Optional[float] = None
    event_unrealized_pnl: Optional[float] = None
    portfolio_total_pnl: Optional[float] = None
    per_trade_loss_budget: Optional[float] = None
    time_to_expiry_ms: Optional[int] = None
    stale_after_ms: Optional[int] = None
    stop_open_triggered: bool = False
    force_flat_triggered: bool = False
    cross_armed: bool = False
    maker_exit_deadline_ms: Optional[int] = None
    flatten_only_triggered: bool = False


@dataclass
class _TokenRiskState:
    entry_ts_ms: Optional[int] = None
    position_size: float = 0.0
    stale_since_ms: Optional[int] = None
    maker_exit_started_at_ms: Optional[int] = None
    maker_exit_entry_size: float = 0.0
    escalation_anchor_unrealized: Optional[float] = None


def _min_positive(values: List[Optional[float]]) -> Optional[float]:
    candidates = [float(value) for value in values if value is not None and float(value) > 0.0]
    if not candidates:
        return None
    return min(candidates)


class RiskManager:
    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or RiskConfig()
        self._sleep_until_by_market: Dict[str, int] = {}
        self._token_states: Dict[str, _TokenRiskState] = {}

    def record_fill(self, *, token_id: str, side: str, ts_ms: Optional[int] = None) -> None:
        token_key = str(token_id or "")
        if not token_key:
            return
        state = self._token_states.setdefault(token_key, _TokenRiskState())
        fill_ts_ms = int(ts_ms) if ts_ms is not None else 0
        side_value = str(side or "").lower()
        if side_value == "buy":
            if state.entry_ts_ms is None:
                state.entry_ts_ms = fill_ts_ms
            state.stale_since_ms = None
            state.maker_exit_started_at_ms = None
            state.maker_exit_entry_size = 0.0
            state.escalation_anchor_unrealized = None
        elif side_value == "sell":
            state.maker_exit_started_at_ms = None
            state.maker_exit_entry_size = 0.0
            state.escalation_anchor_unrealized = None

    def stale_duration_ms(self, market_duration_ms: Optional[int]) -> int:
        duration_ms = int(market_duration_ms or 3_600_000)
        scaled_ms = float(duration_ms) * float(self.config.stale_duration_scale)
        # Hourly BTC buckets should stale around 10s; shorter buckets should not
        # collapse to unusably tiny windows.
        floor_ms = 5_000.0 if duration_ms >= 300_000 else 1_000.0
        return int(max(floor_ms, scaled_ms))

    def expiry_window_ms(self, market_duration_ms: Optional[int], base_secs: float) -> int:
        if base_secs <= 0:
            return 0
        duration_ms = int(market_duration_ms or 3_600_000)
        if duration_ms >= 3_600_000:
            return int(base_secs * 1000.0)
        scaled = float(base_secs) * 1000.0 * (float(duration_ms) / 3_600_000.0)
        return int(max(1_000.0, scaled))

    def reentry_cooldown_ms(self, market_duration_ms: Optional[int]) -> int:
        stale_ms = self.stale_duration_ms(market_duration_ms)
        return int(max(1_000.0, float(stale_ms) * float(self.config.reentry_cooldown_scale)))

    def token_inventory_state(
        self,
        *,
        token_id: str,
        now_ms: int,
        market_duration_ms: Optional[int],
    ) -> Dict[str, Optional[float] | str | bool]:
        token_key = str(token_id or "")
        state = self._token_states.get(token_key)
        stale_after_ms = self.stale_duration_ms(market_duration_ms)
        if state is None or state.entry_ts_ms is None:
            return {
                "entry_ts_ms": None,
                "position_age_ms": None,
                "stale_after_ms": stale_after_ms,
                "stale": False,
                "maker_exit_started_at_ms": None,
                "maker_exit_deadline_ms": None,
                "maker_exit_failed": False,
            }
        position_age_ms = max(0, int(now_ms) - int(state.entry_ts_ms))
        maker_exit_started_at_ms = (
            int(state.maker_exit_started_at_ms)
            if state.maker_exit_started_at_ms is not None
            else None
        )
        if maker_exit_started_at_ms is None and position_age_ms >= stale_after_ms:
            maker_exit_started_at_ms = int(state.entry_ts_ms) + int(stale_after_ms)
        maker_exit_deadline_ms = (
            int(maker_exit_started_at_ms + max(1.0, self.config.maker_exit_grace_secs) * 1000.0)
            if maker_exit_started_at_ms is not None
            else None
        )
        return {
            "entry_ts_ms": int(state.entry_ts_ms),
            "position_age_ms": position_age_ms,
            "stale_after_ms": stale_after_ms,
            "stale": bool(position_age_ms >= stale_after_ms),
            "maker_exit_started_at_ms": maker_exit_started_at_ms,
            "maker_exit_deadline_ms": maker_exit_deadline_ms,
            "maker_exit_failed": bool(
                maker_exit_deadline_ms is not None and int(now_ms) >= int(maker_exit_deadline_ms)
            ),
        }

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
        token_id: Optional[str] = None,
        event_id: Optional[str] = None,
        current_equity: Optional[float] = None,
        reference_equity: Optional[float] = None,
        planned_buy_price: Optional[float] = None,
        market_position_notional: float = 0.0,
        event_position_notional: float = 0.0,
        market_unrealized_pnl: float = 0.0,
        event_unrealized_pnl: float = 0.0,
        portfolio_total_pnl: float = 0.0,
        market_duration_ms: Optional[int] = None,
        time_to_expiry_ms: Optional[int] = None,
    ) -> RiskDecision:
        reasons: List[str] = []
        allow_buy = True
        allow_sell = True
        exit_price: Optional[float] = None
        exit_size = 0.0
        exit_mode: Optional[str] = None
        exit_escalation_reason: Optional[str] = None
        sleep_until_ms = self._sleep_until_by_market.get(str(market_id))

        if sleep_until_ms is not None and int(now_ms) < int(sleep_until_ms):
            reasons.append("sleep_active")
            allow_buy = False
        elif sleep_until_ms is not None and int(now_ms) >= int(sleep_until_ms):
            self._sleep_until_by_market.pop(str(market_id), None)
            sleep_until_ms = None

        token_key = str(token_id or market_id)
        state = self._token_states.setdefault(token_key, _TokenRiskState())
        position = max(0.0, float(position_size))
        avg_cost = max(0.0, float(avg_price))
        mark = float(current_mid) if current_mid is not None else None
        equity_now = max(0.0, float(current_equity or 0.0))
        equity_ref = max(equity_now, float(reference_equity or 0.0))
        if equity_ref <= 0.0:
            equity_ref = equity_now

        stale_after_ms = self.stale_duration_ms(market_duration_ms)
        stale_state = "flat"
        position_age_ms: Optional[int] = None
        if position > 0.0:
            if state.entry_ts_ms is None:
                state.entry_ts_ms = int(now_ms)
            position_age_ms = max(0, int(now_ms) - int(state.entry_ts_ms))
            stale_state = "stale" if position_age_ms >= stale_after_ms else "fresh"
        else:
            state.entry_ts_ms = None
            state.stale_since_ms = None
            state.maker_exit_started_at_ms = None
            state.maker_exit_entry_size = 0.0
            state.escalation_anchor_unrealized = None

        if position < state.position_size - 1e-9:
            state.maker_exit_started_at_ms = int(now_ms)
            state.maker_exit_entry_size = float(position)
            state.escalation_anchor_unrealized = float(market_unrealized_pnl)

        if float(position) >= float(self.config.hard_position_cap):
            reasons.append("position_cap")
            allow_buy = False

        if float(self.config.volatility_threshold) > 0 and float(three_hour_volatility) > float(self.config.volatility_threshold):
            reasons.append("volatility_block")
            state.position_size = float(position)
            return RiskDecision(
                "VOLATILITY_BLOCK",
                False,
                False,
                reasons,
                sleep_until_ms=sleep_until_ms,
                risk_state="volatility_block",
                stale_state=stale_state,
                event_id=event_id,
                current_equity=equity_now,
                reference_equity=equity_ref,
                market_exposure_notional=float(market_position_notional),
                event_exposure_notional=float(event_position_notional),
                market_unrealized_pnl=float(market_unrealized_pnl),
                event_unrealized_pnl=float(event_unrealized_pnl),
                portfolio_total_pnl=float(portfolio_total_pnl),
                per_trade_loss_budget=(equity_ref * float(self.config.per_trade_loss_pct)) if equity_ref > 0 else None,
                time_to_expiry_ms=time_to_expiry_ms,
                stale_after_ms=stale_after_ms,
            )

        stop_open_ms = self.expiry_window_ms(market_duration_ms, self.config.stop_open_before_expiry_secs)
        force_flat_ms = self.expiry_window_ms(market_duration_ms, self.config.force_flat_before_expiry_secs)
        stop_open_triggered = False
        force_flat_triggered = False
        if time_to_expiry_ms is not None and time_to_expiry_ms <= stop_open_ms:
            stop_open_triggered = True
            allow_buy = False
            reasons.append("stop_open_before_expiry")

        max_buy_size = self._max_buy_size(
            equity_now=equity_now,
            equity_ref=equity_ref,
            buy_price=planned_buy_price or current_mid or best_ask or best_bid,
            market_position_notional=float(market_position_notional),
            event_position_notional=float(event_position_notional),
        )
        if max_buy_size is not None and max_buy_size <= 1e-9:
            allow_buy = False
            if equity_now > 0.0 and self.config.max_order_notional_pct > 0:
                reasons.append("order_notional_cap")
            if equity_now > 0.0 and self.config.max_market_exposure_pct > 0 and float(market_position_notional) >= equity_now * float(self.config.max_market_exposure_pct):
                reasons.append("market_exposure_cap")
            if equity_now > 0.0 and self.config.max_event_exposure_pct > 0 and float(event_position_notional) >= equity_now * float(self.config.max_event_exposure_pct):
                reasons.append("event_exposure_cap")

        risk_state = "normal"
        cross_armed = False
        maker_exit_deadline_ms: Optional[int] = None
        per_trade_loss_budget = (equity_ref * float(self.config.per_trade_loss_pct)) if equity_ref > 0 else None
        day_loss_budget = (equity_ref * float(self.config.per_day_loss_pct)) if equity_ref > 0 else None
        event_loss_budget = (equity_ref * float(self.config.per_event_loss_pct)) if equity_ref > 0 else None

        if position > 0.0 and time_to_expiry_ms is not None and time_to_expiry_ms <= force_flat_ms:
            force_flat_triggered = True
            reasons.append("force_flat_before_expiry")
            risk_state = "force_flat"
            exit_price, exit_mode, exit_escalation_reason, cross_armed, maker_exit_deadline_ms = self._exit_route(
                state=state,
                now_ms=now_ms,
                position_size=position,
                market_unrealized_pnl=float(market_unrealized_pnl),
                best_bid=best_bid,
                best_ask=best_ask,
                equity_ref=equity_ref,
                action="FORCE_FLAT",
                maker_preferred=time_to_expiry_ms > int(max(1.0, self.config.maker_exit_grace_secs) * 1000.0),
            )
            exit_size = float(position)
            allow_buy = False
            state.position_size = float(position)
            return RiskDecision(
                "FORCE_FLAT",
                allow_buy,
                True,
                reasons,
                exit_price=exit_price,
                exit_size=exit_size,
                sleep_until_ms=sleep_until_ms,
                risk_state=risk_state,
                stale_state=stale_state,
                exit_mode=exit_mode,
                exit_escalation_reason=exit_escalation_reason,
                event_id=event_id,
                current_equity=equity_now,
                reference_equity=equity_ref,
                max_buy_size=max_buy_size,
                market_exposure_notional=float(market_position_notional),
                event_exposure_notional=float(event_position_notional),
                market_unrealized_pnl=float(market_unrealized_pnl),
                event_unrealized_pnl=float(event_unrealized_pnl),
                portfolio_total_pnl=float(portfolio_total_pnl),
                per_trade_loss_budget=per_trade_loss_budget,
                time_to_expiry_ms=time_to_expiry_ms,
                stale_after_ms=stale_after_ms,
                stop_open_triggered=stop_open_triggered,
                force_flat_triggered=True,
                cross_armed=cross_armed,
                maker_exit_deadline_ms=maker_exit_deadline_ms,
                flatten_only_triggered=True,
            )

        if position > 0.0 and day_loss_budget is not None and float(portfolio_total_pnl) <= -day_loss_budget:
            reasons.append("portfolio_day_loss")
            risk_state = "day_loss_risk_off"
            exit_price, exit_mode, exit_escalation_reason, cross_armed, maker_exit_deadline_ms = self._exit_route(
                state=state,
                now_ms=now_ms,
                position_size=position,
                market_unrealized_pnl=float(market_unrealized_pnl),
                best_bid=best_bid,
                best_ask=best_ask,
                equity_ref=equity_ref,
                action="DAY_LOSS_CAP",
            )
            exit_size = float(position)
            allow_buy = False
            state.position_size = float(position)
            return RiskDecision(
                "DAY_LOSS_CAP",
                False,
                True,
                reasons,
                exit_price=exit_price,
                exit_size=exit_size,
                sleep_until_ms=sleep_until_ms,
                risk_state=risk_state,
                stale_state=stale_state,
                exit_mode=exit_mode,
                exit_escalation_reason=exit_escalation_reason,
                event_id=event_id,
                current_equity=equity_now,
                reference_equity=equity_ref,
                max_buy_size=0.0,
                market_exposure_notional=float(market_position_notional),
                event_exposure_notional=float(event_position_notional),
                market_unrealized_pnl=float(market_unrealized_pnl),
                event_unrealized_pnl=float(event_unrealized_pnl),
                portfolio_total_pnl=float(portfolio_total_pnl),
                per_trade_loss_budget=per_trade_loss_budget,
                time_to_expiry_ms=time_to_expiry_ms,
                stale_after_ms=stale_after_ms,
                stop_open_triggered=stop_open_triggered,
                force_flat_triggered=force_flat_triggered,
                cross_armed=cross_armed,
                maker_exit_deadline_ms=maker_exit_deadline_ms,
                flatten_only_triggered=True,
            )

        if position > 0.0 and event_loss_budget is not None and float(event_unrealized_pnl) <= -event_loss_budget:
            reasons.append("event_loss_cap")
            risk_state = "event_risk_off"
            exit_price, exit_mode, exit_escalation_reason, cross_armed, maker_exit_deadline_ms = self._exit_route(
                state=state,
                now_ms=now_ms,
                position_size=position,
                market_unrealized_pnl=float(market_unrealized_pnl),
                best_bid=best_bid,
                best_ask=best_ask,
                equity_ref=equity_ref,
                action="EVENT_DE_RISK",
            )
            exit_size = float(position)
            allow_buy = False
            state.position_size = float(position)
            return RiskDecision(
                "EVENT_DE_RISK",
                False,
                True,
                reasons,
                exit_price=exit_price,
                exit_size=exit_size,
                sleep_until_ms=sleep_until_ms,
                risk_state=risk_state,
                stale_state=stale_state,
                exit_mode=exit_mode,
                exit_escalation_reason=exit_escalation_reason,
                event_id=event_id,
                current_equity=equity_now,
                reference_equity=equity_ref,
                max_buy_size=0.0,
                market_exposure_notional=float(market_position_notional),
                event_exposure_notional=float(event_position_notional),
                market_unrealized_pnl=float(market_unrealized_pnl),
                event_unrealized_pnl=float(event_unrealized_pnl),
                portfolio_total_pnl=float(portfolio_total_pnl),
                per_trade_loss_budget=per_trade_loss_budget,
                time_to_expiry_ms=time_to_expiry_ms,
                stale_after_ms=stale_after_ms,
                stop_open_triggered=stop_open_triggered,
                force_flat_triggered=force_flat_triggered,
                cross_armed=cross_armed,
                maker_exit_deadline_ms=maker_exit_deadline_ms,
                flatten_only_triggered=True,
            )

        if position > 0.0 and avg_cost > 0.0 and mark is not None:
            pnl_pct = ((float(mark) - float(avg_cost)) / float(avg_cost)) * 100.0
            stop_loss_hit = False
            if per_trade_loss_budget is not None and float(market_unrealized_pnl) <= -per_trade_loss_budget:
                stop_loss_hit = True
                reasons.append("per_trade_loss_cap")
            if (
                pnl_pct < float(self.config.stop_loss_threshold_pct)
                and spread_bps is not None
                and float(spread_bps) <= float(self.config.stop_loss_max_spread_bps)
                and best_ask is not None
            ):
                stop_loss_hit = True
                reasons.append("stop_loss")
            if stop_loss_hit:
                if float(self.config.sleep_hours) > 0:
                    sleep_until_ms = int(now_ms + self.config.sleep_hours * 3600 * 1000)
                    self._sleep_until_by_market[str(market_id)] = sleep_until_ms
                risk_state = "stop_loss_exit"
                exit_price, exit_mode, exit_escalation_reason, cross_armed, maker_exit_deadline_ms = self._exit_route(
                    state=state,
                    now_ms=now_ms,
                    position_size=position,
                    market_unrealized_pnl=float(market_unrealized_pnl),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    equity_ref=equity_ref,
                    action="STOP_LOSS",
                )
                exit_size = float(position)
                allow_buy = False
                state.position_size = float(position)
                return RiskDecision(
                    "STOP_LOSS",
                    False,
                    True,
                    reasons,
                    exit_price=exit_price,
                    exit_size=exit_size,
                    sleep_until_ms=sleep_until_ms,
                    risk_state=risk_state,
                    stale_state=stale_state,
                    exit_mode=exit_mode,
                    exit_escalation_reason=exit_escalation_reason,
                    event_id=event_id,
                    current_equity=equity_now,
                    reference_equity=equity_ref,
                    max_buy_size=max_buy_size,
                    market_exposure_notional=float(market_position_notional),
                    event_exposure_notional=float(event_position_notional),
                    market_unrealized_pnl=float(market_unrealized_pnl),
                    event_unrealized_pnl=float(event_unrealized_pnl),
                    portfolio_total_pnl=float(portfolio_total_pnl),
                    per_trade_loss_budget=per_trade_loss_budget,
                    time_to_expiry_ms=time_to_expiry_ms,
                    stale_after_ms=stale_after_ms,
                    stop_open_triggered=stop_open_triggered,
                    force_flat_triggered=force_flat_triggered,
                    cross_armed=cross_armed,
                    maker_exit_deadline_ms=maker_exit_deadline_ms,
                )

            take_profit_price = float(avg_cost) + (float(avg_cost) * float(self.config.take_profit_pct) / 100.0)
            if best_ask is not None and float(mark) >= take_profit_price:
                reasons.append("take_profit")
                allow_buy = False
                exit_size = float(position)
                exit_price = max(float(best_ask), take_profit_price)
                state.position_size = float(position)
                return RiskDecision(
                    "TAKE_PROFIT",
                    False,
                    True,
                    reasons,
                    exit_price=exit_price,
                    exit_size=exit_size,
                    sleep_until_ms=sleep_until_ms,
                    risk_state="take_profit_exit",
                    stale_state=stale_state,
                    exit_mode="maker",
                    event_id=event_id,
                    current_equity=equity_now,
                    reference_equity=equity_ref,
                    max_buy_size=max_buy_size,
                    market_exposure_notional=float(market_position_notional),
                    event_exposure_notional=float(event_position_notional),
                    market_unrealized_pnl=float(market_unrealized_pnl),
                    event_unrealized_pnl=float(event_unrealized_pnl),
                    portfolio_total_pnl=float(portfolio_total_pnl),
                    per_trade_loss_budget=per_trade_loss_budget,
                    time_to_expiry_ms=time_to_expiry_ms,
                    stale_after_ms=stale_after_ms,
                    stop_open_triggered=stop_open_triggered,
                    force_flat_triggered=force_flat_triggered,
                    cross_armed=False,
                    maker_exit_deadline_ms=int(now_ms + max(1.0, self.config.maker_exit_grace_secs) * 1000.0),
                )

        if position > 0.0 and stale_state == "stale":
            reasons.append("stale_position")
            risk_state = "stale_unwind"
            exit_price, exit_mode, exit_escalation_reason, cross_armed, maker_exit_deadline_ms = self._exit_route(
                state=state,
                now_ms=now_ms,
                position_size=position,
                market_unrealized_pnl=float(market_unrealized_pnl),
                best_bid=best_bid,
                best_ask=best_ask,
                equity_ref=equity_ref,
                action="STALE_UNWIND",
            )
            exit_size = float(position)
            allow_buy = False
            state.position_size = float(position)
            return RiskDecision(
                "STALE_UNWIND",
                False,
                True,
                reasons,
                exit_price=exit_price,
                exit_size=exit_size,
                sleep_until_ms=sleep_until_ms,
                risk_state=risk_state,
                stale_state=stale_state,
                exit_mode=exit_mode,
                exit_escalation_reason=exit_escalation_reason,
                event_id=event_id,
                current_equity=equity_now,
                reference_equity=equity_ref,
                max_buy_size=max_buy_size,
                market_exposure_notional=float(market_position_notional),
                event_exposure_notional=float(event_position_notional),
                market_unrealized_pnl=float(market_unrealized_pnl),
                event_unrealized_pnl=float(event_unrealized_pnl),
                portfolio_total_pnl=float(portfolio_total_pnl),
                per_trade_loss_budget=per_trade_loss_budget,
                time_to_expiry_ms=time_to_expiry_ms,
                stale_after_ms=stale_after_ms,
                stop_open_triggered=stop_open_triggered,
                force_flat_triggered=force_flat_triggered,
                cross_armed=cross_armed,
                maker_exit_deadline_ms=maker_exit_deadline_ms,
            )

        state.position_size = float(position)
        if reasons:
            return RiskDecision(
                "LIMIT_BUYS",
                allow_buy,
                allow_sell,
                reasons,
                sleep_until_ms=sleep_until_ms,
                risk_state="limit_buys",
                stale_state=stale_state,
                event_id=event_id,
                current_equity=equity_now,
                reference_equity=equity_ref,
                max_buy_size=max_buy_size,
                market_exposure_notional=float(market_position_notional),
                event_exposure_notional=float(event_position_notional),
                market_unrealized_pnl=float(market_unrealized_pnl),
                event_unrealized_pnl=float(event_unrealized_pnl),
                portfolio_total_pnl=float(portfolio_total_pnl),
                per_trade_loss_budget=per_trade_loss_budget,
                time_to_expiry_ms=time_to_expiry_ms,
                stale_after_ms=stale_after_ms,
                stop_open_triggered=stop_open_triggered,
                force_flat_triggered=force_flat_triggered,
            )
        return RiskDecision(
            "NORMAL",
            allow_buy,
            allow_sell,
            [],
            sleep_until_ms=sleep_until_ms,
            risk_state=risk_state,
            stale_state=stale_state,
            event_id=event_id,
            current_equity=equity_now,
            reference_equity=equity_ref,
            max_buy_size=max_buy_size,
            market_exposure_notional=float(market_position_notional),
            event_exposure_notional=float(event_position_notional),
            market_unrealized_pnl=float(market_unrealized_pnl),
            event_unrealized_pnl=float(event_unrealized_pnl),
            portfolio_total_pnl=float(portfolio_total_pnl),
            per_trade_loss_budget=per_trade_loss_budget,
            time_to_expiry_ms=time_to_expiry_ms,
            stale_after_ms=stale_after_ms,
            stop_open_triggered=stop_open_triggered,
            force_flat_triggered=force_flat_triggered,
        )

    def _max_buy_size(
        self,
        *,
        equity_now: float,
        equity_ref: float,
        buy_price: Optional[float],
        market_position_notional: float,
        event_position_notional: float,
    ) -> Optional[float]:
        price = float(buy_price or 0.0)
        if price <= 0.0:
            return None
        effective_equity_now = max(0.0, float(equity_now))
        effective_equity_ref = max(effective_equity_now, float(equity_ref))
        risk_cap = (
            (effective_equity_ref * float(self.config.per_trade_loss_pct)) / price
            if bool(self.config.risk_based_share_sizing)
            and effective_equity_ref > 0.0
            and float(self.config.per_trade_loss_pct) > 0.0
            else None
        )
        order_cap = (
            effective_equity_now * float(self.config.max_order_notional_pct)
            if effective_equity_now > 0.0 and float(self.config.max_order_notional_pct) > 0.0
            else None
        )
        market_cap = (
            effective_equity_now * float(self.config.max_market_exposure_pct)
            if effective_equity_now > 0.0 and float(self.config.max_market_exposure_pct) > 0.0
            else None
        )
        event_cap = (
            effective_equity_now * float(self.config.max_event_exposure_pct)
            if effective_equity_now > 0.0 and float(self.config.max_event_exposure_pct) > 0.0
            else None
        )
        remaining_market = None if market_cap is None else max(0.0, float(market_cap) - float(market_position_notional))
        remaining_event = None if event_cap is None else max(0.0, float(event_cap) - float(event_position_notional))
        if remaining_market is not None and remaining_market <= 0.0:
            return 0.0
        if remaining_event is not None and remaining_event <= 0.0:
            return 0.0
        notional_cap = _min_positive([order_cap, remaining_market, remaining_event])
        notional_share_cap = None if notional_cap is None else max(0.0, float(notional_cap) / price)
        share_cap = _min_positive([risk_cap, notional_share_cap])
        if share_cap is not None:
            return max(0.0, float(share_cap))
        if risk_cap is not None:
            return max(0.0, float(risk_cap))
        if notional_share_cap is not None:
            return max(0.0, float(notional_share_cap))
        return None

    def _exit_route(
        self,
        *,
        state: _TokenRiskState,
        now_ms: int,
        position_size: float,
        market_unrealized_pnl: float,
        best_bid: Optional[float],
        best_ask: Optional[float],
        equity_ref: float,
        action: str,
        maker_preferred: bool = True,
    ) -> tuple[Optional[float], Optional[str], Optional[str], bool, Optional[int]]:
        if state.maker_exit_started_at_ms is None or position_size > state.maker_exit_entry_size + 1e-9:
            state.maker_exit_started_at_ms = int(now_ms)
            state.maker_exit_entry_size = float(position_size)
            state.escalation_anchor_unrealized = float(market_unrealized_pnl)
        maker_exit_deadline_ms = int(state.maker_exit_started_at_ms + max(1.0, self.config.maker_exit_grace_secs) * 1000.0)
        cross_armed = False
        escalation_reason: Optional[str] = None
        exit_mode = "maker"
        exit_price = float(best_ask) if best_ask is not None else None

        worsening_budget = float(equity_ref) * float(self.config.cross_escalation_drawdown_pct)
        anchor = float(state.escalation_anchor_unrealized or 0.0)
        if (
            best_bid is not None
            and int(now_ms) >= maker_exit_deadline_ms
            and float(market_unrealized_pnl) <= anchor - worsening_budget
        ):
            exit_mode = "cross"
            exit_price = float(best_bid)
            escalation_reason = f"{action.lower()}_maker_failed"
            cross_armed = True
        elif not maker_preferred and best_bid is not None:
            exit_mode = "cross"
            exit_price = float(best_bid)
            escalation_reason = f"{action.lower()}_expiry_urgent"
            cross_armed = True

        if exit_price is None and best_bid is not None:
            exit_mode = "cross"
            exit_price = float(best_bid)
            escalation_reason = escalation_reason or f"{action.lower()}_fallback_cross"
            cross_armed = True
        return exit_price, exit_mode, escalation_reason, cross_armed, maker_exit_deadline_ms
