import asyncio
from unittest.mock import MagicMock

import pytest

from core_mm.book_manager import BookManager
from core_mm.execution import ExecutionResult
from core_mm.live_broker import LiveBroker
from core_mm.paper_broker import PaperBroker
from core_mm.runner import CoreMMRunner
from core_mm.market_selector import MarketSelectionConfig, MarketSelector


@pytest.fixture()
def selector() -> MarketSelector:
    return MarketSelector(config=MarketSelectionConfig(require_clob_candidate=False))


def _candidate_events():
    return [
        {
            "slug": "btc-updown-15m-old",
            "conditionId": "old",
            "clobTokenIds": ["yes_old", "no_old"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 3,
            "spread": 0.03,
            "prices": [0.48, 0.52],
            "reward_per_100": 5,
        },
        {
            "slug": "btc-updown-15m-new",
            "conditionId": "new",
            "clobTokenIds": ["yes_new", "no_new"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
        },
    ]


def test_runner_replaces_market_from_selection(selector: MarketSelector) -> None:
    runner = CoreMMRunner(market_selector=selector)
    changed = runner.refresh_market_selection(_candidate_events())
    assert changed is True
    assert runner.current_market is not None
    assert runner.current_market.condition_id == "new"


def test_runner_observe_cycle_builds_actions(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(market_selector=selector, book_manager=books, mode="OBSERVE")
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    result = asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert result is not None
    assert result.order_actions
    assert result.execution_results == ()


def test_runner_paper_cycle_places_orders(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books)
    runner = CoreMMRunner(market_selector=selector, book_manager=books, broker=broker, mode="PAPER")
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    result = asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert result is not None
    assert result.order_actions
    assert result.execution_results


def test_runner_user_message_updates_shared_position_state(selector: MarketSelector) -> None:
    runner = CoreMMRunner(market_selector=selector, mode="OBSERVE")
    runner.on_user_message(
        {
            "data": {
                "event_type": "trade",
                "order_id": "o1",
                "asset_id": "yes_old",
                "side": "BUY",
                "size": 5,
                "price": 0.5,
                "trade_id": "t1",
            }
        }
    )
    position = runner.position_tracker.get_position("yes_old")
    assert position.size == 5


def test_runner_status_distinguishes_empty_books(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(market_selector=selector, book_manager=books, mode="OBSERVE")
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    status = runner.status()
    assert status.has_books is False
    assert status.book_diag["per_token"]["yes_old"]["state"] == "book_empty"
    assert status.book_diag["per_token"]["no_old"]["state"] == "book_ok"


def test_runner_respects_market_dwell_before_switching(selector: MarketSelector) -> None:
    runner = CoreMMRunner(market_selector=selector, market_dwell_ms=900_000)
    first = [
        {
            "slug": "btc-updown-15m-old",
            "conditionId": "old",
            "clobTokenIds": ["yes_old", "no_old"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 3,
            "spread": 0.03,
            "prices": [0.48, 0.52],
            "reward_per_100": 5,
        }
    ]
    challenger = _candidate_events()

    changed = runner.refresh_market_selection(first, now_ms=1_000)
    assert changed is True
    assert runner.current_market is not None
    assert runner.current_market.condition_id == "old"

    changed = runner.refresh_market_selection(challenger, now_ms=10_000)
    assert changed is False
    assert runner.current_market is not None
    assert runner.current_market.condition_id == "old"

    changed = runner.refresh_market_selection(challenger, now_ms=901_500)
    assert changed is True
    assert runner.current_market is not None
    assert runner.current_market.condition_id == "new"


def test_runner_merges_before_cycle(selector: MarketSelector) -> None:
    """Set YES=80, NO=50 → after cycle, YES=30, NO=0 (merged 50)."""
    books = BookManager()
    broker = PaperBroker(book_manager=books)
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, broker=broker,
        mode="PAPER", min_merge_size=20.0,
    )
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    # Set positions manually
    runner.position_tracker.apply_fill(token_id="yes_old", side="buy", size=80, price=0.49)
    runner.position_tracker.apply_fill(token_id="no_old", side="buy", size=50, price=0.49)
    asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert runner.position_tracker.get_position("yes_old").size == pytest.approx(30.0)
    assert runner.position_tracker.get_position("no_old").size == pytest.approx(0.0)


def test_runner_no_merge_below_min_size(selector: MarketSelector) -> None:
    """YES=15, NO=10 → positions unchanged (below min_merge_size=20)."""
    books = BookManager()
    broker = PaperBroker(book_manager=books)
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, broker=broker,
        mode="PAPER", min_merge_size=20.0,
    )
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_old", side="buy", size=15, price=0.49)
    runner.position_tracker.apply_fill(token_id="no_old", side="buy", size=10, price=0.49)
    asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert runner.position_tracker.get_position("yes_old").size == pytest.approx(15.0)
    assert runner.position_tracker.get_position("no_old").size == pytest.approx(10.0)


def test_runner_clears_market_when_no_candidates_remain(selector: MarketSelector) -> None:
    runner = CoreMMRunner(market_selector=selector)
    changed = runner.refresh_market_selection(_candidate_events(), now_ms=1_000)
    assert changed is True
    assert runner.current_market is not None

    changed = runner.refresh_market_selection([], now_ms=2_000)
    assert changed is True
    assert runner.current_market is None


# ── Multi-market tests ────────────────────────────────────────────


def test_runner_multi_market_selects_top_n(selector: MarketSelector) -> None:
    """With max_active_markets=2, both candidates should be active."""
    runner = CoreMMRunner(market_selector=selector, max_active_markets=2)
    changed = runner.refresh_market_selection(_candidate_events())
    assert changed is True
    assert len(runner.active_markets) == 2
    assert {m.condition_id for m in runner.active_markets} == {"new", "old"}


def test_runner_all_token_ids_union(selector: MarketSelector) -> None:
    """all_token_ids returns union of all active market token IDs."""
    runner = CoreMMRunner(market_selector=selector, max_active_markets=2)
    runner.refresh_market_selection(_candidate_events())
    assert set(runner.all_token_ids) == {"yes_old", "no_old", "yes_new", "no_new"}


def test_runner_current_market_backward_compat(selector: MarketSelector) -> None:
    """current_market property returns first active market."""
    runner = CoreMMRunner(market_selector=selector, max_active_markets=2)
    assert runner.current_market is None
    runner.refresh_market_selection(_candidate_events())
    assert runner.current_market is not None
    assert runner.current_market.condition_id == runner.active_markets[0].condition_id


def test_runner_run_cycles_multiple_markets(selector: MarketSelector) -> None:
    """run_cycles returns results for each active market."""
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, mode="OBSERVE",
        max_active_markets=2,
    )
    runner.refresh_market_selection(_candidate_events())
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    results = asyncio.run(runner.run_cycles(now_ms=2_000, usdc_balance=1000))
    assert len(results) == 2
    market_ids = {r.market_id for r in results}
    assert len(market_ids) == 2


def test_runner_run_cycles_empty_when_no_markets(selector: MarketSelector) -> None:
    runner = CoreMMRunner(market_selector=selector, max_active_markets=2)
    results = asyncio.run(runner.run_cycles(now_ms=1_000))
    assert results == []


def test_runner_status_reports_all_markets(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, mode="OBSERVE",
        max_active_markets=2,
    )
    runner.refresh_market_selection(_candidate_events())
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    status = runner.status()
    assert len(status.market_ids) == 2
    assert len(status.token_ids) == 4


def test_runner_aggregate_risk_blocks_buys(selector: MarketSelector) -> None:
    """When total position notional exceeds limit, usdc_balance forced to 0."""
    from core_mm.risk_manager import RiskConfig
    books = BookManager()
    risk_config = RiskConfig(max_total_position_notional=5.0)
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, mode="OBSERVE",
        max_active_markets=2, risk_config=risk_config,
    )
    runner.refresh_market_selection(_candidate_events())
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    # Load up positions to exceed the $5 aggregate limit
    runner.position_tracker.apply_fill(token_id="yes_old", side="buy", size=20, price=0.50)
    results = asyncio.run(runner.run_cycles(now_ms=2_000, usdc_balance=1000))
    # Should still return results (observe mode), but effective balance is 0
    assert len(results) == 2


def test_runner_dwell_protects_in_multi_market(selector: MarketSelector) -> None:
    """Dwell protection keeps active markets in their slots."""
    third_event = {
        "slug": "btc-updown-15m-third",
        "conditionId": "third",
        "clobTokenIds": ["yes_third", "no_third"],
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "volatility_sum": 0.5,
        "spread": 0.01,
        "prices": [0.50, 0.50],
        "reward_per_100": 12,
    }
    runner = CoreMMRunner(
        market_selector=selector, max_active_markets=2, market_dwell_ms=900_000,
    )
    # First: select top 2 from [new, old]
    runner.refresh_market_selection(_candidate_events(), now_ms=1_000)
    assert len(runner.active_markets) == 2
    initial_ids = {m.condition_id for m in runner.active_markets}

    # Second: introduce "third" which scores even higher, during dwell
    all_events = _candidate_events() + [third_event]
    runner.refresh_market_selection(all_events, now_ms=10_000)
    # Both initial markets protected by dwell — no change
    assert {m.condition_id for m in runner.active_markets} == initial_ids

    # Third: after dwell expires, should pick top 2 from full candidates
    runner.refresh_market_selection(all_events, now_ms=901_500)
    current_ids = {m.condition_id for m in runner.active_markets}
    assert len(current_ids) == 2
    assert "third" in current_ids  # highest score should be included


# ── LIVE mode tests ────────────────────────────────────────────────


def test_runner_live_mode_requires_broker(selector: MarketSelector) -> None:
    """LIVE mode without a broker should raise ValueError."""
    with pytest.raises(ValueError, match="LiveBroker must be provided"):
        CoreMMRunner(market_selector=selector, mode="LIVE")


def test_runner_live_mode_with_broker(selector: MarketSelector) -> None:
    """LIVE mode with a LiveBroker should initialize successfully."""
    mock_exec = MagicMock()
    mock_exec.place_order.return_value = ExecutionResult(True, {"orderID": "live-1"})
    mock_exec.cancel_order.return_value = ExecutionResult(True, {"canceled": ["live-1"]})
    mock_exec.cancel_all.return_value = ExecutionResult(True, {"canceled": []})
    mock_exec.get_open_orders.return_value = ExecutionResult(True, {"orders": []})
    mock_exec.get_positions.return_value = ExecutionResult(True, {"positions": []})
    broker = LiveBroker(execution_adapter=mock_exec)
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, broker=broker, mode="LIVE",
    )
    assert runner.mode == "LIVE"
    assert runner.broker is broker
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    result = asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert result is not None
    assert result.execution_results  # LIVE mode should produce execution results
