import unittest

from dashboard.app import build_trader_health_chips, format_trader_reason
from dashboard.contracts import HealthGateStatus, TopBarMetrics


def _metrics(**overrides):
    base = TopBarMetrics(
        mode="OBSERVE",
        is_frozen=False,
        alert_state="DEGRADED",
        freeze_reasons=["C_SPREAD_TOO_WIDE"],
        readiness_state="READY",
        market_slug="btc-updown-15m-1769544900",
        token_ids=["t1", "t2"],
        time_to_window_end="08:32",
        pstar_age_current_ms=1314.0,
        pstar_age_p95_5m_ms=2000.0,
        ws_lag_current_ms=13.0,
        ws_lag_p95_5m_ms=126.0,
        ack_p50_5m_ms=None,
        ack_p95_5m_ms=None,
        signal_age_p95_5m_ms=110.0,
        decisions_1h=5,
        signals_1h=2,
        cancels_1h=0,
        replaces_1h=0,
        fills_1h=0,
        rejects_1h=0,
        net_yes=0.0,
        net_no=0.0,
        net_usd_exposure=0.0,
        hedge_completeness=1.0,
    )
    return base.__class__(**{**base.__dict__, **overrides})


class TestTraderTopbarPlainEnglish(unittest.TestCase):
    def test_reason_translation_is_plain_english(self) -> None:
        rendered = format_trader_reason(
            code="C_SPREAD_TOO_WIDE",
            payload={"spread_bps": 202.0, "max_bps": 150.0},
            message=None,
        )
        self.assertIn("Spread too wide", rendered)
        self.assertNotIn("C_SPREAD_TOO_WIDE", rendered)
        self.assertIn("202.0", rendered)

    def test_health_chip_labels_are_non_technical(self) -> None:
        gate_map = {
            "E": HealthGateStatus(gate="E", status="WARN", summary="", details={}),
        }
        chips = build_trader_health_chips(_metrics(ack_p95_5m_ms=150.0), gate_map)
        labels = {chip["label"] for chip in chips}
        self.assertIn("Price Feed", labels)
        self.assertIn("Connection", labels)
        self.assertIn("Execution Path", labels)
        for label in labels:
            self.assertNotIn("_ms", label.lower())


if __name__ == "__main__":
    unittest.main()
