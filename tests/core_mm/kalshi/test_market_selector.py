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
        c = _to_candidate(self._market(last_price=5), config, int(time.time()))
        assert c is None

    def test_filters_extreme_price_high(self):
        config = KalshiSelectorConfig(max_price=0.90)
        c = _to_candidate(self._market(last_price=95), config, int(time.time()))
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
