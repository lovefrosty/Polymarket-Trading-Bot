from __future__ import annotations

from dashboard import app


def test_runtime_time_to_window_end_prefers_runtime_health_payload() -> None:
    snapshot = {
        "active_market_health": {"time_to_expiry_ms": 90_000},
        "selection": {},
        "runner": {},
    }

    assert app._runtime_time_to_window_end(snapshot) == "01:30"


def test_runtime_time_to_window_end_returns_closed_for_expired_market() -> None:
    snapshot = {
        "active_market_health": {"time_to_expiry_ms": 0},
        "selection": {},
        "runner": {},
    }

    assert app._runtime_time_to_window_end(snapshot) == "closed"
