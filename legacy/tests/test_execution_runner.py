from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.broker_base import BrokerBase, BrokerEvent
from core.decision_tape import DecisionRecord, TimeMapper
from core.execution_runner import ExecutionRunner, ExecutionRunnerConfig
from core.trade_tape import TradeTape


class _StubBroker(BrokerBase):
    def __init__(self) -> None:
        self.submit_calls = []

    def submit(self, intent):
        self.submit_calls.append(intent)
        return [
            BrokerEvent(
                event_type="order_submit",
                order_id=intent.order_id,
                payload={
                    "event_id": "broker-submit",
                    "broker": "stub",
                    "status": "submitted",
                    "t_event_wall_ms": intent.as_of_ts_ms,
                    "t_send_wall_ms": intent.as_of_ts_ms,
                    "asset_id": intent.asset_id,
                    "side": intent.side,
                    "price": intent.price,
                    "size": intent.size,
                },
            ),
            BrokerEvent(
                event_type="order_ack",
                order_id=intent.order_id,
                payload={
                    "event_id": "broker-ack",
                    "broker": "stub",
                    "status": "accepted",
                    "t_event_wall_ms": intent.as_of_ts_ms + 1,
                    "t_ack_wall_ms": intent.as_of_ts_ms + 1,
                    "asset_id": intent.asset_id,
                    "side": intent.side,
                    "price": intent.price,
                    "size": intent.size,
                },
            ),
        ]


def _record(*, confidence: float = 1.0, book_stale: bool = False, time_remaining_sec: float = 100.0) -> DecisionRecord:
    return DecisionRecord(
        schema_version="decision_vtest",
        engine_version="test",
        run_id="run",
        t_decision_wall_iso="2024-01-01T00:00:00.000Z",
        t_decision_wall_ms=1000,
        t_decision_mono_ns=1000000,
        asset_id="asset",
        market_slug="btc-updown-15m-1704067200",
        condition_id=None,
        token_id="asset",
        outcome=None,
        outcome_by_token=None,
        book={"book_stale": book_stale},
        p_market_mid=0.5,
        p_market_exec_buy=0.5,
        p_market_exec_sell=0.5,
        p_market=0.5,
        p_fair=0.51,
        edge_net_buy=0.01,
        edge_net_sell=None,
        p_star=None,
        labels=None,
        features_raw=None,
        features_ortho=None,
        whitening=None,
        gates={"allow": True, "reasons": []},
        exec_cost={},
        notes={
            "confidence": confidence,
            "entry_gate": {"allow": True, "reasons": []},
            "signals": {"time_remaining_sec": time_remaining_sec},
        },
    )


class TestExecutionRunner(unittest.TestCase):
    def test_immediate_parent_chain_and_deterministic_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = _StubBroker()
            tape = TradeTape(log_dir=tmp, run_id="run")
            runner = ExecutionRunner(
                trade_tape=tape,
                time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1000, mono_ns=1000000),
                broker=broker,
                enable_trading=True,
                config=ExecutionRunnerConfig(sim_exec=True),
            )
            runner.handle_decision(
                _record(),
                [
                    {
                        "asset_id": "asset",
                        "side": "buy",
                        "size": 1.0,
                        "price": 0.5,
                        "mode": "MAKE",
                        "decision_id": "d-1",
                    }
                ],
            )
            tape.close()
            rows = [json.loads(line) for line in (Path(tmp) / "trade_tape.jsonl").read_text().splitlines()]
            self.assertEqual([row["event_type"] for row in rows], ["order_intent", "order_submit", "order_ack"])
            self.assertEqual(rows[1]["parent_event_id"], rows[0]["event_id"])
            self.assertEqual(rows[2]["parent_event_id"], rows[1]["event_id"])
            self.assertEqual(rows[1]["broker_event_id"], "broker-submit")
            self.assertEqual(rows[2]["broker_event_id"], "broker-ack")
            self.assertNotEqual(rows[1]["event_id"], "broker-submit")
            self.assertNotEqual(rows[2]["event_id"], "broker-ack")
            self.assertEqual(len({row["event_id"] for row in rows}), 3)

    def test_blocks_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = _StubBroker()
            tape = TradeTape(log_dir=tmp, run_id="run")
            runner = ExecutionRunner(
                trade_tape=tape,
                time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1000, mono_ns=1000000),
                broker=broker,
                enable_trading=True,
                config=ExecutionRunnerConfig(min_execution_confidence=0.5),
            )
            runner.handle_decision(
                _record(confidence=0.2),
                [{"asset_id": "asset", "side": "buy", "size": 1.0, "price": 0.5, "mode": "MAKE"}],
            )
            tape.close()
            rows = [json.loads(line) for line in (Path(tmp) / "trade_tape.jsonl").read_text().splitlines()]
            self.assertEqual(rows[-1]["event_type"], "order_reject")
            self.assertEqual(rows[-1]["error_code"], "LOW_CONFIDENCE")
            self.assertEqual(len(broker.submit_calls), 0)

    def test_blocks_book_stale_and_force_flat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = _StubBroker()
            tape = TradeTape(log_dir=tmp, run_id="run")
            runner = ExecutionRunner(
                trade_tape=tape,
                time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1000, mono_ns=1000000),
                broker=broker,
                enable_trading=True,
                config=ExecutionRunnerConfig(force_flat_near_expiry_ms=60_000),
            )
            runner.handle_decision(
                _record(book_stale=True),
                [{"asset_id": "asset", "side": "buy", "size": 1.0, "price": 0.5, "mode": "MAKE"}],
            )
            runner.handle_decision(
                _record(time_remaining_sec=10.0),
                [{"asset_id": "asset", "side": "buy", "size": 1.0, "price": 0.5, "mode": "MAKE", "reduce_only": False}],
            )
            tape.close()
            rows = [json.loads(line) for line in (Path(tmp) / "trade_tape.jsonl").read_text().splitlines()]
            self.assertEqual(rows[1]["error_code"], "BOOK_STALE")
            self.assertEqual(rows[3]["error_code"], "FORCE_FLAT_ONLY")
            self.assertEqual(len(broker.submit_calls), 0)

    def test_failure_cooldown(self) -> None:
        class _RejectingBroker(BrokerBase):
            def submit(self, intent):
                return [
                    BrokerEvent(
                        event_type="broker_error",
                        order_id=intent.order_id,
                        payload={"error_code": "BROKER_DOWN", "t_event_wall_ms": intent.as_of_ts_ms},
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp:
            tape = TradeTape(log_dir=tmp, run_id="run")
            runner = ExecutionRunner(
                trade_tape=tape,
                time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1000, mono_ns=1000000),
                broker=_RejectingBroker(),
                enable_trading=True,
                config=ExecutionRunnerConfig(failure_cooldown_ms=10_000),
            )
            runner.handle_decision(
                _record(),
                [{"asset_id": "asset", "side": "buy", "size": 1.0, "price": 0.5, "mode": "MAKE"}],
            )
            runner.handle_decision(
                _record(),
                [{"asset_id": "asset", "side": "buy", "size": 1.0, "price": 0.5, "mode": "MAKE"}],
            )
            tape.close()
            rows = [json.loads(line) for line in (Path(tmp) / "trade_tape.jsonl").read_text().splitlines()]
            self.assertEqual(rows[-1]["event_type"], "order_reject")
            self.assertEqual(rows[-1]["error_code"], "FAILURE_COOLDOWN")


if __name__ == "__main__":
    unittest.main()
