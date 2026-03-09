from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


CONTRACT_REQUIRED_FIELDS = {
    "decision_id",
    "as_of_ts_ms",
    "action",
    "reasons",
    "evidence",
    "outputs",
    "mode",
    "gate_version",
}


def _maybe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_reasons(record: Dict[str, Any]) -> List[str]:
    for key in ("reasons", "reason_codes", "policy_codes"):
        raw = record.get(key)
        if isinstance(raw, list):
            return [str(item) for item in raw]
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                return [part for part in (item.strip() for item in text.split(",")) if part]
    gates = record.get("gates")
    if isinstance(gates, dict):
        raw = gates.get("reasons")
        if isinstance(raw, list):
            return [str(item) for item in raw]
    return []


def _extract_action(record: Dict[str, Any], reasons: List[str]) -> str:
    raw_action = str(record.get("action") or "").strip().upper()
    if raw_action in {"ALLOW", "BLOCK"}:
        return raw_action
    if raw_action in {"QUOTE", "HOLD", "FREEZE", "SKIP"}:
        return "ALLOW" if raw_action == "QUOTE" else "BLOCK"
    gates = record.get("gates")
    if isinstance(gates, dict) and isinstance(gates.get("allow"), bool):
        return "ALLOW" if bool(gates.get("allow")) else "BLOCK"
    if reasons:
        return "BLOCK"
    return "ALLOW"


def _extract_outputs(record: Dict[str, Any]) -> Dict[str, Any]:
    notes = record.get("notes")
    notes_map = notes if isinstance(notes, dict) else {}
    return {
        "p_market_exec_buy": _maybe_float(record.get("p_market_exec_buy")),
        "p_market_exec_sell": _maybe_float(record.get("p_market_exec_sell")),
        "p_market_mid": _maybe_float(record.get("p_market_mid")),
        "size": _maybe_float(
            notes_map.get("target_size")
            or notes_map.get("size")
            or notes_map.get("order_size")
            or notes_map.get("qty")
        ),
        "fsm_state": str(record.get("fsm_state") or notes_map.get("fsm_state") or ""),
    }


def _derive_decision_id(record: Dict[str, Any], idx: int) -> str:
    direct = record.get("decision_id")
    if direct is not None and str(direct).strip():
        return str(direct)
    token = str(record.get("token_id") or record.get("asset_id") or "unknown")
    ts_ms = _maybe_int(record.get("as_of_ts_ms")) or _maybe_int(record.get("t_decision_wall_ms")) or 0
    return f"derived:{int(ts_ms)}:{token}:{int(idx)}"


def _to_contract_record(record: Dict[str, Any], idx: int) -> Dict[str, Any]:
    reasons = _extract_reasons(record)
    as_of_ts_ms = _maybe_int(record.get("as_of_ts_ms")) or _maybe_int(record.get("t_decision_wall_ms")) or 0
    gate_version = "unknown"
    gates = record.get("gates")
    if isinstance(gates, dict):
        gate_version = str(gates.get("version") or gate_version)
    gate_version = str(record.get("gate_version") or gate_version)
    mode = str(record.get("mode") or "")
    if not mode:
        notes = record.get("notes")
        if isinstance(notes, dict):
            mode = str(notes.get("mode") or "")
    contract = {
        "decision_id": _derive_decision_id(record, idx=idx),
        "as_of_ts_ms": int(as_of_ts_ms),
        "action": _extract_action(record, reasons=reasons),
        "reasons": list(reasons),
        "evidence": {
            "pointers": [f"record:{int(idx)}"],
        },
        "outputs": _extract_outputs(record),
        "mode": str(mode or "unknown"),
        "gate_version": str(gate_version or "unknown"),
    }
    return contract


