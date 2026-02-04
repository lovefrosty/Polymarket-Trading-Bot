import unittest

from src.data.reference_price import PriceSource, ReferencePriceValidator


class TestReferencePrice(unittest.TestCase):
    def test_default_sources_require_spot_and_perp(self) -> None:
        validator = ReferencePriceValidator(
            staleness_ms=1000,
            disagreement_bps=10.0,
            min_confidence=0.5,
        )
        result = validator.validate([PriceSource("spot", 100.0, 0)], decision_ts=100)
        self.assertIsNone(result.price)
        self.assertIn("missing_sources", result.freeze_reason or "")

    def test_missing_source_freezes(self) -> None:
        validator = ReferencePriceValidator(
            required_sources={"spot", "perp"},
            staleness_ms=1000,
            disagreement_bps=10.0,
            min_confidence=0.5,
        )
        result = validator.validate([PriceSource("spot", 100.0, 0)], decision_ts=100)
        self.assertIsNone(result.price)
        self.assertIn("missing_sources", result.freeze_reason or "")

    def test_spot_perp_disagreement_scales_confidence(self) -> None:
        validator = ReferencePriceValidator(
            required_sources={"spot", "perp"},
            staleness_ms=1000,
            disagreement_bps=5.0,
            min_confidence=0.5,
            disagreement_bps_soft=5.0,
            disagreement_bps_hard=50.0,
            disagreement_decay_k=2.0,
        )
        sources = [
            PriceSource("spot", 100.0, 0),
            PriceSource("perp", 100.4, 0),
        ]
        result = validator.validate(sources, decision_ts=100)
        self.assertIsNotNone(result.price)
        self.assertIsNone(result.freeze_reason)
        self.assertIsNotNone(result.diff_bps)
        self.assertIsNotNone(result.disagreement_multiplier)
        self.assertLess(result.disagreement_multiplier or 1.0, 1.0)

    def test_spot_perp_disagreement_extreme_freezes(self) -> None:
        validator = ReferencePriceValidator(
            required_sources={"spot", "perp"},
            staleness_ms=1000,
            disagreement_bps=5.0,
            min_confidence=0.5,
            disagreement_bps_soft=5.0,
            disagreement_bps_hard=10.0,
            disagreement_decay_k=2.0,
        )
        sources = [
            PriceSource("spot", 100.0, 0),
            PriceSource("perp", 200.0, 0),
        ]
        result = validator.validate(sources, decision_ts=100)
        self.assertIsNone(result.price)
        self.assertEqual(result.freeze_reason, "pstar_disagreement_extreme")

    def test_stale_source_freezes(self) -> None:
        validator = ReferencePriceValidator(
            required_sources={"spot", "perp"},
            staleness_ms=10,
            disagreement_bps=100.0,
            min_confidence=0.5,
        )
        sources = [
            PriceSource("spot", 100.0, 0),
            PriceSource("perp", 100.0, 0),
        ]
        result = validator.validate(sources, decision_ts=100)
        self.assertIsNone(result.price)
        self.assertTrue((result.freeze_reason or "").startswith("stale_source:"))

    def test_future_dated_source_freezes(self) -> None:
        validator = ReferencePriceValidator(
            required_sources={"spot", "perp"},
            staleness_ms=1000,
            disagreement_bps=10.0,
            min_confidence=0.5,
        )
        sources = [
            PriceSource("spot", 100.0, 200),
            PriceSource("perp", 100.0, 90),
        ]
        result = validator.validate(sources, decision_ts=100)
        self.assertIsNone(result.price)
        self.assertEqual(result.freeze_reason, "reference_price_from_future")


if __name__ == "__main__":
    unittest.main()
