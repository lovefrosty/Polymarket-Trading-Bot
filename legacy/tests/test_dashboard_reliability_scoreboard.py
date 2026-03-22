import unittest
from unittest.mock import patch

import pandas as pd

from dashboard.panels.reliability import compute_reliability_scoreboard


class TestDashboardReliabilityScoreboard(unittest.TestCase):
    def test_identifies_top_degradation_source(self) -> None:
        def fake_query(sql: str, params=()):
            if "FROM latency_stats" in sql:
                return pd.DataFrame(
                    [
                        {"ts_ms": 1, "p95_ws_lag_ms": 3000.0, "p95_send_ack_ms": 1200.0, "p95_signal_age_ms": 2500.0},
                        {"ts_ms": 2, "p95_ws_lag_ms": 2800.0, "p95_send_ack_ms": 1100.0, "p95_signal_age_ms": 2300.0},
                    ]
                )
            if "FROM pstar_stats" in sql:
                return pd.DataFrame(
                    [
                        {"ts_ms": 1, "disagreement_bps": 20.0, "valid": 1},
                        {"ts_ms": 2, "disagreement_bps": 25.0, "valid": 1},
                    ]
                )
            if "FROM reconciliation_stats" in sql:
                return pd.DataFrame(
                    [
                        {"ts_ms": 1, "outside_tolerance": 0, "unresolved_mismatch_count": 0},
                    ]
                )
            if "FROM alerts" in sql:
                return pd.DataFrame(
                    [
                        {"ts_ms": 1, "severity": "critical", "code": "E_LATENCY"},
                    ]
                )
            if "FROM system_state" in sql:
                return pd.DataFrame(
                    [
                        {"as_of_ts": 1, "is_frozen": 1, "reasons": "E_LATENCY"},
                    ]
                )
            return pd.DataFrame()

        with patch("dashboard.panels.reliability.query_df", side_effect=fake_query):
            scoreboard = compute_reliability_scoreboard()

        rows = scoreboard["rows"]
        self.assertGreater(len(rows), 0)
        self.assertEqual(rows[0]["source"], "execution_path")
        self.assertIn("freeze_trend", scoreboard)


if __name__ == "__main__":
    unittest.main()
