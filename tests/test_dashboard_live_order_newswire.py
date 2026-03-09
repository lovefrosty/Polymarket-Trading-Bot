import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardLiveOrderNewswire(unittest.TestCase):
    def test_incremental_fetch_and_deterministic_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    """
                    CREATE TABLE orders(
                        ts_ms INTEGER,
                        event_id TEXT,
                        order_id TEXT,
                        market_slug TEXT,
                        condition_id TEXT,
                        token_id TEXT,
                        side TEXT,
                        price REAL,
                        qty REAL,
                        status TEXT,
                        mode TEXT,
                        reason TEXT,
                        reason_code TEXT
                    )
                    """
                )
                cx.execute(
                    """
                    CREATE TABLE fills(
                        ts_ms INTEGER,
                        event_id TEXT,
                        order_id TEXT,
                        market_slug TEXT,
                        condition_id TEXT,
                        token_id TEXT,
                        side TEXT,
                        fill_price REAL,
                        fill_qty REAL,
                        liquidity TEXT,
                        mode TEXT,
                        reason_code TEXT
                    )
                    """
                )
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT)")
                cx.execute(
                    "INSERT INTO orders VALUES (1000, 'o2', 'ord-2', 'm2', 'c2', 't2', 'sell', 0.52, 2.0, 'accepted', 'PAPER', '', '')"
                )
                cx.execute(
                    "INSERT INTO orders VALUES (1000, 'o1', 'ord-1', 'm1', 'c1', 't1', 'buy', 0.51, 1.0, 'submitted', 'PAPER', '', '')"
                )
                cx.execute(
                    "INSERT INTO fills VALUES (1001, 'f1', 'ord-1', 'm1', 'c1', 't1', 'buy', 0.51, 1.0, 'maker', 'PAPER', '')"
                )
                cx.commit()
            finally:
                cx.close()

            rows = da.get_live_order_newswire(
                start_ts_ms=0,
                end_ts_ms=2_000,
                limit=10,
                market_slug="ALL",
                db_path=db_path,
            )
            self.assertEqual(list(rows["event_id"]), ["f1", "o2", "o1"])

            latest = rows.iloc[0]
            incremental = da.get_live_order_newswire(
                start_ts_ms=0,
                end_ts_ms=2_000,
                limit=10,
                market_slug="ALL",
                last_seen_ts_ms=int(latest["ts_ms"]),
                last_seen_event_id=str(latest["event_id"]),
                db_path=db_path,
            )
            self.assertTrue(incremental.empty)

            cx2 = sqlite3.connect(db_path.as_posix())
            try:
                cx2.execute(
                    "INSERT INTO orders VALUES (1002, 'o3', 'ord-3', 'm3', 'c3', 't3', 'buy', 0.5, 3.0, 'submitted', 'PAPER', '', '')"
                )
                cx2.commit()
            finally:
                cx2.close()

            incremental = da.get_live_order_newswire(
                start_ts_ms=0,
                end_ts_ms=2_000,
                limit=10,
                market_slug="ALL",
                last_seen_ts_ms=int(latest["ts_ms"]),
                last_seen_event_id=str(latest["event_id"]),
                db_path=db_path,
            )
            self.assertEqual(list(incremental["event_id"]), ["o3"])


if __name__ == "__main__":
    unittest.main()
