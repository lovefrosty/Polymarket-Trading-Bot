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
_REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_db_path() -> Path:
    if st is None:
        return Path(_default_db)
    try:
        runtime_db_path = st.session_state.get("runtime_db_override_path") or st.secrets.get("runtime_db_path", _default_db)  # type: ignore[attr-defined]
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


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def runtime_root_for_db(db_path: Optional[Path] = None) -> Path:
    path = (db_path or resolve_db_path()).resolve()
    if path.is_dir():
        return path
    return path.parent


def get_run_status(runtime_root: Optional[Path] = None, db_path: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root) if runtime_root is not None else runtime_root_for_db(db_path)
    return read_json_file(root / "meta" / "status.json")


def get_run_summary(runtime_root: Optional[Path] = None, db_path: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root) if runtime_root is not None else runtime_root_for_db(db_path)
    return read_json_file(root / "meta" / "run_summary.json")


def _load_constitution_defaults() -> Dict[str, Any]:
    path = _REPO_ROOT / "config" / "constitution.yaml"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_runtime_config_snapshot(runtime_root: Optional[Path] = None, db_path: Optional[Path] = None) -> Dict[str, Any]:
    constitution = _load_constitution_defaults()
    trading = constitution.get("trading") if isinstance(constitution.get("trading"), dict) else {}
    policy = constitution.get("policy") if isinstance(constitution.get("policy"), dict) else {}
    execution = constitution.get("execution") if isinstance(constitution.get("execution"), dict) else {}

    state = query_df(
        "SELECT payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1",
        db_path=db_path,
    )
    state_payload = safe_json(safe_first(state, "payload_json", "{}"))
    state_config = state_payload.get("config") if isinstance(state_payload.get("config"), dict) else {}
    status_config = get_run_status(runtime_root=runtime_root, db_path=db_path).get("config")
    if not isinstance(status_config, dict):
        status_config = {}

    merged: Dict[str, Any] = {
        "per_market_gross_cap_usd": _float_or_none(trading.get("cap_gross_usd")),
        "portfolio_gross_cap_usd": _float_or_none(trading.get("cap_total_gross_usd")),
        "portfolio_net_cap_ratio": _float_or_none(trading.get("cap_total_net_ratio")),
        "per_market_net_cap_ratio": _float_or_none(trading.get("cap_net_ratio")),
        "daily_loss_limit_usdc": _float_or_none(trading.get("max_daily_loss_usdc")),
        "daily_notional_limit_usdc": _float_or_none(trading.get("max_daily_notional_usdc")),
        "maker_quote_size": _float_or_none(execution.get("maker_quote_size")),
        "maker_half_spread_bps": _float_or_none(execution.get("maker_half_spread_bps")),
        "risk_padding_bps": _float_or_none(execution.get("risk_padding_bps")),
        "max_spread_bps": _float_or_none(policy.get("max_spread_bps")),
        "max_slippage_bps": _float_or_none(policy.get("max_slippage_bps")),
    }
    for key in ("trade_size", "max_size", "reverse_position_min_size", "min_order_size", "within_pct", "fee_bps", "fee_mode", "min_size", "fallback_size"):
        value = status_config.get(key, state_config.get(key))
        if key == "fee_mode":
            merged[key] = str(value) if value is not None else None
        else:
            merged[key] = _float_or_none(value)
    return merged


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


def _symbol_from_market_slug(slug: Any) -> Optional[str]:
    if slug is None:
        return None
    text = str(slug).strip()
    if not text:
        return None
    head = text.split("-", 1)[0].strip().upper()
    return head or None


def _latest_token_market(as_of_ts_ms: int, db_path: Optional[Path] = None) -> pd.DataFrame:
    if not table_exists("decisions", db_path=db_path):
        return pd.DataFrame(columns=["token_id", "market_slug", "symbol"])
    rows = query_df(
        """
        WITH ranked AS (
          SELECT
            token_id,
            market,
            ts_ms,
            decision_id,
            ROW_NUMBER() OVER (
              PARTITION BY token_id
              ORDER BY ts_ms DESC, COALESCE(decision_id,'') DESC
            ) AS rn
          FROM decisions
          WHERE ts_ms <= ?
        )
        SELECT token_id, market AS market_slug
        FROM ranked
        WHERE rn = 1
        ORDER BY token_id ASC
        """,
        (int(as_of_ts_ms),),
        db_path=db_path,
    )
    if rows.empty:
        rows["symbol"] = pd.Series(dtype=object)
        return rows
    out = rows.copy()
    out["symbol"] = out["market_slug"].apply(_symbol_from_market_slug)
    return out


def get_mark_snapshot(as_of_ts_ms: int, db_path: Optional[Path] = None) -> pd.DataFrame:
    token_rows: List[str] = []
    if table_exists("inventory", db_path=db_path):
        inv_tokens = query_df(
            "SELECT DISTINCT token_id FROM inventory WHERE ts_ms <= ? ORDER BY token_id ASC",
            (int(as_of_ts_ms),),
            db_path=db_path,
        )
        token_rows.extend(str(item) for item in inv_tokens.get("token_id", pd.Series(dtype=object)).dropna().tolist())
    token_market = _latest_token_market(as_of_ts_ms=int(as_of_ts_ms), db_path=db_path)
    token_rows.extend(str(item) for item in token_market.get("token_id", pd.Series(dtype=object)).dropna().tolist())
    token_ids = sorted(set(token_rows))
    if not token_ids:
        return pd.DataFrame(
            columns=[
                "token_id",
                "market_slug",
                "symbol",
                "mark",
                "mark_source",
                "mark_ts_ms",
                "pstar_value",
                "mid_value",
            ]
        )

    market_map: Dict[str, Dict[str, Any]] = {}
    for _, row in token_market.iterrows():
        token_id = str(row.get("token_id") or "")
        if not token_id:
            continue
        market_map[token_id] = {
            "market_slug": row.get("market_slug"),
            "symbol": row.get("symbol"),
        }

    pstar_map: Dict[str, Dict[str, Any]] = {}
    if table_exists("pstar", db_path=db_path):
        pstar_rows = query_df(
            """
            WITH ranked AS (
              SELECT
                symbol, value, ts_ms,
                ROW_NUMBER() OVER (
                  PARTITION BY symbol
                  ORDER BY ts_ms DESC
                ) AS rn
              FROM pstar
              WHERE ts_ms <= ?
                AND valid = 1
                AND value IS NOT NULL
            )
            SELECT symbol, value, ts_ms
            FROM ranked
            WHERE rn = 1
            ORDER BY symbol ASC
            """,
            (int(as_of_ts_ms),),
            db_path=db_path,
        )
        for _, row in pstar_rows.iterrows():
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            pstar_map[symbol] = {
                "value": row.get("value"),
                "ts_ms": row.get("ts_ms"),
            }

    mid_map: Dict[str, Dict[str, Any]] = {}
    if table_exists("market_data_book", db_path=db_path):
        mids = query_df(
            """
            WITH latest_side AS (
              SELECT token_id, LOWER(side) AS side, MAX(ts_ms) AS max_ts
              FROM market_data_book
              WHERE ts_ms <= ?
                AND LOWER(side) IN ('buy', 'sell')
              GROUP BY token_id, LOWER(side)
            ),
            side_levels AS (
              SELECT b.token_id, LOWER(b.side) AS side, b.ts_ms, b.price
              FROM market_data_book b
              INNER JOIN latest_side s
                ON s.token_id = b.token_id
               AND s.side = LOWER(b.side)
               AND s.max_ts = b.ts_ms
            )
            SELECT
              token_id,
              MAX(CASE WHEN side='buy' THEN price END) AS best_bid,
              MIN(CASE WHEN side='sell' THEN price END) AS best_ask,
              MAX(ts_ms) AS ts_ms
            FROM side_levels
            GROUP BY token_id
            ORDER BY token_id ASC
            """,
            (int(as_of_ts_ms),),
            db_path=db_path,
        )
        for _, row in mids.iterrows():
            token_id = str(row.get("token_id") or "")
            if not token_id:
                continue
            bid = row.get("best_bid")
            ask = row.get("best_ask")
            mid_val: Optional[float]
            if bid is not None and not pd.isna(bid) and ask is not None and not pd.isna(ask):
                mid_val = (float(bid) + float(ask)) / 2.0
            elif bid is not None and not pd.isna(bid):
                mid_val = float(bid)
            elif ask is not None and not pd.isna(ask):
                mid_val = float(ask)
            else:
                mid_val = None
            mid_map[token_id] = {"value": mid_val, "ts_ms": row.get("ts_ms")}

    rows: List[Dict[str, Any]] = []
    for token_id in token_ids:
        market_meta = market_map.get(token_id, {})
        symbol = market_meta.get("symbol")
        pstar = pstar_map.get(str(symbol or ""))
        mid = mid_map.get(token_id)
        mark: Optional[float] = None
        mark_ts_ms: Optional[int] = None
        mark_source = "UNAVAILABLE"
        pstar_value = _float_or_none(pstar.get("value")) if pstar else None
        mid_value = _float_or_none(mid.get("value")) if mid else None
        if pstar_value is not None:
            mark = float(pstar_value)
            mark_ts_ms = _int_or_none(pstar.get("ts_ms")) if pstar else None
            mark_source = "PSTAR"
        elif mid_value is not None:
            mark = float(mid_value)
            mark_ts_ms = _int_or_none(mid.get("ts_ms")) if mid else None
            mark_source = "MID"
        rows.append(
            {
                "token_id": token_id,
                "market_slug": market_meta.get("market_slug"),
                "symbol": symbol,
                "mark": mark,
                "mark_source": mark_source,
                "mark_ts_ms": mark_ts_ms,
                "pstar_value": pstar_value,
                "mid_value": mid_value,
            }
        )
    return pd.DataFrame(rows).sort_values("token_id").reset_index(drop=True)


