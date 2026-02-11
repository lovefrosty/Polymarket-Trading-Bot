from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid
from typing import Any, Deque, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import load_markets, load_settings, validate_markets_config
from core.book_cache import BookCache, BookHealthState, BookSnapshot
from core.broker_base import BrokerEvent, BrokerSnapshot, OrderIntent
from core.broker_polymarket import (
    EXPECTED_CLOB_CLIENT_VERSION,
    BrokerContractError,
    PolymarketBroker,
    PolymarketBrokerConfig,
)
from core.broker_sim import SimBroker, SimBrokerConfig
from core.decision_tape import DecisionRecord, DecisionTape, TimeMapper
from core.event_tape import EventTape
from core.execution_fsm import ExecutionFSM, ExecutionState
from core.market_discovery import GAMMA_BASE_URL, resolve_markets
from core.metrics import Metrics
from core.order_book import OrderBook
from core.policy_gate import PolicyContext, PolicyThresholds, PolicyVerdict, evaluate_policy
from core.pstar import PStar, PStarBuilder
from core.reference_feed import ReferenceFeed, ReferenceFeedConfig
from core.reference_ws import ReferenceWSClient, ReferenceWSConfig
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from data.polymarket_ws import MarketWSClient, WSConfig


def _now_ms() -> int:
    return int(time.time() * 1000)


