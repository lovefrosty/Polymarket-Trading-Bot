import unittest

from scripts.run_system import _discovery_none_found_deadline_exceeded


class TestRuntimeNoActiveMarketDeadlineAlert(unittest.TestCase):
    def test_deadline_alert_threshold_is_deterministic(self) -> None:
        start_ts_ms = 10_000
        deadline_ms = 180_000
        self.assertFalse(
            _discovery_none_found_deadline_exceeded(start_ts_ms=start_ts_ms, now_ms=189_999, deadline_ms=deadline_ms)
        )
        self.assertTrue(
            _discovery_none_found_deadline_exceeded(start_ts_ms=start_ts_ms, now_ms=190_000, deadline_ms=deadline_ms)
        )

    def test_deadline_without_start_is_never_exceeded(self) -> None:
        self.assertFalse(
            _discovery_none_found_deadline_exceeded(start_ts_ms=None, now_ms=1_000_000, deadline_ms=180_000)
        )


if __name__ == "__main__":
    unittest.main()
