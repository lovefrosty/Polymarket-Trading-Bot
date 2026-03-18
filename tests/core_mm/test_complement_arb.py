import asyncio

import pytest

from core_mm.book_manager import BookManager
from core_mm.complement_arb import ComplementArbConfig, ComplementArbScanner, ComplementArbSignal
from core_mm.market_selector import MarketSelectionConfig, MarketSelector
from core_mm.paper_broker import PaperBroker
from core_mm.runner import CoreMMRunner


# ── Unit tests: ComplementArbScanner ───────────────────────────────


class TestComplementArbScanner:
    def test_disabled_returns_default_signal(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(enabled=False))
        signal = scanner.evaluate(yes_bid=0.49, yes_ask=0.51, no_bid=0.49, no_ask=0.51)
        assert signal.maker_arb_active is False
        assert signal.taker_arb_active is False
        assert signal.size_multiplier == 1.0

    def test_missing_prices_returns_default(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(enabled=True))
        signal = scanner.evaluate(yes_bid=0.49, yes_ask=None, no_bid=0.49, no_ask=0.51)
        assert signal.maker_arb_active is False

    def test_zero_prices_returns_default(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(enabled=True))
        signal = scanner.evaluate(yes_bid=0.0, yes_ask=0.51, no_bid=0.49, no_ask=0.51)
        assert signal.maker_arb_active is False

    def test_maker_edge_calculation(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(enabled=True, min_maker_edge_bps=50.0))
        # YES bid=0.48, NO bid=0.48 → sum=0.96 → edge=0.04 → 400 bps
        signal = scanner.evaluate(yes_bid=0.48, yes_ask=0.52, no_bid=0.48, no_ask=0.52)
        assert signal.maker_edge_bps == pytest.approx(400.0)
        assert signal.maker_arb_active is True
        assert signal.complement_sum_bid == pytest.approx(0.96)

    def test_maker_edge_below_threshold(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(enabled=True, min_maker_edge_bps=500.0))
        # YES bid=0.49, NO bid=0.49 → sum=0.98 → edge=0.02 → 200 bps (< 500)
        signal = scanner.evaluate(yes_bid=0.49, yes_ask=0.51, no_bid=0.49, no_ask=0.51)
        assert signal.maker_edge_bps == pytest.approx(200.0)
        assert signal.maker_arb_active is False
        assert signal.size_multiplier == 1.0

    def test_size_multiplier_applied_when_maker_active(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(
            enabled=True, min_maker_edge_bps=100.0, maker_size_multiplier=3.0,
        ))
        # 400 bps maker edge → active → 3x multiplier
        signal = scanner.evaluate(yes_bid=0.48, yes_ask=0.52, no_bid=0.48, no_ask=0.52)
        assert signal.maker_arb_active is True
        assert signal.size_multiplier == 3.0

    def test_taker_edge_calculation(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(
            enabled=True, min_taker_edge_bps=10.0, fee_bps=0.0,
        ))
        # With 0 fees: YES ask=0.49, NO ask=0.49 → sum=0.98 → edge=200 bps
        signal = scanner.evaluate(yes_bid=0.48, yes_ask=0.49, no_bid=0.48, no_ask=0.49)
        assert signal.taker_edge_bps == pytest.approx(200.0)
        assert signal.taker_arb_active is True
        assert signal.complement_sum_ask == pytest.approx(0.98)

    def test_taker_edge_negative_with_fees(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(
            enabled=True, min_taker_edge_bps=10.0, fee_bps=100.0,
        ))
        # YES ask=0.51, NO ask=0.51 → sum=1.02 × 1.01 = 1.0302 → edge = -302 bps
        signal = scanner.evaluate(yes_bid=0.49, yes_ask=0.51, no_bid=0.49, no_ask=0.51)
        assert signal.taker_edge_bps < 0
        assert signal.taker_arb_active is False

    def test_stats_tracking(self) -> None:
        scanner = ComplementArbScanner(ComplementArbConfig(
            enabled=True, min_maker_edge_bps=300.0,
        ))
        scanner.evaluate(yes_bid=0.48, yes_ask=0.52, no_bid=0.48, no_ask=0.52)  # 400 bps → active
        scanner.evaluate(yes_bid=0.49, yes_ask=0.51, no_bid=0.49, no_ask=0.51)  # 200 bps → below threshold
        stats = scanner.stats
        assert stats["total_evaluations"] == 2
        assert stats["total_maker_signals"] == 1


# ── Integration tests: Runner with complement arb ──────────────────


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
    ]


def test_runner_complement_arb_boosts_trade_size(selector: MarketSelector) -> None:
    """When complement arb is active, trade_size should be boosted via size_multiplier."""
    arb_config = ComplementArbConfig(
        enabled=True, min_maker_edge_bps=100.0, maker_size_multiplier=2.5,
    )
    books = BookManager()
    broker = PaperBroker(book_manager=books)
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books, broker=broker,
        mode="PAPER", trade_size=10.0,
        complement_arb_config=arb_config,
    )
    runner.refresh_market_selection(_candidate_events())
    # Wide spread: YES bid=0.45, ask=0.55, NO bid=0.45, ask=0.55
    # sum_bid=0.90, maker_edge=1000 bps → arb active
    books.apply_snapshot("yes_old", bids=[(0.45, 200)], asks=[(0.55, 200)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.45, 200)], asks=[(0.55, 200)], ts_ms=1_000)
    result = asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert result is not None
    # Check arb stats show at least one maker signal
    assert runner.complement_arb_stats["total_maker_signals"] >= 1


def test_runner_complement_arb_disabled_no_boost(selector: MarketSelector) -> None:
    """When complement arb is disabled, no size boost occurs."""
    books = BookManager()
    runner = CoreMMRunner(
        market_selector=selector, book_manager=books,
        mode="OBSERVE", trade_size=10.0,
    )
    runner.refresh_market_selection(_candidate_events())
    books.apply_snapshot("yes_old", bids=[(0.45, 200)], asks=[(0.55, 200)], ts_ms=1_000)
    books.apply_snapshot("no_old", bids=[(0.45, 200)], asks=[(0.55, 200)], ts_ms=1_000)
    result = asyncio.run(runner.run_cycle(now_ms=2_000, usdc_balance=1000))
    assert result is not None
    assert runner.complement_arb_stats["total_maker_signals"] == 0


def test_runner_complement_arb_stats_exposed(selector: MarketSelector) -> None:
    """complement_arb_stats property should be accessible."""
    runner = CoreMMRunner(market_selector=selector)
    stats = runner.complement_arb_stats
    assert "total_evaluations" in stats
    assert "total_maker_signals" in stats
    assert "total_taker_signals" in stats
