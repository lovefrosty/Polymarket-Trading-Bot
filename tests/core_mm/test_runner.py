import asyncio
from unittest.mock import MagicMock

import pytest

from core_mm.book_manager import BookManager
from core_mm.execution import ExecutionResult
from core_mm.hedge_engine import HedgeCovarianceMetrics, HedgeEngine, HedgeExecutionMetrics, HedgePairRelation
from core_mm.live_broker import LiveBroker
from core_mm.risk_manager import RiskConfig
from core_mm.paper_broker import PaperBroker
from core_mm.runner import CoreMMRunner
from core_mm.market_selector import MarketCandidate, MarketSelectionConfig, MarketSelector


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


def _apply_mid_snapshot(books: BookManager, token_id: str, mid: float, *, size: float = 150.0, ts_ms: int) -> None:
    bid = max(0.01, round(float(mid) - 0.01, 4))
    ask = min(0.99, round(float(mid) + 0.01, 4))
    books.apply_snapshot(token_id, bids=[(bid, size)], asks=[(ask, size)], ts_ms=ts_ms)


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
    assert status.selection["selected_reason"] == "current_market"
    assert status.selection["selected_market"]["slug"] == "btc-updown-15m-old"
    assert status.selection["portfolio_selection"]["launch_scope"] == "single_market"
    assert status.selection["portfolio_selection"]["max_active_markets"] == 1
    assert status.active_market_health["quoteability_state"] in {"book_blocked", "book_unavailable"}


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


def test_runner_multi_market_selection_diagnostics_block_adjacent_cluster_neighbor(selector: MarketSelector) -> None:
    class FakeSelector:
        def __init__(self, candidates):
            self._candidates = candidates
            self.last_selection_report = {}

        def select_from_events(self, events, now_ts=None):
            return list(self._candidates)

    candidates = [
        MarketCandidate(
            reference_symbol="BTC",
            slug="KXBTC-26MAR2322-B70650",
            condition_id="a",
            token_ids=("yes_a", "no_a"),
            outcomes=("yes", "no"),
            reward_per_100=9.0,
            volatility_sum=1.0,
            spread=0.02,
            mid_price=0.50,
            active=True,
            closed=False,
            accepting_orders=True,
            tick_size=0.01,
            max_incentive_spread=None,
            min_incentive_size=None,
            end_ts_ms=None,
            end_ts_source=None,
            active_now=True,
            tradable=True,
            clob_candidate=True,
            score=9.0,
            raw={"event_ticker": "KXBTC-26MAR2322"},
        ),
        MarketCandidate(
            reference_symbol="BTC",
            slug="KXBTC-26MAR2322-B70750",
            condition_id="b",
            token_ids=("yes_b", "no_b"),
            outcomes=("yes", "no"),
            reward_per_100=8.0,
            volatility_sum=1.0,
            spread=0.02,
            mid_price=0.50,
            active=True,
            closed=False,
            accepting_orders=True,
            tick_size=0.01,
            max_incentive_spread=None,
            min_incentive_size=None,
            end_ts_ms=None,
            end_ts_source=None,
            active_now=True,
            tradable=True,
            clob_candidate=True,
            score=8.0,
            raw={"event_ticker": "KXBTC-26MAR2322"},
        ),
        MarketCandidate(
            reference_symbol="ETH",
            slug="KXETH-26MAR2322-B3500",
            condition_id="c",
            token_ids=("yes_c", "no_c"),
            outcomes=("yes", "no"),
            reward_per_100=7.0,
            volatility_sum=1.0,
            spread=0.02,
            mid_price=0.50,
            active=True,
            closed=False,
            accepting_orders=True,
            tick_size=0.01,
            max_incentive_spread=None,
            min_incentive_size=None,
            end_ts_ms=None,
            end_ts_source=None,
            active_now=True,
            tradable=True,
            clob_candidate=True,
            score=7.0,
            raw={"event_ticker": "KXETH-26MAR2322"},
        ),
    ]
    runner = CoreMMRunner(market_selector=FakeSelector(candidates), max_active_markets=2, mode="PAPER")
    changed = runner.refresh_market_selection([{}], now_ms=1_000)

    assert changed is True
    assert {market.condition_id for market in runner.active_markets} == {"a", "c"}
    report = runner.status().selection["portfolio_selection"]
    decisions = {row["market_id"]: row for row in report["candidate_decisions"]}
    assert decisions["KXBTC-26MAR2322-B70750"]["reason"] == "same_cluster_adjacent_bucket_blocked"
    assert decisions["KXETH-26MAR2322-B3500"]["reason"] == "different_cluster"


