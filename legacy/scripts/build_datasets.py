from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

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
        _write_partitioned_exports(
            rows,
            dataset_name="ref_window",
            out_dir=out_dir,
            partition_keys=("symbol", "date"),
            formats=args.formats,
        )

    micro_rows = build_microstructure_dataset_from_decisions(
        decision_paths,
        label_index,
        feature_contract=feature_contract.get("features") if feature_contract else None,
    )
    _write_jsonl(out_dir / "micro_decisions.jsonl", micro_rows)
    _write_partitioned_exports(
        micro_rows,
        dataset_name="micro_decisions",
        out_dir=out_dir,
        partition_keys=("symbol", "date"),
        formats=args.formats,
    )

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
        "export_formats": args.formats,
        "partitioning": {"keys": ["symbol", "date"]},
    }
    (out_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    )


def _write_jsonl(path: Path, rows: List[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True, sort_keys=True) + "\n")


def _write_partitioned_exports(
    rows: List[dict],
    dataset_name: str,
    out_dir: Path,
    partition_keys: Tuple[str, str],
    formats: str,
) -> None:
    if not rows:
        return
    formats_list = [entry.strip().lower() for entry in formats.split(",") if entry.strip()]
    if not formats_list:
        return
    flat_rows = [_flatten_row(row) for row in rows]
    partitions = _partition_rows(flat_rows, partition_keys)
    for partition, bucket in partitions.items():
        symbol, date = partition
        for fmt in formats_list:
            if fmt == "jsonl":
                continue
            base = out_dir / f"{dataset_name}_{fmt}" / f"symbol={symbol}" / f"date={date}"
            base.mkdir(parents=True, exist_ok=True)
            if fmt == "csv":
                _write_csv(base / "part-000.csv", bucket)
            elif fmt == "parquet":
                _write_parquet(base / "part-000.parquet", bucket)
            else:
                raise ValueError(f"unsupported_format:{fmt}")


def _partition_rows(rows: List[dict], keys: Tuple[str, str]) -> Dict[Tuple[str, str], List[dict]]:
    partitions: Dict[Tuple[str, str], List[dict]] = {}
    for row in rows:
        symbol = str(row.get(keys[0]) or "unknown")
        date = str(row.get(keys[1]) or "unknown")
        partitions.setdefault((symbol, date), []).append(row)
    return partitions


def _flatten_row(row: dict) -> dict:
    out: Dict[str, object] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for sub_key, sub_val in value.items():
                out[f"{key}.{sub_key}"] = sub_val
        else:
            out[key] = value
    if "as_of_ts_ms" in row:
        out["date"] = _date_from_ms(row.get("as_of_ts_ms"))
    elif "window_start_ts_ms" in row:
        out["date"] = _date_from_ms(row.get("window_start_ts_ms"))
    else:
        out["date"] = None
    return out


def _date_from_ms(ts_ms: object) -> str:
    from datetime import datetime, timezone

    try:
        value = int(ts_ms)
    except (TypeError, ValueError):
        return "unknown"
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _write_csv(path: Path, rows: List[dict]) -> None:
    columns = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_parquet(path: Path, rows: List[dict]) -> None:
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build datasets from EventTape and DecisionTape")
    parser.add_argument("--logs", default="./logs", help="Directory containing event/decision tapes")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols to build")
    parser.add_argument("--out", default="./artifacts", help="Output artifacts directory")
    parser.add_argument(
        "--formats",
        default="jsonl,csv,parquet",
        help="Comma-separated export formats (jsonl,csv,parquet)",
    )
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
