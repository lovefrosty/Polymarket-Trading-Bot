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

    def test_injectable_providers_control_timestamps_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = EventTape(
                log_dir=tmp,
                run_id="run",
                wall_ms_provider=lambda: 1_700_000_000_123,
                mono_ns_provider=lambda: 9_999_999,
                date_key_provider=lambda: "20990101",
            )
            tape.write(
                channel="market",
                event_type="book",
                market="cond",
                asset_id="asset",
                t_event_ms=100,
                raw={"k": "v"},
            )
            tape.close()
            files = list(Path(tmp).glob("market_20990101.jsonl"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(int(record["t_recv_wall_ms"]), 1_700_000_000_123)
            self.assertEqual(int(record["t_recv_mono_ns"]), 9_999_999)

    def test_malformed_recv_iso_uses_provider_fallback_and_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = EventTape(
                log_dir=tmp,
                run_id="run",
                wall_ms_provider=lambda: 1_700_000_123_456,
                mono_ns_provider=lambda: 1_111,
                date_key_provider=lambda: "20990102",
            )
            tape.write(
                channel="market",
                event_type="book",
                market="cond",
                asset_id="asset",
                t_event_ms=100,
                raw={"k": "v"},
                t_recv_wall_iso="bad-iso",
                parse_warnings=[],
            )
            tape.close()
            files = list(Path(tmp).glob("market_20990102.jsonl"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(int(record["t_recv_wall_ms"]), 1_700_000_123_456)
            self.assertIn("INVALID_RECV_WALL_ISO_FALLBACK", record["parse_warnings"])


if __name__ == "__main__":
    unittest.main()