def _validate_contract_record(record: Dict[str, Any]) -> Optional[str]:
    missing = sorted(CONTRACT_REQUIRED_FIELDS - set(record.keys()))
    if missing:
        return f"missing_fields:{','.join(missing)}"
    if record.get("action") not in {"ALLOW", "BLOCK"}:
        return "invalid_action"
    if not isinstance(record.get("reasons"), list):
        return "invalid_reasons"
    if not isinstance(record.get("evidence"), dict):
        return "invalid_evidence"
    if not isinstance(record.get("outputs"), dict):
        return "invalid_outputs"
    if not isinstance(_maybe_int(record.get("as_of_ts_ms")), int):
        return "invalid_as_of_ts_ms"
    return None


def _load_contract_stream(decision_files: Sequence[Path]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    stream: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    idx = 0
    for path in decision_files:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            pointer = f"{path.as_posix()}#L{line_no}"
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append({"pointer": pointer, "error": f"json_decode:{exc}"})
                continue
            if not isinstance(payload, dict):
                errors.append({"pointer": pointer, "error": "record_not_object"})
                continue
            contract = _to_contract_record(payload, idx=idx)
            err = _validate_contract_record(contract)
            if err:
                errors.append({"pointer": pointer, "error": err})
                continue
            stream.append(contract)
            idx += 1
    return stream, errors


def _diff_streams(left: Sequence[Dict[str, Any]], right: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mismatches: List[Dict[str, Any]] = []
    max_len = max(len(left), len(right))
    for idx in range(max_len):
        left_item = left[idx] if idx < len(left) else None
        right_item = right[idx] if idx < len(right) else None
        if left_item == right_item:
            continue
        mismatches.append(
            {
                "index": int(idx),
                "left": left_item,
                "right": right_item,
            }
        )
    return mismatches


def _resolve_decision_files(inputs: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    for value in inputs:
        path = Path(value)
        if any(ch in value for ch in ("*", "?", "[")):
            files.extend(Path(match) for match in sorted(glob.glob(value)))
            continue
        if path.is_dir():
            files.extend(sorted(path.glob("decision_*.jsonl")))
            continue
        if path.exists():
            files.append(path)
    normalized = sorted({path.resolve() for path in files})
    return [Path(path) for path in normalized]


def certify_decision_streams(left_files: Sequence[Path], right_files: Sequence[Path]) -> Dict[str, Any]:
    left_stream, left_errors = _load_contract_stream(left_files)
    right_stream, right_errors = _load_contract_stream(right_files)
    contract_errors = left_errors + right_errors
    if contract_errors:
        return {
            "status": "FAIL",
            "tier": 1,
            "left_count": int(len(left_stream)),
            "right_count": int(len(right_stream)),
            "mismatch_count": 0,
            "contract_errors": contract_errors,
        }
    mismatches = _diff_streams(left_stream, right_stream)
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "tier": 1,
        "left_count": int(len(left_stream)),
        "right_count": int(len(right_stream)),
        "mismatch_count": int(len(mismatches)),
        "first_mismatch": mismatches[0] if mismatches else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay certification for decision stream Tier-1 outputs")
    parser.add_argument("--left", nargs="+", required=True, help="Left decision file(s), directory, or glob")
    parser.add_argument("--right", nargs="+", required=True, help="Right decision file(s), directory, or glob")
    parser.add_argument("--output", default=None, help="Optional path to write JSON report")
    args = parser.parse_args()

    left_files = _resolve_decision_files(args.left)
    right_files = _resolve_decision_files(args.right)
    if not left_files or not right_files:
        result = {
            "status": "FAIL",
            "tier": 1,
            "error": "missing_decision_files",
            "left_files": [str(path) for path in left_files],
            "right_files": [str(path) for path in right_files],
        }
        encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
        print(encoded)
        if args.output:
            Path(args.output).write_text(encoded + "\n", encoding="utf-8")
        raise SystemExit(1)

    result = certify_decision_streams(left_files=left_files, right_files=right_files)
    result["left_files"] = [str(path) for path in left_files]
    result["right_files"] = [str(path) for path in right_files]
    encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    print(encoded)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    raise SystemExit(0 if result.get("status") == "PASS" else 1)


if __name__ == "__main__":
    main()
