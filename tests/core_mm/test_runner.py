import asyncio

import pytest

from core_mm.book_manager import BookManager
from core_mm.paper_broker import PaperBroker
from core_mm.runner import CoreMMRunner
from core_mm.market_selector import MarketSelectionConfig, MarketSelector


@pytest.fixture()
def selector() -> MarketSelector:
    return MarketSelector(config=MarketSelectionConfig())


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
