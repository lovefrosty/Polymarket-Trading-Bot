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
                        {"invalid_ratio": 0.0, "disagree_ratio": 0.0},
                    ]
                )
            if "FROM reconciliation_stats" in sql:
                return pd.DataFrame(
                    [
                        {"outside_tolerance_ratio": 0.0, "unresolved_ratio": 0.0},
                    ]
                )
            if "FROM alerts" in sql and "GROUP BY hour_ms" in sql:
                return pd.DataFrame(
                    [
                        {"hour_ms": 0, "alerts_total": 1, "freeze_related_alerts": 1},
                    ]
                )
            if "FROM system_state" in sql and "GROUP BY hour_ms" in sql:
                return pd.DataFrame(
                    [
                        {"hour_ms": 0, "frozen_samples": 1},
                    ]
                )
            if "FROM system_state" in sql and "AVG(CASE WHEN is_frozen = 1" in sql:
                return pd.DataFrame(
                    [
                        {"freeze_ratio": 1.0},
                    ]
                )
            return pd.DataFrame()

        with patch("dashboard.panels.reliability.query_df", side_effect=fake_query):
            scoreboard = compute_reliability_scoreboard()

        rows = scoreboard["rows"]
        self.assertGreater(len(rows), 0)
        self.assertEqual(rows[0]["source"], "execution_path")
        self.assertIn("freeze_trend", scoreboard)

    def test_uses_hourly_aggregate_trend_queries(self) -> None:
        seen_sql = []

        def fake_query(sql: str, params=()):
            seen_sql.append(" ".join(sql.split()))
            if "FROM latency_stats" in sql:
                return pd.DataFrame(
                    [
                        {"ts_ms": 1, "p95_ws_lag_ms": 100.0, "p95_send_ack_ms": 100.0, "p95_signal_age_ms": 100.0},
                    ]
                )
            if "FROM pstar_stats" in sql:
                return pd.DataFrame([{"invalid_ratio": 0.0, "disagree_ratio": 0.0}])
            if "FROM reconciliation_stats" in sql:
                return pd.DataFrame([{"outside_tolerance_ratio": 0.0, "unresolved_ratio": 0.0}])
            if "FROM system_state" in sql and "AVG(CASE WHEN is_frozen = 1" in sql:
                return pd.DataFrame([{"freeze_ratio": 0.0}])
            if "FROM alerts" in sql and "GROUP BY hour_ms" in sql:
                return pd.DataFrame([{"hour_ms": 0, "alerts_total": 0, "freeze_related_alerts": 0}])
            if "FROM system_state" in sql and "GROUP BY hour_ms" in sql:
                return pd.DataFrame([{"hour_ms": 0, "frozen_samples": 0}])
            return pd.DataFrame()

        with patch("dashboard.panels.reliability.query_df", side_effect=fake_query):
            scoreboard = compute_reliability_scoreboard()

        self.assertIn("rows", scoreboard)
        self.assertIn("freeze_trend", scoreboard)
        self.assertTrue(any("FROM alerts" in sql and "GROUP BY hour_ms" in sql for sql in seen_sql))
        self.assertTrue(any("FROM system_state" in sql and "GROUP BY hour_ms" in sql for sql in seen_sql))


if __name__ == "__main__":
    unittest.main()
