import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardPortfolioPositions(unittest.TestCase):
    def test_positions_mark_priority_and_as_of_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute("CREATE TABLE inventory(ts_ms INTEGER, token_id TEXT, yes_qty REAL, no_qty REAL, usdc REAL, source TEXT)")
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT)")
                cx.execute("CREATE TABLE fills(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, side TEXT, fill_price REAL, fill_qty REAL, payload_json TEXT)")
                cx.execute("CREATE TABLE pstar(ts_ms INTEGER, symbol TEXT, value REAL, valid INTEGER)")
                cx.execute("CREATE TABLE market_data_book(ts_ms INTEGER, token_id TEXT, side TEXT, price REAL, size REAL)")

                # Token 1: P* available and valid.
                cx.execute("INSERT INTO inventory VALUES (1000, 't1', 2.0, 0.0, NULL, 'runtime')")
                cx.execute("INSERT INTO inventory VALUES (2000, 't1', 10.0, 0.0, NULL, 'runtime')")  # newer than as_of, must be ignored
                cx.execute("INSERT INTO decisions VALUES (1000, 'd1', 'btc-updown-15m-1700000000', 't1')")
                cx.execute("INSERT INTO fills VALUES (900, 'f1', 'o1', 't1', 'buy', 0.50, 2.0, '{}')")
                cx.execute("INSERT INTO pstar VALUES (1400, 'BTC', 0.60, 1)")
                cx.execute("INSERT INTO market_data_book VALUES (1400, 't1', 'buy', 0.55, 1.0)")
                cx.execute("INSERT INTO market_data_book VALUES (1400, 't1', 'sell', 0.57, 1.0)")

                # Token 2: no valid P*, fallback to mid.
                cx.execute("INSERT INTO inventory VALUES (1000, 't2', 1.0, 0.0, NULL, 'runtime')")
                cx.execute("INSERT INTO decisions VALUES (1000, 'd2', 'eth-updown-15m-1700000000', 't2')")
                cx.execute("INSERT INTO fills VALUES (950, 'f2', 'o2', 't2', 'buy', 0.40, 1.0, '{}')")
                cx.execute("INSERT INTO pstar VALUES (1400, 'ETH', 0.41, 0)")
                cx.execute("INSERT INTO market_data_book VALUES (1400, 't2', 'buy', 0.40, 1.0)")
                cx.execute("INSERT INTO market_data_book VALUES (1400, 't2', 'sell', 0.44, 1.0)")

                cx.commit()
            finally:
                cx.close()

            out = da.get_positions_as_of(as_of_ts_ms=1500, db_path=db_path)

        self.assertEqual(list(out["token_id"]), ["t1", "t2"])
        row1 = out[out["token_id"] == "t1"].iloc[0]
        self.assertAlmostEqual(float(row1["net_shares"]), 2.0)
        self.assertEqual(str(row1["mark_source"]), "PSTAR")
        self.assertAlmostEqual(float(row1["mark"]), 0.60)
        self.assertAlmostEqual(float(row1["avg_entry"]), 0.50)
        self.assertAlmostEqual(float(row1["unrealized_pnl"]), 0.20)

        row2 = out[out["token_id"] == "t2"].iloc[0]
        self.assertEqual(str(row2["mark_source"]), "MID")
        self.assertAlmostEqual(float(row2["mark"]), 0.42)


if __name__ == "__main__":
    unittest.main()
