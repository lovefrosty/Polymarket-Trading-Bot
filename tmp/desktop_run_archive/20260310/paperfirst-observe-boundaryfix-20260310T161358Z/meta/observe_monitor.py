import json, sqlite3, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'runtime.db'
META = ROOT / 'meta'
while True:
    now = int(time.time() * 1000)
    out = {'ts_ms': now, 'status': 'no_db'}
    if DB.exists():
        conn = sqlite3.connect(str(DB)); cur = conn.cursor(); stats = {}
        for tbl in ['market_data_book','decisions','logs']:
            try:
                cur.execute(f'SELECT COUNT(*), MAX(ts_ms) FROM {tbl}')
                count, mx = cur.fetchone(); stats[tbl] = {'count': count, 'max_ts_ms': mx, 'age_ms': None if mx is None else now - int(mx)}
            except Exception as exc:
                stats[tbl] = {'error': str(exc)}
        conn.close()
        out = {'ts_ms': now, 'status': 'ok', 'db': stats}
    (META / 'checkpoint_latest.json').write_text(json.dumps(out, indent=2) + '\n', encoding='utf-8')
    time.sleep(30)
