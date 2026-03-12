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


if __name__ == "__main__":
    unittest.main()
