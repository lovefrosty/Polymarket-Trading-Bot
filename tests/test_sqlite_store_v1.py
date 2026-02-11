import tempfile
import unittest
from pathlib import Path

from core.sqlite_store import SQLiteStore


class TestSQLiteStoreV1(unittest.TestCase):
    def test_insert_and_export_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            store.insert(
                "decisions",
                {
                    "ts_ms": 1000,
                    "decision_id": "d1",
                    "market": "m",
                    "token_id": "t",
                    "action": "QUOTE",
                    "reason_codes": "",
                    "p_hat": 0.5,
                    "expected_edge": 0.0,
                    "expected_cost": 0.0,
                    "decision_ts_event_ms": 1000,
                    "book_asof_ts_ms": 900,
                    "pstar_asof_ts_ms": 900,
                    "max_feature_ts_ms": 900,
                    "policy_json": "{}",
                },
            )
            out = Path(tmp) / "decision.jsonl"
            store.export_table_jsonl("decisions", out, order_by="ts_ms")
            first = out.read_text(encoding="utf-8")
            store.export_table_jsonl("decisions", out, order_by="ts_ms")
            second = out.read_text(encoding="utf-8")
            store.close()
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
