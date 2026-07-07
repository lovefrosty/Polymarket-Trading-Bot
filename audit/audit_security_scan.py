import json
import re
from pathlib import Path

CONFIG_PATH = Path("audit/audit_config.json")

def main():
    patterns = {
        "private_key": r"0x[a-fA-F0-9]{64}",
        "mnemonic": r"\b(seed|mnemonic)\b.{0,20}\b[a-z]{3,}\b",
        "trading_flag": r"enable_trading\s*=\s*True",
        "aws_key": r"AKIA[0-9A-Z]{16}"
    }
    compat_modules = _load_compat_modules()
    compat_paths = {Path(mod.replace(".", "/") + ".py") for mod in compat_modules}
    hits = []
    shims = []
    for f in Path(".").rglob("*"):
        if f.suffix not in [".py", ".env", ".yaml", ".json"] or "audit" in f.parts: continue
        if f in compat_paths:
            shims.append(str(f))
            continue
        txt = f.read_text(errors="ignore")
        for name, pat in patterns.items():
            if re.search(pat, txt): hits.append(f"{name} in {f}")
    
    payload = {"hits": hits, "compatibility_shims": shims}
    Path("audit/audit_security.json").write_text(json.dumps(payload, indent=2))
    if hits: print(f"[!] Security Risks Found: {len(hits)}")
    else: print("[*] Security Clean.")

if __name__ == "__main__": main()


def _load_compat_modules() -> list[str]:
    if not CONFIG_PATH.exists():
        return []
    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except Exception:
        return []
    modules = raw.get("compatibility_modules") if isinstance(raw, dict) else []
    if not isinstance(modules, list):
        return []
    return [str(item) for item in modules if item]
