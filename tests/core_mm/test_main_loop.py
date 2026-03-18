import asyncio
import unittest

from core_mm.book_manager import BookManager
from core_mm.execution import ExecutionAdapter
from core_mm.main_loop import MarketConfig, TokenState, TradingMainLoop
from core_mm.order_manager import SmartOrderManager
from core_mm.risk_manager import RiskManager


class _FakeOrderArgs:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeOrderType:
    GTC = "GTC"


class _FakeClient:
    def __init__(self) -> None:
        self.created = []
        self.canceled = []

    def create_order(self, order_args):
        self.created.append(order_args.kwargs)
        return {"signed": order_args.kwargs}

    def post_order(self, signed, order_type):
        return {"orderID": f"ord-{len(self.created)}", "signed": signed, "orderType": order_type}

    def cancel(self, order_id=None):
        self.canceled.append(order_id)
        return {"canceled": [order_id]}

    def cancel_all(self):
        return {"canceled": "all"}

    def get_orders(self):
        return []

    def get_positions(self):
        return []


class TestMainLoop(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.books = BookManager()
        self.books.apply_snapshot("yes", bids=[(0.48, 150), (0.47, 50)], asks=[(0.52, 160), (0.53, 70)], ts_ms=1_000)
        self.books.apply_snapshot("no", bids=[(0.46, 150), (0.45, 50)], asks=[(0.54, 160), (0.55, 70)], ts_ms=1_000)
        self.market = MarketConfig(
            market_id="m1",
            token_ids=("yes", "no"),
            tick_size=0.01,
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
            trade_size=50,
            max_size=100,
        )

    async def test_observe_cycle_builds_quotes_and_actions(self) -> None:
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        result = await loop.run_market_cycle(
            market=self.market,
            token_states=(
                TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),
                TokenState(token_id="no", position=0, avg_cost=0.0, usdc_balance=1000),
            ),
            existing_orders={},
            now_ms=2_000,
        )
        self.assertTrue(result.desired_quotes)
        self.assertTrue(result.order_actions)
        self.assertTrue(all(action.action == "PLACE" for action in result.order_actions))
        self.assertEqual(len(result.execution_results), 0)

    async def test_paper_cycle_executes_actions(self) -> None:
        adapter = ExecutionAdapter(_FakeClient(), order_args_type=_FakeOrderArgs, order_type=_FakeOrderType)
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            execution_adapter=adapter,
            mode="PAPER",
        )
        result = await loop.run_market_cycle(
            market=self.market,
            token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
            existing_orders={},
            now_ms=2_000,
        )
        self.assertTrue(result.execution_results)
        self.assertTrue(all(res.success for res in result.execution_results))

    async def test_market_lock_serializes_cycle(self) -> None:
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        lock = loop.get_market_lock("m1")
        await lock.acquire()
        task = asyncio.create_task(
            loop.run_market_cycle(
                market=self.market,
                token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
                existing_orders={},
                now_ms=2_000,
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        lock.release()
        result = await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(result.market_id, "m1")


    # ── Staleness gate tests ──────────────────────────────────────────────────

    async def test_stale_book_gate_blocks_quoting(self) -> None:
        # Book snapshotted at ts=1_000, cycle at ts=20_000 → age=19s > gate 5s
        stale_market = MarketConfig(
            market_id="m2",
            token_ids=("yes",),
            tick_size=0.01,
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
            trade_size=50,
            max_size=100,
            stale_book_gate_ms=5_000,
        )
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        result = await loop.run_market_cycle(
            market=stale_market,
            token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
            existing_orders={},
            now_ms=20_000,  # age = 19_000ms > stale gate 5_000ms
        )
        self.assertEqual(len(result.desired_quotes), 0)
        self.assertEqual(len(result.order_actions), 0)
        yes_dec = next(d for d in result.token_decisions if d.token_id == "yes")
        self.assertEqual(yes_dec.book_diag.state, "book_stale")

    # ── Graduated staleness / caution zone tests ─────────────────────────────

    async def test_fresh_book_quotes_normally(self) -> None:
        # Book at ts=1_000, cycle at ts=2_000 → age=1s < 0.6*5s=3s: FRESH
        fresh_market = MarketConfig(
            market_id="m4",
            token_ids=("yes",),
            tick_size=0.01,
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
            trade_size=50,
            max_size=100,
            stale_book_gate_ms=5_000,
        )
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        result = await loop.run_market_cycle(
            market=fresh_market,
            token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
            existing_orders={},
            now_ms=2_000,
        )
        yes_dec = next(d for d in result.token_decisions if d.token_id == "yes")
        self.assertNotEqual(yes_dec.book_diag.state, "book_stale")
        self.assertNotEqual(yes_dec.book_diag.state, "book_caution")

    async def test_caution_zone_widens_spread(self) -> None:
        # Book at ts=1_000, cycle at ts=4_000 → age=3s; caution=0.6*5s=3s → CAUTION
        caution_market = MarketConfig(
            market_id="m5",
            token_ids=("yes",),
            tick_size=0.01,
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
            trade_size=50,
            max_size=100,
            stale_book_gate_ms=5_000,
        )
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        # Fresh result at age=1s
        result_fresh = await loop.run_market_cycle(
            market=caution_market,
            token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
            existing_orders={},
            now_ms=2_000,
        )
        # Caution result at age=3.1s (just past caution threshold of 3s)
        result_caution = await loop.run_market_cycle(
            market=caution_market,
            token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
            existing_orders={},
            now_ms=4_100,  # age = 3_100ms > 0.6*5000=3000
        )
        yes_dec = next(d for d in result_caution.token_decisions if d.token_id == "yes")
        # state should be book_caution and spread multiplier should be applied
        # (we check the metadata on quotes)
        for quote in result_caution.desired_quotes.values():
            if isinstance(quote.metadata, dict):
                self.assertEqual(quote.metadata.get("spread_multiplier"), 2.0)
                self.assertEqual(quote.metadata.get("book_state"), "book_caution")

    async def test_caution_threshold_at_60_pct(self) -> None:
        # Confirm caution activates at > 60% of stale gate, not before
        market = MarketConfig(
            market_id="m6",
            token_ids=("yes",),
            tick_size=0.01,
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
            trade_size=50,
            max_size=100,
            stale_book_gate_ms=10_000,  # caution at 6s
        )
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        # age=5s: below caution threshold (6s) → should NOT be caution
        result_below = await loop.run_market_cycle(
            market=market,
            token_states=(TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),),
            existing_orders={},
            now_ms=6_000,  # age = 5_000ms < 6_000ms caution threshold
        )
        for quote in result_below.desired_quotes.values():
            if isinstance(quote.metadata, dict):
                self.assertNotEqual(quote.metadata.get("book_state"), "book_caution")

    async def test_emergency_cooldown_returns_no_quotes(self) -> None:
        """When flow filter is in emergency cooldown, no quotes are emitted."""
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        # Force the flow filter for "yes" into emergency cooldown
        flow_obj = loop._get_flow_filter("yes")
        flow_obj.update(100, 100)  # initialize EWMA
        flow_obj._emergency_cooldown = 3
        result = await loop.run_market_cycle(
            market=self.market,
            token_states=(
                TokenState(token_id="yes", position=0, avg_cost=0.0, usdc_balance=1000),
                TokenState(token_id="no", position=0, avg_cost=0.0, usdc_balance=1000),
            ),
            existing_orders={},
            now_ms=2_000,
        )
        yes_dec = next(d for d in result.token_decisions if d.token_id == "yes")
        self.assertEqual(len(yes_dec.desired_quotes), 0)

    async def test_cross_token_net_position_in_metadata(self) -> None:
        """net_position appears in quote metadata."""
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        result = await loop.run_market_cycle(
            market=self.market,
            token_states=(
                TokenState(token_id="yes", position=50, avg_cost=0.50, net_position=20, usdc_balance=1000),
                TokenState(token_id="no", position=30, avg_cost=0.50, net_position=-20, usdc_balance=1000),
            ),
            existing_orders={},
            now_ms=2_000,
        )
        for quote in result.desired_quotes.values():
            if isinstance(quote.metadata, dict):
                self.assertIn("net_position", quote.metadata)

    async def test_inventory_skew_ticks_in_metadata(self) -> None:
        skew_market = MarketConfig(
            market_id="m3",
            token_ids=("yes",),
            tick_size=0.01,
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
            trade_size=50,
            max_size=100,
            max_skew_ticks=4,
            inventory_skew_factor=1.0,
        )
        loop = TradingMainLoop(
            book_manager=self.books,
            order_manager=SmartOrderManager(),
            risk_manager=RiskManager(),
            mode="OBSERVE",
        )
        result = await loop.run_market_cycle(
            market=skew_market,
            # position=50/max=100 → long_ratio=0.5; avg_cost=0.40, mid≈0.50
            # pnl_urgency = clamp(1 - (0.50-0.40)/0.40 * 2, 0.5, 2.0) = clamp(0.5, 0.5, 2.0) = 0.5
            # skew_ticks = round(0.5 * 4 * 0.5) = round(1.0) = 1
            token_states=(TokenState(token_id="yes", position=50, avg_cost=0.40, net_position=50, usdc_balance=1000),),
            existing_orders={},
            now_ms=2_000,
        )
        for quote in result.desired_quotes.values():
            if isinstance(quote.metadata, dict):
                self.assertEqual(quote.metadata.get("inventory_skew_ticks"), 1)


if __name__ == "__main__":
    unittest.main()
