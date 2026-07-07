import unittest

import pandas as pd

from dashboard.panels.replay_diff import compute_replay_mismatches


class TestDashboardReplayDiffV0(unittest.TestCase):
    def test_detects_action_reason_and_p_exec_mismatch(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "decision_id": "d1",
                    "action": "BUY",
                    "reason_codes": "ALLOW",
                    "policy_json": '{"replay_action":"SELL","replay_reason_codes":"ALLOW","live_p_exec":0.51,"replay_p_exec":0.51}',
                },
                {
                    "decision_id": "d2",
                    "action": "BUY",
                    "reason_codes": "A",
                    "policy_json": '{"replay_action":"BUY","replay_reason_codes":"B","live_p_exec":0.50,"replay_p_exec":0.50}',
                },
                {
                    "decision_id": "d3",
                    "action": "BUY",
                    "reason_codes": "A",
                    "policy_json": '{"replay_action":"BUY","replay_reason_codes":"A","live_p_exec":0.50,"replay_p_exec":0.51}',
                },
                {
                    "decision_id": "d4",
                    "action": "BUY",
                    "reason_codes": "A",
                    "policy_json": '{"replay_action":"BUY","replay_reason_codes":"A","live_p_exec":0.50,"replay_p_exec":0.5001}',
                },
            ]
        )

        mismatches = compute_replay_mismatches(df, p_exec_delta_bps=5.0)
        ids = [row.decision_id for row in mismatches]
        self.assertEqual(ids, ["d1", "d2", "d3"])


if __name__ == "__main__":
    unittest.main()
