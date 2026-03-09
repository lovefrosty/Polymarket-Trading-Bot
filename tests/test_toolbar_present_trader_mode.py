import unittest

from dashboard.app import trader_toolbar_groups


class TestToolbarPresentTraderMode(unittest.TestCase):
    def test_toolbar_groups_exist(self) -> None:
        groups = trader_toolbar_groups()
        self.assertIn("left", groups)
        self.assertIn("middle", groups)
        self.assertIn("right", groups)
        self.assertIn("View Mode", groups["left"])
        self.assertIn("Market", groups["left"])
        self.assertIn("Time Window", groups["left"])
        self.assertIn("Auto refresh", groups["middle"])
        self.assertIn("Rows", groups["middle"])
        self.assertIn("Token", groups["right"])
        self.assertIn("Alert Severity", groups["right"])


if __name__ == "__main__":
    unittest.main()
