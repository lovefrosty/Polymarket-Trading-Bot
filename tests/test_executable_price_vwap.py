import unittest

from core.order_book import OrderBook


class TestExecutablePriceVWAP(unittest.TestCase):
    def test_vwap_buy_sell(self) -> None:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        book.apply_snapshot(
            bids=[(0.49, 2.0), (0.48, 2.0)],
            asks=[(0.51, 1.0), (0.52, 3.0)],
            event_ts_ms=100,
            recv_mono_ns=1000,
        )
        vwap_buy = book.vwap_to_fill("buy", 3.0)
        self.assertAlmostEqual(vwap_buy or 0.0, (0.51 * 1.0 + 0.52 * 2.0) / 3.0)
        vwap_sell = book.vwap_to_fill("sell", 3.0)
        self.assertAlmostEqual(vwap_sell or 0.0, (0.49 * 2.0 + 0.48 * 1.0) / 3.0)

    def test_vwap_insufficient_depth(self) -> None:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        book.apply_snapshot(
            bids=[(0.49, 1.0)],
            asks=[(0.51, 1.0)],
            event_ts_ms=100,
            recv_mono_ns=1000,
        )
        self.assertIsNone(book.vwap_to_fill("buy", 5.0))
        self.assertIsNone(book.vwap_to_fill("sell", 5.0))


if __name__ == "__main__":
    unittest.main()
