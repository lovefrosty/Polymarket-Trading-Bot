import json
import sqlite3
import time
from pathlib import Path

ROOT = Path('/Users/padraigjudge/Desktop/paperfirst-paper-20260310T220520Z')
DB = ROOT / 'runtime.db'
META = ROOT / 'meta'
STATUS_JSON = META / 'soak_gate_status.json'
STATUS_MD = META / 'soak_gate_status.md'
STATE_JSON = META / 'soak_gate_state.json'
SAMPLE_SECS = 300
CLEAN_WINDOW_MS = 6 * 60 * 60 * 1000


def scalar(cur, q):
    row = cur.execute(q).fetchone()
    return row[0] if row else None


def latest_ref_ages_ms():
    logs = ROOT / 'logs'
    files = sorted(logs.glob('reference_*.jsonl'))
    now = int(time.time() * 1000)
    if not files:
        return {'spot': None, 'perp': None}
    latest = files[-1]
    last = {}
    with latest.open() as f:
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
            if src in ('spot', 'perp') and isinstance(ts, int):
                last[src] = ts
    return {k: (None if k not in last else now - last[k]) for k in ('spot', 'perp')}


def load_state():
    if STATE_JSON.exists():
        try:
            return json.loads(STATE_JSON.read_text())
        except Exception:
            pass
    return {
        'started_ts_ms': int(time.time() * 1000),
        'clean_window_started_ts_ms': None,
        'last_counts': None,
        'last_sample_ts_ms': None,
    }


def save_state(state):
    STATE_JSON.write_text(json.dumps(state, indent=2, sort_keys=True))


def main():
    state = load_state()
    while True:
        now = int(time.time() * 1000)
        status = {
            'ts_ms': now,
            'run_root': str(ROOT),
            'status': 'waiting',
            'clean_window_started_ts_ms': state.get('clean_window_started_ts_ms'),
            'clean_window_elapsed_ms': None,
            'clean_window_target_ms': CLEAN_WINDOW_MS,
            'commit_blocked': True,
            'blocking_reason': None,
        }
        try:
            con = sqlite3.connect(str(DB))
            cur = con.cursor()
            counts = {
                'orders': scalar(cur, "select count(*) from orders") or 0,
                'fills': scalar(cur, "select count(*) from fills") or 0,
                'quote': scalar(cur, "select count(*) from decisions where action='QUOTE'") or 0,
                'skip': scalar(cur, "select count(*) from decisions where action='SKIP'") or 0,
                'freeze': scalar(cur, "select count(*) from decisions where action='FREEZE'") or 0,
                'rollover_intent': scalar(cur, "select count(*) from logs where msg='rollover_intent'") or 0,
                'rollover_commit': scalar(cur, "select count(*) from logs where msg='rollover_commit'") or 0,
                'rollover_abort_discovery_error': scalar(cur, "select count(*) from logs where msg='rollover_abort_discovery_error'") or 0,
                'rollover_health_freeze': scalar(cur, "select count(*) from logs where msg='rollover_health_freeze'") or 0,
            }
            ages = {
                'book_age_ms': scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from market_data_book"),
                'decision_age_ms': scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from decisions"),
                'log_age_ms': scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from logs"),
            }
            con.close()
            ref_ages = latest_ref_ages_ms()
            last_counts = state.get('last_counts') or counts
            deltas = {k: counts[k] - last_counts.get(k, 0) for k in counts}
            healthy = (
                (ages['book_age_ms'] is not None and ages['book_age_ms'] < 5000)
                and (ages['decision_age_ms'] is not None and ages['decision_age_ms'] < 5000)
                and (ref_ages['spot'] is not None and ref_ages['spot'] < 10000)
                and (ref_ages['perp'] is not None and ref_ages['perp'] < 10000)
            )
            no_new_failures = deltas['rollover_abort_discovery_error'] == 0 and deltas['rollover_health_freeze'] == 0
            forward_progress = deltas['orders'] > 0 and deltas['quote'] > 0
            if healthy and no_new_failures and forward_progress:
                if state.get('clean_window_started_ts_ms') is None:
                    state['clean_window_started_ts_ms'] = now
                elapsed = now - state['clean_window_started_ts_ms']
                status['clean_window_started_ts_ms'] = state['clean_window_started_ts_ms']
                status['clean_window_elapsed_ms'] = elapsed
                if elapsed >= CLEAN_WINDOW_MS:
                    status['status'] = 'clean_window_met'
                    status['commit_blocked'] = False
                else:
                    status['status'] = 'clean_window_active'
                    status['blocking_reason'] = 'WAITING_FOR_6H_CLEAN_WINDOW'
            else:
                state['clean_window_started_ts_ms'] = None
                status['status'] = 'not_clean'
                reasons = []
                if not healthy:
                    reasons.append('FRESHNESS_OR_REFERENCE_BROKEN')
                if not no_new_failures:
                    reasons.append('NEW_ROLLOVER_FAILURES')
                if not forward_progress:
                    reasons.append('NO_ORDER_FLOW_PROGRESS')
                status['blocking_reason'] = ','.join(reasons) or 'UNKNOWN'
            status['counts'] = counts
            status['deltas_since_last_sample'] = deltas
            status['ages_ms'] = ages
            status['reference_ages_ms'] = ref_ages
            state['last_counts'] = counts
            state['last_sample_ts_ms'] = now
            STATUS_JSON.write_text(json.dumps(status, indent=2, sort_keys=True))
            STATUS_MD.write_text(
                '\n'.join([
                    '# PAPER Soak Gate',
                    '',
                    f"- status: `{status['status']}`",
                    f"- commit_blocked: `{status['commit_blocked']}`",
                    f"- blocking_reason: `{status['blocking_reason']}`",
                    f"- clean_window_elapsed_ms: `{status['clean_window_elapsed_ms']}`",
                    f"- book_age_ms: `{ages['book_age_ms']}`",
                    f"- decision_age_ms: `{ages['decision_age_ms']}`",
                    f"- spot_age_ms: `{ref_ages['spot']}`",
                    f"- perp_age_ms: `{ref_ages['perp']}`",
                    f"- orders: `{counts['orders']}` (delta `{deltas['orders']}`)",
                    f"- fills: `{counts['fills']}` (delta `{deltas['fills']}`)",
                    f"- quote: `{counts['quote']}` (delta `{deltas['quote']}`)",
                    f"- skip: `{counts['skip']}` (delta `{deltas['skip']}`)",
                    f"- freeze: `{counts['freeze']}` (delta `{deltas['freeze']}`)",
                    f"- rollover_intent: `{counts['rollover_intent']}` (delta `{deltas['rollover_intent']}`)",
                    f"- rollover_commit: `{counts['rollover_commit']}` (delta `{deltas['rollover_commit']}`)",
                    f"- rollover_abort_discovery_error: `{counts['rollover_abort_discovery_error']}` (delta `{deltas['rollover_abort_discovery_error']}`)",
                    f"- rollover_health_freeze: `{counts['rollover_health_freeze']}` (delta `{deltas['rollover_health_freeze']}`)",
                ])
            )
            save_state(state)
        except Exception as exc:
            status['status'] = 'monitor_error'
            status['blocking_reason'] = str(exc)
            STATUS_JSON.write_text(json.dumps(status, indent=2, sort_keys=True))
            STATUS_MD.write_text(f"# PAPER Soak Gate\n\n- status: `monitor_error`\n- error: `{exc}`\n")
        time.sleep(SAMPLE_SECS)


if __name__ == '__main__':
    main()
