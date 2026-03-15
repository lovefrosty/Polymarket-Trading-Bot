# PAD-17 — Position Merge Follow-Up

Status: parallel-safe after PAD-20 work pauses

Allowed paths:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/positions.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_positions.py

Do not touch:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/runner.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/user_feed.py when another agent owns PAD-20

Goal:
- finish merge semantics only
- keep merge accounting in PositionTracker
- no broker or runner edits in this task
