"""Kalshi fill poller — polls fills endpoint and emits UserEvents.

Runs as an async loop alongside the main trading loop. Each poll
fetches recent fills from Kalshi, deduplicates by trade_id, and
calls the provided on_fill callback with normalized fill dicts
that UserFeedState can consume directly.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Sequence, Set

from core_mm.kalshi.execution_bridge import normalize_kalshi_fill


class KalshiFillPoller:
    """Polls Kalshi fills endpoint and emits UserEvent-compatible dicts.

    Parameters:
        client: ``KalshiClient`` instance.
        on_fill: Callback receiving a normalized fill dict.
        poll_interval_secs: Seconds between fill polls (default 2.0).
        limit: Max fills to fetch per poll.
    """

    def __init__(
        self,
        *,
        client: object,
        on_fill: Callable[[Dict[str, Any]], None],
        poll_interval_secs: float = 2.0,
        limit: int = 50,
    ) -> None:
        self._client = client
        self._on_fill = on_fill
        self._poll_interval = max(0.5, float(poll_interval_secs))
        self._limit = int(limit)
        self._seen: Set[str] = set()
        self._stop_event = asyncio.Event()
        self._poll_count = 0
        self._fill_count = 0

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "polls": self._poll_count,
            "fills_emitted": self._fill_count,
            "dedup_cache_size": len(self._seen),
        }

    async def run(self) -> None:
        """Main poll loop — runs until ``stop()`` is called."""
        while not self._stop_event.is_set():
            try:
                fills = self._client.get_fills(limit=self._limit)
                self._poll_count += 1
                for raw_fill in fills:
                    trade_id = str(raw_fill.get("trade_id") or raw_fill.get("id") or "")
                    if not trade_id or trade_id in self._seen:
                        continue
                    self._seen.add(trade_id)
                    normalized = normalize_kalshi_fill(raw_fill)
                    self._on_fill(normalized)
                    self._fill_count += 1
            except Exception:
                pass  # Transient — retry next cycle
            await asyncio.sleep(self._poll_interval)
