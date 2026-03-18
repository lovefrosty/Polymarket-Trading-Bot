import unittest

from core_mm.book_manager import BookView

from core_mm.book_metrics import classify_book_state, find_meaningful_bbo


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

    def test_low_price_token_depth_uses_absolute_floor(self) -> None:
        # Token at $0.05/$0.06: mid=0.055, within_pct=0.06 → percentage window=0.0033
        # Without the floor this would give bid_sum=0, ask_sum=0 → flow blocks all trading.
        # With the 0.03 absolute floor: window=[0.025, 0.085] → captures both levels.
        metrics = find_meaningful_bbo(
            bids=[(0.05, 200), (0.04, 150)],
            asks=[(0.06, 90), (0.07, 80)],
            min_size=10,
            fallback_size=2,
            within_pct=0.06,
        )
        assert metrics is not None
        self.assertGreater(metrics.bid_sum_within_n_percent, 0.0)
        self.assertGreater(metrics.ask_sum_within_n_percent, 0.0)

    def test_book_diagnostic_absent(self) -> None:
        diag = classify_book_state(None, min_size=100, fallback_size=20, now_ms=2_000)
        self.assertEqual(diag.state, "book_absent")

    def test_book_diagnostic_empty(self) -> None:
        book = BookView(
            token_id="yes",
            bids=((0.49, 10.0),),
            asks=(),
            best_bid=0.49,
            best_ask=None,
            best_bid_size=10.0,
            best_ask_size=0.0,
            mid_price=None,
            last_update_ms=1_000,
        )
        diag = classify_book_state(book, min_size=100, fallback_size=20, now_ms=2_000)
        self.assertEqual(diag.state, "book_empty")
        self.assertEqual(diag.book_age_ms, 1000)

    def test_book_diagnostic_below_meaningful_size(self) -> None:
        book = BookView(
            token_id="yes",
            bids=((0.49, 5.0), (0.48, 8.0)),
            asks=((0.51, 6.0), (0.52, 10.0)),
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=5.0,
            best_ask_size=6.0,
            mid_price=0.50,
            last_update_ms=1_000,
        )
        diag = classify_book_state(book, min_size=100, fallback_size=20, now_ms=2_000)
        self.assertEqual(diag.state, "book_below_meaningful_size")

    def test_book_diagnostic_ok(self) -> None:
        book = BookView(
            token_id="yes",
            bids=((0.49, 150.0), (0.48, 20.0)),
            asks=((0.51, 160.0), (0.52, 25.0)),
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=150.0,
            best_ask_size=160.0,
            mid_price=0.50,
            last_update_ms=1_000,
        )
        diag = classify_book_state(book, min_size=100, fallback_size=20, now_ms=2_000)
        self.assertEqual(diag.state, "book_ok")


if __name__ == "__main__":
    unittest.main()
