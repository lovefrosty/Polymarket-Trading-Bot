import unittest

from scripts.plumb_trade import build_plumb_intent


class TestPlumbTradeIntentPattern(unittest.TestCase):
    def test_intent_keys_are_deterministic(self) -> None:
        intent = build_plumb_intent(
            run_epoch_ms=1000,
            asset_id="token-yes",
            side="buy",
            price=0.44,
            size=1.0,
            cycle_idx=2,
            step_idx=3,
            post_only=True,
        )
        self.assertEqual(intent.order_id, "plumb:1000:token-yes:buy:23")
        self.assertEqual(intent.client_order_id, "plumb-cid:1000:token-yes:buy:23")
        self.assertEqual(intent.quote_group_id, "plumb-qg:1000:token-yes:buy:slot0")
        self.assertEqual(intent.idempotency_key, "plumb-idem:1000:token-yes:buy:23")
        self.assertTrue(intent.post_only)


if __name__ == "__main__":
    unittest.main()
