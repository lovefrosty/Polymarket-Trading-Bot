import pytest
from core_mm.positions import PositionTracker
from core_mm.user_feed import UserFeedState, parse_user_message


def test_parse_user_message_handles_wrapped_order_update() -> None:
    events = parse_user_message(
        {
            "data": {
                "event_type": "order",
                "order_id": "o1",
                "asset_id": "tok1",
                "side": "BUY",
                "status": "UPDATE",
                "size": 100,
                "size_matched": 25,
            }
        }
    )
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "order"
    assert event.order_id == "o1"
    assert event.token_id == "tok1"
    assert event.status == "updated"
    assert event.size_matched == 25


def test_trade_event_updates_position_tracker() -> None:
    tracker = PositionTracker()
    state = UserFeedState(position_tracker=tracker)
    state.apply_message(
        {
            "data": {
                "event_type": "trade",
                "order_id": "o1",
                "asset_id": "tok1",
                "side": "BUY",
                "size": 20,
                "price": 0.47,
                "trade_id": "t1",
            }
        }
    )
    position = tracker.get_position("tok1")
    assert position.size == 20
    assert position.avg_price == pytest.approx(0.47)
    order = state.get_order("o1")
    assert order is not None
    assert order.status == "filled"
    assert order.size_matched == 20


def test_cancellation_updates_internal_order_state() -> None:
    state = UserFeedState()
    state.apply_message(
        {
            "data": {
                "event_type": "order",
                "order_id": "o1",
                "asset_id": "tok1",
                "side": "SELL",
                "status": "PLACEMENT",
                "size": 40,
            }
        }
    )
    state.apply_message(
        {
            "data": {
                "event_type": "order",
                "order_id": "o1",
                "asset_id": "tok1",
                "side": "SELL",
                "status": "CANCELLATION",
                "size": 40,
            }
        }
    )
    order = state.get_order("o1")
    assert order is not None
    assert order.status == "canceled"
