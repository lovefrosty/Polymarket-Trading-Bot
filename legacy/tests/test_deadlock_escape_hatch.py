import unittest

from core.market_rollover import MarketRolloverConfig, MarketRolloverManager, MarketState
from scripts.run_system import _rollover_commit_decision


class TestDeadlockEscapeHatch(unittest.TestCase):
    def test_escape_hatch_timing_and_decision_policy(self) -> None:
        manager = MarketRolloverManager(
            current=MarketState(
                market_slug="btc-updown-15m-1700000900",
                condition_id="c1",
                token_ids=["t1", "t2"],
                market_end_ts_ms=1_700_000_900_000,
                market_end_source="metadata",
            ),
            config=MarketRolloverConfig(grace_ms=60_000),
        )

        self.assertFalse(manager.escape_hatch_open(1_700_000_959_999))
        self.assertTrue(manager.escape_hatch_open(1_700_000_960_000))

        decision_observe_only = _rollover_commit_decision(
            now_ms=1_700_000_960_100,
            readiness_ready=False,
            escape_hatch_open=True,
            liveness_ok=True,
        )
        self.assertEqual(decision_observe_only.action, "COMMIT")
        self.assertTrue(decision_observe_only.force_observe_only)
        self.assertEqual(decision_observe_only.reason, "ESCAPE_HATCH_LIVENESS_ONLY")

        decision_retry = _rollover_commit_decision(
            now_ms=1_700_000_960_100,
            readiness_ready=False,
            escape_hatch_open=True,
            liveness_ok=False,
        )
        self.assertEqual(decision_retry.action, "RETRY")
        self.assertFalse(decision_retry.force_observe_only)
        self.assertEqual(decision_retry.reason, "ESCAPE_HATCH_NO_LIVENESS")


if __name__ == "__main__":
    unittest.main()
