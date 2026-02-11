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
