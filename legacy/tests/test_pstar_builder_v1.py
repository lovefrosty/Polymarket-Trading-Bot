import unittest

from core.pstar import PStarBuilder


class TestPStarBuilderV1(unittest.TestCase):
    def test_invalid_when_missing_sources_and_degraded_disabled(self) -> None:
        builder = PStarBuilder(
            max_age_ms=3000,
            freeze_disagree_bps=50.0,
            allow_degraded_single_source=False,
        )
        builder.ingest("spot", "BTC", 100.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        pstar = builder.build("BTC", now_wall_ms=1500)
        self.assertFalse(pstar.valid)
        self.assertIsNone(pstar.value)

    def test_valid_with_spot_and_perp(self) -> None:
        builder = PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0)
        builder.ingest("spot", "BTC", 100.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        builder.ingest("perp", "BTC", 100.2, ts_event_ms=1100, ts_recv_wall_ms=1100)
        pstar = builder.build("BTC", now_wall_ms=2000)
        self.assertTrue(pstar.valid)
        self.assertIsNotNone(pstar.value)
        self.assertIn("spot", pstar.sources_used)
        self.assertIn("perp", pstar.sources_used)

    def test_freeze_on_extreme_disagreement(self) -> None:
        builder = PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0)
        builder.ingest("spot", "BTC", 100.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        builder.ingest("perp", "BTC", 120.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        pstar = builder.build("BTC", now_wall_ms=1200)
        self.assertFalse(pstar.valid)
        self.assertEqual(pstar.diagnostics.get("freeze_reason"), "pstar_disagreement_extreme")


if __name__ == "__main__":
    unittest.main()