def test_runner_all_token_ids_union(selector: MarketSelector) -> None:
    """all_token_ids returns union of all active market token IDs."""
    runner = CoreMMRunner(market_selector=selector, max_active_markets=2)
    runner.refresh_market_selection(_candidate_events())
    assert set(runner.all_token_ids) == {"yes_old", "no_old", "yes_new", "no_new"}


def test_runner_kalshi_btc_hedge_search_universe_keeps_quote_universe_unchanged() -> None:
    class FakeSelector:
        def __init__(self, candidates):
            self._candidates = candidates
            self.last_selection_report = {}

        def select_from_events(self, events, now_ts=None):
            return list(self._candidates)

    candidates = [
        MarketCandidate(
            reference_symbol="BTC",
            slug="KXBTC-26MAR2322-B70650",
            condition_id="a",
            token_ids=("yes_a", "no_a"),
            outcomes=("yes", "no"),
            reward_per_100=9.0,
            volatility_sum=1.0,
            spread=0.02,
            mid_price=0.50,
            active=True,
            closed=False,
            accepting_orders=True,
            tick_size=0.01,
            max_incentive_spread=None,
            min_incentive_size=None,
            end_ts_ms=None,
            end_ts_source=None,
            active_now=True,
            tradable=True,
            clob_candidate=True,
            score=9.0,
            raw={"event_ticker": "KXBTC-26MAR2322"},
        ),
        MarketCandidate(
            reference_symbol="BTC",
            slug="KXBTC-26MAR2322-B70750",
            condition_id="b",
            token_ids=("yes_b", "no_b"),
            outcomes=("yes", "no"),
            reward_per_100=8.0,
            volatility_sum=1.0,
            spread=0.02,
            mid_price=0.50,
            active=True,
            closed=False,
            accepting_orders=True,
            tick_size=0.01,
            max_incentive_spread=None,
            min_incentive_size=None,
            end_ts_ms=None,
            end_ts_source=None,
            active_now=True,
            tradable=True,
            clob_candidate=True,
            score=8.0,
            raw={"event_ticker": "KXBTC-26MAR2322"},
        ),
        MarketCandidate(
            reference_symbol="ETH",
            slug="KXETH-26MAR2322-B3500",
            condition_id="c",
            token_ids=("yes_c", "no_c"),
            outcomes=("yes", "no"),
            reward_per_100=7.0,
            volatility_sum=1.0,
            spread=0.02,
            mid_price=0.50,
            active=True,
            closed=False,
            accepting_orders=True,
            tick_size=0.01,
            max_incentive_spread=None,
            min_incentive_size=None,
            end_ts_ms=None,
            end_ts_source=None,
            active_now=True,
            tradable=True,
            clob_candidate=True,
            score=7.0,
            raw={"event_ticker": "KXETH-26MAR2322"},
        ),
    ]
    runner = CoreMMRunner(market_selector=FakeSelector(candidates), max_active_markets=1, mode="PAPER")

    changed = runner.refresh_market_selection([{}], now_ms=1_000)

    assert changed is True
    assert {market.condition_id for market in runner.active_markets} == {"a"}
    assert set(runner.all_token_ids) == {"yes_a", "no_a"}
    assert {market.condition_id for market in runner.hedge_search_markets} == {"a", "b"}
    assert set(runner.hedge_search_token_ids) == {"yes_a", "no_a", "yes_b", "no_b"}


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
    assert "clusters" in status.cluster_exposure


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


def test_runner_event_exposure_cap_blocks_neighbor_bucket_buys(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        mode="OBSERVE",
        max_active_markets=2,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=120, price=0.50)
    results = asyncio.run(runner.run_cycles(now_ms=2_000, usdc_balance=1_000))
    buy_quotes = [
        quote
        for result in results
        for quote in result.desired_quotes.values()
        if quote.side == "buy"
    ]
    assert buy_quotes == []


