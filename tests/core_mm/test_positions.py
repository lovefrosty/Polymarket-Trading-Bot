import unittest

from core_mm.positions import PositionTracker


class TestPositionTracker(unittest.TestCase):
    def test_apply_fill_updates_avg_price(self) -> None:
        tracker = PositionTracker()
        tracker.apply_fill(token_id="yes", side="buy", size=100, price=0.40)
        tracker.apply_fill(token_id="yes", side="buy", size=100, price=0.60)
        pos = tracker.get_position("yes")
        self.assertEqual(pos.size, 200.0)
        self.assertAlmostEqual(pos.avg_price, 0.50)

    def test_merge_positions_frees_capital(self) -> None:
        tracker = PositionTracker()
        tracker.set_position("yes", size=100, avg_price=0.42)
        tracker.set_position("no", size=80, avg_price=0.58)
        merge = tracker.merge_positions("yes", "no", min_merge_size=20)
        self.assertTrue(merge.executed)
        self.assertEqual(merge.amount_to_merge, 80.0)
        self.assertEqual(merge.freed_usdc, 80.0)
        self.assertEqual(tracker.get_position("yes").size, 20.0)
        self.assertEqual(tracker.get_position("no").size, 0.0)

    def test_small_overlap_does_not_merge(self) -> None:
        tracker = PositionTracker()
        tracker.set_position("yes", size=15, avg_price=0.42)
        tracker.set_position("no", size=10, avg_price=0.58)
        merge = tracker.merge_positions("yes", "no", min_merge_size=20)
        self.assertFalse(merge.executed)


if __name__ == "__main__":
    unittest.main()
