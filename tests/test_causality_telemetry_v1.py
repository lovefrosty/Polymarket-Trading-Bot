import tempfile
import unittest
from pathlib import Path

from core.sqlite_store import SQLiteStore


class TestCausalityTelemetryV1(unittest.TestCase):
    def test_causality_query_detects_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            base = 1_700_000_000_000
            store.insert(
                "decision_ticks",
                {
                    "ts_ms": base,
                    "event_id": "ok-1",
                    "decision_ts_ms": base,
                    "token_id": "t1",
                    "decision_id": "d1",
                    "book_asof_ts_ms": base - 1,
                    "book_recv_ts_ms": base,
                    "book_seq": 1,
                    "book_level_count": 2,
                    "book_health_state": "FRESH",
                    "pstar_value": 100.0,
                    "pstar_asof_ts_ms": base - 2,
                    "pstar_recv_ts_ms": base - 1,
                    "pstar_sourceset": "[\"spot\",\"perp\"]",
                    "pstar_confidence": 0.9,
                    "pstar_valid": 1,
                    "invalid_reason": "",
                    "max_feature_ts_ms": base - 3,
                    "ws_lag_ms": 1.0,
                    "pstar_age_ms": 2.0,
                    "signal_age_ms": 3.0,
                    "allow_action": 1,
                    "block_reason_codes": "",
                    "payload_json": "{}",
                },
            )
            store.insert(
                "decision_ticks",
                {
                    "ts_ms": base + 1,
                    "event_id": "bad-1",
                    "decision_ts_ms": base + 1,
                    "token_id": "t1",
                    "decision_id": "d2",
                    "book_asof_ts_ms": base + 1,
                    "book_recv_ts_ms": base + 1,
                    "book_seq": 2,
                    "book_level_count": 2,
                    "book_health_state": "FRESH",
                    "pstar_value": 100.0,
                    "pstar_asof_ts_ms": base,
                    "pstar_recv_ts_ms": base,
                    "pstar_sourceset": "[\"spot\",\"perp\"]",
                    "pstar_confidence": 0.9,
                    "pstar_valid": 1,
                    "invalid_reason": "",
                    "max_feature_ts_ms": base + 1,
                    "ws_lag_ms": 1.0,
                    "pstar_age_ms": 1.0,
                    "signal_age_ms": 0.0,
                    "allow_action": 0,
                    "block_reason_codes": "B_FEATURE_TIME_LEAK",
                    "payload_json": "{}",
                },
            )
            rows = store.query(
                """
                SELECT COUNT(*)
                FROM decision_ticks
                WHERE (max_feature_ts_ms >= decision_ts_ms)
                   OR (book_asof_ts_ms IS NOT NULL AND book_asof_ts_ms >= decision_ts_ms)
                   OR (pstar_asof_ts_ms IS NOT NULL AND pstar_asof_ts_ms >= decision_ts_ms)
                """
            )
            store.close()
            self.assertEqual(int(rows[0][0]), 1)


if __name__ == "__main__":
    unittest.main()
