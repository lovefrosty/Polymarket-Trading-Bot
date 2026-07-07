import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestPromotionMissingTablesDiagnostics(unittest.TestCase):
    def test_missing_tables_are_reported_with_impacted_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "empty.db"
            db_path.touch()
            result = subprocess.run(
                [
                    "python3",
                    "scripts/check_promotion_gates.py",
                    "--db-path",
                    str(db_path),
                    "--lookback-hours",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload.get("status"), "FAIL")
            missing = [row for row in payload.get("failed_gates", []) if row.get("code") == "MISSING_TABLE"]
            self.assertGreaterEqual(len(missing), 3)
            tables = {str(row.get("metric", "")).split("table:")[-1] for row in missing}
            self.assertIn("decision_ticks", tables)
            self.assertIn("alerts", tables)
            self.assertIn("reconciliation_stats", tables)
            decision_entry = next(row for row in missing if row.get("metric") == "table:decision_ticks")
            observed = decision_entry.get("observed")
            self.assertIsInstance(observed, dict)
            self.assertIn("impacts", observed)
            self.assertIn("B_CAUSALITY_ZERO", list(observed.get("impacts", [])))


if __name__ == "__main__":
    unittest.main()
