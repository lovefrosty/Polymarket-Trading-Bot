import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.decision_tape import DecisionTape
from core.model_artifact import load_model
from core.order_book import OrderBook
from core.replay import ReplayRunner
from core.validators import OrderConstraints


class TestReplayDeterminismWithModel(unittest.TestCase):
    def test_replay_deterministic_with_model(self) -> None:
        record_snapshot = {
            "run_id": "run",
            "channel": "market",
            "event_type": "snapshot",
            "market": None,
            "asset_id": "asset",
            "t_event_ms": 1000,
            "t_recv_wall_iso": "2024-01-01T00:00:00.000Z",
            "t_recv_mono_ns": 100,
            "raw": {
                "asset_id": "asset",
                "bids": [[0.49, 1.0]],
                "asks": [[0.51, 1.0]],
                "timestamp": 1000,
            },
            "parse_warnings": [],
            "out_of_order": False,
        }
        record_reference = {
            "run_id": "run",
            "channel": "reference",
            "event_type": "reference_update",
            "market": "BTC",
            "asset_id": None,
            "t_event_ms": 900,
            "t_recv_wall_iso": "2024-01-01T00:00:00.050Z",
            "t_recv_mono_ns": 150,
            "raw": {"source": "spot", "symbol": "BTC", "value": 100.0, "t_event_ms": 900},
            "parse_warnings": [],
            "out_of_order": False,
        }
        record_update = {
            "run_id": "run",
            "channel": "market",
            "event_type": "price_change",
            "market": None,
            "asset_id": "asset",
            "t_event_ms": 1100,
            "t_recv_wall_iso": "2024-01-01T00:00:00.100Z",
            "t_recv_mono_ns": 200,
            "raw": {
                "event_type": "price_change",
                "price_changes": [
                    {"asset_id": "asset", "side": "buy", "price": 0.49, "size": 2.0}
                ],
                "timestamp": 1100,
            },
            "parse_warnings": [],
            "out_of_order": False,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tape_path = Path(tmp) / "market_20240101.jsonl"
            tape_path.write_text(
                "\n".join(
                    [json.dumps(record_snapshot), json.dumps(record_reference), json.dumps(record_update)]
                )
            )

            output_a = Path(tmp) / "out_a"
            output_b = Path(tmp) / "out_b"
            output_a.mkdir()
            output_b.mkdir()

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
            model_path = Path(__file__).parent / "fixtures" / "model_artifact.json"
            model = load_model(model_path)

            def run_replay(out_dir: Path) -> str:
                books = {"asset": OrderBook(asset_id="asset", bids={}, asks={})}
                decision_tape = DecisionTape(log_dir=str(out_dir), run_id="replay")
                runner = ReplayRunner(
                    books=books,
                    constraints=constraints,
                    decision_tape=decision_tape,
                    order_size=1.0,
                    fee_rate=0.0025,
                    fee_mode="taker",
                    market_meta={},
                    model_artifact=model,
                    model_path=str(model_path),
                    reference_settings={
                        "staleness_ms": 5000,
                        "disagreement_bps": 50.0,
                        "min_confidence": 0.5,
                        "allowed_symbols": {"BTC"},
                    },
                )
                runner.run([str(tape_path)])
                decision_tape.close()
                files = list(out_dir.glob("decision_*.jsonl"))
                self.assertTrue(files)
                return files[0].read_text()

            out_first = run_replay(output_a)
            out_second = run_replay(output_b)
            self.assertEqual(out_first, out_second)

    def test_replay_certify_cli_detects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left_dir = Path(tmp) / "left"
            right_dir = Path(tmp) / "right"
            left_dir.mkdir()
            right_dir.mkdir()
            left_file = left_dir / "decision_20240101.jsonl"
            right_file = right_dir / "decision_20240101.jsonl"
            left_file.write_text(
                json.dumps(
                    {
                        "t_decision_wall_ms": 1000,
                        "token_id": "token-a",
                        "gates": {"allow": True, "reasons": []},
                        "fsm_state": "QUOTING_BOTH",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            right_file.write_text(
                json.dumps(
                    {
                        "t_decision_wall_ms": 1000,
                        "token_id": "token-a",
                        "gates": {"allow": False, "reasons": ["FORCED_BLOCK"]},
                        "fsm_state": "FROZEN",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/replay_certify.py",
                    "--left",
                    str(left_dir),
                    "--right",
                    str(right_dir),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "FAIL")
            self.assertGreater(payload["mismatch_count"], 0)


if __name__ == "__main__":
    unittest.main()
