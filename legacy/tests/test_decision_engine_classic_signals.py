from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.classic_signals import ClassicSignalConfig
from core.decision_engine import DecisionEngine, DecisionEngineConfig
from core.decision_tape import DecisionTape, TimeMapper
from core.feature_builder import FEATURE_ORDER
from core.model_artifact import ModelArtifact
from core.order_book import OrderBook
from core.reference_price import ReferenceQuote
from core.validators import OrderConstraints


class TestDecisionEngineClassicSignals(unittest.TestCase):
    def test_classic_signals_are_logged_and_skew_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            tape = DecisionTape(log_dir=str(log_dir), run_id="run")
            book = OrderBook(asset_id="token", bids={}, asks={})
            book.apply_snapshot(bids=[(0.49, 10.0)], asks=[(0.51, 10.0)], event_ts_ms=1000, recv_mono_ns=100)
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
            time_mapper = TimeMapper.from_wall_and_mono(wall_ms=1704067200000, mono_ns=100)
            model = ModelArtifact(
                schema_version="model_ridge_logit_v1",
                feature_order=FEATURE_ORDER,
                w=[0.0 for _ in FEATURE_ORDER],
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
                config=DecisionEngineConfig(
                    order_size=1.0,
                    fee_rate=0.0025,
                    classic_signals_skew_enabled=True,
                    classic_signals_max_skew_bps=25.0,
                    classic_signals_inventory_regime_enabled=True,
                    classic_signal_config=ClassicSignalConfig(warmup_updates=1),
                ),
                market_meta=markets,
                model_artifact=model,
            )

            reference_points = [
                (1704066300000, 98.0),
                (1704066900000, 99.0),
                (1704067140000, 100.0),
                (1704067199000, 101.0),
            ]
            for idx, (ts_ms, value) in enumerate(reference_points, start=1):
                engine.on_reference_event(
                    ReferenceQuote(
                        source="spot",
                        symbol="BTC",
                        value=value,
                        t_event_ms=ts_ms,
                        t_recv_mono_ns=idx,
                        t_recv_wall_iso="2024-01-01T00:00:00.000Z",
                        t_recv_wall_ms=ts_ms,
                    )
                )

            engine._emit_decision("token", 1_000_100, trigger="test")
            book.apply_snapshot(bids=[(0.69, 15.0)], asks=[(0.71, 5.0)], event_ts_ms=2000, recv_mono_ns=200)
            engine._emit_decision("token", 2_000_100, trigger="test")
            tape.close()

            files = list(log_dir.glob("decision_*.jsonl"))
            self.assertTrue(files)
            record = json.loads(files[0].read_text().splitlines()[-1])
            notes = record.get("notes") or {}
            classic = notes.get("classic_signals") or {}
            overlay = notes.get("classic_signal_overlay") or {}
            features_raw = record.get("features_raw") or {}

            self.assertTrue(classic.get("valid"))
            self.assertEqual(classic.get("as_of_ts_ms"), record.get("t_decision_wall_ms"))
            self.assertEqual(features_raw.get("classic_signals", {}).get("as_of_ts_ms"), classic.get("as_of_ts_ms"))
            self.assertLessEqual(abs(float(overlay.get("skew_bps") or 0.0)), 25.0)
            self.assertTrue(overlay.get("enabled"))
            self.assertIsNotNone(overlay.get("inventory_regime"))
            self.assertNotEqual(overlay.get("p_fair_model"), overlay.get("p_fair_adjusted"))


if __name__ == "__main__":
    unittest.main()
