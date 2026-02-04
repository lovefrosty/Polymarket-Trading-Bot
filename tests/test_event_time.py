import unittest

from src.book.order_book import OrderBook
from src.data.reference_price import PriceSource, ReferencePriceValidator
from src.execution.execution_engine import TradeIntent
from src.execution.state_machine import BrokerState, HedgeStateMachine
from src.features.feature_vector import FeatureValue, FeatureVector
from src.replay.replay_harness import ReplayHarness
from src.risk.gates import LatencyMetrics, LatencyThresholds


class TestEventTime(unittest.TestCase):
    def test_feature_vector_rejects_future_event_ts(self) -> None:
        features = {
            "mean_reversion": FeatureValue("mean_reversion", 0.1, 200),
            "momentum": FeatureValue("momentum", -0.2, 200),
        }
        with self.assertRaises(ValueError):
            FeatureVector.from_feature_map(features, decision_ts=200)

    def test_replay_harness_asserts_event_time(self) -> None:
        book = OrderBook()
        book.set_snapshot([(0.49, 10.0)], [(0.51, 10.0)], ts=90)
        validator = ReferencePriceValidator(
            required_sources={"spot", "perp"},
            staleness_ms=1000,
            disagreement_bps=10.0,
            min_confidence=0.5,
        )
        reference_result = validator.validate(
            [PriceSource("spot", 100.0, 100), PriceSource("perp", 100.0, 100)],
            decision_ts=200,
        )
        vector = FeatureVector.from_feature_map(
            {
                "mean_reversion": FeatureValue("mean_reversion", 0.1, 150),
                "momentum": FeatureValue("momentum", -0.2, 200),
            },
            decision_ts=250,
        )
        harness = ReplayHarness(
            book=book,
            book_max_age_ms=1000,
            max_spread_bps=200.0,
            max_slippage_bps=200.0,
            latency_thresholds=LatencyThresholds(1000, 200.0, 500.0),
        )
        intent = TradeIntent(side="buy", qty=1.0, market_price=0.5)
        latency = LatencyMetrics(
            signal_ts=100,
            order_send_ts=150,
            ack_ts=160,
            fill_ts=170,
            p95_ack_ms=50.0,
            ws_lag_ms=10.0,
        )
        hedge_machine = HedgeStateMachine()
        broker_state = BrokerState(primary_position=0.0, hedge_position=0.0, ts=200)
        result = harness.evaluate(
            decision_ts=200,
            intent=intent,
            reference_result=reference_result,
            feature_vector=vector,
            latency_metrics=latency,
            hedge_state_machine=hedge_machine,
            broker_state=broker_state,
        )
        self.assertIn("feature_event_ts_not_before_decision", result.violations)


if __name__ == "__main__":
    unittest.main()
