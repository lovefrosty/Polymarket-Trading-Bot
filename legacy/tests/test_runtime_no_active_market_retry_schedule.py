import unittest

from scripts.run_system import (
    _discovery_effective_next_retry_ts_ms,
    _discovery_none_found_retry_delay_ms,
)


class TestRuntimeNoActiveMarketRetrySchedule(unittest.TestCase):
    def test_retry_schedule_is_deterministic_and_capped(self) -> None:
        self.assertEqual(_discovery_none_found_retry_delay_ms(0), 1_000)
        self.assertEqual(_discovery_none_found_retry_delay_ms(1), 2_000)
        self.assertEqual(_discovery_none_found_retry_delay_ms(2), 5_000)
        self.assertEqual(_discovery_none_found_retry_delay_ms(3), 10_000)
        self.assertEqual(_discovery_none_found_retry_delay_ms(10), 10_000)

    def test_effective_retry_respects_discovery_throttle(self) -> None:
        now_ms = 1_000_000
        self.assertEqual(
            _discovery_effective_next_retry_ts_ms(now_ms, retry_index=0, discovery_period_ms=30_000),
            now_ms + 30_000,
        )
        self.assertEqual(
            _discovery_effective_next_retry_ts_ms(now_ms, retry_index=0, discovery_period_ms=500),
            now_ms + 1_000,
        )


if __name__ == "__main__":
    unittest.main()
