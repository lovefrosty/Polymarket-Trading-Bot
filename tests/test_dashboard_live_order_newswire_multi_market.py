import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardLiveOrderNewswireMultiMarket(unittest.TestCase):
    def test_market_filtering_and_market_list(self) -> None:
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
                    "INSERT INTO orders VALUES (1000, 'e1', 'o1', 'btc-updown-15m-1', 'c1', 't1', 'buy', 0.5, 1.0, 'submitted', 'PAPER', '', '')"
                )
                cx.execute(
                    "INSERT INTO orders VALUES (1001, 'e2', 'o2', 'eth-updown-15m-1', 'c2', 't2', 'sell', 0.4, 2.0, 'accepted', 'PAPER', '', '')"
                )
                cx.commit()
            finally:
                cx.close()

            markets = da.get_live_order_newswire_markets(end_ts_ms=2_000, db_path=db_path)
            self.assertEqual(markets, ["btc-updown-15m-1", "eth-updown-15m-1"])

            btc = da.get_live_order_newswire(
                start_ts_ms=0,
                end_ts_ms=2_000,
                limit=10,
                market_slug="btc-updown-15m-1",
                db_path=db_path,
            )
            self.assertEqual(len(btc), 1)
            self.assertEqual(str(btc.iloc[0]["market_slug"]), "btc-updown-15m-1")


if __name__ == "__main__":
    unittest.main()
