from __future__ import annotations

import json

from brokers.ibkr.models import BrokerAccountSnapshot, BrokerPosition, PortfolioSnapshot, SyncHealth
from dashboard.panels.trading_workstation import (
    energy_rows,
    idea_rows,
    load_json_contract,
    macro_decision_rows,
    position_rows,
    quote_direction,
    quote_tiles_html,
    risk_decision_rows,
    risk_warnings,
)


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="dashboard-test",
        fetched_at_ms=1_000,
        health=SyncHealth("healthy", 1_000, True, True),
        accounts=[BrokerAccountSnapshot("DU1", 1_000, net_liquidation=100_000)],
        positions=[
            BrokerPosition("DU1", 1_000, 1, "SPY", "SPY", "STK", "USD", 100, 400, 500, 50_000, 10_000, 0),
            BrokerPosition("DU1", 1_000, 2, "TLT", "TLT", "STK", "USD", 100, 90, 100, 10_000, 1_000, 0),
        ],
    )


def test_load_json_contract_rejects_non_object_root(tmp_path) -> None:
    path = tmp_path / "payload.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    payload, error = load_json_contract(path)

    assert payload is None
    assert error == "invalid: root must be an object"


def test_idea_rows_marks_expired_signals_and_keeps_evidence() -> None:
    payload = {
        "signals": [
            {
                "agent_id": "trend",
                "symbol": "SPY",
                "score": 0.7,
                "confidence": 0.8,
                "as_of_ts_ms": 1_000,
                "expires_ts_ms": 2_000,
                "evidence_links": ["research://walk-forward/spy"],
            }
        ]
    }

    active = idea_rows(payload, now_ms=1_500)[0]
    expired = idea_rows(payload, now_ms=2_500)[0]

    assert active["Status"] == "ACTIVE"
    assert expired["Status"] == "EXPIRED"
    assert active["Evidence"] == "research://walk-forward/spy"


def test_position_rows_and_risk_warnings_show_concentration() -> None:
    snapshot = _snapshot()
    rows = position_rows(snapshot)
    warnings = risk_warnings(snapshot, {"max_drawdown": -0.05, "return_observations": 30})

    assert rows[0]["Symbol"] == "SPY"
    assert rows[0]["Weight"] == 0.5
    assert warnings == ["SPY exceeds 25% of gross position exposure"]


def test_quote_tiles_only_mark_real_value_changes() -> None:
    assert quote_direction(None, 10) == "flat"
    assert quote_direction(10, 11) == "up"
    assert quote_direction(10, 9) == "down"
    assert quote_direction(10, 10) == "flat"

    rendered = quote_tiles_html(
        [{"label": "WTI", "value": "$75.00", "detail": "+1.2%"}],
        {"WTI": "up"},
    )
    assert "tick-up" in rendered
    assert "WTI" in rendered


def test_macro_and_energy_contracts_remain_evidence_first() -> None:
    macro = {
        "features": {
            "policy_stance": "RESTRICTIVE",
            "expected_policy_direction": "EASING",
            "yield_curve_regime": "STEEPENING",
            "liquidity_direction": "DRAINING",
            "inflation_regime": "COOLING",
            "volatility_regime": "ELEVATED",
            "credit_stress": "LOW",
        }
    }
    energy = {
        "markets": {"WTI": {"value": 75.0, "change": 1.2, "source": "IBKR", "as_of_ts_ms": 1_000}},
        "fundamentals": {"US crude stocks": {"value": -3.1, "source": "EIA"}},
    }

    decisions = macro_decision_rows(macro)
    evidence = energy_rows(energy)

    assert decisions[0]["Evidence"] == "Stance: RESTRICTIVE | Direction: EASING"
    assert decisions[0]["Portfolio Decision"].startswith("Duration")
    assert evidence[0]["Category"] == "MARKETS"
    assert evidence[0]["Source"] == "IBKR"
    assert evidence[1]["Indicator"] == "US crude stocks"


def test_risk_decision_rows_surface_blocked_and_review_states() -> None:
    rows = risk_decision_rows(
        _snapshot(),
        {"var_value": 1_000, "cvar_value": 1_500, "return_observations": 10},
        {"margin_utilization": 0.2},
    )

    assert rows[0]["Status"] == "REVIEW"
    assert rows[1]["Status"] == "LOW SAMPLE"
    assert rows[-1]["Status"] == "BLOCKED"
