import unittest

from core.metrics import classify_reliability_rows


class TestMetricsReliabilityRows(unittest.TestCase):
    def test_classify_reliability_rows_orders_by_score(self) -> None:
        rows = classify_reliability_rows(
            {
                "stable": {"ws_lag_ms": 500.0, "ack_ms": 100.0},
                "degraded": {"ws_lag_ms": 3500.0, "ack_ms": 900.0, "freeze_ratio": 0.2},
            }
        )
        self.assertEqual(rows[0].source, "degraded")
        self.assertIn(rows[0].status, {"WARN", "CRITICAL"})
        self.assertEqual(rows[-1].source, "stable")


if __name__ == "__main__":
    unittest.main()
