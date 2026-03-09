import unittest

from dashboard.app import build_trader_health_chips, build_trader_metric_display
from dashboard.contracts import TopBarMetrics


class TestNoNaMetricCardsTraderMode(unittest.TestCase):
    def test_no_na_values_in_trader_display(self) -> None:
        metrics = TopBarMetrics(
            mode="OBSERVE",
            is_frozen=False,
            alert_state="OK",
            freeze_reasons=[],
            readiness_state="READY",
            market_slug="btc-updown-15m-1769544900",
            token_ids=["t1", "t2"],
            time_to_window_end="08:00",
            pstar_age_current_ms=1200.0,
            pstar_age_p95_5m_ms=None,
            ws_lag_current_ms=40.0,
            ws_lag_p95_5m_ms=None,
            ack_p50_5m_ms=None,
            ack_p95_5m_ms=None,
            signal_age_p95_5m_ms=200.0,
            decisions_1h=0,
            signals_1h=0,
            cancels_1h=0,
            replaces_1h=0,
            fills_1h=0,
            rejects_1h=0,
            net_yes=0.0,
            net_no=0.0,
            net_usd_exposure=0.0,
            hedge_completeness=1.0,
        )
        chips = build_trader_health_chips(metrics, {})
        chip_text = " ".join(f"{chip['label']} {chip['state']} {chip['detail']}" for chip in chips)
        metrics_text = " ".join(value for _, value in build_trader_metric_display(metrics))
        self.assertNotIn("N/A", chip_text)
        self.assertNotIn("N/A", metrics_text)
        self.assertIn("No open positions", metrics_text)


if __name__ == "__main__":
    unittest.main()
