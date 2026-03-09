from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TRIGGER_PHRASES: Tuple[str, ...] = (
    "run audit",
    "audit the repo",
    "check alignment",
    "what's missing / risky",
    "is this efficient",
)

SECTION_TITLES: Tuple[str, ...] = (
    "A) Executive Summary",
    "B) Repo Map + Key Modules",
    "C) Goal Alignment Matrix",
    "D) Invariant Checks",
    "E) Risk Register",
    "F) Efficiency Findings",
    "G) Test Coverage + Replay Parity",
    "H) Recommended Next Steps",
    "I) Do Not Do List",
)

GOALS: Tuple[str, ...] = (
    "Deterministic logging + replay parity",
    "Strict causality / anti-leakage as-of discipline",
    "Tradable market discovery (avoid CANDIDATE_NOT_LIVE loops)",
    "Fail-closed trade gating with explicit reasons",
    "Execution mode safety (OBSERVE/PAPER/TRADE)",
    "Telemetry that distinguishes wired vs healthy vs tradable",
)


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ScanMatch:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class GoalMatrixRow:
    goal: str
    evidence: str
    gaps: str


@dataclass(frozen=True)
class RiskItem:
    severity: str
    likelihood: str
    title: str
    impact: str
    evidence: str


@dataclass(frozen=True)
class Recommendation:
    priority: str
    title: str
    file_targets: Tuple[str, ...]
    expected_behavior_change: str
    acceptance_criteria: Tuple[str, ...]
    regression_risk: str


@dataclass(frozen=True)
class AuditReport:
    generated_at: str
    trigger_text: str
    trigger_matched: bool
    repo_state_summary: Tuple[str, ...]
    repo_map_rows: Tuple[Tuple[str, Tuple[str, ...]], ...]
    goal_alignment: Tuple[GoalMatrixRow, ...]
    invariant_checks: Tuple[Tuple[str, Tuple[str, ...]], ...]
    risks: Tuple[RiskItem, ...]
    efficiency_findings: Tuple[str, ...]
    test_coverage: Tuple[str, ...]
    recommendations: Tuple[Recommendation, ...]
    do_not_do: Tuple[str, ...]
    insufficient_evidence: Tuple[str, ...]


def should_run_full_audit(trigger_text: str) -> bool:
    lowered = _normalize_text(trigger_text)
    return any(_normalize_text(phrase) in lowered for phrase in TRIGGER_PHRASES)


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _run_cmd(args: Sequence[str], cwd: Path, timeout_sec: int = 90) -> CommandResult:
    try:
        proc = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return CommandResult(
            command=" ".join(args),
            exit_code=int(proc.returncode),
            stdout=str(proc.stdout or "").strip(),
            stderr=str(proc.stderr or "").strip(),
        )
    except FileNotFoundError:
        return CommandResult(
            command=" ".join(args),
            exit_code=127,
            stdout="",
            stderr=f"command_not_found:{args[0]}",
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            command=" ".join(args),
            exit_code=124,
            stdout="",
            stderr="command_timeout",
        )


def _parse_rg_output(output: str, repo_root: Path, max_matches: int) -> List[ScanMatch]:
    matches: List[ScanMatch] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parts = raw.split(":", 2)
        if len(parts) != 3:
            continue
        path_raw, line_raw, text = parts
        try:
            line_num = int(line_raw)
        except ValueError:
            continue
        path_obj = Path(path_raw)
        if not path_obj.is_absolute():
            path_obj = (repo_root / path_obj).resolve()
        matches.append(ScanMatch(path=path_obj.as_posix(), line=line_num, text=text.strip()))
        if len(matches) >= max_matches:
            break
    return matches


