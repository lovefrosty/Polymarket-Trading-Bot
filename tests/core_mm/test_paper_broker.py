from core_mm.book_manager import BookManager
from core_mm.paper_broker import PaperBroker


def test_paper_broker_fills_crossing_buy() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=1_000)
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.52, size=10)
    assert result.success
    assert result.payload["fill"]["price"] == 0.52
    position = broker.position_tracker.get_position("yes")
    assert position.size == 10


def test_paper_broker_tracks_open_order_when_not_filled() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=1_000)
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.49, size=10)
    assert result.success
    open_orders = broker.get_open_orders()
    assert len(open_orders.payload["orders"]) == 1


def test_paper_broker_sweep_fills_on_touch() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    broker = PaperBroker(book_manager=books)
    broker.place_order(token_id="yes", side="buy", price=0.49, size=10)
    fills = broker.sweep_fills("yes")
    assert len(fills) == 1
    assert fills[0]["price"] == 0.49
    position = broker.position_tracker.get_position("yes")
    assert position.size == 10
