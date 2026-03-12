import unittest

from core_mm.sizing import get_buy_sell_amount


class TestSizing(unittest.TestCase):
    def test_buy_amount_respects_position_headroom(self) -> None:
        plan = get_buy_sell_amount(position=0, max_size=100, trade_size=50, avg_price=0.0)
        self.assertEqual(plan.buy_amount, 50.0)
        self.assertEqual(plan.sell_amount, 0.0)

    def test_buy_amount_caps_at_remaining_space(self) -> None:
        plan = get_buy_sell_amount(position=80, max_size=100, trade_size=50, avg_price=0.0)
        self.assertEqual(plan.buy_amount, 20.0)

    def test_buy_amount_stops_at_max_position(self) -> None:
        plan = get_buy_sell_amount(position=100, max_size=100, trade_size=50, avg_price=0.0)
        self.assertEqual(plan.buy_amount, 0.0)

    def test_reverse_position_blocks_new_buy(self) -> None:
        plan = get_buy_sell_amount(
            position=0,
            max_size=100,
            trade_size=50,
            avg_price=0.0,
            reverse_position=25,
            reverse_position_min_size=20,
        )
        self.assertEqual(plan.buy_amount, 0.0)

    def test_sell_amount_uses_position_and_avg_cost(self) -> None:
        plan = get_buy_sell_amount(position=40, max_size=100, trade_size=50, avg_price=0.55)
        self.assertEqual(plan.sell_amount, 40.0)


if __name__ == "__main__":
    unittest.main()
