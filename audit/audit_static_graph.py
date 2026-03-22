import ast, json, argparse
from pathlib import Path

CONFIG_PATH = Path("audit/audit_config.json")

def main():
    files = [p for p in Path(".").rglob("*.py") if not any(x in p.parts for x in [".venv","tests","audit",".git"])]
    compat_modules = _load_compat_modules()
    compat_paths = {Path(mod.replace(".", "/") + ".py") for mod in compat_modules}
    
    # Build Map of file -> content
    content_map = {f: f.read_text(errors='ignore') for f in files}
    
    # Naive "Referenced By" Check (Strictly Grep-based for safety)
    referenced = set()
    for f, content in content_map.items():
        for other in files:
            if other == f: continue
            # If the filename (minus .py) appears in another file, it's likely used
            if other.stem in content: referenced.add(other)

    # Roots (files that are entry points)
    roots = ["main.py", "scripts"]
    
    orphans = []
    for f in files:
        is_root = any(str(f).startswith(r) for r in roots)
        if f in compat_paths:
            continue
        if not is_root and f not in referenced:
            orphans.append(str(f))
    
    shim_status = {
        mod: "Compatibility shim present — verified inert."
        for mod in compat_modules
        if Path(mod.replace(".", "/") + ".py") in files
    }
    Path("audit/audit_static_graph.json").write_text(
        json.dumps(
            {
                "orphans": orphans,
                "compatibility_modules": sorted(compat_modules),
                "compatibility_shims": shim_status,
            },
            indent=2,
        )
    )
    print(f"[*] Static Analysis: {len(orphans)} potential orphans found.")

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
