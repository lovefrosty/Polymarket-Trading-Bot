"""Tests for core_mm.kalshi.market_feed — book conversion and feeding."""
from __future__ import annotations

import pytest

from core_mm.book_manager import BookManager
from core_mm.kalshi.market_feed import (
    KalshiMarketFeed,
    _apply_kalshi_book,
    _flip_levels,
    _parse_cent_levels,
)


# ── Cent level parsing ─────────────────────────────────────────────────────


class TestParseCentLevels:
    def test_basic_conversion(self):
        raw = [[55, 100], [50, 200]]
        result = _parse_cent_levels(raw)
        assert len(result) == 2
        assert result[0] == (0.55, 100.0)
        assert result[1] == (0.50, 200.0)

    def test_filters_zero_qty(self):
        raw = [[55, 0], [50, 100]]
        result = _parse_cent_levels(raw)
        assert len(result) == 1
        assert result[0] == (0.50, 100.0)

    def test_filters_zero_price(self):
        raw = [[0, 100], [50, 100]]
        result = _parse_cent_levels(raw)
        assert len(result) == 1

    def test_empty(self):
        assert _parse_cent_levels([]) == []
        assert _parse_cent_levels(None) == []

    def test_invalid_items(self):
        raw = ["bad", [55], [50, 100]]
        result = _parse_cent_levels(raw)
        assert len(result) == 1


# ── Level flipping ──────────────────────────────────────────────────────────


class TestFlipLevels:
    def test_basic_flip(self):
        levels = [(0.40, 100.0), (0.35, 200.0)]
        flipped = _flip_levels(levels)
        assert len(flipped) == 2
        assert flipped[0] == (0.60, 100.0)
        assert flipped[1] == (0.65, 200.0)

    def test_boundary_prices_excluded(self):
        # Price of 0.0 or 1.0 after flip should be excluded
        levels = [(1.0, 100.0), (0.0, 100.0)]
        flipped = _flip_levels(levels)
        assert len(flipped) == 0

    def test_empty(self):
        assert _flip_levels([]) == []


# ── Full book application ──────────────────────────────────────────────────


class TestApplyKalshiBook:
    def test_creates_yes_and_no_books(self):
        bm = BookManager()
        ob = {
            "yes": [[55, 100], [50, 200]],
            "no": [[45, 150], [40, 300]],
        }
        applied = _apply_kalshi_book(bm, "KXBTC", ob)
        assert applied == 2

        yes_book = bm.get_book("KXBTC:yes")
        no_book = bm.get_book("KXBTC:no")
        assert yes_book is not None
        assert no_book is not None

    def test_yes_book_bids_and_asks(self):
        bm = BookManager()
        ob = {
            "yes": [[55, 100]],  # YES bid at $0.55
            "no": [[40, 150]],   # NO bid at $0.40 → YES ask at $0.60
        }
        _apply_kalshi_book(bm, "KXBTC", ob)
        yes_book = bm.get_book("KXBTC:yes")
        assert yes_book is not None
        # Best bid should be 0.55
        assert yes_book.best_bid == pytest.approx(0.55)
        # Best ask should be 0.60 (flipped from NO bid at 0.40)
        assert yes_book.best_ask == pytest.approx(0.60)

    def test_no_book_bids_and_asks(self):
        bm = BookManager()
        ob = {
            "yes": [[55, 100]],  # YES bid → NO ask at $0.45
            "no": [[40, 150]],   # NO bid at $0.40
        }
        _apply_kalshi_book(bm, "KXBTC", ob)
        no_book = bm.get_book("KXBTC:no")
        assert no_book is not None
        assert no_book.best_bid == pytest.approx(0.40)
        assert no_book.best_ask == pytest.approx(0.45)

    def test_empty_book(self):
        bm = BookManager()
        applied = _apply_kalshi_book(bm, "EMPTY", {"yes": [], "no": []})
        assert applied == 0

    def test_invalid_input(self):
        bm = BookManager()
        assert _apply_kalshi_book(bm, "BAD", "not_a_dict") == 0
        assert _apply_kalshi_book(bm, "BAD", {}) == 0


# ── Feed set_token_ids ─────────────────────────────────────────────────────


class TestFeedSetTokenIds:
    def test_extracts_tickers(self):
        bm = BookManager()

        class FakeClient:
            def get_orderbook(self, ticker, depth=20):
                return {"yes": [], "no": []}

        feed = KalshiMarketFeed(client=FakeClient(), book_manager=bm, tickers=())
        changed = feed.set_token_ids(["KXBTC:yes", "KXBTC:no", "KXETH:yes"])
        assert changed is True
        assert set(feed._desired_tickers) == {"KXBTC", "KXETH"}

    def test_no_change_returns_false(self):
        bm = BookManager()

        class FakeClient:
            def get_orderbook(self, ticker, depth=20):
                return {"yes": [], "no": []}

        feed = KalshiMarketFeed(client=FakeClient(), book_manager=bm, tickers=("KXBTC",))
        changed = feed.set_token_ids(["KXBTC:yes", "KXBTC:no"])
        assert changed is False
