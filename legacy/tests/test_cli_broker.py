from __future__ import annotations

import unittest

from core.broker_base import OrderIntent
from core.brokers.cli_broker import CLIBroker, CLIBrokerConfig


class TestCLIBroker(unittest.TestCase):
    def _intent(self) -> OrderIntent:
        return OrderIntent(
            order_id="o-1",
            client_order_id="c-1",
            asset_id="XYZ",
            side="buy",
            size=100.0,
            price=0.62,
            mode="MAKE",
            t_decision_wall_ms=1000,
            as_of_ts_ms=1000,
            decision_id="d-1",
            post_only=True,
            time_in_force="GTC",
        )

    def test_order_intent_to_cli_command(self) -> None:
        broker = CLIBroker(CLIBrokerConfig(executable="polymarket", dry_run=True))
        argv = broker.build_submit_argv(self._intent())
        self.assertEqual(
            argv,
            [
                "polymarket",
                "order",
                "place",
                "--token",
                "XYZ",
                "--price",
                "0.62",
                "--size",
                "100",
                "--side",
                "buy",
                "--json",
            ],
        )

    def test_submit_parses_ack_and_fills(self) -> None:
        broker = CLIBroker(CLIBrokerConfig(executable="polymarket", dry_run=False))
        broker._ensure_eligible = lambda intent: None  # type: ignore[method-assign]
        broker._run_json = lambda argv, timeout_secs: type(  # type: ignore[method-assign]
            "Result",
            (),
            {
                "argv": list(argv),
                "returncode": 0,
                "stdout": '{"status":"accepted"}',
                "stderr": "",
                "parsed": {
                    "status": "accepted",
                    "ts_ms": 1010,
                    "fills": [
                        {
                            "fill_event_id": "fill-1",
                            "fill_price": 0.62,
                            "fill_size": 25.0,
                            "filled_size": 25.0,
                            "remaining_size": 75.0,
                            "fees_bps": 1.0,
                            "ts_ms": 1020,
                        }
                    ],
                },
            },
        )()
        events = broker.submit(self._intent())
        self.assertEqual([event.event_type for event in events], ["order_submit", "order_ack", "order_fill"])
        self.assertEqual(events[2].payload.get("remaining_size"), 75.0)

    def test_bad_json_returns_broker_error(self) -> None:
        broker = CLIBroker(CLIBrokerConfig(executable="polymarket", dry_run=False))
        broker._ensure_eligible = lambda intent: None  # type: ignore[method-assign]
        broker._run_json = lambda argv, timeout_secs: type(  # type: ignore[method-assign]
            "Result",
            (),
            {
                "argv": list(argv),
                "returncode": 0,
                "stdout": "not-json",
                "stderr": "",
                "parsed": None,
            },
        )()
        events = broker.submit(self._intent())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "broker_error")
        self.assertIn("BAD_JSON", str(events[0].payload.get("error_code")))


if __name__ == "__main__":
    unittest.main()
