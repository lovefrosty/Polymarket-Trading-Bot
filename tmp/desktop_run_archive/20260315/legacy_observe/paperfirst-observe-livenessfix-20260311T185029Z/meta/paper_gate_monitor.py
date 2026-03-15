from __future__ import annotations
import json, sqlite3, time
from pathlib import Path

ROOT = Path('/Users/padraigjudge/Desktop/paperfirst-observe-livenessfix-20260311T185029Z')
DB = ROOT / 'runtime.db'
LOGS_DIR = ROOT / 'logs'
META = ROOT / 'meta'
STATUS_JSON = META / 'paper_gate_status.json'
STATUS_MD = META / 'paper_gate_status.md'
FRESH_MS = 5_000
REFRESH_MS = 10_000
SAMPLE_SECS = 300

FAILURE_MSGS = {
    'rollover_abort_discovery_error',
    'rollover_health_freeze',
    'rollover_abort_candidate_not_live',
}


def latest_reference_ages_ms() -> dict[str, int | None]:
    files = sorted(LOGS_DIR.glob('reference_*.jsonl'))
    now = int(time.time() * 1000)
    last = {'spot': None, 'perp': None}
    for path in files[-2:]:
        try:
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    raw = row.get('raw', {}) if isinstance(row, dict) else {}
                    src = raw.get('source') or row.get('source')
                    ts = row.get('t_event_ms') or raw.get('t_event_ms')
                    if src in last and isinstance(ts, int):
                        last[src] = ts if last[src] is None else max(last[src], ts)
        except FileNotFoundError:
            continue
    return {k: None if v is None else now - v for k, v in last.items()}


def compute() -> dict:
    now = int(time.time() * 1000)
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    def scalar(q, params=()):
        row = cur.execute(q, params).fetchone()
        return row[0] if row else None

    ages = {
        'market_data_book_age_ms': scalar("select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from market_data_book"),
        'decisions_age_ms': scalar("select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from decisions"),
        'logs_age_ms': scalar("select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from logs"),
    }
    counts = {
        'rollover_intent': int(scalar("select count(*) from logs where msg='rollover_intent'") or 0),
        'rollover_commit': int(scalar("select count(*) from logs where msg='rollover_commit'") or 0),
        'rollover_abort_switch': int(scalar("select count(*) from logs where msg='rollover_abort_switch'") or 0),
        'rollover_abort_discovery_error': int(scalar("select count(*) from logs where msg='rollover_abort_discovery_error'") or 0),
        'rollover_health_freeze': int(scalar("select count(*) from logs where msg='rollover_health_freeze'") or 0),
        'rollover_abort_candidate_not_live': int(scalar("select count(*) from logs where msg='rollover_abort_candidate_not_live'") or 0),
    }
    last_failure_ts = scalar(
        "select max(ts_ms) from logs where msg in ('rollover_abort_discovery_error','rollover_health_freeze','rollover_abort_candidate_not_live','rollover_abort_switch')"
    )
    commits_since_failure = int(scalar("select count(*) from logs where msg='rollover_commit' and (? is null or ts_ms > ?)", (last_failure_ts, last_failure_ts)) or 0)
    refs = latest_reference_ages_ms()
    con.close()

    stale = any(v is None or int(v) >= FRESH_MS for v in ages.values())
    refs_ok = all(refs[k] is not None and int(refs[k]) < REFRESH_MS for k in ('spot', 'perp'))
    no_failures = counts['rollover_abort_discovery_error'] == 0 and counts['rollover_health_freeze'] == 0 and counts['rollover_abort_candidate_not_live'] == 0
    switch_ok = counts['rollover_abort_switch'] <= counts['rollover_commit']
    gate = 'OPEN' if (not stale and refs_ok and no_failures and switch_ok and commits_since_failure >= 2) else 'CLOSED'

    reason_parts = []
    if stale:
        reason_parts.append('STALE_DB')
    if not refs_ok:
        reason_parts.append('REFERENCE_NOT_FRESH')
    if not no_failures:
        reason_parts.append('ROLLOVER_FAILURES_PRESENT')
    if not switch_ok:
        reason_parts.append('ABORT_SWITCH_OUTPACING_COMMITS')
    if commits_since_failure < 2:
        reason_parts.append('NEED_2_CLEAN_COMMITS_SINCE_LAST_FAILURE')

    status = {
        'ts_ms': now,
        'paper_gate': gate,
        'blocking_reason': ','.join(reason_parts) if reason_parts else None,
        'last_failure_ts_ms': last_failure_ts,
        'clean_rollover_commits_since_last_failure': commits_since_failure,
        'freshness_ms': ages,
        'reference_freshness_ms': refs,
        'rollover_counts': counts,
        'run_root': str(ROOT),
    }
    return status


def write_status(status: dict) -> None:
    STATUS_JSON.write_text(json.dumps(status, indent=2, sort_keys=True))
    md = [
        '# PAPER Gate',
        '',
        f"- paper_gate: `{status['paper_gate']}`",
        f"- blocking_reason: `{status['blocking_reason']}`",
        f"- clean_rollover_commits_since_last_failure: `{status['clean_rollover_commits_since_last_failure']}`",
        f"- last_failure_ts_ms: `{status['last_failure_ts_ms']}`",
        f"- market_data_book_age_ms: `{status['freshness_ms']['market_data_book_age_ms']}`",
        f"- decisions_age_ms: `{status['freshness_ms']['decisions_age_ms']}`",
        f"- logs_age_ms: `{status['freshness_ms']['logs_age_ms']}`",
        f"- spot_age_ms: `{status['reference_freshness_ms']['spot']}`",
        f"- perp_age_ms: `{status['reference_freshness_ms']['perp']}`",
        f"- rollover_intent: `{status['rollover_counts']['rollover_intent']}`",
        f"- rollover_commit: `{status['rollover_counts']['rollover_commit']}`",
        f"- rollover_abort_switch: `{status['rollover_counts']['rollover_abort_switch']}`",
        f"- rollover_abort_discovery_error: `{status['rollover_counts']['rollover_abort_discovery_error']}`",
        f"- rollover_health_freeze: `{status['rollover_counts']['rollover_health_freeze']}`",
        f"- rollover_abort_candidate_not_live: `{status['rollover_counts']['rollover_abort_candidate_not_live']}`",
    ]
    STATUS_MD.write_text('\n'.join(md) + '\n')


def main():
    META.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            status = compute()
            write_status(status)
        except Exception as exc:
            STATUS_JSON.write_text(json.dumps({'paper_gate': 'CLOSED', 'blocking_reason': f'MONITOR_ERROR:{exc}', 'run_root': str(ROOT)}, indent=2, sort_keys=True))
            STATUS_MD.write_text(f'# PAPER Gate\n\n- paper_gate: `CLOSED`\n- blocking_reason: `MONITOR_ERROR:{exc}`\n')
        time.sleep(SAMPLE_SECS)

if __name__ == '__main__':
    main()
