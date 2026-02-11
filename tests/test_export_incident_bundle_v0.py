import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_runtime_jsonl import _build_incident_bundle


class TestExportIncidentBundleV0(unittest.TestCase):
    def test_builds_manifest_and_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "runtime.db"
            out_dir = tmp_path / "export"
            with sqlite3.connect(db_path.as_posix()) as cx:
                cx.execute("CREATE TABLE logs(ts_ms INTEGER, level TEXT, msg TEXT, payload_json TEXT)")
                cx.execute(
                    "CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT, action TEXT, reason_codes TEXT, p_hat REAL, expected_edge REAL, expected_cost REAL, decision_ts_event_ms INTEGER, book_asof_ts_ms INTEGER, pstar_asof_ts_ms INTEGER, max_feature_ts_ms INTEGER, policy_json TEXT)"
                )
                cx.execute("CREATE TABLE market_data_book(ts_ms INTEGER, token_id TEXT, side TEXT, price REAL, size REAL, source TEXT)")
                cx.execute("CREATE TABLE system_state(as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
                cx.execute("INSERT INTO logs VALUES (1000,'INFO','ok','{}')")
                cx.execute("INSERT INTO decisions VALUES (1000,'d1','m','t','BUY','A',0.5,0.1,0.01,999,998,997,996,'{}')")
                cx.execute("INSERT INTO market_data_book VALUES (1000,'t','buy',0.5,1.0,'ws')")
                cx.execute("INSERT INTO system_state VALUES (1000,0,'','OBSERVE','{}')")
                cx.commit()

            manifest_path = _build_incident_bundle(
                db_path=db_path,
                out_dir=out_dir,
                start_ts_ms=900,
                end_ts_ms=1100,
                market="ALL",
                token_id="ALL",
                context_json_path=None,
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["manifest_version"], "incident_bundle_v0")
            self.assertIn("context_hash", manifest)
            self.assertTrue((manifest_path.parent / "context.json").exists())
            self.assertTrue((manifest_path.parent / "logs.jsonl").exists())
            self.assertTrue((manifest_path.parent / "decisions.jsonl").exists())
            self.assertTrue((manifest_path.parent / "book_events.jsonl").exists())
            self.assertTrue((manifest_path.parent / "system_state.json").exists())
            self.assertTrue((manifest_path.parent / "config_fingerprint.json").exists())


if __name__ == "__main__":
    unittest.main()
