from __future__ import annotations

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import run_system


def _force_observe_mode(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for idx, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if arg == "--mode":
            skip_next = True
            continue
        if arg.startswith("--mode="):
            continue
        cleaned.append(arg)
    return ["--mode", "OBSERVE", *cleaned]


def main() -> None:
    forced = _force_observe_mode(sys.argv[1:])
    prev = list(sys.argv)
    try:
        sys.argv = [sys.argv[0], *forced]
        run_system.main()
    finally:
        sys.argv = prev


if __name__ == "__main__":
    main()