def get_positions_as_of(as_of_ts_ms: int, db_path: Optional[Path] = None) -> pd.DataFrame:
    if not table_exists("inventory", db_path=db_path):
        return pd.DataFrame(
            columns=[
                "as_of_ts_ms",
                "token_id",
                "market_slug",
                "symbol",
                "yes_qty",
                "no_qty",
                "net_shares",
                "avg_entry",
                "mark_source",
                "mark",
                "unrealized_pnl",
            ]
        )

    inventory_rows = query_df(
        """
        WITH ranked AS (
          SELECT
            token_id, ts_ms, yes_qty, no_qty, usdc, source,
            ROW_NUMBER() OVER (
              PARTITION BY token_id
              ORDER BY ts_ms DESC
            ) AS rn
          FROM inventory
          WHERE ts_ms <= ?
        )
        SELECT token_id, ts_ms, yes_qty, no_qty, usdc, source
        FROM ranked
        WHERE rn = 1
        ORDER BY token_id ASC
        """,
        (int(as_of_ts_ms),),
        db_path=db_path,
    )
    if inventory_rows.empty:
        return inventory_rows

    fills_agg = pd.DataFrame(columns=["token_id", "buy_qty", "buy_notional", "sell_qty", "sell_notional"])
    if table_exists("fills", db_path=db_path):
        fills_agg = query_df(
            """
            SELECT
              token_id,
              SUM(CASE WHEN LOWER(side)='buy' THEN COALESCE(fill_qty,0.0) ELSE 0.0 END) AS buy_qty,
              SUM(CASE WHEN LOWER(side)='buy' THEN COALESCE(fill_qty,0.0) * COALESCE(fill_price,0.0) ELSE 0.0 END) AS buy_notional,
              SUM(CASE WHEN LOWER(side)='sell' THEN COALESCE(fill_qty,0.0) ELSE 0.0 END) AS sell_qty,
              SUM(CASE WHEN LOWER(side)='sell' THEN COALESCE(fill_qty,0.0) * COALESCE(fill_price,0.0) ELSE 0.0 END) AS sell_notional
            FROM fills
            WHERE ts_ms <= ?
            GROUP BY token_id
            ORDER BY token_id ASC
            """,
            (int(as_of_ts_ms),),
            db_path=db_path,
        )

    positions = inventory_rows.copy()
    positions["yes_qty"] = positions["yes_qty"].fillna(0.0).astype(float)
    positions["no_qty"] = positions["no_qty"].fillna(0.0).astype(float)
    positions["net_shares"] = positions["yes_qty"] - positions["no_qty"]
    positions["as_of_ts_ms"] = int(as_of_ts_ms)

    if not fills_agg.empty:
        positions = positions.merge(fills_agg, on="token_id", how="left")
    else:
        positions["buy_qty"] = 0.0
        positions["buy_notional"] = 0.0
        positions["sell_qty"] = 0.0
        positions["sell_notional"] = 0.0

    for col in ["buy_qty", "buy_notional", "sell_qty", "sell_notional"]:
        positions[col] = positions[col].fillna(0.0).astype(float)

    def _avg_entry(row: pd.Series) -> Optional[float]:
        net = float(row.get("net_shares") or 0.0)
        buy_qty = float(row.get("buy_qty") or 0.0)
        buy_notional = float(row.get("buy_notional") or 0.0)
        sell_qty = float(row.get("sell_qty") or 0.0)
        sell_notional = float(row.get("sell_notional") or 0.0)
        # Conservative deterministic rule: only compute when fill history is one-sided.
        if net > 0.0 and sell_qty == 0.0 and buy_qty > 0.0:
            return float(buy_notional / buy_qty)
        if net < 0.0 and buy_qty == 0.0 and sell_qty > 0.0:
            return float(sell_notional / sell_qty)
        return None

    positions["avg_entry"] = positions.apply(_avg_entry, axis=1)

    marks = get_mark_snapshot(as_of_ts_ms=int(as_of_ts_ms), db_path=db_path)
    positions = positions.merge(
        marks[["token_id", "market_slug", "symbol", "mark_source", "mark"]],
        on="token_id",
        how="left",
    )
    positions["gross_notional"] = positions.apply(
        lambda row: (
            float(row["yes_qty"]) * float(row["mark"] if row.get("mark") is not None and not pd.isna(row.get("mark")) else 0.5)
            + float(row["no_qty"]) * float(1.0 - float(row["mark"]) if row.get("mark") is not None and not pd.isna(row.get("mark")) else 0.5)
        ),
        axis=1,
    )
    positions["net_notional"] = positions.apply(
        lambda row: (
            float(row["yes_qty"]) * float(row["mark"] if row.get("mark") is not None and not pd.isna(row.get("mark")) else 0.5)
            - float(row["no_qty"]) * float(1.0 - float(row["mark"]) if row.get("mark") is not None and not pd.isna(row.get("mark")) else 0.5)
        ),
        axis=1,
    )
    positions["unrealized_pnl"] = positions.apply(
        lambda row: (
            float((float(row["mark"]) - float(row["avg_entry"])) * float(row["net_shares"]))
            if row.get("mark") is not None
            and not pd.isna(row.get("mark"))
            and row.get("avg_entry") is not None
            and not pd.isna(row.get("avg_entry"))
            else None
        ),
        axis=1,
    )

    return positions[
        [
            "as_of_ts_ms",
            "token_id",
            "market_slug",
            "symbol",
            "yes_qty",
            "no_qty",
            "net_shares",
            "avg_entry",
            "mark_source",
            "mark",
            "gross_notional",
            "net_notional",
            "unrealized_pnl",
        ]
    ].sort_values("token_id", ascending=True)


