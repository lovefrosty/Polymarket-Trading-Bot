from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.order_state import OrderStateSnapshot, rebuild_order_state


@dataclass(frozen=True)
class TradeTapeReplayResult:
    events: List[Dict[str, Any]]
    order_state: OrderStateSnapshot


class TradeTapeReplayer:
    def replay(self, paths: Iterable[str]) -> TradeTapeReplayResult:
        events = self._load(paths)
        return TradeTapeReplayResult(events=events, order_state=rebuild_order_state(events))

    def _load(self, paths: Iterable[str]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        seen_event_ids = set()

        for path_str in paths:
            path = Path(path_str)
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    event = json.loads(raw_line)
                    event_id = str(event.get("event_id") or "")
                    event_type = str(event.get("event_type") or "")
                    parent_event_id = event.get("parent_event_id")
                    if not event_id:
                        raise ValueError("trade_tape_replay_missing_event_id")
                    if event_id in seen_event_ids:
                        raise ValueError(f"trade_tape_replay_duplicate_event_id:{event_id}")
                    if event_type == "order_intent":
                        if parent_event_id is not None:
                            raise ValueError("trade_tape_replay_intent_parent_invalid")
                    else:
                        if parent_event_id is None:
                            raise ValueError(f"trade_tape_replay_missing_parent:{event_id}")
                        if str(parent_event_id) not in seen_event_ids:
                            raise ValueError(f"trade_tape_replay_unknown_parent:{event_id}")
                    seen_event_ids.add(event_id)
                    events.append(event)

        return events
