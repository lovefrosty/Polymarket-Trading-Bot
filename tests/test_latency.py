import unittest

from src.risk.gates import LatencyMetrics, LatencyThresholds, gate_latency


class TestLatency(unittest.TestCase):
    def test_rejects_signal_age(self) -> None:
        metrics = LatencyMetrics(
            signal_ts=0,
            order_send_ts=200,
            ack_ts=210,
            fill_ts=220,
            p95_ack_ms=50.0,
            ws_lag_ms=10.0,
        )
        thresholds = LatencyThresholds(max_signal_age_ms=100, max_p95_ack_ms=100.0, max_ws_lag_ms=100.0)
        gate = gate_latency(metrics, thresholds)
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.reason, "signal_age_exceeded")

    def test_rejects_ack_latency(self) -> None:
        metrics = LatencyMetrics(
            signal_ts=0,
            order_send_ts=50,
            ack_ts=60,
            fill_ts=70,
            p95_ack_ms=150.0,
            ws_lag_ms=10.0,
        )
        thresholds = LatencyThresholds(max_signal_age_ms=100, max_p95_ack_ms=100.0, max_ws_lag_ms=100.0)
        gate = gate_latency(metrics, thresholds)
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.reason, "ack_latency_exceeded")

    def test_rejects_invalid_timestamp_ordering(self) -> None:
        metrics = LatencyMetrics(
            signal_ts=100,
            order_send_ts=90,
            ack_ts=95,
            fill_ts=110,
            p95_ack_ms=50.0,
            ws_lag_ms=10.0,
        )
        thresholds = LatencyThresholds(max_signal_age_ms=100, max_p95_ack_ms=100.0, max_ws_lag_ms=100.0)
        gate = gate_latency(metrics, thresholds)
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.reason, "latency_timestamp_ordering_invalid")


if __name__ == "__main__":
    unittest.main()