def get_open_orders_latest(as_of_ts_ms: int, db_path: Optional[Path] = None) -> pd.DataFrame:
    if not table_exists("open_orders_snapshot", db_path=db_path):
        return pd.DataFrame(
            columns=[
                "as_of_ts_ms",
                "ts_ms",
                "order_id",
                "token_id",
                "market_slug",
                "side",
                "price",
                "size",
                "status",
                "client_order_id",
                "quote_group_id",
            ]
        )
    rows = query_df(
        """
        WITH ranked AS (
          SELECT
            ts_ms, event_id, order_id, token_id, side, price, size, status, client_order_id, quote_group_id,
            ROW_NUMBER() OVER (
              PARTITION BY order_id
              ORDER BY ts_ms DESC, event_id DESC
            ) AS rn
          FROM open_orders_snapshot
          WHERE ts_ms <= ?
        )
        SELECT ts_ms, event_id, order_id, token_id, side, price, size, status, client_order_id, quote_group_id
        FROM ranked
        WHERE rn = 1
          AND LOWER(COALESCE(status, 'open')) NOT IN ('canceled', 'cancelled', 'filled', 'rejected', 'closed')
        ORDER BY token_id ASC, side ASC, order_id ASC
        """,
        (int(as_of_ts_ms),),
        db_path=db_path,
    )
    rows["as_of_ts_ms"] = int(as_of_ts_ms)
    token_market = _latest_token_market(as_of_ts_ms=int(as_of_ts_ms), db_path=db_path)
    if not token_market.empty:
        rows = rows.merge(token_market[["token_id", "market_slug"]], on="token_id", how="left")
    else:
        rows["market_slug"] = None
    ordered_cols = [
        "as_of_ts_ms",
        "ts_ms",
        "order_id",
        "token_id",
        "market_slug",
        "side",
        "price",
        "size",
        "status",
        "client_order_id",
        "quote_group_id",
    ]
    return rows[ordered_cols]


