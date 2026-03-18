import unittest
from unittest.mock import patch

import pandas as pd

from dashboard.contracts import DashboardFilters
from dashboard.panels import rollover as rollover_panel


class _DummyColumn:
    def metric(self, *_args, **_kwargs) -> None:
        return None


class _DummyStreamlit:
    def __init__(self) -> None:
        self.dataframes = 0

    def markdown(self, *_args, **_kwargs) -> None:
        return None

    def caption(self, *_args, **_kwargs) -> None:
        return None

    def columns(self, n: int):  # type: ignore[no-untyped-def]
        return [_DummyColumn() for _ in range(int(n))]

    def subheader(self, *_args, **_kwargs) -> None:
        return None

    def dataframe(self, *_args, **_kwargs) -> None:
        self.dataframes += 1


class TestDashboardRolloverTab(unittest.TestCase):
    def test_rollover_panel_renders_from_sqlite_queries(self) -> None:
        dummy_st = _DummyStreamlit()
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

        def fake_query(sql: str, params=()):  # type: ignore[no-untyped-def]
            _ = params
            if "FROM rollover_status" in sql and "LIMIT 1" in sql and "event_type, market_slug" in sql:
                return pd.DataFrame(
                    [
                        {
                            "ts_ms": 2_000,
                            "event_type": "COMMIT",
                            "market_slug": "btc-updown-15m-1",
                            "selection_key": "sel",
                            "end_ts_source": "metadata",
                            "readiness_ok": 1,
                            "readiness_reason_codes": "",
                            "confirm_wait_ms": 5.0,
                            "commit_block_ms": 2.0,
                            "unsubscribe_ms": 1.0,
                            "unknown_msg_count": 0,
                            "ignored_old_rate_per_min": 0.0,
                        }
                    ]
                )
            if "FROM rollover_status" in sql and "WHERE event_type IN ('INTENT','CONFIRM','COMMIT','ABORT','HEALTH_FREEZE','READINESS_CHECK')" in sql:
                return pd.DataFrame([{"ts_ms": 2_000, "event_type": "COMMIT"}])
            if "FROM rollover_metrics" in sql:
                return pd.DataFrame(
                    [
                        {"ts_ms": 2_000, "metric_name": "rollover_confirm_wait_ms", "metric_value": 5.0},
                        {"ts_ms": 1_900, "metric_name": "rollover_commit_block_ms", "metric_value": 2.0},
                        {"ts_ms": 1_800, "metric_name": "rollover_unsubscribe_ms", "metric_value": 1.0},
                    ]
                )
            if "FROM rollover_status" in sql and "WHERE event_type IN ('READINESS_CHECK','COMMIT','ABORT')" in sql:
                return pd.DataFrame([{"ts_ms": 2_000, "event_type": "COMMIT", "readiness_ok": 1, "readiness_reason_codes": ""}])
            if "FROM rollover_status" in sql and "unknown_msg_count" in sql:
                return pd.DataFrame([{"ts_ms": 2_000, "unknown_msg_count": 0, "ignored_old_rate_per_min": 0.0}])
            if "FROM discovery_requests" in sql:
                return pd.DataFrame(
                    [
                        {
                            "ts_ms": 2_000,
                            "status": "SELECTED",
                            "reason_code": None,
                            "retry_index": 0,
                            "next_retry_ts_ms": None,
                            "selected_slug": "btc-updown-15m-1",
                        }
                    ]
                )
            return pd.DataFrame()

        with patch.object(rollover_panel, "st", dummy_st), patch.object(
            rollover_panel, "require_sources", return_value=(True, [], [])
        ), patch.object(rollover_panel, "query_df", side_effect=fake_query):
            rollover_panel.render_rollover_panel(filters)

        self.assertGreaterEqual(dummy_st.dataframes, 5)


if __name__ == "__main__":
    unittest.main()
