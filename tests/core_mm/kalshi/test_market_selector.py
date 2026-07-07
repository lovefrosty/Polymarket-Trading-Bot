"""Tests for core_mm.kalshi.market_selector — market discovery and filtering."""
from __future__ import annotations

import time

import pytest

from core_mm.kalshi.market_selector import (
    KalshiMarketSelector,
    KalshiSelectorConfig,
    _extract_symbol,
    _to_candidate,
)


# ── Symbol extraction ──────────────────────────────────────────────────────


class TestExtractSymbol:
    def test_btc_in_title(self):
        assert _extract_symbol("Will BTC be above $85,000?", "KXBTC-25MAR") == "BTC"

    def test_eth_in_ticker(self):
        assert _extract_symbol("Some market", "KXETH-25MAR") == "ETH"

    def test_fallback_to_ticker_prefix(self):
        assert _extract_symbol("Unknown market", "INFLATION-30JUN") == "INFLATION"

    def test_spx(self):
        assert _extract_symbol("S&P 500 SPX above 5000", "SPX-ABOVE") == "SPX"


# ── Candidate filtering ───────────────────────────────────────────────────


class TestToCandidate:
    def _market(self, **overrides) -> dict:
        base = {
            "ticker": "KXBTC-25MAR",
            "title": "Will BTC be above $85,000?",
            "status": "open",
            "result": "",
            "last_price": 55,
            "yes_bid": 54,
            "yes_ask": 56,
            "volume": 1000,
            "open_interest": 500,
            "expiration_time": int(time.time()) + 3600,
        }
        base.update(overrides)
        return base

    def test_basic_candidate(self):
        config = KalshiSelectorConfig()
        now = int(time.time())
        c = _to_candidate(self._market(), config, now)
        assert c is not None
        assert c.slug == "KXBTC-25MAR"
        assert c.condition_id == "KXBTC-25MAR"
        assert c.token_ids == ("KXBTC-25MAR:yes", "KXBTC-25MAR:no")
        assert c.reference_symbol == "BTC"
        assert c.active is True
        assert c.tradable is True

    def test_filters_extreme_price_low(self):
        config = KalshiSelectorConfig(min_price=0.10)
        c = _to_candidate(self._market(yes_bid=5, yes_ask=6, last_price=5), config, int(time.time()))
        assert c is None

    def test_filters_extreme_price_high(self):
        config = KalshiSelectorConfig(max_price=0.90)
        c = _to_candidate(self._market(yes_bid=95, yes_ask=96, last_price=95), config, int(time.time()))
        assert c is None

    def test_filters_near_expiry(self):
        config = KalshiSelectorConfig(min_time_to_expiry_secs=120)
        near_expiry = int(time.time()) + 30  # 30 seconds away
        c = _to_candidate(self._market(expiration_time=near_expiry), config, int(time.time()))
        assert c is None

    def test_filters_low_volume(self):
        config = KalshiSelectorConfig(min_volume_24hr=5000)
        c = _to_candidate(self._market(volume=100), config, int(time.time()))
        assert c is None

    def test_closed_market(self):
        config = KalshiSelectorConfig()
        c = _to_candidate(self._market(status="closed"), config, int(time.time()))
        assert c is not None
        assert c.active is False
        assert c.tradable is False

    def test_missing_ticker_returns_none(self):
        config = KalshiSelectorConfig()
        c = _to_candidate({"ticker": ""}, config, int(time.time()))
        assert c is None

    def test_rejects_one_sided_active_market(self):
        config = KalshiSelectorConfig()
        c = _to_candidate(self._market(yes_ask=None), config, int(time.time()))
        assert c is None

    def test_score_based_on_volume(self):
        config = KalshiSelectorConfig()
        now = int(time.time())
        c1 = _to_candidate(self._market(volume=1000, open_interest=100), config, now)
        c2 = _to_candidate(self._market(volume=5000, open_interest=500), config, now)
        assert c1 is not None and c2 is not None
        assert c2.score > c1.score

    def test_tick_size_conversion(self):
        """Tick sizes > 1.0 are assumed to be in cents and converted."""
        config = KalshiSelectorConfig()
        now = int(time.time())
        c = _to_candidate(self._market(tick_size=1), config, now)
        assert c is not None
        assert c.tick_size == pytest.approx(0.01)

    def test_dollar_price_from_last_price(self):
        """Prices > 1.0 are assumed to be in cents and converted."""
        config = KalshiSelectorConfig()
        now = int(time.time())
        c = _to_candidate(self._market(last_price=55), config, now)
        assert c is not None
        assert c.mid_price is not None
        assert 0.0 < c.mid_price < 1.0

    def test_rejects_low_liquidity_score(self):
        config = KalshiSelectorConfig(min_liquidity_score=0.75)
        c = _to_candidate(
            self._market(volume=10, open_interest=5, yes_bid=40, yes_ask=60, last_price=50),
            config,
            int(time.time()),
        )
        assert c is None


