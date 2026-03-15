# PAD-20 — User Feed And Position State

Status: active issue in progress

Allowed paths:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/user_feed.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/positions.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_user_feed.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_positions.py

Do not touch:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/runner.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/market_selector.py
- legacy user WS transport in /Users/padraigjudge/Desktop/Polymarket Bot/data/polymarket_ws.py unless explicitly assigned

Goal:
- keep fill/order state ownership in one place
- make user WS parsing update shared PositionTracker cleanly
- avoid duplicate order-state logic across runner and feed
