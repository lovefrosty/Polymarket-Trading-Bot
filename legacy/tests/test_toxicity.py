import unittest

from core.toxicity import toxicity_bps


class TestToxicity(unittest.TestCase):
    def test_toxicity_buy(self) -> None:
        result = toxicity_bps("buy", 0.5, 0.52)
        self.assertEqual(result.blockers, [])
        self.assertAlmostEqual(result.tox_bps, 200.0)

    def test_toxicity_sell(self) -> None:
        result = toxicity_bps("sell", 0.5, 0.48)
        self.assertEqual(result.blockers, [])
        self.assertAlmostEqual(result.tox_bps, 200.0)

    def test_toxicity_missing(self) -> None:
        result = toxicity_bps("buy", None, 0.52)
        self.assertIsNone(result.tox_bps)
        self.assertIn("TOX_UNAVAILABLE", result.blockers)


if __name__ == "__main__":
    unittest.main()
