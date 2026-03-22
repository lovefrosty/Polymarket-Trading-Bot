from __future__ import annotations
import argparse, json, os, signal, time, tracemalloc, sys, runpy
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib.parse import urlsplit
from pathlib import Path

# --- INSTRUMENTATION DEFAULTS ---
FORBIDDEN_KEYWORDS = [b"place_order", b"create_order", b"buy", b"sell", b"side", b"c:orders"]
FORBIDDEN_PATHS = ("/order", "/orders", "/trade", "/secret")

@dataclass
class CallStats:
    count: int = 0
    latencies: List[float] = field(default_factory=list)
    def add(self, elapsed: float) -> None:
        self.count += 1
        self.latencies.append(elapsed)
    def summary(self) -> Dict[str, Any]:
        if not self.latencies: return {"count": 0, "p50": None, "p99": None}
        vals = sorted(self.latencies)
        return {"count": self.count, "p50": vals[int(len(vals)*0.5)], "p99": vals[int(len(vals)*0.99)]}

# --- PROBE LOGIC ---
def main():
    args = _parse_args()
    tracemalloc.start()
    start_snap = tracemalloc.take_snapshot()
    stats, url_hits, forbidden = {}, {}, []
    
    _patch_requests(stats, url_hits, forbidden)
    _patch_websockets(stats, url_hits, forbidden)

    print(f"[*] Probe active. Root: {os.getcwd()}")
    print(f"[*] Executing: {args.script_path}")
    _schedule_stop(args.runtime_sec)

    # RECONSTRUCT ENVIRONMENT
    original_argv = sys.argv[:]
    original_path = sys.path[:]
    
    sys.path.insert(0, os.getcwd()) 
    sys.argv = [args.script_path] + args.extra_args
    
    try:
        runpy.run_path(args.script_path, run_name="__main__")
    except (KeyboardInterrupt, SystemExit): 
        pass
    except Exception as e: 
        print(f"[!] Target Script Crashed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        sys.argv = original_argv
        sys.path = original_path

    end_snap = tracemalloc.take_snapshot()
    diff = end_snap.compare_to(start_snap, 'lineno')
    
    output = {
        "urls": url_hits,
        "forbidden_activity": forbidden,
        "requests": {k: v.summary() for k, v in stats.items()},
        "memory_leak_top_5": [{"file": s.traceback[0].filename, "line": s.traceback[0].lineno, "size_diff": s.size_diff} for s in diff[:5]]
    }
    Path(args.output).write_text(json.dumps(output, indent=2))
    print(f"[*] Audit complete. Stats saved to {args.output}")

def _scan_payload(data: Any, forbidden: List[str], source: str):
    body = b""
    try:
        if isinstance(data, bytes): body = data
        elif isinstance(data, str): body = data.encode()
        elif isinstance(data, dict): body = json.dumps(data).encode()
    except: return
    for kw in FORBIDDEN_KEYWORDS:
        if kw in body.lower():
            forbidden.append(f"{source} found {kw.decode()}")

def _patch_requests(stats, hits, forbidden):
    try:
        import requests
        orig = requests.request
        def hook(method, url, **kwargs):
            if "json" in kwargs: _scan_payload(kwargs["json"], forbidden, f"HTTP {method}")
            start = time.perf_counter()
            try: return orig(method, url, **kwargs)
            finally:
                dur = (time.perf_counter()-start)*1000
                key = f"HTTP {method} {urlsplit(url).path}"
                stats.setdefault(key, CallStats()).add(dur)
                hits[key] = hits.get(key, 0) + 1
                if any(b in url for b in FORBIDDEN_PATHS): forbidden.append(f"URL {url}")
        requests.request = hook
        requests.get = lambda u, **k: requests.request("GET", u, **k)
        requests.post = lambda u, **k: requests.request("POST", u, **k)
    except ImportError: pass

def _patch_websockets(stats, hits, forbidden):
    try:
        import websockets
        orig_connect = websockets.connect
        async def hook(url, **kwargs):
            start = time.perf_counter()
            proto = await orig_connect(url, **kwargs)
            dur = (time.perf_counter()-start)*1000
            key = f"WS {urlsplit(url).netloc}"
            stats.setdefault(key, CallStats()).add(dur)
            hits[key] = hits.get(key, 0) + 1
            return proto
        websockets.connect = hook
    except ImportError: pass

def _schedule_stop(sec):
    import threading
    def _kill(): 
        time.sleep(sec)
        print("\n[*] Probe time limit reached. Stopping...")
        os.kill(os.getpid(), signal.SIGINT)
    threading.Thread(target=_kill, daemon=True).start()

def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--script-path", default="scripts/run_readonly.py") 
    p.add_argument("--output", default="audit/audit_runtime_stats.json")
    p.add_argument("--runtime-sec", type=int, default=30)
    p.add_argument("--extra-args", nargs=argparse.REMAINDER, default=[])
    return p.parse_args()

if __name__ == "__main__":
    main()