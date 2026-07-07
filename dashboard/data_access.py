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
from core_mm.control_plane import ControlCommandStore


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


def _connect_write(db_path: Path) -> sqlite3.Connection:
    cx = sqlite3.connect(db_path.as_posix())
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.execute("PRAGMA busy_timeout=5000")
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


def table_columns(table: str, db_path: Optional[Path] = None) -> List[str]:
    path = db_path or resolve_db_path()
    if not path.exists():
        return []
    cx = _connect(path)
    try:
        rows = cx.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        cx.close()
    return [str(row[1]) for row in rows if row and len(row) > 1 and row[1]]


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


def _hedge_context_from_mapping(mapping: Any) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    return {
        "control_state": mapping.get("control_state") or mapping.get("hedge_control_state"),
        "hedge_action": mapping.get("hedge_action"),
        "hedge_cluster_id": mapping.get("hedge_cluster_id") or mapping.get("cluster_id"),
        "hedge_action_reason": mapping.get("hedge_action_reason"),
        "hedge_market_id": mapping.get("hedge_market_id"),
        "hedge_target_token_id": mapping.get("hedge_target_token_id"),
        "hedge_target_side": mapping.get("hedge_target_side"),
        "hedge_preferred_side": mapping.get("hedge_preferred_side"),
        "hedge_ratio": mapping.get("hedge_ratio"),
        "hedge_quality_score": mapping.get("hedge_quality_score"),
        "hedge_success_window_ms": mapping.get("hedge_success_window_ms"),
        "hedge_failed_cooldown_until_ms": mapping.get("hedge_failed_cooldown_until_ms"),
    }


