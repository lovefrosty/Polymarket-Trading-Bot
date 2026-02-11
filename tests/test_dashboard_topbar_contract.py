import unittest

from dashboard.app import TopBarMetrics


class TestDashboardTopbarContract(unittest.TestCase):
    def test_topbar_fields(self) -> None:
        fields = set(TopBarMetrics.__annotations__.keys())
        required = {
            "mode",
            "is_frozen",
            "freeze_reasons",
            "market_slug",
            "token_ids",
            "time_to_window_end",
            "pstar_age_current_ms",
            "pstar_age_p95_5m_ms",
            "ws_lag_current_ms",
            "ws_lag_p95_5m_ms",
            "ack_p50_5m_ms",
            "ack_p95_5m_ms",
            "signal_age_p95_5m_ms",
            "decisions_1h",
            "signals_1h",
            "cancels_1h",
            "replaces_1h",
            "fills_1h",
            "rejects_1h",
            "net_yes",
            "net_no",
            "net_usd_exposure",
            "hedge_completeness",
        }
        self.assertTrue(required.issubset(fields))


if __name__ == "__main__":
    unittest.main()
