import unittest

from core.entry_exit_rules import PositionState
from core.hedge_policy import HedgeParams, hedge_policy


class TestHedgePolicy(unittest.TestCase):
    def test_no_position_blocks(self) -> None:
        params = HedgeParams(edge_min=0.015, tox_max=0.0008, h_min=0.0, h_max=1.0, hedge_required_vol_pct=95.0)
        result = hedge_policy(None, {"notes": {"signals": {}}}, params)
        self.assertEqual(result["hedge_ratio_target"], 0.0)
        self.assertIn("NO_POSITION", result["blockers"])

    def test_monotone_risk_reduction(self) -> None:
        params = HedgeParams(edge_min=0.015, tox_max=0.0008, h_min=0.0, h_max=1.0, hedge_required_vol_pct=95.0)
        position = PositionState(
            token_id="token",
            outcome="Up",
            side="buy",
            entry_mono_ns=0,
            entry_edge=0.02,
            size=1.0,
            notional=1.0,
        )
        decision = {"notes": {"signals": {"edge_net": 0.02, "vol_regime": 0.99, "tox_10s": 0.0}}}
        result = hedge_policy(position, decision, params)
        self.assertTrue(0.0 <= result["hedge_ratio_target"] <= 1.0)
        self.assertLessEqual(abs(result["target_hedge_notional"]), 1.0)

    def test_hedge_not_feasible(self) -> None:
        params = HedgeParams(edge_min=0.015, tox_max=0.0008, h_min=0.0, h_max=1.0, hedge_required_vol_pct=95.0)
        position = PositionState(
            token_id="token",
            outcome="Up",
            side="buy",
            entry_mono_ns=0,
            entry_edge=0.02,
            size=1.0,
            notional=10.0,
        )
        decision = {"notes": {"signals": {"edge_net": 0.02, "vol_regime": 0.99}}, "hedge_capacity": 1.0}
        result = hedge_policy(position, decision, params)
        self.assertIn("HEDGE_NOT_FEASIBLE", result["blockers"])


if __name__ == "__main__":
    unittest.main()
