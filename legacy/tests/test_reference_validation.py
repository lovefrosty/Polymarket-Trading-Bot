import unittest

from core.reference_price import ReferencePriceAggregator, ReferenceQuote


class TestReferenceValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.aggregator = ReferencePriceAggregator(
            required_sources={"spot", "perp"},
            staleness_ms=1000,
            disagreement_bps=10.0,
            min_confidence=0.5,
            disagreement_bps_soft=10.0,
            disagreement_bps_hard=50.0,
            disagreement_decay_k=2.0,
            allowed_symbols={"BTC"},
        )

    def _quote(
        self,
        source: str,
        value: float,
        recv_mono_ns: int,
        recv_wall_ms: int,
        t_event_ms: int = 1000,
    ) -> ReferenceQuote:
        return ReferenceQuote(
            source=source,
            symbol="BTC",
            value=value,
            t_event_ms=t_event_ms,
            t_recv_mono_ns=recv_mono_ns,
            t_recv_wall_iso="2024-01-01T00:00:00.000Z",
            t_recv_wall_ms=recv_wall_ms,
        )

    def test_missing_source(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 1000))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=1000)
        self.assertEqual(result.status, "missing_source")

    def test_stale_source(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 0))
        self.aggregator.ingest(self._quote("perp", 100.0, 1_000_000, 0))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=3_000_000_000, decision_wall_ms=2001)
        self.assertEqual(result.status, "stale")
        self.assertEqual(result.c_stale, 0.0)

    def test_disagree_soft(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 1000))
        self.aggregator.ingest(self._quote("perp", 100.1, 1_000_000, 1000))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=2000)
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.diff_bps)
        self.assertEqual(result.c_basis, 1.0)

    def test_disagree_extreme(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 1000))
        self.aggregator.ingest(self._quote("perp", 200.0, 1_000_000, 1000))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=2000)
        self.assertEqual(result.status, "basis_extreme")
        self.assertEqual(result.c_basis, 0.0)

    def test_future_leakage(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 2000))
        self.aggregator.ingest(self._quote("perp", 100.0, 1_000_000, 2000))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=1000)
        self.assertEqual(result.status, "future_leakage")

    def test_as_of_gating(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 5_000_000, 900))
        self.aggregator.ingest(self._quote("perp", 100.0, 5_000_000, 900))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=1_000_000, decision_wall_ms=1000)
        self.assertEqual(result.status, "future_leakage")

    def test_happy_path(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 1000))
        self.aggregator.ingest(self._quote("perp", 100.01, 1_000_000, 1200))
        result = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=2000)
        self.assertIsNotNone(result.price)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.pstar_asof_wall_ms, 1200)

    def test_symmetry_and_determinism(self) -> None:
        self.aggregator.ingest(self._quote("spot", 100.0, 1_000_000, 1000))
        self.aggregator.ingest(self._quote("perp", 101.0, 1_000_000, 1000))
        result_a = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=2000)
        result_b = self.aggregator.validated_price("BTC", as_of_mono_ns=2_000_000, decision_wall_ms=2000)
        self.assertEqual(result_a.diff_bps, result_b.diff_bps)
        self.assertEqual(result_a.c_basis, result_b.c_basis)


if __name__ == "__main__":
    unittest.main()
