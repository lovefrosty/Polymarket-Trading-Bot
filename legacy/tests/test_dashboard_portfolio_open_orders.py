import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardPortfolioOpenOrders(unittest.TestCase):
    def test_latest_snapshot_per_order_and_status_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    "CREATE TABLE open_orders_snapshot(ts_ms INTEGER, event_id TEXT, run_id TEXT, mode TEXT, token_id TEXT, side TEXT, order_id TEXT, price REAL, size REAL, status TEXT, client_order_id TEXT, quote_group_id TEXT, payload_json TEXT)"
                )
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT)")
                cx.execute("INSERT INTO decisions VALUES (1000, 'd1', 'btc-updown-15m-1700000000', 't1')")
                cx.execute("INSERT INTO open_orders_snapshot VALUES (1000, 'e1', 'r', 'TRADE', 't1', 'buy', 'o1', 0.45, 1.0, 'open', 'cid1', 'qg1', '{}')")
                cx.execute("INSERT INTO open_orders_snapshot VALUES (1100, 'e2', 'r', 'TRADE', 't1', 'buy', 'o1', 0.46, 2.0, 'open', 'cid1', 'qg1', '{}')")
                cx.execute("INSERT INTO open_orders_snapshot VALUES (1150, 'e3', 'r', 'TRADE', 't1', 'sell', 'o2', 0.60, 1.0, 'canceled', 'cid2', 'qg2', '{}')")
                cx.commit()
            finally:
                cx.close()

            out = da.get_open_orders_latest(as_of_ts_ms=1200, db_path=db_path)

        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(str(row["order_id"]), "o1")
        self.assertAlmostEqual(float(row["price"]), 0.46)
        self.assertAlmostEqual(float(row["size"]), 2.0)
        self.assertEqual(str(row["market_slug"]), "btc-updown-15m-1700000000")

    def test_missing_table_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            sqlite3.connect(db_path.as_posix()).close()
            out = da.get_open_orders_latest(as_of_ts_ms=1000, db_path=db_path)
        self.assertTrue(out.empty)
        self.assertIn("order_id", out.columns)


if __name__ == "__main__":
    unittest.main()
