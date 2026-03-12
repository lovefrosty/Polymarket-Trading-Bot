import unittest

from core_mm.book_metrics import find_meaningful_bbo


class TestBookMetrics(unittest.TestCase):
    def test_meaningful_bbo_skips_dust(self) -> None:
        metrics = find_meaningful_bbo(
            bids=[(0.80, 5), (0.78, 200), (0.77, 150)],
            asks=[(0.82, 5), (0.84, 220), (0.85, 140)],
            min_size=100,
            fallback_size=20,
        )
        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics.best_bid, 0.78)
        self.assertEqual(metrics.best_ask, 0.84)
        self.assertEqual(metrics.second_bid, 0.77)
        self.assertEqual(metrics.second_ask, 0.85)

    def test_returns_none_when_book_is_too_thin(self) -> None:
        metrics = find_meaningful_bbo(
            bids=[(0.80, 5), (0.79, 10)],
            asks=[(0.82, 8), (0.83, 11)],
            min_size=100,
            fallback_size=20,
        )
        self.assertIsNone(metrics)

    def test_volume_within_percent_is_computed(self) -> None:
        metrics = find_meaningful_bbo(
            bids=[(0.50, 120), (0.49, 30), (0.47, 50)],
            asks=[(0.52, 125), (0.53, 40), (0.56, 60)],
            min_size=100,
            fallback_size=20,
            within_pct=0.05,
        )
        assert metrics is not None
        self.assertAlmostEqual(metrics.bid_sum_within_n_percent, 150.0)
        self.assertAlmostEqual(metrics.ask_sum_within_n_percent, 165.0)


if __name__ == "__main__":
    unittest.main()
