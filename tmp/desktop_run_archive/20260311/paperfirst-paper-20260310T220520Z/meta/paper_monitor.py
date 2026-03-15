import json, sqlite3, time
from pathlib import Path
root = Path(__file__).resolve().parent.parent
_db = root / 'runtime.db'
out = root / 'meta' / 'checkpoint_latest.json'

def scalar(cur, q):
    row = cur.execute(q).fetchone()
    return row[0] if row else None

while True:
    payload = {'ts_ms': int(time.time()*1000), 'status': 'starting'}
    if _db.exists():
        try:
            con = sqlite3.connect(str(_db))
            cur = con.cursor()
            payload = {
                'ts_ms': int(time.time()*1000),
                'book_count': scalar(cur, 'select count(*) from market_data_book'),
                'decision_count': scalar(cur, 'select count(*) from decisions'),
                'log_count': scalar(cur, 'select count(*) from logs'),
                'book_age_ms': scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from market_data_book"),
                'decision_age_ms': scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from decisions"),
                'rollover_commit_count': scalar(cur, "select count(*) from logs where msg='rollover_commit'"),
                'rollover_intent_count': scalar(cur, "select count(*) from logs where msg='rollover_intent'"),
                'quote_count': scalar(cur, "select count(*) from decisions where action='QUOTE'"),
                'skip_count': scalar(cur, "select count(*) from decisions where action='SKIP'"),
                'freeze_count': scalar(cur, "select count(*) from decisions where action='FREEZE'"),
            }
            con.close()
        except Exception as exc:
            payload = {'ts_ms': int(time.time()*1000), 'status': 'error', 'error': str(exc)}
    out.write_text(json.dumps(payload, indent=2))
    time.sleep(60)
