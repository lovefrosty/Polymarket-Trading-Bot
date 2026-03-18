import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardTradeBlotter(unittest.TestCase):
    def test_blotter_ordering_and_execution_quality_join(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    "CREATE TABLE fills(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, side TEXT, fill_price REAL, fill_qty REAL, payload_json TEXT)"
                )
                cx.execute(
                    "CREATE TABLE execution_quality(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, side TEXT, fill_ts_ms INTEGER, realized_spread_bps REAL, markout_5s_bps REAL, net_edge_bps REAL)"
                )
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT)")

                cx.execute("INSERT INTO decisions VALUES (1000, 'd1', 'btc-updown-15m-1700000000', 't1')")
                cx.execute("INSERT INTO fills VALUES (1000, 'f1', 'o1', 't1', 'buy', 0.50, 1.0, '{}')")
                cx.execute("INSERT INTO fills VALUES (1200, 'f2', 'o2', 't1', 'sell', 0.55, 2.0, '{}')")
                cx.execute("INSERT INTO execution_quality VALUES (1100, 'eq-old', 'o1', 't1', 'buy', 1000, 5.0, 2.0, 3.0)")
                cx.execute("INSERT INTO execution_quality VALUES (1300, 'eq-new', 'o1', 't1', 'buy', 1001, 7.0, 4.0, 6.0)")
                cx.commit()
            finally:
                cx.close()

            out = da.get_trade_blotter(start_ts_ms=900, end_ts_ms=1300, limit=20, db_path=db_path)

        self.assertEqual(list(out["event_id"]), ["f2", "f1"])
        row_o1 = out[out["order_id"] == "o1"].iloc[0]
        self.assertAlmostEqual(float(row_o1["realized_spread_bps"]), 7.0)
        self.assertAlmostEqual(float(row_o1["markout_5s_bps"]), 4.0)
        self.assertAlmostEqual(float(row_o1["net_edge_bps"]), 6.0)


if __name__ == "__main__":
    unittest.main()
