import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_run import _build_run_summary


class TestRunSummaryArtifact(unittest.TestCase):
    def test_build_run_summary_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "runtime.db"
            run_dir = root / "run"
            run_dir.mkdir(parents=True, exist_ok=True)

            (run_dir / "market_20260211.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"channel": "market", "event_type": "book"}),
                        json.dumps({"channel": "market", "event_type": "price_change"}),
                        json.dumps({"channel": "system", "event_type": "ROLLOVER_INTENT"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    "CREATE TABLE latency_stats(ts_ms INTEGER PRIMARY KEY,p50_send_ack_ms REAL,p95_send_ack_ms REAL,p50_ack_fill_ms REAL,p95_ack_fill_ms REAL,ws_lag_ms REAL,p50_signal_age_ms REAL,p95_signal_age_ms REAL,p50_ws_lag_ms REAL,p95_ws_lag_ms REAL,p50_pstar_age_ms REAL,p95_pstar_age_ms REAL)"
                )
                cx.execute(
                    "CREATE TABLE rollover_status(ts_ms INTEGER,event_id TEXT PRIMARY KEY,event_type TEXT,market_slug TEXT,selection_key TEXT,end_ts_source TEXT,readiness_ok INTEGER,readiness_reason_codes TEXT,confirm_wait_ms REAL,commit_block_ms REAL,unsubscribe_ms REAL,unknown_msg_count INTEGER,ignored_old_rate_per_min REAL,payload_json TEXT)"
                )
                cx.execute(
                    "CREATE TABLE discovery_requests(ts_ms INTEGER,event_id TEXT PRIMARY KEY,requested_symbol TEXT,requested_horizon TEXT,mode TEXT,status TEXT,now_ms INTEGER,selected_slug TEXT,end_ts_ms INTEGER,end_ts_source TEXT,reason_code TEXT,retry_index INTEGER,next_retry_ts_ms INTEGER,counts_json TEXT,payload_json TEXT)"
                )
                cx.execute(
                    "CREATE TABLE rollover_metrics(ts_ms INTEGER,event_id TEXT PRIMARY KEY,metric_name TEXT,metric_value REAL,market_slug TEXT,selection_key TEXT,payload_json TEXT)"
                )

                cx.execute(
                    "INSERT INTO latency_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (1_000, None, None, None, None, 10.0, None, 40.0, None, None, None, None),
                )
                cx.execute(
                    "INSERT INTO latency_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (2_000, None, None, None, None, 20.0, None, 60.0, None, None, None, None),
                )
                cx.execute(
                    "INSERT INTO rollover_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        2_000,
                        "evt-1",
                        "ABORT",
                        "slug",
                        "sel",
                        "metadata",
                        0,
                        "READINESS_NOT_READY",
                        100.0,
                        None,
                        None,
                        5,
                        1.0,
                        json.dumps({"abort_reason": "CONFIRM_TIMEOUT"}, separators=(",", ":")),
                    ),
                )
                cx.execute(
                    "INSERT INTO rollover_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        2_500,
                        "evt-2",
                        "COMMIT",
                        "slug2",
                        "sel2",
                        "metadata",
                        1,
                        "",
                        50.0,
                        3.0,
                        1.0,
                        1,
                        0.2,
                        json.dumps({}, separators=(",", ":")),
                    ),
                )
                cx.execute(
                    "INSERT INTO discovery_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        1_000,
                        "disc-1",
                        "BTC",
                        "15m",
                        "latest_active",
                        "NONE_FOUND",
                        1_000,
                        None,
                        None,
                        None,
                        "NO_ACTIVE_BTC_15M",
                        0,
                        2_000,
                        "{}",
                        "{}",
                    ),
                )
                cx.execute(
                    "INSERT INTO discovery_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        4_000,
                        "disc-2",
                        "BTC",
                        "15m",
                        "latest_active",
                        "SELECTED",
                        4_000,
                        "slug2",
                        5_000,
                        "metadata",
                        None,
                        0,
                        None,
                        "{}",
                        "{}",
                    ),
                )
                cx.execute(
                    "INSERT INTO rollover_metrics VALUES (?,?,?,?,?,?,?)",
                    (3_000, "m1", "unknown_msg_count", 12.0, "slug", "sel", "{}"),
                )
                cx.execute(
                    "INSERT INTO rollover_metrics VALUES (?,?,?,?,?,?,?)",
                    (3_000, "m2", "ignored_old_rate_per_min", 1.5, "slug", "sel", "{}"),
                )
                cx.execute(
                    "INSERT INTO rollover_metrics VALUES (?,?,?,?,?,?,?)",
                    (3_000, "m3", "active_rate_per_min", 30.0, "slug", "sel", "{}"),
                )
                cx.commit()

                summary = _build_run_summary(cx, run_dir=run_dir)
            finally:
                cx.close()

        self.assertEqual(summary["event_counts"]["market:book"], 1)
        self.assertEqual(summary["event_counts"]["market:price_change"], 1)
        self.assertEqual(summary["rollover"]["commit_count"], 1)
        self.assertEqual(summary["rollover"]["abort_by_reason"], {"CONFIRM_TIMEOUT": 1})
        self.assertEqual(summary["none_found"]["streak_count"], 1)
        self.assertEqual(summary["unknown_ignored"]["unknown_rate_per_min_last"], 12.0)


if __name__ == "__main__":
    unittest.main()
