"""Tests for core_mm.kalshi.execution_bridge — order/fill normalization."""
from __future__ import annotations

import pytest

from core_mm.kalshi.execution_bridge import normalize_kalshi_fill, normalize_kalshi_order


class TestNormalizeKalshiOrder:
    def test_buy_action(self):
        order = {
            "order_id": "abc123",
            "ticker": "KXBTC-25MAR",
            "action": "buy",
            "side": "yes",
            "yes_price": 55,
            "remaining_count": 10,
        }
        result = normalize_kalshi_order(order)
        assert result["order_id"] == "abc123"
        assert result["token_id"] == "KXBTC-25MAR:yes"
        assert result["side"] == "buy"
        assert result["price"] == pytest.approx(0.55)
        assert result["size"] == 10.0

    def test_sell_action(self):
        order = {
            "order_id": "def456",
            "ticker": "KXBTC-25MAR",
            "action": "sell",
            "side": "yes",
            "yes_price": 60,
            "count": 5,
        }
        result = normalize_kalshi_order(order)
        assert result["token_id"] == "KXBTC-25MAR:yes"
        assert result["side"] == "sell"
        assert result["price"] == pytest.approx(0.60)
        assert result["size"] == 5.0

    def test_dollar_price_field(self):
        order = {
            "order_id": "x",
            "ticker": "TICK",
            "action": "buy",
            "yes_price_dollars": 0.55,
            "remaining_count": 1,
        }
        result = normalize_kalshi_order(order)
        assert result["price"] == pytest.approx(0.55)


class TestNormalizeKalshiFill:
    def test_buy_fill(self):
        fill = {
            "trade_id": "t001",
            "order_id": "o001",
            "ticker": "KXBTC-25MAR",
            "action": "buy",
            "side": "yes",
            "yes_price": 55,
            "count": 5,
        }
        result = normalize_kalshi_fill(fill)
        assert result["event_type"] == "trade"
        assert result["trade_id"] == "t001"
        assert result["order_id"] == "o001"
        assert result["token_id"] == "KXBTC-25MAR:yes"
        assert result["side"] == "buy"
        assert result["price"] == pytest.approx(0.55)
        assert result["size"] == 5.0

    def test_sell_fill(self):
        fill = {
            "trade_id": "t002",
            "ticker": "KXBTC-25MAR",
            "action": "sell",
            "yes_price": 60,
            "count": 3,
        }
        result = normalize_kalshi_fill(fill)
        assert result["token_id"] == "KXBTC-25MAR:yes"
        assert result["side"] == "sell"
        assert result["price"] == pytest.approx(0.60)
