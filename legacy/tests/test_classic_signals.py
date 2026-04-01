from __future__ import annotations

import unittest

from core.classic_signals import ClassicSignalConfig, ClassicSignalState, DispersionFloorConfig


class TestClassicSignals(unittest.TestCase):
    def test_warmup_then_valid(self) -> None:
        state = ClassicSignalState(ClassicSignalConfig(warmup_updates=3))
        snap1 = state.update(
            as_of_ts_ms=1000,
            market_as_of_ts_ms=900,
            fair_as_of_ts_ms=800,
            p_fair=0.50,
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        snap2 = state.update(
            as_of_ts_ms=2000,
            market_as_of_ts_ms=1900,
            fair_as_of_ts_ms=1800,
            p_fair=0.50,
            best_bid=0.50,
            best_ask=0.52,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        snap3 = state.update(
            as_of_ts_ms=3000,
            market_as_of_ts_ms=2900,
            fair_as_of_ts_ms=2800,
            p_fair=0.50,
            best_bid=0.51,
            best_ask=0.53,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        self.assertFalse(snap1.valid)
        self.assertEqual(snap1.invalid_reason, "warmup")
        self.assertFalse(snap2.valid)
        self.assertTrue(snap3.valid)
        self.assertEqual(snap3.warmup_remaining, 0)

    def test_signs_follow_residual_direction(self) -> None:
        state = ClassicSignalState(ClassicSignalConfig(warmup_updates=1))
        positive = state.update(
            as_of_ts_ms=1000,
            market_as_of_ts_ms=900,
            fair_as_of_ts_ms=800,
            p_fair=0.40,
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        negative = state.update(
            as_of_ts_ms=2000,
            market_as_of_ts_ms=1900,
            fair_as_of_ts_ms=1800,
            p_fair=0.60,
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        self.assertGreaterEqual(positive.residual or 0.0, 0.0)
        self.assertLessEqual(positive.mean_reversion_score, 0.0)
        self.assertLessEqual(negative.residual or 0.0, 0.0)
        self.assertGreaterEqual(negative.mean_reversion_score, 0.0)

    def test_scores_are_bounded(self) -> None:
        state = ClassicSignalState(ClassicSignalConfig(warmup_updates=1))
        snap = None
        for idx in range(1, 10):
            snap = state.update(
                as_of_ts_ms=idx * 1000,
                market_as_of_ts_ms=idx * 1000 - 1,
                fair_as_of_ts_ms=idx * 1000 - 2,
                p_fair=0.10 if idx % 2 else 0.90,
                best_bid=0.01,
                best_ask=0.99,
                best_bid_size=10.0,
                best_ask_size=1.0,
            )
        assert snap is not None
        self.assertLessEqual(abs(snap.trend_score), 1.0)
        self.assertLessEqual(abs(snap.momentum_score), 1.0)
        self.assertLessEqual(abs(snap.mean_reversion_score), 1.0)

    def test_timestamp_regression_is_invalid_and_non_mutating(self) -> None:
        state = ClassicSignalState(ClassicSignalConfig(warmup_updates=1))
        first = state.update(
            as_of_ts_ms=1000,
            market_as_of_ts_ms=900,
            fair_as_of_ts_ms=800,
            p_fair=0.50,
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        second = state.update(
            as_of_ts_ms=999,
            market_as_of_ts_ms=998,
            fair_as_of_ts_ms=997,
            p_fair=0.60,
            best_bid=0.58,
            best_ask=0.62,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        self.assertTrue(first.valid)
        self.assertFalse(second.valid)
        self.assertEqual(second.invalid_reason, "timestamp_regression")
        self.assertIsNone(second.residual)

    def test_dispersion_floor_applies_on_flat_series(self) -> None:
        state = ClassicSignalState(
            ClassicSignalConfig(
                warmup_updates=1,
                dispersion_floor=DispersionFloorConfig(abs_floor=0.01, half_life_sec=10.0),
            )
        )
        snap = state.update(
            as_of_ts_ms=1000,
            market_as_of_ts_ms=900,
            fair_as_of_ts_ms=800,
            p_fair=0.50,
            best_bid=0.49,
            best_ask=0.51,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        self.assertTrue(snap.valid)
        self.assertTrue(snap.dispersion_floored)
        self.assertEqual(snap.dispersion, 0.01)

    def test_probability_edge_cases(self) -> None:
        state = ClassicSignalState(ClassicSignalConfig(warmup_updates=1))
        boundary = state.update(
            as_of_ts_ms=1000,
            market_as_of_ts_ms=900,
            fair_as_of_ts_ms=800,
            p_fair=1.0,
            best_bid=0.99,
            best_ask=1.0,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        invalid = state.update(
            as_of_ts_ms=2000,
            market_as_of_ts_ms=1900,
            fair_as_of_ts_ms=1800,
            p_fair=1.2,
            best_bid=0.99,
            best_ask=1.0,
            best_bid_size=10.0,
            best_ask_size=10.0,
        )
        self.assertTrue(boundary.valid)
        self.assertFalse(invalid.valid)
        self.assertEqual(invalid.invalid_reason, "probability_out_of_bounds")


if __name__ == "__main__":
    unittest.main()
