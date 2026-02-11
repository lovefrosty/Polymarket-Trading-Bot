import unittest

from scripts.run_system import _should_emit_unknown_ws_alert


class TestUnknownWsAlertGuard(unittest.TestCase):
    def test_suppresses_in_observe_mode(self) -> None:
        self.assertFalse(
            _should_emit_unknown_ws_alert(
                mode="OBSERVE",
                now_ms=1_000_000,
                run_epoch_ms=0,
                unknown_rate_per_min=500.0,
                active_rate_per_min=10.0,
                threshold_per_min=120,
                min_ratio_vs_active=2.0,
                startup_grace_ms=180_000,
            )
        )

    def test_requires_startup_grace_and_ratio(self) -> None:
        self.assertFalse(
            _should_emit_unknown_ws_alert(
                mode="PAPER",
                now_ms=100_000,
                run_epoch_ms=0,
                unknown_rate_per_min=500.0,
                active_rate_per_min=100.0,
                threshold_per_min=120,
                min_ratio_vs_active=2.0,
                startup_grace_ms=180_000,
            )
        )
        self.assertFalse(
            _should_emit_unknown_ws_alert(
                mode="PAPER",
                now_ms=500_000,
                run_epoch_ms=0,
                unknown_rate_per_min=180.0,
                active_rate_per_min=120.0,
                threshold_per_min=120,
                min_ratio_vs_active=2.0,
                startup_grace_ms=180_000,
            )
        )
        self.assertTrue(
            _should_emit_unknown_ws_alert(
                mode="PAPER",
                now_ms=500_000,
                run_epoch_ms=0,
                unknown_rate_per_min=300.0,
                active_rate_per_min=100.0,
                threshold_per_min=120,
                min_ratio_vs_active=2.0,
                startup_grace_ms=180_000,
            )
        )


if __name__ == "__main__":
    unittest.main()