def test_runner_cluster_exposure_snapshot_groups_related_markets(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        mode="OBSERVE",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
    )
    now_ms = 2_000_000_000_000
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
            "endTime": int((now_ms + 120_000) / 1000),
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
            "endTime": int((now_ms + 300_000) / 1000),
        },
    ]
    runner.refresh_market_selection(same_event, now_ms=now_ms)
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=now_ms - 1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=10, price=0.50)
    runner.position_tracker.apply_fill(token_id="no_b", side="buy", size=4, price=0.50)

    portfolio_risk = runner._portfolio_risk_snapshot(usdc_balance=1_000.0)
    cluster_payload = runner._build_cluster_exposure_state(now_ms=now_ms, portfolio_risk=portfolio_risk)["payload"]

    assert cluster_payload["cluster_count"] == 1
    cluster = cluster_payload["clusters"][0]
    assert cluster["cluster_id"] == "BTC-HOURLY-1"
    assert cluster["market_count"] == 2
    assert cluster["active_market_count"] == 2
    assert cluster["yes_exposure_notional"] == pytest.approx(5.0)
    assert cluster["no_exposure_notional"] == pytest.approx(2.0)
    assert cluster["gross_exposure"] == pytest.approx(7.0)
    assert cluster["net_yes_exposure_notional"] == pytest.approx(3.0)
    assert cluster["time_to_expiry_ms"] == 120_000


def test_runner_cluster_cap_blocks_even_when_market_cap_would_not(selector: MarketSelector) -> None:
    books = BookManager()
    risk_config = RiskConfig(max_market_exposure_pct=0.50, max_event_exposure_pct=0.05)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        mode="OBSERVE",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
        use_allocated_equity_for_risk=True,
        risk_config=risk_config,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=100, price=0.50)

    results = asyncio.run(runner.run_cycles(now_ms=2_000, usdc_balance=1_000.0))
    buy_quotes = [
        quote
        for result in results
        for quote in result.desired_quotes.values()
        if quote.side == "buy"
    ]
    assert buy_quotes == []
    cluster = runner.status().cluster_exposure["clusters"][0]
    assert cluster["gross_exposure"] == pytest.approx(50.0)
    assert cluster["remaining_event_exposure_notional"] == pytest.approx(0.0)


def test_runner_cluster_stale_unwind_suppresses_new_entries_in_sibling_market(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=500.0,
        safe_risk_profile="500",
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "KXBTC-26MAR2322",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "KXBTC-26MAR2322",
        },
    ]
    runner.refresh_market_selection(same_event, now_ms=1_000)
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=10, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)

    results = asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=500.0))
    sibling_quotes = [
        quote
        for result in results
        if result.market_id == "btc-updown-15m-b"
        for quote in result.desired_quotes.values()
        if quote.side == "buy"
    ]

    assert sibling_quotes == []
    cluster = runner.status().cluster_exposure["clusters"][0]
    assert cluster["new_entries_suppressed"] is True
    assert cluster["new_entry_block_reason"] == "correlated_market_stale"


def test_runner_paper_cluster_hedge_targets_related_market(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
        safe_risk_profile="1000",
        hedge_covariance_enabled=False,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    books.apply_snapshot("yes_a", bids=[(0.45, 50)], asks=[(0.55, 50)], ts_ms=19_000)
    books.apply_snapshot("no_a", bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=19_000)
    books.apply_snapshot("yes_b", bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=19_000)
    books.apply_snapshot("no_b", bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=19_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=80, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    results = asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=1_000.0))

    cluster_hedge = runner.status().cluster_hedge
    assert cluster_hedge["enabled"] is True
    cluster_plan = cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "HEDGE"
    assert cluster_plan["control_state"] == "HEDGE_ACTIVE"
    assert cluster_plan["hedge_market_id"] == "btc-updown-15m-b"
    hedge_directives = cluster_plan["token_directives"]
    assert any(
        directive["token_id"] == "no_b" and directive["action"] == "HEDGE"
        for directive in hedge_directives
    )
    assert results


