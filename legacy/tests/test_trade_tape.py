from __future__ import annotations

import glob
import tempfile
import unittest
from pathlib import Path

from core.trade_tape import TradeTape


class TestTradeTapeDeterminism(unittest.TestCase):
    def _write_events(self, log_dir: Path) -> str:
        tape = TradeTape(log_dir=str(log_dir), run_id="run")
        tape.write(
            {
                "schema_version": "trade_v1",
                "run_id": "run",
                "event_id": 1,
                "parent_event_id": None,
                "event_type": "order_intent",
                "order_id": "o1",
                "client_order_id": "o1:client",
                "asset_id": "asset",
                "side": "buy",
                "size": 1.0,
                "price": 0.5,
                "mode": "TAKE",
                "t_decision_wall_ms": 1000,
                "t_event_wall_ms": 1000,
                "t_event_mono_ns": 1000000,
                "as_of_ts_ms": 1000,
            }
        )
        tape.write(
            {
                "schema_version": "trade_v1",
                "run_id": "run",
                "event_id": 2,
                "parent_event_id": 1,
                "event_type": "order_reject",
                "order_id": "o1",
                "t_event_wall_ms": 1000,
                "t_event_mono_ns": 1000001,
                "as_of_ts_ms": 1000,
                "reason": "RISK_GATE",
                "error_code": "RISK_GATE",
            }
        )
        tape.close()
        files = sorted(glob.glob(str(log_dir / "trade_*.jsonl")))
        self.assertTrue(files)
        return Path(files[0]).read_text(encoding="utf-8")

    def test_trade_tape_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first = self._write_events(Path(tmpdir) / "first")
            second = self._write_events(Path(tmpdir) / "second")
            self.assertEqual(first, second)
