import unittest

from src.book.order_book import OrderBook
from src.risk.gates import gate_book


class TestBookDepth(unittest.TestCase):
    def test_depth_metrics_use_ladder(self) -> None:
        book = OrderBook()
        book.set_snapshot(
            bids=[(0.49, 1.0), (0.48, 2.0)],
            asks=[(0.51, 1.0), (0.52, 2.0)],
            ts=100,
        )
        metrics = book.depth_metrics("buy", qty=2.0)
        self.assertTrue(metrics.depth_ok)
        self.assertAlmostEqual(metrics.filled_qty, 2.0)
        self.assertAlmostEqual(metrics.avg_price, (0.51 * 1.0 + 0.52 * 1.0) / 2.0)

    def test_gate_rejects_insufficient_depth(self) -> None:
        book = OrderBook()
        book.set_snapshot(
            bids=[(0.49, 1.0)],
            asks=[(0.51, 1.0)],
            ts=100,
        )
        gate = gate_book(
            book,
            decision_ts=150,
            side="buy",
            qty=5.0,
            max_age_ms=1000,
            max_spread_bps=500.0,
            max_slippage_bps=500.0,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.reason, "insufficient_depth")

    def test_out_of_order_update_does_not_rewind_timestamp(self) -> None:
        book = OrderBook()
        book.set_snapshot(
            bids=[(0.49, 1.0)],
            asks=[(0.51, 1.0)],
            ts=100,
        )
        book.update_l2([], [], ts=90)
        self.assertEqual(book.last_update_ts, 100)


if __name__ == "__main__":
    unittest.main()
