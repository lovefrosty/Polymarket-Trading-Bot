import unittest

from core.book_cache import BookSnapshot
from core.policy_gate import PolicyContext, PolicyThresholds, evaluate_policy
from core.pstar import PStar


class TestPolicyGateV1(unittest.TestCase):
    def test_rejects_invalid_pstar(self) -> None:
        snap = BookSnapshot(
            token_id="token",
            bids=((0.4995, 10.0),),
            asks=((0.5005, 10.0),),
            ts_event_ms=1000,
            ts_recv_mono_ns=1,
            ts_recv_wall_ms=1000,
        )
        ctx = PolicyContext(
            market="m",
            token_id="token",
            now_ms=1500,
            decision_ts_event_ms=1500,
            feature_max_ts_ms=1499,
            book=snap,
            pstar=PStar(symbol="BTC", value=None, ts_event_ms=None, sources_used=set(), confidence=0.0, valid=False, diagnostics={}),
            quote_side="buy",
            quote_qty=1.0,
            signal_age_ms=50,
            ack_p95_ms=100.0,
            ws_lag_ms=10.0,
            one_leg_age_ms=None,
            fsm_state="QUOTING_BOTH",
            expected_slippage_bps=5.0,
            depth_at_qty=2.0,
        )
        verdict = evaluate_policy(ctx, PolicyThresholds())
        self.assertFalse(verdict.allow)
        self.assertIn("A_PSTAR_INVALID", verdict.reason_codes)

    def test_rejects_time_leakage(self) -> None:
        snap = BookSnapshot(
            token_id="token",
            bids=((0.49, 10.0),),
            asks=((0.51, 10.0),),
            ts_event_ms=2000,
            ts_recv_mono_ns=1,
            ts_recv_wall_ms=2000,
        )
        pstar = PStar(symbol="BTC", value=100.0, ts_event_ms=2001, sources_used={"spot"}, confidence=0.5, valid=True, diagnostics={})
        ctx = PolicyContext(
            market="m",
            token_id="token",
            now_ms=2002,
            decision_ts_event_ms=2000,
            feature_max_ts_ms=2000,
            book=snap,
            pstar=pstar,
            quote_side="buy",
            quote_qty=1.0,
            signal_age_ms=10,
            ack_p95_ms=100.0,
            ws_lag_ms=10.0,
            one_leg_age_ms=None,
            fsm_state="QUOTING_BOTH",
            expected_slippage_bps=5.0,
            depth_at_qty=2.0,
        )
        verdict = evaluate_policy(ctx, PolicyThresholds())
        self.assertFalse(verdict.allow)
        self.assertIn("B_FEATURE_TIME_LEAK", verdict.reason_codes)

    def test_allows_clean_context(self) -> None:
        snap = BookSnapshot(
            token_id="token",
            bids=((0.4995, 10.0),),
            asks=((0.5005, 10.0),),
            ts_event_ms=1000,
            ts_recv_mono_ns=1,
            ts_recv_wall_ms=1000,
        )
        pstar = PStar(symbol="BTC", value=100.0, ts_event_ms=900, sources_used={"spot", "perp"}, confidence=0.9, valid=True, diagnostics={})
        ctx = PolicyContext(
            market="m",
            token_id="token",
            now_ms=1500,
            decision_ts_event_ms=1500,
            feature_max_ts_ms=1400,
            book=snap,
            pstar=pstar,
            quote_side="buy",
            quote_qty=1.0,
            signal_age_ms=100,
            ack_p95_ms=100.0,
            ws_lag_ms=100.0,
            one_leg_age_ms=None,
            fsm_state="QUOTING_BOTH",
            expected_slippage_bps=10.0,
            depth_at_qty=2.0,
        )
        verdict = evaluate_policy(ctx, PolicyThresholds())
        self.assertTrue(verdict.allow)
        self.assertEqual(verdict.action, "QUOTE")


if __name__ == "__main__":
    unittest.main()
