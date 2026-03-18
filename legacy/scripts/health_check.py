from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Runtime health check")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--max-lag-sec", type=int, default=30)
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"CRITICAL: db missing at {db_path}")
        raise SystemExit(2)

    with sqlite3.connect(db_path.as_posix()) as cx:
        row = cx.execute("SELECT MAX(ts_ms) FROM decisions").fetchone()
        last_ts_ms = row[0] if row and row[0] is not None else 0
        state_row = cx.execute("SELECT is_frozen, reasons FROM system_state ORDER BY as_of_ts DESC LIMIT 1").fetchone()
    now_ms = int(time.time() * 1000)
    lag_sec = (now_ms - int(last_ts_ms)) / 1000.0 if last_ts_ms else 1e9

    if lag_sec > args.max_lag_sec:
        print(f"CRITICAL: decision stream stale lag_sec={lag_sec:.2f}")
        raise SystemExit(2)

    if state_row and int(state_row[0]) == 1:
        print(f"WARNING: system frozen reasons={state_row[1]}")
        raise SystemExit(1)

    print(f"OK: lag_sec={lag_sec:.2f}")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
