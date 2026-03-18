from __future__ import annotations

import unittest

from scripts.paper_soak_gate_monitor import compute_soak_gate_status


class TestPaperSoakGateMonitor(unittest.TestCase):
    def test_stale_db_forces_failed_stale_runtime(self) -> None:
        status = compute_soak_gate_status(
            now_ms=1_000_000,
            counts={"orders": 10, "quote": 5, "rollover_abort_discovery_error": 0, "rollover_health_freeze": 0},
            ages_ms={"book_age_ms": 6_000, "decision_age_ms": 100, "log_age_ms": 100},
            reference_ages_ms={"spot": 100, "perp": 100},
            last_counts={"orders": 9, "quote": 4, "rollover_abort_discovery_error": 0, "rollover_health_freeze": 0},
            clean_window_started_ts_ms=900_000,
        )
        self.assertEqual(status["status"], "failed_stale_runtime")
        self.assertEqual(status["blocking_reason"], "DB_TRUTH_STALE_OVERRIDES_MONITOR")
        self.assertTrue(status["commit_blocked"])

    def test_fresh_runtime_can_enter_clean_window(self) -> None:
        status = compute_soak_gate_status(
            now_ms=1_000_000,
            counts={"orders": 10, "quote": 5, "rollover_abort_discovery_error": 0, "rollover_health_freeze": 0},
            ages_ms={"book_age_ms": 100, "decision_age_ms": 100, "log_age_ms": 100},
            reference_ages_ms={"spot": 100, "perp": 100},
            last_counts={"orders": 9, "quote": 4, "rollover_abort_discovery_error": 0, "rollover_health_freeze": 0},
            clean_window_started_ts_ms=None,
            clean_window_target_ms=10_000,
        )
        self.assertEqual(status["status"], "clean_window_active")
        self.assertEqual(status["blocking_reason"], "WAITING_FOR_6H_CLEAN_WINDOW")
        self.assertTrue(status["commit_blocked"])


if __name__ == "__main__":
    unittest.main()
