import unittest
from pathlib import Path

from scripts.arb_half_life import analyze_arb_half_life


class TestArbHalfLifeOutputs(unittest.TestCase):
    def test_arb_half_life_report(self) -> None:
        fixture_dir = Path(__file__).parent / "fixtures"
        decision = fixture_dir / "decision_tape_sample.jsonl"
        reference = fixture_dir / "reference_tape_sample.jsonl"
        report_a = analyze_arb_half_life(
            [str(decision)],
            [str(reference)],
            shock_horizon_sec=10,
            shock_quantile_q=0.5,
            shock_min_count=1,
        )
        report_b = analyze_arb_half_life(
            [str(decision)],
            [str(reference)],
            shock_horizon_sec=10,
            shock_quantile_q=0.5,
            shock_min_count=1,
        )
        self.assertEqual(report_a, report_b)
        self.assertIn("lag_stats", report_a)
        self.assertIn("half_life_ms", report_a)
        self.assertIn("shock", report_a)
        self.assertIn("by_symbol", report_a["shock"])
        symbol_meta = report_a["shock"]["by_symbol"].get("BTC", {})
        self.assertIn("threshold", symbol_meta)
        self.assertEqual(symbol_meta.get("quantile_q"), 0.5)


if __name__ == "__main__":
    unittest.main()
