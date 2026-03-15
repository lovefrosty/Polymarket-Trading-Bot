# PAD-19 — Market Selector Hardening

Status: active issue in progress

Allowed paths:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/market_selector.py
- /Users/padraigjudge/Desktop/Polymarket Bot/tests/core_mm/test_market_selector.py

Do not touch:
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/runner.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/market_ws_adapter.py
- /Users/padraigjudge/Desktop/Polymarket Bot/core_mm/user_feed.py
- legacy discovery in /Users/padraigjudge/Desktop/Polymarket Bot/core/market_discovery.py

Goal:
- keep selector self-contained
- support live Gamma payloads for BTC 15m selection
- keep contradictory active/closed metadata ambiguous, not hard-rejected
- no legacy rescue workflow logic
