import unittest

from dashboard.app import build_paper_limit_status, build_paper_limit_summary


class TestDashboardPaperLimitsSummary(unittest.TestCase):
    def test_summary_is_plain_english_when_unavailable(self) -> None:
        snapshot = {
            "available": False,
            "stale": False,
            "profile": {},
            "utilization": {},
        }
        values = " ".join(value for _, value in build_paper_limit_summary(snapshot))
        self.assertIn("Unavailable", values)
        self.assertNotIn("N/A", values)
        self.assertEqual(build_paper_limit_status(snapshot), "Paper limits not yet published by runtime")

    def test_summary_is_plain_english_when_available(self) -> None:
        snapshot = {
            "available": True,
            "stale": False,
            "profile": {
                "max_orders_per_min": 10,
                "max_cancels_per_min": 8,
                "max_daily_notional_usdc": 500.0,
                "max_daily_loss_usdc": 25.0,
            },
            "utilization": {
                "orders_per_min": 2,
                "cancels_per_min": 1,
                "daily_notional_usdc": 100.0,
                "daily_loss_usdc": 5.0,
                "open_quote_count": 3,
                "active_risk_reasons": [],
            },
        }
        values = " ".join(value for _, value in build_paper_limit_summary(snapshot))
        self.assertIn("2/10 per min", values)
        self.assertIn("$100.00/$500.00", values)
        self.assertNotIn("N/A", values)
        self.assertEqual(build_paper_limit_status(snapshot), "Paper limits healthy - 3 open quote(s)")


if __name__ == "__main__":
    unittest.main()
