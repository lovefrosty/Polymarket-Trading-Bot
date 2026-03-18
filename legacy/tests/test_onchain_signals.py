import unittest

from core.onchain_signals import OnchainSignalState


class TestOnchainSignals(unittest.TestCase):
    def test_imbalance_and_whale_activity(self) -> None:
        state = OnchainSignalState(window_secs=60.0, whales={"0xabc"})
        record = {
            "event_type": "OrderFilled",
            "t_recv_mono_ns": 1,
            "raw": {
                "args": {
                    "makerAssetId": "A",
                    "takerAssetId": "B",
                    "makerAmountFilled": "5",
                    "takerAmountFilled": "10",
                    "maker": "0x111",
                    "taker": "0xAbC",
                }
            },
        }
        state.ingest_record(record)
        snapshot = state.snapshot("B", None, now_mono_ns=2)
        self.assertIsNotNone(snapshot)
        self.assertAlmostEqual(snapshot["buy_volume"], 10.0)
        self.assertAlmostEqual(snapshot["sell_volume"], 0.0)
        self.assertAlmostEqual(snapshot["imbalance"], 1.0)
        self.assertEqual(len(snapshot["whale_activity"]), 1)
        self.assertEqual(snapshot["whale_activity"][0]["whale"], "0xabc")

    def test_capital_flow(self) -> None:
        state = OnchainSignalState(window_secs=60.0)
        split = {
            "event_type": "PositionsSplit",
            "t_recv_mono_ns": 10,
            "raw": {"args": {"conditionId": "cond", "amount": "100"}},
        }
        merge = {
            "event_type": "PositionsMerge",
            "t_recv_mono_ns": 20,
            "raw": {"args": {"conditionId": "cond", "amount": "40"}},
        }
        state.ingest_record(split)
        state.ingest_record(merge)
        snapshot = state.snapshot("asset", "cond", now_mono_ns=30)
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(snapshot["capital_flow"])
        self.assertAlmostEqual(snapshot["capital_flow"]["net_amount"], 60.0)
        self.assertEqual(snapshot["capital_flow"]["signal"], "BULLISH")

    def test_out_of_order_ignored(self) -> None:
        state = OnchainSignalState(window_secs=60.0)
        first = {
            "event_type": "OrderFilled",
            "t_recv_mono_ns": 100,
            "raw": {
                "args": {
                    "makerAssetId": "A",
                    "takerAssetId": "B",
                    "makerAmountFilled": "1",
                    "takerAmountFilled": "1",
                }
            },
        }
        late = {
            "event_type": "OrderFilled",
            "t_recv_mono_ns": 50,
            "raw": {
                "args": {
                    "makerAssetId": "A",
                    "takerAssetId": "B",
                    "makerAmountFilled": "2",
                    "takerAmountFilled": "2",
                }
            },
        }
        state.ingest_record(first)
        state.ingest_record(late)
        snapshot = state.snapshot("B", None, now_mono_ns=200)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["out_of_order"], 1)
        self.assertAlmostEqual(snapshot["buy_volume"], 1.0)


if __name__ == "__main__":
    unittest.main()
