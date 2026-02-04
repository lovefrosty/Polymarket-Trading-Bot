from __future__ import annotations

import argparse
from pathlib import Path

from backtests.scientific_method.weight_calibration import run_calibration


def main() -> None:
    args = _parse_args()
    run_calibration(Path(args.spec), output_dir_override=Path(args.output) if args.output else None)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline weight calibration on Decision/Reference tapes")
    parser.add_argument("--spec", required=True, help="Path to calibration spec JSON")
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory (default: backtests/scientific_method/calibration)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