def test_runner_proof_only_cluster_hedge_searches_adjacent_buckets(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    proof_selector = MarketSelector(config=MarketSelectionConfig(require_clob_candidate=False, current_window_only=False))
    runner = CoreMMRunner(
        market_selector=proof_selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=3,
        strategy_allocated_equity=1_000.0,
        safe_risk_profile="1000",
        hedge_search_profile="proof-only",
        proof_only_bucket_distance=2,
        proof_only_expiry_slack_ms=60_000,
        hedge_covariance_enabled=False,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-B70650",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-PROOF-1",
            "open_time": 1_699_999_400.0,
            "endTime": 1_700_000_900,
        },
        {
            "slug": "btc-updown-15m-B70750",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1,
            "spread": 0.02,
            "prices": [0.48, 0.52],
            "reward_per_100": 7,
            "event_ticker": "BTC-PROOF-1",
            "open_time": 1_699_999_400.0,
            "endTime": 1_700_000_900,
        },
        {
            "slug": "btc-updown-15m-B70850",
            "conditionId": "c",
            "clobTokenIds": ["yes_c", "no_c"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1,
            "spread": 0.02,
            "prices": [0.47, 0.53],
            "reward_per_100": 6,
            "event_ticker": "BTC-PROOF-1",
            "open_time": 1_699_999_400.0,
            "endTime": 1_700_000_900,
        },
    ]
    runner.refresh_market_selection(same_event, now_ms=1_000)
    for token_id in runner.all_token_ids:
        books.apply_snapshot(token_id, bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=19_000)
    books.apply_snapshot("yes_a", bids=[(0.45, 50)], asks=[(0.55, 50)], ts_ms=19_000)
    books.apply_snapshot("no_b", bids=[(0.495, 4)], asks=[(0.505, 4)], ts_ms=19_000)
    books.apply_snapshot("no_c", bids=[(0.49, 200)], asks=[(0.50, 200)], ts_ms=19_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=80, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    results = asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=1_000.0))

    cluster_hedge = runner.status().cluster_hedge
    plan = cluster_hedge["clusters"][0]
    assert plan["action"] == "HEDGE"
    assert plan["control_state"] == "HEDGE_ACTIVE"
    assert plan["hedge_market_id"] == "btc-updown-15m-B70850"
    candidate_summary = plan["candidate_summary"]
    assert candidate_summary["proof_only_lane"] is True
    assert candidate_summary["accepted_count"] >= 1
    assert candidate_summary["best_candidate"]["bucket_distance"] == 2
    hedge_quotes = [
        quote
        for result in results
        for quote in result.desired_quotes.values()
        if quote.metadata.get("hedge_action") == "HEDGE"
    ]
    assert hedge_quotes
    assert {quote.token_id for quote in hedge_quotes} == {"no_c"}


def test_runner_paper_cluster_hedge_reaches_baseline_cap_threshold(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=500.0,
        safe_risk_profile="500",
        hedge_covariance_enabled=False,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    books.apply_snapshot("yes_a", bids=[(0.45, 50)], asks=[(0.55, 50)], ts_ms=1_000)
    books.apply_snapshot("no_a", bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=1_000)
    books.apply_snapshot("yes_b", bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=1_000)
    books.apply_snapshot("no_b", bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=1_000)
    # Baseline event cap is about $20 on the $500 profile; a $10 long is enough for SKEW
    # and HEDGE_ELIGIBLE, but below the $12 HEDGE activation threshold.
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=20, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    results = asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=500.0))

    cluster_hedge = runner.status().cluster_hedge
    assert cluster_hedge["enabled"] is True
    assert cluster_hedge["clusters"][0]["action"] == "SKEW"
    assert cluster_hedge["clusters"][0]["control_state"] == "HEDGE_ELIGIBLE"
    hedge_quotes = [
        quote
        for result in results
        for quote in result.desired_quotes.values()
        if quote.metadata.get("hedge_action") == "HEDGE"
    ]
    assert hedge_quotes == []


