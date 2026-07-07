import json
import tempfile
import unittest
from pathlib import Path

from core.event_tape import EventTape


class TestEventTapeSchema(unittest.TestCase):
    def test_required_fields_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = EventTape(log_dir=tmp, run_id="run")
            tape.write(
                channel="market",
                event_type="price_change",
                market="cond",
                asset_id="asset",
                t_event_ms=123,
                raw={"foo": "bar"},
                parse_warnings=[],
                out_of_order=False,
            )
            tape.close()
            files = list(Path(tmp).glob("market_*.jsonl"))
            self.assertTrue(files)
            record = json.loads(files[0].read_text().splitlines()[0])
            for key in (
                "run_id",
                "channel",
                "event_type",
                "market",
                "asset_id",
                "source",
                "t_event_ms",
                "t_recv_wall_ms",
                "t_recv_wall_iso",
                "t_recv_mono_ns",
                "raw",
                "parse_warnings",
                "out_of_order",
            ):
                self.assertIn(key, record)


if __name__ == "__main__":
    unittest.main()
