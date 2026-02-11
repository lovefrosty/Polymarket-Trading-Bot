from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.sqlite_store import SQLiteStore


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _table_exists(db_path: Path, name: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path.as_posix()) as cx:
        row = cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _export_query_jsonl(db_path: Path, sql: str, params: Sequence[Any], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with sqlite3.connect(db_path.as_posix()) as cx:
        cur = cx.execute(sql, params)
        columns = [d[0] for d in cur.description]
        with out_path.open("w", encoding="utf-8") as fh:
            for row in cur.fetchall():
                payload = {key: row[idx] for idx, key in enumerate(columns)}
                fh.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
                fh.write("\n")
                row_count += 1
    return row_count


def _load_context_json(path: Optional[str], fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path:
        return fallback
    p = Path(path)
    if not p.exists():
        return fallback
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return fallback


def _config_fingerprint() -> Dict[str, Any]:
    files = [
        Path("config/constitution.yaml"),
        Path("config/markets.yaml"),
        Path("config/settings.py"),
    ]
    result: Dict[str, Any] = {}
    for file in files:
        if not file.exists():
            result[file.as_posix()] = None
            continue
        result[file.as_posix()] = {
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "size": file.stat().st_size,
        }
    return result


def _build_incident_bundle(
    db_path: Path,
    out_dir: Path,
    start_ts_ms: int,
    end_ts_ms: int,
    market: str,
    token_id: str,
    context_json_path: Optional[str],
) -> Path:
    bundle_id = datetime.now(timezone.utc).strftime("incident_%Y%m%dT%H%M%SZ")
    bundle_dir = out_dir / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    where_market = ""
    params_market: List[Any] = []
    if market != "ALL":
        where_market = " AND market=?"
        params_market.append(market)

    where_token = ""
    params_token: List[Any] = []
    if token_id != "ALL":
        where_token = " AND token_id=?"
        params_token.append(token_id)

    fallback_context = {
        "metric_key": "MANUAL_INCIDENT_EXPORT",
        "start_ts_ms": int(start_ts_ms),
        "end_ts_ms": int(end_ts_ms),
        "market": market,
        "token_id": token_id,
        "reason_codes": [],
        "evidence_refs": [],
        "payload": {},
    }
    context = _load_context_json(context_json_path, fallback_context)
    canonical_context = json.dumps(context, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    context_hash = hashlib.sha256(canonical_context.encode("utf-8")).hexdigest()
    context_id = str(context.get("context_id") or f"ctx-{context_hash[:12]}")
    context["context_id"] = context_id
    context["context_hash"] = context_hash

    context_path = bundle_dir / "context.json"
    context_path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")

    row_counts: Dict[str, int] = {}
    file_hashes: Dict[str, str] = {}

    if _table_exists(db_path, "logs"):
        logs_path = bundle_dir / "logs.jsonl"
        row_counts["logs"] = _export_query_jsonl(
            db_path,
            "SELECT ts_ms, level, msg, payload_json FROM logs WHERE ts_ms BETWEEN ? AND ? ORDER BY ts_ms",
            (start_ts_ms, end_ts_ms),
            logs_path,
        )
        file_hashes["logs.jsonl"] = _sha256(logs_path)

    if _table_exists(db_path, "decisions"):
        decisions_path = bundle_dir / "decisions.jsonl"
        row_counts["decisions"] = _export_query_jsonl(
            db_path,
            f"""
            SELECT ts_ms, decision_id, market, token_id, action, reason_codes, p_hat, expected_edge, expected_cost,
                   decision_ts_event_ms, book_asof_ts_ms, pstar_asof_ts_ms, max_feature_ts_ms, policy_json
            FROM decisions
            WHERE ts_ms BETWEEN ? AND ? {where_market} {where_token}
            ORDER BY ts_ms
            """,
            (start_ts_ms, end_ts_ms, *params_market, *params_token),
            decisions_path,
        )
        file_hashes["decisions.jsonl"] = _sha256(decisions_path)

    if _table_exists(db_path, "market_data_book"):
        book_path = bundle_dir / "book_events.jsonl"
        row_counts["book_events"] = _export_query_jsonl(
            db_path,
            f"""
            SELECT ts_ms, token_id, side, price, size, source
            FROM market_data_book
            WHERE ts_ms BETWEEN ? AND ? {where_token}
            ORDER BY ts_ms
            """,
            (start_ts_ms, end_ts_ms, *params_token),
            book_path,
        )
        file_hashes["book_events.jsonl"] = _sha256(book_path)

    system_state_path = bundle_dir / "system_state.json"
    if _table_exists(db_path, "system_state"):
        with sqlite3.connect(db_path.as_posix()) as cx:
            row = cx.execute(
                "SELECT as_of_ts, is_frozen, reasons, mode, payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1"
            ).fetchone()
        payload = {
            "as_of_ts": row[0] if row else None,
            "is_frozen": row[1] if row else None,
            "reasons": row[2] if row else None,
            "mode": row[3] if row else None,
            "payload_json": row[4] if row else None,
        }
    else:
        payload = {}
    system_state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    file_hashes["system_state.json"] = _sha256(system_state_path)

    cfg_path = bundle_dir / "config_fingerprint.json"
    cfg_path.write_text(json.dumps(_config_fingerprint(), indent=2, sort_keys=True), encoding="utf-8")
    file_hashes["config_fingerprint.json"] = _sha256(cfg_path)

    manifest = {
        "manifest_version": "incident_bundle_v0",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "context_id": context_id,
        "context_hash": context_hash,
        "time_window": {"start_ts_ms": int(start_ts_ms), "end_ts_ms": int(end_ts_ms)},
        "filters": {"market": market, "token_id": token_id},
        "row_counts": row_counts,
        "file_hashes": file_hashes,
        "schema_versions": {
            "context": "v1",
            "logs": "v1",
            "decisions": "v1",
            "book_events": "v1",
            "system_state": "v1",
        },
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SQLite runtime tables to deterministic JSONL")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--out-dir", default="logs/export")
    parser.add_argument("--incident-bundle", action="store_true", help="Write strict incident bundle v0")
    parser.add_argument("--start-ts-ms", type=int, default=None)
    parser.add_argument("--end-ts-ms", type=int, default=None)
    parser.add_argument("--market", default="ALL")
    parser.add_argument("--token-id", default="ALL")
    parser.add_argument("--context-json", default=None, help="Path to context.json payload")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.incident_bundle:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ts_ms = int(args.start_ts_ms if args.start_ts_ms is not None else now_ms - 60 * 60 * 1000)
        end_ts_ms = int(args.end_ts_ms if args.end_ts_ms is not None else now_ms)
        manifest_path = _build_incident_bundle(
            db_path=db_path,
            out_dir=out_dir,
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
            market=str(args.market),
            token_id=str(args.token_id),
            context_json_path=args.context_json,
        )
        print(manifest_path.as_posix())
        return

    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    db = SQLiteStore(db_path)
    try:
        db.export_table_jsonl("market_data_book", out_dir / f"market_{date_key}.jsonl", order_by="ts_ms")
        db.export_table_jsonl("pstar", out_dir / f"reference_{date_key}.jsonl", order_by="ts_ms")
        db.export_table_jsonl("decisions", out_dir / f"decision_{date_key}.jsonl", order_by="ts_ms")
        db.export_table_jsonl("orders", out_dir / f"trade_{date_key}.jsonl", order_by="ts_ms")
    finally:
        db.close()


if __name__ == "__main__":
    main()
