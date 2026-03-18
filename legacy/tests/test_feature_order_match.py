import json
import tempfile
import unittest
from pathlib import Path

from core.decision_engine import DecisionEngine, DecisionEngineConfig
from core.decision_tape import DecisionTape, TimeMapper
from core.model_artifact import ModelArtifact
from core.order_book import OrderBook
from core.reference_price import ReferencePriceAggregator, ReferenceQuote
from core.validators import OrderConstraints


class TestFeatureOrderMatch(unittest.TestCase):
    def test_feature_order_mismatch_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            tape = DecisionTape(log_dir=str(log_dir), run_id="run")
            book = OrderBook(asset_id="token", bids={}, asks={})
            book.apply_snapshot(bids=[(0.49, 1.0)], asks=[(0.51, 1.0)], event_ts_ms=1000, recv_mono_ns=100)
            constraints = {
                "token": OrderConstraints(
                    min_tick=0.01,
                    min_size=1.0,
                    min_price=0.01,
                    max_price=0.99,
                    max_spread_bps=1000.0,
                    max_slippage_bps=1000.0,
                    max_book_staleness_ms=10_000,
                )
            }
            markets = {
                "token": {
                    "slug": "btc-updown-15m-1704067200",
                    "condition_id": "cond",
                    "outcome": "Up",
                    "outcome_by_token": {"token": "Up"},
                    "reference_symbol": "BTC",
                }
            }
            aggregator = ReferencePriceAggregator(
                required_sources={"spot", "perp"},
                staleness_ms=10_000,
                disagreement_bps=100.0,
                min_confidence=0.1,
                allowed_symbols={"BTC"},
            )
            time_mapper = TimeMapper.from_wall_and_mono(wall_ms=1704067200000, mono_ns=100)

            model = ModelArtifact(
                schema_version="model_ridge_logit_v1",
                feature_order=["z_mom", "z_rev", "ret_60s", "ret_300s", "ret_900s", "ewma_vol_300s"],
                w=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                b=0.0,
                offset_mode=None,
                platt=None,
                metadata={},
            )

            engine = DecisionEngine(
                books={"token": book},
                constraints=constraints,
                tape=tape,
                time_mapper=time_mapper,
                config=DecisionEngineConfig(order_size=1.0, fee_rate=0.0025),
                market_meta=markets,
                reference_aggregator=aggregator,
                model_artifact=model,
                model_path="/tmp/model.json",
            )

            quote = ReferenceQuote(
                source="spot",
                symbol="BTC",
                value=100.0,
                t_event_ms=1704067190000,
                t_recv_mono_ns=100,
                t_recv_wall_iso="2024-01-01T00:00:00.000Z",
                t_recv_wall_ms=1704067190000,
            )
            aggregator.ingest(quote)
            engine.on_reference_event(quote)
            aggregator.ingest(
                ReferenceQuote(
                    source="perp",
                    symbol="BTC",
                    value=100.0,
                    t_event_ms=1704067190000,
                    t_recv_mono_ns=100,
                    t_recv_wall_iso="2024-01-01T00:00:00.000Z",
                    t_recv_wall_ms=1704067190000,
                )
            )

            engine._emit_decision("token", 1_000_100, trigger="test")
            tape.close()

            files = list(log_dir.glob("decision_*.jsonl"))
            self.assertTrue(files)
            record = json.loads(files[0].read_text().splitlines()[-1])
            notes = record.get("notes") or {}
            self.assertEqual(notes.get("model_used"), "baseline")
            self.assertIn("FEATURE_MISMATCH", notes.get("model_blockers", []))


if __name__ == "__main__":
    unittest.main()
