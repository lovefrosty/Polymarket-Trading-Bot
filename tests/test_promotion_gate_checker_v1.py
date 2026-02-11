import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from core.sqlite_store import SQLiteStore


class TestPromotionGateCheckerV1(unittest.TestCase):
    def _run_checker(self, db_path: Path, lookback_hours: int = 1) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                "scripts/check_promotion_gates.py",
                "--db-path",
                str(db_path),
                "--lookback-hours",
                str(lookback_hours),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_checker_passes_clean_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            now_ms = int(time.time() * 1000)
            for i in range(100):
                ts = now_ms - 1000 + i
                store.insert(
                    "decision_ticks",
                    {
                        "ts_ms": ts,
                        "event_id": f"ok-{i}",
                        "decision_ts_ms": ts,
                        "token_id": "token-a",
                        "decision_id": f"d-{i}",
                        "book_asof_ts_ms": ts - 10,
                        "book_recv_ts_ms": ts - 1,
                        "book_seq": i + 1,
                        "book_level_count": 4,
                        "book_health_state": "FRESH",
                        "pstar_value": 100.0,
                        "pstar_asof_ts_ms": ts - 20,
                        "pstar_recv_ts_ms": ts - 2,
                        "pstar_sourceset": "[\"perp\",\"spot\"]",
                        "pstar_confidence": 0.9,
                        "pstar_valid": 1,
                        "invalid_reason": "",
                        "max_feature_ts_ms": ts - 30,
                        "ws_lag_ms": 50.0,
                        "pstar_age_ms": 120.0,
                        "signal_age_ms": 300.0,
                        "allow_action": 1,
                        "block_reason_codes": "",
                        "payload_json": "{}",
                    },
                )
            store.insert(
                "reconciliation_stats",
                {
                    "ts_ms": now_ms,
                    "event_id": "recon-ok-1",
                    "run_id": "run-test",
                    "mode": "OBSERVE",
                    "broker_open_orders": 0,
                    "broker_inventory": 0.0,
                    "onchain_inventory": 0.0,
                    "derived_inventory": 0.0,
                    "inventory_delta_qty": 0.0,
                    "inventory_delta_usdc": 0.0,
                    "tolerance_qty": 0.01,
                    "tolerance_usdc": 1.0,
                    "outside_tolerance": 0,
                    "mismatch_count": 0,
                    "unresolved_mismatch_count": 0,
                    "consecutive_mismatch_cycles": 0,
                    "consecutive_onchain_disagree_cycles": 0,
                    "freeze_state": 0,
                    "freeze_reason": "",
                    "payload_json": "{}",
                },
            )
            store.close()

            result = self._run_checker(db_path)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["failed_gates"], [])

    def test_checker_fails_on_causality_and_latency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            store = SQLiteStore(db_path)
            now_ms = int(time.time() * 1000)
            store.insert(
                "decision_ticks",
                {
                    "ts_ms": now_ms,
                    "event_id": "bad-1",
                    "decision_ts_ms": now_ms,
                    "token_id": "token-b",
                    "decision_id": "d-bad",
                    "book_asof_ts_ms": now_ms,
                    "book_recv_ts_ms": now_ms,
                    "book_seq": 1,
                    "book_level_count": 2,
                    "book_health_state": "FRESH",
                    "pstar_value": 100.0,
                    "pstar_asof_ts_ms": now_ms,
                    "pstar_recv_ts_ms": now_ms,
                    "pstar_sourceset": "[\"spot\"]",
                    "pstar_confidence": 0.4,
                    "pstar_valid": 1,
                    "invalid_reason": "",
                    "max_feature_ts_ms": now_ms,
                    "ws_lag_ms": 10_000.0,
                    "pstar_age_ms": 10_000.0,
                    "signal_age_ms": 10_000.0,
                    "allow_action": 0,
                    "block_reason_codes": "B_FEATURE_TIME_LEAK",
                    "payload_json": "{}",
                },
            )
            store.close()

            result = self._run_checker(db_path)
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FAIL")
            codes = {row["code"] for row in payload["failed_gates"]}
            self.assertIn("B_CAUSALITY_ZERO", codes)
            self.assertIn("E_WS_LAG_P95", codes)


if __name__ == "__main__":
    unittest.main()
