import unittest

from core_mm.book_metrics import MeaningfulBBO
from core_mm.quote_engine import compute_inventory_skew_ticks, get_order_prices, resolve_tick_size


def _wide_bbo(**kwargs) -> MeaningfulBBO:
    """BBO with 20-tick spread so asymmetric skew tests don't hit the inversion guard."""
    defaults = dict(
        best_bid=0.40,
        best_bid_size=500.0,
        second_bid=0.39,
        best_ask=0.60,
        best_ask_size=500.0,
        second_ask=0.61,
        top_bid=0.40,
        top_ask=0.60,
        bid_sum_within_n_percent=500.0,
        ask_sum_within_n_percent=500.0,
        min_size_used=100.0,
    )
    defaults.update(kwargs)
    return MeaningfulBBO(**defaults)


def _bbo(**kwargs) -> MeaningfulBBO:
    defaults = dict(
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
    )
    defaults.update(kwargs)
    return MeaningfulBBO(**defaults)


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

    # ── Inventory skew tests ──────────────────────────────────────────────────

    def test_compute_skew_ticks_flat(self) -> None:
        self.assertEqual(compute_inventory_skew_ticks(0, max_size=100, max_skew_ticks=3), 0)

    def test_compute_skew_ticks_at_max(self) -> None:
        self.assertEqual(compute_inventory_skew_ticks(100, max_size=100, max_skew_ticks=3), 3)

    def test_compute_skew_ticks_at_half(self) -> None:
        self.assertEqual(compute_inventory_skew_ticks(50, max_size=100, max_skew_ticks=4), 2)

    def test_compute_skew_ticks_clamped(self) -> None:
        # Position beyond max_size is clamped to max_skew_ticks
        self.assertEqual(compute_inventory_skew_ticks(200, max_size=100, max_skew_ticks=3), 3)

    def test_inventory_skew_shifts_prices_down_when_long(self) -> None:
        # 2-tick long skew (asymmetric): bid_skew=1 tick, ask_skew=3 ticks
        # Use wide BBO so there's no inversion with these offsets.
        base = get_order_prices(_wide_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=0)
        skewed = get_order_prices(_wide_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=2)
        self.assertAlmostEqual((base.bid_price or 0) - (skewed.bid_price or 0), 0.01, places=6)
        self.assertAlmostEqual((base.ask_price or 0) - (skewed.ask_price or 0), 0.03, places=6)

    def test_inventory_skew_zero_is_no_op(self) -> None:
        base = get_order_prices(_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=0)
        same = get_order_prices(_bbo(), min_size=100, tick_size=0.01)
        self.assertEqual(base.bid_price, same.bid_price)
        self.assertEqual(base.ask_price, same.ask_price)

    # ── P&L urgency tests ─────────────────────────────────────────────────────

    def test_pnl_urgency_underwater_increases_skew(self) -> None:
        # Underwater (avg_cost=0.60, mid=0.50 → pct=-16.7% → urgency=1.33)
        skew_under = compute_inventory_skew_ticks(
            50, max_size=100, max_skew_ticks=4, avg_cost=0.60, mid_price=0.50
        )
        # Breakeven (avg_cost=mid=0.50 → urgency=1.0)
        skew_even = compute_inventory_skew_ticks(
            50, max_size=100, max_skew_ticks=4, avg_cost=0.50, mid_price=0.50
        )
        self.assertGreater(skew_under, skew_even)

    def test_pnl_urgency_profitable_decreases_skew(self) -> None:
        # In profit (avg_cost=0.40, mid=0.50 → pct=+25% → urgency=0.5)
        skew_profit = compute_inventory_skew_ticks(
            50, max_size=100, max_skew_ticks=4, avg_cost=0.40, mid_price=0.50
        )
        skew_even = compute_inventory_skew_ticks(
            50, max_size=100, max_skew_ticks=4, avg_cost=0.50, mid_price=0.50
        )
        self.assertLess(skew_profit, skew_even)

    def test_pnl_urgency_zero_position_returns_zero(self) -> None:
        skew = compute_inventory_skew_ticks(
            0, max_size=100, max_skew_ticks=4, avg_cost=0.60, mid_price=0.40
        )
        self.assertEqual(skew, 0)

    def test_pnl_urgency_zero_avg_cost_no_crash(self) -> None:
        # avg_cost=0 → pnl_urgency=1.0, normal skew
        skew = compute_inventory_skew_ticks(
            50, max_size=100, max_skew_ticks=4, avg_cost=0.0, mid_price=0.50
        )
        self.assertEqual(skew, compute_inventory_skew_ticks(50, max_size=100, max_skew_ticks=4))

    def test_pnl_urgency_clamps_at_extremes(self) -> None:
        # Extreme loss: avg_cost=0.90, mid=0.10 → urgency should clamp to 2.0
        skew_extreme = compute_inventory_skew_ticks(
            50, max_size=100, max_skew_ticks=4, avg_cost=0.90, mid_price=0.10
        )
        # urgency clamped to 2.0 → skew = round(0.5 * 4 * 2.0) = 4
        self.assertLessEqual(skew_extreme, 4 * 2)  # can't exceed 2x max_skew_ticks

    # ── Asymmetric skew tests ─────────────────────────────────────────────────

    def test_asymmetric_skew_long_ask_drops_more_than_bid(self) -> None:
        # skew_ticks=2 (long): bid_skew=1 tick, ask_skew=3 ticks → ask drops more than bid
        base = get_order_prices(_wide_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=0)
        skewed = get_order_prices(_wide_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=2)
        bid_drop = (base.bid_price or 0) - (skewed.bid_price or 0)
        ask_drop = (base.ask_price or 0) - (skewed.ask_price or 0)
        self.assertGreater(ask_drop, bid_drop)

    def test_asymmetric_skew_short_bid_rises_more_than_ask(self) -> None:
        # skew_ticks=-2 (short): bid_skew=3 ticks, ask_skew=1 tick → bid rises more than ask
        base = get_order_prices(_wide_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=0)
        skewed = get_order_prices(_wide_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=-2)
        bid_change = (skewed.bid_price or 0) - (base.bid_price or 0)
        ask_change = (skewed.ask_price or 0) - (base.ask_price or 0)
        self.assertGreater(bid_change, ask_change)

    def test_asymmetric_skew_flat_is_no_op(self) -> None:
        base = get_order_prices(_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=0)
        same = get_order_prices(_bbo(), min_size=100, tick_size=0.01)
        self.assertEqual(base.bid_price, same.bid_price)
        self.assertEqual(base.ask_price, same.ask_price)

    def test_asymmetric_skew_no_inversion_extreme(self) -> None:
        # Even with extreme skew_ticks=6, spread never inverts
        plan = get_order_prices(_bbo(), min_size=100, tick_size=0.01, inventory_skew_ticks=6)
        if plan.bid_price is not None and plan.ask_price is not None:
            self.assertLess(plan.bid_price, plan.ask_price)

    # ── spread_multiplier tests ───────────────────────────────────────────────

    def test_spread_multiplier_widens_both_sides(self) -> None:
        base = get_order_prices(_bbo(), min_size=100, tick_size=0.01)
        wide = get_order_prices(_bbo(), min_size=100, tick_size=0.01, spread_multiplier=2.0)
        # Bid should be lower, ask should be higher
        self.assertLess(wide.bid_price or 0, base.bid_price or 0)
        self.assertGreater(wide.ask_price or 0, base.ask_price or 0)

    def test_spread_multiplier_one_is_no_op(self) -> None:
        base = get_order_prices(_bbo(), min_size=100, tick_size=0.01)
        same = get_order_prices(_bbo(), min_size=100, tick_size=0.01, spread_multiplier=1.0)
        self.assertEqual(base.bid_price, same.bid_price)
        self.assertEqual(base.ask_price, same.ask_price)


if __name__ == "__main__":
    unittest.main()
