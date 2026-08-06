from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
import sys
from typing import Iterable, Optional


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _candidate_runtime_dbs(root: Path) -> Iterable[Path]:
    default_db = root / "runtime.db"
    if default_db.exists():
        yield default_db
    for pattern in ("tmp/core_mm_runs/*/runtime.db", "tmp/desktop_run_archive/*/core_mm_runs/*/runtime.db"):
        yield from root.glob(pattern)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def resolve_dashboard_db_path(root: Path, db_path: Optional[str]) -> Path:
    if db_path:
        return Path(db_path).resolve()
    candidates = [path for path in _candidate_runtime_dbs(root) if path.exists()]
    if not candidates:
        return (root / "runtime.db").resolve()
    def _candidate_key(path: Path) -> tuple[float, float, str]:
        runtime_root = path.parent
        status_path = runtime_root / "meta" / "status.json"
        summary_path = runtime_root / "meta" / "run_summary.json"
        status = _read_json(status_path)
        summary = _read_json(summary_path)
        stage = str(status.get("stage") or "")
        is_active = 1.0 if stage == "running" else 0.0
        updated_at_ms = status.get("updated_at_ms") or summary.get("updated_at_ms") or status.get("last_cycle_at_ms")
        if updated_at_ms is not None:
            try:
                freshness = float(updated_at_ms)
            except (TypeError, ValueError):
                freshness = 0.0
        else:
            freshness = max(
                path.stat().st_mtime if path.exists() else 0.0,
                status_path.stat().st_mtime if status_path.exists() else 0.0,
                summary_path.stat().st_mtime if summary_path.exists() else 0.0,
            )
        return (is_active, freshness, str(path))

    latest = max(candidates, key=_candidate_key)
    return latest.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Polymarket V1 Streamlit dashboard")
    parser.add_argument("--db-path", default=None, help="Path to SQLite runtime db")
    parser.add_argument("--port", default="8501", help="Streamlit port")
    parser.add_argument("--host", default="0.0.0.0", help="Streamlit host")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    app_path = root / "dashboard" / "app.py"
    env = dict(os.environ)
    env["RUNTIME_DB_PATH"] = str(resolve_dashboard_db_path(root, args.db_path))

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(args.port),
        "--server.address",
        str(args.host),
        "--server.headless",
        "true",
    ]
    raise SystemExit(subprocess.call(cmd, cwd=str(root), env=env))


if __name__ == "__main__":
    main()
