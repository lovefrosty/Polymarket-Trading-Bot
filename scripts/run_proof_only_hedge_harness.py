from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_mm.risk_harness import run_proof_only_hedge_harness


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate proof-only crypto-cluster hedge evidence.")
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    result = run_proof_only_hedge_harness(Path(args.runtime_root))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
