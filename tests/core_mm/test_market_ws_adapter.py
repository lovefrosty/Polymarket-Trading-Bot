from core_mm.book_manager import BookManager
from core_mm.market_ws_adapter import PolymarketMarketFeed, _apply_market_message, _market_subscribe_payload


def test_market_subscribe_payload_shape() -> None:
    payload = _market_subscribe_payload(["t2", "t1"])
    assert payload == {"type": "market", "assets_ids": ["t2", "t1"]}


def test_market_feed_token_replacement() -> None:
    feed = PolymarketMarketFeed(book_manager=BookManager(), token_ids=["a", "b"])
    changed = feed.set_token_ids(["c", "d"])
    assert changed is True
    status = feed.status()
    assert status.subscribed_token_ids == ()


def test_apply_market_message_handles_top_level_snapshot_array() -> None:
    manager = BookManager()
    message = [
        {
            "asset_id": "yes",
            "buys": [{"price": 0.49, "size": 150}],
            "sells": [{"price": 0.51, "size": 160}],
            "timestamp": 1_000,
        },
        {
            "asset_id": "no",
            "buys": [{"price": 0.48, "size": 120}],
            "sells": [{"price": 0.52, "size": 130}],
            "timestamp": 1_000,
        },
    ]
    applied = _apply_market_message(manager, message)
    assert applied == 2
    assert manager.get_book("yes") is not None
    assert manager.get_book("no") is not None


def test_market_feed_invokes_callback_on_applied_update() -> None:
    called = {"count": 0}

    def on_update() -> None:
        called["count"] += 1

    feed = PolymarketMarketFeed(book_manager=BookManager(), token_ids=["a"], on_applied_update=on_update)
    payload = {"asset_id": "a", "buys": [{"price": 0.49, "size": 100}], "sells": [{"price": 0.51, "size": 100}]}
    applied = _apply_market_message(feed._book_manager, payload)
    if applied > 0:
        on_update()
    assert called["count"] == 1
