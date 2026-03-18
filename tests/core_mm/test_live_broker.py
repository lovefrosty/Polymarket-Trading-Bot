from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core_mm.execution import ExecutionResult
from core_mm.live_broker import LiveBroker
from core_mm.positions import PositionTracker


def _mock_exec() -> MagicMock:
    mock = MagicMock()
    mock.place_order.return_value = ExecutionResult(True, {"orderID": "live-1"})
    mock.cancel_order.return_value = ExecutionResult(True, {"canceled": ["live-1"]})
    mock.cancel_all.return_value = ExecutionResult(True, {"canceled": ["live-1"]})
    mock.get_open_orders.return_value = ExecutionResult(True, {"orders": []})
    mock.get_positions.return_value = ExecutionResult(True, {"positions": []})
    return mock


def _broker(**kwargs) -> LiveBroker:
    defaults = dict(
        execution_adapter=_mock_exec(),
        fee_bps=25.0,
        max_order_notional=5.0,
        max_position_notional=10.0,
        max_daily_loss=3.0,
    )
    defaults.update(kwargs)
    return LiveBroker(**defaults)


# ── Risk check tests ────────────────────────────────────────────────


def test_risk_rejects_price_out_of_bounds() -> None:
    broker = _broker()
    result = broker.place_order(token_id="t1", side="buy", price=1.05, size=5.0)
    assert not result.success
    assert "price_out_of_bounds" in (result.error or "")


def test_risk_rejects_price_too_low() -> None:
    broker = _broker()
    result = broker.place_order(token_id="t1", side="buy", price=0.005, size=5.0)
    assert not result.success
    assert "price_out_of_bounds" in (result.error or "")


def test_risk_rejects_size_too_small() -> None:
    broker = _broker()
    result = broker.place_order(token_id="t1", side="buy", price=0.50, size=0.5)
    assert not result.success
    assert "size_too_small" in (result.error or "")


def test_risk_rejects_order_notional_exceeded() -> None:
    broker = _broker(max_order_notional=2.0)
    result = broker.place_order(token_id="t1", side="buy", price=0.50, size=10.0)
    assert not result.success
    assert "order_notional_exceeded" in (result.error or "")


def test_risk_rejects_position_notional_exceeded() -> None:
    broker = _broker(max_position_notional=5.0)
    # Fill brings position to 5 * 0.50 = $2.50 notional
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 5.0})
    # Second fill brings position to 10 * 0.50 = $5.00 notional
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 5.0})
    # Next order would bring position to 15 * 0.50 = $7.50 — rejected
    result = broker.place_order(token_id="t1", side="buy", price=0.50, size=5.0)
    assert not result.success
    assert "position_notional_exceeded" in (result.error or "")


def test_risk_rejects_when_daily_loss_exceeded() -> None:
    broker = _broker(max_daily_loss=2.0)
    # Record a losing sell: bought at 0.50, sold at 0.30 → loss = (0.30 - 0.50) * 5 = -$1.00 - fees
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 5.0})
    broker.record_fill({"token_id": "t1", "side": "sell", "price": 0.30, "size": 5.0})
    # Another big loss
    broker.record_fill({"token_id": "t2", "side": "buy", "price": 0.50, "size": 5.0})
    broker.record_fill({"token_id": "t2", "side": "sell", "price": 0.10, "size": 5.0})
    # Now net PnL is well below -$2.00
    assert broker.stats()["realized_net_pnl"] < -2.0
    result = broker.place_order(token_id="t1", side="buy", price=0.50, size=1.0)
    assert not result.success
    assert "daily_loss_exceeded" in (result.error or "")


def test_risk_allows_sells_without_checks() -> None:
    """Sells reduce risk — they bypass pre-trade risk checks."""
    broker = _broker(max_order_notional=1.0)
    # Sell of any size should be allowed (notional check is buy-only)
    result = broker.place_order(token_id="t1", side="sell", price=0.50, size=100.0)
    assert result.success


