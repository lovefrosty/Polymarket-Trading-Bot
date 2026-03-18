from __future__ import annotations

import argparse
from pathlib import Path

from backtests.scientific_method.engine import run_experiment


def main() -> None:
    args = _parse_args()
    spec_path = Path(args.spec)
    run_experiment(spec_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a scientific method backtest experiment")
    parser.add_argument("--spec", required=True, help="Path to experiment spec JSON")
    return parser.parse_args()


if __name__ == "__main__":
    main()
