from __future__ import annotations

import argparse
from pathlib import Path
import sys

import uvicorn


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_mm.operator_service import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local operator service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=str(args.host), port=int(args.port), log_level="info")


if __name__ == "__main__":
    main()
