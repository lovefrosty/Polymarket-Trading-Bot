from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def adapt_reference_file(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    state: dict[str, dict[str, dict[str, Any]]] = {}
    stats = {
        "input_path": str(path),
        "records_seen": 0,
        "reference_records": 0,
        "symbols_seen": [],
        "sources_seen": [],
        "emitted": 0,
        "compatible": False,
        "skip_reason": None,
    }
    symbols_seen: set[str] = set()
    sources_seen: set[str] = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        stats["records_seen"] += 1
        record = json.loads(line)
        if record.get("channel") != "reference":
            continue
        stats["reference_records"] += 1
        raw = record.get("raw") or {}
        symbol = str(raw.get("symbol") or record.get("market") or "").upper()
        source = _normalize_source(raw.get("source"))
        value = _to_float(raw.get("value") or raw.get("mid") or raw.get("price"))
        ts_event_ms = _first_int(record.get("t_event_ms"), raw.get("t_event_ms"), record.get("t_recv_wall_ms"))
        t_recv_wall_ms = _first_int(record.get("t_recv_wall_ms"), ts_event_ms)
        t_recv_mono_ns = _first_int(record.get("t_recv_mono_ns"), 0)
        if not symbol or source not in {"spot", "perp"} or value is None or ts_event_ms is None or t_recv_wall_ms is None:
            continue
        symbols_seen.add(symbol)
        sources_seen.add(source)
        state.setdefault(symbol, {})[source] = {
            "value": value,
            "t_event_ms": ts_event_ms,
            "t_recv_wall_ms": t_recv_wall_ms,
            "t_recv_mono_ns": t_recv_mono_ns,
        }
        pair = state[symbol]
        if "spot" not in pair or "perp" not in pair:
            continue
        spot_value = float(pair["spot"]["value"])
        perp_value = float(pair["perp"]["value"])
        mid = (spot_value + perp_value) / 2.0
        if mid <= 0:
            continue
        event_ts_ms = max(int(pair["spot"]["t_event_ms"]), int(pair["perp"]["t_event_ms"]))
        recv_wall_ms = max(int(pair["spot"]["t_recv_wall_ms"]), int(pair["perp"]["t_recv_wall_ms"]))
        recv_mono_ns = max(int(pair["spot"]["t_recv_mono_ns"]), int(pair["perp"]["t_recv_mono_ns"]))
        diff_bps = abs(spot_value - perp_value) / mid * 10000.0
        outputs.append(
            {
                "run_id": record.get("run_id") or path.stem,
                "channel": "reference",
                "event_type": "reference_tick",
                "market": symbol,
                "asset_id": None,
                "t_event_ms": event_ts_ms,
                "t_recv_wall_ms": recv_wall_ms,
                "t_recv_wall_iso": _iso_from_ms(recv_wall_ms),
                "t_recv_mono_ns": recv_mono_ns,
                "raw": {
                    "symbol": symbol,
                    "value": mid,
                    "sources": ["spot", "perp"],
                    "spot_value": spot_value,
                    "perp_value": perp_value,
                    "diff_bps": diff_bps,
                    "confidence": 1.0,
                    "t_event_ms": event_ts_ms,
                },
                "parse_warnings": [],
                "out_of_order": False,
            }
        )

    stats["symbols_seen"] = sorted(symbols_seen)
    stats["sources_seen"] = sorted(sources_seen)
    stats["emitted"] = len(outputs)
    stats["compatible"] = len(outputs) > 0
    if len(outputs) == 0:
        stats["skip_reason"] = "missing_spot_or_perp_pair"
    return outputs, stats


def adapt_reference_paths(paths: list[Path], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    file_stats: list[dict[str, Any]] = []
    emitted_paths: list[str] = []
    for path in paths:
        rows, stats = adapt_reference_file(path)
        file_stats.append(stats)
        if not rows:
            continue
        out_path = out_dir / _output_name(path)
        with out_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True, sort_keys=True) + "\n")
        emitted_paths.append(str(out_path))
    manifest = {
        "schema_version": "reference_enriched_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_count": len(paths),
        "output_count": len(emitted_paths),
        "compatible_count": sum(1 for item in file_stats if item["compatible"]),
        "outputs": emitted_paths,
        "files": file_stats,
    }
    (out_dir / "reference_enriched_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adapt raw reference tapes into enriched spot+perp reference events")
    parser.add_argument("--inputs", nargs="+", required=True, help="Reference tape paths or glob patterns")
    parser.add_argument("--out-dir", required=True, help="Directory for enriched outputs")
    return parser.parse_args()


def _resolve_paths(patterns: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for pattern in patterns:
        matches = sorted(Path().glob(pattern)) if not Path(pattern).exists() else [Path(pattern)]
        for match in matches:
            if match.is_file():
                resolved.append(match.resolve())
    seen: set[str] = set()
    unique: list[Path] = []
    for path in resolved:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _output_name(path: Path) -> str:
    stem = path.stem
    if stem.startswith("reference_"):
        stem = stem[len("reference_") :]
    return f"reference_enriched_{stem}.jsonl"


def _normalize_source(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "spot":
        return "spot"
    if text == "perp" or text.endswith("_perp") or "perp" in text:
        return "perp"
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        try:
            if value is None:
                continue
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    args = _parse_args()
    paths = _resolve_paths(args.inputs)
    if not paths:
        raise SystemExit("no_reference_inputs")
    manifest = adapt_reference_paths(paths, Path(args.out_dir))
    print(json.dumps(manifest, separators=(",", ":"), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
