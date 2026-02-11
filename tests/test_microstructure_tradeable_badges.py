import unittest

from dashboard.app import build_tradeable_hint, classify_spread_state


class TestMicrostructureTradeableBadges(unittest.TestCase):
    def test_spread_state_thresholds(self) -> None:
        self.assertEqual(classify_spread_state(99.0), "OK")
        self.assertEqual(classify_spread_state(100.0), "CAUTION")
        self.assertEqual(classify_spread_state(200.0), "CAUTION")
        self.assertEqual(classify_spread_state(201.0), "BLOCKED")

    def test_tradeable_hint_waits_on_wide_spread(self) -> None:
        status, reason = build_tradeable_hint(
            {
                "spread_bps": 220.0,
                "depth_at_qty_buy": 1.0,
                "depth_at_qty_sell": 1.0,
                "book_health": "UP",
            }
        )
        self.assertEqual(status, "WAIT")
        self.assertIn("Spread too wide", reason)

    def test_tradeable_hint_yes_on_healthy_row(self) -> None:
        status, reason = build_tradeable_hint(
            {
                "spread_bps": 80.0,
                "depth_at_qty_buy": 1.0,
                "depth_at_qty_sell": 1.0,
                "book_health": "UP",
            }
        )
        self.assertEqual(status, "YES")
        self.assertEqual(reason, "Tradeable")


if __name__ == "__main__":
    unittest.main()
