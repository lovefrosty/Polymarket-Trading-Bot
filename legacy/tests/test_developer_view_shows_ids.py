import unittest

import pandas as pd

from dashboard.app import build_signals_table_for_view


class TestDeveloperViewShowsIds(unittest.TestCase):
    def test_developer_view_keeps_token_and_decision_ids(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "ts": "2026-01-01T00:00:00Z",
                    "decision_id": "d1",
                    "market": "btc-updown-15m-1769544900",
                    "token_id": "0xabc123456789",
                    "action": "BUY",
                    "strategy": "mean_reversion",
                    "p_hat": 0.54,
                    "ev": 0.02,
                    "gate_result": "ALLOW",
                    "reason_codes": "",
                }
            ]
        )
        registry = {
            "btc-updown-15m-1769544900": {
                "market_label": "BTC 15m Up/Down",
                "token_to_outcome": {"0xabc123456789": "YES"},
            }
        }
        out = build_signals_table_for_view(df, "developer", registry)
        self.assertIn("token_id", out.columns)
        self.assertIn("decision_id", out.columns)


if __name__ == "__main__":
    unittest.main()
