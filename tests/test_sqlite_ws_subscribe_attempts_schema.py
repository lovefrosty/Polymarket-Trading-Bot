import tempfile
import unittest
from pathlib import Path

from core.sqlite_store import SQLiteStore


class TestSQLiteWSSubscribeAttemptsSchema(unittest.TestCase):
    def test_table_exists_and_append_helper_writes_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            store.append_ws_subscribe_attempt(
                run_id="run-1",
                ts_ms=1_000,
                attempt_id=1,
                action="resubscribe",
                active_sub_id_before=1,
                pending_sub_id=2,
                asset_ids_json="[\"a\",\"b\"]",
                payload_json="{\"assets_ids\":[\"a\",\"b\"],\"type\":\"market\"}",
                ack_status="UNSUPPORTED",
                ack_ts_ms=None,
                ack_error=None,
                preclass_pending_hits=0,
                preclass_active_hits=2,
                preclass_unknown_schema=0,
                preclass_missing_asset=0,
                preclass_missing_sub=2,
                confirm_required_updates=2,
                confirm_counts_by_asset_json="{\"a\":0,\"b\":0}",
                confirm_preclass_hits_by_asset_json="{\"a\":0,\"b\":0}",
                first_pending_recv_ts_ms=None,
                last_pending_recv_ts_ms=None,
                confirm_wait_ms=5000.0,
                result="TIMEOUT",
            )
            rows = store.query("SELECT attempt_id, ack_status, result FROM ws_subscribe_attempts ORDER BY ts_ms")
            store.close()

            self.assertEqual(rows, [(1, "UNSUPPORTED", "TIMEOUT")])

            reopened = SQLiteStore(db_path)
            exists = reopened.query(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ws_subscribe_attempts'"
            )
            reopened.close()
            self.assertEqual(int(exists[0][0]), 1)


if __name__ == "__main__":
    unittest.main()
