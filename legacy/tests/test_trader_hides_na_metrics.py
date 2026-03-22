import unittest

from dashboard.app import build_trader_health_chips
from dashboard.contracts import TopBarMetrics


class TestTraderHidesNaMetrics(unittest.TestCase):
    def test_trader_chips_do_not_emit_na_strings(self) -> None:
        metrics = TopBarMetrics(
            mode="OBSERVE",
            is_frozen=False,
            alert_state="OK",
            freeze_reasons=[],
            readiness_state="READY",
            market_slug="btc-updown-15m-1769544900",
            token_ids=["t1", "t2"],
            time_to_window_end="08:32",
            pstar_age_current_ms=1200.0,
            pstar_age_p95_5m_ms=None,
            ws_lag_current_ms=40.0,
            ws_lag_p95_5m_ms=None,
            ack_p50_5m_ms=None,
            ack_p95_5m_ms=None,
            signal_age_p95_5m_ms=100.0,
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
        rendered = " ".join(f"{c.get('label')} {c.get('state')} {c.get('detail')}" for c in chips)
        self.assertNotIn("N/A", rendered)


if __name__ == "__main__":
    unittest.main()