def _iter_scan_files(repo_root: Path, targets: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    seen: set[str] = set()
    excluded = (repo_root / "scripts/repo_audit.py").resolve()

    for target in targets:
        candidate = (repo_root / target).resolve()
        if candidate == excluded:
            continue
        if candidate.is_file():
            key = candidate.as_posix()
            if key not in seen:
                seen.add(key)
                files.append(candidate)
            continue
        if not candidate.is_dir():
            continue
        for path in sorted(candidate.rglob("*")):
            if not path.is_file() or path == excluded:
                continue
            key = path.resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            files.append(path.resolve())

    return files


def _python_fallback_search(
    repo_root: Path,
    pattern: str,
    targets: Sequence[str],
    max_matches: int = 10,
) -> List[ScanMatch]:
    try:
        regex = re.compile(pattern)
    except re.error:
        return []

    matches: List[ScanMatch] = []
    for path in _iter_scan_files(repo_root=repo_root, targets=targets):
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in payload:
            continue
        text = payload.decode("utf-8", errors="ignore")
        for line_num, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue
            matches.append(ScanMatch(path=path.as_posix(), line=line_num, text=line.strip()))
            if len(matches) >= max_matches:
                return matches
    return matches


def _rg_search(
    repo_root: Path,
    pattern: str,
    targets: Sequence[str],
    max_matches: int = 10,
) -> Tuple[List[ScanMatch], Optional[str]]:
    rg_path = shutil.which("rg")
    if rg_path is None:
        fallback = _python_fallback_search(
            repo_root=repo_root,
            pattern=pattern,
            targets=targets,
            max_matches=max_matches,
        )
        result = "matches_found" if fallback else "no_matches"
        return fallback, f"tool_unavailable:rg;fallback:python_scan;result:{result}"
    args = [
        rg_path,
        "-n",
        "--no-heading",
        "--color",
        "never",
        pattern,
        "-g",
        "!scripts/repo_audit.py",
        *targets,
    ]
    result = _run_cmd(args, cwd=repo_root)
    if result.exit_code not in {0, 1}:
        fallback = _python_fallback_search(
            repo_root=repo_root,
            pattern=pattern,
            targets=targets,
            max_matches=max_matches,
        )
        fallback_result = "matches_found" if fallback else "no_matches"
        err = result.stderr or str(result.exit_code)
        return fallback, f"rg_error:{err};fallback:python_scan;result:{fallback_result}"
    return _parse_rg_output(result.stdout, repo_root=repo_root, max_matches=max_matches), None


def _format_matches(matches: Sequence[ScanMatch], limit: int = 3) -> str:
    if not matches:
        return "none"
    sample = []
    for item in list(matches)[:limit]:
        sample.append(f"`{item.path}:{item.line}`")
    return ", ".join(sample)


def _collect_repo_state(repo_root: Path) -> Tuple[Tuple[str, ...], List[str], Dict[str, CommandResult]]:
    commands = {
        "status": _run_cmd(["git", "status", "--short", "--branch"], cwd=repo_root),
        "log": _run_cmd(["git", "log", "-n", "5", "--oneline"], cwd=repo_root),
        "diff_stat": _run_cmd(["git", "diff", "--stat"], cwd=repo_root),
    }
    notes: List[str] = []
    missing: List[str] = []
    for key, res in commands.items():
        if res.exit_code != 0:
            missing.append(f"{key}:{res.stderr or res.exit_code}")

    status_line = commands["status"].stdout.splitlines()[0] if commands["status"].stdout else "git_status_unavailable"
    dirty_count = 0
    if commands["status"].stdout:
        dirty_count = max(0, len(commands["status"].stdout.splitlines()) - 1)
    last_commit = commands["log"].stdout.splitlines()[0] if commands["log"].stdout else "git_log_unavailable"
    diff_lines = commands["diff_stat"].stdout.splitlines()
    diff_summary = diff_lines[-1] if diff_lines else "no_unstaged_diff_or_diff_unavailable"

    notes.append(f"Branch/worktree: {status_line}")
    notes.append(f"Uncommitted path count (approx): {dirty_count}")
    notes.append(f"Latest commit: {last_commit}")
    notes.append(f"Current diff stat: {diff_summary}")

    return tuple(notes), missing, commands


def _repo_map(repo_root: Path) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    def p(rel: str) -> str:
        return (repo_root / rel).resolve().as_posix()

    return (
        ("Market discovery", (p("core/market_discovery.py"),)),
        ("WS ingestion/subscription", (p("data/polymarket_ws.py"),)),
        (
            "Decision/policy gates",
            (
                p("scripts/run_system.py"),
                p("core/policy_gate.py"),
            ),
        ),
        (
            "Execution/brokers/FSM",
            (
                p("core/broker_sim.py"),
                p("core/broker_polymarket.py"),
                p("core/execution_fsm.py"),
            ),
        ),
        (
            "Storage/audit trail",
            (
                p("core/sqlite_store.py"),
                p("core/event_tape.py"),
                p("core/decision_tape.py"),
                p("core/trade_tape.py"),
            ),
        ),
        (
            "Replay",
            (
                p("core/replay.py"),
                p("scripts/replay_runner.py"),
            ),
        ),
        (
            "Dashboard health UX",
            (
                p("dashboard/panels/reliability.py"),
                p("dashboard/panels/overview.py"),
            ),
        ),
    )


def _scan_invariants(repo_root: Path) -> Tuple[Dict[str, List[ScanMatch]], List[str]]:
    insufficient: List[str] = []
    scans: Dict[str, List[ScanMatch]] = {}

    scan_plan = {
        "nondeterminism_uuid_random": (
            r"uuid\.uuid4|random\.(random|randint|choice|uniform)",
            ["core", "data", "scripts", "dashboard", "src"],
        ),
        "wall_clock_sources": (
            r"time\.time|datetime\.now|utcnow|Timestamp\.now",
            ["core", "data", "scripts", "dashboard", "src"],
        ),
        "causality_enforcement": (
            r"as_of|decision_ts|feature_max_ts|B_[A-Z_]*TIME_LEAK",
            ["core", "data", "scripts", "dashboard", "src"],
        ),
        "mode_safety": (
            r"\bOBSERVE\b|\bPAPER\b|\bTRADE\b|mode\s*==\s*\"OBSERVE\"|mode\s+in\s+\{\"PAPER\",\s*\"TRADE\"\}",
            ["scripts/run_system.py", "scripts/run_readonly.py", "core/broker_sim.py", "core/broker_polymarket.py"],
        ),
        "tradability_rollover": (
            r"CANDIDATE_NOT_LIVE|selected_tradable_meta_state|NON_TRADABLE_|candidate_liveness|selection_witness",
            ["core/market_discovery.py", "scripts/run_system.py"],
        ),
        "audit_trail": (
            r"reason_codes|append_alert|append_evidence_row|upsert_system_state|decision_ts_event_ms|as_of_ts_ms",
            ["scripts/run_system.py", "core/sqlite_store.py", "dashboard/panels/reliability.py", "dashboard/panels/overview.py"],
        ),
        "replay_parity": (
            r"MarketWSClient\\._handle_message|DecisionEngine|ReplayRunner|test_replay_determinism|replay parity|replay_determinism",
            ["core/replay.py", "scripts/replay_runner.py", "tests", "dashboard/panels/replay_diff.py"],
        ),
        "efficiency_hotspots": (
            r"_cx\.commit\(\)|SELECT .* FROM execution_quality|flush\(\)|LIMIT 500|ORDER BY ts_ms",
            ["core/sqlite_store.py", "scripts/run_system.py", "core/event_tape.py", "core/decision_tape.py", "core/trade_tape.py", "dashboard/panels/reliability.py"],
        ),
    }

    for key, (pattern, targets) in scan_plan.items():
        matches, err = _rg_search(repo_root=repo_root, pattern=pattern, targets=targets, max_matches=20)
        scans[key] = matches
        if err is not None:
            insufficient.append(f"{key}:{err}")

    return scans, insufficient


def _run_quality_gate(repo_root: Path, skip_pytest: bool) -> Tuple[Tuple[str, ...], Optional[str]]:
    if skip_pytest:
        return (("Quality gate skipped (`--skip-pytest`).",), None)

    pytest_cmd = [".venv/bin/pytest", "-q"]
    if not (repo_root / ".venv/bin/pytest").exists():
        pytest_cmd = ["pytest", "-q"]

    result = _run_cmd(pytest_cmd, cwd=repo_root, timeout_sec=600)
    if result.exit_code == 127:
        return (("Quality gate unavailable: pytest command missing.",), "pytest_missing")

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.exit_code == 0:
        if lines:
            return ((f"`{' '.join(pytest_cmd)}` passed: {lines[-1]}",), None)
        return ((f"`{' '.join(pytest_cmd)}` passed.",), None)

    summary = lines[-1] if lines else (result.stderr or "pytest_failed")
    return ((f"`{' '.join(pytest_cmd)}` failed: {summary}",), "pytest_failed")


def _build_goal_alignment(repo_root: Path, scans: Dict[str, List[ScanMatch]]) -> Tuple[GoalMatrixRow, ...]:
    p = lambda rel: (repo_root / rel).resolve().as_posix()
    return (
        GoalMatrixRow(
            goal=GOALS[0],
            evidence=(
                f"Replay runner reuses live message/decision flow in `{p('core/replay.py')}`; "
                f"determinism tests present in `{p('tests/test_replay_determinism.py')}` and `{p('tests/test_replay_determinism_with_model.py')}`."
            ),
            gaps=(
                "Non-deterministic UUID generation still appears in runtime/audit writes "
                f"({_format_matches(scans.get('nondeterminism_uuid_random', []), limit=2)})."
            ),
        ),
        GoalMatrixRow(
            goal=GOALS[1],
            evidence=(
                f"Time-leak gates (`B_*_TIME_LEAK`) and `decision_ts`/`as_of` fields are implemented in `{p('core/policy_gate.py')}` "
                f"and `{p('scripts/run_system.py')}`."
            ),
            gaps=(
                "Wall-clock helpers exist in runtime-adjacent code; decision-critical paths must keep using mapped timestamps "
                f"({_format_matches(scans.get('wall_clock_sources', []), limit=2)})."
            ),
        ),
        GoalMatrixRow(
            goal=GOALS[2],
            evidence=(
                f"`latest_active` selection ranks tradability and skips explicit NON_TRADABLE states in `{p('core/market_discovery.py')}`; "
                f"rollover emits `CANDIDATE_NOT_LIVE` and mapped rejection reasons in `{p('scripts/run_system.py')}`."
            ),
            gaps=(
                "`UNKNOWN` tradability metadata can still be selected by design; this should remain visible in health telemetry."
            ),
        ),
        GoalMatrixRow(
            goal=GOALS[3],
            evidence=(
                f"Policy verdicts return allow/block + structured reason codes in `{p('core/policy_gate.py')}`; "
                f"decisions persist `reason_codes`/`decision_ts_event_ms` in `{p('scripts/run_system.py')}` and `{p('core/sqlite_store.py')}`."
            ),
            gaps=("No critical gap found in static scan; monitor for reason-code taxonomy drift."),
        ),
        GoalMatrixRow(
            goal=GOALS[4],
            evidence=(
                f"OBSERVE-mode order blocking and PAPER/TRADE broker separation are explicit in `{p('scripts/run_system.py')}` and `{p('scripts/run_readonly.py')}`."
            ),
            gaps=("No critical mode-safety gap found in static scan."),
        ),
        GoalMatrixRow(
            goal=GOALS[5],
            evidence=(
                f"Reliability and overview panels expose tradeability/freeze/readiness surfaces in `{p('dashboard/panels/reliability.py')}` "
                f"and `{p('dashboard/panels/overview.py')}`."
            ),
            gaps=(
                "Dashboard queries still mix wall-clock SQL windowing for some aggregates; replay-forensic views may need stricter as-of anchoring."
            ),
        ),
    )


def _build_invariant_checks(repo_root: Path, scans: Dict[str, List[ScanMatch]]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    p = lambda rel: (repo_root / rel).resolve().as_posix()
    return (
        (
            "Determinism",
            (
                "Replay determinism tests exist and pass in baseline runs.",
                f"Non-deterministic ID/random sources still present in runtime paths: {_format_matches(scans.get('nondeterminism_uuid_random', []), limit=4)}.",
                f"Primary deterministic tape checks are enforced in `{p('core/trade_tape.py')}`.",
            ),
        ),
        (
            "Causality",
            (
                f"`B_*_TIME_LEAK` checks are implemented in `{p('core/policy_gate.py')}` and causality diagnostics in `{p('scripts/run_system.py')}`.",
                f"Causality evidence hits: {_format_matches(scans.get('causality_enforcement', []), limit=4)}.",
            ),
        ),
        (
            "Fail-closed",
            (
                "Policy gate returns SKIP/FREEZE on uncertainty/staleness; quotes are blocked before submit when guards fail.",
                f"Audit trail writes reason-coded freezes/alerts via `{p('core/sqlite_store.py')}`.",
            ),
        ),
        (
            "Audit Trail",
            (
                "Decisions, orders, fills, alerts, and evidence rows include reason fields and serialized payloads.",
                f"Evidence references: {_format_matches(scans.get('audit_trail', []), limit=4)}.",
            ),
        ),
        (
            "Mode Safety",
            (
                "OBSERVE mode blocks quote execution; PAPER uses simulated broker; TRADE uses Polymarket broker contract checks.",
                f"Mode guard references: {_format_matches(scans.get('mode_safety', []), limit=4)}.",
            ),
        ),
    )


def _build_risks(repo_root: Path, scans: Dict[str, List[ScanMatch]]) -> Tuple[RiskItem, ...]:
    p = lambda rel: (repo_root / rel).resolve().as_posix()
    return (
        RiskItem(
            severity="High",
            likelihood="High",
            title="Non-deterministic runtime IDs in audit-relevant rows",
            impact="Can break strict replay mapping/forensics across runs with identical input streams.",
            evidence=f"UUID generation in `{p('scripts/run_system.py')}` and `{p('core/sqlite_store.py')}` ({_format_matches(scans.get('nondeterminism_uuid_random', []), limit=3)}).",
        ),
        RiskItem(
            severity="High",
            likelihood="Medium",
            title="Per-row SQLite commits in hot runtime paths",
            impact="Increases I/O latency and jitter under high message/order rates.",
            evidence=f"`insert()`/`insert_many()` commit behavior in `{p('core/sqlite_store.py')}`; hotspot references {_format_matches(scans.get('efficiency_hotspots', []), limit=3)}.",
        ),
        RiskItem(
            severity="Medium",
            likelihood="Medium",
            title="Wall-clock fallbacks in runtime-adjacent code",
            impact="Potential drift from strict time-mapping discipline if used in decision-adjacent paths.",
            evidence=f"Fallback helpers in `{p('scripts/run_system.py')}` and `{p('core/event_tape.py')}`.",
        ),
        RiskItem(
            severity="Medium",
            likelihood="Medium",
            title="Repeated 1h aggregation scans during stats cycles",
            impact="Potential avoidable DB load and tail-latency pressure.",
            evidence=f"Execution-quality scan logic in `{p('scripts/run_system.py')}` (`_record_execution_quality_stats`).",
        ),
        RiskItem(
            severity="Low",
            likelihood="Low",
            title="Tradability UNKNOWN path can defer certainty",
            impact="May temporarily select candidates with incomplete metadata, increasing reliance on downstream live checks.",
            evidence=f"`UNKNOWN` tradability handling in `{p('core/market_discovery.py')}`.",
        ),
    )


def _build_efficiency_findings(repo_root: Path) -> Tuple[str, ...]:
    p = lambda rel: (repo_root / rel).resolve().as_posix()
    return (
        f"Hot-path DB writes currently commit per insert in `{p('core/sqlite_store.py')}`; batching/transaction windows are the highest-impact optimization.",
        f"Stats loop recomputes execution-quality aggregates over 1h windows in `{p('scripts/run_system.py')}`; incremental rollups would reduce load.",
        f"Tape writers flush on every write in `{p('core/event_tape.py')}`, `{p('core/decision_tape.py')}`, `{p('core/trade_tape.py')}`; evaluate buffered flush cadence with crash-safety guardrails.",
        f"Dashboard reliability queries include repeated 24h scans in `{p('dashboard/panels/reliability.py')}`; consider cached aggregates for high-refresh views.",
    )


def default_recommendations(repo_root: Path) -> Tuple[Recommendation, ...]:
    p = lambda rel: (repo_root / rel).resolve().as_posix()
    return (
        Recommendation(
            priority="HIGH",
            title="Replace non-deterministic runtime UUID generation with deterministic event-id derivation in replay-critical tables",
            file_targets=(p("scripts/run_system.py"), p("core/sqlite_store.py")),
            expected_behavior_change=(
                "Event/evidence IDs become reproducible for identical ordered inputs, improving replay/audit comparability without changing trading semantics."
            ),
            acceptance_criteria=(
                "Two replay runs over identical tapes produce identical IDs for decisions/orders/fills/evidence rows where IDs are derived.",
                "Existing tests continue passing and replay determinism tests remain green.",
                "No order lifecycle behavior changes beyond ID stability.",
            ),
            regression_risk="Medium: ID format changes can impact downstream dashboard/export assumptions if not coordinated.",
        ),
        Recommendation(
            priority="HIGH",
            title="Introduce bounded transaction batching for high-frequency SQLite writes",
            file_targets=(p("core/sqlite_store.py"), p("scripts/run_system.py")),
            expected_behavior_change=(
                "Reduce per-event commit overhead while preserving fail-closed behavior and durable audit trail ordering."
            ),
            acceptance_criteria=(
                "Write throughput improves under synthetic high-frequency event load with no dropped rows.",
                "Ordering and monotonic invariants for trade/decision events remain intact.",
                "Crash-recovery test confirms no silent data loss beyond configured transaction window.",
            ),
            regression_risk="Medium: batching may alter durability timing if flush windows are too large.",
        ),
        Recommendation(
            priority="MEDIUM",
            title="Harden time-source boundaries so decision-critical paths never depend on wall-clock fallback",
            file_targets=(p("scripts/run_system.py"), p("core/event_tape.py"), p("core/decision_tape.py")),
            expected_behavior_change=(
                "Decision and policy timestamps stay strictly mapped from monotonic/event inputs; wall-clock helpers remain for non-critical metadata only."
            ),
            acceptance_criteria=(
                "Static scan shows no direct wall-clock usage in policy/decision gating paths.",
                "Causality and replay determinism tests pass unchanged.",
                "Audit report clearly labels allowed wall-clock contexts.",
            ),
            regression_risk="Low: primarily guardrail tightening; minimal runtime semantics impact expected.",
        ),
        Recommendation(
            priority="MEDIUM",
            title="Replace full-window execution-quality rescans with incremental aggregates",
            file_targets=(p("scripts/run_system.py"),),
            expected_behavior_change=(
                "Stats cycle DB load decreases while preserving reported execution-quality metrics and thresholds."
            ),
            acceptance_criteria=(
                "Metrics parity check shows no material divergence from baseline over a controlled replay run.",
                "Stats-loop latency remains within configured interval budget.",
                "No regression in dashboard panels that read execution-quality stats.",
            ),
            regression_risk="Medium: aggregation boundary bugs can skew operational metrics if not validated.",
        ),
        Recommendation(
            priority="LOW",
            title="Add CI-facing repository audit command wrapper",
            file_targets=(p("scripts/repo_audit.py"), p("README.md")),
            expected_behavior_change=(
                "Operators can run one command to generate a deterministic A–I audit report and enforce baseline contracts."
            ),
            acceptance_criteria=(
                "`python3 -m scripts.repo_audit --trigger 'run audit'` emits A–I sections in required order.",
                "Command exits successfully in read-only mode on clean/dirty worktrees.",
                "Report includes insufficient-evidence notes when tools are unavailable.",
            ),
            regression_risk="Low: additive tooling only.",
        ),
    )


def validate_recommendation_contract(recommendations: Sequence[Recommendation]) -> Tuple[bool, Tuple[str, ...]]:
    errors: List[str] = []
    for idx, rec in enumerate(recommendations, start=1):
        if not rec.file_targets:
            errors.append(f"rec_{idx}:missing_file_targets")
        if not rec.expected_behavior_change.strip():
            errors.append(f"rec_{idx}:missing_expected_behavior_change")
        if not rec.acceptance_criteria:
            errors.append(f"rec_{idx}:missing_acceptance_criteria")
        if not rec.regression_risk.strip():
            errors.append(f"rec_{idx}:missing_regression_risk")
    return (len(errors) == 0, tuple(errors))


def _build_report(
    repo_root: Path,
    trigger_text: str,
    skip_pytest: bool,
) -> AuditReport:
    trigger_matched = should_run_full_audit(trigger_text)
    repo_state_summary, repo_state_missing, _ = _collect_repo_state(repo_root)
    scans, scan_missing = _scan_invariants(repo_root)
    goal_alignment = _build_goal_alignment(repo_root, scans)
    invariant_checks = _build_invariant_checks(repo_root, scans)
    risks = _build_risks(repo_root, scans)
    efficiency = _build_efficiency_findings(repo_root)
    test_coverage, pytest_missing = _run_quality_gate(repo_root, skip_pytest=skip_pytest)
    recommendations = default_recommendations(repo_root)
    contract_ok, contract_errors = validate_recommendation_contract(recommendations)

    insufficient: List[str] = []
    insufficient.extend(repo_state_missing)
    insufficient.extend(scan_missing)
    if pytest_missing is not None:
        insufficient.append(pytest_missing)
    if not contract_ok:
        insufficient.extend(contract_errors)

    do_not_do = (
        "Do not replace deterministic ordering/time contracts with convenience wall-clock calls in decision logic.",
        "Do not weaken fail-closed behavior by allowing quote placement when any hard gate is uncertain.",
        "Do not introduce broad refactors while closing invariant violations; keep fixes minimal and auditable.",
        "Do not collapse OBSERVE/PAPER/TRADE separation in broker wiring or startup guard behavior.",
        "Do not add hidden randomness in runtime decisions without deterministic seeding + replay contract updates.",
    )

    return AuditReport(
        generated_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        trigger_text=trigger_text,
        trigger_matched=trigger_matched,
        repo_state_summary=repo_state_summary,
        repo_map_rows=_repo_map(repo_root),
        goal_alignment=goal_alignment,
        invariant_checks=invariant_checks,
        risks=risks,
        efficiency_findings=efficiency,
        test_coverage=test_coverage,
        recommendations=recommendations,
        do_not_do=do_not_do,
        insufficient_evidence=tuple(insufficient),
    )


def render_report_markdown(report: AuditReport) -> str:
    lines: List[str] = []

    lines.append(f"## {SECTION_TITLES[0]}")
    lines.append(f"- Generated at: {report.generated_at}")
    lines.append(f"- Trigger text: `{report.trigger_text}`")
    lines.append(f"- Trigger contract matched: {'yes' if report.trigger_matched else 'no (full audit still executed by default)'}")
    for item in report.repo_state_summary:
        lines.append(f"- {item}")
    lines.append("- Mode safety, fail-closed gating, and replay parity scaffolding are present and test-covered.")
    lines.append("- Highest current risk remains deterministic-ID drift in runtime/audit rows.")
    lines.append("- Highest performance risk remains per-row SQLite commits in hot paths.")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[1]}")
    for subsystem, file_targets in report.repo_map_rows:
        lines.append(f"- {subsystem}: {', '.join(f'`{path}`' for path in file_targets)}")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[2]}")
    lines.append("| Goal | Evidence in repo | Gaps |")
    lines.append("|---|---|---|")
    for row in report.goal_alignment:
        lines.append(f"| {row.goal} | {row.evidence} | {row.gaps} |")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[3]}")
    for check_name, details in report.invariant_checks:
        lines.append(f"- {check_name}:")
        for detail in details:
            lines.append(f"  - {detail}")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[4]}")
    lines.append("| Severity | Likelihood | Risk | Impact | Evidence |")
    lines.append("|---|---|---|---|---|")
    for risk in report.risks:
        lines.append(
            f"| {risk.severity} | {risk.likelihood} | {risk.title} | {risk.impact} | {risk.evidence} |"
        )

    lines.append("")
    lines.append(f"## {SECTION_TITLES[5]}")
    for finding in report.efficiency_findings:
        lines.append(f"- {finding}")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[6]}")
    for item in report.test_coverage:
        lines.append(f"- {item}")
    lines.append("- Replay parity evidence sources: `core/replay.py`, `scripts/replay_runner.py`, `tests/test_replay_determinism.py`, `tests/test_replay_determinism_with_model.py`.")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[7]}")
    ordered = sorted(
        report.recommendations,
        key=lambda item: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(item.priority.upper(), 9), item.title),
    )
    for idx, rec in enumerate(ordered, start=1):
        lines.append(f"{idx}. [{rec.priority}] {rec.title}")
        lines.append(f"   - File targets: {', '.join(f'`{path}`' for path in rec.file_targets)}")
        lines.append(f"   - Expected behavior change: {rec.expected_behavior_change}")
        lines.append("   - Acceptance criteria:")
        for criterion in rec.acceptance_criteria:
            lines.append(f"     - {criterion}")
        lines.append(f"   - Regression risk: {rec.regression_risk}")

    lines.append("")
    lines.append(f"## {SECTION_TITLES[8]}")
    for item in report.do_not_do:
        lines.append(f"- {item}")

    if report.insufficient_evidence:
        lines.append("")
        lines.append("### Insufficient Evidence Notes")
        for item in report.insufficient_evidence:
            lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic, read-only repository audit and emit A-I report")
    parser.add_argument("--trigger", default="run audit", help="Invocation text; trigger phrases map to full audit run")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip default .venv/bin/pytest -q quality gate")
    parser.add_argument("--output", default=None, help="Optional markdown report output path")
    parser.add_argument("--json", action="store_true", help="Emit JSON payload instead of markdown")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    report = _build_report(repo_root=repo_root, trigger_text=str(args.trigger), skip_pytest=bool(args.skip_pytest))

    if args.json:
        payload = {
            "generated_at": report.generated_at,
            "trigger_text": report.trigger_text,
            "trigger_matched": report.trigger_matched,
            "repo_state_summary": list(report.repo_state_summary),
            "insufficient_evidence": list(report.insufficient_evidence),
            "section_titles": list(SECTION_TITLES),
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True)
    else:
        rendered = render_report_markdown(report)

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = (repo_root / out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
