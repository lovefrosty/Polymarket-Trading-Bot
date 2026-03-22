import unittest

from core.order_book import OrderBook


class TestOrderBookMonotonic(unittest.TestCase):
    def test_out_of_order_recv_mono_does_not_mutate(self) -> None:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        result = book.apply_snapshot(
            bids=[(0.49, 1.0)],
            asks=[(0.51, 1.0)],
            event_ts_ms=100,
            recv_mono_ns=100,
        )
        self.assertFalse(result.recv_out_of_order)
        self.assertEqual(book.last_event_ts_ms, 100)
        self.assertEqual(book.last_recv_mono_ns, 100)
        bids_before = dict(book.bids)
        asks_before = dict(book.asks)
        best_bid_before = book.best_bid()
        best_ask_before = book.best_ask()

        result = book.apply_snapshot(
            bids=[(0.40, 10.0)],
            asks=[(0.60, 10.0)],
            event_ts_ms=90,
            recv_mono_ns=90,
        )
        self.assertTrue(result.recv_out_of_order)
        self.assertEqual(book.bids, bids_before)
        self.assertEqual(book.asks, asks_before)
        self.assertEqual(book.best_bid(), best_bid_before)
        self.assertEqual(book.best_ask(), best_ask_before)
        self.assertEqual(book.last_event_ts_ms, 100)
        self.assertEqual(book.last_recv_mono_ns, 100)

        result = book.apply_update(
            side="buy",
            price=0.49,
            size=2.0,
            event_ts_ms=110,
            recv_mono_ns=110,
        )
        self.assertFalse(result.recv_out_of_order)
        self.assertEqual(book.bids.get(0.49), 2.0)

        result = book.apply_update(
            side="buy",
            price=0.49,
            size=0.0,
            event_ts_ms=120,
            recv_mono_ns=105,
        )
        self.assertTrue(result.recv_out_of_order)
        self.assertEqual(book.bids.get(0.49), 2.0)

    def test_event_time_regression_is_warning_only(self) -> None:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        result = book.apply_snapshot(
            bids=[(0.49, 1.0)],
            asks=[(0.51, 1.0)],
            event_ts_ms=200,
            recv_mono_ns=100,
        )
        self.assertFalse(result.event_time_regressed)

        result = book.apply_update(
            side="buy",
            price=0.49,
            size=3.0,
            event_ts_ms=150,
            recv_mono_ns=110,
        )
        self.assertTrue(result.event_time_regressed)
        self.assertEqual(book.bids.get(0.49), 3.0)


if __name__ == "__main__":
    unittest.main()
