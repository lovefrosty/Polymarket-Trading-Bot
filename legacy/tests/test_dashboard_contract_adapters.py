import unittest

import pandas as pd

from dashboard.app import adapt_decisions, adapt_fills, adapt_orders


class TestDashboardContractAdapters(unittest.TestCase):
    def test_adapt_decisions_derives_ev_and_signal_and_strategy(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "ts_ms": 1000,
                    "action": "BUY",
                    "expected_edge": 0.12,
                    "expected_cost": 0.02,
                    "policy_json": '{"strategy":"mean_reversion"}',
                },
                {
                    "ts_ms": 2000,
                    "action": "FREEZE",
                    "expected_edge": 0.0,
                    "expected_cost": 0.0,
                    "policy_json": "{}",
                },
            ]
        )
        out = adapt_decisions(df)
        self.assertAlmostEqual(float(out.iloc[0]["ev"]), 0.10)
        self.assertEqual(str(out.iloc[0]["strategy"]), "mean_reversion")
        self.assertEqual(str(out.iloc[0]["gate_result"]), "ALLOW")
        self.assertTrue(bool(out.iloc[0]["is_signal"]))
        self.assertEqual(str(out.iloc[1]["gate_result"]), "FREEZE")
        self.assertFalse(bool(out.iloc[1]["is_signal"]))

    def test_adapt_orders_classifies_kinds(self) -> None:
        df = pd.DataFrame(
            [
                {"status": "cancelled", "reason": "", "fsm_state": ""},
                {"status": "new", "reason": "", "fsm_state": "replace_quote"},
                {"status": "rejected", "reason": "risk_reject", "fsm_state": ""},
            ]
        )
        out = adapt_orders(df)
        self.assertEqual(list(out["event_kind"]), ["cancel", "replace", "reject"])

    def test_adapt_fills_marks_hedge(self) -> None:
        df = pd.DataFrame(
            [
                {"payload_json": '{"is_hedge":true}'},
                {"payload_json": '{"note":"primary fill"}'},
            ]
        )
        out = adapt_fills(df)
        self.assertEqual(list(out["is_hedge"]), [True, False])


if __name__ == "__main__":
    unittest.main()
