import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardPaperPnlSummary(unittest.TestCase):
    def test_paper_pnl_summary_computes_total_and_drawdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    "CREATE TABLE paper_pnl(ts_ms INTEGER, event_id INTEGER, market_slug TEXT, token_id TEXT, realized_gross_pnl REAL, realized_net_pnl REAL, unrealized_pnl REAL, cumulative_fees REAL, turnover REAL, win_count INTEGER, loss_count INTEGER, payload_json TEXT)"
                )
                cx.execute("INSERT INTO paper_pnl VALUES (1000, 1, 'm1', 't1', 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, '{}')")
                cx.execute("INSERT INTO paper_pnl VALUES (2000, 2, 'm1', 't1', 5.5, 5.0, 1.0, 0.1, 10.0, 1, 0, '{}')")
                cx.execute("INSERT INTO paper_pnl VALUES (2000, 3, 'm1', 't2', 5.5, 5.0, -0.5, 0.1, 10.0, 1, 0, '{}')")
                cx.execute("INSERT INTO paper_pnl VALUES (3000, 4, 'm1', 't1', 2.5, 2.0, -1.0, 0.2, 20.0, 1, 1, '{}')")
                cx.execute("INSERT INTO paper_pnl VALUES (3000, 5, 'm1', 't2', 2.5, 2.0, -0.5, 0.2, 20.0, 1, 1, '{}')")
                cx.commit()
            finally:
                cx.close()

            curve = da.get_paper_pnl_curve(db_path=db_path)
            summary = da.get_paper_pnl_summary(db_path=db_path)

        self.assertEqual(list(curve["ts_ms"]), [1000, 2000, 3000])
        self.assertAlmostEqual(float(curve[curve["ts_ms"] == 2000]["total_pnl"].iloc[0]), 5.5)
        self.assertAlmostEqual(float(summary["total_pnl"]), 0.5)
        self.assertAlmostEqual(float(summary["max_drawdown_abs"]), 5.0)
        self.assertAlmostEqual(float(summary["realized_net_pnl"]), 2.0)
        self.assertAlmostEqual(float(summary["unrealized_pnl"]), -1.5)


if __name__ == "__main__":
    unittest.main()
