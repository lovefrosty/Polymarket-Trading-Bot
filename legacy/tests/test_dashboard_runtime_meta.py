import json
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardRuntimeMeta(unittest.TestCase):
    def test_status_and_run_summary_read_from_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = root / "meta"
            meta.mkdir(parents=True, exist_ok=True)
            (meta / "status.json").write_text(json.dumps({"mode": "PAPER", "stage": "running", "decisions": 12}), encoding="utf-8")
            (meta / "run_summary.json").write_text(json.dumps({"realized_net_pnl": 3.5, "fills": 4}), encoding="utf-8")

            status = da.get_run_status(runtime_root=root)
            summary = da.get_run_summary(runtime_root=root)

        self.assertEqual(status["mode"], "PAPER")
        self.assertEqual(status["stage"], "running")
        self.assertEqual(summary["fills"], 4)
        self.assertAlmostEqual(float(summary["realized_net_pnl"]), 3.5)


if __name__ == "__main__":
    unittest.main()
