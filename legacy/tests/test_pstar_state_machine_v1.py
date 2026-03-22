import unittest

from core.pstar import PStarBuilder


class TestPStarStateMachineV1(unittest.TestCase):
    def test_reports_unavailable_when_no_sources(self) -> None:
        builder = PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0)
        pstar = builder.build("BTC", now_wall_ms=1000)
        self.assertFalse(pstar.valid)
        self.assertEqual(pstar.state, "UNAVAILABLE")

    def test_reports_warming_for_single_source_degraded_mode(self) -> None:
        builder = PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0)
        builder.ingest("spot", "BTC", 100.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        pstar = builder.build("BTC", now_wall_ms=1500)
        self.assertTrue(pstar.valid)
        self.assertEqual(pstar.state, "WARMING")

    def test_reports_diverged_on_extreme_disagreement(self) -> None:
        builder = PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0)
        builder.ingest("spot", "BTC", 100.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        builder.ingest("perp", "BTC", 120.0, ts_event_ms=1000, ts_recv_wall_ms=1000)
        pstar = builder.build("BTC", now_wall_ms=1200)
        self.assertFalse(pstar.valid)
        self.assertEqual(pstar.state, "DIVERGED")

    def test_one_second_cadence_produces_non_zero_valid_dwell(self) -> None:
        builder = PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0)
        states = []
        for second in range(0, 12):
            ts = 1000 + second * 1000
            builder.ingest("spot", "BTC", 100.0 + 0.01 * second, ts_event_ms=ts, ts_recv_wall_ms=ts)
            builder.ingest("perp", "BTC", 100.02 + 0.01 * second, ts_event_ms=ts, ts_recv_wall_ms=ts)
            pstar = builder.build("BTC", now_wall_ms=ts + 200)
            states.append(str(pstar.state))
        valid_count = sum(1 for state in states if state == "VALID")
        valid_dwell_pct = (100.0 * float(valid_count) / float(len(states))) if states else 0.0
        self.assertGreater(valid_count, 0)
        self.assertGreater(valid_dwell_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
