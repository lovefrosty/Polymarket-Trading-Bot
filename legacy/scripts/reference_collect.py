from __future__ import annotations

import argparse
import asyncio
import signal
from typing import List

from core.event_tape import EventTape
from core.reference_ws import ReferenceWSClient, ReferenceWSConfig


async def main() -> None:
    args = _parse_args()
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    if not symbols:
        raise SystemExit("no_symbols")

    tape = EventTape(log_dir=args.log_dir, run_id="reference_collect")
    client = ReferenceWSClient(
        tape=tape,
        config=ReferenceWSConfig(venue=args.venue, symbols=symbols),
    )
    stop_event = asyncio.Event()

    def _handle_stop(*_args) -> None:
        client.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_stop)

    task = asyncio.create_task(client.run())
    await stop_event.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    tape.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect reference WS ticker into EventTape")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols (BTC,ETH)")
    parser.add_argument("--venue", default="kraken", help="Reference venue: kraken")
    parser.add_argument("--log_dir", default="./logs", help="Output log directory")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
