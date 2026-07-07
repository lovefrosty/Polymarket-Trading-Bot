import unittest

from core_mm.order_manager import DesiredQuote, RestingOrder, SmartOrderManager


class TestOrderManager(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SmartOrderManager()

    def test_price_move_below_threshold_is_noop(self) -> None:
        action = self.manager.decide_one(
            "q1",
            desired_quote=DesiredQuote("q1", "token-1", "buy", 0.503, 100),
            existing=RestingOrder("q1", "o1", "token-1", "buy", 0.500, 100, 1_000),
            now_ms=2_000,
        )
        self.assertEqual(action.action, "NOOP")

    def test_price_move_above_threshold_replaces(self) -> None:
        action = self.manager.decide_one(
            "q1",
            desired_quote=DesiredQuote("q1", "token-1", "buy", 0.506, 100),
            existing=RestingOrder("q1", "o1", "token-1", "buy", 0.500, 100, 1_000),
            now_ms=2_000,
        )
        self.assertEqual(action.action, "CANCEL_AND_REPLACE")

    def test_size_change_below_threshold_is_noop(self) -> None:
        action = self.manager.decide_one(
            "q1",
            desired_quote=DesiredQuote("q1", "token-1", "buy", 0.500, 108),
            existing=RestingOrder("q1", "o1", "token-1", "buy", 0.500, 100, 1_000),
            now_ms=2_000,
        )
        self.assertEqual(action.action, "NOOP")

    def test_size_change_above_threshold_replaces(self) -> None:
        action = self.manager.decide_one(
            "q1",
            desired_quote=DesiredQuote("q1", "token-1", "buy", 0.500, 115),
            existing=RestingOrder("q1", "o1", "token-1", "buy", 0.500, 100, 1_000),
            now_ms=2_000,
        )
        self.assertEqual(action.action, "CANCEL_AND_REPLACE")

    def test_no_existing_order_places(self) -> None:
        action = self.manager.decide_one(
            "q1",
            desired_quote=DesiredQuote("q1", "token-1", "buy", 0.500, 100),
            existing=None,
            now_ms=2_000,
        )
        self.assertEqual(action.action, "PLACE")

    def test_empty_desired_quote_cancels_existing(self) -> None:
        action = self.manager.decide_one(
            "q1",
            desired_quote=None,
            existing=RestingOrder("q1", "o1", "token-1", "buy", 0.500, 100, 1_000),
            now_ms=2_000,
        )
        self.assertEqual(action.action, "CANCEL")


if __name__ == "__main__":
    unittest.main()
