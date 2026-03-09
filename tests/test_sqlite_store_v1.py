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

    def test_batched_market_trades_preserve_order_and_no_drop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            expected_trade_ids = []
            for i in range(80):
                trade_id = f"tr-{i:03d}"
                expected_trade_ids.append(trade_id)
                store.insert(
                    "market_trades",
                    {
                        "ts_ms": 2000 + i,
                        "token_id": "token-a",
                        "side": "BUY",
                        "price": 0.5,
                        "size": 1.0,
                        "trade_id": trade_id,
                    },
                )
            store.close()

            reopened = SQLiteStore(db_path)
            try:
                rows = reopened.query("SELECT trade_id FROM market_trades ORDER BY rowid")
            finally:
                reopened.close()

            actual_trade_ids = [str(row[0]) for row in rows]
            self.assertEqual(actual_trade_ids, expected_trade_ids)
            self.assertEqual(len(actual_trade_ids), len(expected_trade_ids))

    def test_pending_batch_then_decision_keeps_reason_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            for i in range(10):
                store.insert(
                    "market_data_book",
                    {
                        "ts_ms": 3000 + i,
                        "token_id": "token-b",
                        "side": "BUY",
                        "price": 0.4 + (i * 0.001),
                        "size": 5.0,
                        "source": "ws",
                    },
                )

            decision_id = "d-batch"
            market = "m-batch"
            token_id = "t-batch"
            reason_codes = "R_A,R_B"
            store.insert(
                "decisions",
                {
                    "ts_ms": 5000,
                    "decision_id": decision_id,
                    "market": market,
                    "token_id": token_id,
                    "action": "QUOTE",
                    "reason_codes": reason_codes,
                    "p_hat": 0.51,
                    "expected_edge": 0.01,
                    "expected_cost": 0.001,
                    "decision_ts_event_ms": 5000,
                    "book_asof_ts_ms": 4990,
                    "pstar_asof_ts_ms": 4990,
                    "max_feature_ts_ms": 4990,
                    "policy_json": "{}",
                },
            )
            store.close()

            reopened = SQLiteStore(db_path)
            try:
                book_count = reopened.query("SELECT COUNT(*) FROM market_data_book")
                decision_rows = reopened.query(
                    "SELECT decision_id, reason_codes FROM decisions WHERE decision_id = ?",
                    [decision_id],
                )
                entity = f"{market}:{token_id}:{decision_id}"
                evidence_rows = reopened.query(
                    "SELECT source, entity, event_type, reason_code, severity FROM evidence_rows WHERE entity = ?",
                    [entity],
                )
            finally:
                reopened.close()

            self.assertEqual(int(book_count[0][0]), 10)
            self.assertEqual(len(decision_rows), 1)
            self.assertEqual(str(decision_rows[0][0]), decision_id)
            self.assertEqual(str(decision_rows[0][1]), reason_codes)
            self.assertEqual(len(evidence_rows), 1)
            self.assertEqual(str(evidence_rows[0][0]), "runtime")
            self.assertEqual(str(evidence_rows[0][1]), entity)
            self.assertEqual(str(evidence_rows[0][2]), "QUOTE")
            self.assertEqual(str(evidence_rows[0][3]), reason_codes)
            self.assertEqual(str(evidence_rows[0][4]), "info")


if __name__ == "__main__":
    unittest.main()
