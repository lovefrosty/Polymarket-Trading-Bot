from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from pandas.errors import DatabaseError

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DrillthroughContext


_default_db = os.getenv("RUNTIME_DB_PATH", "runtime.db")


def resolve_db_path() -> Path:
    if st is None:
        return Path(_default_db)
    try:
        runtime_db_path = st.secrets.get("runtime_db_path", _default_db)  # type: ignore[attr-defined]
    except Exception:
        runtime_db_path = _default_db
    return Path(runtime_db_path)


def _connect(db_path: Path) -> sqlite3.Connection:
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute("PRAGMA query_only = 1")
    except sqlite3.OperationalError:
        pass
    return cx


def table_exists(table: str, db_path: Optional[Path] = None) -> bool:
    path = db_path or resolve_db_path()
    if not path.exists():
        return False
    cx = _connect(path)
    try:
        row = cx.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    finally:
        cx.close()
    return row is not None


def existing_tables(db_path: Optional[Path] = None) -> List[str]:
    path = db_path or resolve_db_path()
    if not path.exists():
        return []
    cx = _connect(path)
    try:
        rows = cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        cx.close()
    return sorted(str(row[0]) for row in rows if row and row[0])


def require_sources(
    required_sources: Sequence[str],
    optional_sources: Sequence[str] = (),
    db_path: Optional[Path] = None,
) -> Tuple[bool, List[str], List[str]]:
    present = set(existing_tables(db_path))
    missing_required = [name for name in required_sources if name not in present]
    missing_optional = [name for name in optional_sources if name not in present]
    return len(missing_required) == 0, missing_required, missing_optional


def query_df(sql: str, params: Sequence[Any] = (), db_path: Optional[Path] = None) -> pd.DataFrame:
    path = db_path or resolve_db_path()
    if not path.exists():
        return pd.DataFrame()
    cx = _connect(path)
    try:
        return pd.read_sql_query(sql, cx, params=params)
    except (DatabaseError, sqlite3.OperationalError):
        return pd.DataFrame()
    finally:
        cx.close()


def q(sql: str, db_path: Optional[Path] = None) -> pd.DataFrame:
    return query_df(sql, db_path=db_path)


def safe_first(df: pd.DataFrame, col: str, default: Any = 0) -> Any:
    if df.empty or col not in df.columns:
        return default
    value = df.iloc[0][col]
    return value if pd.notna(value) else default


