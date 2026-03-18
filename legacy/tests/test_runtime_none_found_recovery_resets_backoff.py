import unittest

from scripts.run_system import _discovery_none_found_retry_delay_ms


class TestRuntimeNoneFoundRecoveryResetsBackoff(unittest.TestCase):
    def test_recovery_reset_restores_initial_delay(self) -> None:
        capped_delay = _discovery_none_found_retry_delay_ms(6)
        self.assertEqual(capped_delay, 10_000)

        reset_delay = _discovery_none_found_retry_delay_ms(0)
        self.assertEqual(reset_delay, 1_000)


if __name__ == "__main__":
    unittest.main()
