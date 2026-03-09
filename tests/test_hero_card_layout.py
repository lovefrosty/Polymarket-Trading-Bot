import unittest

from dashboard.app import hero_card_layout_contract


class TestHeroCardLayout(unittest.TestCase):
    def test_hero_contract_contains_required_parts(self) -> None:
        contract = hero_card_layout_contract()
        self.assertIn("row1", contract)
        self.assertIn("row2", contract)
        self.assertIn("row3", contract)
        self.assertIn("Polymarket Terminal", contract["row1"])
        self.assertIn("View Mode pill", contract["row1"])
        self.assertIn("Runtime pill", contract["row1"])
        self.assertIn("Mode pill", contract["row2"])
        self.assertIn("State pill", contract["row2"])
        self.assertIn("Readiness pill", contract["row2"])
        self.assertIn("Status line", contract["row3"])


if __name__ == "__main__":
    unittest.main()
