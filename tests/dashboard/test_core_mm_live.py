from __future__ import annotations

import pandas as pd

from dashboard import data_access as da
from dashboard.panels import core_mm_live


def test_humanize_reason_codes_prefers_operator_language() -> None:
    assert da.humanize_reason_codes("book_empty,spread_too_wide") == "order book empty; spread too wide"


def test_build_operator_brief_prefers_flatten_only_state() -> None:
    snapshot = {
        "quoteable": True,
        "selected_reason": "quoteable_book",
        "state": "healthy",
        "control_state": {"flatten_only_mode": True, "kill_switch_enabled": False},
        "runner": {},
        "active_market_health": {},
    }

    brief = core_mm_live._build_operator_brief(snapshot, pd.DataFrame())

    assert brief["headline"] == "Flattening inventory only"
    assert "reduces existing exposure" in brief["summary"]


def test_build_operator_brief_uses_latest_decision_context() -> None:
    snapshot = {
        "quoteable": False,
        "selected_reason": "book_empty",
        "state": "degraded",
        "control_state": {},
        "runner": {},
        "active_market_health": {},
    }
    explainer = pd.DataFrame(
        [
            {
                "decision_summary": "Freeze",
                "plain_english": "Waiting: order book empty",
                "reason_codes": "book_empty",
            }
        ]
    )

    brief = core_mm_live._build_operator_brief(snapshot, explainer)

    assert brief["headline"] == "Trading is frozen"
    assert "order book empty" in brief["summary"]


def test_build_operator_brief_calls_out_zero_fill_waiting_state() -> None:
    snapshot = {
        "quoteable": False,
        "selected_reason": "spread_too_wide",
        "state": "degraded",
        "fills": 0,
        "total_pnl": 0.0,
        "control_state": {},
        "runner": {},
        "active_market_health": {},
    }

    brief = core_mm_live._build_operator_brief(snapshot, pd.DataFrame())

    assert brief["headline"] == "Waiting on market conditions"
    assert "No fills recorded in this session yet" in brief["summary"]
    assert "spread is too wide" in brief["summary"].lower()


def test_selection_market_label_prefers_ticker_title_or_market() -> None:
    assert core_mm_live._selection_market_label({"ticker": "KXBTC-TEST"}) == "KXBTC-TEST"
    assert core_mm_live._selection_market_label({"title": "BTC range"}) == "BTC range"
    assert core_mm_live._selection_market_label("KXBTC-RAW") == "KXBTC-RAW"


def test_expiry_badge_uses_runtime_time_to_expiry_when_present() -> None:
    badge = core_mm_live._expiry_badge("KXBTC-NO-TIMESTAMP", time_to_expiry_ms=90_000)

    assert "to close" in badge
    assert "1.5m" in badge


def test_cluster_gap_questions_translate_backend_gaps_into_operator_questions() -> None:
    questions = core_mm_live._cluster_gap_questions(
        [
            "cluster control_state",
            "cluster hedge action label",
            "cluster hedge ratio",
        ]
    )

    assert questions[0] == "Which cluster-level gate is binding right now?"
    assert questions[1] == "Did the runner want SKEW, HEDGE, or UNWIND here?"
    assert questions[2] == "What hedge ratio was the runner targeting?"


def test_selection_gap_questions_translate_multi_market_visibility_gaps() -> None:
    questions = core_mm_live._selection_gap_questions(
        [
            "blocking market id",
            "blocking cluster id",
            "blocking reason",
        ]
    )

    assert questions[0] == "Which active market blocked this candidate?"
    assert questions[1] == "Which event cluster suppressed this candidate?"
    assert questions[2] == "What multi-market rule caused the suppression?"


def test_format_size_limiter_shows_primary_and_chain() -> None:
    text = core_mm_live._format_size_limiter(
        {"buy_limiter": "affordability", "buy_limiters": "risk_budget,affordability"},
        "buy",
    )

    assert text == "affordability [risk budget -> affordability]"
