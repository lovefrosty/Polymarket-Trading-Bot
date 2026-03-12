import unittest

from core_mm.book_metrics import MeaningfulBBO
from core_mm.quote_engine import get_order_prices, resolve_tick_size


class TestQuoteEngine(unittest.TestCase):
    def test_thick_book_improves_by_one_tick(self) -> None:
        plan = get_order_prices(
            MeaningfulBBO(
                best_bid=0.50,
                best_bid_size=500.0,
                second_bid=0.49,
                best_ask=0.54,
                best_ask_size=500.0,
                second_ask=0.55,
                top_bid=0.50,
                top_ask=0.54,
                bid_sum_within_n_percent=500.0,
                ask_sum_within_n_percent=500.0,
                min_size_used=100.0,
            ),
            min_size=100,
            tick_size=0.01,
        )
        self.assertEqual(plan.bid_price, 0.51)
        self.assertEqual(plan.ask_price, 0.53)
        self.assertEqual(plan.bid_mode, "improve")
        self.assertEqual(plan.ask_mode, "improve")

    def test_thin_book_joins_existing_level(self) -> None:
        plan = get_order_prices(
            MeaningfulBBO(
                best_bid=0.50,
                best_bid_size=30.0,
                second_bid=0.49,
                best_ask=0.56,
                best_ask_size=30.0,
                second_ask=0.57,
                top_bid=0.50,
                top_ask=0.56,
                bid_sum_within_n_percent=30.0,
                ask_sum_within_n_percent=30.0,
                min_size_used=20.0,
            ),
            min_size=20,
            tick_size=0.01,
        )
        self.assertEqual(plan.bid_price, 0.5)
        self.assertEqual(plan.ask_price, 0.56)
        self.assertEqual(plan.bid_mode, "join")
        self.assertEqual(plan.ask_mode, "join")

    def test_crossing_falls_back_to_top_levels(self) -> None:
        plan = get_order_prices(
            MeaningfulBBO(
                best_bid=0.50,
                best_bid_size=500.0,
                second_bid=0.49,
                best_ask=0.51,
                best_ask_size=500.0,
                second_ask=0.52,
                top_bid=0.50,
                top_ask=0.51,
                bid_sum_within_n_percent=500.0,
                ask_sum_within_n_percent=500.0,
                min_size_used=100.0,
            ),
            min_size=100,
            tick_size=0.01,
        )
        self.assertEqual(plan.bid_price, 0.5)
        self.assertEqual(plan.bid_mode, "fallback_top_bid")
        self.assertEqual(plan.ask_price, 0.51)
        self.assertEqual(plan.ask_mode, "fallback_top_ask")

    def test_ask_never_below_average_cost(self) -> None:
        plan = get_order_prices(
            MeaningfulBBO(
                best_bid=0.48,
                best_bid_size=200.0,
                second_bid=0.47,
                best_ask=0.50,
                best_ask_size=300.0,
                second_ask=0.51,
                top_bid=0.48,
                top_ask=0.50,
                bid_sum_within_n_percent=200.0,
                ask_sum_within_n_percent=300.0,
                min_size_used=100.0,
            ),
            avg_cost=0.515,
            min_size=100,
            tick_size=0.01,
        )
        self.assertGreaterEqual(plan.ask_price or 0.0, 0.515)

    def test_dynamic_tick_size_near_boundaries(self) -> None:
        self.assertEqual(resolve_tick_size(0.97), 0.001)
        self.assertEqual(resolve_tick_size(0.995), 0.0001)
        self.assertEqual(resolve_tick_size(0.50), 0.01)


if __name__ == "__main__":
    unittest.main()
