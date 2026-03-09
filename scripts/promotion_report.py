from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.check_integration_health import _build_report
from scripts.check_promotion_gates import _load_constitution
from scripts.replay_certify import _resolve_decision_files, certify_decision_streams


def _maybe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _default_runtime_fingerprint(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.as_posix().encode("utf-8"))
        if path.exists():
            digest.update(path.read_bytes())
        else:
            digest.update(b"MISSING")
    return digest.hexdigest()


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def _write_json_file(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_promotion_gates(db_path: Path, lookback_hours: int, constitution: Path) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str((_ROOT / "scripts" / "check_promotion_gates.py").resolve()),
        "--db-path",
        str(db_path),
        "--lookback-hours",
        str(int(lookback_hours)),
        "--constitution",
        str(constitution),
    ]
    result = subprocess.run(cmd, cwd=_ROOT.as_posix(), capture_output=True, text=True, check=False)
    stdout = (result.stdout or "").strip()
    payload: Dict[str, Any]
    if stdout:
        try:
            parsed = json.loads(stdout)
            payload = parsed if isinstance(parsed, dict) else {"status": "FAIL", "error": "invalid_checker_json"}
        except json.JSONDecodeError:
            payload = {"status": "FAIL", "error": "invalid_checker_json", "raw_stdout": stdout}
    else:
        payload = {"status": "FAIL", "error": "missing_checker_output"}
    payload["exit_code"] = int(result.returncode)
    if result.stderr:
        payload["stderr"] = result.stderr.strip()
    return payload


def _load_soak_history(path: Path) -> Dict[str, Any]:
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return {
            "schema_version": "promotion_soak_v1",
            "last_runtime_fingerprint": "",
            "mode_windows": {},
        }
    mode_windows = payload.get("mode_windows")
    if not isinstance(mode_windows, dict):
        mode_windows = {}
    return {
        "schema_version": "promotion_soak_v1",
        "last_runtime_fingerprint": str(payload.get("last_runtime_fingerprint") or ""),
        "mode_windows": mode_windows,
    }


def _update_soak_history(
    history: Dict[str, Any],
    *,
    now_ms: int,
    current_mode: str,
    runtime_fingerprint: str,
) -> Dict[str, Any]:
    mode_windows = history.get("mode_windows")
    if not isinstance(mode_windows, dict):
        mode_windows = {}
    if str(history.get("last_runtime_fingerprint") or "") != str(runtime_fingerprint):
        mode_windows = {}
    window = mode_windows.get(current_mode)
    if not isinstance(window, dict):
        window = {"start_ts_ms": int(now_ms), "last_ts_ms": int(now_ms)}
    raw_start = _maybe_int(window.get("start_ts_ms"))
    start_ts_ms = int(raw_start) if raw_start is not None else int(now_ms)
    if int(now_ms) < int(start_ts_ms):
        start_ts_ms = int(now_ms)
    window["start_ts_ms"] = int(start_ts_ms)
    window["last_ts_ms"] = int(now_ms)
    mode_windows[current_mode] = window
    return {
        "schema_version": "promotion_soak_v1",
        "last_runtime_fingerprint": str(runtime_fingerprint),
        "mode_windows": mode_windows,
    }


def _elapsed_ms(mode_windows: Dict[str, Any], mode: str) -> int:
    raw = mode_windows.get(mode)
    if not isinstance(raw, dict):
        return 0
    start_ts = _maybe_int(raw.get("start_ts_ms"))
    last_ts = _maybe_int(raw.get("last_ts_ms"))
    if start_ts is None or last_ts is None:
        return 0
    return int(max(0, int(last_ts) - int(start_ts)))


