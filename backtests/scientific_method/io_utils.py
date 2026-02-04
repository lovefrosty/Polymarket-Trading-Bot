from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def expand_paths(patterns: Iterable[str]) -> List[str]:
    paths: List[str] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(matches)
        else:
            path = Path(pattern)
            if path.exists():
                paths.append(str(path))
    return sorted(set(paths))


def load_jsonl(paths: Iterable[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
