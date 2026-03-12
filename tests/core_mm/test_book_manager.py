import unittest

from core_mm.book_manager import BookManager


class TestBookManager(unittest.TestCase):
    def test_snapshot_extracts_bbo(self) -> None:
        manager = BookManager()
        applied = manager.process_message(
            {
                "asset_id": "token-1",
                "bids": [[0.74, 100], [0.73, 50]],
                "asks": [[0.76, 100], [0.77, 50]],
            },
            recv_wall_ms=1_000,
        )
        self.assertEqual(applied, 1)
        book = manager.get_book("token-1")
        self.assertIsNotNone(book)
        assert book is not None
        self.assertEqual(book.best_bid, 0.74)
        self.assertEqual(book.best_ask, 0.76)
        self.assertEqual(book.mid_price, 0.75)
        self.assertEqual(book.bids[0], (0.74, 100.0))
        self.assertEqual(book.asks[0], (0.76, 100.0))

    def test_incremental_insert_and_remove(self) -> None:
        manager = BookManager()
        manager.apply_snapshot("token-1", bids=[(0.74, 100)], asks=[(0.76, 100)], ts_ms=1_000)
        applied = manager.process_message(
            {
                "asset_id": "token-1",
                "event_type": "price_change",
                "price_changes": [
                    {"side": "buy", "price": 0.75, "size": 60},
                    {"side": "sell", "price": 0.76, "size": 0},
                ],
            },
            recv_wall_ms=2_000,
        )
        self.assertEqual(applied, 2)
        book = manager.get_book("token-1")
        assert book is not None
        self.assertEqual(book.best_bid, 0.75)
        self.assertEqual(book.best_bid_size, 60.0)
        self.assertEqual(book.best_ask, None)

    def test_stale_detection(self) -> None:
        manager = BookManager(stale_after_ms=30_000)
        manager.apply_snapshot("token-1", bids=[(0.74, 100)], asks=[(0.76, 100)], ts_ms=1_000)
        self.assertFalse(manager.is_stale("token-1", now_ms=30_999))
        self.assertTrue(manager.is_stale("token-1", now_ms=31_001))


if __name__ == "__main__":
    unittest.main()
