import unittest

from core.feature_builder import FeatureConfig, ReferenceFeatureState, build_feature_vector
from core.reference_price import ReferenceQuote


class TestNoLeakageFeatureBuilder(unittest.TestCase):
    def test_feature_builder_uses_only_past_event_time(self) -> None:
        state = ReferenceFeatureState(max_history_ms=1_000_000)
        quotes = [
            ReferenceQuote("spot", "BTC", 100.0, 0, 0, ""),
            ReferenceQuote("spot", "BTC", 105.0, 60_000, 0, ""),
            ReferenceQuote("spot", "BTC", 110.0, 300_000, 0, ""),
            ReferenceQuote("spot", "BTC", 120.0, 899_000, 0, ""),
            ReferenceQuote("spot", "BTC", 130.0, 959_000, 0, ""),
            ReferenceQuote("spot", "BTC", 150.0, 960_000, 0, ""),
        ]
        for quote in quotes:
            state.ingest(quote)

        cfg = FeatureConfig(
            lookbacks={"ret_60s": 60, "ret_300s": 300, "ret_900s": 900},
            ewma_halflife_secs=300,
            clip_sigma=8.0,
            ewma_window_secs=900,
            max_history_ms=1_000_000,
        )
        order, vector = build_feature_vector({"t_decision_wall_ms": 960_000}, state, cfg)
        self.assertEqual(order[0], "ret_60s")
        ret_60s = vector[0]
        expected = 0.0
        # price at 959_000 vs price at 899_000; event at 960_000 must be excluded
        import math

        expected = math.log(130.0 / 120.0)
        self.assertAlmostEqual(ret_60s, expected, places=12)


if __name__ == "__main__":
    unittest.main()
