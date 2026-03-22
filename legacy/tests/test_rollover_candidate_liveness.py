import unittest

from scripts.run_system import _candidate_liveness


class TestRolloverCandidateLiveness(unittest.TestCase):
    def test_tradable_candidate_is_live(self) -> None:
        asset_meta = {
            "a1": {"active": True, "closed": False, "accepting_orders": True},
            "a2": {"active": True, "closed": False, "accepting_orders": True},
        }
        ok, state, details = _candidate_liveness(
            asset_meta=asset_meta,
            token_ids=["a1", "a2"],
            now_ms=1_000,
            market_end_ts_ms=2_000,
        )
        self.assertTrue(ok)
        self.assertEqual(state, "CANDIDATE_LIVE")
        self.assertIn("tradability_state", details)

    def test_closed_or_non_active_candidate_is_not_live(self) -> None:
        asset_meta = {
            "a1": {"active": False, "closed": False, "accepting_orders": True},
            "a2": {"active": True, "closed": False, "accepting_orders": True},
        }
        ok, state, _details = _candidate_liveness(
            asset_meta=asset_meta,
            token_ids=["a1", "a2"],
            now_ms=1_000,
            market_end_ts_ms=2_000,
        )
        self.assertFalse(ok)
        self.assertEqual(state, "CANDIDATE_INACTIVE")

    def test_contradictory_active_closed_metadata_is_live_eligible(self) -> None:
        asset_meta = {
            "a1": {"active": True, "closed": True, "accepting_orders": False},
            "a2": {"active": True, "closed": True, "accepting_orders": False},
        }
        ok, state, details = _candidate_liveness(
            asset_meta=asset_meta,
            token_ids=["a1", "a2"],
            now_ms=1_000,
            market_end_ts_ms=2_000,
        )
        self.assertTrue(ok)
        self.assertEqual(state, "CANDIDATE_LIVE")
        self.assertEqual(details["tradability_state"], "CANDIDATE_TRADABILITY_AMBIGUOUS")

    def test_unambiguous_closed_candidate_still_blocks(self) -> None:
        asset_meta = {
            "a1": {"active": None, "closed": True, "accepting_orders": False},
            "a2": {"active": True, "closed": False, "accepting_orders": True},
        }
        ok, state, _details = _candidate_liveness(
            asset_meta=asset_meta,
            token_ids=["a1", "a2"],
            now_ms=1_000,
            market_end_ts_ms=2_000,
        )
        self.assertFalse(ok)
        self.assertEqual(state, "CANDIDATE_CLOSED")

    def test_market_end_time_blocks_liveness(self) -> None:
        asset_meta = {
            "a1": {"active": True, "closed": False, "accepting_orders": True},
            "a2": {"active": True, "closed": False, "accepting_orders": True},
        }
        ok, state, details = _candidate_liveness(
            asset_meta=asset_meta,
            token_ids=["a1", "a2"],
            now_ms=3_000,
            market_end_ts_ms=2_000,
        )
        self.assertFalse(ok)
        self.assertEqual(state, "CANDIDATE_ENDED")
        self.assertEqual(details["market_end_ts_ms"], 2_000)

    def test_unknown_metadata_falls_back_to_confirmation_gate(self) -> None:
        ok, state, details = _candidate_liveness(
            asset_meta={"a1": {}, "a2": {}},
            token_ids=["a1", "a2"],
            now_ms=1_000,
            market_end_ts_ms=None,
        )
        self.assertTrue(ok)
        self.assertEqual(state, "CANDIDATE_LIVE")
        self.assertEqual(details["tradability_state"], "CANDIDATE_TRADABILITY_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
