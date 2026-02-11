import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard.data_access import build_drillthrough_context, require_sources


class TestDashboardSourceGuards(unittest.TestCase):
    def test_require_sources_reports_missing_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            with sqlite3.connect(db_path.as_posix()) as cx:
                cx.execute("CREATE TABLE alerts(ts_ms INTEGER)")
                cx.commit()

            ok, missing_required, missing_optional = require_sources(
                required_sources=["alerts", "decisions"],
                optional_sources=["logs"],
                db_path=db_path,
            )

        self.assertFalse(ok)
        self.assertEqual(missing_required, ["decisions"])
        self.assertEqual(missing_optional, ["logs"])

    def test_drillthrough_context_hash_is_deterministic(self) -> None:
        a = build_drillthrough_context(
            metric_key="PSTAR_AGE",
            start_ts_ms=1000,
            end_ts_ms=2000,
            market="btc-updown-15m",
            token_id="ALL",
            reason_codes=["A_STALE"],
            evidence_refs=["decisions"],
            payload={"k": 1},
        )
        b = build_drillthrough_context(
            metric_key="PSTAR_AGE",
            start_ts_ms=1000,
            end_ts_ms=2000,
            market="btc-updown-15m",
            token_id="ALL",
            reason_codes=["A_STALE"],
            evidence_refs=["decisions"],
            payload={"k": 1},
        )
        self.assertEqual(a.context_hash, b.context_hash)
        self.assertEqual(a.context_id, b.context_id)


if __name__ == "__main__":
    unittest.main()