def safe_json(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def parse_reasons(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def classify_signal_action(action: str) -> bool:
    bad = {"FREEZE", "HOLD", "SKIP", "NONE", "NO_ACTION"}
    return action.upper() not in bad


def adapt_decisions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "expected_edge" in out.columns and "expected_cost" in out.columns:
        out["ev"] = out["expected_edge"].fillna(0.0) - out["expected_cost"].fillna(0.0)
    else:
        out["ev"] = 0.0

    strategy: List[str] = []
    gate_result: List[str] = []
    signal_flag: List[bool] = []
    for _, row in out.iterrows():
        payload = safe_json(row.get("policy_json"))
        strategy.append(str(payload.get("strategy") or payload.get("model_used") or "unknown"))
        action = str(row.get("action") or "")
        if action.upper() == "FREEZE":
            gate_result.append("FREEZE")
        else:
            gate_result.append("ALLOW" if classify_signal_action(action) else "BLOCK")
        signal_flag.append(classify_signal_action(action))
    out["strategy"] = strategy
    out["gate_result"] = gate_result
    out["is_signal"] = signal_flag
    if "ts_ms" in out.columns:
        out["ts"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True)
    return out


def adapt_orders(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    kinds: List[str] = []
    for _, row in out.iterrows():
        status = str(row.get("status") or "").lower()
        reason = str(row.get("reason") or "").lower()
        fsm_state = str(row.get("fsm_state") or "").lower()
        if "cancel" in status or "cancel" in reason:
            kinds.append("cancel")
        elif "replace" in status or "replace" in reason or "replace" in fsm_state:
            kinds.append("replace")
        elif "reject" in status or "reject" in reason:
            kinds.append("reject")
        elif status in {"open", "working", "new", "resting", "submitted", "accepted"}:
            kinds.append("open")
        else:
            kinds.append("other")
    out["event_kind"] = kinds
    if "ts_ms" in out.columns:
        out["ts"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True)
        out["age_s"] = ((_now_ms() - out["ts_ms"].astype(float)) / 1000.0).clip(lower=0)
    return out


def adapt_fills(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    hedge_flags: List[bool] = []
    slippage: List[Optional[float]] = []
    time_to_fill: List[Optional[float]] = []
    for _, row in out.iterrows():
        payload = safe_json(row.get("payload_json"))
        payload_text = json.dumps(payload).lower()
        is_hedge = bool(payload.get("is_hedge")) or "hedge" in payload_text
        hedge_flags.append(is_hedge)
        slippage.append(payload.get("slippage_bps"))
        primary_ts = payload.get("primary_fill_ts_ms")
        hedge_ts = payload.get("hedge_fill_ts_ms")
        if primary_ts is not None and hedge_ts is not None:
            try:
                time_to_fill.append((float(hedge_ts) - float(primary_ts)) / 1000.0)
            except (TypeError, ValueError):
                time_to_fill.append(None)
        else:
            time_to_fill.append(None)
    out["is_hedge"] = hedge_flags
    out["slippage_bps"] = slippage
    out["time_to_fill_s"] = time_to_fill
    if "ts_ms" in out.columns:
        out["ts"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True)
    return out


def query_evidence_rows(
    start_ts_ms: int,
    end_ts_ms: int,
    market: str = "ALL",
    token_id: str = "ALL",
    limit: int = 500,
) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []

    where_market = ""
    market_params: List[Any] = []
    if market != "ALL":
        where_market = " AND market=?"
        market_params.append(market)

    where_token = ""
    token_params: List[Any] = []
    if token_id != "ALL":
        where_token = " AND token_id=?"
        token_params.append(token_id)

    if table_exists("decisions"):
        rows.append(
            query_df(
                f"""
                SELECT ts_ms,
                       'runtime' AS source,
                       COALESCE(market,'') || ':' || COALESCE(token_id,'') || ':' || COALESCE(decision_id,'') AS entity,
                       action AS event_type,
                       reason_codes AS reason_code,
                       policy_json AS payload,
                       CASE WHEN UPPER(action)='FREEZE' THEN 'error' ELSE 'info' END AS severity
                FROM decisions
                WHERE ts_ms BETWEEN ? AND ? {where_market} {where_token}
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (start_ts_ms, end_ts_ms, *market_params, *token_params, limit),
            )
        )

    if table_exists("orders"):
        rows.append(
            query_df(
                f"""
                SELECT ts_ms,
                       'runtime' AS source,
                       COALESCE(token_id,'') || ':' || COALESCE(order_id,'') AS entity,
                       COALESCE(status,'order') AS event_type,
                       COALESCE(reason,'') AS reason_code,
                       payload_json AS payload,
                       CASE WHEN LOWER(COALESCE(status,'')) LIKE '%reject%' THEN 'error' ELSE 'info' END AS severity
                FROM orders
                WHERE ts_ms BETWEEN ? AND ? {where_token}
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (start_ts_ms, end_ts_ms, *token_params, limit),
            )
        )

    if table_exists("fills"):
        rows.append(
            query_df(
                f"""
                SELECT ts_ms,
                       'runtime' AS source,
                       COALESCE(token_id,'') || ':' || COALESCE(order_id,'') AS entity,
                       'fill' AS event_type,
                       '' AS reason_code,
                       payload_json AS payload,
                       'info' AS severity
                FROM fills
                WHERE ts_ms BETWEEN ? AND ? {where_token}
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (start_ts_ms, end_ts_ms, *token_params, limit),
            )
        )

    if table_exists("alerts"):
        rows.append(
            query_df(
                """
                SELECT ts_ms,
                       'runtime' AS source,
                       COALESCE(code,'') AS entity,
                       COALESCE(code,'alert') AS event_type,
                       COALESCE(code,'') AS reason_code,
                       payload_json AS payload,
                       LOWER(COALESCE(severity,'warn')) AS severity
                FROM alerts
                WHERE ts_ms BETWEEN ? AND ?
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (start_ts_ms, end_ts_ms, limit),
            )
        )

    if table_exists("logs"):
        rows.append(
            query_df(
                """
                SELECT ts_ms,
                       'runtime' AS source,
                       COALESCE(level,'') AS entity,
                       COALESCE(msg,'log') AS event_type,
                       '' AS reason_code,
                       payload_json AS payload,
                       CASE
                           WHEN UPPER(level)='ERROR' THEN 'error'
                           WHEN UPPER(level)='WARN' THEN 'warn'
                           ELSE 'info'
                       END AS severity
                FROM logs
                WHERE ts_ms BETWEEN ? AND ?
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (start_ts_ms, end_ts_ms, limit),
            )
        )

    if not rows:
        return pd.DataFrame(columns=["ts_ms", "source", "entity", "event_type", "reason_code", "payload", "severity"])

    out = pd.concat(rows, ignore_index=True)
    out = out.sort_values("ts_ms", ascending=False).head(limit)
    return out


def make_context_hash(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_drillthrough_context(
    metric_key: str,
    start_ts_ms: int,
    end_ts_ms: int,
    market: str,
    token_id: str,
    reason_codes: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> DrillthroughContext:
    body = {
        "metric_key": metric_key,
        "start_ts_ms": int(start_ts_ms),
        "end_ts_ms": int(end_ts_ms),
        "market": market,
        "token_id": token_id,
        "reason_codes": sorted(reason_codes or []),
        "evidence_refs": sorted(evidence_refs or []),
        "payload": payload or {},
    }
    context_hash = make_context_hash(body)
    context_id = f"ctx-{context_hash[:12]}"
    return DrillthroughContext(
        context_id=context_id,
        context_hash=context_hash,
        metric_key=metric_key,
        start_ts_ms=int(start_ts_ms),
        end_ts_ms=int(end_ts_ms),
        market=market,
        token_id=token_id,
        reason_codes=sorted(reason_codes or []),
        evidence_refs=sorted(evidence_refs or []),
        payload=payload or {},
    )


def now_utc_iso(ts_ms: Optional[int] = None) -> str:
    if ts_ms is None:
        ts_ms = _now_ms()
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def percentile(series: Iterable[float], pct: float) -> Optional[float]:
    values = [float(v) for v in series if v is not None and not math.isnan(float(v))]
    if not values:
        return None
    values.sort()
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * pct))))
    return float(values[idx])


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