def get_trade_blotter(
    start_ts_ms: int,
    end_ts_ms: int,
    limit: int,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    if not table_exists("fills", db_path=db_path):
        return pd.DataFrame(
            columns=[
                "ts_ms",
                "event_id",
                "order_id",
                "token_id",
                "market_slug",
                "side",
                "fill_price",
                "fill_qty",
                "realized_spread_bps",
                "markout_5s_bps",
                "net_edge_bps",
            ]
        )
    capped_limit = max(1, int(limit))
    has_eq = table_exists("execution_quality", db_path=db_path)
    token_market = _latest_token_market(as_of_ts_ms=int(end_ts_ms), db_path=db_path)
    if has_eq:
        rows = query_df(
            """
            WITH eq_latest AS (
              SELECT
                order_id,
                token_id,
                side,
                realized_spread_bps,
                markout_5s_bps,
                net_edge_bps,
                ROW_NUMBER() OVER (
                  PARTITION BY order_id
                  ORDER BY fill_ts_ms DESC, ts_ms DESC, event_id DESC
                ) AS rn
              FROM execution_quality
              WHERE ts_ms <= ?
            )
            SELECT
              f.ts_ms, f.event_id, f.order_id, f.token_id, f.side, f.fill_price, f.fill_qty,
              e.realized_spread_bps, e.markout_5s_bps, e.net_edge_bps
            FROM fills f
            LEFT JOIN eq_latest e
              ON e.order_id = f.order_id
             AND e.rn = 1
            WHERE f.ts_ms BETWEEN ? AND ?
            ORDER BY f.ts_ms DESC, f.event_id DESC
            LIMIT ?
            """,
            (int(end_ts_ms), int(start_ts_ms), int(end_ts_ms), int(capped_limit)),
            db_path=db_path,
        )
    else:
        rows = query_df(
            """
            SELECT
              f.ts_ms, f.event_id, f.order_id, f.token_id, f.side, f.fill_price, f.fill_qty,
              NULL AS realized_spread_bps, NULL AS markout_5s_bps, NULL AS net_edge_bps
            FROM fills f
            WHERE f.ts_ms BETWEEN ? AND ?
            ORDER BY f.ts_ms DESC, f.event_id DESC
            LIMIT ?
            """,
            (int(start_ts_ms), int(end_ts_ms), int(capped_limit)),
            db_path=db_path,
        )
    if not token_market.empty:
        rows = rows.merge(token_market[["token_id", "market_slug"]], on="token_id", how="left")
    else:
        rows["market_slug"] = None
    ordered_cols = [
        "ts_ms",
        "event_id",
        "order_id",
        "token_id",
        "market_slug",
        "side",
        "fill_price",
        "fill_qty",
        "realized_spread_bps",
        "markout_5s_bps",
        "net_edge_bps",
    ]
    return rows[ordered_cols]


def get_paper_pnl_curve(
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    if not table_exists("paper_pnl", db_path=db_path):
        return pd.DataFrame(
            columns=[
                "ts_ms",
                "realized_gross_pnl",
                "realized_net_pnl",
                "unrealized_pnl",
                "total_pnl",
                "cumulative_fees",
                "turnover",
                "win_count",
                "loss_count",
                "equity_peak",
                "drawdown_abs",
                "drawdown_pct",
            ]
        )

    predicates: List[str] = []
    params: List[Any] = []
    if start_ts_ms is not None:
        predicates.append("ts_ms >= ?")
        params.append(int(start_ts_ms))
    if end_ts_ms is not None:
        predicates.append("ts_ms <= ?")
        params.append(int(end_ts_ms))
    where_sql = f"WHERE {' AND '.join(predicates)}" if predicates else ""
    rows = query_df(
        f"""
        SELECT
          ts_ms,
          MAX(realized_gross_pnl) AS realized_gross_pnl,
          MAX(realized_net_pnl) AS realized_net_pnl,
          SUM(COALESCE(unrealized_pnl, 0.0)) AS unrealized_pnl,
          MAX(cumulative_fees) AS cumulative_fees,
          MAX(turnover) AS turnover,
          MAX(win_count) AS win_count,
          MAX(loss_count) AS loss_count
        FROM paper_pnl
        {where_sql}
        GROUP BY ts_ms
        ORDER BY ts_ms ASC
        """,
        tuple(params),
        db_path=db_path,
    )
    if rows.empty:
        return rows

    out = rows.copy()
    for col in ["realized_gross_pnl", "realized_net_pnl", "unrealized_pnl", "cumulative_fees", "turnover"]:
        out[col] = out[col].fillna(0.0).astype(float)
    for col in ["win_count", "loss_count"]:
        out[col] = out[col].fillna(0).astype(int)
    out["total_pnl"] = out["realized_net_pnl"] + out["unrealized_pnl"]
    peaks: List[float] = []
    running_peak: Optional[float] = None
    for value in out["total_pnl"].tolist():
        current = float(value)
        running_peak = current if running_peak is None else max(running_peak, current)
        peaks.append(float(running_peak))
    out["equity_peak"] = peaks
    out["drawdown_abs"] = (out["equity_peak"] - out["total_pnl"]).clip(lower=0.0)
    out["drawdown_pct"] = out.apply(
        lambda row: (float(row["drawdown_abs"]) / float(row["equity_peak"])) if float(row["equity_peak"]) > 0 else None,
        axis=1,
    )
    return out


def get_paper_pnl_summary(as_of_ts_ms: Optional[int] = None, db_path: Optional[Path] = None) -> Dict[str, Any]:
    curve = get_paper_pnl_curve(end_ts_ms=as_of_ts_ms, db_path=db_path)
    if curve.empty:
        summary = get_run_summary(db_path=db_path)
        if summary:
            realized_net = _float_or_none(summary.get("realized_net_pnl")) or 0.0
            unrealized = _float_or_none(summary.get("unrealized_pnl")) or 0.0
            return {
                "latest_ts_ms": _int_or_none(summary.get("updated_at_ms")),
                "realized_gross_pnl": _float_or_none(summary.get("realized_gross_pnl")) or 0.0,
                "realized_net_pnl": realized_net,
                "unrealized_pnl": unrealized,
                "total_pnl": realized_net + unrealized,
                "cumulative_fees": _float_or_none(summary.get("total_fees")) or 0.0,
                "turnover": _float_or_none(summary.get("turnover")) or 0.0,
                "fills": _int_or_none(summary.get("fills")) or 0,
                "decisions": _int_or_none(summary.get("decisions")) or 0,
                "win_count": None,
                "loss_count": None,
                "max_drawdown_abs": None,
                "max_drawdown_pct": None,
            }
        return {
            "latest_ts_ms": None,
            "realized_gross_pnl": 0.0,
            "realized_net_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "cumulative_fees": 0.0,
            "turnover": 0.0,
            "fills": 0,
            "decisions": 0,
            "win_count": None,
            "loss_count": None,
            "max_drawdown_abs": None,
            "max_drawdown_pct": None,
        }

    latest = curve.iloc[-1]
    return {
        "latest_ts_ms": _int_or_none(latest.get("ts_ms")),
        "realized_gross_pnl": float(latest.get("realized_gross_pnl") or 0.0),
        "realized_net_pnl": float(latest.get("realized_net_pnl") or 0.0),
        "unrealized_pnl": float(latest.get("unrealized_pnl") or 0.0),
        "total_pnl": float(latest.get("total_pnl") or 0.0),
        "cumulative_fees": float(latest.get("cumulative_fees") or 0.0),
        "turnover": float(latest.get("turnover") or 0.0),
        "fills": int(query_df("SELECT COUNT(*) AS n FROM fills", db_path=db_path).get("n", pd.Series([0])).iloc[0] if table_exists("fills", db_path=db_path) else 0),
        "decisions": int(query_df("SELECT COUNT(*) AS n FROM decisions", db_path=db_path).get("n", pd.Series([0])).iloc[0] if table_exists("decisions", db_path=db_path) else 0),
        "win_count": _int_or_none(latest.get("win_count")),
        "loss_count": _int_or_none(latest.get("loss_count")),
        "max_drawdown_abs": float(curve["drawdown_abs"].max()) if "drawdown_abs" in curve.columns else None,
        "max_drawdown_pct": float(curve["drawdown_pct"].dropna().max()) if "drawdown_pct" in curve.columns and not curve["drawdown_pct"].dropna().empty else None,
    }


def get_current_edge_snapshot(as_of_ts_ms: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    if not table_exists("decisions", db_path=db_path):
        return {}
    latest = query_df(
        """
        WITH ranked AS (
          SELECT
            ts_ms,
            decision_id,
            market,
            token_id,
            action,
            p_hat,
            expected_edge,
            expected_cost,
            policy_json,
            ROW_NUMBER() OVER (
              PARTITION BY token_id
              ORDER BY ts_ms DESC, COALESCE(decision_id, '') DESC
            ) AS rn
          FROM decisions
          WHERE ts_ms <= ?
        )
        SELECT ts_ms, decision_id, market, token_id, action, p_hat, expected_edge, expected_cost, policy_json
        FROM ranked
        WHERE rn = 1
        ORDER BY ts_ms DESC, token_id ASC
        """,
        (int(as_of_ts_ms),),
        db_path=db_path,
    )
    if latest.empty:
        return {}

    latest = latest.copy()
    latest["ev"] = latest["expected_edge"].fillna(0.0) - latest["expected_cost"].fillna(0.0)
    recent_eq = pd.DataFrame()
    if table_exists("execution_quality", db_path=db_path):
        recent_eq = query_df(
            """
            SELECT realized_spread_bps, net_edge_bps, markout_5s_bps
            FROM execution_quality
            WHERE ts_ms <= ?
            ORDER BY ts_ms DESC
            LIMIT 50
            """,
            (int(as_of_ts_ms),),
            db_path=db_path,
        )
    newest = latest.iloc[0]
    return {
        "latest_ts_ms": _int_or_none(newest.get("ts_ms")),
        "latest_market": newest.get("market"),
        "latest_token_id": newest.get("token_id"),
        "latest_action": newest.get("action"),
        "latest_p_hat": _float_or_none(newest.get("p_hat")),
        "latest_ev": _float_or_none(newest.get("ev")),
        "best_ev": float(latest["ev"].max()) if "ev" in latest.columns else None,
        "avg_ev": float(latest["ev"].mean()) if "ev" in latest.columns else None,
        "recent_realized_spread_bps": percentile(recent_eq.get("realized_spread_bps", pd.Series(dtype=float)), 0.5),
        "recent_net_edge_bps": percentile(recent_eq.get("net_edge_bps", pd.Series(dtype=float)), 0.5),
        "recent_markout_5s_bps": percentile(recent_eq.get("markout_5s_bps", pd.Series(dtype=float)), 0.5),
    }


def get_active_quote_summary(as_of_ts_ms: int, db_path: Optional[Path] = None) -> pd.DataFrame:
    open_orders = get_open_orders_latest(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    if open_orders.empty:
        return pd.DataFrame(
            columns=[
                "token_id",
                "market_slug",
                "bid_count",
                "ask_count",
                "bid_size",
                "ask_size",
                "avg_bid",
                "avg_ask",
                "mid",
                "offered_spread_bps",
                "quote_state",
                "oldest_age_s",
                "newest_age_s",
            ]
        )

    marks = get_mark_snapshot(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    mark_map = {
        str(row["token_id"]): _float_or_none(row.get("mark"))
        for _, row in marks.iterrows()
        if row.get("token_id") is not None
    }
    rows: List[Dict[str, Any]] = []
    for (token_id, market_slug), group in open_orders.groupby(["token_id", "market_slug"], dropna=False):
        token_rows = group.copy()
        bid_rows = token_rows[token_rows["side"].astype(str).str.lower() == "buy"]
        ask_rows = token_rows[token_rows["side"].astype(str).str.lower() == "sell"]
        bid_size = float(bid_rows["size"].fillna(0.0).sum()) if not bid_rows.empty else 0.0
        ask_size = float(ask_rows["size"].fillna(0.0).sum()) if not ask_rows.empty else 0.0
        avg_bid = None
        avg_ask = None
        if not bid_rows.empty:
            weights = bid_rows["size"].fillna(0.0).astype(float)
            prices = bid_rows["price"].fillna(0.0).astype(float)
            avg_bid = float((prices * weights).sum() / weights.sum()) if float(weights.sum()) > 0 else float(prices.mean())
        if not ask_rows.empty:
            weights = ask_rows["size"].fillna(0.0).astype(float)
            prices = ask_rows["price"].fillna(0.0).astype(float)
            avg_ask = float((prices * weights).sum() / weights.sum()) if float(weights.sum()) > 0 else float(prices.mean())
        mid = mark_map.get(str(token_id))
        if mid is None and avg_bid is not None and avg_ask is not None:
            mid = (float(avg_bid) + float(avg_ask)) / 2.0
        spread_bps = None
        if avg_bid is not None and avg_ask is not None and mid is not None and float(mid) > 0:
            spread_bps = ((float(avg_ask) - float(avg_bid)) / float(mid)) * 10000.0
        quote_state = "live" if avg_bid is not None and avg_ask is not None else ("partial" if avg_bid is not None or avg_ask is not None else "absent")
        ages = ((int(as_of_ts_ms) - token_rows["ts_ms"].astype(float)) / 1000.0).clip(lower=0.0) if "ts_ms" in token_rows.columns else pd.Series(dtype=float)
        rows.append(
            {
                "token_id": token_id,
                "market_slug": market_slug,
                "bid_count": int(len(bid_rows)),
                "ask_count": int(len(ask_rows)),
                "bid_size": bid_size,
                "ask_size": ask_size,
                "avg_bid": avg_bid,
                "avg_ask": avg_ask,
                "mid": mid,
                "offered_spread_bps": spread_bps,
                "quote_state": quote_state,
                "oldest_age_s": float(ages.max()) if not ages.empty else None,
                "newest_age_s": float(ages.min()) if not ages.empty else None,
            }
        )
    return pd.DataFrame(rows).sort_values(["market_slug", "token_id"]).reset_index(drop=True)


def get_portfolio_risk_summary(as_of_ts_ms: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    positions = get_positions_as_of(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    open_orders = get_open_orders_latest(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    quotes = get_active_quote_summary(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    pnl = get_paper_pnl_summary(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    edge = get_current_edge_snapshot(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    cfg = get_runtime_config_snapshot(db_path=db_path)

    gross_exposure = float(positions["gross_notional"].fillna(0.0).sum()) if "gross_notional" in positions.columns else 0.0
    net_exposure = float(positions["net_notional"].fillna(0.0).sum()) if "net_notional" in positions.columns else 0.0
    max_position_notional = float(positions["gross_notional"].fillna(0.0).max()) if not positions.empty and "gross_notional" in positions.columns else 0.0
    active_positions = int((positions["net_shares"].fillna(0.0).abs() > 0).sum()) if "net_shares" in positions.columns else 0
    active_orders = int(len(open_orders))
    live_quote_rows = int((quotes["quote_state"] == "live").sum()) if not quotes.empty and "quote_state" in quotes.columns else 0
    partial_quote_rows = int((quotes["quote_state"] == "partial").sum()) if not quotes.empty and "quote_state" in quotes.columns else 0
    offered_spread_bps = percentile(quotes.get("offered_spread_bps", pd.Series(dtype=float)), 0.5)

    fills = adapt_fills(query_df("SELECT ts_ms, payload_json FROM fills WHERE ts_ms <= ?", (int(as_of_ts_ms),), db_path=db_path))
    if fills.empty:
        hedge_completeness = 1.0
        hedge_state = "no fills yet"
    else:
        hedge_count = int(fills["is_hedge"].sum())
        primary_count = max(0, len(fills) - hedge_count)
        hedge_completeness = 1.0 if primary_count == 0 else min(1.0, hedge_count / float(primary_count))
        hedge_state = "hedged" if hedge_completeness >= 1.0 else ("partial" if hedge_count > 0 else "unhedged")

    system_state = query_df("SELECT payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1", db_path=db_path)
    system_payload = safe_json(safe_first(system_state, "payload_json", "{}"))
    broker_stats = system_payload.get("broker_stats") if isinstance(system_payload.get("broker_stats"), dict) else {}
    realized_net = _float_or_none(broker_stats.get("realized_net_pnl"))
    if realized_net is not None:
        pnl["realized_net_pnl"] = realized_net
        pnl["total_pnl"] = float(realized_net) + float(pnl.get("unrealized_pnl") or 0.0)

    max_risk_per_trade_shares = cfg.get("trade_size") or cfg.get("maker_quote_size")
    price_ref = edge.get("latest_p_hat")
    max_risk_per_trade_usd = float(max_risk_per_trade_shares) * float(price_ref) if max_risk_per_trade_shares is not None and price_ref is not None else None
    per_market_cap = cfg.get("per_market_gross_cap_usd")
    portfolio_cap = cfg.get("portfolio_gross_cap_usd")

    return {
        "active_positions": active_positions,
        "active_orders": active_orders,
        "live_quote_rows": live_quote_rows,
        "partial_quote_rows": partial_quote_rows,
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "max_position_notional": max_position_notional,
        "hedge_completeness": hedge_completeness,
        "hedge_state": hedge_state,
        "offered_spread_bps": offered_spread_bps,
        "current_edge": edge.get("latest_ev"),
        "recent_realized_spread_bps": edge.get("recent_realized_spread_bps"),
        "recent_net_edge_bps": edge.get("recent_net_edge_bps"),
        "realized_net_pnl": pnl.get("realized_net_pnl"),
        "unrealized_pnl": pnl.get("unrealized_pnl"),
        "total_pnl": pnl.get("total_pnl"),
        "turnover": pnl.get("turnover"),
        "cumulative_fees": pnl.get("cumulative_fees"),
        "max_drawdown_abs": pnl.get("max_drawdown_abs"),
        "max_drawdown_pct": pnl.get("max_drawdown_pct"),
        "win_count": pnl.get("win_count"),
        "loss_count": pnl.get("loss_count"),
        "per_market_cap_usd": per_market_cap,
        "portfolio_cap_usd": portfolio_cap,
        "per_market_cap_utilization": (max_position_notional / float(per_market_cap)) if per_market_cap not in (None, 0) else None,
        "portfolio_cap_utilization": (gross_exposure / float(portfolio_cap)) if portfolio_cap not in (None, 0) else None,
        "daily_loss_limit_usdc": cfg.get("daily_loss_limit_usdc"),
        "daily_notional_limit_usdc": cfg.get("daily_notional_limit_usdc"),
        "maker_quote_size": cfg.get("maker_quote_size"),
        "maker_half_spread_bps": cfg.get("maker_half_spread_bps"),
        "trade_size": cfg.get("trade_size"),
        "max_size": cfg.get("max_size"),
        "max_risk_per_trade_shares": max_risk_per_trade_shares,
        "max_risk_per_trade_usd": max_risk_per_trade_usd,
        "latest_p_hat": edge.get("latest_p_hat"),
    }


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


def get_execution_quality_df(db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return all execution_quality rows with ts, side, prices, and markout columns."""
    if not table_exists("execution_quality", db_path=db_path):
        return pd.DataFrame()
    df = query_df(
        """
        SELECT
          ts_ms,
          order_id,
          token_id,
          side,
          fill_price,
          mid_at_placement,
          mid_at_fill,
          realized_spread_bps,
          markout_1s_bps,
          markout_5s_bps,
          net_edge_bps,
          slippage_bps,
          fill_trigger,
          quote_mode
        FROM execution_quality
        ORDER BY fill_ts_ms ASC
        """,
        db_path=db_path,
    )
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df


def get_fills_recent(limit: int = 20, db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return the most recent fills with payload fields unpacked."""
    if not table_exists("fills", db_path=db_path):
        return pd.DataFrame()
    df = query_df(
        f"""
        SELECT
          ts_ms,
          order_id,
          token_id,
          side,
          fill_price,
          fill_qty,
          payload_json
        FROM fills
        ORDER BY ts_ms DESC
        LIMIT {int(limit)}
        """,
        db_path=db_path,
    )
    if df.empty:
        return df
    realized_deltas: List[Optional[float]] = []
    fee_usdc: List[Optional[float]] = []
    for _, row in df.iterrows():
        payload = safe_json(row.get("payload_json"))
        realized_deltas.append(_float_or_none(payload.get("realized_net_pnl_delta")))
        fee_usdc.append(_float_or_none(payload.get("fee_usdc")))
    df["realized_net_pnl_delta"] = realized_deltas
    df["fee_usdc"] = fee_usdc
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df


def get_latest_system_payload(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the latest system_state payload_json as a dict."""
    if not table_exists("system_state", db_path=db_path):
        return {}
    df = query_df("SELECT payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1", db_path=db_path)
    return safe_json(safe_first(df, "payload_json", "{}"))


def get_per_token_inventory(db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return latest inventory per token_id."""
    if not table_exists("inventory", db_path=db_path):
        return pd.DataFrame()
    return query_df(
        """
        SELECT i.token_id, i.yes_qty, i.ts_ms
        FROM inventory i
        INNER JOIN (SELECT token_id, MAX(ts_ms) AS max_ts FROM inventory GROUP BY token_id) latest
          ON i.token_id = latest.token_id AND i.ts_ms = latest.max_ts
        """,
        db_path=db_path,
    )


def get_per_token_fill_counts(db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return fill count and volume per token_id and side."""
    if not table_exists("fills", db_path=db_path):
        return pd.DataFrame()
    return query_df(
        """
        SELECT token_id, side,
               COUNT(*) AS fill_count,
               COALESCE(SUM(fill_qty), 0.0) AS fill_volume,
               COALESCE(SUM(fill_price * fill_qty), 0.0) AS fill_notional
        FROM fills
        GROUP BY token_id, side
        """,
        db_path=db_path,
    )


def get_drawdown_series(db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return drawdown time series from paper_pnl curve."""
    curve = get_paper_pnl_curve(db_path=db_path)
    if curve.empty or "total_pnl" not in curve.columns:
        return pd.DataFrame()
    plot = curve[["ts_ms", "total_pnl"]].copy()
    plot["ts"] = pd.to_datetime(plot["ts_ms"], unit="ms", utc=True)
    plot["equity_peak"] = plot["total_pnl"].cummax()
    plot["drawdown"] = plot["total_pnl"] - plot["equity_peak"]
    return plot


def get_decision_action_counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Return counts of each decision action type."""
    if not table_exists("decisions", db_path=db_path):
        return {}
    df = query_df("SELECT action, COUNT(*) AS n FROM decisions GROUP BY action", db_path=db_path)
    return {str(row["action"]): int(row["n"]) for _, row in df.iterrows()}


def get_market_history_summary(db_path: Optional[Path] = None) -> pd.DataFrame:
    """Per-market aggregated stats: fills, PnL, avg markout, avg spread, quote counts."""
    if not table_exists("fills", db_path=db_path):
        return pd.DataFrame()
    fills_df = query_df(
        """
        SELECT market_slug, COUNT(*) AS fills,
               COALESCE(SUM(fill_qty), 0.0) AS fill_volume
        FROM (
            SELECT json_extract(payload_json, '$.market_slug') AS market_slug,
                   fill_qty
            FROM fills
            WHERE json_extract(payload_json, '$.market_slug') IS NOT NULL
        )
        GROUP BY market_slug
        """,
        db_path=db_path,
    )
    # Per-market PnL (latest snapshot per market)
    pnl_df = pd.DataFrame()
    if table_exists("paper_pnl", db_path=db_path):
        pnl_df = query_df(
            """
            SELECT p.market_slug,
                   SUM(p.realized_net_pnl) AS realized_net_pnl,
                   SUM(p.unrealized_pnl)   AS unrealized_pnl,
                   SUM(p.win_count)        AS win_count,
                   SUM(p.loss_count)       AS loss_count,
                   MIN(p.ts_ms)            AS first_ts_ms,
                   MAX(p.ts_ms)            AS last_ts_ms
            FROM paper_pnl p
            INNER JOIN (
                SELECT market_slug, token_id, MAX(ts_ms) AS max_ts
                FROM paper_pnl GROUP BY market_slug, token_id
            ) latest ON p.market_slug = latest.market_slug
                     AND p.token_id = latest.token_id
                     AND p.ts_ms = latest.max_ts
            WHERE p.market_slug IS NOT NULL
            GROUP BY p.market_slug
            """,
            db_path=db_path,
        )
    # Per-market execution quality
    eq_df = pd.DataFrame()
    if table_exists("execution_quality", db_path=db_path):
        eq_df = query_df(
            """
            SELECT json_extract(payload_json, '$.market_slug') AS market_slug,
                   AVG(realized_spread_bps) AS avg_spread_bps,
                   AVG(markout_1s_bps)      AS avg_markout_1s,
                   AVG(net_edge_bps)        AS avg_net_edge_bps,
                   COUNT(*)                 AS eq_samples
            FROM execution_quality
            WHERE json_extract(payload_json, '$.market_slug') IS NOT NULL
            GROUP BY json_extract(payload_json, '$.market_slug')
            """,
            db_path=db_path,
        )
    # Per-market decision counts
    dec_df = pd.DataFrame()
    if table_exists("decisions", db_path=db_path):
        dec_df = query_df(
            """
            SELECT market,
                   COUNT(*) AS total_decisions,
                   SUM(CASE WHEN action='QUOTE' THEN 1 ELSE 0 END) AS quote_decisions
            FROM decisions WHERE market IS NOT NULL
            GROUP BY market
            """,
            db_path=db_path,
        )
        if not dec_df.empty:
            dec_df = dec_df.rename(columns={"market": "market_slug"})

    result = fills_df if not fills_df.empty else pd.DataFrame(columns=["market_slug"])
    for other, on in [(pnl_df, "market_slug"), (eq_df, "market_slug"), (dec_df, "market_slug")]:
        if not other.empty:
            result = result.merge(other, on=on, how="outer") if not result.empty else other
    return result


def get_per_token_stats_for_market(market_slug: str, db_path: Optional[Path] = None) -> pd.DataFrame:
    """Per-token fill counts and inventory for a specific market."""
    if not table_exists("fills", db_path=db_path):
        return pd.DataFrame()
    return query_df(
        """
        SELECT f.token_id, f.side,
               COUNT(*) AS fill_count,
               COALESCE(SUM(f.fill_qty), 0.0) AS fill_volume,
               AVG(f.fill_price) AS avg_price
        FROM fills f
        WHERE json_extract(f.payload_json, '$.market_slug') = ?
        GROUP BY f.token_id, f.side
        """,
        params=(market_slug,),
        db_path=db_path,
    )


def get_per_symbol_summary(db_path: Optional[Path] = None) -> pd.DataFrame:
    """Per-symbol (BTC/ETH/SOL/XRP) aggregated stats from market history."""
    hist = get_market_history_summary(db_path=db_path)
    if hist.empty:
        return pd.DataFrame()

    _SYM_ORDER = ["BTC", "ETH", "SOL", "XRP"]

    def _sym(slug: str) -> str:
        s = str(slug).lower()
        for sym in ["btc", "eth", "sol", "xrp"]:
            if s.startswith(sym + "-"):
                return sym.upper()
        return "?"

    hist["symbol"] = hist["market_slug"].map(_sym)
    agg = hist.groupby("symbol", as_index=False).agg(
        markets=("market_slug", "count"),
        fills=("fills", "sum"),
        fill_volume=("fill_volume", "sum"),
        realized_net_pnl=("realized_net_pnl", "sum"),
        total_decisions=("total_decisions", "sum"),
        avg_spread_bps=("avg_spread_bps", "mean"),
    )
    # Ensure all 4 symbols always appear
    for sym in _SYM_ORDER:
        if sym not in agg["symbol"].values:
            agg = pd.concat(
                [agg, pd.DataFrame([{"symbol": sym, "markets": 0, "fills": 0,
                    "fill_volume": 0.0, "realized_net_pnl": 0.0, "total_decisions": 0,
                    "avg_spread_bps": float("nan")}])],
                ignore_index=True,
            )
    agg["symbol"] = pd.Categorical(agg["symbol"], categories=_SYM_ORDER, ordered=True)
    return agg.sort_values("symbol").reset_index(drop=True)


def get_alpha_overlay_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Extract alpha overlay diagnostics from recent decision metadata."""
    if not table_exists("decisions", db_path=db_path):
        return {}
    df = query_df(
        """
        SELECT policy_json FROM decisions
        WHERE action = 'QUOTE'
        ORDER BY ts_ms DESC
        LIMIT 100
        """,
        db_path=db_path,
    )
    if df.empty:
        return {}

    vol_regimes: List[str] = []
    adversity_ratios: List[float] = []
    extra_skews: List[int] = []
    spread_mults: List[float] = []
    complement_bps: List[float] = []
    depth_changes: List[float] = []

    for _, row in df.iterrows():
        payload = safe_json(row.get("policy_json"))
        quotes = payload.get("desired_quotes") or []
        for q_item in quotes:
            meta = q_item.get("metadata") or {}
            vol_regime = meta.get("alpha_vol_regime")
            if vol_regime is not None:
                vol_regimes.append(str(vol_regime))
            adv = _float_or_none(meta.get("alpha_adversity"))
            if adv is not None:
                adversity_ratios.append(adv)
            skew = meta.get("alpha_extra_skew")
            if skew is not None:
                extra_skews.append(int(skew))
            sm = _float_or_none(meta.get("alpha_spread_mult"))
            if sm is not None:
                spread_mults.append(sm)
            comp = _float_or_none(meta.get("alpha_complement_bps"))
            if comp is not None:
                complement_bps.append(comp)
            dc = _float_or_none(meta.get("alpha_depth_change"))
            if dc is not None:
                depth_changes.append(dc)

    regime_counts: Dict[str, int] = {}
    for r in vol_regimes:
        regime_counts[r] = regime_counts.get(r, 0) + 1

    return {
        "samples": len(vol_regimes),
        "vol_regime_counts": regime_counts,
        "avg_adversity_ratio": float(sum(adversity_ratios) / len(adversity_ratios)) if adversity_ratios else None,
        "avg_extra_skew": float(sum(extra_skews) / len(extra_skews)) if extra_skews else None,
        "avg_spread_mult": float(sum(spread_mults) / len(spread_mults)) if spread_mults else None,
        "max_spread_mult": max(spread_mults) if spread_mults else None,
        "skew_nonzero_pct": (sum(1 for s in extra_skews if s != 0) / len(extra_skews) * 100.0) if extra_skews else None,
        "avg_complement_bps": float(sum(complement_bps) / len(complement_bps)) if complement_bps else None,
        "max_complement_bps": max(abs(c) for c in complement_bps) if complement_bps else None,
        "complement_active_pct": (sum(1 for c in complement_bps if abs(c) > 50) / len(complement_bps) * 100.0) if complement_bps else None,
        "avg_depth_change": float(sum(depth_changes) / len(depth_changes)) if depth_changes else None,
        "depth_change_active_pct": (sum(1 for d in depth_changes if abs(d) > 0.01) / len(depth_changes) * 100.0) if depth_changes else None,
    }


def get_memory_layer_stats(memory_db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read memory layer data from the shared memory.db."""
    if memory_db_path is None or not memory_db_path.exists():
        return {}
    try:
        import sqlite3
        conn = sqlite3.connect(str(memory_db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Symbol-level memories
        memories = []
        try:
            rows = cur.execute("SELECT data_json FROM market_memory ORDER BY symbol").fetchall()
            import json
            for row in rows:
                data = json.loads(row[0])
                memories.append(data)
        except Exception:
            pass

        # Recent session history
        sessions = []
        try:
            rows = cur.execute(
                "SELECT summary_json FROM session_history ORDER BY ts_ms DESC LIMIT 20"
            ).fetchall()
            import json
            for row in rows:
                data = json.loads(row[0])
                sessions.append(data)
        except Exception:
            pass

        conn.close()

        return {
            "memories": memories,
            "sessions": sessions,
            "total_symbols": len(set(m.get("symbol", "") for m in memories)),
            "total_sessions": len(sessions),
        }
    except Exception:
        return {}


def get_latest_book_snapshot(token_id: str, db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return latest L2 book levels for *token_id* with cumulative size."""
    if not table_exists("book_snapshots", db_path=db_path):
        return pd.DataFrame(columns=["side", "price", "size", "cumulative_size"])
    df = query_df(
        """
        SELECT side, price, size
        FROM book_snapshots
        WHERE token_id = ?
          AND ts_ms = (SELECT MAX(ts_ms) FROM book_snapshots WHERE token_id = ?)
        ORDER BY side, price
        """,
        params=(token_id, token_id),
        db_path=db_path,
    )
    if df.empty:
        df["cumulative_size"] = pd.Series(dtype="float64")
        return df
    rows: list[dict] = []
    for side in ("bid", "ask"):
        sub = df[df["side"] == side].copy()
        if side == "bid":
            sub = sub.sort_values("price", ascending=False)
        else:
            sub = sub.sort_values("price", ascending=True)
        sub["cumulative_size"] = sub["size"].cumsum()
        rows.extend(sub.to_dict("records"))
    return pd.DataFrame(rows)


def get_book_snapshot_at(ts_ms: int, token_id: str, db_path: Optional[Path] = None) -> pd.DataFrame:
    """Return L2 book at closest snapshot <= *ts_ms*."""
    if not table_exists("book_snapshots", db_path=db_path):
        return pd.DataFrame(columns=["side", "price", "size", "cumulative_size"])
    df = query_df(
        """
        SELECT side, price, size
        FROM book_snapshots
        WHERE token_id = ?
          AND ts_ms = (SELECT MAX(ts_ms) FROM book_snapshots WHERE token_id = ? AND ts_ms <= ?)
        ORDER BY side, price
        """,
        params=(token_id, token_id, ts_ms),
        db_path=db_path,
    )
    if df.empty:
        df["cumulative_size"] = pd.Series(dtype="float64")
        return df
    rows: list[dict] = []
    for side in ("bid", "ask"):
        sub = df[df["side"] == side].copy()
        if side == "bid":
            sub = sub.sort_values("price", ascending=False)
        else:
            sub = sub.sort_values("price", ascending=True)
        sub["cumulative_size"] = sub["size"].cumsum()
        rows.extend(sub.to_dict("records"))
    return pd.DataFrame(rows)


def get_all_markets_summary(
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return summary stats for every market the bot has traded, ordered by most recent first."""
    if not table_exists("decisions", db_path=db_path):
        return []
    df = query_df(
        """
        SELECT
            market,
            COUNT(*) AS decisions,
            COUNT(DISTINCT token_id) AS tokens,
            MIN(ts_ms) AS first_ts,
            MAX(ts_ms) AS last_ts,
            SUM(CASE WHEN action = 'QUOTE' THEN 1 ELSE 0 END) AS quotes,
            SUM(CASE WHEN action = 'SKIP' THEN 1 ELSE 0 END) AS skips,
            SUM(CASE WHEN action = 'FREEZE' THEN 1 ELSE 0 END) AS freezes
        FROM decisions
        GROUP BY market
        ORDER BY MAX(ts_ms) DESC
        """,
        db_path=db_path,
    )
    if df.empty:
        return []

    # Enrich with fill counts and PnL per market
    fills_df = query_df(
        """
        SELECT d.market, COUNT(f.rowid) AS fills
        FROM fills f
        JOIN decisions d ON f.token_id = d.token_id
        GROUP BY d.market
        """,
        db_path=db_path,
    ) if table_exists("fills", db_path=db_path) else pd.DataFrame()

    pnl_df = query_df(
        """
        SELECT market_slug AS market,
               SUM(realized_net_pnl) AS realized_net_pnl,
               SUM(turnover) AS turnover
        FROM (
            SELECT market_slug, token_id, realized_net_pnl, turnover,
                   ROW_NUMBER() OVER (PARTITION BY market_slug, token_id ORDER BY ts_ms DESC) AS rn
            FROM paper_pnl
        )
        WHERE rn = 1
        GROUP BY market_slug
        """,
        db_path=db_path,
    ) if table_exists("paper_pnl", db_path=db_path) else pd.DataFrame()

    fills_map: Dict[str, int] = {}
    if not fills_df.empty:
        for _, r in fills_df.iterrows():
            fills_map[str(r["market"])] = int(r["fills"])

    pnl_map: Dict[str, Dict] = {}
    if not pnl_df.empty:
        for _, r in pnl_df.iterrows():
            pnl_map[str(r["market"])] = {
                "realized_net_pnl": float(r.get("realized_net_pnl") or 0),
                "turnover": float(r.get("turnover") or 0),
            }

    result = []
    for _, row in df.iterrows():
        mkt = str(row["market"])
        pnl_data = pnl_map.get(mkt, {})
        result.append({
            "market": mkt,
            "decisions": int(row["decisions"]),
            "tokens": int(row["tokens"]),
            "first_ts": int(row["first_ts"]),
            "last_ts": int(row["last_ts"]),
            "quotes": int(row["quotes"]),
            "skips": int(row["skips"]),
            "freezes": int(row["freezes"]),
            "fills": fills_map.get(mkt, 0),
            "realized_net_pnl": pnl_data.get("realized_net_pnl", 0.0),
            "turnover": pnl_data.get("turnover", 0.0),
        })
    return result


def get_latest_decisions_per_token(
    db_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return the latest decision (with parsed policy_json) for each active token_id.

    Returns {token_id: {action, reason_codes, ts_ms, book_diag, metrics,
             flow_filter, quote_plan, size_plan, risk_decision, ...}}
    """
    if not table_exists("decisions", db_path=db_path):
        return {}
    df = query_df(
        """
        SELECT d.*
        FROM decisions d
        INNER JOIN (
            SELECT token_id, MAX(ts_ms) AS max_ts
            FROM decisions
            GROUP BY token_id
        ) latest ON d.token_id = latest.token_id AND d.ts_ms = latest.max_ts
        """,
        db_path=db_path,
    )
    if df.empty:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        tid = str(row.get("token_id", ""))
        payload = safe_json(row.get("policy_json"))
        result[tid] = {
            "action": str(row.get("action") or "?"),
            "reason_codes": str(row.get("reason_codes") or ""),
            "ts_ms": row.get("ts_ms"),
            "expected_edge": _float_or_none(row.get("expected_edge")),
            "book_diag": payload.get("book_diag") or {},
            "metrics": payload.get("metrics") or {},
            "flow_filter": payload.get("flow_filter") or {},
            "quote_plan": payload.get("quote_plan") or {},
            "size_plan": payload.get("size_plan") or {},
            "risk_decision": payload.get("risk_decision") or {},
        }
    return result


def get_quote_time_series(
    token_id: str,
    limit: int = 300,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Return time-series of mid price, our bid/ask, and trade size from decisions.

    Columns: ts_ms, mid, best_bid, best_ask, our_bid, our_ask, trade_size
    """
    if not table_exists("decisions", db_path=db_path):
        return pd.DataFrame()
    df = query_df(
        """
        SELECT ts_ms, policy_json
        FROM decisions
        WHERE token_id = ? AND action = 'QUOTE'
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (str(token_id), int(limit)),
        db_path=db_path,
    )
    if df.empty:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        ts_ms = row.get("ts_ms")
        payload = safe_json(row.get("policy_json"))
        metrics = payload.get("metrics") or {}
        quote_plan = payload.get("quote_plan") or {}
        size_plan = payload.get("size_plan") or {}

        best_bid = _float_or_none(metrics.get("best_bid"))
        best_ask = _float_or_none(metrics.get("best_ask"))
        mid = None
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0

        buy_amt = _float_or_none(size_plan.get("buy_amount"))
        sell_amt = _float_or_none(size_plan.get("sell_amount"))
        trade_size = max(buy_amt or 0.0, sell_amt or 0.0) or None

        rows.append({
            "ts_ms": ts_ms,
            "mid": mid,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "our_bid": _float_or_none(quote_plan.get("bid_price")),
            "our_ask": _float_or_none(quote_plan.get("ask_price")),
            "trade_size": trade_size,
        })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("ts_ms", ascending=True).reset_index(drop=True)
    return result


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
