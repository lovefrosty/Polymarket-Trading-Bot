import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.check_integration_health import _build_report


class TestCheckIntegrationHealth(unittest.TestCase):
    def test_build_report_pass_with_explainable_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    "CREATE TABLE liveness_stats(ts_ms INTEGER,event_id TEXT PRIMARY KEY,mode TEXT,clock_drift_ms REAL,sequence_gap_rate_per_min REAL,sequence_gap_count_1m INTEGER,ws_starvation_token_count INTEGER,max_ws_starvation_ms REAL,active_market_lag_ms REAL,freeze_state INTEGER,reason_codes TEXT,payload_json TEXT)"
                )
                cx.execute(
                    "CREATE TABLE reconciliation_stats(ts_ms INTEGER,event_id TEXT PRIMARY KEY,run_id TEXT,mode TEXT,broker_open_orders INTEGER,broker_inventory REAL,onchain_inventory REAL,derived_inventory REAL,inventory_delta_qty REAL,inventory_delta_usdc REAL,tolerance_qty REAL,tolerance_usdc REAL,outside_tolerance INTEGER,mismatch_count INTEGER,unresolved_mismatch_count INTEGER,consecutive_mismatch_cycles INTEGER,consecutive_onchain_disagree_cycles INTEGER,freeze_state INTEGER,freeze_reason TEXT,payload_json TEXT)"
                )
                cx.execute("CREATE TABLE alerts(ts_ms INTEGER,severity TEXT,code TEXT,message TEXT,payload_json TEXT)")
                cx.execute(
                    "CREATE TABLE rollover_metrics(ts_ms INTEGER,event_id TEXT PRIMARY KEY,metric_name TEXT,metric_value REAL,market_slug TEXT,selection_key TEXT,payload_json TEXT)"
                )
                cx.execute(
                    "CREATE TABLE rollover_status(ts_ms INTEGER,event_id TEXT PRIMARY KEY,event_type TEXT,market_slug TEXT,selection_key TEXT,end_ts_source TEXT,readiness_ok INTEGER,readiness_reason_codes TEXT,confirm_wait_ms REAL,commit_block_ms REAL,unsubscribe_ms REAL,unknown_msg_count INTEGER,ignored_old_rate_per_min REAL,payload_json TEXT)"
                )
                cx.execute(
                    "CREATE TABLE pstar(ts_ms INTEGER,symbol TEXT,value REAL,ts_event_ms INTEGER,pstar_recv_ts_ms INTEGER,confidence REAL,valid INTEGER,invalid_reason TEXT,sources_used TEXT,diagnostics_json TEXT)"
                )
                now_ms = 2_000_000
                cx.execute(
                    "INSERT INTO liveness_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now_ms, "l1", "OBSERVE", 10.0, 0.0, 0, 0, 100.0, 50.0, 0, "", "{}"),
                )
                cx.execute(
                    "INSERT INTO reconciliation_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now_ms, "r1", "run", "OBSERVE", 1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 1.0, 0, 0, 0, 0, 0, 0, "", "{}"),
                )
                cx.execute(
                    "INSERT INTO alerts VALUES (?,?,?,?,?)",
                    (now_ms, "info", "RECONCILIATION_UNFROZEN_EDGE", "ok", "{}"),
                )
                cx.execute(
                    "INSERT INTO alerts VALUES (?,?,?,?,?)",
                    (now_ms + 10, "critical", "POLICY_FREEZE", "A_PSTAR_INVALID", '{"codes":["A_PSTAR_INVALID"]}'),
                )
                cx.execute(
                    "INSERT INTO rollover_metrics VALUES (?,?,?,?,?,?,?)",
                    (now_ms, "m1", "active_rate_per_min", 120.0, "slug", "sel", "{}"),
                )
                cx.execute(
                    "INSERT INTO rollover_metrics VALUES (?,?,?,?,?,?,?)",
                    (now_ms, "m2", "ignored_old_rate_per_min", 12.0, "slug", "sel", "{}"),
                )
                cx.execute(
                    "INSERT INTO rollover_metrics VALUES (?,?,?,?,?,?,?)",
                    (now_ms, "m3", "unknown_msg_count", 1.0, "slug", "sel", "{}"),
                )
                cx.execute(
                    "INSERT INTO rollover_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        now_ms,
                        "rs1",
                        "ABORT",
                        "slug",
                        "sel",
                        "metadata",
                        0,
                        "",
                        100.0,
                        None,
                        None,
                        1,
                        0.1,
                        '{"abort_reason":"CONFIRM_TIMEOUT"}',
                    ),
                )
                cx.execute(
                    "INSERT INTO rollover_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        now_ms + 1,
                        "rs2",
                        "COMMIT",
                        "slug2",
                        "sel2",
                        "metadata",
                        1,
                        "",
                        50.0,
                        2.0,
                        1.0,
                        0,
                        0.0,
                        "{}",
                    ),
                )
                cx.execute(
                    "INSERT INTO pstar VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (now_ms, "BTC", 100.0, now_ms, now_ms, 1.0, 1, "", "spot,perp", '{"state":"VALID"}'),
                )
                cx.execute(
                    "INSERT INTO pstar VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (now_ms + 1, "BTC", None, None, now_ms + 1, 0.0, 0, "stale_source:spot", "", '{"state":"STALE"}'),
                )
                cx.commit()

                report = _build_report(
                    cx=cx,
                    lookback_start_ms=now_ms - 1000,
                    now_ms=now_ms,
                    clock_drift_max_ms=250.0,
                    ws_starvation_max_ms=5000.0,
                    min_liveness_green_pct=0.0,
                    min_pstar_valid_dwell_pct=0.0,
                )
            finally:
                cx.close()

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["unexplained_freeze_count"], 0)
        self.assertEqual(report["liveness"]["sample_count"], 1)
        self.assertAlmostEqual(report["subscription_partition"]["ignored_vs_active_ratio"], 0.1)
        self.assertEqual(report["rollover"]["commit_count"], 1)
        self.assertEqual(report["rollover"]["confirm_timeout_count"], 1)
        self.assertEqual(report["unknown_rate_health"]["status"], "OK")
        self.assertEqual(report["pstar_state_counts"], {"STALE": 1, "VALID": 1})
        self.assertEqual(report["pstar_valid_dwell_pct"], 50.0)
        self.assertEqual(report["a_invalid_alert_count"], 1)


if __name__ == "__main__":
    unittest.main()
