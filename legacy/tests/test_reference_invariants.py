import unittest

from core.reference_store import ReferenceStore


class TestReferenceInvariants(unittest.TestCase):
    def test_event_after_recv_flagged(self) -> None:
        store = ReferenceStore()
        record = {
            "market": "BTC",
            "t_event_ms": 2000,
            "t_recv_wall_ms": 1000,
            "t_recv_mono_ns": 100,
            "t_recv_wall_iso": "2024-01-01T00:00:01.000Z",
            "raw": {"symbol": "BTC", "mid": 100.0, "value": 100.0},
        }
        result = store.ingest_record(record)
        self.assertTrue(result.event_after_recv)
        asof = store.asof("BTC", decision_ts_ms=3000, lag_guard_ms=0, staleness_ms=5000)
        self.assertIn("REF_MISSING", asof.blockers)

    def test_recv_mono_regression(self) -> None:
        store = ReferenceStore()
        record_a = {
            "market": "BTC",
            "t_event_ms": 1000,
            "t_recv_wall_ms": 1000,
            "t_recv_mono_ns": 200,
            "t_recv_wall_iso": "2024-01-01T00:00:01.000Z",
            "raw": {"symbol": "BTC", "mid": 100.0, "value": 100.0},
        }
        record_b = {
            "market": "BTC",
            "t_event_ms": 1100,
            "t_recv_wall_ms": 1100,
            "t_recv_mono_ns": 100,
            "t_recv_wall_iso": "2024-01-01T00:00:01.100Z",
            "raw": {"symbol": "BTC", "mid": 101.0, "value": 101.0},
        }
        res_a = store.ingest_record(record_a)
        self.assertFalse(res_a.recv_out_of_order)
        res_b = store.ingest_record(record_b)
        self.assertTrue(res_b.recv_out_of_order)
        asof = store.asof("BTC", decision_ts_ms=1200, lag_guard_ms=0, staleness_ms=5000)
        self.assertEqual(asof.mid, 100.0)


if __name__ == "__main__":
    unittest.main()
