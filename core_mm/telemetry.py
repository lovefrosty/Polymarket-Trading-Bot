from __future__ import annotations

from collections import Counter
from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence

from core_mm.book_manager import BookManager
from core_mm.main_loop import MarketCycleResult, TokenCycleDecision
from core_mm.memory import SessionSummary
from core_mm.positions import PositionTracker
from core_mm.runner import CoreMMRunner


class StandaloneTelemetry:
    def __init__(
        self,
        *,
        runtime_root: Path,
        book_manager: BookManager,
        position_tracker: PositionTracker,
        mode: str,
        flush_every: int = 25,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.meta_dir = self.runtime_root / "meta"
        self.tapes_dir = self.runtime_root / "tapes"
        self.db_path = self.runtime_root / "runtime.db"
        self._book_manager = book_manager
        self._position_tracker = position_tracker
        self._mode = str(mode).upper()
        self._flush_every = max(1, int(flush_every))
        self._cx = sqlite3.connect(self.db_path.as_posix())
        self._cx.execute("PRAGMA journal_mode=WAL")
        self._cx.execute("PRAGMA synchronous=NORMAL")
        self._cx.execute("PRAGMA busy_timeout=5000")
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.tapes_dir.mkdir(parents=True, exist_ok=True)
        self._event_id = 0
        self._queues: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._tape_paths = {
            "decisions": self.tapes_dir / "decisions.jsonl",
            "orders": self.tapes_dir / "orders.jsonl",
            "fills": self.tapes_dir / "fills.jsonl",
            "pnl": self.tapes_dir / "pnl.jsonl",
        }
        self._pending_markouts: Dict[str, Dict[str, Any]] = {}
        self._last_summary: Dict[str, Any] = {}
        self._ensure_schema()

    def close(self) -> None:
        self.flush()
        self._write_run_summary(final=True)
        self._cx.close()

    def to_session_summary(self, *, run_id: str, symbol: str, market_slug: str = "") -> SessionSummary:
        """Build a SessionSummary from telemetry data for memory ingestion."""
        self.flush()
        cur = self._cx.cursor()

        def scalar(sql: str) -> Any:
            row = cur.execute(sql).fetchone()
            return row[0] if row else None

        total_fills = int(scalar("SELECT COUNT(*) FROM fills") or 0)
        decisions = int(scalar("SELECT COUNT(*) FROM decisions") or 0)
        realized_pnl = float(scalar("SELECT COALESCE(MAX(realized_net_pnl), 0.0) FROM paper_pnl") or 0.0)
        placed_orders = int(scalar("SELECT COUNT(*) FROM orders WHERE status IN ('open', 'replace')") or 0)
        fill_rate = _safe_div(total_fills, placed_orders)

        # Adverse fills: count fills where markout_1s_bps < 0
        adverse_fills = int(scalar(
            "SELECT COUNT(*) FROM execution_quality WHERE markout_1s_bps IS NOT NULL AND markout_1s_bps < 0"
        ) or 0)

        # Average spread from execution quality
        avg_spread = float(scalar(
            "SELECT COALESCE(AVG(realized_spread_bps), 0.0) FROM execution_quality WHERE realized_spread_bps IS NOT NULL"
        ) or 0.0)

        # Average vol from execution quality markout variance (proxy)
        avg_vol = float(scalar(
            "SELECT COALESCE(AVG(ABS(markout_1s_bps)), 0.0) FROM execution_quality WHERE markout_1s_bps IS NOT NULL"
        ) or 0.0)

        # Max position seen
        max_pos = float(scalar(
            "SELECT COALESCE(MAX(ABS(yes_qty)), 0.0) FROM inventory"
        ) or 0.0)

        # Duration
        first_ts = scalar("SELECT MIN(ts_ms) FROM decisions")
        last_ts = scalar("SELECT MAX(ts_ms) FROM decisions")
        duration_secs = 0.0
        if first_ts is not None and last_ts is not None:
            duration_secs = max(0.0, (int(last_ts) - int(first_ts)) / 1000.0)

        return SessionSummary(
            run_id=run_id,
            symbol=symbol,
            market_slug=market_slug,
            duration_secs=duration_secs,
            total_fills=total_fills,
            adverse_fills=adverse_fills,
            fill_rate=fill_rate,
            realized_pnl=realized_pnl,
            avg_spread_bps=avg_spread,
            avg_vol_bps=avg_vol,
            max_position=max_pos,
            decisions=decisions,
            ts_ms=_now_ms(),
        )

    def record_cycle(
        self,
        *,
        now_ms: int,
        runner: CoreMMRunner,
        result: Optional[MarketCycleResult] = None,
        results: Optional[Sequence[MarketCycleResult]] = None,
        feed_status: Dict[str, Any],
        last_error: Optional[str],
        config: Dict[str, Any],
    ) -> None:
        # Support both single result (backward compat) and multi-result list
        all_results: List[MarketCycleResult] = []
        if results is not None:
            all_results = [r for r in results if r is not None]
        elif result is not None:
            all_results = [result]
        for r in all_results:
            market_slug = r.market_id
            for token_decision in r.token_decisions:
                self._record_decision(now_ms=now_ms, market_slug=market_slug, token_decision=token_decision)
            for action in r.order_actions:
                self._record_order_action(now_ms=now_ms, market_slug=market_slug, action=action)

        market_slug = runner.current_market.slug if runner.current_market is not None else None
        self._record_open_orders(now_ms=now_ms, runner=runner, market_slug=market_slug)
        self._record_book_snapshot(now_ms=now_ms, runner=runner)
        self._record_inventory(now_ms=now_ms, runner=runner)
        self._record_system_state(
            now_ms=now_ms,
            runner=runner,
            feed_status=feed_status,
            last_error=last_error,
            config=config,
        )
        self.process_markouts(now_ms=now_ms)
        self._write_run_summary(final=False)
        if self._pending_row_count() >= self._flush_every:
            self.flush()

    def record_fill_events(
        self,
        *,
        now_ms: int,
        market_slug: Optional[str],
        fill_events: Sequence[Dict[str, Any]],
        broker_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        for fill in fill_events:
            self._record_fill(now_ms=now_ms, market_slug=market_slug, fill=fill, broker_stats=broker_stats or {})
        if fill_events:
            self._write_run_summary(final=False)
        if self._pending_row_count() >= self._flush_every:
            self.flush()

    def process_markouts(self, *, now_ms: Optional[int] = None) -> None:
        ts_ms = int(now_ms or _now_ms())
        completed: List[str] = []
        for order_id, pending in list(self._pending_markouts.items()):
            token_id = str(pending["token_id"])
            book = self._book_manager.get_book(token_id)
            if book is None or book.mid_price is None:
                continue
            age_ms = ts_ms - int(pending["fill_ts_ms"])
            updates: Dict[str, Any] = {}
            if pending.get("markout_1s_bps") is None and age_ms >= 1000:
                updates["markout_1s_bps"] = _signed_edge_bps(
                    side=str(pending["side"]),
                    reference=float(pending["fill_price"]),
                    compare=float(book.mid_price),
                )
            if pending.get("markout_5s_bps") is None and age_ms >= 5000:
                updates["markout_5s_bps"] = _signed_edge_bps(
                    side=str(pending["side"]),
                    reference=float(pending["fill_price"]),
                    compare=float(book.mid_price),
                )
            if not updates:
                continue
            pending.update(updates)
            payload = dict(pending["payload_json"])
            payload.update({k: v for k, v in updates.items() if v is not None})
            row = {
                "ts_ms": ts_ms,
                "event_id": self._next_event_id(),
                "order_id": order_id,
                "token_id": token_id,
                "side": pending["side"],
                "fill_ts_ms": pending["fill_ts_ms"],
                "placement_ts_ms": pending["placement_ts_ms"],
                "fill_price": pending["fill_price"],
                "mid_at_placement": pending["mid_at_placement"],
                "mid_at_fill": pending["mid_at_fill"],
                "realized_spread_bps": pending["realized_spread_bps"],
                "markout_1s_bps": pending.get("markout_1s_bps"),
                "markout_5s_bps": pending.get("markout_5s_bps"),
                "net_edge_bps": pending["net_edge_bps"],
                "slippage_bps": pending["slippage_bps"],
                "fill_trigger": pending["fill_trigger"],
                "quote_mode": pending["quote_mode"],
                "payload_json": json.dumps(payload, sort_keys=True),
            }
            self._queues["execution_quality"].append(row)
            if pending.get("markout_1s_bps") is not None and pending.get("markout_5s_bps") is not None:
                completed.append(order_id)
        for order_id in completed:
            self._pending_markouts.pop(order_id, None)

    def flush(self) -> None:
        with self._cx:
            for table, rows in self._queues.items():
                if not rows:
                    continue
                keys = list(rows[0].keys())
                sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
                self._cx.executemany(sql, [tuple(row.get(key) for key in keys) for row in rows])
        self._queues.clear()

    def _record_decision(self, *, now_ms: int, market_slug: Optional[str], token_decision: TokenCycleDecision) -> None:
        action = _decision_action(token_decision)
        reasons = _decision_reasons(token_decision)
        expected_edge = None
        expected_cost = None
        if token_decision.metrics is not None and token_decision.metrics.best_bid is not None and token_decision.metrics.best_ask is not None:
            spread = float(token_decision.metrics.best_ask) - float(token_decision.metrics.best_bid)
            expected_edge = spread / 2.0
        if token_decision.risk_decision is not None:
            expected_cost = float(len(token_decision.risk_decision.reasons or [])) * 0.0
        payload = {
            "book_diag": token_decision.book_diag.as_dict(),
            "metrics": token_decision.metrics.as_dict() if token_decision.metrics is not None else None,
            "flow_filter": asdict(token_decision.flow_filter) if token_decision.flow_filter is not None else None,
            "quote_plan": asdict(token_decision.quote_plan) if token_decision.quote_plan is not None else None,
            "size_plan": asdict(token_decision.size_plan) if token_decision.size_plan is not None else None,
            "risk_decision": asdict(token_decision.risk_decision) if token_decision.risk_decision is not None else None,
            "desired_quotes": [
                {
                    "quote_key": quote.quote_key,
                    "side": quote.side,
                    "price": quote.price,
                    "size": quote.size,
                    "metadata": quote.metadata or {},
                }
                for quote in token_decision.desired_quotes
            ],
        }
        row = {
            "ts_ms": now_ms,
            "decision_id": f"{market_slug or 'unknown'}:{token_decision.token_id}:{now_ms}:{self._next_event_id()}",
            "market": market_slug,
            "token_id": token_decision.token_id,
            "action": action,
            "reason_codes": ",".join(reasons),
            "p_hat": None,
            "expected_edge": expected_edge,
            "expected_cost": expected_cost,
            "policy_json": json.dumps(payload, sort_keys=True),
        }
        self._queues["decisions"].append(row)
        self._append_tape("decisions", row)

    def _record_order_action(self, *, now_ms: int, market_slug: Optional[str], action: Any) -> None:
        desired = action.desired_quote
        status = {
            "PLACE": "open",
            "CANCEL": "canceled",
            "CANCEL_AND_REPLACE": "replace",
            "NOOP": "noop",
        }.get(str(action.action), str(action.action).lower())
        row = {
            "ts_ms": now_ms,
            "event_id": self._next_event_id(),
            "order_id": action.existing_order_id or (desired.quote_key if desired is not None else None),
            "token_id": desired.token_id if desired is not None else None,
            "market_slug": market_slug,
            "side": desired.side if desired is not None else None,
            "price": desired.price if desired is not None else None,
            "size": desired.size if desired is not None else None,
            "status": status,
            "client_order_id": desired.quote_key if desired is not None else None,
            "quote_group_id": market_slug,
            "reason": action.reason,
            "payload_json": json.dumps(
                {
                    "action": action.action,
                    "quote_key": action.quote_key,
                    "reason": action.reason,
                    "desired_quote": {
                        "token_id": desired.token_id,
                        "side": desired.side,
                        "price": desired.price,
                        "size": desired.size,
                        "metadata": desired.metadata or {},
                    }
                    if desired is not None
                    else None,
                },
                sort_keys=True,
            ),
        }
        self._queues["orders"].append(row)
        self._append_tape("orders", row)

    def _record_open_orders(self, *, now_ms: int, runner: CoreMMRunner, market_slug: Optional[str]) -> None:
        broker = runner.broker
        if broker is None or not hasattr(broker, "get_open_orders"):
            return
        snapshot = broker.get_open_orders()
        if not snapshot.success:
            return
        for order in snapshot.payload.get("orders") or []:
            row = {
                "ts_ms": now_ms,
                "event_id": self._next_event_id(),
                "order_id": order.get("order_id") or order.get("orderID"),
                "token_id": order.get("token_id"),
                "side": order.get("side"),
                "price": order.get("price"),
                "size": order.get("size"),
                "status": order.get("status") or "open",
                "client_order_id": order.get("client_order_id"),
                "quote_group_id": order.get("quote_group_id") or market_slug,
            }
            self._queues["open_orders_snapshot"].append(row)

    def _record_book_snapshot(self, *, now_ms: int, runner: CoreMMRunner) -> None:
        if runner.current_market is None:
            return
        max_levels = 20
        for token_id in runner.current_market.token_ids:
            book = self._book_manager.get_book(str(token_id))
            if book is None:
                continue
            eid = self._next_event_id()
            for price, size in book.bids[:max_levels]:
                self._queues["book_snapshots"].append({
                    "ts_ms": now_ms, "event_id": eid,
                    "token_id": str(token_id), "side": "bid",
                    "price": float(price), "size": float(size),
                })
            for price, size in book.asks[:max_levels]:
                self._queues["book_snapshots"].append({
                    "ts_ms": now_ms, "event_id": eid,
                    "token_id": str(token_id), "side": "ask",
                    "price": float(price), "size": float(size),
                })

    def _record_fill(
        self,
        *,
        now_ms: int,
        market_slug: Optional[str],
        fill: Dict[str, Any],
        broker_stats: Dict[str, Any],
    ) -> None:
        payload = {
            "market_slug": market_slug,
            "fee_bps": fill.get("fee_bps"),
            "fee_usdc": fill.get("fee_usdc"),
            "gross_notional": fill.get("gross_notional"),
            "net_notional": fill.get("net_notional"),
            "liquidity_mode": fill.get("liquidity_mode"),
            "fill_trigger": fill.get("fill_trigger"),
            "realized_gross_pnl_delta": fill.get("realized_gross_pnl_delta"),
            "realized_net_pnl_delta": fill.get("realized_net_pnl_delta"),
            "inventory_after_fill": fill.get("inventory_after_fill"),
            "placement_metadata": fill.get("placement_metadata"),
        }
        row = {
            "ts_ms": int(fill.get("ts_ms") or now_ms),
            "event_id": self._next_event_id(),
            "order_id": fill.get("order_id"),
            "token_id": fill.get("token_id"),
            "side": fill.get("side"),
            "fill_price": fill.get("price"),
            "fill_qty": fill.get("size"),
            "payload_json": json.dumps(payload, sort_keys=True),
        }
        self._queues["fills"].append(row)
        self._append_tape("fills", row)

        placement = dict(fill.get("placement_metadata") or {})
        mid_at_placement = _float_or_none(placement.get("mid"))
        book = self._book_manager.get_book(str(fill.get("token_id") or ""))
        mid_at_fill = _float_or_none(book.mid_price if book is not None else None)
        fill_price = _float_or_none(fill.get("price"))
        realized_spread_bps = None
        slippage_bps = None
        if fill_price is not None and mid_at_fill is not None:
            realized_spread_bps = _signed_edge_bps(side=str(fill.get("side") or ""), reference=fill_price, compare=mid_at_fill)
        if fill_price is not None and mid_at_placement is not None:
            slippage_bps = -_signed_edge_bps(side=str(fill.get("side") or ""), reference=fill_price, compare=mid_at_placement)
        fee_bps = _float_or_none(fill.get("fee_bps")) or 0.0
        net_edge_bps = (realized_spread_bps - fee_bps) if realized_spread_bps is not None else None
        eq_payload = {
            "market_slug": market_slug,
            "fee_bps": fee_bps,
            "fee_usdc": fill.get("fee_usdc"),
            "gross_notional": fill.get("gross_notional"),
            "net_notional": fill.get("net_notional"),
            "liquidity_mode": fill.get("liquidity_mode"),
            "fill_trigger": fill.get("fill_trigger"),
            "quote_mode": placement.get("quote_mode"),
            "best_bid_at_placement": placement.get("best_bid"),
            "best_ask_at_placement": placement.get("best_ask"),
            "spread_bps_at_placement": placement.get("spread_bps"),
        }
        eq_row = {
            "ts_ms": now_ms,
            "event_id": self._next_event_id(),
            "order_id": fill.get("order_id"),
            "token_id": fill.get("token_id"),
            "side": fill.get("side"),
            "fill_ts_ms": int(fill.get("ts_ms") or now_ms),
            "placement_ts_ms": int(fill.get("placed_at_ms") or now_ms),
            "fill_price": fill.get("price"),
            "mid_at_placement": mid_at_placement,
            "mid_at_fill": mid_at_fill,
            "realized_spread_bps": realized_spread_bps,
            "markout_1s_bps": None,
            "markout_5s_bps": None,
            "net_edge_bps": net_edge_bps,
            "slippage_bps": slippage_bps,
            "fill_trigger": fill.get("fill_trigger"),
            "quote_mode": placement.get("quote_mode"),
            "payload_json": json.dumps(eq_payload, sort_keys=True),
        }
        self._queues["execution_quality"].append(eq_row)
        self._pending_markouts[str(fill.get("order_id"))] = {
            **eq_row,
            "payload_json": eq_payload,
        }

    def _record_inventory(self, *, now_ms: int, runner: CoreMMRunner) -> None:
        positions = runner.position_tracker.snapshot()
        usdc_balance = None
        for token_id, position in positions.items():
            row = {
                "ts_ms": now_ms,
                "token_id": token_id,
                "yes_qty": float(position.size),
                "no_qty": 0.0,
                "usdc": usdc_balance,
                "source": self._mode,
            }
            self._queues["inventory"].append(row)

    def _record_pnl(
        self,
        *,
        now_ms: int,
        market_slug: Optional[str],
        token_id: str,
        broker_stats: Dict[str, Any],
    ) -> None:
        position = self._position_tracker.get_position(token_id)
        book = self._book_manager.get_book(token_id)
        mark = _float_or_none(book.mid_price if book is not None else None)
        unrealized_pnl = None
        if mark is not None and position.size > 0 and position.avg_price > 0:
            unrealized_pnl = (float(mark) - float(position.avg_price)) * float(position.size)
        realized_gross = float(broker_stats.get("realized_gross_pnl") or 0.0)
        realized_net = float(broker_stats.get("realized_net_pnl") or 0.0)
        cumulative_fees = float(broker_stats.get("cumulative_fees") or 0.0)
        turnover = float(broker_stats.get("turnover") or 0.0)
        win_count = int(broker_stats.get("win_count") or 0)
        loss_count = int(broker_stats.get("loss_count") or 0)
        row = {
            "ts_ms": now_ms,
            "event_id": self._next_event_id(),
            "market_slug": market_slug,
            "token_id": token_id,
            "realized_gross_pnl": realized_gross,
            "realized_net_pnl": realized_net,
            "unrealized_pnl": unrealized_pnl,
            "cumulative_fees": cumulative_fees,
            "turnover": turnover,
            "win_count": win_count,
            "loss_count": loss_count,
            "payload_json": json.dumps({"mark": mark, "avg_price": position.avg_price, "position_size": position.size}, sort_keys=True),
        }
        self._queues["paper_pnl"].append(row)
        self._append_tape("pnl", row)

    def _record_system_state(
        self,
        *,
        now_ms: int,
        runner: CoreMMRunner,
        feed_status: Dict[str, Any],
        last_error: Optional[str],
        config: Dict[str, Any],
    ) -> None:
        runner_status = runner.status()
        reasons: List[str] = []
        if not runner_status.has_books:
            reasons.append("books_unavailable")
        if last_error:
            reasons.append(str(last_error))
        broker_stats = runner.broker.stats() if hasattr(runner.broker, "stats") else {}
        merge_stats = runner.merge_stats if hasattr(runner, "merge_stats") else {}
        flow_stats = runner.main_loop.flow_stats if hasattr(runner, "main_loop") and hasattr(runner.main_loop, "flow_stats") else {}
        per_token_quote_stats = runner.per_token_quote_stats if hasattr(runner, "per_token_quote_stats") else {}
        payload = {
            "runner": asdict(runner_status),
            "feed": dict(feed_status),
            "broker_stats": broker_stats,
            "merge_stats": merge_stats,
            "flow_stats": flow_stats,
            "per_token_quote_stats": per_token_quote_stats,
            "config": config,
        }
        row = {
            "as_of_ts": now_ms,
            "is_frozen": 1 if bool(reasons) else 0,
            "reasons": ",".join(reasons),
            "mode": self._mode,
            "payload_json": json.dumps(payload, sort_keys=True),
        }
        self._queues["system_state"].append(row)
        token_ids = set(runner.position_tracker.snapshot().keys())
        if runner.current_market is not None:
            token_ids.update(str(token_id) for token_id in runner.current_market.token_ids)
        for token_id in sorted(token_ids):
            self._record_pnl(
                now_ms=now_ms,
                market_slug=(runner.current_market.slug if runner.current_market is not None else None),
                token_id=str(token_id),
                broker_stats=broker_stats,
            )

    def _write_run_summary(self, *, final: bool) -> None:
        summary = self._summary_payload()
        summary["final"] = bool(final)
        (self.meta_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
        self._last_summary = summary

    def _summary_payload(self) -> Dict[str, Any]:
        self.flush()
        cur = self._cx.cursor()

        def scalar(sql: str) -> Any:
            row = cur.execute(sql).fetchone()
            return row[0] if row else None

        realized_net = float(scalar("SELECT COALESCE(MAX(realized_net_pnl), 0.0) FROM paper_pnl") or 0.0)
        realized_gross = float(scalar("SELECT COALESCE(MAX(realized_gross_pnl), 0.0) FROM paper_pnl") or 0.0)
        fees = float(scalar("SELECT COALESCE(MAX(cumulative_fees), 0.0) FROM paper_pnl") or 0.0)
        turnover = float(scalar("SELECT COALESCE(MAX(turnover), 0.0) FROM paper_pnl") or 0.0)
        unrealized = float(scalar("SELECT COALESCE(SUM(unrealized_pnl), 0.0) FROM (SELECT token_id, MAX(ts_ms) AS max_ts FROM paper_pnl GROUP BY token_id) latest JOIN paper_pnl p ON p.token_id = latest.token_id AND p.ts_ms = latest.max_ts") or 0.0)
        fills = int(scalar("SELECT COUNT(*) FROM fills") or 0)
        decisions = int(scalar("SELECT COUNT(*) FROM decisions") or 0)
        placed_orders = int(scalar("SELECT COUNT(*) FROM orders WHERE status IN ('open', 'replace')") or 0)
        canceled_orders = int(scalar("SELECT COUNT(*) FROM orders WHERE status = 'canceled'") or 0)
        fill_rate = _safe_div(fills, placed_orders)
        total_pnl = realized_net + unrealized
        per_token_rows = cur.execute(
            """
            SELECT token_id, MAX(ts_ms) AS max_ts
            FROM paper_pnl
            GROUP BY token_id
            """
        ).fetchall()
        per_token: Dict[str, Dict[str, float]] = {}
        for token_id, max_ts in per_token_rows:
            row = cur.execute(
                """
                SELECT realized_net_pnl, unrealized_pnl, cumulative_fees, turnover
                FROM paper_pnl
                WHERE token_id = ? AND ts_ms = ?
                ORDER BY event_id DESC
                LIMIT 1
                """,
                (token_id, max_ts),
            ).fetchone()
            if row is None:
                continue
            per_token[str(token_id)] = {
                "realized_net_pnl": float(row[0] or 0.0),
                "unrealized_pnl": float(row[1] or 0.0),
                "cumulative_fees": float(row[2] or 0.0),
                "turnover": float(row[3] or 0.0),
            }
        cycle_summary = _decision_cycle_summary(cur)
        execution_quality = _execution_quality_summary(cur)
        loss_source_hints: List[str] = []
        if cycle_summary["quoteable_cycles"] <= 0:
            loss_source_hints.append("no_quoteable_cycles")
        if cycle_summary["quoteable_cycles"] > 0 and fills <= 0:
            loss_source_hints.append("quoteable_but_no_fills")
        if execution_quality["avg_markout_5s_bps"] is not None and float(execution_quality["avg_markout_5s_bps"]) < 0:
            loss_source_hints.append("adverse_selection_after_5s")
        if (
            execution_quality["avg_realized_spread_bps"] is not None
            and execution_quality["avg_net_edge_bps"] is not None
            and float(execution_quality["avg_realized_spread_bps"]) > 0
            and float(execution_quality["avg_net_edge_bps"]) < 0
        ):
            loss_source_hints.append("fees_or_quote_width_overwhelm_spread_capture")
        if cycle_summary["freeze_cycles"] > cycle_summary["quoteable_cycles"]:
            loss_source_hints.append("selection_or_feed_gating_dominates_runtime")
        phase0_acceptance = _phase0_acceptance_summary(
            total_pnl=total_pnl,
            fills=fills,
            cycle_summary=cycle_summary,
            loss_source_hints=loss_source_hints,
        )
        return {
            "runtime_db_path": self.db_path.as_posix(),
            "decisions": decisions,
            "placed_orders": placed_orders,
            "canceled_orders": canceled_orders,
            "fills": fills,
            "fill_rate": fill_rate,
            "realized_gross_pnl": realized_gross,
            "realized_net_pnl": realized_net,
            "unrealized_pnl": unrealized,
            "total_pnl": total_pnl,
            "total_fees": fees,
            "turnover": turnover,
            "per_token": per_token,
            "cycle_summary": cycle_summary,
            "execution_quality": execution_quality,
            "phase0_acceptance": phase0_acceptance,
            "updated_at_ms": _now_ms(),
        }

    def _pending_row_count(self) -> int:
        return sum(len(rows) for rows in self._queues.values())

    def _append_tape(self, tape_name: str, row: Dict[str, Any]) -> None:
        path = self._tape_paths[tape_name]
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def _next_event_id(self) -> int:
        self._event_id += 1
        return self._event_id

    def _ensure_schema(self) -> None:
        cur = self._cx.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
              ts_ms INTEGER,
              decision_id TEXT,
              market TEXT,
              token_id TEXT,
              action TEXT,
              reason_codes TEXT,
              p_hat REAL,
              expected_edge REAL,
              expected_cost REAL,
              policy_json TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
              ts_ms INTEGER,
              event_id INTEGER,
              order_id TEXT,
              token_id TEXT,
              market_slug TEXT,
              side TEXT,
              price REAL,
              size REAL,
              status TEXT,
              client_order_id TEXT,
              quote_group_id TEXT,
              reason TEXT,
              payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS fills (
              ts_ms INTEGER,
              event_id INTEGER,
              order_id TEXT,
              token_id TEXT,
              side TEXT,
              fill_price REAL,
              fill_qty REAL,
              payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS open_orders_snapshot (
              ts_ms INTEGER,
              event_id INTEGER,
              order_id TEXT,
              token_id TEXT,
              side TEXT,
              price REAL,
              size REAL,
              status TEXT,
              client_order_id TEXT,
              quote_group_id TEXT
            );
            CREATE TABLE IF NOT EXISTS inventory (
              ts_ms INTEGER,
              token_id TEXT,
              yes_qty REAL,
              no_qty REAL,
              usdc REAL,
              source TEXT
            );
            CREATE TABLE IF NOT EXISTS system_state (
              as_of_ts INTEGER,
              is_frozen INTEGER,
              reasons TEXT,
              mode TEXT,
              payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_pnl (
              ts_ms INTEGER,
              event_id INTEGER,
              market_slug TEXT,
              token_id TEXT,
              realized_gross_pnl REAL,
              realized_net_pnl REAL,
              unrealized_pnl REAL,
              cumulative_fees REAL,
              turnover REAL,
              win_count INTEGER,
              loss_count INTEGER,
              payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS book_snapshots (
              ts_ms INTEGER,
              event_id INTEGER,
              token_id TEXT,
              side TEXT,
              price REAL,
              size REAL
            );
            CREATE TABLE IF NOT EXISTS execution_quality (
              ts_ms INTEGER,
              event_id INTEGER,
              order_id TEXT,
              token_id TEXT,
              side TEXT,
              fill_ts_ms INTEGER,
              placement_ts_ms INTEGER,
              fill_price REAL,
              mid_at_placement REAL,
              mid_at_fill REAL,
              realized_spread_bps REAL,
              markout_1s_bps REAL,
              markout_5s_bps REAL,
              net_edge_bps REAL,
              slippage_bps REAL,
              fill_trigger TEXT,
              quote_mode TEXT,
              payload_json TEXT
            );
            """
        )
        self._cx.commit()


def _decision_action(token_decision: TokenCycleDecision) -> str:
    if token_decision.book_diag.state != "book_ok":
        return "FREEZE"
    if token_decision.risk_decision is not None and token_decision.risk_decision.action in {"STOP_LOSS", "TAKE_PROFIT"}:
        return "EXIT"
    if token_decision.desired_quotes:
        return "QUOTE"
    return "SKIP"


def _decision_reasons(token_decision: TokenCycleDecision) -> List[str]:
    reasons: List[str] = []
    if token_decision.book_diag.state != "book_ok":
        reasons.append(token_decision.book_diag.state)
        return reasons
    if token_decision.flow_filter is not None:
        if not token_decision.flow_filter.allow_buy:
            reasons.append("flow_blocks_buy")
        if not token_decision.flow_filter.allow_sell:
            reasons.append("flow_blocks_sell")
    if token_decision.risk_decision is not None:
        reasons.extend([str(reason) for reason in token_decision.risk_decision.reasons])
    if token_decision.quote_plan is not None:
        reasons.append(f"bid_{token_decision.quote_plan.bid_mode}")
        reasons.append(f"ask_{token_decision.quote_plan.ask_mode}")
    return sorted({reason for reason in reasons if reason})


def _signed_edge_bps(*, side: str, reference: float, compare: float) -> Optional[float]:
    ref = _float_or_none(reference)
    other = _float_or_none(compare)
    if ref is None or other is None or ref <= 0:
        return None
    if str(side).lower() == "buy":
        return ((other - ref) / ref) * 10_000.0
    return ((ref - other) / ref) * 10_000.0


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_div(numerator: float, denominator: float) -> float:
    if float(denominator) <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _decision_cycle_summary(cur: sqlite3.Cursor) -> Dict[str, Any]:
    rows = cur.execute(
        """
        SELECT ts_ms, market, GROUP_CONCAT(action), GROUP_CONCAT(COALESCE(reason_codes, ''))
        FROM decisions
        GROUP BY ts_ms, COALESCE(market, '')
        ORDER BY ts_ms ASC
        """
    ).fetchall()
    if not rows:
        return {
            "cycles_total": 0,
            "quoteable_cycles": 0,
            "freeze_cycles": 0,
            "no_quote_cycles": 0,
            "quoteable_ratio": 0.0,
            "inactive_ratio": 0.0,
            "estimated_quoteable_time_ms": 0,
            "estimated_inactive_time_ms": 0,
            "inferred_cycle_interval_ms": 0,
            "freeze_reason_counts": {},
            "no_quote_reason_counts": {},
        }
    cycle_interval_ms = _infer_cycle_interval_ms([int(row[0]) for row in rows])
    quoteable = 0
    freeze = 0
    no_quote = 0
    freeze_reasons: Counter[str] = Counter()
    no_quote_reasons: Counter[str] = Counter()
    for _, _, actions_blob, reasons_blob in rows:
        actions = {part for part in str(actions_blob or "").split(",") if part}
        reasons = [
            reason
            for reason in str(reasons_blob or "").split(",")
            if reason
        ]
        if "QUOTE" in actions:
            quoteable += 1
            continue
        if actions == {"FREEZE"} or ("FREEZE" in actions and "QUOTE" not in actions):
            freeze += 1
            freeze_reasons.update(reasons)
            continue
        no_quote += 1
        no_quote_reasons.update(reasons)
    total = len(rows)
    inactive = freeze + no_quote
    return {
        "cycles_total": total,
        "quoteable_cycles": quoteable,
        "freeze_cycles": freeze,
        "no_quote_cycles": no_quote,
        "quoteable_ratio": _safe_div(quoteable, total),
        "inactive_ratio": _safe_div(inactive, total),
        "estimated_quoteable_time_ms": int(quoteable * cycle_interval_ms),
        "estimated_inactive_time_ms": int(inactive * cycle_interval_ms),
        "inferred_cycle_interval_ms": int(cycle_interval_ms),
        "freeze_reason_counts": {key: int(freeze_reasons[key]) for key in sorted(freeze_reasons)},
        "no_quote_reason_counts": {key: int(no_quote_reasons[key]) for key in sorted(no_quote_reasons)},
    }


def _infer_cycle_interval_ms(ts_values: Sequence[int]) -> int:
    if len(ts_values) < 2:
        return 1000
    deltas = sorted(
        max(0, int(curr) - int(prev))
        for prev, curr in zip(ts_values, ts_values[1:])
        if int(curr) > int(prev)
    )
    if not deltas:
        return 1000
    return int(deltas[len(deltas) // 2])


def _execution_quality_summary(cur: sqlite3.Cursor) -> Dict[str, Any]:
    rows = cur.execute(
        """
        SELECT realized_spread_bps, markout_1s_bps, markout_5s_bps, net_edge_bps
        FROM execution_quality
        ORDER BY ts_ms ASC
        """
    ).fetchall()
    realized_spread = [float(row[0]) for row in rows if row[0] is not None]
    markout_1s = [float(row[1]) for row in rows if row[1] is not None]
    markout_5s = [float(row[2]) for row in rows if row[2] is not None]
    net_edge = [float(row[3]) for row in rows if row[3] is not None]
    fee_bps = [
        float(realized) - float(net)
        for realized, net in zip((row[0] for row in rows), (row[3] for row in rows))
        if realized is not None and net is not None
    ]
    return {
        "fills_measured": len(rows),
        "avg_realized_spread_bps": _avg(realized_spread),
        "avg_fee_bps": _avg(fee_bps),
        "avg_net_edge_bps": _avg(net_edge),
        "avg_markout_1s_bps": _avg(markout_1s),
        "avg_markout_5s_bps": _avg(markout_5s),
        "negative_markout_1s_rate": _safe_div(sum(1 for value in markout_1s if value < 0), len(markout_1s)),
        "negative_markout_5s_rate": _safe_div(sum(1 for value in markout_5s if value < 0), len(markout_5s)),
        "negative_net_edge_rate": _safe_div(sum(1 for value in net_edge if value < 0), len(net_edge)),
    }


def _phase0_acceptance_summary(
    *,
    total_pnl: float,
    fills: int,
    cycle_summary: Dict[str, Any],
    loss_source_hints: Sequence[str],
) -> Dict[str, Any]:
    structural_hints = {
        "no_quoteable_cycles",
        "selection_or_feed_gating_dominates_runtime",
    }
    tunable_hints = {
        "quoteable_but_no_fills",
        "adverse_selection_after_5s",
        "fees_or_quote_width_overwhelm_spread_capture",
    }
    hint_set = {str(hint) for hint in loss_source_hints}
    net_pnl_non_negative = bool(total_pnl >= 0.0)
    quoteable_cycles_present = bool(cycle_summary["quoteable_cycles"] > 0)
    fills_present = bool(fills > 0)
    structural_blocker = bool(hint_set & structural_hints)
    tunable_loss = bool(hint_set) and hint_set.issubset(tunable_hints)
    if net_pnl_non_negative and quoteable_cycles_present and fills_present:
        result = "pass"
        rationale = "economics_non_negative"
    elif structural_blocker:
        result = "structural_blocker"
        rationale = "architectural_or_feed_blocker_detected"
    elif total_pnl < 0.0 and tunable_loss:
        result = "tunable_loss"
        rationale = "losses_point_to_parameter_tuning"
    else:
        result = "needs_review"
        rationale = "more_runtime_or_manual_review_required"
    return {
        "result": result,
        "rationale": rationale,
        "economics_ready_for_phase1": bool(result == "pass"),
        "net_pnl_non_negative": net_pnl_non_negative,
        "quoteable_cycles_present": quoteable_cycles_present,
        "fills_present": fills_present,
        "manual_tuning_review_required": bool(result == "tunable_loss"),
        "structural_blocker_detected": structural_blocker,
        "loss_source_hints": sorted(hint_set),
    }


def _avg(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values) / len(values))