def test_place_order_delegates_to_execution_adapter() -> None:
    mock_exec = _mock_exec()
    broker = _broker(execution_adapter=mock_exec)
    result = broker.place_order(token_id="t1", side="buy", price=0.50, size=2.0)
    assert result.success
    mock_exec.place_order.assert_called_once()
    call_kwargs = mock_exec.place_order.call_args[1]
    assert call_kwargs["token_id"] == "t1"
    assert call_kwargs["side"] == "buy"
    assert call_kwargs["price"] == 0.50
    assert call_kwargs["size"] == 2.0


# ── Fill tracking tests ─────────────────────────────────────────────


def test_record_fill_updates_stats() -> None:
    broker = _broker()
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 10.0})
    stats = broker.stats()
    assert stats["turnover"] == 5.0  # 0.50 * 10
    assert stats["cumulative_fees"] > 0


def test_record_fill_computes_pnl_on_sell() -> None:
    broker = _broker()
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.40, "size": 10.0})
    broker.record_fill({"token_id": "t1", "side": "sell", "price": 0.50, "size": 10.0})
    stats = broker.stats()
    # Gross PnL = (0.50 - 0.40) * 10 = $1.00
    assert stats["realized_gross_pnl"] == pytest.approx(1.0)
    # Net PnL = $1.00 - fees
    assert stats["realized_net_pnl"] < 1.0
    assert stats["realized_net_pnl"] > 0


def test_fills_returns_all_fills() -> None:
    broker = _broker()
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 5.0})
    broker.record_fill({"token_id": "t1", "side": "sell", "price": 0.55, "size": 5.0})
    assert len(broker.fills()) == 2


def test_drain_new_fills_returns_only_new() -> None:
    broker = _broker()
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 5.0})
    first = broker.drain_new_fills()
    assert len(first) == 1

    # Second drain returns empty
    assert broker.drain_new_fills() == []

    # New fill shows up
    broker.record_fill({"token_id": "t1", "side": "sell", "price": 0.55, "size": 5.0})
    second = broker.drain_new_fills()
    assert len(second) == 1


# ── Delegation tests ─────────────────────────────────────────────────


def test_cancel_order_delegates() -> None:
    mock_exec = _mock_exec()
    broker = _broker(execution_adapter=mock_exec)
    result = broker.cancel_order("order-123")
    assert result.success
    mock_exec.cancel_order.assert_called_once_with("order-123")


def test_cancel_all_delegates() -> None:
    mock_exec = _mock_exec()
    broker = _broker(execution_adapter=mock_exec)
    result = broker.cancel_all()
    assert result.success
    mock_exec.cancel_all.assert_called_once()


def test_get_open_orders_delegates() -> None:
    mock_exec = _mock_exec()
    broker = _broker(execution_adapter=mock_exec)
    result = broker.get_open_orders()
    assert result.success
    mock_exec.get_open_orders.assert_called_once()


def test_sweep_fills_is_noop() -> None:
    broker = _broker()
    assert broker.sweep_fills() == []


# ── FIFO duration tracking ──────────────────────────────────────────


def test_fifo_duration_tracking() -> None:
    broker = _broker()
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 10.0, "ts_ms": 1000})
    broker.record_fill({"token_id": "t1", "side": "sell", "price": 0.55, "size": 10.0, "ts_ms": 2000})
    assert broker.avg_duration_ms == pytest.approx(1000.0)


def test_consume_fifo_for_merge() -> None:
    broker = _broker()
    broker.record_fill({"token_id": "t1", "side": "buy", "price": 0.50, "size": 10.0, "ts_ms": 1000})
    broker.consume_fifo_for_merge("t1", 10.0, 3000)
    assert broker.avg_duration_ms == pytest.approx(2000.0)


# ── Position tracker ────────────────────────────────────────────────


def test_position_tracker_property() -> None:
    tracker = PositionTracker()
    broker = _broker(position_tracker=tracker)
    assert broker.position_tracker is tracker
