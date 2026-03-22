import unittest

import pandas as pd

from dashboard.app import _build_label_registry_from_records, label_token


class TestLabelMapperStability(unittest.TestCase):
    def test_registry_uses_resolved_mapping_deterministically(self) -> None:
        resolved_rows = [
            {
                "market_slug": "btc-updown-15m-1769544900",
                "token_ids": ["t_down", "t_up"],
                "outcomes": ["Down", "Up"],
                "outcome_by_token": {"t_down": "Down", "t_up": "Up"},
            }
        ]
        discovery_rows = [{"payload_json": '{"selected_slug":"btc-updown-15m-1769544900","selected_clobTokenIds":["t_down","t_up"]}'}]
        decision_rows = pd.DataFrame(
            [
                {"market": "btc-updown-15m-1769544900", "token_id": "t_down", "policy_json": "{}"},
                {"market": "btc-updown-15m-1769544900", "token_id": "t_up", "policy_json": "{}"},
            ]
        )
        registry = _build_label_registry_from_records(resolved_rows, discovery_rows, decision_rows)
        down = label_token(registry, "btc-updown-15m-1769544900", "t_down")
        up = label_token(registry, "btc-updown-15m-1769544900", "t_up")
        self.assertEqual(down["market_label"], "BTC 15m Up/Down")
        self.assertEqual(down["outcome_label"], "NO")
        self.assertEqual(up["outcome_label"], "YES")

    def test_registry_falls_back_to_outcome_a_b(self) -> None:
        registry = _build_label_registry_from_records(
            resolved_rows=[],
            discovery_payload_rows=[],
            decision_rows=pd.DataFrame(
                [
                    {"market": "btc-updown-15m-1769544900", "token_id": "x1", "policy_json": "{}"},
                    {"market": "btc-updown-15m-1769544900", "token_id": "x2", "policy_json": "{}"},
                ]
            ),
        )
        one = label_token(registry, "btc-updown-15m-1769544900", "x1")
        two = label_token(registry, "btc-updown-15m-1769544900", "x2")
        self.assertEqual(one["outcome_label"], "Outcome A")
        self.assertEqual(two["outcome_label"], "Outcome B")


if __name__ == "__main__":
    unittest.main()