def _utc_iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _round_down(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return (int(price / tick)) * tick


def _round_up(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return (int((price + tick - 1e-12) / tick)) * tick


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(max(0, min(len(ordered) - 1, round((len(ordered) - 1) * q))))
    return float(ordered[idx])


@dataclass
class OpenQuote:
    order_id: str
    client_order_id: str
    side: str
    price: float
    qty: float
    post_only: bool
    quote_group_id: str
    idempotency_key: str
    updated_ms: int


class RuntimeEngine:
    def __init__(
        self,
        mode: str,
        db: SQLiteStore,
        decision_tape: DecisionTape,
        trade_tape: TradeTape,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        market_meta: Dict[str, Dict[str, Any]],
        pstar_builder: PStarBuilder,
        policy_thresholds: PolicyThresholds,
        constitution: Dict[str, Any],
        time_mapper: TimeMapper,
        broker: Optional[Any],
        run_epoch_ms: int,
    ) -> None:
        self.mode = mode
        self.db = db
        self.decision_tape = decision_tape
        self.trade_tape = trade_tape
        self.books = books
        self.constraints = constraints
        self.market_meta = market_meta
        self.pstar_builder = pstar_builder
        self.policy_thresholds = policy_thresholds
        self.constitution = constitution
        self.time_mapper = time_mapper
        self.broker = broker
        self.run_epoch_ms = int(run_epoch_ms)
        self.book_cache = BookCache()
        self.fsms: Dict[str, ExecutionFSM] = {
            token: ExecutionFSM(rebalance_timeout_ms=self.policy_thresholds.hedge_timeout_ms)
            for token in books.keys()
        }
        self._last_fsm_state_by_token: Dict[str, str] = {
            token: ExecutionState.QUOTING_BOTH.value for token in books.keys()
        }
        self.inventory_yes: Dict[str, float] = defaultdict(float)
        self.inventory_no: Dict[str, float] = defaultdict(float)
        self.open_quotes: Dict[str, Dict[str, OpenQuote]] = defaultdict(dict)
        self.last_pstar_value: Dict[str, float] = {}
        self.last_pstar_ts: Dict[str, int] = {}
        self.send_ts_by_order: Dict[str, int] = {}
        self.ack_ts_by_order: Dict[str, int] = {}
        self.send_ack_samples: Deque[float] = deque(maxlen=2000)
        self.ack_fill_samples: Deque[float] = deque(maxlen=2000)
        self.signal_age_samples: Deque[float] = deque(maxlen=2000)
        self.ws_lag_samples: Deque[float] = deque(maxlen=2000)
        self.pending_freeze: Dict[str, List[str]] = defaultdict(list)
        self._trade_tape_parent_event_ids: Dict[str, int] = {}
        self._unwind_state: Dict[str, Dict[str, Any]] = {}
        self._decision_seq = 0
        self._quote_revision: Dict[Tuple[str, str], int] = defaultdict(int)
        self._post_only_attempts_by_token: Dict[str, int] = defaultdict(int)
        self._post_only_rejects_by_token: Dict[str, int] = defaultdict(int)
        self._last_q_by_token: Dict[str, float] = defaultdict(lambda: 0.5)
        self._book_stale_after_ms = int(self.constitution.get("policy", {}).get("book_stale_after_ms", 30_000))
        self._book_down_after_ms = int(self.constitution.get("policy", {}).get("book_down_after_ms", 120_000))
        self._cap_state: Dict[str, Dict[str, float]] = self._build_caps(
            trading_cfg=self.constitution.get("trading", {}),
            execution_cfg=self.constitution.get("execution", {}),
        )

    def on_reference(self, source: str, symbol: str, value: float, ts_event_ms: Optional[int], ts_recv_ms: int) -> None:
        event_ts = int(ts_event_ms if ts_event_ms is not None else ts_recv_ms)
        self.pstar_builder.ingest(source=source, symbol=symbol, value=value, ts_event_ms=event_ts, ts_recv_wall_ms=ts_recv_ms)

    async def run_quote_cycle(self, now_ms: int) -> None:
        target_size = float(self.constitution["execution"].get("maker_quote_size", 1.0))
        half_spread_bps = float(self.constitution["execution"].get("maker_half_spread_bps", 40.0))
        inventory_skew_per_unit = float(self.constitution["execution"].get("inventory_skew_per_unit", 0.0025))
        risk_padding_bps = float(self.constitution["execution"].get("risk_padding_bps", 5.0))
        is_frozen_any = False
        freeze_reasons: List[str] = []

        for token_id, book in self.books.items():
            meta = self.market_meta.get(token_id, {})
            symbol = str(meta.get("reference_symbol") or "")
            constraint = self.constraints[token_id]
            snap = BookSnapshot.from_order_book(token_id, book, now_ms)
            self.book_cache.update(snap)
            self._record_book_snapshot(snap)
            book_health = snap.health_state(
                now_wall_ms=now_ms,
                stale_after_ms=self._book_stale_after_ms,
                down_after_ms=self._book_down_after_ms,
            )

            ws_lag_ms = None
            if snap.ts_event_ms is not None:
                ws_lag_ms = float(now_ms - snap.ts_event_ms)
                self.ws_lag_samples.append(ws_lag_ms)

            pstar = self.pstar_builder.build(symbol, now_ms) if symbol else PStar(symbol="", value=None, ts_event_ms=None, sources_used=set(), confidence=0.0, valid=False, diagnostics={"freeze_reason": "missing_symbol"})
            self._record_pstar(now_ms, symbol, pstar)

            q = self._fair_probability(token_id=token_id, symbol=symbol, snap=snap, pstar=pstar, now_ms=now_ms)
            self._last_q_by_token[token_id] = float(q)
            depth_buy = snap.depth_at_qty("buy", target_size)
            depth_sell = snap.depth_at_qty("sell", target_size)
            slip_buy = snap.expected_slippage_bps("buy", target_size)
            slip_sell = snap.expected_slippage_bps("sell", target_size)
            eff_buy = snap.effective_spread_bps("buy", target_size)
            eff_sell = snap.effective_spread_bps("sell", target_size)

            fsm = self.fsms[token_id]
            if fsm.on_rebalance_tick(now_ms) or fsm.status().state == ExecutionState.UNWINDING:
                await self._emergency_unwind(token_id, now_ms, constraint)

            fsm_state = fsm.status().state.value
            feature_max_ts = int(pstar.ts_event_ms or 0)
            decision_ts_event_ms = int(now_ms)
            signal_age_ms = int(max(0, now_ms - feature_max_ts)) if feature_max_ts else 10_000
            self.signal_age_samples.append(float(signal_age_ms))
            ack_p95 = _quantile(list(self.send_ack_samples), 0.95)
            causality_reasons = self._causality_violations(
                decision_ts_event_ms=decision_ts_event_ms,
                feature_max_ts_ms=feature_max_ts,
                book_asof_ts_ms=snap.ts_event_ms,
                pstar_asof_ts_ms=pstar.ts_event_ms,
            )

            buy_ctx = PolicyContext(
                market=str(meta.get("slug") or token_id),
                token_id=token_id,
                now_ms=now_ms,
                decision_ts_event_ms=decision_ts_event_ms,
                feature_max_ts_ms=feature_max_ts,
                book=snap,
                pstar=pstar,
                quote_side="buy",
                quote_qty=target_size,
                signal_age_ms=signal_age_ms,
                ack_p95_ms=ack_p95,
                ws_lag_ms=ws_lag_ms,
                one_leg_age_ms=_one_leg_age_ms(fsm.status().one_leg_since_ms, now_ms),
                fsm_state=fsm_state,
                expected_slippage_bps=slip_buy,
                depth_at_qty=depth_buy,
                book_health_state=book_health.value,
            )
            sell_ctx = PolicyContext(
                market=str(meta.get("slug") or token_id),
                token_id=token_id,
                now_ms=now_ms,
                decision_ts_event_ms=decision_ts_event_ms,
                feature_max_ts_ms=feature_max_ts,
                book=snap,
                pstar=pstar,
                quote_side="sell",
                quote_qty=target_size,
                signal_age_ms=signal_age_ms,
                ack_p95_ms=ack_p95,
                ws_lag_ms=ws_lag_ms,
                one_leg_age_ms=_one_leg_age_ms(fsm.status().one_leg_since_ms, now_ms),
                fsm_state=fsm_state,
                expected_slippage_bps=slip_sell,
                depth_at_qty=depth_sell,
                book_health_state=book_health.value,
            )
            buy_verdict = evaluate_policy(buy_ctx, self.policy_thresholds)
            sell_verdict = evaluate_policy(sell_ctx, self.policy_thresholds)

            reasons = sorted(set(buy_verdict.reason_codes + sell_verdict.reason_codes + causality_reasons))
            cap_diag = self._inventory_cap_diagnostics(token_id=token_id, q=q)
            if bool(cap_diag.get("hard_breach", False)):
                reasons.append("RISK_CAP_BREACH")
            elif bool(cap_diag.get("soft_breach", False)):
                reasons.append("RISK_CAP_SOFT")
            reasons = sorted(set(reasons))
            force_freeze = bool(
                book_health == BookHealthState.DOWN
                or bool(causality_reasons)
                or bool(cap_diag.get("hard_breach", False))
            )
            if force_freeze:
                buy_verdict = PolicyVerdict(
                    allow=False,
                    action="FREEZE",
                    reason_codes=reasons,
                    diagnostics={**buy_verdict.diagnostics, "cap_diag": cap_diag},
                )
                sell_verdict = PolicyVerdict(
                    allow=False,
                    action="FREEZE",
                    reason_codes=reasons,
                    diagnostics={**sell_verdict.diagnostics, "cap_diag": cap_diag},
                )
            prev_fsm_state = fsm.status().state.value
            if buy_verdict.action == "FREEZE" or sell_verdict.action == "FREEZE":
                fsm.freeze("policy_freeze")
                is_frozen_any = True
                freeze_reasons.extend(reasons)
                self.pending_freeze[token_id] = reasons
            else:
                if fsm.status().state == ExecutionState.FROZEN and not reasons:
                    fsm.unfreeze()
                    self.pending_freeze.pop(token_id, None)
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_fsm_state,
                new_state=fsm.status().state.value,
                ts_ms=now_ms,
                reason="policy_verdict",
            )

            spread_mult = 2.0 if book_health == BookHealthState.STALE else 1.0
            if bool(cap_diag.get("soft_breach", False)):
                spread_mult = max(spread_mult, 1.5)
            bid_px, ask_px = self._compute_quotes(
                q=q,
                mid=snap.mid(),
                constraint=constraint,
                half_spread_bps=half_spread_bps * spread_mult,
                inventory_skew=self.inventory_yes[token_id] * inventory_skew_per_unit,
                risk_padding_bps=risk_padding_bps,
            )

            self._decision_seq += 1
            decision_id = f"{self.run_epoch_ms}:{token_id}:{self._decision_seq}"
            self._record_decision(
                now_ms=now_ms,
                decision_id=decision_id,
                token_id=token_id,
                market=str(meta.get("slug") or token_id),
                action="FREEZE"
                if ("C_BOOK_DOWN" in reasons or buy_verdict.action == "FREEZE" or sell_verdict.action == "FREEZE")
                else ("QUOTE" if (buy_verdict.allow or sell_verdict.allow) else "SKIP"),
                reason_codes=reasons,
                p_hat=q,
                expected_edge=0.0,
                expected_cost=0.0,
                decision_ts_event_ms=decision_ts_event_ms,
                book_asof_ts_ms=snap.ts_event_ms,
                pstar_asof_ts_ms=pstar.ts_event_ms,
                max_feature_ts_ms=feature_max_ts,
                policy_json={
                    "buy": buy_verdict.diagnostics,
                    "sell": sell_verdict.diagnostics,
                    "book_health_state": book_health.value,
                    "cap_diag": cap_diag,
                    "codes": reasons,
                },
                fsm_state=fsm.status().state.value,
                pstar_diag=pstar.diagnostics,
            )
            self._record_microstructure(
                now_ms=now_ms,
                token_id=token_id,
                book_health=book_health.value,
                spread_bps=snap.spread_bps(),
                depth_at_qty_buy=depth_buy,
                depth_at_qty_sell=depth_sell,
                slippage_bps_buy=slip_buy,
                slippage_bps_sell=slip_sell,
                effective_spread_bps_buy=eff_buy,
                effective_spread_bps_sell=eff_sell,
            )

            if book_health != BookHealthState.DOWN:
                await self._apply_side(token_id, "buy", bid_px, target_size, constraint, buy_verdict, now_ms, decision_id)
                await self._apply_side(token_id, "sell", ask_px, target_size, constraint, sell_verdict, now_ms, decision_id)
            else:
                self.db.append_alert(now_ms, "critical", "BOOK_DOWN_FREEZE", f"{token_id}:health={book_health.value}")
            self._record_inventory(now_ms, token_id)

        self.db.upsert_system_state(
            as_of_ts=now_ms,
            is_frozen=is_frozen_any,
            reasons=",".join(sorted(set(freeze_reasons))) if freeze_reasons else "",
            mode=self.mode,
            payload={"freeze_by_market": self.pending_freeze},
        )

    async def _apply_side(
        self,
        token_id: str,
        side: str,
        price: float,
        qty: float,
        constraint: OrderConstraints,
        verdict,
        now_ms: int,
        decision_id: str,
    ) -> None:
        current = self.open_quotes[token_id].get(side)
        if not verdict.allow:
            if current is not None and self.mode in {"PAPER", "TRADE"} and self.broker is not None:
                for event in await self._broker_cancel(current.order_id):
                    self._handle_broker_event(token_id, side, event, decision_id)
                self.open_quotes[token_id].pop(side, None)
            if verdict.action == "FREEZE":
                self.db.append_alert(now_ms, "critical", "POLICY_FREEZE", f"{token_id}:{side}:{','.join(verdict.reason_codes)}")
            return

        if self.mode == "OBSERVE" or self.broker is None:
            return

        needs_replace = current is not None and (abs(current.price - price) >= max(constraint.min_tick, 1e-6) or abs(current.qty - qty) > 1e-9)
        needs_submit = current is None
        if not (needs_submit or needs_replace):
            return

        revision_key = (token_id, side)
        self._quote_revision[revision_key] += 1
        revision = self._quote_revision[revision_key]
        order_id = f"{self.run_epoch_ms}:{token_id}:{side}:{self._decision_seq}:{revision}"
        client_order_id = f"{order_id}:client"
        quote_group_id = f"{self.run_epoch_ms}:{token_id}:{self._decision_seq}"
        idempotency_key = f"{self.run_epoch_ms}:{token_id}:{side}:{self._decision_seq}:{revision}"
        intent = OrderIntent(
            order_id=order_id,
            client_order_id=client_order_id,
            asset_id=token_id,
            side=side,
            size=float(qty),
            price=float(price),
            mode="MAKE",
            t_decision_wall_ms=int(now_ms),
            as_of_ts_ms=int(now_ms),
            decision_id=decision_id,
            reason="quote_update",
            post_only=True,
            time_in_force="GTC",
            reduce_only=False,
            quote_group_id=quote_group_id,
            idempotency_key=idempotency_key,
        )

        events: List[BrokerEvent] = []
        if needs_submit:
            events = await self._broker_submit(intent)
        elif needs_replace:
            events = await self._broker_replace(current.order_id, intent)
        self._post_only_attempts_by_token[token_id] += 1
        if not events:
            return

        rejected_post_only = False
        has_submit = False
        has_ack = False
        has_reject = False
        for event in events:
            self._handle_broker_event(token_id, side, event, decision_id)
            if event.event_type == "order_submit":
                has_submit = True
            elif event.event_type == "order_ack":
                has_ack = True
            elif event.event_type in {"order_reject", "broker_error"}:
                has_reject = True
            if event.event_type == "order_reject":
                reason = str(event.payload.get("reason") or "")
                code = str(event.payload.get("error_code") or "")
                if "POST_ONLY" in reason.upper() or "POST_ONLY" in code.upper():
                    rejected_post_only = True
                    self._post_only_rejects_by_token[token_id] += 1

        if rejected_post_only:
            retried = await self._retry_post_only(token_id, side, intent, constraint, decision_id)
            if retried is not None:
                self.open_quotes[token_id][side] = OpenQuote(
                    order_id=intent.order_id,
                    client_order_id=intent.client_order_id,
                    side=side,
                    price=float(retried),
                    qty=float(qty),
                    post_only=True,
                    quote_group_id=quote_group_id,
                    idempotency_key=idempotency_key,
                    updated_ms=now_ms,
                )
                return
            self.open_quotes[token_id].pop(side, None)
            return

        if has_reject and not (has_submit or has_ack):
            self.open_quotes[token_id].pop(side, None)
            return

        if has_submit or has_ack:
            self.open_quotes[token_id][side] = OpenQuote(
                order_id=intent.order_id,
                client_order_id=intent.client_order_id,
                side=side,
                price=float(price),
                qty=float(qty),
                post_only=True,
                quote_group_id=quote_group_id,
                idempotency_key=idempotency_key,
                updated_ms=now_ms,
            )
        else:
            self.open_quotes[token_id].pop(side, None)

    async def _retry_post_only(
        self,
        token_id: str,
        side: str,
        intent: OrderIntent,
        constraint: OrderConstraints,
        decision_id: str,
    ) -> Optional[float]:
        tick = max(constraint.min_tick, 1e-6)
        new_price = intent.price - tick if side == "buy" else intent.price + tick
        new_price = _clamp(new_price, constraint.min_price, constraint.max_price)
        retry_intent = OrderIntent(
            order_id=intent.order_id,
            client_order_id=intent.client_order_id,
            asset_id=intent.asset_id,
            side=intent.side,
            size=intent.size,
            price=float(new_price),
            mode=intent.mode,
            t_decision_wall_ms=intent.t_decision_wall_ms,
            as_of_ts_ms=intent.as_of_ts_ms,
            decision_id=decision_id,
            reason="post_only_reprice",
            post_only=True,
            time_in_force=intent.time_in_force,
            reduce_only=intent.reduce_only,
            quote_group_id=intent.quote_group_id,
            idempotency_key=f"{decision_id}:{side}:retry",
        )
        self._post_only_attempts_by_token[token_id] += 1
        events = await self._broker_submit(retry_intent)
        for event in events:
            self._handle_broker_event(token_id, side, event, decision_id)
        any_reject = any(event.event_type in {"order_reject", "broker_error"} for event in events)
        return None if any_reject else float(new_price)

    async def _emergency_unwind(self, token_id: str, now_ms: int, constraint: OrderConstraints) -> None:
        fsm = self.fsms[token_id]
        status = fsm.status()
        qty = abs(status.net_qty)
        if qty <= 0:
            self._unwind_state.pop(token_id, None)
            fsm.reset_if_flat()
            return
        if self.mode == "OBSERVE" or self.broker is None:
            prev_state = fsm.status().state.value
            fsm.freeze("unwind_required_observe_mode")
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_state,
                new_state=fsm.status().state.value,
                ts_ms=now_ms,
                reason="unwind_required_observe_mode",
            )
            self.db.append_alert(now_ms, "critical", "UNWIND_REQUIRED", f"{token_id}:observe_mode")
            return

        unwind_state = self._unwind_state.setdefault(
            token_id,
            {
                "started_ms": int(now_ms),
                "maker_order_id": None,
                "last_taker_ms": None,
            },
        )
        side = "sell" if status.net_qty > 0 else "buy"
        maker_order_id = unwind_state.get("maker_order_id")
        if not maker_order_id:
            maker_price = self._maker_unwind_price(token_id, side, constraint)
            maker_order_id = f"{token_id}:unwind:maker:{uuid.uuid4().hex[:10]}"
            maker_intent = OrderIntent(
                order_id=maker_order_id,
                client_order_id=f"{maker_order_id}:client",
                asset_id=token_id,
                side=side,
                size=float(qty),
                price=float(maker_price),
                mode="MAKE",
                t_decision_wall_ms=now_ms,
                as_of_ts_ms=now_ms,
                decision_id=f"unwind-maker:{now_ms}",
                reason="hedge_timeout_maker_attempt",
                post_only=True,
                time_in_force="GTC",
                reduce_only=True,
                quote_group_id=f"{token_id}:unwind",
                idempotency_key=f"{token_id}:unwind:maker:{now_ms}",
            )
            events = await self._broker_submit(maker_intent)
            acked = False
            for event in events:
                self._handle_broker_event(token_id, side, event, maker_intent.decision_id or "")
                if event.event_type in {"order_submit", "order_ack"}:
                    acked = True
            if acked:
                unwind_state["maker_order_id"] = maker_order_id
            self.db.append_alert(now_ms, "warning", "UNWIND_MAKER_ATTEMPT", f"{token_id}:{side}:{qty}")
            return

        trading_cfg = self.constitution.get("trading", {}) if isinstance(self.constitution, dict) else {}
        emergency_taker_enabled = bool(trading_cfg.get("emergency_taker_enabled", True))
        taker_after_ms = int(trading_cfg.get("emergency_taker_after_ms", 1000))
        elapsed_ms = int(now_ms - int(unwind_state.get("started_ms", now_ms)))
        if not emergency_taker_enabled:
            if elapsed_ms >= taker_after_ms:
                prev_state = fsm.status().state.value
                fsm.freeze("unwind_timeout_taker_disabled")
                self._record_fsm_transition(
                    token_id=token_id,
                    prev_state=prev_state,
                    new_state=fsm.status().state.value,
                    ts_ms=now_ms,
                    reason="unwind_timeout_taker_disabled",
                )
                self.db.append_alert(now_ms, "critical", "UNWIND_STUCK", f"{token_id}:taker_disabled")
            return
        if elapsed_ms < taker_after_ms:
            return
        last_taker_ms = unwind_state.get("last_taker_ms")
        if isinstance(last_taker_ms, int) and now_ms - last_taker_ms < taker_after_ms:
            return

        maker_order_id = unwind_state.get("maker_order_id")
        if isinstance(maker_order_id, str) and maker_order_id:
            for event in await self._broker_cancel(maker_order_id):
                self._handle_broker_event(token_id, side, event, f"unwind-cancel:{now_ms}")
            unwind_state["maker_order_id"] = None

        taker_price = constraint.min_price if side == "sell" else constraint.max_price
        taker_order_id = f"{token_id}:unwind:taker:{uuid.uuid4().hex[:10]}"
        taker_intent = OrderIntent(
            order_id=taker_order_id,
            client_order_id=f"{taker_order_id}:client",
            asset_id=token_id,
            side=side,
            size=float(qty),
            price=float(taker_price),
            mode="TAKE",
            t_decision_wall_ms=now_ms,
            as_of_ts_ms=now_ms,
            decision_id=f"unwind-taker:{now_ms}",
            reason="hedge_timeout_taker",
            post_only=False,
            time_in_force="IOC",
            reduce_only=True,
            quote_group_id=f"{token_id}:unwind",
            idempotency_key=f"{token_id}:unwind:taker:{now_ms}",
        )
        for event in await self._broker_submit(taker_intent):
            self._handle_broker_event(token_id, side, event, taker_intent.decision_id or "")
        unwind_state["last_taker_ms"] = int(now_ms)
        self.db.append_alert(now_ms, "critical", "EMERGENCY_UNWIND", f"{token_id}:{side}:{qty}")

    def _maker_unwind_price(self, token_id: str, side: str, constraint: OrderConstraints) -> float:
        tick = max(constraint.min_tick, 1e-6)
        snap = self.book_cache.get(token_id)
        if side == "sell":
            if snap is not None and snap.best_ask() is not None:
                return _clamp(_round_up(float(snap.best_ask()), tick), constraint.min_price, constraint.max_price)
            if snap is not None and snap.mid() is not None:
                return _clamp(_round_up(float(snap.mid()) + tick, tick), constraint.min_price, constraint.max_price)
            return _clamp(constraint.max_price - tick, constraint.min_price, constraint.max_price)
        if snap is not None and snap.best_bid() is not None:
            return _clamp(_round_down(float(snap.best_bid()), tick), constraint.min_price, constraint.max_price)
        if snap is not None and snap.mid() is not None:
            return _clamp(_round_down(float(snap.mid()) - tick, tick), constraint.min_price, constraint.max_price)
        return _clamp(constraint.min_price + tick, constraint.min_price, constraint.max_price)

    async def _broker_submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        return await asyncio.to_thread(self.broker.submit, intent)

    async def _broker_cancel(self, order_id: str) -> List[BrokerEvent]:
        return await asyncio.to_thread(self.broker.cancel, order_id)

    async def _broker_replace(self, order_id: str, new_intent: OrderIntent) -> List[BrokerEvent]:
        return await asyncio.to_thread(self.broker.replace, order_id, new_intent)

    def _handle_broker_event(self, token_id: str, side: str, event: BrokerEvent, decision_id: str) -> None:
        ts_ms = int(event.payload.get("t_event_wall_ms") or _now_ms())
        event_id = uuid.uuid4().hex
        status = str(event.payload.get("status") or event.event_type)
        self.db.insert(
            "orders",
            {
                "ts_ms": ts_ms,
                "event_id": event_id,
                "order_id": event.order_id,
                "client_order_id": str(event.payload.get("client_order_id") or ""),
                "token_id": token_id,
                "side": side,
                "price": float(event.payload.get("fill_price") or event.payload.get("price") or 0.0),
                "qty": float(event.payload.get("fill_size") or event.payload.get("size") or 0.0),
                "post_only": 1 if bool(event.payload.get("post_only", True)) else 0,
                "tif": str(event.payload.get("time_in_force") or "GTC"),
                "status": status,
                "reason": str(event.payload.get("reason") or ""),
                "quote_group_id": str(event.payload.get("quote_group_id") or ""),
                "idempotency_key": str(event.payload.get("idempotency_key") or ""),
                "fsm_state": self.fsms[token_id].status().state.value,
                "payload_json": json.dumps(event.payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        self._write_trade_tape_event(ts_ms, event_id, event)

        if event.event_type == "order_submit":
            send_ts = int(event.payload.get("t_send_wall_ms") or ts_ms)
            self.send_ts_by_order[event.order_id] = send_ts
        elif event.event_type == "order_ack":
            ack_ts = int(event.payload.get("t_ack_wall_ms") or ts_ms)
            self.ack_ts_by_order[event.order_id] = ack_ts
            send_ts = self.send_ts_by_order.get(event.order_id)
            if send_ts is not None:
                self.send_ack_samples.append(float(max(0, ack_ts - send_ts)))
                self.db.insert(
                    "exec_latency",
                    {
                        "ts_ms": ts_ms,
                        "event_id": uuid.uuid4().hex,
                        "decision_id": decision_id,
                        "token_id": token_id,
                        "signal_age_ms": None,
                        "send_ack_ms": float(max(0, ack_ts - send_ts)),
                        "ack_fill_ms": None,
                        "ws_lag_ms": None,
                        "payload_json": "{}",
                    },
                )
        elif event.event_type == "order_fill":
            fill_ts = int(event.payload.get("t_fill_wall_ms") or ts_ms)
            fill_qty = float(event.payload.get("fill_size") or 0.0)
            fill_price = float(event.payload.get("fill_price") or 0.0)
            self.db.insert(
                "fills",
                {
                    "ts_ms": fill_ts,
                    "event_id": uuid.uuid4().hex,
                    "order_id": event.order_id,
                    "token_id": token_id,
                    "side": side,
                    "fill_price": fill_price,
                    "fill_qty": fill_qty,
                    "fee": float(event.payload.get("fees_bps") or 0.0),
                    "liquidity": "maker" if str(event.payload.get("mode") or "").upper() == "MAKE" else "taker",
                    "payload_json": json.dumps(event.payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                },
            )
            ack_ts = self.ack_ts_by_order.get(event.order_id)
            if ack_ts is not None:
                ack_fill_ms = float(max(0, fill_ts - ack_ts))
                self.ack_fill_samples.append(ack_fill_ms)
                self.db.insert(
                    "exec_latency",
                    {
                        "ts_ms": fill_ts,
                        "event_id": uuid.uuid4().hex,
                        "decision_id": decision_id,
                        "token_id": token_id,
                        "signal_age_ms": None,
                        "send_ack_ms": None,
                        "ack_fill_ms": ack_fill_ms,
                        "ws_lag_ms": None,
                        "payload_json": "{}",
                    },
                )
            if side == "buy":
                self.inventory_yes[token_id] += fill_qty
            else:
                self.inventory_yes[token_id] -= fill_qty
            prev_state = self.fsms[token_id].status().state.value
            self.fsms[token_id].on_fill(side=side, qty=fill_qty, ts_ms=fill_ts)
            self.fsms[token_id].reset_if_flat()
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_state,
                new_state=self.fsms[token_id].status().state.value,
                ts_ms=fill_ts,
                reason="order_fill",
            )
        elif event.event_type in {"order_reject", "broker_error"}:
            self.db.append_alert(ts_ms, "warning", "ORDER_REJECT", f"{token_id}:{side}:{event.payload}")

    def _write_trade_tape_event(self, ts_ms: int, event_id: str, event: BrokerEvent) -> None:
        event_type = event.event_type
        if event_type not in {"order_submit", "order_ack", "order_fill", "order_cancel", "order_reject", "broker_error"}:
            return
        if event.order_id not in self._trade_tape_parent_event_ids:
            intent_event_id = self.trade_tape.next_event_id()
            self.trade_tape.write(
                {
                    "schema_version": "trade_v1",
                    "run_id": self.trade_tape.run_id,
                    "event_id": intent_event_id,
                    "parent_event_id": None,
                    "event_type": "order_intent",
                    "order_id": event.order_id,
                    "client_order_id": str(event.payload.get("client_order_id") or f"{event.order_id}:client"),
                    "asset_id": str(event.payload.get("asset_id") or ""),
                    "side": str(event.payload.get("side") or ""),
                    "size": float(event.payload.get("size") or 0.0),
                    "price": float(event.payload.get("price") or 0.0),
                    "mode": str(event.payload.get("mode") or "MAKE"),
                    "t_decision_wall_ms": ts_ms,
                    "t_event_wall_ms": ts_ms,
                    "t_event_mono_ns": int(self.time_mapper.mono_ns_from_wall_ms(ts_ms)),
                    "as_of_ts_ms": ts_ms,
                }
            )
            self._trade_tape_parent_event_ids[event.order_id] = int(intent_event_id)

        parent_event_id = self._trade_tape_parent_event_ids.get(event.order_id)
        if parent_event_id is None:
            return
        payload: Dict[str, Any] = {
            "schema_version": "trade_v1",
            "run_id": self.trade_tape.run_id,
            "event_id": self.trade_tape.next_event_id(),
            "parent_event_id": parent_event_id,
            "event_type": event_type,
            "order_id": event.order_id,
            "t_event_wall_ms": ts_ms,
            "t_event_mono_ns": int(self.time_mapper.mono_ns_from_wall_ms(ts_ms)),
            "as_of_ts_ms": ts_ms,
        }
        if event_type == "order_submit":
            payload.update(
                {
                    "broker": str(event.payload.get("broker") or "polymarket"),
                    "status": str(event.payload.get("status") or "submitted"),
                    "t_send_wall_ms": int(event.payload.get("t_send_wall_ms") or ts_ms),
                }
            )
        elif event_type == "order_ack":
            payload.update(
                {
                    "broker": str(event.payload.get("broker") or "polymarket"),
                    "status": str(event.payload.get("status") or "accepted"),
                    "t_ack_wall_ms": int(event.payload.get("t_ack_wall_ms") or ts_ms),
                }
            )
        elif event_type == "order_fill":
            payload.update(
                {
                    "fill_price": float(event.payload.get("fill_price") or 0.0),
                    "fill_size": float(event.payload.get("fill_size") or 0.0),
                    "fees_bps": float(event.payload.get("fees_bps") or 0.0),
                    "t_fill_wall_ms": int(event.payload.get("t_fill_wall_ms") or ts_ms),
                }
            )
        elif event_type == "order_cancel":
            payload.update({"reason": str(event.payload.get("reason") or "CANCELED")})
        elif event_type == "order_reject":
            payload.update({"reason": str(event.payload.get("reason") or "REJECTED")})
        elif event_type == "broker_error":
            payload.update({"error_code": str(event.payload.get("error_code") or "BROKER_ERROR")})
        try:
            self.trade_tape.write(payload)
        except Exception:
            # Keep runtime resilient even if tape schema rejects auxiliary rows.
            pass

    def _record_book_snapshot(self, snap: BookSnapshot) -> None:
        rows = [
            {
                "ts_ms": int(snap.ts_event_ms or snap.ts_recv_wall_ms),
                "token_id": token,
                "side": side,
                "price": price,
                "size": size,
                "source": "ws",
            }
            for token, side, price, size, _ in snap.to_l2_rows()
        ]
        if rows:
            self.db.insert_many("market_data_book", rows)

    def _record_pstar(self, now_ms: int, symbol: str, pstar: PStar) -> None:
        sources = ",".join(sorted(pstar.sources_used))
        self.db.insert(
            "pstar",
            {
                "ts_ms": now_ms,
                "symbol": symbol or "",
                "value": pstar.value,
                "ts_event_ms": pstar.ts_event_ms,
                "confidence": float(pstar.confidence),
                "valid": 1 if pstar.valid else 0,
                "sources_used": sources,
                "diagnostics_json": json.dumps(pstar.diagnostics, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        diag = pstar.diagnostics or {}
        age_ms = diag.get("age_ms") if isinstance(diag.get("age_ms"), dict) else {}
        self.db.insert(
            "pstar_stats",
            {
                "ts_ms": now_ms,
                "symbol": symbol or "",
                "p_spot": _maybe_float(diag.get("spot_px")),
                "age_spot_ms": _maybe_int(age_ms.get("spot") if isinstance(age_ms, dict) else None),
                "p_perp": _maybe_float(diag.get("perp_px")),
                "age_perp_ms": _maybe_int(age_ms.get("perp") if isinstance(age_ms, dict) else None),
                "disagreement_bps": _maybe_float(diag.get("disagreement_bps")),
                "confidence": float(pstar.confidence),
                "valid": 1 if pstar.valid else 0,
            },
        )

    def _record_decision(
        self,
        now_ms: int,
        decision_id: str,
        token_id: str,
        market: str,
        action: str,
        reason_codes: List[str],
        p_hat: Optional[float],
        expected_edge: float,
        expected_cost: float,
        decision_ts_event_ms: int,
        book_asof_ts_ms: Optional[int],
        pstar_asof_ts_ms: Optional[int],
        max_feature_ts_ms: int,
        policy_json: Dict[str, Any],
        fsm_state: str,
        pstar_diag: Dict[str, Any],
    ) -> None:
        self.db.insert(
            "decisions",
            {
                "ts_ms": now_ms,
                "decision_id": decision_id,
                "market": market,
                "token_id": token_id,
                "action": action,
                "reason_codes": ",".join(reason_codes),
                "p_hat": p_hat,
                "expected_edge": expected_edge,
                "expected_cost": expected_cost,
                "decision_ts_event_ms": int(decision_ts_event_ms),
                "book_asof_ts_ms": _maybe_int(book_asof_ts_ms),
                "pstar_asof_ts_ms": _maybe_int(pstar_asof_ts_ms),
                "max_feature_ts_ms": int(max_feature_ts_ms),
                "policy_json": json.dumps(policy_json, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        # Compatibility decision tape row for existing audit/replay tooling.
        record = DecisionRecord(
            schema_version="decision_v4_system",
            engine_version="run_system_v1",
            run_id=self.decision_tape.run_id,
            t_decision_wall_iso=_utc_iso_from_ms(now_ms),
            t_decision_wall_ms=now_ms,
            t_decision_mono_ns=int(self.time_mapper.mono_ns_from_wall_ms(now_ms)),
            asset_id=token_id,
            market_slug=market,
            condition_id=None,
            token_id=token_id,
            outcome=None,
            outcome_by_token=None,
            book={},
            p_market_mid=None,
            p_market_exec_buy=None,
            p_market_exec_sell=None,
            p_market=None,
            p_fair=p_hat,
            edge_net_buy=None,
            edge_net_sell=None,
            p_star={
                "value": pstar_diag.get("value"),
                "confidence": pstar_diag.get("confidence"),
                "diagnostics": pstar_diag,
            },
            labels=None,
            features_raw=None,
            features_ortho=None,
            whitening=None,
            gates={"allow": action == "QUOTE", "reasons": reason_codes},
            exec_cost={},
            notes={
                "policy": policy_json,
                "action": action,
            },
            as_of_ts_ms=now_ms,
            pstar_diag=pstar_diag,
            policy_codes=reason_codes,
            latency={},
            fsm_state=fsm_state,
        )
        self.decision_tape.write(record)

    def _record_inventory(self, now_ms: int, token_id: str) -> None:
        self.db.insert(
            "inventory",
            {
                "ts_ms": now_ms,
                "token_id": token_id,
                "yes_qty": float(self.inventory_yes[token_id]),
                "no_qty": float(self.inventory_no[token_id]),
                "usdc": None,
                "source": "offchain",
                "payload_json": "{}",
            },
        )

    def _record_fsm_transition(
        self,
        token_id: str,
        prev_state: str,
        new_state: str,
        ts_ms: int,
        reason: str,
    ) -> None:
        self._last_fsm_state_by_token[token_id] = str(new_state)
        if prev_state == new_state:
            return
        self.db.insert(
            "recovery_events",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid.uuid4().hex,
                "recovery_action": "FSM_TRANSITION",
                "token_id": token_id,
                "side": None,
                "order_id": None,
                "adopted_order_count": None,
                "payload_json": json.dumps(
                    {
                        "prev_state": prev_state,
                        "new_state": new_state,
                        "reason": reason,
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        )

    def _record_microstructure(
        self,
        now_ms: int,
        token_id: str,
        book_health: str,
        spread_bps: Optional[float],
        depth_at_qty_buy: float,
        depth_at_qty_sell: float,
        slippage_bps_buy: Optional[float],
        slippage_bps_sell: Optional[float],
        effective_spread_bps_buy: Optional[float],
        effective_spread_bps_sell: Optional[float],
    ) -> None:
        attempts = max(0, int(self._post_only_attempts_by_token.get(token_id, 0)))
        rejects = max(0, int(self._post_only_rejects_by_token.get(token_id, 0)))
        reject_rate = float(rejects / attempts) if attempts > 0 else 0.0
        self.db.insert(
            "microstructure_stats",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "token_id": token_id,
                "book_health": str(book_health),
                "spread_bps": _maybe_float(spread_bps),
                "depth_at_qty_buy": float(depth_at_qty_buy),
                "depth_at_qty_sell": float(depth_at_qty_sell),
                "slippage_bps_buy": _maybe_float(slippage_bps_buy),
                "slippage_bps_sell": _maybe_float(slippage_bps_sell),
                "effective_spread_bps_buy": _maybe_float(effective_spread_bps_buy),
                "effective_spread_bps_sell": _maybe_float(effective_spread_bps_sell),
                "post_only_reject_rate": reject_rate,
            },
        )

    def _causality_violations(
        self,
        decision_ts_event_ms: int,
        feature_max_ts_ms: int,
        book_asof_ts_ms: Optional[int],
        pstar_asof_ts_ms: Optional[int],
    ) -> List[str]:
        reasons: List[str] = []
        if int(feature_max_ts_ms) >= int(decision_ts_event_ms):
            reasons.append("B_FEATURE_TIME_LEAK")
        if book_asof_ts_ms is not None and int(book_asof_ts_ms) >= int(decision_ts_event_ms):
            reasons.append("B_BOOK_TIME_LEAK")
        if pstar_asof_ts_ms is not None and int(pstar_asof_ts_ms) >= int(decision_ts_event_ms):
            reasons.append("B_PSTAR_TIME_LEAK")
        return reasons

    def _build_caps(self, trading_cfg: Dict[str, Any], execution_cfg: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        bankroll_usd = float(trading_cfg.get("bankroll_usd", 10_000.0))
        per_market_abs = float(trading_cfg.get("cap_gross_usd", 500.0))
        per_market_frac = float(trading_cfg.get("cap_gross_bankroll_frac", 0.005))
        per_market_gross = min(per_market_abs, bankroll_usd * per_market_frac)
        per_market_soft = float(trading_cfg.get("cap_soft_ratio", 0.80)) * per_market_gross
        per_market_net = float(trading_cfg.get("cap_net_ratio", 0.20)) * per_market_gross

        portfolio_abs = float(trading_cfg.get("cap_total_gross_usd", 2000.0))
        portfolio_frac = float(trading_cfg.get("cap_total_gross_bankroll_frac", 0.02))
        portfolio_gross = min(portfolio_abs, bankroll_usd * portfolio_frac)
        portfolio_net = float(trading_cfg.get("cap_total_net_ratio", 0.25)) * portfolio_gross

        return {
            "per_market": {
                "gross": float(max(1.0, per_market_gross)),
                "soft": float(max(1.0, per_market_soft)),
                "net": float(max(0.1, per_market_net)),
            },
            "portfolio": {
                "gross": float(max(1.0, portfolio_gross)),
                "net": float(max(0.1, portfolio_net)),
            },
        }

    def _inventory_cap_diagnostics(self, token_id: str, q: float) -> Dict[str, Any]:
        yes_qty = float(self.inventory_yes.get(token_id, 0.0))
        no_qty = float(self.inventory_no.get(token_id, 0.0))
        gross_qty = abs(yes_qty) + abs(no_qty)
        net_qty = yes_qty - no_qty
        token_notional = gross_qty * max(0.01, min(0.99, float(q)))
        token_net_notional = abs(net_qty) * max(0.01, min(0.99, float(q)))

        portfolio_gross = 0.0
        portfolio_net = 0.0
        for tkn in self.books.keys():
            token_q = max(0.01, min(0.99, float(self._last_q_by_token.get(tkn, 0.5))))
            y = float(self.inventory_yes.get(tkn, 0.0))
            n = float(self.inventory_no.get(tkn, 0.0))
            portfolio_gross += (abs(y) + abs(n)) * token_q
            portfolio_net += abs(y - n) * token_q

        per_market_caps = self._cap_state["per_market"]
        portfolio_caps = self._cap_state["portfolio"]
        hard_breach = bool(
            token_notional >= per_market_caps["gross"]
            or token_net_notional >= per_market_caps["net"]
            or portfolio_gross >= portfolio_caps["gross"]
            or portfolio_net >= portfolio_caps["net"]
        )
        soft_breach = bool(token_notional >= per_market_caps["soft"] and not hard_breach)
        return {
            "within_limits": not hard_breach,
            "hard_breach": hard_breach,
            "soft_breach": soft_breach,
            "token_notional": token_notional,
            "token_net_notional": token_net_notional,
            "portfolio_gross": portfolio_gross,
            "portfolio_net": portfolio_net,
            "caps": self._cap_state,
        }

    def adopt_open_orders(self, snapshot: BrokerSnapshot, now_ms: int) -> int:
        open_orders = snapshot.open_orders or {}
        if not isinstance(open_orders, dict):
            return 0
        adopted = 0
        latest_by_token_side: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for order_id, payload in open_orders.items():
            if not isinstance(payload, dict):
                continue
            token_id = str(payload.get("token_id") or "")
            side = str(payload.get("side") or "").lower()
            if not token_id or side not in {"buy", "sell"}:
                continue
            latest_by_token_side[(token_id, side)] = {
                "order_id": str(payload.get("order_id") or order_id),
                "client_order_id": str(payload.get("client_order_id") or f"{order_id}:client"),
                "side": side,
                "price": float(payload.get("price") or 0.0),
                "qty": float(payload.get("size") or 0.0),
                "quote_group_id": str(payload.get("quote_group_id") or f"recovered:{token_id}:{side}"),
                "idempotency_key": str(payload.get("idempotency_key") or f"recovered:{token_id}:{side}"),
            }
        for (token_id, side), row in latest_by_token_side.items():
            self.open_quotes[token_id][side] = OpenQuote(
                order_id=row["order_id"],
                client_order_id=row["client_order_id"],
                side=side,
                price=float(row["price"]),
                qty=float(row["qty"]),
                post_only=True,
                quote_group_id=row["quote_group_id"],
                idempotency_key=row["idempotency_key"],
                updated_ms=int(now_ms),
            )
            adopted += 1
            self.db.insert(
                "recovery_events",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "recovery_action": "ADOPT_OPEN_ORDER",
                    "token_id": token_id,
                    "side": side,
                    "order_id": row["order_id"],
                    "adopted_order_count": adopted,
                    "payload_json": json.dumps(snapshot.meta or {}, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                },
            )
        return adopted

    def _fair_probability(self, token_id: str, symbol: str, snap: BookSnapshot, pstar: PStar, now_ms: int) -> float:
        mid = snap.mid()
        if mid is None:
            return 0.5
        q = float(mid)
        if pstar.valid and pstar.value is not None and symbol:
            prev = self.last_pstar_value.get(symbol)
            prev_ts = self.last_pstar_ts.get(symbol)
            self.last_pstar_value[symbol] = float(pstar.value)
            self.last_pstar_ts[symbol] = int(pstar.ts_event_ms or now_ms)
            if prev is not None and prev > 0 and prev_ts is not None:
                dt_s = max(1e-3, (now_ms - prev_ts) / 1000.0)
                ret = (float(pstar.value) - prev) / prev
                drift = _clamp(ret / dt_s * 2.0, -0.03, 0.03)
                q = _clamp(mid + drift, 0.01, 0.99)
        return _clamp(q, 0.01, 0.99)

    def _compute_quotes(
        self,
        q: float,
        mid: Optional[float],
        constraint: OrderConstraints,
        half_spread_bps: float,
        inventory_skew: float,
        risk_padding_bps: float,
    ) -> Tuple[float, float]:
        base = float(mid if mid is not None else q)
        half = max(constraint.min_tick, base * (half_spread_bps / 10000.0))
        risk_pad = base * (risk_padding_bps / 10000.0)
        bid = q - half - inventory_skew - risk_pad
        ask = q + half - inventory_skew + risk_pad
        bid = _clamp(bid, constraint.min_price, constraint.max_price - constraint.min_tick)
        ask = _clamp(ask, constraint.min_price + constraint.min_tick, constraint.max_price)
        if ask <= bid:
            ask = min(constraint.max_price, bid + constraint.min_tick)
        bid = _round_down(bid, constraint.min_tick)
        ask = _round_up(ask, constraint.min_tick)
        return bid, ask

    async def run_stats_cycle(self, now_ms: int) -> None:
        p50_send_ack = _quantile(list(self.send_ack_samples), 0.50)
        p95_send_ack = _quantile(list(self.send_ack_samples), 0.95)
        p50_ack_fill = _quantile(list(self.ack_fill_samples), 0.50)
        p95_ack_fill = _quantile(list(self.ack_fill_samples), 0.95)
        ws_lag = _quantile(list(self.ws_lag_samples), 0.95)
        p50_signal_age = _quantile(list(self.signal_age_samples), 0.50)
        p95_signal_age = _quantile(list(self.signal_age_samples), 0.95)
        self.db.insert(
            "latency_stats",
            {
                "ts_ms": now_ms,
                "p50_send_ack_ms": p50_send_ack,
                "p95_send_ack_ms": p95_send_ack,
                "p50_ack_fill_ms": p50_ack_fill,
                "p95_ack_fill_ms": p95_ack_fill,
                "ws_lag_ms": ws_lag,
                "p50_signal_age_ms": p50_signal_age,
                "p95_signal_age_ms": p95_signal_age,
            },
        )
        await self._record_reconciliation(now_ms)

    async def _record_reconciliation(self, now_ms: int) -> None:
        broker_open_orders = 0
        broker_inventory = None
        onchain_inventory = None
        mismatch_count = 0
        unresolved_mismatch_count = 0
        payload: Dict[str, Any] = {}
        if self.mode == "TRADE" and self.broker is not None:
            snapshot = await asyncio.to_thread(self.broker.snapshot)
            broker_orders = snapshot.open_orders if isinstance(snapshot.open_orders, dict) else {}
            broker_open_orders = len(broker_orders)
            broker_ids = set(broker_orders.keys())
            local_ids = {
                quote.order_id
                for token_quotes in self.open_quotes.values()
                for quote in token_quotes.values()
            }
            only_local = local_ids - broker_ids
            only_broker = broker_ids - local_ids
            mismatch_count = len(only_local) + len(only_broker)
            unresolved_mismatch_count = mismatch_count
            payload = {
                "only_local": sorted(only_local),
                "only_broker": sorted(only_broker),
                "meta": snapshot.meta,
            }
            broker_inventory = _maybe_float((snapshot.meta or {}).get("broker_inventory"))
            onchain_inventory = _maybe_float((snapshot.meta or {}).get("onchain_inventory"))
            if unresolved_mismatch_count > 0:
                self.db.append_alert(
                    now_ms,
                    "warning",
                    "RECON_MISMATCH",
                    f"mismatch_count={mismatch_count}",
                    payload=payload,
                )

        self.db.insert(
            "reconciliation_stats",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "broker_open_orders": int(broker_open_orders),
                "broker_inventory": _maybe_float(broker_inventory),
                "onchain_inventory": _maybe_float(onchain_inventory),
                "mismatch_count": int(mismatch_count),
                "unresolved_mismatch_count": int(unresolved_mismatch_count),
                "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )


def _one_leg_age_ms(start_ms: Optional[int], now_ms: int) -> Optional[int]:
    if start_ms is None:
        return None
    return int(max(0, now_ms - start_ms))


def _maybe_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _load_constitution(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "trading": {},
            "policy": {},
            "execution": {},
        }
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("constitution_invalid_format")
    return data


async def _run() -> None:
    args = _parse_args()
    settings = load_settings()
    constitution_path = Path(args.constitution or "config/constitution.yaml")
    constitution = _load_constitution(constitution_path)
    trading_cfg = constitution.get("trading", {}) if isinstance(constitution, dict) else {}
    policy_cfg = constitution.get("policy", {}) if isinstance(constitution, dict) else {}
    mode = (args.mode or settings.trading_mode or trading_cfg.get("mode_default") or "OBSERVE").upper()
    if mode not in {"OBSERVE", "PAPER", "TRADE"}:
        raise ValueError(f"unsupported_mode:{mode}")

    markets_path = args.markets or settings.track_markets_yaml
    markets = load_markets(markets_path)
    validate_markets_config(markets, auto_discover=settings.auto_discover)

    log_dir = Path(args.log_dir or settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db_path or settings.runtime_db_path)
    db = SQLiteStore(db_path)
    run_id = uuid.uuid4().hex
    event_tape = EventTape(log_dir=str(log_dir), run_id=run_id)
    decision_tape = DecisionTape(log_dir=str(log_dir), run_id=run_id)
    trade_tape = TradeTape(log_dir=str(log_dir), run_id=run_id)
    metrics = Metrics()

    resolved_markets, asset_meta = await resolve_markets(
        markets=markets,
        auto_discover=args.auto_discover or settings.auto_discover,
        cache_path=log_dir / "cache_gamma_markets.json",
        gamma_base_url=GAMMA_BASE_URL,
        discovery_summary={},
    )
    asset_ids = sorted({token for market in resolved_markets for token in market.token_ids if token})
    if not asset_ids:
        raise ValueError("no_asset_ids_resolved")

    books = {asset_id: OrderBook(asset_id=asset_id, bids={}, asks={}) for asset_id in asset_ids}
    constraints = {
        asset_id: OrderConstraints(
            min_tick=market.min_tick,
            min_size=market.min_size,
            min_price=market.min_price,
            max_price=market.max_price,
            max_spread_bps=float(policy_cfg.get("max_spread_bps", settings.max_spread_bps)),
            max_slippage_bps=float(policy_cfg.get("max_slippage_bps", settings.max_slippage_bps)),
            max_book_staleness_ms=int(policy_cfg.get("max_book_age_ms", settings.max_book_staleness_ms)),
        )
        for market in resolved_markets
        for asset_id in market.token_ids
        if asset_id
    }
    time_mapper = TimeMapper.from_wall_and_mono(wall_ms=_now_ms(), mono_ns=time.monotonic_ns())

    policy_thresholds = PolicyThresholds(
        max_book_age_ms=int(policy_cfg.get("max_book_age_ms", settings.max_book_staleness_ms)),
        book_stale_after_ms=int(policy_cfg.get("book_stale_after_ms", 30_000)),
        book_down_after_ms=int(policy_cfg.get("book_down_after_ms", 120_000)),
        max_spread_bps=float(policy_cfg.get("max_spread_bps", settings.max_spread_bps)),
        max_slippage_bps=float(policy_cfg.get("max_slippage_bps", settings.max_slippage_bps)),
        min_depth_at_qty=float(policy_cfg.get("min_depth_at_qty", 1.0)),
        max_signal_age_ms=int(policy_cfg.get("signal_age_max_ms", settings.signal_age_max_ms)),
        max_ack_p95_ms=float(policy_cfg.get("ack_p95_max_ms", settings.ack_p95_max_ms)),
        max_ws_lag_ms=float(policy_cfg.get("ws_lag_max_ms", settings.ws_lag_max_ms)),
        hedge_timeout_ms=int(trading_cfg.get("rebalance_timeout_ms", settings.rebalance_timeout_ms)),
    )

    pstar_builder = PStarBuilder(
        max_age_ms=int(policy_cfg.get("pstar_max_age_ms", settings.pstar_max_age_ms)),
        freeze_disagree_bps=float(policy_cfg.get("pstar_freeze_disagree_bps", settings.pstar_freeze_disagree_bps)),
        degrade_disagree_bps=float(policy_cfg.get("pstar_degrade_disagree_bps", 10.0)),
        allow_degraded_single_source=bool(policy_cfg.get("allow_degraded_single_source", True)),
    )

    broker = None
    if mode == "PAPER":
        broker = SimBroker(
            books=books,
            constraints=constraints,
            time_mapper=time_mapper,
            fee_status_by_asset={asset_id: "unknown" for asset_id in asset_ids},
            config=SimBrokerConfig(latency_ms=0, fee_mode="MAKE"),
        )
    elif mode == "TRADE":
        expected_client_version = str(
            trading_cfg.get("clob_client_version")
            or os.getenv("CLOB_CLIENT_VERSION")
            or EXPECTED_CLOB_CLIENT_VERSION
        )
        if not args.dry_run:
            try:
                PolymarketBroker.assert_contract(
                    expected_version=expected_client_version,
                    strict=True,
                )
            except BrokerContractError as exc:
                raise RuntimeError(f"broker_contract_guard_failed:{exc}") from exc
        broker = PolymarketBroker(
            api_key=settings.polymarket_api_key,
            secret=settings.polymarket_secret,
            passphrase=settings.polymarket_passphrase,
            private_key=settings.polymarket_private_key,
            config=PolymarketBrokerConfig(
                dry_run=args.dry_run,
                expected_client_version=expected_client_version,
                strict_contract=True,
            ),
        )

    run_epoch_ms = _now_ms()
    runtime = RuntimeEngine(
        mode=mode,
        db=db,
        decision_tape=decision_tape,
        trade_tape=trade_tape,
        books=books,
        constraints=constraints,
        market_meta=asset_meta,
        pstar_builder=pstar_builder,
        policy_thresholds=policy_thresholds,
        constitution=constitution,
        time_mapper=time_mapper,
        broker=broker,
        run_epoch_ms=run_epoch_ms,
    )

    if mode == "TRADE" and broker is not None and not args.dry_run:
        snapshot = await asyncio.to_thread(broker.snapshot)
        adopted = runtime.adopt_open_orders(snapshot=snapshot, now_ms=_now_ms())
        db.append_log(
            _now_ms(),
            "INFO",
            "startup_recovery_complete",
            {
                "adopted_order_count": int(adopted),
                "open_order_count": int(len(snapshot.open_orders or {})),
            },
        )

    ws_config = WSConfig(
        reconnect_base_ms=settings.ws_reconnect_base_ms,
        reconnect_max_ms=settings.ws_reconnect_max_ms,
    )
    market_client = MarketWSClient(
        asset_ids=asset_ids,
        books=books,
        tape=event_tape,
        metrics=metrics,
        config=ws_config,
        decision_engine=None,
    )

    quote_interval_ms = int(args.quote_interval_ms or trading_cfg.get("quote_interval_ms", settings.quote_interval_ms))
    stats_interval_ms = int(args.stats_interval_ms or trading_cfg.get("stats_interval_ms", settings.stats_interval_ms))
    reference_sources = args.reference_source or settings.reference_source or "poll_coinbase,poll_binance_perp"

    stop_event = asyncio.Event()
    tasks: List[asyncio.Task] = []
    tasks.append(asyncio.create_task(market_client.run()))

    ref_feeds: List[ReferenceFeed] = []
    ref_ws_clients: List[ReferenceWSClient] = []
    symbols = sorted({market.reference_symbol for market in resolved_markets if market.reference_symbol})

    def _on_reference_quote(quote) -> None:
        ts_recv_ms = int(getattr(quote, "t_recv_wall_ms", _now_ms()) or _now_ms())
        runtime.on_reference(
            source=str(getattr(quote, "source", "spot")),
            symbol=str(getattr(quote, "symbol", "")),
            value=float(getattr(quote, "value", 0.0)),
            ts_event_ms=getattr(quote, "t_event_ms", None),
            ts_recv_ms=ts_recv_ms,
        )

    for source in [s.strip().lower() for s in reference_sources.split(",") if s.strip()]:
        if source.startswith("poll_"):
            feed = ReferenceFeed(
                aggregator=None,  # type: ignore[arg-type]
                tape=event_tape,
                config=ReferenceFeedConfig(
                    symbols=symbols,
                    poll_interval_secs=settings.reference_poll_secs,
                    source=source,
                ),
                on_quote=_on_reference_quote,
                reference_store=None,
            )
            ref_feeds.append(feed)
            tasks.append(asyncio.create_task(feed.run()))
        elif source == "ws_kraken":
            ws_client = ReferenceWSClient(
                tape=event_tape,
                config=ReferenceWSConfig(venue="kraken", symbols=symbols),
                on_quote=_on_reference_quote,
                reference_store=None,
            )
            ref_ws_clients.append(ws_client)
            tasks.append(asyncio.create_task(ws_client.run()))

    async def _quote_loop() -> None:
        while not stop_event.is_set():
            now_ms = _now_ms()
            await runtime.run_quote_cycle(now_ms)
            await asyncio.sleep(max(0.05, quote_interval_ms / 1000.0))

    async def _stats_loop() -> None:
        while not stop_event.is_set():
            now_ms = _now_ms()
            await runtime.run_stats_cycle(now_ms)
            await asyncio.sleep(max(0.1, stats_interval_ms / 1000.0))

    tasks.append(asyncio.create_task(_quote_loop()))
    tasks.append(asyncio.create_task(_stats_loop()))

    def _shutdown(*_args) -> None:
        stop_event.set()
        market_client.stop()
        for feed in ref_feeds:
            feed.stop()
        for client in ref_ws_clients:
            client.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    db.append_log(_now_ms(), "INFO", f"run_system_started mode={mode}", {"run_id": run_id})
    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    db.append_log(_now_ms(), "INFO", "run_system_stopped", {"run_id": run_id})
    event_tape.close()
    decision_tape.close()
    trade_tape.close()
    db.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket V1 dual-loop system runner")
    parser.add_argument("--mode", default=None, help="OBSERVE|PAPER|TRADE")
    parser.add_argument("--markets", default=None, help="Path to markets config")
    parser.add_argument("--log-dir", default=None, help="Directory for JSONL compatibility tapes")
    parser.add_argument("--db-path", default=None, help="SQLite path")
    parser.add_argument("--constitution", default=None, help="Path to constitution config")
    parser.add_argument("--auto_discover", action="store_true", help="Resolve markets via discovery")
    parser.add_argument("--reference_source", default=None, help="CSV sources: poll_coinbase,poll_binance_perp,ws_kraken")
    parser.add_argument("--quote-interval-ms", type=int, default=None, help="Quote loop interval in ms")
    parser.add_argument("--stats-interval-ms", type=int, default=None, help="Stats loop interval in ms")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run live broker methods")
    return parser.parse_args()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
