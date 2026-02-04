import asyncio
import glob
import tempfile
import time
import unittest
from pathlib import Path

from core.event_tape import EventTape
from core.onchain_ingest import OnchainIngestConfig, OnchainIngestor, _FilterHandle


class _FakeFilter:
    def __init__(self, entries):
        self._entries = list(entries)

    def get_new_entries(self):
        entries = self._entries
        self._entries = []
        return entries


class _FakeEvent:
    def __init__(self, entries):
        self._entries = entries

    def create_filter(self, fromBlock="latest"):
        return _FakeFilter(self._entries)


class _FailingIngestor(OnchainIngestor):
    async def _init_web3(self, url):  # type: ignore[override]
        return object()

    def _build_contracts(self, web3):  # type: ignore[override]
        return [("OrderFilled", _FakeEvent([]))]

    def _create_filters(self, contracts):  # type: ignore[override]
        return [_FilterHandle(event_name="OrderFilled", event=_FakeEvent([]), filt=_FakeFilter([]))]

    def _drain_filters_once(self, filters):  # type: ignore[override]
        return 0, True


class TestOnchainIngest(unittest.TestCase):
    def test_drain_filters_once_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = EventTape(log_dir=tmp, run_id="run")
            config = OnchainIngestConfig(rpc_http_url="http://localhost", rpc_ws_url=None)
            ingestor = OnchainIngestor(tape=tape, config=config)
            event = _FakeEvent(
                [
                    {"args": {"makerAssetId": "1", "takerAssetId": "2", "makerAmountFilled": "1", "takerAmountFilled": "2"}}
                ]
            )
            handle = _FilterHandle(event_name="OrderFilled", event=event, filt=event.create_filter())
            start = time.monotonic()
            processed, had_error = ingestor._drain_filters_once([handle])
            elapsed = time.monotonic() - start
            self.assertEqual(processed, 1)
            self.assertFalse(had_error)
            self.assertLess(elapsed, 1.0)
            tape.close()

    def test_dedupe_drops_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = EventTape(log_dir=tmp, run_id="run")
            config = OnchainIngestConfig(rpc_http_url="http://localhost", rpc_ws_url=None)
            ingestor = OnchainIngestor(tape=tape, config=config)
            entry = {
                "args": {"makerAssetId": "1"},
                "transactionHash": b"\x01",
                "logIndex": 5,
                "blockNumber": 10,
                "blockHash": b"\x02",
            }
            ingestor._handle_event("OrderFilled", entry)
            ingestor._handle_event("OrderFilled", entry)
            tape.close()
            paths = sorted(glob.glob(f"{tmp}/onchain_*.jsonl"))
            self.assertEqual(len(paths), 1)
            lines = Path(paths[0]).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)

    def test_ws_fallback_after_outage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = EventTape(log_dir=tmp, run_id="run")
            config = OnchainIngestConfig(
                rpc_http_url="http://localhost",
                rpc_ws_url="ws://localhost",
                use_ws=True,
                ws_loop_sleep_secs=0.0,
                recreate_filter_after_secs=0.0,
            )
            ingestor = _FailingIngestor(tape=tape, config=config)
            stop_event = asyncio.Event()
            fallback = asyncio.run(ingestor._ws_loop(object(), [("OrderFilled", _FakeEvent([]))], stop_event))
            tape.close()
            self.assertTrue(fallback)
            self.assertGreaterEqual(ingestor._reconnects, 1)


if __name__ == "__main__":
    unittest.main()