# ── Selector integration ──────────────────────────────────────────────────


class TestKalshiMarketSelector:
    def test_select_markets_uses_client(self):
        class FakeClient:
            def __init__(self):
                self.called = False

            def get_markets(self, **kwargs):
                self.called = True
                return [
                    {
                        "ticker": "KXBTC-25MAR",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.54",
                        "yes_ask_dollars": "0.56",
                        "last_price": 55,
                        "volume": 1000,
                        "open_interest": 500,
                        "expiration_time": int(time.time()) + 3600,
                    }
                ]

        client = FakeClient()
        selector = KalshiMarketSelector(client=client)
        candidates = selector.select_markets()
        assert client.called
        assert len(candidates) == 1
        assert candidates[0].slug == "KXBTC-25MAR"

    def test_select_markets_respects_max_results(self):
        class FakeClient:
            def get_markets(self, **kwargs):
                return [
                    {
                        "ticker": f"TICK-{i}",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.49",
                        "yes_ask_dollars": "0.51",
                        "last_price": 50,
                        "volume": 1000 - i,
                        "open_interest": 100,
                        "expiration_time": int(time.time()) + 3600,
                    }
                    for i in range(20)
                ]

        selector = KalshiMarketSelector(
            client=FakeClient(),
            config=KalshiSelectorConfig(max_results=5),
        )
        candidates = selector.select_markets()
        assert len(candidates) == 5

    def test_last_selection_report_includes_diagnostics(self):
        class FakeClient:
            def get_markets(self, **kwargs):
                return [
                    {
                        "ticker": "KXBTC-25MAR-A",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.48",
                        "yes_ask_dollars": "0.50",
                        "volume": 2000,
                        "open_interest": 800,
                        "expiration_time": int(time.time()) + 3600,
                    },
                    {
                        "ticker": "KXBTC-25MAR-B",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.00",
                        "yes_ask_dollars": "0.10",
                        "volume": 1,
                        "open_interest": 1,
                        "expiration_time": int(time.time()) + 3600,
                    },
                ]

        selector = KalshiMarketSelector(client=FakeClient())
        candidates = selector.select_markets()
        report = selector.last_selection_report
        assert candidates
        assert report["selected_market"]["ticker"] == "KXBTC-25MAR-A"
        assert report["accepted_count"] == 1
        assert report["rejected_count"] == 1
        assert report["accepted_candidates"][0]["ticker"] == "KXBTC-25MAR-A"
        assert report["rejected_candidates"][0]["reason"] in {"one_sided_book", "spread_too_wide", "insufficient_volume", "insufficient_open_interest"}

    def test_select_markets_attaches_series_fee_metadata_when_available(self):
        class FakeClient:
            def get_markets(self, **kwargs):
                return [
                    {
                        "ticker": "KXBTC-25MAR-A",
                        "series_ticker": "KXBTC",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.48",
                        "yes_ask_dollars": "0.50",
                        "volume": 2000,
                        "open_interest": 800,
                        "expiration_time": int(time.time()) + 3600,
                    },
                ]

            def get_series(self, series_ticker: str):
                assert series_ticker == "KXBTC"
                return {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1.0}

        selector = KalshiMarketSelector(client=FakeClient())
        candidates = selector.select_markets()
        assert len(candidates) == 1
        assert candidates[0].raw["series_fee_type"] == "quadratic_with_maker_fees"
        assert candidates[0].raw["series_fee_multiplier"] == 1.0

    def test_adjacent_bucket_transition_penalty_prefers_clearer_bucket(self):
        future_ts = int(time.time()) + 3600

        class FakeClient:
            def get_markets(self, **kwargs):
                return [
                    {
                        "ticker": "KXBTC-25MAR-B68000",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.57",
                        "yes_ask_dollars": "0.59",
                        "volume": 4000,
                        "open_interest": 1000,
                        "expiration_time": future_ts,
                        "floor_strike": 68000,
                        "cap_strike": 68099.99,
                    },
                    {
                        "ticker": "KXBTC-25MAR-B68100",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.55",
                        "yes_ask_dollars": "0.57",
                        "volume": 4000,
                        "open_interest": 1000,
                        "expiration_time": future_ts,
                        "floor_strike": 68100,
                        "cap_strike": 68199.99,
                    },
                    {
                        "ticker": "KXBTC-25MAR-B68200",
                        "status": "open",
                        "result": "",
                        "yes_bid_dollars": "0.20",
                        "yes_ask_dollars": "0.22",
                        "volume": 4000,
                        "open_interest": 1000,
                        "expiration_time": future_ts,
                        "floor_strike": 68200,
                        "cap_strike": 68299.99,
                    },
                ]

        selector = KalshiMarketSelector(client=FakeClient())
        candidates = selector.select_markets()
        assert candidates
        report = selector.last_selection_report
        assert report["selected_market"]["ticker"] == "KXBTC-25MAR-B68000"
