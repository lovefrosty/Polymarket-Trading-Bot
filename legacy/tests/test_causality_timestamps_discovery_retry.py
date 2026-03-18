import tempfile
import unittest
from pathlib import Path

from core.sqlite_store import SQLiteStore
from scripts.run_system import _append_discovery_request_rows


class TestCausalityTimestampsDiscoveryRetry(unittest.TestCase):
    def test_discovery_retry_rows_preserve_asof_and_retry_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            db = SQLiteStore(db_path)
            try:
                _append_discovery_request_rows(
                    db=db,
                    ts_ms=100_000,
                    discovery_requests=[
                        {
                            "status": "NONE_FOUND",
                            "requested_symbol": "BTC",
                            "requested_horizon": "15m",
                            "requested_mode": "latest_active",
                            "now_wall_ms": 100_000,
                            "error_code": "NO_ACTIVE_BTC_15M",
                            "retry_index": 0,
                            "next_retry_ts_ms": 130_000,
                            "n_total": 10,
                            "n_btc_15m": 10,
                            "n_with_end_ts": 10,
                            "n_active_now": 0,
                        },
                        {
                            "status": "NONE_FOUND",
                            "requested_symbol": "BTC",
                            "requested_horizon": "15m",
                            "requested_mode": "latest_active",
                            "now_wall_ms": 130_000,
                            "error_code": "NO_ACTIVE_BTC_15M",
                            "retry_index": 1,
                            "next_retry_ts_ms": 160_000,
                            "n_total": 10,
                            "n_btc_15m": 10,
                            "n_with_end_ts": 10,
                            "n_active_now": 0,
                        },
                    ],
                )
                rows = db.query(
                    """
                    SELECT status, now_ms, retry_index, next_retry_ts_ms
                    FROM discovery_requests
                    ORDER BY now_ms ASC
                    """
                )
            finally:
                db.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "NONE_FOUND")
        self.assertEqual(rows[0][1], 100_000)
        self.assertEqual(rows[0][2], 0)
        self.assertEqual(rows[0][3], 130_000)
        self.assertEqual(rows[1][0], "NONE_FOUND")
        self.assertEqual(rows[1][1], 130_000)
        self.assertEqual(rows[1][2], 1)
        self.assertEqual(rows[1][3], 160_000)
        self.assertGreater(rows[0][3], rows[0][1])
        self.assertGreater(rows[1][3], rows[1][1])


if __name__ == "__main__":
    unittest.main()
