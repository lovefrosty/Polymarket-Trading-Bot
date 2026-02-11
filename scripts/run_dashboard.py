from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
import sys


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Polymarket V1 Streamlit dashboard")
    parser.add_argument("--db-path", default="runtime.db", help="Path to SQLite runtime db")
    parser.add_argument("--port", default="8501", help="Streamlit port")
    parser.add_argument("--host", default="0.0.0.0", help="Streamlit host")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    app_path = root / "dashboard" / "app.py"
    env = dict(os.environ)
    env["RUNTIME_DB_PATH"] = str(Path(args.db_path).resolve())

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