def parse_reasons(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def humanize_reason_codes(raw: Any) -> str:
    reason_map = {
        "book_absent": "order book unavailable",
        "book_empty": "order book empty",
        "one_sided_book": "one-sided book",
        "price_out_of_range": "price outside safe range",
        "spread_too_wide": "spread too wide",
        "insufficient_volume": "volume too low",
        "insufficient_open_interest": "open interest too low",
        "liquidity_score_too_low": "liquidity score too low",
        "stale_position": "stale inventory needs reducing",
        "take_profit": "locking in profit",
        "stop_loss": "cutting a losing position",
        "flow_blocks_buy": "buy flow blocked",
        "flow_blocks_sell": "sell flow blocked",
        "quoteable_book": "book is quoteable",
        "freeze": "safety gate freeze",
        "no_hedge_market": "no better hedge market was available",
        "hedge_not_better_than_inventory_market": "hedge quality did not beat the inventory market",
        "gross_increase_ceiling_exhausted": "temporary gross exposure would exceed the ceiling",
        "stale_inventory_required": "stale inventory was required before hedging",
        "maker_exit_window_active": "maker exit window was still active",
        "hedge_failed_cooldown": "failed hedge cooldown was still active",
        "hedge_failed_no_improvement": "hedge did not improve inventory enough",
        "stop_open_window": "open-window guard blocked the hedge",
        "force_flat_window": "force-flat window was active",
        "forced_reduction": "forced reduction was already in progress",
        "paper_only": "paper-only hedge telemetry",
    }
    reasons = parse_reasons(raw)
    if not reasons:
        return "policy gate"
    words = [reason_map.get(reason.lower(), reason.replace("_", " ")) for reason in reasons[:3]]
    return "; ".join(words)


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


def _friendly_symbol_from_market_slug(slug: Any) -> Optional[str]:
    if slug is None:
        return None
    text = str(slug).strip()
    if not text:
        return None
    head = text.split("-", 1)[0].strip().upper()
    if head.startswith("KX") and len(head) > 2:
        head = head[2:]
    return head or None


def _infer_exchange_from_market_slug(slug: Any) -> str:
    head = str(slug or "").strip().upper()
    if head.startswith("KX"):
        return "Kalshi"
    if head:
        return "Polymarket"
    return "Unknown"


def _market_label_from_slug(slug: Any) -> str:
    text = str(slug or "").strip()
    if not text:
        return "Unknown market"
    parts = text.split("-")
    if len(parts) >= 3 and parts[1].lower() == "updown":
        symbol = parts[0].upper()
        horizon = parts[2]
        return f"{symbol} {horizon} Up/Down"
    return text


def _format_age_s(age_s: float) -> str:
    if age_s < 60:
        return f"{age_s:.0f}s"
    mins = age_s / 60.0
    if mins < 60:
        return f"{mins:.0f}m"
    hours = mins / 60.0
    return f"{hours:.1f}h"


def _cluster_exposure_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cluster_exposure = payload.get("cluster_exposure")
    if isinstance(cluster_exposure, dict):
        return cluster_exposure
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    cluster_exposure = runner.get("cluster_exposure")
    return cluster_exposure if isinstance(cluster_exposure, dict) else {}


def _cluster_hedge_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cluster_hedge = payload.get("cluster_hedge")
    if isinstance(cluster_hedge, dict):
        return cluster_hedge
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    cluster_hedge = runner.get("cluster_hedge")
    return cluster_hedge if isinstance(cluster_hedge, dict) else {}


def _runtime_health_snapshot(status: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    selection = status.get("selection") if isinstance(status.get("selection"), dict) else {}
    if not selection and isinstance(payload.get("selection"), dict):
        selection = payload.get("selection")  # type: ignore[assignment]
    active_market_health = status.get("active_market_health") if isinstance(status.get("active_market_health"), dict) else {}
    if not active_market_health and isinstance(payload.get("active_market_health"), dict):
        active_market_health = payload.get("active_market_health")  # type: ignore[assignment]

    book_diag = runner.get("book_diag") if isinstance(runner.get("book_diag"), dict) else {}
    per_token = book_diag.get("per_token") if isinstance(book_diag.get("per_token"), dict) else {}
    spread_samples: List[float] = []
    tokens_ok = int(book_diag.get("tokens_ok") or 0)
    tokens_blocked = int(book_diag.get("tokens_blocked") or 0)
    for diag in per_token.values():
        if not isinstance(diag, dict):
            continue
        bid = _float_or_none(diag.get("best_bid"))
        ask = _float_or_none(diag.get("best_ask"))
        if bid is None or ask is None or bid <= 0.0 or ask <= 0.0:
            continue
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_samples.append(((ask - bid) / mid) * 10000.0)

    quoteable = active_market_health.get("quoteable")
    if quoteable is None:
        quoteable = active_market_health.get("book_valid_both_sides")
    if quoteable is None:
        quoteable = bool(runner.get("has_books")) and tokens_ok > 0 and tokens_blocked == 0
    quoteable = bool(quoteable)

    book_health = str(
        active_market_health.get("book_health")
        or active_market_health.get("state")
        or ("healthy" if quoteable else "degraded")
    )
    state = str(active_market_health.get("state") or ("healthy" if quoteable else "degraded"))
    spread_bps = _float_or_none(active_market_health.get("spread_bps"))
    if spread_bps is None:
        spread_bps = percentile(spread_samples, 0.5) if spread_samples else None

    selected_reason = (
        selection.get("selected_reason")
        or selection.get("reason")
        or selection.get("selected_reason_code")
        or active_market_health.get("reason")
        or active_market_health.get("selected_reason")
    )

    freeze_reasons = selection.get("freeze_reasons")
    if not isinstance(freeze_reasons, list):
        freeze_reasons = []
    return {
        "selection": selection,
        "active_market_health": active_market_health,
        "book_health": book_health,
        "state": state,
        "quoteable": quoteable,
        "spread_bps": spread_bps,
        "selected_reason": selected_reason,
        "book_valid_both_sides": bool(active_market_health.get("book_valid_both_sides") if active_market_health.get("book_valid_both_sides") is not None else quoteable),
        "tokens_ok": tokens_ok,
        "tokens_blocked": tokens_blocked,
        "freeze_reasons": freeze_reasons,
    }


def get_runtime_status_snapshot(runtime_root: Optional[Path] = None, db_path: Optional[Path] = None) -> Dict[str, Any]:
    runtime_root_path = Path(runtime_root) if runtime_root is not None else runtime_root_for_db(db_path)
    resolved_db_path = Path(db_path) if db_path is not None else runtime_root_path / "runtime.db"
    status = get_run_status(runtime_root=runtime_root_path, db_path=resolved_db_path)
    payload = get_latest_system_payload(db_path=resolved_db_path if resolved_db_path.exists() else db_path)
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    health = _runtime_health_snapshot(status, payload)
    market = str(status.get("market") or runner.get("market_id") or "")
    mode = str(status.get("mode") or runner.get("mode") or "UNKNOWN").upper()
    stage = str(status.get("stage") or "unknown")
    strategy_name = str(status.get("run_name") or runtime_root_path.name)
    exchange = str(status.get("exchange") or payload.get("exchange") or _infer_exchange_from_market_slug(market))

    updated_at_ms = _int_or_none(status.get("updated_at_ms"))
    if updated_at_ms is None:
        updated_at_ms = _int_or_none(payload.get("updated_at_ms"))

    decisions = _int_or_none(status.get("decisions"))
    fills = _int_or_none(status.get("fills"))
    orders = _int_or_none(status.get("order_actions"))

    broker_stats = payload.get("broker_stats") if isinstance(payload.get("broker_stats"), dict) else {}
    control_state = payload.get("control_state") if isinstance(payload.get("control_state"), dict) else {}
    if not control_state and isinstance(status.get("control_state"), dict):
        control_state = status.get("control_state")  # type: ignore[assignment]
    realized_net_pnl = _float_or_none(broker_stats.get("realized_net_pnl"))
    unrealized_pnl = _float_or_none(broker_stats.get("unrealized_pnl"))
    total_pnl = None
    if realized_net_pnl is not None or unrealized_pnl is not None:
        total_pnl = float(realized_net_pnl or 0.0) + float(unrealized_pnl or 0.0)

    return {
        "runtime_root": runtime_root_path.as_posix(),
        "db_path": (Path(db_path).resolve().as_posix() if db_path is not None else (runtime_root_path / "runtime.db").resolve().as_posix()),
        "exchange": exchange,
        "mode": mode,
        "stage": stage,
        "market": market,
        "strategy_name": strategy_name,
        "symbols": status.get("symbols") if isinstance(status.get("symbols"), list) else [],
        "updated_at_ms": updated_at_ms,
        "decisions": decisions,
        "fills": fills,
        "order_actions": orders,
        "realized_net_pnl": realized_net_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "selection": health["selection"],
        "active_market_health": health["active_market_health"],
        "selected_reason": health["selected_reason"],
        "book_health": health["book_health"],
        "quoteable": health["quoteable"],
        "book_valid_both_sides": health["book_valid_both_sides"],
        "spread_bps": health["spread_bps"],
        "state": health["state"],
        "freeze_reasons": health["freeze_reasons"],
        "runner": runner,
        "control_state": control_state,
        "cluster_exposure": _cluster_exposure_payload(payload),
        "cluster_hedge": _cluster_hedge_payload(payload),
        "status": status,
        "payload_json": payload,
    }


def get_cluster_exposure_snapshot(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    cluster_exposure = snapshot.get("cluster_exposure")
    return cluster_exposure if isinstance(cluster_exposure, dict) else {}


def get_cluster_hedge_snapshot(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    cluster_hedge = snapshot.get("cluster_hedge")
    return cluster_hedge if isinstance(cluster_hedge, dict) else {}


def get_cluster_exposure_rows(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    cluster_exposure = get_cluster_exposure_snapshot(runtime_snapshot=runtime_snapshot, db_path=db_path)
    cluster_hedge = get_cluster_hedge_snapshot(runtime_snapshot=runtime_snapshot, db_path=db_path)
    clusters = cluster_exposure.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return pd.DataFrame()

    hedge_by_cluster: Dict[str, Dict[str, Any]] = {}
    for hedge_cluster in cluster_hedge.get("clusters") or []:
        if not isinstance(hedge_cluster, dict):
            continue
        cluster_id = str(hedge_cluster.get("cluster_id") or "")
        if cluster_id:
            hedge_by_cluster[cluster_id] = hedge_cluster

    rows: List[Dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("cluster_id") or cluster.get("event_id") or "")
        hedge_meta = hedge_by_cluster.get(cluster_id, {})
        row = {
            "cluster_id": cluster_id or None,
            "event_id": cluster.get("event_id"),
            "market_count": _int_or_none(cluster.get("market_count")) or 0,
            "active_market_count": _int_or_none(cluster.get("active_market_count")) or 0,
            "yes_exposure_notional": _float_or_none(cluster.get("yes_exposure_notional")) or 0.0,
            "no_exposure_notional": _float_or_none(cluster.get("no_exposure_notional")) or 0.0,
            "net_yes_exposure_notional": _float_or_none(cluster.get("net_yes_exposure_notional")) or 0.0,
            "gross_exposure": _float_or_none(cluster.get("gross_exposure")) or 0.0,
            "unrealized_pnl": _float_or_none(cluster.get("unrealized_pnl")) or 0.0,
            "time_to_expiry_ms": _int_or_none(cluster.get("time_to_expiry_ms")),
            "max_event_exposure_notional": _float_or_none(cluster.get("max_event_exposure_notional")),
            "remaining_event_exposure_notional": _float_or_none(cluster.get("remaining_event_exposure_notional")),
            "stale_inventory_state": "stale" if bool(cluster.get("has_stale_inventory")) else "fresh",
            "stale_market_count": _int_or_none(cluster.get("stale_market_count")) or 0,
            "stale_exposure_notional": _float_or_none(cluster.get("stale_exposure_notional")) or 0.0,
            "control_state": hedge_meta.get("control_state") or cluster.get("control_state") or cluster.get("state"),
            "hedge_action": hedge_meta.get("action") or cluster.get("hedge_action") or cluster.get("action") or cluster.get("next_action"),
            "hedge_action_reason": hedge_meta.get("action_reason") or cluster.get("hedge_action_reason") or cluster.get("action_reason") or cluster.get("reason"),
            "hedge_ratio": _float_or_none(
                hedge_meta.get("hedge_ratio")
                if hedge_meta.get("hedge_ratio") is not None
                else (cluster.get("hedge_ratio") if cluster.get("hedge_ratio") is not None else cluster.get("target_hedge_ratio"))
            ),
            "hedge_target_market": hedge_meta.get("hedge_market_id") or cluster.get("hedge_target_market") or cluster.get("hedge_target_market_id") or cluster.get("target_market"),
            "hedge_target_token": hedge_meta.get("hedge_target_token_id") or cluster.get("hedge_target_token") or cluster.get("hedge_target_token_id"),
            "hedge_target_side": hedge_meta.get("hedge_target_side") or cluster.get("hedge_target_side"),
            "dominant_side": hedge_meta.get("dominant_side"),
            "affected_market_ids": list(hedge_meta.get("affected_market_ids") or []),
            "hedge_rejection_reasons": ", ".join(str(item) for item in (hedge_meta.get("rejection_reasons") or []) if item not in (None, "")) or None,
        }
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        by=["active_market_count", "gross_exposure", "cluster_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def get_cluster_market_rows(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    cluster_exposure = get_cluster_exposure_snapshot(runtime_snapshot=runtime_snapshot, db_path=db_path)
    cluster_rows = get_cluster_exposure_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    clusters = cluster_exposure.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return pd.DataFrame()

    cluster_meta: Dict[str, Dict[str, Any]] = {}
    if not cluster_rows.empty:
        cluster_meta = {
            str(row.get("cluster_id") or ""): row
            for row in cluster_rows.to_dict("records")
            if str(row.get("cluster_id") or "")
        }

    rows: List[Dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = cluster.get("cluster_id") or cluster.get("event_id")
        cluster_info = cluster_meta.get(str(cluster_id or ""), {})
        markets = cluster.get("markets")
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            market_id = market.get("market_id") or market.get("condition_id")
            affected_market_ids = cluster_info.get("affected_market_ids") or []
            rows.append(
                {
                    "cluster_id": cluster_id,
                    "market_id": market_id,
                    "condition_id": market.get("condition_id"),
                    "active": bool(market.get("active")),
                    "market_position_notional": _float_or_none(market.get("market_position_notional")) or 0.0,
                    "market_unrealized_pnl": _float_or_none(market.get("market_unrealized_pnl")) or 0.0,
                    "yes_exposure_notional": _float_or_none(market.get("yes_exposure_notional")) or 0.0,
                    "no_exposure_notional": _float_or_none(market.get("no_exposure_notional")) or 0.0,
                    "unknown_exposure_notional": _float_or_none(market.get("unknown_exposure_notional")) or 0.0,
                    "time_to_expiry_ms": _int_or_none(market.get("time_to_expiry_ms")),
                    "stale_inventory_state": "stale" if bool(market.get("has_stale_inventory")) else ("stale" if bool(cluster.get("has_stale_inventory")) else "fresh"),
                    "control_state": cluster_info.get("control_state"),
                    "hedge_action": market.get("hedge_action") or market.get("action") or market.get("next_action") or cluster_info.get("hedge_action"),
                    "hedge_action_reason": market.get("hedge_action_reason") or market.get("action_reason") or market.get("reason") or cluster_info.get("hedge_action_reason"),
                    "hedge_ratio": _float_or_none(
                        market.get("hedge_ratio")
                        if market.get("hedge_ratio") is not None
                        else (market.get("target_hedge_ratio") if market.get("target_hedge_ratio") is not None else cluster_info.get("hedge_ratio"))
                    ),
                    "hedge_target_market": market.get("hedge_target_market") or market.get("hedge_target_market_id") or market.get("target_market") or cluster_info.get("hedge_target_market"),
                    "hedge_target_token": market.get("hedge_target_token") or market.get("hedge_target_token_id") or cluster_info.get("hedge_target_token"),
                    "hedge_target_side": market.get("hedge_target_side") or cluster_info.get("hedge_target_side"),
                    "affected_by_cluster_action": bool(market_id and market_id in affected_market_ids),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        by=["active", "cluster_id", "market_position_notional"],
        ascending=[False, True, False],
    ).reset_index(drop=True)


def get_active_market_rows(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    cluster_market_rows = get_cluster_market_rows(runtime_snapshot=snapshot, db_path=db_path)
    if not cluster_market_rows.empty:
        active_rows = cluster_market_rows[cluster_market_rows["active"].astype(bool)].copy()
        if not active_rows.empty:
            return active_rows.sort_values(
                by=["cluster_id", "market_position_notional", "market_id"],
                ascending=[True, False, True],
            ).reset_index(drop=True)

    runner = snapshot.get("runner") if isinstance(snapshot.get("runner"), dict) else {}
    active_market_health = snapshot.get("active_market_health") if isinstance(snapshot.get("active_market_health"), dict) else {}
    market_ids = runner.get("market_ids") or active_market_health.get("market_ids") or []
    if not isinstance(market_ids, list) or not market_ids:
        return pd.DataFrame()
    rows = []
    for market_id in market_ids:
        rows.append(
            {
                "cluster_id": None,
                "market_id": market_id,
                "active": True,
                "market_position_notional": None,
                "market_unrealized_pnl": None,
                "time_to_expiry_ms": _int_or_none(active_market_health.get("time_to_expiry_ms")),
                "stale_inventory_state": None,
                "control_state": None,
                "hedge_action": None,
                "hedge_action_reason": None,
                "affected_by_cluster_action": False,
            }
        )
    return pd.DataFrame(rows)


def get_selection_diagnostic_rows(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    selection = snapshot.get("selection") if isinstance(snapshot.get("selection"), dict) else {}
    rows: List[Dict[str, Any]] = []
    for accepted_state, key in ((True, "accepted_candidates"), (False, "rejected_candidates")):
        candidates = selection.get(key) or []
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            rows.append(
                {
                    "accepted": accepted_state,
                    "status": "accepted" if accepted_state else "rejected",
                    "ticker": candidate.get("ticker"),
                    "title": candidate.get("title"),
                    "reason": candidate.get("reason"),
                    "quoteability_state": candidate.get("quoteability_state"),
                    "score": _float_or_none(candidate.get("score")),
                    "liquidity_score": _float_or_none(candidate.get("liquidity_score")),
                    "transition_risk": _float_or_none(candidate.get("transition_risk")),
                    "proximity_score": _float_or_none(candidate.get("proximity_score")),
                    "mid": _float_or_none(candidate.get("mid")),
                    "spread": _float_or_none(candidate.get("spread")),
                    "volume": _float_or_none(candidate.get("volume")),
                    "touch_depth": _float_or_none(candidate.get("touch_depth")),
                    "blocking_market_id": candidate.get("blocking_market_id") or candidate.get("suppressed_by_market_id"),
                    "blocking_cluster_id": candidate.get("blocking_cluster_id") or candidate.get("suppressed_by_cluster_id"),
                    "blocking_reason": candidate.get("blocking_reason") or candidate.get("suppression_reason"),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        by=["accepted", "score", "liquidity_score"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def get_selection_diagnostic_gaps(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> List[str]:
    rows = get_selection_diagnostic_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    if rows.empty:
        return ["selection candidate diagnostics missing"]
    missing: List[str] = []
    if "blocking_market_id" not in rows.columns or not rows["blocking_market_id"].notna().any():
        missing.append("blocking market id")
    if "blocking_cluster_id" not in rows.columns or not rows["blocking_cluster_id"].notna().any():
        missing.append("blocking cluster id")
    if "blocking_reason" not in rows.columns or not rows["blocking_reason"].notna().any():
        missing.append("blocking reason")
    return missing


def _hedge_candidate_rows_from_snapshot(runtime_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    cluster_hedge = get_cluster_hedge_snapshot(runtime_snapshot=runtime_snapshot)
    cluster_exposure = get_cluster_exposure_snapshot(runtime_snapshot=runtime_snapshot)
    exposure_by_cluster: Dict[str, Dict[str, Any]] = {}
    for cluster in cluster_exposure.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        if cluster_id:
            exposure_by_cluster[cluster_id] = cluster

    rows: List[Dict[str, Any]] = []
    for cluster in cluster_hedge.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        if not cluster_id:
            continue
        exposure = exposure_by_cluster.get(cluster_id, {})
        candidate_summary = dict(cluster.get("candidate_summary") or {})
        rejection_reasons = [str(item) for item in (cluster.get("rejection_reasons") or []) if item not in (None, "")]
        candidate_state = str(cluster.get("candidate_state") or "").strip().lower()
        if not candidate_state:
            candidate_state = "rejected" if rejection_reasons else ("accepted" if str(cluster.get("action") or "").upper() == "HEDGE" else "deferred")
        hedge_quality_score = _float_or_none(cluster.get("hedge_quality_score"))
        inventory_quality_score = _float_or_none(
            cluster.get("inventory_market_quality_score")
            if cluster.get("inventory_market_quality_score") not in (None, "")
            else exposure.get("dominant_inventory_market_quality_score")
        )
        hedge_quality_gap = _float_or_none(cluster.get("hedge_quality_gap"))
        if hedge_quality_gap is None and hedge_quality_score is not None and inventory_quality_score is not None:
            hedge_quality_gap = hedge_quality_score - inventory_quality_score
        best_candidate = dict(candidate_summary.get("best_candidate") or {})
        rows.append(
            {
                "cluster_id": cluster_id,
                "action": str(cluster.get("action") or "NONE"),
                "candidate_state": candidate_state,
                "control_state": str(cluster.get("control_state") or "NORMAL"),
                "action_reason": cluster.get("action_reason"),
                "dominant_side": cluster.get("dominant_side"),
                "hedge_market_id": cluster.get("hedge_market_id"),
                "hedge_target_token_id": cluster.get("hedge_target_token_id"),
                "hedge_target_side": cluster.get("hedge_target_side"),
                "hedge_ratio": _float_or_none(cluster.get("hedge_ratio")),
                "inventory_market_quality_score": inventory_quality_score,
                "hedge_quality_score": hedge_quality_score,
                "hedge_execution_quality_score": _float_or_none(cluster.get("hedge_execution_quality_score")),
                "hedge_quality_gap": hedge_quality_gap,
                "hedge_covariance": _float_or_none(cluster.get("hedge_covariance")),
                "hedge_correlation": _float_or_none(cluster.get("hedge_correlation")),
                "hedge_beta_raw": _float_or_none(cluster.get("hedge_beta_raw")),
                "hedge_beta": _float_or_none(cluster.get("hedge_beta")),
                "hedge_beta_shrunk": _float_or_none(cluster.get("hedge_beta_shrunk")),
                "hedge_beta_clipped": _float_or_none(cluster.get("hedge_beta_clipped")),
                "hedge_covariance_sample_count": _int_or_none(cluster.get("hedge_covariance_sample_count")),
                "hedge_covariance_state": cluster.get("hedge_covariance_state"),
                "hedge_covariance_confidence": cluster.get("hedge_covariance_confidence"),
                "hedge_pair_score": _float_or_none(cluster.get("hedge_pair_score")),
                "hedgeability_tier": cluster.get("hedgeability_tier"),
                "hedge_structural_score": _float_or_none(cluster.get("hedge_structural_score")),
                "hedge_covariance_score": _float_or_none(cluster.get("hedge_covariance_score")),
                "hedge_beta_stability_score": _float_or_none(cluster.get("hedge_beta_stability_score")),
                "hedge_execution_availability_score": _float_or_none(cluster.get("hedge_execution_availability_score")),
                "hedge_realized_outcome_score": _float_or_none(cluster.get("hedge_realized_outcome_score")),
                "hedge_relation_confidence_state": cluster.get("hedge_relation_confidence_state"),
                "hedge_permission_state": cluster.get("hedge_permission_state"),
                "hedge_rejection_reason": cluster.get("hedge_rejection_reason"),
                "hedge_model_state": cluster.get("hedge_model_state"),
                "hedge_realized_improvement_state": cluster.get("hedge_realized_improvement_state"),
                "hedge_success_window_ms": _int_or_none(cluster.get("hedge_success_window_ms")),
                "hedge_failed_cooldown_until_ms": _int_or_none(cluster.get("hedge_failed_cooldown_until_ms")),
                "candidate_count": _int_or_none(candidate_summary.get("candidate_count")),
                "accepted_count": _int_or_none(candidate_summary.get("accepted_count")),
                "rejection_counts_json": json.dumps(candidate_summary.get("rejection_counts") or {}, sort_keys=True),
                "best_candidate_market_id": best_candidate.get("market_id"),
                "best_candidate_token_id": best_candidate.get("token_id"),
                "best_candidate_quality_score": _float_or_none(best_candidate.get("quality_score")),
                "best_candidate_quality_gap": _float_or_none(best_candidate.get("quality_gap")),
                "best_candidate_alignment_fraction": _float_or_none(best_candidate.get("alignment_fraction")),
                "search_profile": candidate_summary.get("search_profile"),
                "proof_only_lane": bool(candidate_summary.get("proof_only_lane")),
                "proof_only_bucket_distance": _int_or_none(candidate_summary.get("proof_only_bucket_distance")),
                "proof_only_expiry_slack_ms": _int_or_none(candidate_summary.get("proof_only_expiry_slack_ms")),
                "rejection_reasons": json.dumps(sorted(rejection_reasons)),
                "affected_market_ids": json.dumps(sorted(str(item) for item in (cluster.get("affected_market_ids") or []) if item not in (None, ""))),
                "token_directives_json": json.dumps(cluster.get("token_directives") or [], sort_keys=True),
                "quality_gap_state": (
                    "positive"
                    if hedge_quality_gap is not None and hedge_quality_gap > 0
                    else "negative"
                    if hedge_quality_gap is not None and hedge_quality_gap < 0
                    else "flat"
                    if hedge_quality_gap == 0
                    else "unknown"
                ),
            }
        )
    return rows


def get_hedge_candidate_rows(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    path = db_path or resolve_db_path()
    rows: pd.DataFrame = pd.DataFrame()
    if table_exists("hedge_candidates", path):
        desired_columns = [
            "ts_ms",
            "event_id",
            "cluster_id",
            "action",
            "candidate_state",
            "control_state",
            "action_reason",
            "dominant_side",
            "hedge_market_id",
            "hedge_target_token_id",
            "hedge_target_side",
            "hedge_ratio",
            "inventory_market_quality_score",
            "hedge_quality_score",
            "hedge_execution_quality_score",
            "hedge_quality_gap",
            "hedge_covariance",
            "hedge_correlation",
            "hedge_beta_raw",
            "hedge_beta",
            "hedge_beta_shrunk",
            "hedge_beta_clipped",
            "hedge_covariance_sample_count",
            "hedge_covariance_state",
            "hedge_covariance_confidence",
            "hedge_pair_score",
            "hedgeability_tier",
            "hedge_structural_score",
            "hedge_covariance_score",
            "hedge_beta_stability_score",
            "hedge_execution_availability_score",
            "hedge_realized_outcome_score",
            "hedge_relation_confidence_state",
            "hedge_permission_state",
            "hedge_rejection_reason",
            "hedge_model_state",
            "hedge_realized_improvement_state",
            "hedge_success_window_ms",
            "hedge_failed_cooldown_until_ms",
            "candidate_count",
            "accepted_count",
            "rejection_counts_json",
            "best_candidate_market_id",
            "best_candidate_token_id",
            "best_candidate_quality_score",
            "best_candidate_quality_gap",
            "best_candidate_alignment_fraction",
            "search_profile",
            "proof_only_lane",
            "proof_only_bucket_distance",
            "proof_only_expiry_slack_ms",
            "rejection_reasons",
            "affected_market_ids",
            "token_directives_json",
            "quality_gap_state",
            "payload_json",
        ]
        available = set(table_columns("hedge_candidates", db_path=path))
        selected_columns = [column for column in desired_columns if column in available]
        rows = query_df(
            f"""
            SELECT
                {', '.join(selected_columns)}
            FROM hedge_candidates
            ORDER BY ts_ms ASC, event_id ASC
            """,
            db_path=path,
        )
    if rows.empty:
        rows = pd.DataFrame(_hedge_candidate_rows_from_snapshot(snapshot))
    if rows.empty:
        return rows
    rows = rows.copy()
    candidate_state = rows["candidate_state"].astype(str) if "candidate_state" in rows.columns else pd.Series([""] * len(rows), index=rows.index)
    rows["accepted"] = candidate_state.eq("accepted")
    rows["rejected"] = candidate_state.eq("rejected")
    rows["deferred"] = candidate_state.eq("deferred")
    if "hedge_quality_gap" in rows.columns:
        rows["hedge_quality_gap"] = rows["hedge_quality_gap"].apply(_float_or_none)
    if "hedge_covariance" in rows.columns:
        rows["hedge_covariance"] = rows["hedge_covariance"].apply(_float_or_none)
    if "hedge_correlation" in rows.columns:
        rows["hedge_correlation"] = rows["hedge_correlation"].apply(_float_or_none)
    if "hedge_beta_raw" in rows.columns:
        rows["hedge_beta_raw"] = rows["hedge_beta_raw"].apply(_float_or_none)
    if "hedge_beta" in rows.columns:
        rows["hedge_beta"] = rows["hedge_beta"].apply(_float_or_none)
    if "hedge_beta_shrunk" in rows.columns:
        rows["hedge_beta_shrunk"] = rows["hedge_beta_shrunk"].apply(_float_or_none)
    if "hedge_beta_clipped" in rows.columns:
        rows["hedge_beta_clipped"] = rows["hedge_beta_clipped"].apply(_float_or_none)
    if "hedge_covariance_sample_count" in rows.columns:
        rows["hedge_covariance_sample_count"] = rows["hedge_covariance_sample_count"].apply(_int_or_none)
    for column in (
        "hedge_pair_score",
        "hedge_structural_score",
        "hedge_covariance_score",
        "hedge_beta_stability_score",
        "hedge_execution_availability_score",
        "hedge_realized_outcome_score",
    ):
        if column in rows.columns:
            rows[column] = rows[column].apply(_float_or_none)
    if "candidate_count" in rows.columns:
        rows["candidate_count"] = rows["candidate_count"].apply(_int_or_none)
    if "accepted_count" in rows.columns:
        rows["accepted_count"] = rows["accepted_count"].apply(_int_or_none)
    if "best_candidate_quality_score" in rows.columns:
        rows["best_candidate_quality_score"] = rows["best_candidate_quality_score"].apply(_float_or_none)
    if "best_candidate_quality_gap" in rows.columns:
        rows["best_candidate_quality_gap"] = rows["best_candidate_quality_gap"].apply(_float_or_none)
    if "best_candidate_alignment_fraction" in rows.columns:
        rows["best_candidate_alignment_fraction"] = rows["best_candidate_alignment_fraction"].apply(_float_or_none)
    if "proof_only_lane" in rows.columns:
        rows["proof_only_lane"] = rows["proof_only_lane"].apply(lambda value: bool(_int_or_none(value)))
    if "rejection_counts_json" in rows.columns:
        rows["rejection_counts_json"] = rows["rejection_counts_json"].fillna("{}")
    if "rejection_reasons" in rows.columns:
        rows["rejection_reason_count"] = rows["rejection_reasons"].apply(
            lambda raw: len(json.loads(raw)) if isinstance(raw, str) and raw else (len(raw) if isinstance(raw, list) else 0)
        )
    else:
        rows["rejection_reason_count"] = 0
    if "candidate_count" in rows.columns and "accepted_count" in rows.columns:
        rows["rejected_count"] = rows["candidate_count"].fillna(0).astype(int) - rows["accepted_count"].fillna(0).astype(int)
    else:
        rows["rejected_count"] = 0
    return rows.sort_values(
        by=["accepted", "hedge_quality_gap", "hedge_quality_score", "cluster_id"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)


def get_hedge_candidate_gaps(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> List[str]:
    rows = get_hedge_candidate_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    if rows.empty:
        return ["hedge candidate diagnostics missing"]
    missing: List[str] = []
    if "hedge_quality_gap" not in rows.columns or not rows["hedge_quality_gap"].notna().any():
        missing.append("hedge quality gap")
    if "hedge_market_id" not in rows.columns or not rows["hedge_market_id"].notna().any():
        missing.append("hedge target market")
    if "hedge_target_token_id" not in rows.columns or "hedge_target_side" not in rows.columns:
        missing.append("hedge target token/side")
    return missing


def _first_present_dict(*sources: Any) -> Dict[str, Any]:
    for source in sources:
        if isinstance(source, dict):
            return source
    return {}


def _extract_ms_metric(*sources: Any, keys: Sequence[str]) -> Optional[float]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                out = float(value)
            except (TypeError, ValueError):
                continue
            if math.isnan(out):
                continue
            return out
    return None


def _extract_hold_tail_metrics(summary: Dict[str, Any], risk_proof: Dict[str, Any], control: Dict[str, Any]) -> Dict[str, Any]:
    hold_tail_payload = _first_present_dict(
        summary.get("hold_tail"),
        summary.get("hold_tail_metrics"),
        risk_proof.get("hold_tail"),
        risk_proof.get("hold_tail_metrics"),
        control.get("hold_tail"),
    )
    hold_tail_distribution = hold_tail_payload.get("distribution") if isinstance(hold_tail_payload, dict) else None
    if hold_tail_distribution is None:
        for source in (summary, risk_proof, control):
            if not isinstance(source, dict):
                continue
            for key in ("hold_tail_distribution", "distribution"):
                if key in source and source.get(key) not in (None, ""):
                    hold_tail_distribution = source.get(key)
                    break
            if hold_tail_distribution is not None:
                break

    sample_count = None
    for key in ("sample_count", "count", "n", "observations", "samples"):
        value = _extract_ms_metric(hold_tail_payload, summary, risk_proof, control, keys=(key,))
        if value is not None:
            sample_count = int(value)
            break

    source = None
    if hold_tail_payload:
        source = "nested"
    elif any(key in summary for key in ("hold_tail_distribution", "hold_tail")):
        source = "summary"
    elif any(key in risk_proof for key in ("hold_tail_distribution", "hold_tail")):
        source = "risk_proof"

    return {
        "sample_count": sample_count,
        "p50_ms": _extract_ms_metric(hold_tail_payload, summary, risk_proof, control, keys=("p50_ms", "p50", "median_ms", "median")),
        "p90_ms": _extract_ms_metric(hold_tail_payload, summary, risk_proof, control, keys=("p90_ms", "p90")),
        "p95_ms": _extract_ms_metric(hold_tail_payload, summary, risk_proof, control, keys=("p95_ms", "p95")),
        "max_ms": _extract_ms_metric(hold_tail_payload, summary, risk_proof, control, keys=("max_ms", "max", "p100_ms")),
        "distribution": hold_tail_distribution if hold_tail_distribution not in ({}, []) else None,
        "source": source,
    }


def _summarize_hedge_rejections(selection_rows: pd.DataFrame) -> Dict[str, Any]:
    if selection_rows.empty or "accepted" not in selection_rows.columns:
        return {"reason_counts": {}, "top_reason": None, "top_reason_count": 0}
    rejected = selection_rows[~selection_rows["accepted"].astype(bool)].copy()
    if rejected.empty or "reason" not in rejected.columns:
        return {"reason_counts": {}, "top_reason": None, "top_reason_count": 0}
    reasons = rejected["reason"].dropna().astype(str)
    if reasons.empty:
        return {"reason_counts": {}, "top_reason": None, "top_reason_count": 0}
    counts = reasons.value_counts()
    return {
        "reason_counts": {str(key): int(value) for key, value in counts.to_dict().items()},
        "top_reason": str(counts.index[0]),
        "top_reason_count": int(counts.iloc[0]),
    }


def get_hedge_readout_summary(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    selection = snapshot.get("selection") if isinstance(snapshot.get("selection"), dict) else {}
    control = get_control_plane_snapshot(db_path=db_path)
    run_summary = get_run_summary(runtime_root=snapshot.get("runtime_root"), db_path=db_path)
    risk_proof = dict(run_summary.get("risk_proof") or {})

    rows = get_selection_diagnostic_rows(runtime_snapshot=snapshot, db_path=db_path)
    accepted_rows = rows[rows["accepted"].astype(bool)].copy() if not rows.empty and "accepted" in rows.columns else pd.DataFrame()
    rejected_rows = rows[~rows["accepted"].astype(bool)].copy() if not rows.empty and "accepted" in rows.columns else pd.DataFrame()

    top_accepted: Dict[str, Any] = {}
    top_rejected: Dict[str, Any] = {}
    top_accepted_score: Optional[float] = None
    top_rejected_score: Optional[float] = None

    if not accepted_rows.empty:
        score_cols = [col for col in ("score", "liquidity_score", "transition_risk", "proximity_score") if col in accepted_rows.columns]
        if score_cols:
            accepted_sorted = accepted_rows.sort_values(by=score_cols[: min(2, len(score_cols))], ascending=[False] * min(2, len(score_cols)))
        else:
            accepted_sorted = accepted_rows.sort_values(by=["ticker"], ascending=[True])
        top_accepted = accepted_sorted.iloc[0].to_dict()
        top_accepted_score = _float_or_none(top_accepted.get("score"))

    if not rejected_rows.empty:
        score_cols = [col for col in ("score", "liquidity_score", "transition_risk", "proximity_score") if col in rejected_rows.columns]
        if score_cols:
            rejected_sorted = rejected_rows.sort_values(by=score_cols[: min(2, len(score_cols))], ascending=[False] * min(2, len(score_cols)))
        else:
            rejected_sorted = rejected_rows.sort_values(by=["ticker"], ascending=[True])
        top_rejected = rejected_sorted.iloc[0].to_dict()
        top_rejected_score = _float_or_none(top_rejected.get("score"))

    rejection_summary = _summarize_hedge_rejections(rows)
    quality_gap = None
    if top_accepted_score is not None and top_rejected_score is not None:
        quality_gap = float(top_accepted_score) - float(top_rejected_score)

    cluster_hedge = snapshot.get("cluster_hedge") if isinstance(snapshot.get("cluster_hedge"), dict) else {}
    cluster_count = len(cluster_hedge.get("clusters") or []) if isinstance(cluster_hedge, dict) else 0

    accepted_count = int(len(accepted_rows))
    candidate_count = int(len(rows))
    hold_tail = _extract_hold_tail_metrics(run_summary, risk_proof, control)

    return {
        "candidate_count": candidate_count,
        "accepted_count": accepted_count,
        "rejected_count": int(len(rejected_rows)),
        "accepted_rate": (float(accepted_count) / float(candidate_count)) if candidate_count > 0 else None,
        "top_accepted": top_accepted,
        "top_rejected": top_rejected,
        "top_accepted_score": top_accepted_score,
        "top_rejected_score": top_rejected_score,
        "quality_gap": quality_gap,
        "rejection_reason_counts": rejection_summary["reason_counts"],
        "top_rejection_reason": rejection_summary["top_reason"],
        "top_rejection_reason_count": rejection_summary["top_reason_count"],
        "selection_reason": selection.get("selected_reason") or selection.get("reason") or snapshot.get("selected_reason"),
        "selected_market": selection.get("selected_market") or selection.get("market") or snapshot.get("market"),
        "selected_score": _float_or_none(selection.get("selected_score")),
        "selected_quoteability_state": selection.get("quoteability_state") or selection.get("selected_quoteability_state"),
        "cluster_count": cluster_count,
        "hold_tail": hold_tail,
        "risk_proof": risk_proof,
        "forced_flat_events": list(control.get("forced_flat_events") or []),
        "forced_flat_markets": list(control.get("forced_flat_markets") or []),
        "stale_unwind_observed": bool(risk_proof.get("stale_unwind_observed")),
        "force_flat_observed": bool(risk_proof.get("force_flat_observed")),
        "day_loss_observed": bool(risk_proof.get("day_loss_observed")),
        "flatten_only_cycles": _int_or_none(risk_proof.get("flatten_only_cycles")) or 0,
    }


def get_cluster_calibration_gaps(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> List[str]:
    cluster_exposure = get_cluster_exposure_snapshot(runtime_snapshot=runtime_snapshot, db_path=db_path)
    clusters = cluster_exposure.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return ["cluster_exposure payload missing"]

    missing: List[str] = []
    if not any(isinstance(cluster, dict) and cluster.get("control_state") not in (None, "") for cluster in clusters):
        missing.append("cluster control_state")
    if not any(
        isinstance(cluster, dict)
        and (
            cluster.get("hedge_action") not in (None, "")
            or cluster.get("action") not in (None, "")
            or cluster.get("next_action") not in (None, "")
        )
        for cluster in clusters
    ):
        missing.append("cluster hedge action label")
    if not any(
        isinstance(cluster, dict)
        and (
            cluster.get("hedge_action_reason") not in (None, "")
            or cluster.get("action_reason") not in (None, "")
            or cluster.get("reason") not in (None, "")
        )
        for cluster in clusters
    ):
        missing.append("cluster action reason")
    if not any(isinstance(cluster, dict) and cluster.get("unrealized_pnl") not in (None, "") for cluster in clusters):
        missing.append("cluster unrealized_pnl")
    if not any(
        isinstance(cluster, dict)
        and (
            cluster.get("hedge_ratio") not in (None, "")
            or cluster.get("target_hedge_ratio") not in (None, "")
        )
        for cluster in clusters
    ):
        missing.append("cluster hedge ratio")
    if not any(
        isinstance(cluster, dict)
        and (
            cluster.get("hedge_target_market") not in (None, "")
            or cluster.get("hedge_target_market_id") not in (None, "")
            or cluster.get("hedge_target_cluster_id") not in (None, "")
            or cluster.get("target_market") not in (None, "")
        )
        for cluster in clusters
    ):
        missing.append("hedge target market")
    if not any(
        isinstance(cluster, dict)
        and (
            cluster.get("hedge_target_token") not in (None, "")
            or cluster.get("hedge_target_token_id") not in (None, "")
            or cluster.get("hedge_target_side") not in (None, "")
        )
        for cluster in clusters
    ):
        missing.append("hedge target token/side")
    return missing


def discover_core_mm_runtimes(repo_root: Optional[Path] = None) -> pd.DataFrame:
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    db_paths: List[Path] = []
    default_db = root / "runtime.db"
    if default_db.exists():
        db_paths.append(default_db)
    for pattern in (
        "tmp/core_mm_runs/*/runtime.db",
        "tmp/desktop_run_archive/*/core_mm_runs/*/runtime.db",
    ):
        db_paths.extend(root.glob(pattern))

    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []
    for db_path in db_paths:
        resolved = db_path.resolve().as_posix()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not db_path.exists():
            continue
        runtime_root = db_path.parent
        status = get_run_status(runtime_root=runtime_root, db_path=db_path)
        summary = get_run_summary(runtime_root=runtime_root, db_path=db_path)
        snapshot = get_runtime_status_snapshot(runtime_root=runtime_root, db_path=db_path)
        status_path = runtime_root / "meta" / "status.json"
        summary_path = runtime_root / "meta" / "run_summary.json"
        archived = "desktop_run_archive" in runtime_root.parts
        latest_mtime = max(
            db_path.stat().st_mtime if db_path.exists() else 0.0,
            status_path.stat().st_mtime if status_path.exists() else 0.0,
            summary_path.stat().st_mtime if summary_path.exists() else 0.0,
        )
        updated_at_ms = snapshot.get("updated_at_ms")
        age_s = None
        if updated_at_ms is not None:
            try:
                age_s = max(0.0, datetime.now(timezone.utc).timestamp() - (float(updated_at_ms) / 1000.0))
            except (TypeError, ValueError):
                age_s = None
        if age_s is None:
            age_s = max(0.0, datetime.now(timezone.utc).timestamp() - latest_mtime)
        stage = str(snapshot.get("stage") or ("archived" if archived else "unknown"))
        mode = str(snapshot.get("mode") or "N/A").upper()
        run_name = str(snapshot.get("strategy_name") or runtime_root.name)
        market = str(snapshot.get("market") or "")
        active_hint = (not archived) and stage == "running" and age_s <= 15 * 60
        fills = int(snapshot.get("fills") or summary.get("fills") or 0)
        pnl_val = float(snapshot.get("total_pnl") or summary.get("realized_net_pnl") or 0.0)
        decision_count = int(snapshot.get("decisions") or summary.get("decisions") or 0)
        selection_reason = snapshot.get("selected_reason") or ""
        rows.append(
            {
                "label": f"{'● ' if active_hint else '  '}{run_name} [{mode} {stage} {_format_age_s(age_s)}]",
                "runtime_root": runtime_root.as_posix(),
                "db_path": db_path.resolve().as_posix(),
                "status_path": status_path.resolve().as_posix(),
                "summary_path": summary_path.resolve().as_posix(),
                "archived": archived,
                "active_hint": active_hint,
                "is_repo_default": db_path.resolve() == default_db.resolve(),
                "latest_mtime": latest_mtime,
                "age_s": age_s,
                "mode": mode,
                "stage": stage,
                "exchange": snapshot.get("exchange") or _infer_exchange_from_market_slug(market),
                "market": market,
                "market_label": _market_label_from_slug(str(market)),
                "strategy_name": run_name,
                "symbols": snapshot.get("symbols") or [],
                "decisions": decision_count,
                "fills": fills,
                "order_actions": int(snapshot.get("order_actions") or 0),
                "realized_net_pnl": float(snapshot.get("realized_net_pnl") or summary.get("realized_net_pnl") or 0.0),
                "unrealized_pnl": float(snapshot.get("unrealized_pnl") or summary.get("unrealized_pnl") or 0.0),
                "total_pnl": pnl_val,
                "selection_reason": selection_reason,
                "book_health": snapshot.get("book_health") or "unknown",
                "quoteable": bool(snapshot.get("quoteable")),
                "spread_bps": snapshot.get("spread_bps"),
                "selection": snapshot.get("selection") or {},
                "active_market_health": snapshot.get("active_market_health") or {},
                "status": status,
                "summary": summary,
                "snapshot": snapshot,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "label",
                "runtime_root",
                "db_path",
                "status_path",
                "summary_path",
                "archived",
                "active_hint",
                "is_repo_default",
                "latest_mtime",
                "age_s",
                "mode",
                "stage",
                "exchange",
                "market",
                "market_label",
                "strategy_name",
                "symbols",
                "decisions",
                "fills",
                "order_actions",
                "realized_net_pnl",
                "unrealized_pnl",
                "total_pnl",
                "selection_reason",
                "book_health",
                "quoteable",
                "spread_bps",
                "selection",
                "active_market_health",
                "status",
                "summary",
                "snapshot",
            ]
        )
    out = pd.DataFrame(rows)
    out = out.sort_values(
        by=["active_hint", "is_repo_default", "latest_mtime"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    return out


def get_portfolio_curve_from_runtimes(runtimes: Optional[pd.DataFrame] = None, repo_root: Optional[Path] = None) -> pd.DataFrame:
    runtime_df = runtimes if runtimes is not None else discover_core_mm_runtimes(repo_root=repo_root)
    if runtime_df.empty or "db_path" not in runtime_df.columns:
        return pd.DataFrame()

    curves: List[pd.DataFrame] = []
    for _, row in runtime_df.iterrows():
        db_path = str(row.get("db_path") or "")
        if not db_path:
            continue
        curve = get_paper_pnl_curve(db_path=Path(db_path))
        if curve.empty:
            continue
        curve = curve.copy()
        curve["source"] = str(row.get("strategy_name") or Path(db_path).parent.name)
        curves.append(curve)
    if not curves:
        return pd.DataFrame()

    combined = pd.concat(curves, ignore_index=True, sort=False)
    agg_map: Dict[str, str] = {
        "realized_gross_pnl": "sum",
        "realized_net_pnl": "sum",
        "unrealized_pnl": "sum",
        "cumulative_fees": "sum",
        "turnover": "sum",
        "win_count": "sum",
        "loss_count": "sum",
    }
    grouped = combined.groupby("ts_ms", as_index=False).agg(agg_map)
    grouped = grouped.sort_values("ts_ms").reset_index(drop=True)
    grouped["total_pnl"] = grouped["realized_net_pnl"] + grouped["unrealized_pnl"]
    peaks: List[float] = []
    running_peak: Optional[float] = None
    for value in grouped["total_pnl"].tolist():
        current = float(value)
        running_peak = current if running_peak is None else max(running_peak, current)
        peaks.append(float(running_peak))
    grouped["equity_peak"] = peaks
    grouped["drawdown_abs"] = (grouped["equity_peak"] - grouped["total_pnl"]).clip(lower=0.0)
    grouped["drawdown_pct"] = grouped.apply(
        lambda row: (float(row["drawdown_abs"]) / float(row["equity_peak"])) if float(row["equity_peak"]) > 0 else None,
        axis=1,
    )
    return grouped


def get_strategy_market_summary(db_path: Optional[Path] = None) -> pd.DataFrame:
    if not table_exists("decisions", db_path=db_path):
        return pd.DataFrame()

    market_hist = get_market_history_summary(db_path=db_path)
    if market_hist.empty:
        market_hist = pd.DataFrame(columns=["market_slug"])
    elif "decisions" not in market_hist.columns and "total_decisions" in market_hist.columns:
        market_hist = market_hist.rename(columns={"total_decisions": "decisions"})

    latest_decisions = query_df(
        """
        WITH ranked AS (
          SELECT
            market AS market_slug,
            token_id,
            action,
            reason_codes,
            p_hat,
            expected_edge,
            expected_cost,
            ts_ms,
            ROW_NUMBER() OVER (
              PARTITION BY market
              ORDER BY ts_ms DESC, COALESCE(decision_id, '') DESC
            ) AS rn
          FROM decisions
          WHERE market IS NOT NULL
        )
        SELECT market_slug, token_id, action, reason_codes, p_hat, expected_edge, expected_cost, ts_ms AS latest_decision_ts_ms
        FROM ranked
        WHERE rn = 1
        """,
        db_path=db_path,
    )

    latest_ts = 0
    if not market_hist.empty and "last_ts_ms" in market_hist.columns:
        latest_ts = int(pd.to_numeric(market_hist["last_ts_ms"], errors="coerce").fillna(0).max() or 0)
    if latest_decisions is not None and not latest_decisions.empty and "latest_decision_ts_ms" in latest_decisions.columns:
        latest_ts = max(latest_ts, int(pd.to_numeric(latest_decisions["latest_decision_ts_ms"], errors="coerce").fillna(0).max() or 0))
    if latest_ts <= 0:
        latest_ts = _now_ms()

    open_orders = get_open_orders_latest(as_of_ts_ms=latest_ts, db_path=db_path)
    if not open_orders.empty and "market_slug" in open_orders.columns:
        order_counts = open_orders.groupby("market_slug", as_index=False).agg(active_orders=("order_id", "count"))
    else:
        order_counts = pd.DataFrame(columns=["market_slug", "active_orders"])

    quotes = get_active_quote_summary(as_of_ts_ms=latest_ts, db_path=db_path)
    if not quotes.empty and "market_slug" in quotes.columns:
        quote_summary = quotes.groupby("market_slug", as_index=False).agg(
            current_spread_bps=("offered_spread_bps", "median"),
            quote_state=("quote_state", "first"),
            quote_rows=("token_id", "count"),
        )
    else:
        quote_summary = pd.DataFrame(columns=["market_slug", "current_spread_bps", "quote_state", "quote_rows"])

    result = market_hist.copy()
    if not latest_decisions.empty:
        result = result.merge(latest_decisions, on="market_slug", how="left")
    if not order_counts.empty:
        result = result.merge(order_counts, on="market_slug", how="left")
    if not quote_summary.empty:
        result = result.merge(quote_summary, on="market_slug", how="left")

    if result.empty:
        return result

    if "active_orders" not in result.columns:
        result["active_orders"] = 0
    result["active_orders"] = pd.to_numeric(result["active_orders"], errors="coerce").fillna(0).astype(int)
    if "current_spread_bps" not in result.columns:
        result["current_spread_bps"] = pd.Series(dtype=float)
    if "quote_state" not in result.columns:
        result["quote_state"] = "absent"
    result["symbol"] = result["market_slug"].map(_friendly_symbol_from_market_slug)
    if "realized_net_pnl" not in result.columns:
        result["realized_net_pnl"] = 0.0
    if "unrealized_pnl" not in result.columns:
        result["unrealized_pnl"] = 0.0
    result["total_pnl"] = pd.to_numeric(result["realized_net_pnl"], errors="coerce").fillna(0.0) + pd.to_numeric(result["unrealized_pnl"], errors="coerce").fillna(0.0)
    if "latest_decision_ts_ms" not in result.columns:
        result["latest_decision_ts_ms"] = pd.Series(dtype="float64")
    if "last_ts_ms" in result.columns:
        result["last_update_ms"] = pd.to_numeric(result["last_ts_ms"], errors="coerce").fillna(0).astype(int)
    else:
        result["last_update_ms"] = 0
    if "latest_decision_ts_ms" in result.columns:
        result["last_update_ms"] = pd.concat(
            [
                pd.to_numeric(result["last_update_ms"], errors="coerce").fillna(0),
                pd.to_numeric(result["latest_decision_ts_ms"], errors="coerce").fillna(0),
            ],
            axis=1,
        ).max(axis=1)
    result["last_update_ms"] = result["last_update_ms"].astype(int)
    result["state"] = result.get("action", pd.Series(dtype=object)).fillna("UNKNOWN").map(
        lambda value: {
            "QUOTE": "quoting",
            "SKIP": "waiting",
            "FREEZE": "frozen",
        }.get(str(value).upper(), str(value).lower())
    )
    result["state"] = result["state"].fillna("unknown")
    if "reason_codes" not in result.columns:
        result["reason_codes"] = ""
    result["profitability_usd"] = pd.to_numeric(result["total_pnl"], errors="coerce").fillna(0.0)
    if "decisions" not in result.columns:
        result["decisions"] = 0
    result = result.sort_values(["last_update_ms", "decisions"], ascending=[False, False]).reset_index(drop=True)
    return result[
        [
            col
            for col in [
                "market_slug",
                "symbol",
                "market",
                "decisions",
                "quotes",
                "skips",
                "freezes",
                "active_orders",
                "fills",
                "realized_net_pnl",
                "unrealized_pnl",
                "total_pnl",
                "profitability_usd",
                "current_spread_bps",
                "quote_state",
                "state",
                "latest_decision_ts_ms",
                "last_update_ms",
                "action",
                "reason_codes",
                "p_hat",
                "expected_edge",
                "expected_cost",
            ]
            if col in result.columns
        ]
    ]


def get_strategy_operation_rows(db_path: Optional[Path] = None, limit: int = 60) -> pd.DataFrame:
    if not table_exists("decisions", db_path=db_path):
        return pd.DataFrame()

    capped_limit = max(1, int(limit))
    latest_ts = query_df("SELECT MAX(ts_ms) AS max_ts FROM decisions", db_path=db_path)
    as_of_ts_ms = int(_int_or_none(safe_first(latest_ts, "max_ts", 0)) or 0)
    if as_of_ts_ms <= 0:
        as_of_ts_ms = _now_ms()

    decisions = query_df(
        f"""
        SELECT
          d.*,
          'decision' AS row_type
        FROM decisions d
        ORDER BY ts_ms DESC, COALESCE(decision_id, '') DESC
        LIMIT {capped_limit}
        """,
        db_path=db_path,
    )
    orders = get_open_orders_latest(as_of_ts_ms=as_of_ts_ms, db_path=db_path)
    if not orders.empty:
        orders = orders.head(capped_limit).copy()
        orders.insert(0, "row_type", "open_order")
        if "market_slug" not in orders.columns:
            orders["market_slug"] = None
        for col in ["reason_codes", "p_hat", "expected_edge", "expected_cost"]:
            if col not in orders.columns:
                orders[col] = None
        if "action" not in orders.columns:
            orders["action"] = orders["side"]
        orders = orders[
            [
                col
                for col in [
                    "ts_ms",
                    "row_type",
                    "market_slug",
                    "token_id",
                    "action",
                    "reason_codes",
                    "p_hat",
                    "expected_edge",
                    "expected_cost",
                    "price",
                    "size",
                    "status",
                ]
                if col in orders.columns
            ]
        ]
    combined = pd.concat([decisions, orders], ignore_index=True, sort=False) if not orders.empty else decisions
    if combined.empty:
        return combined
    if "market_slug" not in combined.columns and "market" in combined.columns:
        combined["market_slug"] = combined["market"]
    combined["market_label"] = combined["market_slug"].apply(lambda value: _friendly_symbol_from_market_slug(value) or _market_label_from_slug(value))
    combined["why"] = combined.apply(
        lambda row: str(row.get("reason_codes") or "").strip() or (
            "resting order" if str(row.get("row_type")) == "open_order" else "no blocker"
        ),
        axis=1,
    )
    combined["ts"] = pd.to_datetime(combined["ts_ms"], unit="ms", utc=True, errors="coerce")
    return combined.sort_values("ts_ms", ascending=False).head(capped_limit).reset_index(drop=True)


def get_decision_explainer_rows(db_path: Optional[Path] = None, limit: int = 20) -> pd.DataFrame:
    if not table_exists("decisions", db_path=db_path):
        return pd.DataFrame()
    df = query_df(
        f"""
        SELECT *
        FROM decisions
        ORDER BY ts_ms DESC, COALESCE(decision_id, '') DESC
        LIMIT {max(1, int(limit))}
        """,
        db_path=db_path,
    )
    if df.empty:
        return df
    out = adapt_decisions(df)
    for column in [
        "control_state",
        "hedge_action",
        "hedge_cluster_id",
        "hedge_action_reason",
        "hedge_market_id",
        "hedge_target_token_id",
        "hedge_target_side",
        "hedge_preferred_side",
        "hedge_ratio",
        "hedge_quality_score",
        "hedge_success_window_ms",
        "hedge_failed_cooldown_until_ms",
    ]:
        if column not in out.columns:
            out[column] = None
    if "policy_json" in out.columns:
        for idx, row in out.iterrows():
            policy = safe_json(row.get("policy_json"))
            hedge_context = _hedge_context_from_mapping(policy.get("hedge_context"))
            if not hedge_context:
                desired_quotes = list(policy.get("desired_quotes") or [])
                if desired_quotes:
                    hedge_context = _hedge_context_from_mapping(desired_quotes[0].get("metadata") or {})
            for key, value in hedge_context.items():
                if key in out.columns and (row.get(key) in (None, "") or pd.isna(row.get(key))):
                    out.at[idx, key] = value
    out["market_label"] = out["market"].apply(lambda value: _market_label_from_slug(str(value)))
    out["symbol"] = out["market"].apply(lambda value: _friendly_symbol_from_market_slug(value))
    out["decision_summary"] = out.apply(
        lambda row: (
            "Quote"
            if str(row.get("action") or "").upper() == "QUOTE"
            else ("Skip" if str(row.get("action") or "").upper() == "SKIP" else ("Freeze" if str(row.get("action") or "").upper() == "FREEZE" else str(row.get("action") or "").title()))
        ),
        axis=1,
    )
    out["plain_english"] = out.apply(
        lambda row: (
            "Trading now"
            if str(row.get("gate_result") or "").upper() == "ALLOW"
            else f"Waiting: {humanize_reason_codes(row.get('reason_codes'))}"
        ),
        axis=1,
    )
    out["ts"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True, errors="coerce")
    return out.sort_values("ts_ms", ascending=False).reset_index(drop=True)


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
    for key in (
        "safe_risk_profile",
        "strategy_allocated_equity",
        "use_allocated_equity_for_risk",
        "risk_based_share_sizing",
        "trade_size",
        "max_size",
        "min_order_size",
        "within_pct",
        "fee_bps",
        "fee_mode",
        "min_size",
        "fallback_size",
        "cycle_secs",
        "refresh_market_secs",
        "quote_spread_multiplier",
        "market_dwell_secs",
        "hard_position_cap",
        "stale_duration_scale",
        "maker_exit_grace_secs",
        "cross_escalation_drawdown_pct",
        "pre_kill_warning_fraction",
    ):
        value = status_config.get(key, state_config.get(key))
        if key == "fee_mode" or key == "safe_risk_profile":
            merged[key] = str(value) if value is not None else None
        elif key in {"use_allocated_equity_for_risk", "risk_based_share_sizing"}:
            merged[key] = bool(value) if value is not None else None
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
        SELECT *
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
    market_slugs: List[Optional[str]] = []
    event_ids: List[Optional[str]] = []
    control_states: List[Optional[str]] = []
    hedge_actions: List[Optional[str]] = []
    hedge_cluster_ids: List[Optional[str]] = []
    hedge_action_reasons: List[Optional[str]] = []
    hedge_market_ids: List[Optional[str]] = []
    hedge_target_token_ids: List[Optional[str]] = []
    hedge_target_sides: List[Optional[str]] = []
    hedge_preferred_sides: List[Optional[str]] = []
    hedge_ratios: List[Optional[float]] = []
    hedge_quality_scores: List[Optional[float]] = []
    hedge_success_windows: List[Optional[int]] = []
    hedge_failed_cooldown_until_ms: List[Optional[int]] = []
    liquidity_modes: List[Optional[str]] = []
    fill_triggers: List[Optional[str]] = []
    quote_modes: List[Optional[str]] = []
    risk_actions: List[Optional[str]] = []
    risk_states: List[Optional[str]] = []
    stale_states: List[Optional[str]] = []
    exit_modes: List[Optional[str]] = []
    exit_escalations: List[Optional[str]] = []
    buy_reentry_blocked: List[Optional[bool]] = []
    current_equities: List[Optional[float]] = []
    market_exposures: List[Optional[float]] = []
    event_exposures: List[Optional[float]] = []
    time_to_expiry: List[Optional[int]] = []
    for _, row in df.iterrows():
        payload = safe_json(row.get("payload_json"))
        placement = safe_json(payload.get("placement_metadata"))
        realized_deltas.append(_float_or_none(payload.get("realized_net_pnl_delta")))
        fee_usdc.append(_float_or_none(payload.get("fee_usdc")))
        market_slugs.append(payload.get("market_slug"))
        event_ids.append(placement.get("event_id"))
        control_states.append(row.get("control_state") or payload.get("control_state") or placement.get("control_state"))
        hedge_actions.append(row.get("hedge_action") or payload.get("hedge_action") or placement.get("hedge_action"))
        hedge_cluster_ids.append(row.get("hedge_cluster_id") or payload.get("hedge_cluster_id") or placement.get("hedge_cluster_id"))
        hedge_action_reasons.append(row.get("hedge_action_reason") or payload.get("hedge_action_reason") or placement.get("hedge_action_reason"))
        hedge_market_ids.append(row.get("hedge_market_id") or payload.get("hedge_market_id") or placement.get("hedge_market_id"))
        hedge_target_token_ids.append(row.get("hedge_target_token_id") or payload.get("hedge_target_token_id") or placement.get("hedge_target_token_id"))
        hedge_target_sides.append(row.get("hedge_target_side") or payload.get("hedge_target_side") or placement.get("hedge_target_side"))
        hedge_preferred_sides.append(row.get("hedge_preferred_side") or payload.get("hedge_preferred_side") or placement.get("hedge_preferred_side"))
        hedge_ratios.append(_float_or_none(row.get("hedge_ratio") or payload.get("hedge_ratio") or placement.get("hedge_ratio")))
        hedge_quality_scores.append(_float_or_none(row.get("hedge_quality_score") or payload.get("hedge_quality_score") or placement.get("hedge_quality_score")))
        hedge_success_windows.append(_int_or_none(row.get("hedge_success_window_ms") or payload.get("hedge_success_window_ms") or placement.get("hedge_success_window_ms")))
        hedge_failed_cooldown_until_ms.append(_int_or_none(row.get("hedge_failed_cooldown_until_ms") or payload.get("hedge_failed_cooldown_until_ms") or placement.get("hedge_failed_cooldown_until_ms")))
        liquidity_modes.append(payload.get("liquidity_mode"))
        fill_triggers.append(payload.get("fill_trigger"))
        quote_modes.append(placement.get("quote_mode"))
        risk_actions.append(placement.get("risk_action"))
        risk_states.append(placement.get("risk_state"))
        stale_states.append(placement.get("stale_state"))
        exit_modes.append(placement.get("exit_mode"))
        exit_escalations.append(placement.get("exit_escalation_reason"))
        buy_reentry_blocked.append(placement.get("buy_reentry_blocked"))
        current_equities.append(_float_or_none(placement.get("current_equity")))
        market_exposures.append(_float_or_none(placement.get("market_exposure_notional")))
        event_exposures.append(_float_or_none(placement.get("event_exposure_notional")))
        time_to_expiry.append(_int_or_none(placement.get("time_to_expiry_ms")))
    df["realized_net_pnl_delta"] = realized_deltas
    df["fee_usdc"] = fee_usdc
    df["market_slug"] = market_slugs
    df["event_id"] = event_ids
    df["control_state"] = control_states
    df["hedge_action"] = hedge_actions
    df["hedge_cluster_id"] = hedge_cluster_ids
    df["hedge_action_reason"] = hedge_action_reasons
    df["hedge_market_id"] = hedge_market_ids
    df["hedge_target_token_id"] = hedge_target_token_ids
    df["hedge_target_side"] = hedge_target_sides
    df["hedge_preferred_side"] = hedge_preferred_sides
    df["hedge_ratio"] = hedge_ratios
    df["hedge_quality_score"] = hedge_quality_scores
    df["hedge_success_window_ms"] = hedge_success_windows
    df["hedge_failed_cooldown_until_ms"] = hedge_failed_cooldown_until_ms
    df["liquidity_mode"] = liquidity_modes
    df["fill_trigger"] = fill_triggers
    df["quote_mode"] = quote_modes
    df["risk_action"] = risk_actions
    df["risk_state"] = risk_states
    df["stale_state"] = stale_states
    df["exit_mode"] = exit_modes
    df["exit_escalation_reason"] = exit_escalations
    df["buy_reentry_blocked"] = buy_reentry_blocked
    df["current_equity"] = current_equities
    df["market_exposure_notional"] = market_exposures
    df["event_exposure_notional"] = event_exposures
    df["time_to_expiry_ms"] = time_to_expiry
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df


def get_fill_risk_timeline(limit: int = 100, db_path: Optional[Path] = None) -> pd.DataFrame:
    events: List[Dict[str, Any]] = []
    capped_limit = max(10, int(limit))

    if table_exists("fills", db_path=db_path):
        fills = query_df(
            f"""
            SELECT *
            FROM fills
            ORDER BY ts_ms DESC
            LIMIT {capped_limit}
            """,
            db_path=db_path,
        )
        for _, row in fills.iterrows():
            payload = safe_json(row.get("payload_json"))
            placement = safe_json(payload.get("placement_metadata"))
            hedge_context = _hedge_context_from_mapping({
                **placement,
                **payload,
                **row.to_dict(),
            })
            side = str(row.get("side") or "").upper()
            qty = _float_or_none(row.get("fill_qty"))
            px = _float_or_none(row.get("fill_price"))
            summary = f"FILL {side} {qty or 0:.0f} @ {px or 0:.3f}"
            risk_action = str(placement.get("risk_action") or "NORMAL")
            if risk_action != "NORMAL":
                summary = f"{summary} · {risk_action}"
            control_state = str(hedge_context.get("control_state") or "").strip()
            hedge_action = str(hedge_context.get("hedge_action") or "").strip()
            hedge_reason = str(hedge_context.get("hedge_action_reason") or "").strip()
            target_bits = [
                str(hedge_context.get("hedge_market_id") or "").strip(),
                str(hedge_context.get("hedge_target_token_id") or "").strip(),
                str(hedge_context.get("hedge_target_side") or "").strip(),
            ]
            if control_state:
                summary = f"{summary} · {control_state}"
            if hedge_action and hedge_action != "NONE":
                summary = f"{summary} · {hedge_action}"
            if hedge_reason:
                summary = f"{summary} · {hedge_reason}"
            if any(target_bits):
                summary = f"{summary} · {'/'.join(bit for bit in target_bits if bit)}"
            events.append(
                {
                    "ts_ms": _int_or_none(row.get("ts_ms")),
                    "event_kind": "fill",
                    "market_slug": payload.get("market_slug"),
                    "token_id": row.get("token_id"),
                    "summary": summary,
                    "side": row.get("side"),
                    "control_state": control_state or None,
                    "hedge_action": hedge_action or None,
                    "hedge_action_reason": hedge_reason or None,
                    "hedge_cluster_id": hedge_context.get("hedge_cluster_id") or None,
                    "hedge_market_id": hedge_context.get("hedge_market_id") or None,
                    "hedge_target_token_id": hedge_context.get("hedge_target_token_id") or None,
                    "hedge_target_side": hedge_context.get("hedge_target_side") or None,
                    "hedge_preferred_side": hedge_context.get("hedge_preferred_side") or None,
                    "hedge_ratio": _float_or_none(hedge_context.get("hedge_ratio")),
                    "hedge_quality_score": _float_or_none(hedge_context.get("hedge_quality_score")),
                    "hedge_success_window_ms": _int_or_none(hedge_context.get("hedge_success_window_ms")),
                    "hedge_failed_cooldown_until_ms": _int_or_none(hedge_context.get("hedge_failed_cooldown_until_ms")),
                    "risk_action": placement.get("risk_action"),
                    "risk_state": placement.get("risk_state"),
                    "stale_state": placement.get("stale_state"),
                    "exit_mode": placement.get("exit_mode"),
                    "exit_escalation_reason": placement.get("exit_escalation_reason"),
                }
            )

    if table_exists("decisions", db_path=db_path):
        decisions = query_df(
            f"""
            SELECT *
            FROM decisions
            ORDER BY ts_ms DESC
            LIMIT {capped_limit * 2}
            """,
            db_path=db_path,
        )
        for _, row in decisions.iterrows():
            policy = safe_json(row.get("policy_json"))
            risk = safe_json(policy.get("risk_decision"))
            if not risk:
                continue
            hedge_context = _hedge_context_from_mapping({
                **safe_json(policy.get("hedge_context")),
                **safe_json((policy.get("desired_quotes") or [{}])[0].get("metadata") if isinstance(policy.get("desired_quotes"), list) and policy.get("desired_quotes") else {}),
                **row.to_dict(),
            })
            action = str(risk.get("action") or row.get("action") or "NORMAL")
            risk_state = str(risk.get("risk_state") or "normal")
            stale_state = str(risk.get("stale_state") or "flat")
            exit_reason = risk.get("exit_escalation_reason")
            stop_open = bool(risk.get("stop_open_triggered"))
            force_flat = bool(risk.get("force_flat_triggered"))
            if action == "NORMAL" and risk_state == "normal" and stale_state != "stale" and not exit_reason and not stop_open and not force_flat:
                continue
            summary_bits = [action]
            control_state = str(hedge_context.get("control_state") or row.get("control_state") or "NORMAL").strip()
            hedge_action = str(hedge_context.get("hedge_action") or row.get("hedge_action") or "NONE").strip()
            hedge_reason = str(hedge_context.get("hedge_action_reason") or row.get("hedge_action_reason") or "").strip()
            if risk_state not in {"", "normal"}:
                summary_bits.append(risk_state)
            if control_state and control_state != "NORMAL":
                summary_bits.append(control_state)
            if hedge_action and hedge_action != "NONE":
                summary_bits.append(hedge_action)
            if stale_state == "stale":
                summary_bits.append("stale")
            if stop_open:
                summary_bits.append("stop-open")
            if force_flat:
                summary_bits.append("force-flat")
            if hedge_reason:
                summary_bits.append(hedge_reason)
            hedge_target_bits = [
                str(hedge_context.get("hedge_market_id") or row.get("hedge_market_id") or "").strip(),
                str(hedge_context.get("hedge_target_token_id") or row.get("hedge_target_token_id") or "").strip(),
                str(hedge_context.get("hedge_target_side") or row.get("hedge_target_side") or "").strip(),
            ]
            if any(hedge_target_bits):
                summary_bits.append("/".join(bit for bit in hedge_target_bits if bit))
            if exit_reason:
                summary_bits.append(str(exit_reason))
            events.append(
                {
                    "ts_ms": _int_or_none(row.get("ts_ms")),
                    "event_kind": "risk",
                    "market_slug": row.get("market"),
                    "token_id": row.get("token_id"),
                    "summary": " · ".join(summary_bits),
                    "side": None,
                    "control_state": control_state or None,
                    "hedge_action": hedge_action or None,
                    "hedge_action_reason": hedge_reason or None,
                    "hedge_cluster_id": hedge_context.get("hedge_cluster_id") or row.get("hedge_cluster_id") or None,
                    "hedge_market_id": hedge_context.get("hedge_market_id") or row.get("hedge_market_id") or None,
                    "hedge_target_token_id": hedge_context.get("hedge_target_token_id") or row.get("hedge_target_token_id") or None,
                    "hedge_target_side": hedge_context.get("hedge_target_side") or row.get("hedge_target_side") or None,
                    "hedge_preferred_side": hedge_context.get("hedge_preferred_side") or row.get("hedge_preferred_side") or None,
                    "hedge_ratio": _float_or_none(hedge_context.get("hedge_ratio") or row.get("hedge_ratio")),
                    "hedge_quality_score": _float_or_none(hedge_context.get("hedge_quality_score") or row.get("hedge_quality_score")),
                    "hedge_success_window_ms": _int_or_none(hedge_context.get("hedge_success_window_ms") or row.get("hedge_success_window_ms")),
                    "hedge_failed_cooldown_until_ms": _int_or_none(hedge_context.get("hedge_failed_cooldown_until_ms") or row.get("hedge_failed_cooldown_until_ms")),
                    "risk_action": action,
                    "risk_state": risk_state,
                    "stale_state": stale_state,
                    "exit_mode": risk.get("exit_mode"),
                    "exit_escalation_reason": exit_reason,
                }
            )

    if table_exists("system_state", db_path=db_path):
        states = query_df(
            f"""
            SELECT as_of_ts, payload_json
            FROM system_state
            ORDER BY as_of_ts ASC
            LIMIT {capped_limit * 4}
            """,
            db_path=db_path,
        )
        previous_market: Optional[str] = None
        for _, row in states.iterrows():
            payload = safe_json(row.get("payload_json"))
            runner = safe_json(payload.get("runner"))
            market_id = runner.get("market_id")
            if market_id in (None, ""):
                continue
            market_text = str(market_id)
            if previous_market is not None and market_text != previous_market:
                events.append(
                    {
                        "ts_ms": _int_or_none(row.get("as_of_ts")),
                        "event_kind": "market_switch",
                        "market_slug": market_text,
                        "token_id": None,
                        "summary": f"MARKET SWITCH {previous_market} -> {market_text}",
                        "side": None,
                        "risk_action": None,
                        "risk_state": None,
                        "stale_state": None,
                        "exit_mode": None,
                        "exit_escalation_reason": None,
                    }
                )
            previous_market = market_text

    if not events:
        return pd.DataFrame()
    df = pd.DataFrame(events)
    df = df.dropna(subset=["ts_ms"]).sort_values("ts_ms", ascending=False).head(capped_limit).reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df


def get_latest_system_payload(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the latest system_state payload_json as a dict."""
    if not table_exists("system_state", db_path=db_path):
        return {}
    df = query_df("SELECT payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1", db_path=db_path)
    return safe_json(safe_first(df, "payload_json", "{}"))


def queue_control_command(
    *,
    command_type: str,
    payload: Optional[Dict[str, Any]] = None,
    scope: str = "global",
    requested_by: str = "dashboard",
    expires_in_ms: int = 120_000,
    db_path: Optional[Path] = None,
) -> str:
    path = db_path or resolve_db_path()
    runtime_root = runtime_root_for_db(path)
    status = get_run_status(runtime_root=runtime_root, db_path=path)
    run_id = str(status.get("run_id") or runtime_root.name)
    store = ControlCommandStore(path)
    return store.submit_command(
        run_id=run_id,
        runtime_root=runtime_root.as_posix(),
        scope=scope,
        command_type=str(command_type or ""),
        payload=dict(payload or {}),
        requested_by=str(requested_by or "dashboard"),
        expires_in_ms=int(expires_in_ms),
    )


def get_recent_control_commands(db_path: Optional[Path] = None, limit: int = 20) -> pd.DataFrame:
    if not table_exists("control_commands", db_path=db_path):
        return pd.DataFrame()
    return query_df(
        """
        SELECT command_id, run_id, runtime_root, scope, command_type,
               requested_by, requested_at_ms, status, expires_at_ms,
               payload_json, result_json
        FROM control_commands
        ORDER BY requested_at_ms DESC
        LIMIT ?
        """,
        (int(limit),),
        db_path=db_path,
    )


def get_recent_control_events(db_path: Optional[Path] = None, limit: int = 40) -> pd.DataFrame:
    if not table_exists("control_events", db_path=db_path):
        return pd.DataFrame()
    return query_df(
        """
        SELECT event_id, command_id, ts_ms, event_type, status, payload_json
        FROM control_events
        ORDER BY ts_ms DESC, event_id DESC
        LIMIT ?
        """,
        (int(limit),),
        db_path=db_path,
    )


def get_control_plane_snapshot(db_path: Optional[Path] = None) -> Dict[str, Any]:
    runtime_snapshot = get_runtime_status_snapshot(db_path=db_path)
    payload = runtime_snapshot.get("payload_json") if isinstance(runtime_snapshot.get("payload_json"), dict) else {}
    control_state = safe_json(payload.get("control_state"))
    if not control_state:
        control_state = safe_json((payload.get("runner") or {}).get("control_state"))
    status_control = runtime_snapshot.get("status", {}).get("control_state") if isinstance(runtime_snapshot.get("status"), dict) else {}
    if isinstance(status_control, dict):
        control_state = {**status_control, **control_state}
    commands = get_recent_control_commands(db_path=db_path, limit=20)
    pending = commands[commands["status"].astype(str) == "pending"] if not commands.empty and "status" in commands.columns else pd.DataFrame()
    last_applied = (
        commands[commands["status"].astype(str) == "applied"].iloc[0].to_dict()
        if not commands.empty and "status" in commands.columns and any(commands["status"].astype(str) == "applied")
        else {}
    )
    return {
        "trading_enabled": bool(control_state.get("trading_enabled", True)),
        "kill_switch_enabled": bool(control_state.get("kill_switch_enabled", False)),
        "flatten_only_mode": bool(control_state.get("flatten_only_mode", False)),
        "halt_after_flatten": bool(control_state.get("halt_after_flatten", False)),
        "risk_warning_triggered": bool(control_state.get("risk_warning_triggered", False)),
        "cycle_secs": _float_or_none(control_state.get("cycle_secs")),
        "refresh_market_secs": _float_or_none(control_state.get("refresh_market_secs")),
        "quote_spread_multiplier": _float_or_none(control_state.get("quote_spread_multiplier")),
        "strategy_allocated_equity": _float_or_none(control_state.get("strategy_allocated_equity")),
        "use_allocated_equity_for_risk": bool(control_state.get("use_allocated_equity_for_risk")) if control_state.get("use_allocated_equity_for_risk") is not None else None,
        "risk_based_share_sizing": bool(control_state.get("risk_based_share_sizing")) if control_state.get("risk_based_share_sizing") is not None else None,
        "safe_risk_profile": control_state.get("safe_risk_profile"),
        "forced_flat_events": list(control_state.get("forced_flat_events") or []),
        "forced_flat_markets": list(control_state.get("forced_flat_markets") or []),
        "last_control_command": control_state.get("last_control_command") or {},
        "pending_count": int(len(pending)),
        "last_applied": last_applied,
    }


def get_strategy_settings_view(db_path: Optional[Path] = None) -> Dict[str, Any]:
    config = get_runtime_config_snapshot(db_path=db_path)
    control = get_control_plane_snapshot(db_path=db_path)
    commands = get_recent_control_commands(db_path=db_path, limit=30)
    pending_patch: Dict[str, Any] = {}
    last_applied_patch: Dict[str, Any] = {}
    if not commands.empty:
        for _, row in commands.iterrows():
            if str(row.get("command_type") or "") != "apply_config_patch":
                continue
            payload = safe_json(row.get("payload_json"))
            result = safe_json(row.get("result_json"))
            patch = safe_json(payload.get("patch")) if "patch" in payload else payload
            if str(row.get("status") or "") == "pending" and not pending_patch:
                pending_patch = patch
            if str(row.get("status") or "") == "applied" and not last_applied_patch:
                last_applied_patch = safe_json(result.get("applied")) if "applied" in result else patch
    return {
        "current": config,
        "pending_patch": pending_patch,
        "last_applied_patch": last_applied_patch,
        "control": control,
    }


def get_runtime_alert_feed(db_path: Optional[Path] = None) -> pd.DataFrame:
    snapshot = get_runtime_status_snapshot(db_path=db_path)
    control = get_control_plane_snapshot(db_path=db_path)
    rows: List[Dict[str, Any]] = []
    now_ts = _now_ms()
    if not snapshot.get("quoteable"):
        rows.append({"ts_ms": now_ts, "severity": "warn", "owner": "Kant", "alert_type": "quoteability", "summary": "Runtime is not quoteable", "next_action": "Inspect selection and live books"})
    if str(snapshot.get("book_health") or "") not in {"healthy", "unknown"}:
        rows.append({"ts_ms": now_ts, "severity": "warn", "owner": "Kant", "alert_type": "book_health", "summary": f"Book health degraded: {snapshot.get('book_health')}", "next_action": "Review book diagnostics and feed health"})
    if control.get("pending_count", 0) > 0:
        rows.append({"ts_ms": now_ts, "severity": "info", "owner": "Ramanujan", "alert_type": "control_backlog", "summary": f"{control.get('pending_count')} pending control command(s)", "next_action": "Verify runner is acknowledging staged commands"})
    commands = get_recent_control_commands(db_path=db_path, limit=20)
    if not commands.empty and "status" in commands.columns:
        rejected = commands[commands["status"].astype(str) == "rejected"]
        if not rejected.empty:
            latest = rejected.iloc[0]
            rows.append({
                "ts_ms": _int_or_none(latest.get("requested_at_ms")) or now_ts,
                "severity": "critical",
                "owner": "Ramanujan",
                "alert_type": "command_rejected",
                "summary": f"Control command rejected: {latest.get('command_type')}",
                "next_action": "Inspect command validation and dashboard payload",
            })
    events = get_fill_risk_timeline(limit=100, db_path=db_path)
    if not events.empty and "risk_action" in events.columns:
        stale_count = int((events["risk_action"].astype(str) == "STALE_UNWIND").sum())
        if stale_count >= 10:
            rows.append({"ts_ms": now_ts, "severity": "warn", "owner": "Kant", "alert_type": "stale_inventory", "summary": f"High stale unwind activity: {stale_count} recent events", "next_action": "Review stale timer and quote quality"})
    return pd.DataFrame(rows).sort_values("ts_ms", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def get_overnight_supervision_rows(db_path: Optional[Path] = None) -> pd.DataFrame:
    snapshot = get_runtime_status_snapshot(db_path=db_path)
    alerts = get_runtime_alert_feed(db_path=db_path)
    rows: List[Dict[str, Any]] = []
    rows.append(
        {
            "workstream": "Kalshi live-readiness gates",
            "owner": "Kant",
            "status": "active" if bool(snapshot.get("quoteable")) else "needs_attention",
            "evidence": snapshot.get("market") or "No active market",
            "next_task": "Keep paper runtime safe and quoteable",
        }
    )
    rows.append(
        {
            "workstream": "Dashboard operator surface",
            "owner": "Ramanujan",
            "status": "active",
            "evidence": f"{int(len(alerts))} active alerts" if not alerts.empty else "No active dashboard alerts",
            "next_task": "Keep controls, telemetry, and monitoring readable",
        }
    )
    if not alerts.empty:
        for _, row in alerts.head(4).iterrows():
            rows.append(
                {
                    "workstream": str(row.get("alert_type") or "alert"),
                    "owner": str(row.get("owner") or "Ramanujan"),
                    "status": str(row.get("severity") or "info"),
                    "evidence": str(row.get("summary") or ""),
                    "next_task": str(row.get("next_action") or ""),
                }
            )
    return pd.DataFrame(rows)


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
        metadata = _desired_quote_metadata(payload)
        result[tid] = {
            "market": row.get("market"),
            "token_id": row.get("token_id"),
            "action": str(row.get("action") or "?"),
            "reason_codes": str(row.get("reason_codes") or ""),
            "ts_ms": row.get("ts_ms"),
            "expected_edge": _float_or_none(row.get("expected_edge")),
            "p_fair": _float_or_none(metadata.get("p_fair")),
            "fee_type": metadata.get("fee_type"),
            "fee_multiplier": _float_or_none(metadata.get("fee_multiplier")),
            "book_diag": payload.get("book_diag") or {},
            "metrics": payload.get("metrics") or {},
            "flow_filter": payload.get("flow_filter") or {},
            "quote_plan": payload.get("quote_plan") or {},
            "size_plan": payload.get("size_plan") or {},
            "risk_decision": payload.get("risk_decision") or {},
        }
    return result


def _safe_json_list(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _desired_quote_metadata(policy: Dict[str, Any]) -> Dict[str, Any]:
    desired_quotes = _safe_json_list(policy.get("desired_quotes"))
    for quote in desired_quotes:
        if not isinstance(quote, dict):
            continue
        metadata = quote.get("metadata")
        if isinstance(metadata, dict):
            return metadata
    return {}


def get_latest_decision_snapshot(
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if not table_exists("decisions", db_path=db_path):
        return {}
    df = query_df(
        """
        SELECT *
        FROM decisions
        ORDER BY ts_ms DESC, COALESCE(decision_id, '') DESC
        LIMIT 1
        """,
        db_path=db_path,
    )
    if df.empty:
        return {}

    row = df.iloc[0]
    policy = safe_json(row.get("policy_json"))
    metadata = _desired_quote_metadata(policy)
    size_plan = policy.get("size_plan") if isinstance(policy.get("size_plan"), dict) else {}
    risk_decision = policy.get("risk_decision") if isinstance(policy.get("risk_decision"), dict) else {}

    return {
        "market": row.get("market"),
        "token_id": row.get("token_id"),
        "action": row.get("action"),
        "reason_codes": row.get("reason_codes"),
        "expected_edge": _float_or_none(row.get("expected_edge")),
        "expected_cost": _float_or_none(row.get("expected_cost")),
        "p_fair": _float_or_none(metadata.get("p_fair")),
        "fee_type": metadata.get("fee_type"),
        "fee_multiplier": _float_or_none(metadata.get("fee_multiplier")),
        "buy_amount": _float_or_none(size_plan.get("buy_amount")),
        "sell_amount": _float_or_none(size_plan.get("sell_amount")),
        "buy_limiter": size_plan.get("buy_limiter"),
        "sell_limiter": size_plan.get("sell_limiter"),
        "buy_limiters": size_plan.get("buy_limiters"),
        "sell_limiters": size_plan.get("sell_limiters"),
        "risk_action": risk_decision.get("action"),
        "risk_state": risk_decision.get("risk_state"),
        "hedge_action": row.get("hedge_action") or metadata.get("hedge_action"),
        "quote_plan": policy.get("quote_plan") if isinstance(policy.get("quote_plan"), dict) else {},
        "size_plan": size_plan,
        "risk_decision": risk_decision,
        "metadata": metadata,
        "ts_ms": _int_or_none(row.get("ts_ms")),
    }


def get_selection_session_summary(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
    episode_limit: int = 5,
) -> Dict[str, Any]:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    selected_reason = str(snapshot.get("selected_reason") or "")
    if not table_exists("system_state", db_path=db_path):
        return {
            "episode_count": 0,
            "market_change_count": 0,
            "current_episode_started_at_ms": None,
            "previous_market": None,
            "latest_switch_reason": selected_reason or None,
            "top_markets_by_decision_count": [],
            "top_switch_reasons": [],
            "recent_episodes": [],
        }

    states = query_df(
        """
        SELECT as_of_ts, payload_json
        FROM system_state
        ORDER BY as_of_ts ASC
        """,
        db_path=db_path,
    )
    episodes: List[Dict[str, Any]] = []
    previous_market: Optional[str] = None
    for _, row in states.iterrows():
        payload = safe_json(row.get("payload_json"))
        runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
        selection = payload.get("selection")
        if not isinstance(selection, dict):
            selection = runner.get("selection") if isinstance(runner.get("selection"), dict) else {}
        selected_market = selection.get("selected_market") if isinstance(selection.get("selected_market"), dict) else {}
        market_text = (
            selected_market.get("ticker")
            or selected_market.get("slug")
            or runner.get("market_id")
            or ""
        )
        if not market_text:
            continue
        market_text = str(market_text)
        reason = str(
            selection.get("selected_reason")
            or selected_market.get("reason")
            or runner.get("selected_reason")
            or ""
        )
        if market_text != previous_market:
            episodes.append(
                {
                    "ts_ms": _int_or_none(row.get("as_of_ts")),
                    "market": market_text,
                    "reason": reason or None,
                }
            )
            previous_market = market_text

    top_markets_df = query_df(
        """
        SELECT market, COUNT(*) AS decision_count
        FROM decisions
        WHERE market IS NOT NULL AND market != ''
        GROUP BY market
        ORDER BY decision_count DESC, market ASC
        LIMIT 5
        """,
        db_path=db_path,
    )
    top_markets = [
        {
            "market": row.get("market"),
            "decision_count": int(row.get("decision_count") or 0),
        }
        for _, row in top_markets_df.iterrows()
    ]

    switch_reason_counts: Dict[str, int] = {}
    for episode in episodes[1:]:
        reason = str(episode.get("reason") or "unknown")
        switch_reason_counts[reason] = switch_reason_counts.get(reason, 0) + 1
    top_switch_reasons = [
        {"reason": reason, "count": count}
        for reason, count in sorted(
            switch_reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:5]
    ]

    previous_market_value = episodes[-2]["market"] if len(episodes) > 1 else None
    latest_switch_reason = None
    if episodes:
        latest_switch_reason = episodes[-1].get("reason") or None
    if not latest_switch_reason:
        latest_switch_reason = selected_reason or None

    return {
        "episode_count": len(episodes),
        "market_change_count": max(0, len(episodes) - 1),
        "current_episode_started_at_ms": episodes[-1]["ts_ms"] if episodes else None,
        "previous_market": previous_market_value,
        "latest_switch_reason": latest_switch_reason,
        "top_markets_by_decision_count": top_markets,
        "top_switch_reasons": top_switch_reasons,
        "recent_episodes": episodes[-max(1, int(episode_limit)):],
    }


def get_session_performance_summary(
    *,
    runtime_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snapshot = runtime_snapshot if isinstance(runtime_snapshot, dict) else get_runtime_status_snapshot(db_path=db_path)
    pnl = get_paper_pnl_summary(db_path=db_path)
    fill_count = int(query_df("SELECT COUNT(*) AS n FROM fills", db_path=db_path).get("n", pd.Series([0])).iloc[0] if table_exists("fills", db_path=db_path) else 0)
    distinct_orders = int(query_df("SELECT COUNT(DISTINCT order_id) AS n FROM fills", db_path=db_path).get("n", pd.Series([0])).iloc[0] if table_exists("fills", db_path=db_path) else 0)

    control_state_counts = {
        str(row.get("control_state") or "UNKNOWN"): int(row.get("n") or 0)
        for _, row in query_df(
            """
            SELECT control_state, COUNT(*) AS n
            FROM decisions
            GROUP BY control_state
            """,
            db_path=db_path,
        ).iterrows()
    } if table_exists("decisions", db_path=db_path) else {}
    hedge_action_counts = {
        str(row.get("hedge_action") or "UNKNOWN"): int(row.get("n") or 0)
        for _, row in query_df(
            """
            SELECT hedge_action, COUNT(*) AS n
            FROM decisions
            GROUP BY hedge_action
            """,
            db_path=db_path,
        ).iterrows()
    } if table_exists("decisions", db_path=db_path) else {}

    risk_action_counts: Dict[str, int] = {}
    if table_exists("decisions", db_path=db_path):
        decisions = query_df("SELECT policy_json FROM decisions", db_path=db_path)
        for _, row in decisions.iterrows():
            policy = safe_json(row.get("policy_json"))
            risk = policy.get("risk_decision") if isinstance(policy.get("risk_decision"), dict) else {}
            action = str(risk.get("action") or "NONE")
            risk_action_counts[action] = risk_action_counts.get(action, 0) + 1

    latest_fill_fee: Dict[str, Any] = {}
    if table_exists("fills", db_path=db_path):
        fills = query_df(
            """
            SELECT payload_json
            FROM fills
            ORDER BY ts_ms DESC, COALESCE(order_id, '') DESC
            LIMIT 1
            """,
            db_path=db_path,
        )
        if not fills.empty:
            payload = safe_json(fills.iloc[0].get("payload_json"))
            latest_fill_fee = {
                "fee_source": payload.get("fee_source"),
                "fee_type": payload.get("fee_type"),
                "fee_multiplier": _float_or_none(payload.get("fee_multiplier")),
                "realized_net_pnl_delta": _float_or_none(payload.get("realized_net_pnl_delta")),
            }

    return {
        "fill_count": fill_count,
        "distinct_orders": distinct_orders,
        "turnover": pnl.get("turnover"),
        "cumulative_fees": pnl.get("cumulative_fees"),
        "max_drawdown_abs": pnl.get("max_drawdown_abs"),
        "max_drawdown_pct_peak": pnl.get("max_drawdown_pct"),
        "control_state_counts": control_state_counts,
        "risk_action_counts": risk_action_counts,
        "hedge_action_counts": hedge_action_counts,
        "latest_fill_fee": latest_fill_fee,
        "realized_net_pnl": snapshot.get("realized_net_pnl"),
        "unrealized_pnl": snapshot.get("unrealized_pnl"),
        "total_pnl": snapshot.get("total_pnl"),
    }


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
