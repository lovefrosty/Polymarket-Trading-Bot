import json, sys
from pathlib import Path

def main():
    print("[*] Validating Contracts...")
    errors = []
    
    # 1. DecisionTape
    decisions = list(Path("logs").glob("**/decision_*.jsonl"))
    if not decisions: errors.append("No DecisionTape found")
    else:
        # Check first 100 lines for sanity
        for line in decisions[0].read_text().splitlines()[:100]:
            try:
                row = json.loads(line)
                if not (0 <= row.get("p_market", -1) <= 1): errors.append(f"Price OOB: {row.get('p_market')}")
                if "book_stale" not in row: errors.append("Missing book_stale")
            except: pass

    # 2. Discovery Summary
    summaries = list(Path("logs").glob("**/discovery_summary.json"))
    if not summaries: errors.append("No discovery_summary found")
    
    Path("audit/audit_contract.json").write_text(json.dumps({"status": "FAIL" if errors else "PASS", "errors": errors}, indent=2))
    if errors: print(f"[!] Contract Failures: {errors}")
    else: print("[*] Contracts Valid.")

if __name__ == "__main__": main()