def test_runner_stale_maker_exit_failed_exception_hedges_below_global_threshold(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=500.0,
        safe_risk_profile="500",
        hedge_covariance_enabled=True,
        hedge_covariance_min_samples=3,
        hedge_covariance_min_correlation=0.2,
        hedge_covariance_min_abs_beta=0.05,
        hedge_covariance_beta_clip=1.0,
        hedge_covariance_gate_required=True,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    samples = [
        (0.60, 0.40),
        (0.58, 0.42),
        (0.56, 0.44),
        (0.54, 0.46),
    ]
    for idx, (yes_a_mid, no_b_mid) in enumerate(samples, start=1):
        ts_ms = idx * 1_000
        _apply_mid_snapshot(books, "yes_a", yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_a", 1.0 - yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_b", no_b_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "yes_b", 1.0 - no_b_mid, ts_ms=ts_ms)
        asyncio.run(runner.run_cycles(now_ms=ts_ms, usdc_balance=500.0))

    # $6 net exposure is above the skew trigger but below the global hedge trigger.
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=12, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=500.0))

    cluster_plan = runner.status().cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "HEDGE"
    assert cluster_plan["control_state"] == "HEDGE_ACTIVE"
    assert cluster_plan["action_reason"] == "stale_maker_exit_failed_exception"


def test_runner_stale_maker_exit_failed_exception_requires_covariance_ok(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=500.0,
        safe_risk_profile="500",
        hedge_covariance_enabled=True,
        hedge_covariance_min_samples=3,
        hedge_covariance_min_correlation=0.2,
        hedge_covariance_min_abs_beta=0.05,
        hedge_covariance_beta_clip=1.0,
        hedge_covariance_gate_required=True,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    weak_samples = [
        (0.60, 0.40),
        (0.58, 0.38),
        (0.56, 0.36),
        (0.54, 0.34),
    ]
    for idx, (yes_a_mid, no_b_mid) in enumerate(weak_samples, start=1):
        ts_ms = idx * 1_000
        _apply_mid_snapshot(books, "yes_a", yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_a", 1.0 - yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_b", no_b_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "yes_b", 1.0 - no_b_mid, ts_ms=ts_ms)
        asyncio.run(runner.run_cycles(now_ms=ts_ms, usdc_balance=500.0))

    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=12, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=500.0))

    cluster_plan = runner.status().cluster_hedge["clusters"][0]
    assert cluster_plan["control_state"] == "UNWIND_ONLY"
    assert cluster_plan["action"] == "UNWIND"
    assert cluster_plan["action_reason"] == "stale_without_acceptable_hedge"


def test_cluster_relative_promotion_requires_beta_stability_gate() -> None:
    engine = HedgeEngine()
    pair_relation = HedgePairRelation(
        inventory_market_id="inv",
        hedge_market_id="hedge",
        cluster_id="cluster",
        underlying_symbol="BTC",
        event_family="BTC-HOURLY-1",
        expiry_bucket="1",
        contract_family="BTC-HOURLY",
        structural_score=1.0,
        covariance_score=0.6,
        beta_stability_score=0.05,
        execution_availability_score=1.0,
        realized_outcome_score=0.5,
        pair_score=0.65,
        hedgeability_tier="plausible",
        confidence_state="usable",
        basis_accumulation_flag=False,
        accepted_hedge_count=0,
        successful_hedge_count=0,
        failed_hedge_count=0,
        execution_observation_count=10,
        execution_ok_count=10,
        covariance_state="ok",
        covariance_confidence="usable",
        execution_state="ok",
        candidate_state="rejected",
        rejection_reason="weak_co_movement",
        last_updated_at_ms=0,
    )
    covariance_metrics = HedgeCovarianceMetrics(
        covariance=-0.01,
        correlation=-0.6,
        beta_raw=0.2,
        beta=0.2,
        beta_shrunk=0.15,
        beta_clipped=0.15,
        beta_sign_consistency=1.0,
        alignment_fraction=1.0,
        sample_count=10,
        state="ok",
        confidence="usable",
    )
    execution_metrics = HedgeExecutionMetrics(
        quality_score=20_000.0,
        state="ok",
    )

    assert engine._cluster_relative_promotes(
        pair_relation=pair_relation,
        covariance_metrics=covariance_metrics,
        execution_metrics=execution_metrics,
        top_score=1_000.0,
        second_score=0.0,
    ) is False


