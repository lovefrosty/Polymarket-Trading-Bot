from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.sqlite_store import SQLiteStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SQLite runtime tables to deterministic JSONL")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--out-dir", default="logs/export")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")

    db = SQLiteStore(args.db_path)
    try:
        db.export_table_jsonl("market_data_book", out_dir / f"market_{date_key}.jsonl", order_by="ts_ms")
        db.export_table_jsonl("pstar", out_dir / f"reference_{date_key}.jsonl", order_by="ts_ms")
        db.export_table_jsonl("decisions", out_dir / f"decision_{date_key}.jsonl", order_by="ts_ms")
        db.export_table_jsonl("orders", out_dir / f"trade_{date_key}.jsonl", order_by="ts_ms")
    finally:
        db.close()


if __name__ == "__main__":
    main()
