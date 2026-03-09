import unittest

from dashboard.app import trader_tab_labels


class TestTabsPresentTraderMode(unittest.TestCase):
    def test_trader_tabs_match_required_set(self) -> None:
        tabs = trader_tab_labels()
        self.assertEqual(
            tabs,
            ["Health", "Signals", "Inventory & Quotes", "Portfolio", "Microstructure", "Logs"],
        )
        self.assertNotIn("Overview", tabs)
        self.assertNotIn("Rollover", " ".join(tabs))
        self.assertNotIn("WS Subscribe", " ".join(tabs))


if __name__ == "__main__":
    unittest.main()
