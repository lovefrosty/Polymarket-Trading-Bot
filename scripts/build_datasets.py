from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from core.dataset_builder import build_microstructure_dataset_from_decisions, build_reference_window_dataset


def main() -> None:
    args = _parse_args()
    log_dir = Path(args.logs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    reference_paths = sorted(log_dir.glob("reference_*.jsonl"))
    decision_paths = sorted(log_dir.glob("decision_*.jsonl"))
    symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]

    ref_counts: Dict[str, int] = {}
    label_index: Dict[tuple[str, int], int] = {}

    feature_contract = _load_contract(Path("feature_contract.json"))
    label_contract = _load_contract(Path("label_contract.json"))

    for symbol in symbols:
        rows = build_reference_window_dataset(reference_paths, symbol)
        ref_counts[symbol] = len(rows)
        for row in rows:
            label_index[(symbol, int(row["window_start_ts_ms"]))] = int(row["label_up"])
        _write_jsonl(out_dir / f"ref_window_{symbol}.jsonl", rows)

    micro_rows = build_microstructure_dataset_from_decisions(
        decision_paths,
        label_index,
        feature_contract=feature_contract.get("features") if feature_contract else None,
    )
    _write_jsonl(out_dir / "micro_decisions.jsonl", micro_rows)

    manifest = {
        "schema_version": "dataset_manifest_v1",
        "symbols": symbols,
        "reference_paths": [str(p) for p in reference_paths],
        "decision_paths": [str(p) for p in decision_paths],
        "ref_counts": ref_counts,
        "micro_count": len(micro_rows),
        "created_at": _utc_iso(),
        "git_sha": _git_sha(),
        "feature_contract_path": "feature_contract.json" if feature_contract else None,
        "label_contract_path": "label_contract.json" if label_contract else None,
    }
    (out_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    )


def _write_jsonl(path: Path, rows: List[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True, sort_keys=True) + "\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build datasets from EventTape and DecisionTape")
    parser.add_argument("--logs", default="./logs", help="Directory containing event/decision tapes")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols to build")
    parser.add_argument("--out", default="./artifacts", help="Output artifacts directory")
    return parser.parse_args()


def _load_contract(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _git_sha() -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception:
        return None


def _utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


if __name__ == "__main__":
    main()
