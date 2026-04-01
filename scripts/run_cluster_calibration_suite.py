from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_STALE_DURATION_SCALE = 10.0 / 3600.0
DEFAULT_MAKER_EXIT_GRACE_SECS = 3.0
DEFAULT_CROSS_ESCALATION_DRAWDOWN_PCT = 0.005
DEFAULT_SAFE_PROFILE = "200"
DEFAULT_SYMBOL = "BTC"
DEFAULT_EXCHANGE = "kalshi"


@dataclass(frozen=True)
class RunSpec:
    key: str
    duration_secs: int
    max_active_markets: int
    max_market_exposure_pct: float
    max_event_exposure_pct: float
    pre_kill_warning_fraction: float
    notes: str
    extra_args: tuple[str, ...] = ()


@dataclass
class RunResult:
    key: str
    runtime_root: str
    duration_secs: int
    status: str
    summary: Dict[str, Any] = field(default_factory=dict)
    phase0_report: Dict[str, Any] = field(default_factory=dict)
    hold_summary: Dict[str, Any] = field(default_factory=dict)
    cluster_summary: Dict[str, Any] = field(default_factory=dict)
    action_effectiveness: Dict[str, Any] = field(default_factory=dict)
    stranded_positions: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    error: Optional[str] = None


def _timestamp_label() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _build_three_hour_specs() -> List[RunSpec]:
    return [
        RunSpec(
            key="open-market-safety-30m",
            duration_secs=30 * 60,
            max_active_markets=1,
            max_market_exposure_pct=0.03,
            max_event_exposure_pct=0.04,
            pre_kill_warning_fraction=0.60,
            notes="Baseline open-market proof run for stale_unwind / day-loss / flatten-only evidence.",
        ),
        RunSpec(
            key="mixed-60m",
            duration_secs=60 * 60,
            max_active_markets=2,
            max_market_exposure_pct=0.03,
            max_event_exposure_pct=0.04,
            pre_kill_warning_fraction=0.60,
            notes="Primary cluster-cap mixed run using recommended starting settings.",
        ),
        RunSpec(
            key="skew-proof-20m",
            duration_secs=20 * 60,
            max_active_markets=2,
            max_market_exposure_pct=0.02,
            max_event_exposure_pct=0.03,
            pre_kill_warning_fraction=0.50,
            notes="Tighter caps to favor SKEW over full hedge expansion.",
        ),
        RunSpec(
            key="hedge-proof-20m",
            duration_secs=20 * 60,
            max_active_markets=2,
            max_market_exposure_pct=0.03,
            max_event_exposure_pct=0.05,
            pre_kill_warning_fraction=0.60,
            notes="Looser event cap to allow HEDGE actions when quality is sufficient.",
        ),
        RunSpec(
            key="unwind-proof-20m",
            duration_secs=20 * 60,
            max_active_markets=2,
            max_market_exposure_pct=0.02,
            max_event_exposure_pct=0.03,
            pre_kill_warning_fraction=0.50,
            notes="Force concentration pressure and verify UNWIND respects expiry/risk rails.",
        ),
        RunSpec(
            key="mixed-confirm-30m",
            duration_secs=30 * 60,
            max_active_markets=2,
            max_market_exposure_pct=0.03,
            max_event_exposure_pct=0.04,
            pre_kill_warning_fraction=0.60,
            notes="Confirmation rerun on the recommended configuration after proof runs.",
        ),
    ]


def build_suite_plan(*, budget_minutes: int) -> List[RunSpec]:
    specs = _build_three_hour_specs()
    total_budget = max(0, int(budget_minutes)) * 60
    elapsed = 0
    selected: List[RunSpec] = []
    for spec in specs:
        if elapsed + spec.duration_secs > total_budget and selected:
            break
        if spec.duration_secs > total_budget and not selected:
            selected.append(spec)
            break
        selected.append(spec)
        elapsed += spec.duration_secs
    return selected