def test_runner_paper_cluster_unwind_for_stale_inventory(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        strategy_allocated_equity=200.0,
        safe_risk_profile="200",
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        }
    ]
    runner.refresh_market_selection(same_event)
    books.apply_snapshot("yes_a", bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=1_000)
    books.apply_snapshot("no_a", bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=10, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=200.0))

    cluster_hedge = runner.status().cluster_hedge
    assert cluster_hedge["clusters"][0]["action"] == "UNWIND"
    assert cluster_hedge["clusters"][0]["control_state"] == "UNWIND_ONLY"
    directives = cluster_hedge["clusters"][0]["token_directives"]
    assert any(directive["token_id"] == "yes_a" and directive["action"] == "UNWIND" for directive in directives)


def test_runner_negative_fresh_inventory_skews_and_blocks_new_adds(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        strategy_allocated_equity=200.0,
        safe_risk_profile="200",
    )
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.44, 300)], asks=[(0.46, 300)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.54, 300)], asks=[(0.56, 300)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_old", side="buy", size=10, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_old", side="buy", ts_ms=16_000)

    results = asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=200.0))

    cluster_plan = runner.status().cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "SKEW"
    assert cluster_plan["action_reason"] == "negative_mark_to_market_reduce_only"
    directives = cluster_plan["token_directives"]
    assert any(
        directive["token_id"] == "yes_old"
        and directive["block_buy"] is True
        and int(directive["extra_skew_ticks"]) == 2
        for directive in directives
    )
    buy_quotes = [
        quote
        for result in results
        for quote in result.desired_quotes.values()
        if quote.token_id == "yes_old" and quote.side == "buy"
    ]
    assert buy_quotes == []


def test_runner_negative_worsening_inventory_enters_unwind(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        strategy_allocated_equity=200.0,
        safe_risk_profile="200",
    )
    runner.refresh_market_selection([_candidate_events()[0]])
    runner.position_tracker.apply_fill(token_id="yes_old", side="buy", size=10, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_old", side="buy", ts_ms=18_000)

    books.apply_snapshot("yes_old", bids=[(0.47, 300)], asks=[(0.49, 300)], ts_ms=16_000)
    books.apply_snapshot("no_old", bids=[(0.51, 300)], asks=[(0.53, 300)], ts_ms=16_000)
    asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=200.0))

    books.apply_snapshot("yes_old", bids=[(0.42, 300)], asks=[(0.44, 300)], ts_ms=21_000)
    books.apply_snapshot("no_old", bids=[(0.56, 300)], asks=[(0.58, 300)], ts_ms=21_000)
    asyncio.run(runner.run_cycles(now_ms=22_000, usdc_balance=200.0))

    cluster_plan = runner.status().cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "UNWIND"
    assert cluster_plan["control_state"] == "UNWIND_ONLY"
    assert cluster_plan["action_reason"] == "negative_mark_to_market_worsening"


def test_runner_failed_hedge_downgrades_cluster_to_unwind_only(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
        safe_risk_profile="1000",
        hedge_covariance_enabled=False,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    books.apply_snapshot("yes_a", bids=[(0.45, 50)], asks=[(0.55, 50)], ts_ms=1_000)
    books.apply_snapshot("no_a", bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=1_000)
    books.apply_snapshot("yes_b", bids=[(0.49, 150)], asks=[(0.51, 150)], ts_ms=1_000)
    books.apply_snapshot("no_b", bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=80, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)

    asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=1_000.0))
    asyncio.run(runner.run_cycles(now_ms=26_000, usdc_balance=1_000.0))

    cluster_hedge = runner.status().cluster_hedge
    cluster_plan = cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "UNWIND"
    assert cluster_plan["control_state"] == "UNWIND_ONLY"
    assert cluster_plan["action_reason"] == "hedge_failed_no_improvement"
    assert int(cluster_plan["hedge_failed_cooldown_until_ms"]) > 26_000


