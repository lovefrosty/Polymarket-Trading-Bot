"""Tests for core_mm.kalshi.client — PSS-RSA signing, order translation, virtual tokens."""
from __future__ import annotations

import pytest

from core_mm.kalshi.client import (
    KalshiOrderArgs,
    _parse_virtual_token,
    _translate_order,
)


# ── Virtual token parsing ─────────────────────────────────────────────────


class TestParseVirtualToken:
    def test_yes_token(self):
        ticker, is_yes = _parse_virtual_token("KXBTC-25MAR:yes")
        assert ticker == "KXBTC-25MAR"
        assert is_yes is True

    def test_no_token(self):
        ticker, is_yes = _parse_virtual_token("KXBTC-25MAR:no")
        assert ticker == "KXBTC-25MAR"
        assert is_yes is False

    def test_bare_ticker(self):
        ticker, is_yes = _parse_virtual_token("KXBTC-25MAR")
        assert ticker == "KXBTC-25MAR"
        assert is_yes is True  # Default to YES

    def test_case_insensitive(self):
        ticker, is_yes = _parse_virtual_token("TICK:YES")
        assert is_yes is True
        ticker, is_yes = _parse_virtual_token("TICK:No")
        assert is_yes is False


# ── Order translation ──────────────────────────────────────────────────────


class TestTranslateOrder:
    def test_buy_yes(self):
        action, price = _translate_order(is_yes=True, side="BUY", price=0.55)
        assert action == "buy"
        assert price == 55

    def test_sell_yes(self):
        action, price = _translate_order(is_yes=True, side="SELL", price=0.55)
        assert action == "sell"
        assert price == 55

    def test_buy_no(self):
        # Buying NO at 0.40 = selling YES at 0.60
        action, price = _translate_order(is_yes=False, side="BUY", price=0.40)
        assert action == "sell"
        assert price == 60  # 1.0 - 0.40 = 0.60 → 60 cents

    def test_sell_no(self):
        # Selling NO at 0.40 = buying YES at 0.60
        action, price = _translate_order(is_yes=False, side="SELL", price=0.40)
        assert action == "buy"
        assert price == 60

    def test_price_clamped_low(self):
        action, price = _translate_order(is_yes=True, side="BUY", price=0.001)
        assert price >= 1  # Minimum 1 cent

    def test_price_clamped_high(self):
        action, price = _translate_order(is_yes=True, side="BUY", price=0.999)
        assert price <= 99  # Maximum 99 cents


# ── KalshiOrderArgs ──────────────────────────────────────────────────────


class TestKalshiOrderArgs:
    def test_dataclass_fields(self):
        args = KalshiOrderArgs(token_id="TICK:yes", price=0.50, size=10.0, side="BUY")
        assert args.token_id == "TICK:yes"
        assert args.price == 0.50
        assert args.size == 10.0
        assert args.side == "BUY"

    def test_frozen(self):
        args = KalshiOrderArgs(token_id="TICK:yes", price=0.50, size=10.0, side="BUY")
        with pytest.raises(AttributeError):
            args.price = 0.60  # type: ignore[misc]
