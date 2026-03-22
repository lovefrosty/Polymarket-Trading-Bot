from __future__ import annotations

import unittest

from core.order_state import rebuild_order_state


class TestOrderState(unittest.TestCase):
    def test_order_state_replay(self) -> None:
        events = [
            {
                "event_type": "order_intent",
                "order_id": "o1",
                "asset_id": "asset",
                "side": "buy",
            },
            {
                "event_type": "order_fill",
                "order_id": "o1",
                "fill_price": 0.4,
                "fill_size": 2.0,
                "fees_bps": 100.0,
                "t_fill_wall_ms": 1000,
            },
            {
                "event_type": "order_fill",
                "order_id": "o1",
                "fill_price": 0.4,
                "fill_size": 2.0,
                "fees_bps": 100.0,
                "t_fill_wall_ms": 1000,
            },
            {
                "event_type": "order_fill",
                "order_id": "o1",
                "fill_price": 0.6,
                "fill_size": 1.0,
                "fees_bps": 100.0,
                "t_fill_wall_ms": 2000,
            },
        ]
        snapshot = rebuild_order_state(events)
        summary = snapshot.orders["o1"]
        self.assertAlmostEqual(summary.filled_qty, 3.0)
        expected_avg = (0.4 * 2.0 + 0.6 * 1.0) / 3.0
        self.assertAlmostEqual(summary.avg_fill_price, expected_avg)
        expected_fees = 0.4 * 2.0 * 0.01 + 0.6 * 1.0 * 0.01
        self.assertAlmostEqual(summary.fees_paid, expected_fees)
        self.assertAlmostEqual(snapshot.net_position["asset"], 3.0)
