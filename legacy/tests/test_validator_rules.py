import time
import unittest

from core.order_book import OrderBook
from core.validators import HypotheticalOrder, OrderConstraints, SimBalances, validate_hypothetical_order


class TestValidatorRules(unittest.TestCase):
    def setUp(self) -> None:
        self.book = OrderBook(asset_id="asset", bids={}, asks={})
        self.book.apply_snapshot(
            bids=[(0.49, 10.0)],
            asks=[(0.51, 10.0)],
            event_ts_ms=100,
            recv_mono_ns=1000,
        )
        self.constraints = OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=1000.0,
            max_slippage_bps=1000.0,
            max_book_staleness_ms=2000,
        )
        self.balances = SimBalances(usd=10.0, tokens={}, default_token_balance=1.0)

    def test_tick_violation(self) -> None:
        order = HypotheticalOrder(
            asset_id="asset",
            side="BUY",
            price=0.015,
            size=1.0,
            t_decision_wall="",
            t_decision_mono_ns=0,
            t_decision_event_ts_ms=0,
        )
        ok, reasons, _ = validate_hypothetical_order(
            order,
            self.book,
            self.constraints,
            self.balances,
            now_mono_ns=time.monotonic_ns(),
            execution_mode="MAKER_LIMIT",
        )
        self.assertFalse(ok)
        self.assertIn("MIN_TICK", reasons)

    def test_min_size_violation(self) -> None:
        order = HypotheticalOrder(
            asset_id="asset",
            side="BUY",
            price=0.5,
            size=0.5,
            t_decision_wall="",
            t_decision_mono_ns=0,
            t_decision_event_ts_ms=0,
        )
        ok, reasons, _ = validate_hypothetical_order(
            order, self.book, self.constraints, self.balances, now_mono_ns=time.monotonic_ns()
        )
        self.assertFalse(ok)
        self.assertIn("MIN_SIZE", reasons)

    def test_price_bounds_violation(self) -> None:
        self.book.apply_snapshot(
            bids=[(0.49, 10.0)],
            asks=[(1.2, 10.0)],
            event_ts_ms=100,
            recv_mono_ns=1000,
        )
        order = HypotheticalOrder(
            asset_id="asset",
            side="BUY",
            price=1.5,
            size=1.0,
            t_decision_wall="",
            t_decision_mono_ns=0,
            t_decision_event_ts_ms=0,
        )
        ok, reasons, _ = validate_hypothetical_order(
            order, self.book, self.constraints, self.balances, now_mono_ns=time.monotonic_ns()
        )
        self.assertFalse(ok)
        self.assertIn("PRICE_BOUNDS", reasons)

    def test_book_stale_violation(self) -> None:
        order = HypotheticalOrder(
            asset_id="asset",
            side="BUY",
            price=0.5,
            size=1.0,
            t_decision_wall="",
            t_decision_mono_ns=0,
            t_decision_event_ts_ms=0,
        )
        stale_now = self.book.last_recv_mono_ns + 3_000_000_000
        ok, reasons, _ = validate_hypothetical_order(
            order, self.book, self.constraints, self.balances, now_mono_ns=stale_now
        )
        self.assertFalse(ok)
        self.assertIn("BOOK_STALE", reasons)

    def test_no_execution_price_rejects(self) -> None:
        order = HypotheticalOrder(
            asset_id="asset",
            side="BUY",
            price=0.5,
            size=100.0,
            t_decision_wall="",
            t_decision_mono_ns=0,
            t_decision_event_ts_ms=0,
        )
        ok, reasons, _ = validate_hypothetical_order(
            order, self.book, self.constraints, self.balances, now_mono_ns=time.monotonic_ns()
        )
        self.assertFalse(ok)
        self.assertIn("NO_EXECUTION_PRICE", reasons)


if __name__ == "__main__":
    unittest.main()
