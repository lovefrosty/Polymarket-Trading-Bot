from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from core_mm.control_plane import validate_command
from dashboard import data_access as da


REPO_ROOT = Path(__file__).resolve().parents[1]


class StartRuntimeRequest(BaseModel):
    exchange: str = "polymarket"
    mode: str = "PAPER"
    runtime_root: Optional[str] = None
    symbol: str = "BTC"
    symbols: Optional[List[str]] = None
    horizon: str = "15m"
    usdc_balance: float = 1000.0
    safe_risk_profile: str = "500"
    strategy_allocated_equity: Optional[float] = None
    duration_secs: float = 86_400.0
    cycle_secs: float = 1.0
    refresh_market_secs: float = 60.0
    run_name: Optional[str] = None


class CommandRequest(BaseModel):
    command_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    scope: str = "global"
    requested_by: str = "operator_app"
    expires_in_ms: int = 120_000


class StopRuntimeRequest(BaseModel):
    force_kill_after_ms: int = 5_000


@dataclass
class ManagedRuntime:
    run_id: str
    runtime_root: Path
    db_path: Path
    process: subprocess.Popen[str]
    started_at_ms: int
    mode: str
    log_path: Path


def _now_ms() -> int:
    return int(time.time() * 1000)


def _candidate_runtime_dbs(root: Path) -> List[Path]:
    candidates: List[Path] = []
    default_db = root / "runtime.db"
    if default_db.exists():
        candidates.append(default_db)
    for pattern in ("tmp/core_mm_runs/*/runtime.db", "tmp/desktop_run_archive/*/core_mm_runs/*/runtime.db"):
        candidates.extend(path for path in root.glob(pattern) if path.exists())
    return candidates


