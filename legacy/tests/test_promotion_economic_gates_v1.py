import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from core.sqlite_store import SQLiteStore


class TestPromotionEconomicGatesV1(unittest.TestCase):
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

    def _insert_base_rows(self, store: SQLiteStore, now_ms: int) -> None:
        for i in range(20):
            ts = now_ms - 1_000 + i
            store.insert(
                "decision_ticks",
                {
                    "ts_ms": ts,
                    "event_id": f"dt-{i}",
                    "decision_ts_ms": ts,
                    "token_id": "token-a",
                    "decision_id": f"d-{i}",
                    "book_asof_ts_ms": ts - 10,
                    "book_recv_ts_ms": ts - 1,
                    "book_seq": i + 1,
                    "book_level_count": 2,
                    "book_health_state": "FRESH",
                    "pstar_value": 100.0,
                    "pstar_asof_ts_ms": ts - 20,
                    "pstar_recv_ts_ms": ts - 2,
                    "pstar_sourceset": "[\"spot\",\"perp\"]",
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
                "event_id": "recon-ok",
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
        store.insert(
            "latency_stats",
            {
                "ts_ms": now_ms,
                "p50_send_ack_ms": 10.0,
                "p95_send_ack_ms": 20.0,
                "p50_ack_fill_ms": 20.0,
                "p95_ack_fill_ms": 30.0,
                "ws_lag_ms": 50.0,
                "p50_signal_age_ms": 100.0,
                "p95_signal_age_ms": 200.0,
                "p50_ws_lag_ms": 50.0,
                "p95_ws_lag_ms": 100.0,
                "p50_pstar_age_ms": 100.0,
                "p95_pstar_age_ms": 200.0,
            },
        )
        store.insert(
            "book_health_stats",
            {
                "ts_ms": now_ms,
                "event_id": "bh-ok",
                "token_id": "token-a",
                "book_asof_ts_ms": now_ms - 10,
                "book_recv_ts_ms": now_ms - 1,
                "book_seq": 1,
                "book_level_count": 2,
                "book_health_state": "FRESH",
                "book_age_p50_ms": 5.0,
                "book_age_p95_ms": 10.0,
                "ws_recv_rate_msgs_min": 20.0,
            },
        )
        store.insert(
            "liveness_stats",
            {
                "ts_ms": now_ms,
                "event_id": "live-ok",
                "mode": "OBSERVE",
                "clock_drift_ms": 10.0,
                "sequence_gap_rate_per_min": 0.0,
                "sequence_gap_count_1m": 0,
                "ws_starvation_token_count": 0,
                "max_ws_starvation_ms": 50.0,
                "active_market_lag_ms": 10.0,
                "freeze_state": 0,
                "reason_codes": "",
                "payload_json": "{}",
            },
        )

    def test_economic_gates_pass_and_fail_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok_db = Path(tmp) / "ok.db"
            store = SQLiteStore(ok_db)
            now_ms = int(time.time() * 1000)
            self._insert_base_rows(store, now_ms)
            store.insert(
                "execution_quality",
                {
                    "ts_ms": now_ms,
                    "event_id": "eq-ok",
                    "run_id": "run-test",
                    "mode": "OBSERVE",
                    "token_id": "token-a",
                    "order_id": "o-1",
                    "side": "buy",
                    "fill_ts_ms": now_ms - 1000,
                    "fill_price": 0.50,
                    "fill_qty": 1.0,
                    "fee_bps": 2.0,
                    "mid_at_send": 0.5010,
                    "mid_at_ack": 0.5015,
                    "mid_at_fill": 0.5012,
                    "mid_1s": 0.5018,
                    "mid_5s": 0.5020,
                    "mid_30s": 0.5022,
                    "realized_spread_bps": 20.0,
                    "markout_1s_bps": 15.0,
                    "markout_5s_bps": 12.0,
                    "markout_30s_bps": 10.0,
                    "net_edge_bps": 18.0,
                    "payload_json": "{}",
                },
            )
            store.close()
            ok = self._run_checker(ok_db)
            self.assertEqual(ok.returncode, 0, msg=ok.stderr)
            ok_payload = json.loads(ok.stdout)
            self.assertEqual(ok_payload["status"], "PASS")

            bad_db = Path(tmp) / "bad.db"
            store_bad = SQLiteStore(bad_db)
            self._insert_base_rows(store_bad, now_ms)
            store_bad.insert(
                "execution_quality",
                {
                    "ts_ms": now_ms,
                    "event_id": "eq-bad",
                    "run_id": "run-test",
                    "mode": "OBSERVE",
                    "token_id": "token-a",
                    "order_id": "o-2",
                    "side": "buy",
                    "fill_ts_ms": now_ms - 1000,
                    "fill_price": 0.50,
                    "fill_qty": 1.0,
                    "fee_bps": 2.0,
                    "mid_at_send": 0.4980,
                    "mid_at_ack": 0.4975,
                    "mid_at_fill": 0.4970,
                    "mid_1s": 0.4960,
                    "mid_5s": 0.4950,
                    "mid_30s": 0.4940,
                    "realized_spread_bps": -40.0,
                    "markout_1s_bps": -60.0,
                    "markout_5s_bps": -80.0,
                    "markout_30s_bps": -100.0,
                    "net_edge_bps": -42.0,
                    "payload_json": "{}",
                },
            )
            store_bad.close()
            bad = self._run_checker(bad_db)
            self.assertNotEqual(bad.returncode, 0)
            bad_payload = json.loads(bad.stdout)
            codes = {row["code"] for row in bad_payload.get("failed_gates", [])}
            self.assertIn("ECON_NET_EDGE_P50_NONNEG", codes)
            self.assertIn("ECON_ADVERSE_MARKOUT_5S_P95_MAX", codes)


if __name__ == "__main__":
    unittest.main()
