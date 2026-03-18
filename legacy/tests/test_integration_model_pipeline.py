import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.dataset_builder import build_microstructure_dataset_from_decisions, build_reference_window_dataset
from core.decision_tape import DecisionTape
from core.model_artifact import load_model
from core.order_book import OrderBook
from core.replay import ReplayRunner
from core.validators import OrderConstraints
from scripts.analyze_audit import analyze_decision_files
from scripts.train_model import train_from_rows


class TestIntegrationModelPipeline(unittest.TestCase):
    def test_pipeline_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
            base_ms = int(base_dt.timestamp() * 1000)

            reference_events = []
            for sec, price in [
                (0, 100.0),
                (60, 105.0),
                (300, 110.0),
                (899, 120.0),
                (959, 130.0),
                (1200, 132.0),
                (1500, 134.0),
                (1800, 140.0),
                (2100, 142.0),
                (2400, 144.0),
                (2700, 145.0),
                (3300, 148.0),
                (3600, 150.0),
            ]:
                reference_events.append(
                    _reference_event(
                        t_event_ms=base_ms + sec * 1000,
                        t_recv_mono_ns=sec * 1_000_000_000,
                        t_recv_wall_iso=_iso_from_ms(base_ms + sec * 1000),
                        value=price,
                    )
                )

            market_events = [
                _market_snapshot(
                    t_event_ms=base_ms + 960 * 1000,
                    t_recv_mono_ns=960 * 1_000_000_000,
                    t_recv_wall_iso=_iso_from_ms(base_ms + 960 * 1000),
                ),
                _market_update(
                    t_event_ms=base_ms + 961 * 1000,
                    t_recv_mono_ns=961 * 1_000_000_000,
                    t_recv_wall_iso=_iso_from_ms(base_ms + 961 * 1000),
                ),
            ]

            ref_path = log_dir / "reference_20240101.jsonl"
            market_path = log_dir / "market_20240101.jsonl"
            ref_path.write_text("\n".join(json.dumps(e) for e in reference_events))
            market_path.write_text("\n".join(json.dumps(e) for e in market_events))

            constraints = {
                "asset": OrderConstraints(
                    min_tick=0.01,
                    min_size=1.0,
                    min_price=0.01,
                    max_price=0.99,
                    max_spread_bps=1000.0,
                    max_slippage_bps=1000.0,
                    max_book_staleness_ms=2000,
                )
            }
            market_meta = {
                "asset": {
                    "slug": "btc-updown-15m-1704069000",
                    "condition_id": "cond",
                    "outcome": "Up",
                    "outcome_by_token": {"asset": "Up"},
                    "reference_symbol": "BTC",
                }
            }

            replay_out = log_dir / "replay_a"
            replay_out.mkdir()
            _run_replay([
                str(ref_path),
                str(market_path),
            ], replay_out, constraints, market_meta)

            decision_files = list(replay_out.glob("decision_*.jsonl"))
            self.assertTrue(decision_files)

            ref_rows = build_reference_window_dataset([ref_path], symbol="BTC")
            self.assertTrue(ref_rows)
            label_index = {
                (row["symbol"], int(row["window_start_ts_ms"])): int(row["label_up"])
                for row in ref_rows
            }
            micro_rows = build_microstructure_dataset_from_decisions(decision_files, label_index)
            self.assertTrue(micro_rows)

            model = train_from_rows(ref_rows, l2_lambda=1.0, max_iter=200, tol=1e-6, seed=0)
            model_path = log_dir / "model.json"
            model_path.write_text(json.dumps(model, sort_keys=True))
            model_artifact = load_model(model_path)

            replay_out_b = log_dir / "replay_b"
            replay_out_b.mkdir()
            _run_replay(
                [str(ref_path), str(market_path)],
                replay_out_b,
                constraints,
                market_meta,
                model_artifact=model_artifact,
                model_path=str(model_path),
            )
            decision_files_b = list(replay_out_b.glob("decision_*.jsonl"))
            self.assertTrue(decision_files_b)

            report_a = analyze_decision_files([str(decision_files_b[0])], reference_files=[str(ref_path)])
            report_b = analyze_decision_files([str(decision_files_b[0])], reference_files=[str(ref_path)])
            digest_a = _hash_report(report_a)
            digest_b = _hash_report(report_b)
            self.assertEqual(digest_a, digest_b)


def _run_replay(
    event_files,
    out_dir: Path,
    constraints,
    market_meta,
    model_artifact=None,
    model_path=None,
) -> None:
    books = {"asset": OrderBook(asset_id="asset", bids={}, asks={})}
    decision_tape = DecisionTape(log_dir=str(out_dir), run_id="replay")
    runner = ReplayRunner(
        books=books,
        constraints=constraints,
        decision_tape=decision_tape,
        order_size=1.0,
        fee_rate=0.0025,
        fee_mode="taker",
        market_meta=market_meta,
        model_artifact=model_artifact,
        model_path=model_path,
        reference_settings={
            "staleness_ms": 5000,
            "disagreement_bps": 50.0,
            "min_confidence": 0.5,
            "allowed_symbols": {"BTC"},
        },
    )
    runner.run(event_files)
    decision_tape.close()


def _reference_event(t_event_ms: int, t_recv_mono_ns: int, t_recv_wall_iso: str, value: float) -> dict:
    return {
        "run_id": "run",
        "channel": "reference",
        "event_type": "reference_update",
        "market": "BTC",
        "asset_id": None,
        "t_event_ms": t_event_ms,
        "t_recv_wall_iso": t_recv_wall_iso,
        "t_recv_mono_ns": t_recv_mono_ns,
        "raw": {"source": "spot", "symbol": "BTC", "value": value, "t_event_ms": t_event_ms},
        "parse_warnings": [],
        "out_of_order": False,
    }


def _market_snapshot(t_event_ms: int, t_recv_mono_ns: int, t_recv_wall_iso: str) -> dict:
    return {
        "run_id": "run",
        "channel": "market",
        "event_type": "snapshot",
        "market": None,
        "asset_id": "asset",
        "t_event_ms": t_event_ms,
        "t_recv_wall_iso": t_recv_wall_iso,
        "t_recv_mono_ns": t_recv_mono_ns,
        "raw": {
            "asset_id": "asset",
            "bids": [[0.49, 1.0]],
            "asks": [[0.51, 1.0]],
            "timestamp": t_event_ms,
        },
        "parse_warnings": [],
        "out_of_order": False,
    }


def _market_update(t_event_ms: int, t_recv_mono_ns: int, t_recv_wall_iso: str) -> dict:
    return {
        "run_id": "run",
        "channel": "market",
        "event_type": "price_change",
        "market": None,
        "asset_id": "asset",
        "t_event_ms": t_event_ms,
        "t_recv_wall_iso": t_recv_wall_iso,
        "t_recv_mono_ns": t_recv_mono_ns,
        "raw": {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "asset", "side": "buy", "price": 0.49, "size": 2.0}
            ],
            "timestamp": t_event_ms,
        },
        "parse_warnings": [],
        "out_of_order": False,
    }


def _iso_from_ms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _hash_report(report: dict) -> str:
    payload = json.dumps(report, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