class OperatorService:
    def __init__(
        self,
        *,
        repo_root: Optional[Path] = None,
        command_builder: Optional[Callable[[StartRuntimeRequest, Path], List[str]]] = None,
    ) -> None:
        self.repo_root = Path(repo_root or REPO_ROOT).resolve()
        self._managed: Dict[str, ManagedRuntime] = {}
        self._command_builder = command_builder or self._default_command_builder

    def _default_command_builder(self, request: StartRuntimeRequest, runtime_root: Path) -> List[str]:
        command = [
            sys.executable,
            str((self.repo_root / "scripts" / "run_core_mm.py").resolve()),
            "--exchange",
            str(request.exchange or "polymarket"),
            "--mode",
            "PAPER",
            "--runtime-root",
            runtime_root.as_posix(),
            "--duration-secs",
            str(float(request.duration_secs)),
            "--symbol",
            str(request.symbol or "BTC"),
            "--horizon",
            str(request.horizon or "15m"),
            "--usdc-balance",
            str(float(request.usdc_balance)),
            "--safe-risk-profile",
            str(request.safe_risk_profile or "500"),
            "--cycle-secs",
            str(float(request.cycle_secs)),
            "--refresh-market-secs",
            str(float(request.refresh_market_secs)),
        ]
        if request.strategy_allocated_equity is not None:
            command.extend(["--strategy-allocated-equity", str(float(request.strategy_allocated_equity))])
        if request.symbols:
            command.extend(["--symbols", ",".join(str(symbol).upper() for symbol in request.symbols if str(symbol).strip())])
        if request.run_name:
            command.extend(["--run-name", str(request.run_name)])
        return command

    def _prune_managed(self) -> None:
        stale: List[str] = []
        for run_id, runtime in self._managed.items():
            if runtime.process.poll() is not None:
                stale.append(run_id)
        for run_id in stale:
            self._managed.pop(run_id, None)

    def _runtime_record(self, runtime_root: Path, *, managed: Optional[ManagedRuntime] = None) -> Dict[str, Any]:
        db_path = runtime_root / "runtime.db"
        snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=db_path if db_path.exists() else None)
        run_id = str(snapshot.get("status", {}).get("run_id") or runtime_root.name)
        active_managed = managed or self._managed.get(run_id)
        process_alive = bool(active_managed and active_managed.process.poll() is None)
        return {
            "run_id": run_id,
            "runtime_root": runtime_root.as_posix(),
            "db_path": db_path.as_posix(),
            "mode": str(snapshot.get("mode") or (active_managed.mode if active_managed else "UNKNOWN")).upper(),
            "stage": str(snapshot.get("stage") or ("running" if process_alive else "unknown")),
            "market": snapshot.get("market"),
            "strategy_name": snapshot.get("strategy_name"),
            "updated_at_ms": snapshot.get("updated_at_ms"),
            "quoteable": bool(snapshot.get("quoteable")) if snapshot.get("quoteable") is not None else None,
            "total_pnl": snapshot.get("total_pnl"),
            "managed": bool(active_managed is not None),
            "pid": active_managed.process.pid if active_managed else None,
            "started_at_ms": active_managed.started_at_ms if active_managed else None,
        }

    def discover_runtimes(self) -> List[Dict[str, Any]]:
        self._prune_managed()
        runtime_roots: Dict[str, Path] = {}
        for db_path in _candidate_runtime_dbs(self.repo_root):
            runtime_roots[db_path.parent.as_posix()] = db_path.parent
        for managed in self._managed.values():
            runtime_roots[managed.runtime_root.as_posix()] = managed.runtime_root
        records = [self._runtime_record(root) for root in runtime_roots.values()]
        return sorted(records, key=lambda item: float(item.get("updated_at_ms") or 0.0), reverse=True)

    def _runtime_by_run_id(self, run_id: str) -> Dict[str, Any]:
        for record in self.discover_runtimes():
            if str(record.get("run_id")) == str(run_id):
                return record
        raise KeyError(str(run_id))

    def _serialize_fills(self, *, db_path: Optional[Path], limit: int = 20) -> List[Dict[str, Any]]:
        if db_path is None or not db_path.exists():
            return []
        fills = da.get_fills_recent(limit=limit, db_path=db_path)
        rows: List[Dict[str, Any]] = []
        for _, row in fills.iterrows():
            payload = da.safe_json(row.get("payload_json"))
            rows.append(
                {
                    "ts_ms": int(row.get("ts_ms") or 0),
                    "order_id": row.get("order_id"),
                    "token_id": row.get("token_id"),
                    "side": row.get("side"),
                    "fill_price": row.get("fill_price"),
                    "fill_qty": row.get("fill_qty"),
                    "market_slug": row.get("market_slug"),
                    "quote_mode": row.get("quote_mode"),
                    "risk_action": row.get("risk_action"),
                    "control_state": row.get("control_state"),
                    "hedge_action": row.get("hedge_action"),
                    "fee_source": payload.get("fee_source"),
                    "fee_type": payload.get("fee_type"),
                    "fee_multiplier": payload.get("fee_multiplier"),
                    "realized_net_pnl_delta": payload.get("realized_net_pnl_delta"),
                }
            )
        return rows

    def _serialize_decisions(self, *, db_path: Optional[Path]) -> List[Dict[str, Any]]:
        if db_path is None or not db_path.exists():
            return []
        decisions = list(da.get_latest_decisions_per_token(db_path=db_path).values())
        ordered = sorted(decisions, key=lambda item: int(item.get("ts_ms") or 0), reverse=True)
        rows: List[Dict[str, Any]] = []
        for item in ordered[:20]:
            rows.append(
                {
                    "ts_ms": int(item.get("ts_ms") or 0),
                    "market": item.get("market"),
                    "token_id": item.get("token_id"),
                    "action": item.get("action"),
                    "reason_codes": item.get("reason_codes"),
                    "expected_edge": item.get("expected_edge"),
                    "p_fair": item.get("p_fair"),
                    "fee_type": item.get("fee_type"),
                    "fee_multiplier": item.get("fee_multiplier"),
                    "size_plan": item.get("size_plan") or {},
                    "risk_decision": item.get("risk_decision") or {},
                    "quote_plan": item.get("quote_plan") or {},
                }
            )
        return rows

    def _read_monitor_summary(self, runtime_root: Path) -> Dict[str, Any]:
        meta_dir = runtime_root / "meta"
        for name in ("live_monitor_latest.json", "overnight_protocol_latest.json"):
            path = meta_dir / name
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            return {
                "source": name,
                "path": path.as_posix(),
                "last_check_ts_ms": payload.get("check_ts_ms"),
                "warning_level": payload.get("warning_level"),
                "summary": payload.get("summary") or payload.get("protocol_observations") or {},
            }
        return {}

    def _serialize_alerts(self, *, db_path: Optional[Path], limit: int = 20) -> List[Dict[str, Any]]:
        if db_path is None or not db_path.exists():
            return []
        alerts = da.get_runtime_alert_feed(db_path=db_path)
        rows: List[Dict[str, Any]] = []
        for _, row in alerts.head(limit).iterrows():
            rows.append(
                {
                    "ts_ms": int(row.get("ts_ms") or 0),
                    "severity": row.get("severity"),
                    "alert_type": row.get("alert_type"),
                    "summary": row.get("summary"),
                    "next_action": row.get("next_action"),
                }
            )
        return rows

    def _serialize_commands(self, *, db_path: Optional[Path], limit: int = 20) -> List[Dict[str, Any]]:
        if db_path is None or not db_path.exists():
            return []
        commands = da.get_recent_control_commands(db_path=db_path, limit=limit)
        rows: List[Dict[str, Any]] = []
        for _, row in commands.iterrows():
            rows.append(
                {
                    "command_id": row.get("command_id"),
                    "command_type": row.get("command_type"),
                    "requested_by": row.get("requested_by"),
                    "requested_at_ms": int(row.get("requested_at_ms") or 0),
                    "status": row.get("status"),
                    "payload": json.loads(row.get("payload_json") or "{}"),
                    "result": json.loads(row.get("result_json") or "{}"),
                }
            )
        return rows

    def get_chart_history(self, run_id: str, *, points: int = 60) -> Dict[str, Any]:
        record = self._runtime_by_run_id(run_id)
        db_path = Path(str(record["db_path"]))
        db_path_or_none = db_path if db_path.exists() else None
        curve = da.get_paper_pnl_curve(db_path=db_path_or_none)
        rows: List[Dict[str, Any]] = []
        if not curve.empty:
            tail = curve.tail(max(1, int(points)))
            for _, row in tail.iterrows():
                rows.append(
                    {
                        "ts_ms": int(row.get("ts_ms") or 0),
                        "total_pnl": float(row.get("total_pnl") or 0.0),
                        "gross_exposure": None,
                    }
                )
        snapshot = self.build_operator_snapshot(run_id)
        latest_point = {
            "ts_ms": int(snapshot["health"].get("last_update_age_ms") or 0),
            "total_pnl": snapshot["portfolio"].get("total_pnl"),
            "gross_exposure": snapshot["portfolio"].get("gross_exposure"),
        }
        if rows:
            rows[-1]["gross_exposure"] = latest_point["gross_exposure"]
        elif latest_point["total_pnl"] is not None or latest_point["gross_exposure"] is not None:
            rows.append(
                {
                    "ts_ms": _now_ms(),
                    "total_pnl": latest_point["total_pnl"],
                    "gross_exposure": latest_point["gross_exposure"],
                }
            )
        return {"run_id": run_id, "points": rows}

    def build_operator_snapshot(self, run_id: str) -> Dict[str, Any]:
        record = self._runtime_by_run_id(run_id)
        runtime_root = Path(str(record["runtime_root"]))
        db_path = Path(str(record["db_path"]))
        db_path_or_none = db_path if db_path.exists() else None
        snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=db_path_or_none)
        control = da.get_control_plane_snapshot(db_path=db_path_or_none)
        portfolio_risk = snapshot.get("active_market_health", {}).get("portfolio_risk") if isinstance(snapshot.get("active_market_health"), dict) else {}
        payload = snapshot.get("payload_json") if isinstance(snapshot.get("payload_json"), dict) else {}
        feed = payload.get("feed") if isinstance(payload.get("feed"), dict) else {}
        selection = snapshot.get("selection") if isinstance(snapshot.get("selection"), dict) else {}
        updated_at_ms = int(snapshot.get("updated_at_ms") or 0)
        positions_df = da.get_per_token_inventory(db_path=db_path_or_none)
        open_orders_df = da.get_open_orders_latest(as_of_ts_ms=updated_at_ms, db_path=db_path_or_none) if updated_at_ms > 0 else None
        current_decision = da.get_latest_decision_snapshot(db_path=db_path_or_none)
        session_selection = da.get_selection_session_summary(runtime_snapshot=snapshot, db_path=db_path_or_none)
        session_performance = da.get_session_performance_summary(runtime_snapshot=snapshot, db_path=db_path_or_none)
        positions: List[Dict[str, Any]] = []
        for _, row in positions_df.iterrows():
            positions.append(
                {
                    "token_id": row.get("token_id"),
                    "yes_qty": row.get("yes_qty"),
                    "ts_ms": int(row.get("ts_ms") or 0),
                }
            )
        open_orders: List[Dict[str, Any]] = []
        if open_orders_df is not None:
            for _, row in open_orders_df.iterrows():
                open_orders.append(
                    {
                        "order_id": row.get("order_id"),
                        "token_id": row.get("token_id"),
                        "market_slug": row.get("market_slug"),
                        "side": row.get("side"),
                        "price": row.get("price"),
                        "size": row.get("size"),
                        "status": row.get("status"),
                        "ts_ms": int(row.get("ts_ms") or 0),
                    }
                )
        portfolio_selection = selection.get("portfolio_selection") if isinstance(selection.get("portfolio_selection"), dict) else {}
        return {
            "runtime": {
                "run_id": record["run_id"],
                "mode": snapshot.get("mode"),
                "stage": snapshot.get("stage"),
                "started_at_ms": record.get("started_at_ms"),
                "pid": record.get("pid"),
                "runtime_root": runtime_root.as_posix(),
                "db_path": db_path.as_posix(),
                "service_managed": bool(record.get("managed")),
            },
            "controls": {
                "trading_enabled": bool(control.get("trading_enabled", True)),
                "flatten_only_mode": bool(control.get("flatten_only_mode", False)),
                "kill_switch_enabled": bool(control.get("kill_switch_enabled", False)),
                "pending_command_count": int(control.get("pending_count") or 0),
                "last_applied": control.get("last_applied") or {},
            },
            "portfolio": {
                "realized_net_pnl": snapshot.get("realized_net_pnl"),
                "unrealized_pnl": snapshot.get("unrealized_pnl"),
                "total_pnl": snapshot.get("total_pnl"),
                "gross_exposure": portfolio_risk.get("gross_exposure") if isinstance(portfolio_risk, dict) else None,
                "active_positions": portfolio_risk.get("active_positions") if isinstance(portfolio_risk, dict) else None,
                "positions": positions,
            },
            "market": {
                "selected_market": snapshot.get("market"),
                "quoteable": snapshot.get("quoteable"),
                "book_health": snapshot.get("book_health"),
                "selected_reason": snapshot.get("selected_reason"),
                "selection": {
                    "launch_scope": portfolio_selection.get("launch_scope"),
                    "max_active_markets": portfolio_selection.get("max_active_markets"),
                    "selected_market": selection.get("selected_market") if isinstance(selection.get("selected_market"), dict) else {},
                    "selected_reason": selection.get("selected_reason") or snapshot.get("selected_reason"),
                    "accepted_candidates": list(selection.get("accepted_candidates") or [])[:3] if isinstance(selection.get("accepted_candidates"), list) else [],
                    "rejected_candidates": list(selection.get("rejected_candidates") or [])[:3] if isinstance(selection.get("rejected_candidates"), list) else [],
                    "candidate_decisions": list(portfolio_selection.get("candidate_decisions") or [])[:5] if isinstance(portfolio_selection.get("candidate_decisions"), list) else [],
                },
            },
            "decision": {
                "current": current_decision,
            },
            "session": {
                "selection": session_selection,
                "performance": session_performance,
            },
            "health": {
                "feed_connected": bool(feed.get("connected", False)),
                "last_update_age_ms": max(0, _now_ms() - updated_at_ms) if updated_at_ms > 0 else None,
                "state": snapshot.get("state"),
                "freeze_reasons": list(snapshot.get("freeze_reasons") or []),
            },
            "monitor": self._read_monitor_summary(runtime_root),
            "recent": {
                "fills": self._serialize_fills(db_path=db_path_or_none),
                "decisions": self._serialize_decisions(db_path=db_path_or_none),
                "alerts": self._serialize_alerts(db_path=db_path_or_none),
                "commands": self._serialize_commands(db_path=db_path_or_none),
                "open_orders": open_orders,
            },
        }

    def queue_command(self, run_id: str, request: CommandRequest) -> str:
        record = self._runtime_by_run_id(run_id)
        mode = str(record.get("mode") or "UNKNOWN").upper()
        errors = validate_command(mode, request.command_type, dict(request.payload))
        if errors:
            raise ValueError(",".join(errors))
        return da.queue_control_command(
            command_type=request.command_type,
            payload=dict(request.payload),
            scope=request.scope,
            requested_by=request.requested_by,
            expires_in_ms=int(request.expires_in_ms),
            db_path=Path(str(record["db_path"])),
        )

    def list_commands(self, run_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        record = self._runtime_by_run_id(run_id)
        return self._serialize_commands(db_path=Path(str(record["db_path"])), limit=limit)

    async def start_runtime(self, request: StartRuntimeRequest) -> Dict[str, Any]:
        if str(request.mode or "").upper() != "PAPER":
            raise ValueError("only_paper_mode_supported_in_v1")
        runtime_root = Path(request.runtime_root).resolve() if request.runtime_root else (
            self.repo_root / "tmp" / "core_mm_runs" / f"operator-{_now_ms()}"
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        log_path = runtime_root / "meta" / "operator_service.runner.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._command_builder(request, runtime_root)
        log_handle = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=self.repo_root.as_posix(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        run_id = runtime_root.name
        self._managed[run_id] = ManagedRuntime(
            run_id=run_id,
            runtime_root=runtime_root,
            db_path=runtime_root / "runtime.db",
            process=process,
            started_at_ms=_now_ms(),
            mode="PAPER",
            log_path=log_path,
        )
        await asyncio.sleep(0.05)
        return self._runtime_record(runtime_root, managed=self._managed[run_id])

    async def stop_runtime(self, run_id: str, *, force_kill_after_ms: int = 5_000) -> Dict[str, Any]:
        record = self._runtime_by_run_id(run_id)
        managed = self._managed.get(str(record["run_id"]))
        if managed is None:
            raise ValueError("runtime_not_service_managed")
        if managed.process.poll() is not None:
            self._managed.pop(str(record["run_id"]), None)
            return {"run_id": record["run_id"], "status": "already_stopped", "pid": managed.process.pid}
        managed.process.terminate()
        timeout_secs = max(1.0, float(force_kill_after_ms) / 1000.0)
        try:
            await asyncio.wait_for(asyncio.to_thread(managed.process.wait), timeout=timeout_secs)
            stopped = "terminated"
        except asyncio.TimeoutError:
            managed.process.kill()
            await asyncio.to_thread(managed.process.wait)
            stopped = "killed"
        self._managed.pop(str(record["run_id"]), None)
        return {"run_id": record["run_id"], "status": stopped, "pid": managed.process.pid}

    async def stream_runtime(self, websocket: WebSocket, run_id: str) -> None:
        previous: Dict[str, str] = {}
        while True:
            snapshot = self.build_operator_snapshot(run_id)
            payloads = {
                "runtime_status": {
                    "runtime": snapshot["runtime"],
                    "market": snapshot["market"],
                    "decision": snapshot["decision"],
                    "session": snapshot["session"],
                    "health": snapshot["health"],
                    "controls": snapshot["controls"],
                    "monitor": snapshot["monitor"],
                },
                "portfolio_status": snapshot["portfolio"],
                "fill_event": snapshot["recent"]["fills"],
                "decision_event": snapshot["recent"]["decisions"],
                "alert_event": snapshot["recent"]["alerts"],
                "command_event": snapshot["recent"]["commands"],
                "process_event": {
                    "run_id": snapshot["runtime"]["run_id"],
                    "pid": snapshot["runtime"]["pid"],
                    "stage": snapshot["runtime"]["stage"],
                    "service_managed": snapshot["runtime"]["service_managed"],
                },
            }
            for event_name, data in payloads.items():
                serialized = json.dumps(data, sort_keys=True, default=str)
                if previous.get(event_name) != serialized:
                    previous[event_name] = serialized
                    await websocket.send_json({"event": event_name, "data": data})
            await asyncio.sleep(0.5)


def create_app(service: Optional[OperatorService] = None) -> FastAPI:
    operator_service = service or OperatorService()
    app = FastAPI(title="Core MM Operator Service", version="0.1.0")

    @app.get("/api/runtimes")
    async def list_runtimes() -> Dict[str, Any]:
        return {"runtimes": operator_service.discover_runtimes()}

    @app.get("/api/runtimes/{run_id}/snapshot")
    async def get_snapshot(run_id: str) -> Dict[str, Any]:
        try:
            return operator_service.build_operator_snapshot(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="runtime_not_found") from exc

    @app.get("/api/runtimes/{run_id}/history")
    async def get_history(run_id: str, points: int = 60) -> Dict[str, Any]:
        try:
            return operator_service.get_chart_history(run_id, points=points)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="runtime_not_found") from exc

    @app.post("/api/runtimes/start")
    async def start_runtime(request: StartRuntimeRequest) -> Dict[str, Any]:
        try:
            runtime = await operator_service.start_runtime(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"runtime": runtime}

    @app.post("/api/runtimes/{run_id}/commands")
    async def queue_runtime_command(run_id: str, request: CommandRequest) -> Dict[str, Any]:
        try:
            command_id = operator_service.queue_command(run_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="runtime_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"command_id": command_id}

    @app.get("/api/runtimes/{run_id}/commands")
    async def get_runtime_commands(run_id: str, limit: int = 20) -> Dict[str, Any]:
        try:
            return {"commands": operator_service.list_commands(run_id, limit=limit)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="runtime_not_found") from exc

    @app.post("/api/runtimes/{run_id}/stop")
    async def stop_runtime(run_id: str, request: StopRuntimeRequest) -> Dict[str, Any]:
        try:
            result = await operator_service.stop_runtime(run_id, force_kill_after_ms=int(request.force_kill_after_ms))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="runtime_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result

    @app.websocket("/ws/runtimes/{run_id}")
    async def runtime_ws(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        try:
            await operator_service.stream_runtime(websocket, run_id)
        except KeyError:
            await websocket.send_json({"event": "process_event", "data": {"error": "runtime_not_found", "run_id": run_id}})
            await websocket.close(code=4404)
        except WebSocketDisconnect:
            return

    return app


app = create_app()
