import asyncio
import time
import unittest

from core.decision_tape import TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class _DummyDB:
    def insert(self, table: str, row: dict) -> None:  # pragma: no cover - intentionally simple sink
        _ = (table, row)


class _ExplodingBroker:
    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"broker_should_not_be_touched_in_commit:{name}")


def _constraints(tokens: list[str]) -> dict[str, OrderConstraints]:
    return {
        token: OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=200.0,
            max_slippage_bps=200.0,
            max_book_staleness_ms=10_000,
        )
        for token in tokens
    }


def _runtime(tokens: list[str]) -> RuntimeEngine:
    books = {token: OrderBook(asset_id=token, bids={}, asks={}) for token in tokens}
    return RuntimeEngine(
        mode="OBSERVE",
        db=_DummyDB(),
        decision_tape=object(),  # type: ignore[arg-type]
        trade_tape=object(),  # type: ignore[arg-type]
        books=books,
        constraints=_constraints(tokens),
        market_meta={token: {"reference_symbol": "BTC"} for token in tokens},
        pstar_builder=PStarBuilder(max_age_ms=10_000, freeze_disagree_bps=25.0),
        policy_thresholds=PolicyThresholds(),
        constitution={"trading": {}, "policy": {}, "execution": {}},
        time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1_000_000, mono_ns=1_000_000_000),
        broker=None,
        run_epoch_ms=1_000_000,
    )


class TestRolloverLockScope(unittest.IsolatedAsyncioTestCase):
    async def test_commit_path_does_not_touch_broker(self) -> None:
        runtime = _runtime(["a1", "a2"])
        runtime.mode = "TRADE"
        runtime.broker = _ExplodingBroker()
        runtime.commit_rollover_swap(
            books={"a1": OrderBook(asset_id="a1", bids={}, asks={}), "a2": OrderBook(asset_id="a2", bids={}, asks={})},
            constraints=_constraints(["a1", "a2"]),
            market_meta={"a1": {"reference_symbol": "BTC"}, "a2": {"reference_symbol": "BTC"}},
            now_ms=2_000,
        )

    async def test_prepare_outside_lock_keeps_lock_hold_short(self) -> None:
        runtime = _runtime(["a1", "a2"])
        runtime_lock = asyncio.Lock()
        lock_waits: list[float] = []

        async def slow_prepare(next_token_ids: list[str], now_ms: int) -> dict:
            _ = (next_token_ids, now_ms)
            await asyncio.sleep(0.05)
            return {"removed_tokens": [], "cancelled_orders": 0}

        runtime.prepare_rollover = slow_prepare  # type: ignore[method-assign]

        async def ticker() -> None:
            for _ in range(30):
                t0 = time.monotonic()
                async with runtime_lock:
                    pass
                lock_waits.append(time.monotonic() - t0)
                await asyncio.sleep(0.005)

        async def rollover_once() -> None:
            await runtime.prepare_rollover(["a1", "a2"], now_ms=2_000)
            async with runtime_lock:
                runtime.commit_rollover_swap(
                    books={"a1": OrderBook(asset_id="a1", bids={}, asks={}), "a2": OrderBook(asset_id="a2", bids={}, asks={})},
                    constraints=_constraints(["a1", "a2"]),
                    market_meta={"a1": {"reference_symbol": "BTC"}, "a2": {"reference_symbol": "BTC"}},
                    now_ms=2_000,
                )

        await asyncio.gather(ticker(), rollover_once())
        self.assertTrue(lock_waits)
        waits = sorted(lock_waits)
        p95 = waits[int(0.95 * (len(waits) - 1))]
        self.assertLess(p95, 0.03)


if __name__ == "__main__":
    unittest.main()