def test_runner_covariance_gate_accepts_inverse_co_movement_for_hedge(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
        safe_risk_profile="1000",
        hedge_covariance_enabled=True,
        hedge_covariance_min_samples=3,
        hedge_covariance_min_correlation=0.2,
        hedge_covariance_min_abs_beta=0.05,
        hedge_covariance_beta_clip=1.0,
        hedge_covariance_gate_required=True,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    samples = [
        (0.60, 0.40),
        (0.58, 0.42),
        (0.56, 0.44),
        (0.54, 0.46),
    ]
    for idx, (yes_a_mid, no_b_mid) in enumerate(samples, start=1):
        ts_ms = idx * 1_000
        _apply_mid_snapshot(books, "yes_a", yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_a", 1.0 - yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_b", no_b_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "yes_b", 1.0 - no_b_mid, ts_ms=ts_ms)
        asyncio.run(runner.run_cycles(now_ms=ts_ms, usdc_balance=1_000.0))

    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=80, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    results = asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=1_000.0))

    cluster_plan = runner.status().cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "HEDGE"
    assert cluster_plan["hedge_covariance_state"] == "ok"
    assert float(cluster_plan["hedge_correlation"]) < -0.2
    assert float(cluster_plan["hedge_beta_clipped"]) > 0.0
    assert float(cluster_plan["hedge_pair_score"]) >= 0.55
    assert cluster_plan["hedgeability_tier"] in {"usable", "preferred"}
    assert cluster_plan["pair_relations"]
    hedge_directives = cluster_plan["token_directives"]
    assert any(
        directive["token_id"] == "no_b" and directive["action"] == "HEDGE"
        for directive in hedge_directives
    )
    assert results


def test_runner_covariance_gate_rejects_weak_hedge_even_with_good_execution(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
        safe_risk_profile="1000",
        hedge_covariance_enabled=True,
        hedge_covariance_min_samples=3,
        hedge_covariance_min_correlation=0.2,
        hedge_covariance_min_abs_beta=0.05,
        hedge_covariance_beta_clip=1.0,
        hedge_covariance_gate_required=True,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    samples = [
        (0.60, 0.40),
        (0.58, 0.38),
        (0.56, 0.36),
        (0.54, 0.34),
    ]
    for idx, (yes_a_mid, no_b_mid) in enumerate(samples, start=1):
        ts_ms = idx * 1_000
        _apply_mid_snapshot(books, "yes_a", yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_a", 1.0 - yes_a_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "no_b", no_b_mid, ts_ms=ts_ms)
        _apply_mid_snapshot(books, "yes_b", 1.0 - no_b_mid, ts_ms=ts_ms)
        asyncio.run(runner.run_cycles(now_ms=ts_ms, usdc_balance=1_000.0))

    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=80, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_a", side="buy", ts_ms=0)
    asyncio.run(runner.run_cycles(now_ms=20_000, usdc_balance=1_000.0))

    cluster_plan = runner.status().cluster_hedge["clusters"][0]
    assert cluster_plan["action"] == "UNWIND"
    assert cluster_plan["candidate_summary"]["rejection_counts"]["weak_co_movement"] >= 1
    assert cluster_plan["pair_relations"][0]["hedgeability_tier"] in {"plausible", "not_hedgeable"}


def test_hedge_engine_uses_persistence_aware_execution_and_beta_sign_consistency() -> None:
    engine = HedgeEngine()
    pair_key = ("inv", "hedge")

    assert engine._execution_availability_score(pair_key) == pytest.approx(0.5)

    engine._pair_execution_stats[pair_key] = {"observed": 1, "ok": 1}
    assert engine._execution_availability_score(pair_key) == pytest.approx(0.625)

    engine._pair_execution_stats[pair_key] = {"observed": 4, "ok": 3}
    assert engine._execution_availability_score(pair_key) == pytest.approx(0.75)

    stable_score = engine._beta_sign_consistency_score(
        common_ts=[1, 2, 3, 4, 5, 6],
        x_vals=[-0.02, -0.01, -0.015, -0.025, -0.02, -0.01],
        y_vals=[0.02, 0.01, 0.015, 0.025, 0.02, 0.01],
    )
    unstable_score = engine._beta_sign_consistency_score(
        common_ts=[1, 2, 3, 4, 5, 6],
        x_vals=[-0.02, -0.01, -0.015, 0.025, 0.02, 0.01],
        y_vals=[0.02, 0.01, 0.015, 0.025, 0.02, 0.01],
    )

    assert stable_score > 0.5
    assert unstable_score == pytest.approx(0.0)

    metrics = HedgeCovarianceMetrics(
        covariance=-0.001,
        correlation=-0.7,
        beta_raw=-0.8,
        beta=-0.8,
        beta_shrunk=-0.52,
        beta_clipped=0.52,
        beta_sign_consistency=stable_score,
        alignment_fraction=1.0,
        sample_count=6,
        state="ok",
        confidence="usable",
    )
    unstable_metrics = HedgeCovarianceMetrics(
        covariance=-0.001,
        correlation=-0.7,
        beta_raw=-0.8,
        beta=-0.8,
        beta_shrunk=-0.52,
        beta_clipped=0.52,
        beta_sign_consistency=unstable_score,
        alignment_fraction=1.0,
        sample_count=6,
        state="ok",
        confidence="usable",
    )

    assert engine._beta_stability_score(metrics) > 0.25
    assert engine._beta_stability_score(unstable_metrics) == pytest.approx(0.0)


def test_hedge_engine_aligns_lagged_returns_and_penalizes_repeated_failures() -> None:
    engine = HedgeEngine(hedge_covariance_max_update_gap_ms=1_000)
    aligned = engine._aligned_return_pairs(
        {1_000: -0.01, 2_000: -0.02, 3_000: -0.03},
        {1_500: 0.01, 2_500: 0.02, 3_500: 0.03},
    )
    assert len(aligned) == 3
    assert engine._alignment_fraction(inventory_count=3, hedge_count=3, aligned_count=len(aligned)) == pytest.approx(1.0)

    pair_key = ("inv", "hedge")
    engine._pair_outcome_stats[pair_key] = {"accepted": 2, "improved": 0, "failed": 2}
    score, confidence = engine._realized_outcome_score(pair_key)
    assert score == pytest.approx(0.0)
    assert confidence == "validated"
    assert engine._basis_accumulation_flag(pair_key) is True


def test_runner_observe_pause_temporarily_stops_quoting(selector: MarketSelector) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        broker=broker,
        mode="PAPER",
        observe_pause_interval_secs=10.0,
        observe_pause_duration_secs=5.0,
    )
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)

    first = asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1_000.0))
    paused = asyncio.run(runner.run_cycles(now_ms=13_000, usdc_balance=1_000.0))

    assert first is not None
    assert paused == []
    assert runner.control_state()["observe_pause_active"] is True


