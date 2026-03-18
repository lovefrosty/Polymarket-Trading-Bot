import unittest
from unittest.mock import patch

import pandas as pd

from dashboard.app import DashboardFilters, compute_health_a_to_e


class TestDashboardHealthTabs(unittest.TestCase):
    def test_compute_health_a_to_e_deterministic(self) -> None:
        filters = DashboardFilters(
            lookback_rows=200,
            window_minutes=60,
            selected_market="ALL",
            selected_token="ALL",
            severity_filter="ALL",
            positive_ev_only=False,
            allow_only=False,
            strategy_filter="",
        )

        def fake_query(sql: str, params=()):
            if "FROM pstar_stats" in sql:
                return pd.DataFrame(
                    [
                        {
                            "ts_ms": 1000,
                            "symbol": "BTC",
                            "disagreement_bps": 120.0,
                            "confidence": 0.2,
                            "age_spot_ms": 7000,
                            "age_perp_ms": 5000,
                            "valid": 0,
                        }
                    ]
                )
            if "FROM decisions" in sql and "max_feature_ts_ms" in sql:
                return pd.DataFrame(
                    [{"ts_ms": 2000, "max_feature_ts_ms": 2000, "decision_ts_event_ms": 1999}]
                )
            if "FROM book_health_stats" in sql:
                return pd.DataFrame(
                    [{"token_id": "t1", "book_health_state": "DOWN", "book_age_p95_ms": 9000}]
                )
            if "FROM alerts" in sql and "ONE_LEG" in sql:
                return pd.DataFrame([{"n": 1}])
            if "FROM fills" in sql:
                return pd.DataFrame([{"ts_ms": 3000, "payload_json": '{"is_hedge": false}'}])
            if "FROM latency_stats" in sql:
                return pd.DataFrame(
                    [{"ts_ms": 4000, "p95_ws_lag_ms": 6000.0, "p95_send_ack_ms": 1200.0, "p95_signal_age_ms": 7000.0}]
                )
            if "FROM decision_ticks" in sql:
                return pd.DataFrame([{"decision_ts_ms": 1000}, {"decision_ts_ms": 1500}, {"decision_ts_ms": 3200}])
            return pd.DataFrame()

        with patch("dashboard.app.query_df", side_effect=fake_query):
            status = compute_health_a_to_e(filters)

        self.assertEqual(status["A"].status, "CRITICAL")
        self.assertEqual(status["B"].status, "CRITICAL")
        self.assertEqual(status["C"].status, "CRITICAL")
        self.assertEqual(status["D"].status, "CRITICAL")
        self.assertEqual(status["E"].status, "CRITICAL")


if __name__ == "__main__":
    unittest.main()
