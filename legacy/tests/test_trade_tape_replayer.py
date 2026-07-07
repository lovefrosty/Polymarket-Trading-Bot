from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.trade_tape import TradeTape
from core.trade_tape_replayer import TradeTapeReplayer


class TestTradeTapeReplayer(unittest.TestCase):
    def test_replays_trade_tape_into_order_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            tape = TradeTape(log_dir=str(log_dir), run_id="run")
            tape.write(
                {
                    "event_id": "evt-1",
                    "parent_event_id": None,
                    "event_type": "order_intent",
                    "order_id": "o1",
                    "client_order_id": "o1:client",
                    "asset_id": "asset",
                    "side": "buy",
                    "size": 2.0,
                    "price": 0.52,
                    "mode": "MAKE",
                    "t_decision_wall_ms": 1000,
                    "t_event_wall_ms": 1000,
                    "t_event_mono_ns": 1,
                    "as_of_ts_ms": 1000,
                }
            )
            tape.write(
                {
                    "event_id": "evt-2",
                    "parent_event_id": "evt-1",
                    "event_type": "order_submit",
                    "order_id": "o1",
                    "broker": "cli",
                    "status": "submitted",
                    "t_send_wall_ms": 1001,
                    "t_event_wall_ms": 1001,
                    "t_event_mono_ns": 2,
                    "as_of_ts_ms": 1000,
                    "raw": {"argv": ["polymarket", "order", "place"]},
                }
            )
            tape.write(
                {
                    "event_id": "evt-3",
                    "parent_event_id": "evt-2",
                    "event_type": "order_fill",
                    "order_id": "o1",
                    "asset_id": "asset",
                    "side": "buy",
                    "fill_price": 0.52,
                    "fill_size": 1.5,
                    "fees_bps": 1.0,
                    "filled_size": 1.5,
                    "remaining_size": 0.5,
                    "t_fill_wall_ms": 1002,
                    "t_event_wall_ms": 1002,
                    "t_event_mono_ns": 3,
                    "as_of_ts_ms": 1000,
                }
            )
            tape.close()

            result = TradeTapeReplayer().replay([str(log_dir / "trade_tape.jsonl")])
            self.assertEqual([event["event_type"] for event in result.events], ["order_intent", "order_submit", "order_fill"])
            order = result.order_state.orders["o1"]
            self.assertEqual(order.status, "filled")
            self.assertEqual(order.filled_qty, 1.5)
            self.assertAlmostEqual(result.order_state.net_position["asset"], 1.5)

    def test_rejects_unknown_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trade_tape.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_id": "evt-2",
                                "parent_event_id": "missing",
                                "event_type": "order_submit",
                                "order_id": "o1",
                                "broker": "cli",
                                "status": "submitted",
                                "t_send_wall_ms": 1001,
                                "t_event_wall_ms": 1001,
                                "t_event_mono_ns": 2,
                                "as_of_ts_ms": 1000,
                            }
                        )
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "trade_tape_replay_unknown_parent"):
                TradeTapeReplayer().replay([str(path)])


if __name__ == "__main__":
    unittest.main()
