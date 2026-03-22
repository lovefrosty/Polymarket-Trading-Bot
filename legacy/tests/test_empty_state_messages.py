import unittest

from dashboard.app import EMPTY_STATE_MESSAGES
from dashboard.panels.reliability import EMPTY_LOG_MESSAGES


class TestEmptyStateMessages(unittest.TestCase):
    def test_trader_empty_states_are_explicit(self) -> None:
        self.assertIn("No signals right now", EMPTY_STATE_MESSAGES["signals"])
        self.assertIn("No open positions", EMPTY_STATE_MESSAGES["positions"])
        self.assertIn("No microstructure telemetry", EMPTY_STATE_MESSAGES["micro"])

    def test_log_empty_states_are_explicit(self) -> None:
        self.assertIn("No recent errors", EMPTY_LOG_MESSAGES["alerts"])
        self.assertIn("No gate breaches", EMPTY_LOG_MESSAGES["breaches"])
        self.assertIn("No warning/error logs", EMPTY_LOG_MESSAGES["logs"])


if __name__ == "__main__":
    unittest.main()
