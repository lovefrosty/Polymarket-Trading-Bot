# PAD-18 — Main Loop Integration

Status: ready for isolated parallel work

Allowed paths:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/runner.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/market_ws_adapter.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/paper_broker.py
- /Users/padraigjudge/Desktop/Polymarket Bot/scripts/run_core_mm.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_runner.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_market_ws_adapter.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_paper_broker.py

Do not touch:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/market_selector.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/user_feed.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/positions.py
- legacy runtime files under /Users/padraigjudge/Desktop/Polymarket Bot/scripts/run_system.py or /Users/padraigjudge/Desktop/Polymarket Bot/data/

Goal:
- keep the standalone runner single-process and cheap
- wire market replacement cleanly
- no legacy rollover state reuse
- preserve green /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm
