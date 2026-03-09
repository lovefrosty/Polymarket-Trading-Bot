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

    def _run_promotion_report(
        self,
        *,
        db_path: Path,
        mode_history: Path,
        replay_report: Path,
        current_mode: str,
        now_ms: int,
        runtime_fingerprint: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                "scripts/promotion_report.py",
                "--db-path",
                str(db_path),
                "--lookback-hours",
                "1",
                "--current-mode",
                str(current_mode),
                "--mode-history",
                str(mode_history),
                "--replay-report",
                str(replay_report),
                "--now-ms",
                str(int(now_ms)),
                "--runtime-fingerprint",
                str(runtime_fingerprint),
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )

    def _seed_clean_fixture(self, db_path: Path) -> None:
        store = SQLiteStore(db_path)
        now_ms = int(time.time() * 1000)
        store.insert(
            "decision_ticks",
            {
                "ts_ms": now_ms,
                "event_id": "ok-0",
                "decision_ts_ms": now_ms,
                "token_id": "token-a",
                "decision_id": "d-0",
                "book_asof_ts_ms": now_ms - 10,
                "book_recv_ts_ms": now_ms - 1,
                "book_seq": 1,
                "book_level_count": 4,
                "book_health_state": "FRESH",
                "pstar_value": 100.0,
                "pstar_asof_ts_ms": now_ms - 20,
                "pstar_recv_ts_ms": now_ms - 2,
                "pstar_sourceset": "[\"perp\",\"spot\"]",
                "pstar_confidence": 0.9,
                "pstar_valid": 1,
                "invalid_reason": "",
                "max_feature_ts_ms": now_ms - 30,
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
        store.insert(
            "execution_quality",
            {
                "ts_ms": now_ms,
                "event_id": "eq-ok-1",
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
                "markout_1s_bps": 25.0,
                "markout_5s_bps": 28.0,
                "markout_30s_bps": 30.0,
                "net_edge_bps": 18.0,
                "payload_json": "{}",
            },
        )
        store.insert(
            "liveness_stats",
            {
                "ts_ms": now_ms,
                "event_id": "live-ok-1",
                "mode": "OBSERVE",
                "clock_drift_ms": 5.0,
                "sequence_gap_rate_per_min": 0.0,
                "sequence_gap_count_1m": 0,
                "ws_starvation_token_count": 0,
                "max_ws_starvation_ms": 50.0,
                "active_market_lag_ms": 30.0,
                "freeze_state": 0,
                "reason_codes": "",
                "payload_json": "{}",
            },
        )
        store.close()

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
            store.insert(
                "execution_quality",
                {
                    "ts_ms": now_ms,
                    "event_id": "eq-ok-1",
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
                    "markout_1s_bps": 25.0,
                    "markout_5s_bps": 28.0,
                    "markout_30s_bps": 30.0,
                    "net_edge_bps": 18.0,
                    "payload_json": "{}",
                },
            )
            store.insert(
                "liveness_stats",
                {
                    "ts_ms": now_ms,
                    "event_id": "live-ok-1",
                    "mode": "OBSERVE",
                    "clock_drift_ms": 5.0,
                    "sequence_gap_rate_per_min": 0.0,
                    "sequence_gap_count_1m": 0,
                    "ws_starvation_token_count": 0,
                    "max_ws_starvation_ms": 50.0,
                    "active_market_lag_ms": 30.0,
                    "freeze_state": 0,
                    "reason_codes": "",
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

    def test_promotion_report_holds_when_soak_is_unmet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime.db"
            self._seed_clean_fixture(db_path)
            replay_report = root / "replay.json"
            replay_report.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            mode_history = root / "soak.json"

            result = self._run_promotion_report(
                db_path=db_path,
                mode_history=mode_history,
                replay_report=replay_report,
                current_mode="TRADE",
                now_ms=1_000_000,
                runtime_fingerprint="fp-hold",
            )
            self.assertNotEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "HOLD")
            self.assertIn("SOAK_OBSERVE_MIN_48H", payload["block_reasons"])
            self.assertIn("SOAK_PAPER_MIN_48H", payload["block_reasons"])

    def test_promotion_report_promotes_when_soak_and_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime.db"
            self._seed_clean_fixture(db_path)
            replay_report = root / "replay.json"
            replay_report.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            mode_history = root / "soak.json"
            mode_history.write_text(
                json.dumps(
                    {
                        "schema_version": "promotion_soak_v1",
                        "last_runtime_fingerprint": "fp-promote",
                        "mode_windows": {
                            "OBSERVE": {"start_ts_ms": 0, "last_ts_ms": 172_800_000},
                            "PAPER": {"start_ts_ms": 0, "last_ts_ms": 172_800_000},
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_promotion_report(
                db_path=db_path,
                mode_history=mode_history,
                replay_report=replay_report,
                current_mode="TRADE",
                now_ms=172_800_000,
                runtime_fingerprint="fp-promote",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "PROMOTE")
            self.assertTrue(payload["promotion_ready"])
            self.assertEqual(payload["block_reasons"], [])

    def test_promotion_report_resets_soak_on_runtime_fingerprint_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime.db"
            self._seed_clean_fixture(db_path)
            replay_report = root / "replay.json"
            replay_report.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            mode_history = root / "soak.json"
            mode_history.write_text(
                json.dumps(
                    {
                        "schema_version": "promotion_soak_v1",
                        "last_runtime_fingerprint": "old-fingerprint",
                        "mode_windows": {
                            "OBSERVE": {"start_ts_ms": 0, "last_ts_ms": 172_800_000},
                            "PAPER": {"start_ts_ms": 0, "last_ts_ms": 172_800_000},
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_promotion_report(
                db_path=db_path,
                mode_history=mode_history,
                replay_report=replay_report,
                current_mode="TRADE",
                now_ms=172_800_000,
                runtime_fingerprint="new-fingerprint",
            )
            self.assertNotEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "HOLD")
            self.assertIn("SOAK_OBSERVE_MIN_48H", payload["block_reasons"])
            self.assertIn("SOAK_PAPER_MIN_48H", payload["block_reasons"])


if __name__ == "__main__":
    unittest.main()