def test_runner_cluster_hedge_disabled_outside_paper(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        mode="OBSERVE",
        max_active_markets=2,
        strategy_allocated_equity=1_000.0,
    )
    same_event = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["yes_a", "no_a"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-HOURLY-1",
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["yes_b", "no_b"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 7,
            "event_ticker": "BTC-HOURLY-1",
        },
    ]
    runner.refresh_market_selection(same_event)
    for tid in runner.all_token_ids:
        books.apply_snapshot(tid, bids=[(0.49, 300)], asks=[(0.51, 300)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_a", side="buy", size=80, price=0.50)
    asyncio.run(runner.run_cycles(now_ms=2_000, usdc_balance=1_000.0))

    cluster_hedge = runner.status().cluster_hedge
    assert cluster_hedge["enabled"] is False
    assert list(cluster_hedge["clusters"][0]["rejection_reasons"]) == ["paper_only"]


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


def test_runner_uses_allocated_equity_for_risk_snapshot(selector: MarketSelector) -> None:
    runner = CoreMMRunner(
        market_selector=selector,
        strategy_allocated_equity=500.0,
        use_allocated_equity_for_risk=True,
    )
    snapshot = runner._portfolio_risk_snapshot(usdc_balance=1_000.0)
    assert snapshot["reference_equity"] == pytest.approx(500.0)
    assert snapshot["current_equity"] == pytest.approx(500.0)


def test_runner_enters_flatten_only_before_day_loss_halt(selector: MarketSelector) -> None:
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector,
        book_manager=books,
        mode="OBSERVE",
        strategy_allocated_equity=500.0,
        safe_risk_profile="500",
    )
    runner.refresh_market_selection([_candidate_events()[0]])
    books.apply_snapshot("yes_old", bids=[(0.19, 150)], asks=[(0.21, 160)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.79, 150)], asks=[(0.81, 160)], ts_ms=1_000)
    runner.position_tracker.apply_fill(token_id="yes_old", side="buy", size=20, price=0.60)
    mock_broker = MagicMock()
    mock_broker.stats.return_value = {"realized_net_pnl": -25.0}
    runner.broker = mock_broker
    asyncio.run(runner.run_cycles(now_ms=2_000, usdc_balance=500.0))
    assert runner.control_state()["flatten_only_mode"] is True
