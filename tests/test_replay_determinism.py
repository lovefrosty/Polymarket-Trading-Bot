import json
import tempfile
import unittest
from pathlib import Path

from core.decision_tape import DecisionTape
from core.order_book import OrderBook
from core.replay import ReplayRunner
from core.validators import OrderConstraints
from scripts.replay_certify import certify_decision_streams


class TestReplayDeterminism(unittest.TestCase):
    def test_replay_decision_tape_deterministic(self) -> None:
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

        with tempfile.TemporaryDirectory() as tmp:
            tape_path = Path(tmp) / "market_20240101.jsonl"
            tape_path.write_text(
                "\n".join([json.dumps(record_snapshot), json.dumps(record_reference), json.dumps(record_update)])
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

    def test_replay_certify_reports_pass_and_fail(self) -> None:
        left_records = [
            {
                "t_decision_wall_ms": 1000,
                "token_id": "token-a",
                "gates": {"allow": False, "reasons": ["BOOK_STALE"]},
                "fsm_state": "FROZEN",
            },
            {
                "t_decision_wall_ms": 1001,
                "token_id": "token-a",
                "gates": {"allow": True, "reasons": []},
                "fsm_state": "QUOTING_BOTH",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            left = Path(tmp) / "decision_left.jsonl"
            right = Path(tmp) / "decision_right.jsonl"
            left.write_text("\n".join(json.dumps(row) for row in left_records), encoding="utf-8")
            right.write_text("\n".join(json.dumps(row) for row in left_records), encoding="utf-8")

            report_pass = certify_decision_streams(left_files=[left], right_files=[right])
            self.assertEqual(report_pass["status"], "PASS")
            self.assertEqual(report_pass["mismatch_count"], 0)

            changed = list(left_records)
            changed[1] = {
                **changed[1],
                "gates": {"allow": False, "reasons": ["FORCED_BLOCK"]},
            }
            right.write_text("\n".join(json.dumps(row) for row in changed), encoding="utf-8")
            report_fail = certify_decision_streams(left_files=[left], right_files=[right])
            self.assertEqual(report_fail["status"], "FAIL")
            self.assertGreater(report_fail["mismatch_count"], 0)


if __name__ == "__main__":
    unittest.main()