def _runtime_root_for(suite_root: Path, index: int, spec: RunSpec) -> Path:
    return suite_root / f"{index:02d}-{spec.key}"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_system_state_rows(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    cx = sqlite3.connect(db_path.as_posix())
    try:
        rows = cx.execute(
            "SELECT as_of_ts, payload_json FROM system_state ORDER BY as_of_ts ASC"
        ).fetchall()
    finally:
        cx.close()
    result: List[Dict[str, Any]] = []
    for as_of_ts, payload_json in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        result.append({"as_of_ts": int(as_of_ts), "payload": payload if isinstance(payload, dict) else {}})
    return result


def _hold_summary(db_path: Path) -> Dict[str, Any]:
    if not db_path.exists():
        return {}
    cx = sqlite3.connect(db_path.as_posix())
    try:
        rows = cx.execute(
            "SELECT ts_ms, token_id, side, fill_price, fill_qty FROM fills ORDER BY ts_ms ASC"
        ).fetchall()
    finally:
        cx.close()
    open_buys: Dict[str, List[Dict[str, float]]] = {}
    holds_ms: List[float] = []
    for ts_ms, token_id, side, _fill_price, fill_qty in rows:
        token = str(token_id or "")
        qty = float(fill_qty or 0.0)
        direction = str(side or "").upper()
        if direction == "BUY":
            open_buys.setdefault(token, []).append({"ts_ms": float(ts_ms), "qty": qty})
            continue
        if direction != "SELL" or qty <= 0.0:
            continue
        remaining = qty
        queue = open_buys.get(token) or []
        while remaining > 1e-9 and queue:
            lot = queue[0]
            matched = min(remaining, float(lot["qty"]))
            holds_ms.append(float(ts_ms) - float(lot["ts_ms"]))
            lot["qty"] = float(lot["qty"]) - matched
            remaining -= matched
            if float(lot["qty"]) <= 1e-9:
                queue.pop(0)
        if queue:
            open_buys[token] = queue
    if not holds_ms:
        return {"matched_round_trips": 0}
    sorted_holds = sorted(holds_ms)
    p90_index = min(len(sorted_holds) - 1, max(0, math.ceil(len(sorted_holds) * 0.9) - 1))
    return {
        "matched_round_trips": len(sorted_holds),
        "mean_hold_secs": round(float(statistics.fmean(sorted_holds)) / 1000.0, 3),
        "median_hold_secs": round(float(statistics.median(sorted_holds)) / 1000.0, 3),
        "p90_hold_secs": round(float(sorted_holds[p90_index]) / 1000.0, 3),
        "max_hold_secs": round(float(max(sorted_holds)) / 1000.0, 3),
    }


def _stranded_positions(db_path: Path) -> Dict[str, Any]:
    if not db_path.exists():
        return {}
    cx = sqlite3.connect(db_path.as_posix())
    try:
        rows = cx.execute(
            "SELECT token_id, side, fill_qty FROM fills ORDER BY ts_ms ASC"
        ).fetchall()
    finally:
        cx.close()
    net_qty: Dict[str, float] = {}
    for token_id, side, fill_qty in rows:
        token = str(token_id or "")
        qty = float(fill_qty or 0.0)
        sign = 1.0 if str(side or "").upper() == "BUY" else -1.0
        net_qty[token] = float(net_qty.get(token, 0.0)) + sign * qty
    stranded = {token: round(qty, 6) for token, qty in net_qty.items() if qty > 1e-9}
    return {
        "open_token_count": len(stranded),
        "open_tokens": stranded,
    }


def _cluster_summary(db_path: Path) -> Dict[str, Any]:
    snapshots = _load_system_state_rows(db_path)
    if not snapshots:
        return {}
    peak_abs_net_by_cluster: Dict[str, float] = {}
    first_abs_net_by_cluster: Dict[str, float] = {}
    last_abs_net_by_cluster: Dict[str, float] = {}
    gross_exposure_peak = 0.0
    active_clusters_peak = 0
    for snapshot in snapshots:
        payload = snapshot["payload"]
        cluster_exposure = payload.get("cluster_exposure") or {}
        gross_exposure_peak = max(gross_exposure_peak, float(cluster_exposure.get("gross_exposure") or 0.0))
        active_clusters_peak = max(active_clusters_peak, int(cluster_exposure.get("active_cluster_count") or 0))
        for cluster in list(cluster_exposure.get("clusters") or []):
            cluster_id = str(cluster.get("cluster_id") or "")
            if not cluster_id:
                continue
            abs_net = abs(float(cluster.get("net_yes_exposure_notional") or 0.0))
            peak_abs_net_by_cluster[cluster_id] = max(abs_net, peak_abs_net_by_cluster.get(cluster_id, 0.0))
            first_abs_net_by_cluster.setdefault(cluster_id, abs_net)
            last_abs_net_by_cluster[cluster_id] = abs_net
    return {
        "cluster_count_seen": len(peak_abs_net_by_cluster),
        "gross_exposure_peak": round(gross_exposure_peak, 4),
        "active_cluster_count_peak": active_clusters_peak,
        "peak_abs_net_exposure_by_cluster": {
            key: round(value, 4) for key, value in sorted(peak_abs_net_by_cluster.items())
        },
        "start_abs_net_exposure_by_cluster": {
            key: round(value, 4) for key, value in sorted(first_abs_net_by_cluster.items())
        },
        "end_abs_net_exposure_by_cluster": {
            key: round(value, 4) for key, value in sorted(last_abs_net_by_cluster.items())
        },
    }


def _action_effectiveness(db_path: Path) -> Dict[str, Any]:
    snapshots = _load_system_state_rows(db_path)
    if len(snapshots) < 2:
        return {}
    effects: Dict[str, Dict[str, int]] = {}
    for idx in range(len(snapshots) - 1):
        current = snapshots[idx]["payload"]
        following = snapshots[idx + 1]["payload"]
        current_exposure = {
            str(cluster.get("cluster_id") or ""): abs(float(cluster.get("net_yes_exposure_notional") or 0.0))
            for cluster in list((current.get("cluster_exposure") or {}).get("clusters") or [])
            if cluster.get("cluster_id")
        }
        next_exposure = {
            str(cluster.get("cluster_id") or ""): abs(float(cluster.get("net_yes_exposure_notional") or 0.0))
            for cluster in list((following.get("cluster_exposure") or {}).get("clusters") or [])
            if cluster.get("cluster_id")
        }
        for cluster in list((current.get("cluster_hedge") or {}).get("clusters") or []):
            action = str(cluster.get("action") or "").strip()
            cluster_id = str(cluster.get("cluster_id") or "")
            if not action or action == "NONE" or not cluster_id:
                continue
            before = current_exposure.get(cluster_id)
            after = next_exposure.get(cluster_id)
            if before is None or after is None:
                continue
            bucket = effects.setdefault(action, {"observed": 0, "improved": 0, "flat": 0, "worsened": 0})
            bucket["observed"] += 1
            if after < before - 1e-9:
                bucket["improved"] += 1
            elif after > before + 1e-9:
                bucket["worsened"] += 1
            else:
                bucket["flat"] += 1
    return {key: dict(value) for key, value in sorted(effects.items())}


def _load_run_result(spec: RunSpec, runtime_root: Path) -> RunResult:
    meta_dir = runtime_root / "meta"
    summary = _read_json(meta_dir / "run_summary.json")
    phase0_report = _read_json(meta_dir / "phase0_report.json")
    db_path = runtime_root / "runtime.db"
    return RunResult(
        key=spec.key,
        runtime_root=runtime_root.as_posix(),
        duration_secs=spec.duration_secs,
        status="completed" if summary else "missing_summary",
        summary=summary,
        phase0_report=phase0_report,
        hold_summary=_hold_summary(db_path),
        cluster_summary=_cluster_summary(db_path),
        action_effectiveness=_action_effectiveness(db_path),
        stranded_positions=_stranded_positions(db_path),
        notes=spec.notes,
    )


def _run_subprocess(cmd: List[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd.as_posix(), check=True)


def _run_one(
    spec: RunSpec,
    *,
    suite_root: Path,
    index: int,
    exchange: str,
    symbol: str,
    safe_risk_profile: str,
    allocated_equity: float,
) -> RunResult:
    runtime_root = _runtime_root_for(suite_root, index, spec)
    runtime_root.mkdir(parents=True, exist_ok=True)
    run_name = f"Calibration {index:02d} {spec.key}"
    cmd = [
        sys.executable,
        "scripts/run_core_mm.py",
        "--exchange",
        exchange,
        "--mode",
        "PAPER",
        "--runtime-root",
        runtime_root.as_posix(),
        "--duration-secs",
        str(spec.duration_secs),
        "--symbol",
        symbol,
        "--safe-risk-profile",
        safe_risk_profile,
        "--strategy-allocated-equity",
        str(allocated_equity),
        "--kelly-fraction",
        "0.0",
        "--max-active-markets",
        str(spec.max_active_markets),
        "--max-market-exposure-pct",
        str(spec.max_market_exposure_pct),
        "--max-event-exposure-pct",
        str(spec.max_event_exposure_pct),
        "--pre-kill-warning-fraction",
        str(spec.pre_kill_warning_fraction),
        "--stale-duration-scale",
        str(DEFAULT_STALE_DURATION_SCALE),
        "--maker-exit-grace-secs",
        str(DEFAULT_MAKER_EXIT_GRACE_SECS),
        "--cross-escalation-drawdown-pct",
        str(DEFAULT_CROSS_ESCALATION_DRAWDOWN_PCT),
        "--run-name",
        run_name,
    ]
    cmd.extend(spec.extra_args)
    _run_subprocess(cmd, cwd=REPO_ROOT)
    _run_subprocess(
        [
            sys.executable,
            "scripts/report_core_mm_run.py",
            "--runtime-root",
            runtime_root.as_posix(),
        ],
        cwd=REPO_ROOT,
    )
    return _load_run_result(spec, runtime_root)


def _result_row(result: RunResult) -> Dict[str, Any]:
    summary = result.summary
    phase0 = result.phase0_report.get("live_readiness") or {}
    hedge_summary = summary.get("hedge_summary") or {}
    risk_proof = summary.get("risk_proof") or {}
    row = {
        "key": result.key,
        "runtime_root": result.runtime_root,
        "status": result.status,
        "duration_minutes": round(result.duration_secs / 60.0, 1),
        "total_pnl": summary.get("total_pnl"),
        "fills": summary.get("fills"),
        "placed_orders": summary.get("placed_orders"),
        "quoteable_ratio": (summary.get("cycle_summary") or {}).get("quoteable_ratio"),
        "go_no_go": phase0.get("go_no_go"),
        "risk_observed": risk_proof,
        "hedge_actions": hedge_summary.get("cluster_actions") or {},
        "hold_summary": result.hold_summary,
        "cluster_summary": result.cluster_summary,
        "action_effectiveness": result.action_effectiveness,
        "stranded_positions": result.stranded_positions,
        "notes": result.notes,
    }
    if result.error:
        row["error"] = result.error
    return row


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _format_questions() -> List[str]:
    return [
        "What is the maximum acceptable inactivity level before the bot is considered too constrained to be useful?",
        "At each bankroll tier ($200, $500, $1000), what are the exact maximum gross cluster exposure and maximum net directional cluster exposure you are willing to allow?",
        "When cluster concentration rises, should the bot prefer SKEW first in all cases, or are there conditions where it should jump straight to HEDGE or UNWIND?",
        "What is the maximum taker/cross cost you are willing to pay to neutralize stale or concentrated inventory before it becomes a policy violation?",
        "Which market families are allowed to share a hedge cluster: same expiry only, adjacent buckets only, or broader event-family groupings?",
        "How many simultaneous active markets are acceptable per bankroll tier, and at what threshold should new-market entry freeze when one cluster is already under unwind?",
        "Near expiry, do you want the hedge engine to keep trying portfolio hedges, or should it shift to direct UNWIND only once the stop-open window begins?",
        "What level of churn is acceptable if it materially reduces cluster concentration and drawdown tail risk?",
        "When HEDGE fails to reduce concentration quickly, how long should the bot wait before demoting that cluster to UNWIND-only behavior?",
        "What evidence threshold should promote PAD-24: one good mixed run, multiple green runs, or a minimum number of runs with repeated SKEW/HEDGE/UNWIND proof?",
    ]


def _write_paper(
    *,
    suite_root: Path,
    suite_name: str,
    results: Iterable[RunResult],
    suite_meta: Dict[str, Any],
) -> None:
    rows = list(results)
    completed = [row for row in rows if row.status == "completed"]
    total_pnl = round(sum(float((row.summary or {}).get("total_pnl") or 0.0) for row in completed), 4)
    total_fills = sum(int((row.summary or {}).get("fills") or 0) for row in completed)
    total_orders = sum(int((row.summary or {}).get("placed_orders") or 0) for row in completed)
    lines: List[str] = []
    lines.append(f"# Cluster Calibration CEO Brief: {suite_name}")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    lines.append(f"- Suite root: `{suite_root.as_posix()}`")
    lines.append(f"- Runs completed: `{len(completed)}/{len(rows)}`")
    lines.append(f"- Aggregate paper PnL: `{total_pnl}`")
    lines.append(f"- Aggregate fills / placed orders: `{total_fills}/{total_orders}`")
    lines.append("")
    lines.append("## What Changed In The Platform")
    lines.append("")
    lines.append("- Safe-first sizing is now driven by allocated bankroll and per-trade loss budget instead of arbitrary share sizing alone.")
    lines.append("- Risk timing is tighter: shorter stale windows, shorter maker grace, faster escalation, and flatten-then-halt on day-loss breach.")
    lines.append("- The dashboard is now an operator surface with staged controls, risk-state visibility, and calibration-oriented supervision panels.")
    lines.append("- The runner now tracks cluster-level exposure so related markets are treated as shared portfolio risk instead of isolated books.")
    lines.append("- The current frontier is proving cluster-aware paper behavior cleanly enough to unlock broader market expansion.")
    lines.append("")
    lines.append("## Executive Readout")
    lines.append("")
    if not completed:
        lines.append("- No completed runs yet. The suite is still in progress or failed before producing summaries.")
    else:
        best = max(completed, key=lambda row: float((row.summary or {}).get("total_pnl") or -10**9))
        worst = min(completed, key=lambda row: float((row.summary or {}).get("total_pnl") or 10**9))
        lines.append(f"- Best run so far: `{best.key}` with total PnL `{(best.summary or {}).get('total_pnl')}`.")
        lines.append(f"- Weakest run so far: `{worst.key}` with total PnL `{(worst.summary or {}).get('total_pnl')}`.")
        lines.append("- Main question for promotion is no longer whether the bot can place trades; it is whether cluster actions actually reduce concentration without producing fee-driven churn.")
    lines.append("")
    lines.append("## Run Breakdown")
    lines.append("")
    for row in rows:
        summary = row.summary or {}
        live_readiness = row.phase0_report.get("live_readiness") or {}
        hedge_summary = summary.get("hedge_summary") or {}
        risk_proof = summary.get("risk_proof") or {}
        lines.append(f"### {row.key}")
        lines.append(f"- Runtime root: `{row.runtime_root}`")
        lines.append(f"- Status: `{row.status}`")
        if row.error:
            lines.append(f"- Error: `{row.error}`")
            lines.append("")
            continue
        lines.append(f"- Notes: {row.notes}")
        lines.append(f"- PnL: total `{summary.get('total_pnl')}`, realized `{summary.get('realized_net_pnl')}`, unrealized `{summary.get('unrealized_pnl')}`")
        lines.append(f"- Activity: fills `{summary.get('fills')}`, placed orders `{summary.get('placed_orders')}`, quoteable ratio `{(summary.get('cycle_summary') or {}).get('quoteable_ratio')}`")
        lines.append(f"- Holds: {json.dumps(row.hold_summary, sort_keys=True)}")
        lines.append(f"- Cluster exposure: {json.dumps(row.cluster_summary, sort_keys=True)}")
        lines.append(f"- Hedge actions: {json.dumps(hedge_summary.get('cluster_actions') or {}, sort_keys=True)}")
        lines.append(f"- Action effectiveness: {json.dumps(row.action_effectiveness, sort_keys=True)}")
        lines.append(f"- Risk proof: {json.dumps(risk_proof, sort_keys=True)}")
        lines.append(f"- Live-readiness gate: `{live_readiness.get('go_no_go')}` blockers={json.dumps(live_readiness.get('blockers') or [], sort_keys=True)}")
        lines.append(f"- Stranded positions: {json.dumps(row.stranded_positions, sort_keys=True)}")
        lines.append("")
    lines.append("## How To Read These Results")
    lines.append("")
    lines.append("- Good settings reduce peak cluster net exposure, shorten the hold-time tail, reduce stranded positions, and do not explode churn.")
    lines.append("- Bad settings block too much participation, fire HEDGE without lowering concentration, or leave stale inventory tail risk basically unchanged.")
    lines.append("")
    lines.append("## Questions You Need To Answer Precisely")
    lines.append("")
    for question in _format_questions():
        lines.append(f"- {question}")
    lines.append("")
    lines.append("## Artifact Index")
    lines.append("")
    lines.append(f"- Suite metadata: `{(suite_root / 'meta' / 'suite_state.json').as_posix()}`")
    lines.append(f"- Suite summary: `{(suite_root / 'meta' / 'suite_summary.json').as_posix()}`")
    for row in rows:
        lines.append(f"- {row.key}: `{row.runtime_root}`")
    paper_path = suite_root / "meta" / "cluster_calibration_ceo_brief.md"
    paper_path.parent.mkdir(parents=True, exist_ok=True)
    paper_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_suite_state(
    *,
    suite_root: Path,
    suite_name: str,
    specs: List[RunSpec],
    results: List[RunResult],
    started_at_ms: int,
) -> None:
    payload = {
        "suite_name": suite_name,
        "suite_root": suite_root.as_posix(),
        "started_at_ms": started_at_ms,
        "updated_at_ms": int(time.time() * 1000),
        "planned_runs": [asdict(spec) for spec in specs],
        "results": [_result_row(result) for result in results],
    }
    _write_json(suite_root / "meta" / "suite_state.json", payload)
    _write_json(suite_root / "meta" / "suite_summary.json", payload)
    _write_paper(suite_root=suite_root, suite_name=suite_name, results=results, suite_meta=payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a clean cluster calibration paper suite and generate a CEO brief.")
    parser.add_argument("--runtime-base", default="tmp/core_mm_runs")
    parser.add_argument("--suite-name", default=None)
    parser.add_argument("--budget-minutes", type=int, default=180)
    parser.add_argument("--exchange", default=DEFAULT_EXCHANGE)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--safe-risk-profile", default=DEFAULT_SAFE_PROFILE)
    parser.add_argument("--allocated-equity", type=float, default=200.0)
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    suite_name = args.suite_name or f"cluster-calibration-{_timestamp_label()}"
    suite_root = (REPO_ROOT / args.runtime_base / suite_name).resolve()
    suite_root.mkdir(parents=True, exist_ok=True)
    specs = build_suite_plan(budget_minutes=args.budget_minutes)
    started_at_ms = int(time.time() * 1000)
    results: List[RunResult] = []
    _write_suite_state(
        suite_root=suite_root,
        suite_name=suite_name,
        specs=specs,
        results=results,
        started_at_ms=started_at_ms,
    )
    if args.dry_run:
        print(json.dumps({"suite_root": suite_root.as_posix(), "planned_runs": [asdict(spec) for spec in specs]}, indent=2))
        return
    for index, spec in enumerate(specs, start=1):
        try:
            result = _run_one(
                spec,
                suite_root=suite_root,
                index=index,
                exchange=str(args.exchange),
                symbol=str(args.symbol),
                safe_risk_profile=str(args.safe_risk_profile),
                allocated_equity=float(args.allocated_equity),
            )
        except subprocess.CalledProcessError as exc:
            result = RunResult(
                key=spec.key,
                runtime_root=_runtime_root_for(suite_root, index, spec).as_posix(),
                duration_secs=spec.duration_secs,
                status="failed",
                notes=spec.notes,
                error=f"command_failed:{exc.returncode}",
            )
        results.append(result)
        _write_suite_state(
            suite_root=suite_root,
            suite_name=suite_name,
            specs=specs,
            results=results,
            started_at_ms=started_at_ms,
        )
    print(json.dumps({"suite_root": suite_root.as_posix()}, sort_keys=True))


if __name__ == "__main__":
    main()
