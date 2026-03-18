import unittest

from core.entry_exit_rules import EntryExitParams, PositionState, entry_gate, exit_gate


class TestEntryExitRules(unittest.TestCase):
    def test_entry_gate_allows(self) -> None:
        params = EntryExitParams(
            edge_min=0.015,
            edge_exit=0.00375,
            edge_stop=0.0075,
            z_mom_min=1.0,
            t_min_secs=90.0,
            hold_max_secs=480.0,
            vol_pct_hi=95.0,
            edge_min_mult_hivol=1.5,
        )
        decision = {
            "token_id": "token",
            "outcome": "Up",
            "p_fair": 0.55,
            "p_market_exec_buy": 0.5,
            "p_market_exec_sell": 0.48,
            "p_star": {"freeze_reason": None},
            "gates": {"allow": True, "reasons": []},
            "notes": {"signals": {"z_mom": 1.2, "time_remaining_sec": 120, "vol_regime": 0.5}},
        }
        result = entry_gate(decision, params)
        self.assertTrue(result["allow"])

    def test_entry_gate_high_vol_requires_more_edge(self) -> None:
        params = EntryExitParams(
            edge_min=0.015,
            edge_exit=0.00375,
            edge_stop=0.0075,
            z_mom_min=1.0,
            t_min_secs=90.0,
            hold_max_secs=480.0,
            vol_pct_hi=95.0,
            edge_min_mult_hivol=1.5,
        )
        decision = {
            "token_id": "token",
            "outcome": "Up",
            "p_fair": 0.52,
            "p_market_exec_buy": 0.5,
            "p_market_exec_sell": 0.48,
            "p_star": {"freeze_reason": None},
            "gates": {"allow": True, "reasons": []},
            "notes": {"signals": {"z_mom": 1.5, "time_remaining_sec": 120, "vol_regime": 0.99}},
        }
        result = entry_gate(decision, params)
        self.assertFalse(result["allow"])
        self.assertIn("EDGE_TOO_SMALL", result["reasons"])

    def test_entry_alignment_mismatch(self) -> None:
        params = EntryExitParams(
            edge_min=0.015,
            edge_exit=0.00375,
            edge_stop=0.0075,
            z_mom_min=1.0,
            t_min_secs=90.0,
            hold_max_secs=480.0,
            vol_pct_hi=95.0,
            edge_min_mult_hivol=1.5,
        )
        decision = {
            "token_id": "token",
            "outcome": "Down",
            "p_fair": 0.6,
            "p_market_exec_buy": 0.5,
            "p_market_exec_sell": 0.48,
            "p_star": {"freeze_reason": None},
            "gates": {"allow": True, "reasons": []},
            "notes": {"signals": {"z_mom": 1.2, "time_remaining_sec": 120, "vol_regime": 0.5}},
        }
        result = entry_gate(decision, params)
        self.assertIn("ALIGNMENT_MISMATCH", result["reasons"])

    def test_exit_gate_edge_collapse(self) -> None:
        params = EntryExitParams(
            edge_min=0.015,
            edge_exit=0.00375,
            edge_stop=0.0075,
            z_mom_min=1.0,
            t_min_secs=90.0,
            hold_max_secs=480.0,
            vol_pct_hi=95.0,
            edge_min_mult_hivol=1.5,
        )
        position = PositionState(
            token_id="token",
            outcome="Up",
            side="buy",
            entry_mono_ns=0,
            entry_edge=0.02,
            size=1.0,
            notional=1.0,
        )
        decision = {
            "p_fair": 0.51,
            "p_market_exec_sell": 0.509,
            "t_decision_mono_ns": 100_000_000_000,
        }
        result = exit_gate(position, decision, params)
        self.assertTrue(result["should_exit"])
        self.assertEqual(result["reason"], "EDGE_COLLAPSE")


if __name__ == "__main__":
    unittest.main()