def _replay_status(
    *,
    replay_report_path: Optional[Path],
    replay_left: Optional[List[str]],
    replay_right: Optional[List[str]],
) -> Dict[str, Any]:
    if replay_report_path is not None:
        payload = _read_json_file(replay_report_path)
        if isinstance(payload, dict):
            return payload
        return {"status": "FAIL", "error": "invalid_replay_report_json"}
    if replay_left and replay_right:
        left_files = _resolve_decision_files(replay_left)
        right_files = _resolve_decision_files(replay_right)
        if not left_files or not right_files:
            return {
                "status": "FAIL",
                "error": "missing_replay_inputs",
                "left_files": [str(path) for path in left_files],
                "right_files": [str(path) for path in right_files],
            }
        payload = certify_decision_streams(left_files=left_files, right_files=right_files)
        payload["left_files"] = [str(path) for path in left_files]
        payload["right_files"] = [str(path) for path in right_files]
        return payload
    return {"status": "MISSING"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified promotion verdict with soak + replay + gate checks")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--lookback-hours", type=int, default=48)
    parser.add_argument("--constitution", default="config/constitution.yaml")
    parser.add_argument("--current-mode", default="OBSERVE", help="OBSERVE|PAPER|TRADE")
    parser.add_argument("--observe-soak-hours", type=float, default=None)
    parser.add_argument("--paper-soak-hours", type=float, default=None)
    parser.add_argument("--mode-history", default=".promotion_mode_history.json")
    parser.add_argument("--runtime-fingerprint", default=None)
    parser.add_argument("--now-ms", type=int, default=None)
    parser.add_argument("--replay-report", default=None, help="JSON output from scripts/replay_certify.py")
    parser.add_argument("--replay-left", nargs="+", default=None, help="Left replay decision file/dir/glob")
    parser.add_argument("--replay-right", nargs="+", default=None, help="Right replay decision file/dir/glob")
    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    constitution_path = Path(args.constitution).resolve()
    mode_history_path = Path(args.mode_history).resolve()
    current_mode = str(args.current_mode or "OBSERVE").upper()
    if current_mode not in {"OBSERVE", "PAPER", "TRADE"}:
        raise ValueError(f"unsupported_mode:{current_mode}")
    now_ms = int(args.now_ms) if args.now_ms is not None else int(time.time() * 1000)

    constitution = _load_constitution(constitution_path)
    trading_cfg = constitution.get("trading", {}) if isinstance(constitution, dict) else {}
    observe_soak_hours = float(
        args.observe_soak_hours
        if args.observe_soak_hours is not None
        else trading_cfg.get("observe_min_soak_hours", 48.0)
    )
    paper_soak_hours = float(
        args.paper_soak_hours
        if args.paper_soak_hours is not None
        else trading_cfg.get("paper_min_soak_hours", 48.0)
    )
    observe_soak_ms = int(max(0.0, observe_soak_hours) * 3_600_000.0)
    paper_soak_ms = int(max(0.0, paper_soak_hours) * 3_600_000.0)

    # Build integration report directly from current DB state.
    integration_report: Dict[str, Any]
    if not db_path.exists():
        integration_report = {"status": "FAIL", "error": f"db_missing:{db_path.as_posix()}"}
    else:
        with sqlite3.connect(db_path.as_posix()) as cx:
            integration_report = _build_report(
                cx=cx,
                lookback_start_ms=int(now_ms - max(1, int(args.lookback_hours)) * 3_600_000),
                now_ms=int(now_ms),
                clock_drift_max_ms=float(trading_cfg.get("clock_drift_max_ms", 250.0)),
                ws_starvation_max_ms=float(trading_cfg.get("ws_starvation_max_ms", 5000.0)),
                min_liveness_green_pct=float(trading_cfg.get("integration_min_liveness_green_pct", 95.0)),
                min_pstar_valid_dwell_pct=float(trading_cfg.get("integration_min_pstar_valid_dwell_pct", 98.0)),
            )

    promotion_gate_report = _run_promotion_gates(
        db_path=db_path,
        lookback_hours=int(args.lookback_hours),
        constitution=constitution_path,
    )
    replay_report = _replay_status(
        replay_report_path=Path(args.replay_report).resolve() if args.replay_report else None,
        replay_left=args.replay_left,
        replay_right=args.replay_right,
    )

    runtime_fingerprint = str(
        args.runtime_fingerprint
        or _default_runtime_fingerprint(
            [
                (_ROOT / "scripts" / "run_system.py").resolve(),
                (_ROOT / "data" / "polymarket_ws.py").resolve(),
                (_ROOT / "core" / "policy_gate.py").resolve(),
            ]
        )
    )
    history = _load_soak_history(mode_history_path)
    updated_history = _update_soak_history(
        history,
        now_ms=int(now_ms),
        current_mode=current_mode,
        runtime_fingerprint=runtime_fingerprint,
    )
    _write_json_file(mode_history_path, updated_history)
    mode_windows = updated_history.get("mode_windows", {})
    observe_elapsed_ms = _elapsed_ms(mode_windows, "OBSERVE")
    paper_elapsed_ms = _elapsed_ms(mode_windows, "PAPER")

    target_mode = {"OBSERVE": "PAPER", "PAPER": "TRADE", "TRADE": "TRADE"}[current_mode]
    block_reasons: List[str] = []
    if str(integration_report.get("status")) != "PASS":
        block_reasons.append("INTEGRATION_HEALTH_FAIL")
    if str(promotion_gate_report.get("status")) != "PASS":
        block_reasons.append("PROMOTION_GATES_FAIL")
    replay_status = str(replay_report.get("status") or "MISSING")
    if replay_status != "PASS":
        if replay_status == "MISSING":
            block_reasons.append("REPLAY_CERTIFY_MISSING")
        else:
            block_reasons.append("REPLAY_CERTIFY_FAIL")
    if target_mode in {"PAPER", "TRADE"} and observe_elapsed_ms < observe_soak_ms:
        block_reasons.append(f"SOAK_OBSERVE_MIN_{int(observe_soak_hours)}H")
    if target_mode == "TRADE" and paper_elapsed_ms < paper_soak_ms:
        block_reasons.append(f"SOAK_PAPER_MIN_{int(paper_soak_hours)}H")

    promotion_ready = len(block_reasons) == 0
    result = {
        "status": "PROMOTE" if promotion_ready else "HOLD",
        "promotion_ready": bool(promotion_ready),
        "current_mode": str(current_mode),
        "target_mode": str(target_mode),
        "block_reasons": sorted(set(block_reasons)),
        "soak": {
            "observe_min_hours": float(observe_soak_hours),
            "paper_min_hours": float(paper_soak_hours),
            "observe_elapsed_ms": int(observe_elapsed_ms),
            "paper_elapsed_ms": int(paper_elapsed_ms),
            "runtime_fingerprint": str(runtime_fingerprint),
            "history_path": mode_history_path.as_posix(),
        },
        "integration_health": integration_report,
        "promotion_gates": promotion_gate_report,
        "replay_certify": replay_report,
    }
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
    raise SystemExit(0 if promotion_ready else 1)


if __name__ == "__main__":
    main()
