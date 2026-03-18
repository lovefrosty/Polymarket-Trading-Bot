import unittest
from pathlib import Path

from scripts.analyze_audit import analyze_decision_files


class TestAuditPipeline(unittest.TestCase):
    def test_analyze_audit_outputs(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "decision_tape_sample.jsonl"
        reference = Path(__file__).parent / "fixtures" / "reference_tape_sample.jsonl"
        report = analyze_decision_files([str(fixture)], reference_files=[str(reference)])
        self.assertIn("gate_failures", report)
        self.assertIn("confusion", report)
        self.assertEqual(report["confusion"].get("rejected_by_EDGE_accepted_by_Z"), 1)
        self.assertEqual(report["confusion"].get("accepted_then_exited_by_EDGE_COLLAPSE"), 1)

        tox = report["toxicity"]["horizons"]["10s"]["accepted"]
        self.assertEqual(tox.get("count"), 2)

        self.assertIn("pathology_table", report)
        self.assertIn("hedge_policy", report)
        self.assertGreater(report["calibration"]["overall"].get("count", 0), 0)

    def test_audit_deterministic(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "decision_tape_sample.jsonl"
        reference = Path(__file__).parent / "fixtures" / "reference_tape_sample.jsonl"
        report_a = analyze_decision_files([str(fixture)], reference_files=[str(reference)])
        report_b = analyze_decision_files([str(fixture)], reference_files=[str(reference)])
        self.assertEqual(report_a, report_b)


if __name__ == "__main__":
    unittest.main()
