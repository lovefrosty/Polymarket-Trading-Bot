import unittest

from core.order_book import OrderBook


class TestDepthSlippage(unittest.TestCase):
    def test_depth_and_slippage(self) -> None:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        book.apply_snapshot(
            bids=[(0.49, 2.0), (0.48, 3.0)],
            asks=[(0.51, 2.0), (0.52, 3.0)],
            event_ts_ms=100,
            recv_mono_ns=1000,
        )
        depth = book.depth_at_qty("buy", 4.0)
        self.assertEqual(depth, 4.0)
        slippage = book.expected_slippage_to_fill("buy", 4.0)
        self.assertAlmostEqual(slippage, 300.0, places=3)

    def test_depth_within_ticks(self) -> None:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        book.apply_snapshot(
            bids=[(0.49, 2.0), (0.48, 3.0), (0.47, 4.0)],
            asks=[(0.51, 2.0), (0.52, 3.0), (0.53, 4.0)],
            event_ts_ms=100,
            recv_mono_ns=1000,
        )
        depth_bid = book.depth_within_ticks_bid(ticks=1, tick_size=0.01)
        depth_ask = book.depth_within_ticks_ask(ticks=1, tick_size=0.01)
        self.assertEqual(depth_bid, 5.0)
        self.assertEqual(depth_ask, 5.0)


if __name__ == "__main__":
    unittest.main()
