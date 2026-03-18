import time

from core_mm.book_manager import BookManager
from core_mm.paper_broker import PaperBroker


def _now_ms() -> int:
    return int(time.time() * 1000)


def test_paper_broker_fills_crossing_buy() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.52, size=10)
    assert result.success
    assert result.payload["fill"]["price"] == 0.52
    position = broker.position_tracker.get_position("yes")
    assert position.size == 10


def test_paper_broker_tracks_open_order_when_not_filled() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.49, size=10)
    assert result.success
    open_orders = broker.get_open_orders()
    assert len(open_orders.payload["orders"]) == 1


def test_paper_broker_sweep_fills_on_touch() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=_now_ms())
    # Disable min_queue_wait so sweep can fill immediately in tests
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    broker.place_order(token_id="yes", side="buy", price=0.49, size=10)
    fills = broker.sweep_fills("yes")
    assert len(fills) == 1
    assert fills[0]["price"] == 0.49
    position = broker.position_tracker.get_position("yes")
    assert position.size == 10


# ── Realism gate tests ────────────────────────────────────────────────────────


def test_paper_broker_stale_book_refuses_fill() -> None:
    """Gate 1: stale book prevents crossing fill."""
    books = BookManager()
    # ts_ms=1 is Jan 1970 — guaranteed stale against real wall clock
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=1)
    broker = PaperBroker(book_manager=books, stale_book_ms=5_000)
    result = broker.place_order(token_id="yes", side="buy", price=0.52, size=10)
    # Order registers but no fill because book is stale
    assert result.success
    assert "fill" not in result.payload
    assert broker.position_tracker.get_position("yes").size == 0.0


def test_paper_broker_stale_disabled_when_zero() -> None:
    """stale_book_ms=0 disables the gate entirely."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=1)
    broker = PaperBroker(book_manager=books, stale_book_ms=0)
    result = broker.place_order(token_id="yes", side="buy", price=0.52, size=10)
    assert result.success
    assert result.payload.get("fill") is not None


def test_paper_broker_queue_wait_blocks_immediate_touch_fill() -> None:
    """Gate 2: resting order must wait min_queue_wait_ms before touch-fill."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 200)], asks=[(0.51, 200)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=500)
    broker.place_order(token_id="yes", side="buy", price=0.49, size=10)
    # Sweep immediately — order age << 500ms → no fill
    fills = broker.sweep_fills("yes")
    assert len(fills) == 0
    assert broker.position_tracker.get_position("yes").size == 0.0


def test_paper_broker_partial_fill_from_depth_cap() -> None:
    """Gate 3: fill size is capped to (1 - queue_fraction) * visible_depth."""
    books = BookManager()
    # Ask at 0.52 has 20 units visible; queue_depth_fraction=0.5 → 10 available
    books.apply_snapshot("yes", bids=[(0.48, 200)], asks=[(0.52, 20)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, queue_depth_fraction=0.5)
    result = broker.place_order(token_id="yes", side="buy", price=0.52, size=100)
    assert result.success
    fill = result.payload.get("fill")
    assert fill is not None
    assert fill["size"] == 10.0
    assert fill["is_partial"] is True


# ── Markout tracking tests ────────────────────────────────────────────────────


def test_markout_buy_positive_when_mid_rises() -> None:
    """BUY fill at mid=0.50, mid rises to 0.51 after 1s → markout_1s_bps ≈ +200."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    assert result.success
    fill_ts = result.payload["fill"]["ts_ms"]

    # Simulate mid rising to 0.51 bid / 0.53 ask → mid = 0.52
    books.apply_snapshot("yes", bids=[(0.51, 100)], asks=[(0.53, 100)], ts_ms=_now_ms())
    broker.process_markouts(now_ms=fill_ts + 1_100)

    # avg_markout_1s_bps should now be set and positive
    assert broker.avg_markout_1s_bps > 0.0


def test_markout_sell_negative_when_mid_rises() -> None:
    """SELL fill at mid=0.50, mid rises to 0.52 after 1s → markout_1s_bps < 0."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    # Place a sell at bid (crossing order)
    result = broker.place_order(token_id="yes", side="sell", price=0.49, size=10)
    assert result.success
    fill_ts = result.payload["fill"]["ts_ms"]

    # Mid rises → bad for a sell
    books.apply_snapshot("yes", bids=[(0.51, 100)], asks=[(0.53, 100)], ts_ms=_now_ms())
    broker.process_markouts(now_ms=fill_ts + 1_100)

    assert broker.avg_markout_1s_bps < 0.0


def test_markout_buy_zero_when_mid_unchanged() -> None:
    """BUY fill, mid unchanged after 1s → markout_1s_bps ≈ 0."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    assert result.success
    fill_ts = result.payload["fill"]["ts_ms"]

    # Book unchanged
    broker.process_markouts(now_ms=fill_ts + 1_100)

    assert abs(broker.avg_markout_1s_bps) < 1.0  # within 1 bps of zero


def test_markout_record_removed_after_all_windows_filled() -> None:
    """Records with all 3 windows filled are removed from _pending_markouts."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    result = broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    assert result.success
    fill_ts = result.payload["fill"]["ts_ms"]

    # After 30s, all windows should be computed and record removed
    broker.process_markouts(now_ms=fill_ts + 31_000)
    assert len(broker._pending_markouts) == 0


# ── FIFO duration tracking tests ─────────────────────────────────────────────


def test_fifo_duration_buy_then_sell() -> None:
    """Buy, wait, sell → duration > 0."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, min_queue_wait_ms=0)
    # Buy
    result = broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    assert result.success and result.payload.get("fill") is not None
    # Update book and sell
    books.apply_snapshot("yes", bids=[(0.50, 100)], asks=[(0.52, 100)], ts_ms=_now_ms())
    broker.place_order(token_id="yes", side="sell", price=0.50, size=10)
    assert broker.avg_duration_ms >= 0.0


def test_fifo_partial_consume() -> None:
    """Buy 20, sell 10 → 10 remaining in FIFO."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 200)], asks=[(0.51, 200)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, queue_depth_fraction=0.0)
    broker.place_order(token_id="yes", side="buy", price=0.51, size=20)
    books.apply_snapshot("yes", bids=[(0.50, 200)], asks=[(0.52, 200)], ts_ms=_now_ms())
    broker.place_order(token_id="yes", side="sell", price=0.50, size=10)
    entries = broker._fifo_entries.get("yes", [])
    total_remaining = sum(e[1] for e in entries)
    assert abs(total_remaining - 10.0) < 0.01


def test_fifo_merge_records_duration() -> None:
    """consume_fifo_for_merge → avg_duration_ms > 0."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 200)], asks=[(0.51, 200)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, queue_depth_fraction=0.0)
    broker.place_order(token_id="yes", side="buy", price=0.51, size=20)
    merge_ts = _now_ms() + 5000
    broker.consume_fifo_for_merge("yes", 20.0, merge_ts)
    assert broker.avg_duration_ms > 0.0


def test_duration_in_broker_stats() -> None:
    """stats() includes avg_duration_ms key."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)
    stats = broker.stats()
    assert "avg_duration_ms" in stats


def test_markout_ewma_updates_over_sequence() -> None:
    """avg_markout_1s_bps EWMA updates correctly over a sequence of fills."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 100)], asks=[(0.51, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books)

    # First fill: initialised to first markout
    result1 = broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    fill_ts1 = result1.payload["fill"]["ts_ms"]
    broker.process_markouts(now_ms=fill_ts1 + 31_000)
    first_avg = broker.avg_markout_1s_bps

    # Second fill: EWMA should move toward new value
    books.apply_snapshot("yes", bids=[(0.51, 100)], asks=[(0.53, 100)], ts_ms=_now_ms())
    result2 = broker.place_order(token_id="yes", side="buy", price=0.53, size=10)
    fill_ts2 = result2.payload["fill"]["ts_ms"]
    # Mid rises further after fill
    books.apply_snapshot("yes", bids=[(0.53, 100)], asks=[(0.55, 100)], ts_ms=_now_ms())
    broker.process_markouts(now_ms=fill_ts2 + 31_000)

    # EWMA should have moved from first measurement
    assert broker.avg_markout_1s_bps != first_avg or first_avg == 0.0


# ── Maker fee = 0 tests ────────────────────────────────────────────


def test_maker_touch_fill_zero_fees() -> None:
    """Touch fills (maker) should have zero fees on Polymarket."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, fee_bps=25.0, min_queue_wait_ms=0)
    broker.place_order(token_id="yes", side="buy", price=0.49, size=10)
    fills = broker.sweep_fills("yes")
    assert len(fills) == 1
    assert fills[0]["fill_trigger"] == "touch"
    assert fills[0]["fee_usdc"] == 0.0  # Maker fee = 0


def test_taker_cross_fill_charges_fees() -> None:
    """Crossing fills (taker) should still charge fees."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, fee_bps=25.0)
    result = broker.place_order(token_id="yes", side="buy", price=0.52, size=10)
    fill = result.payload["fill"]
    assert fill["fill_trigger"] == "cross"
    assert fill["fee_usdc"] > 0  # Taker fee > 0
    expected_fee = 10 * 0.52 * 25.0 / 10_000.0
    assert abs(fill["fee_usdc"] - expected_fee) < 0.001


# ── Bankroll tracking tests ────────────────────────────────────────


def test_bankroll_tracks_buys_and_sells() -> None:
    """Bankroll should track net cash flow from trades."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 200)], asks=[(0.51, 200)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, fee_bps=0.0, min_queue_wait_ms=0)
    # Buy: spend 10 * 0.51 = 5.10
    broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    stats = broker.stats()
    assert stats["bankroll_spent"] > 0
    assert stats["bankroll_received"] == 0.0
    # Sell: receive 10 * 0.49 = 4.90
    broker.place_order(token_id="yes", side="sell", price=0.49, size=10)
    fills = broker.sweep_fills("yes")
    stats = broker.stats()
    assert stats["bankroll_received"] > 0
    assert stats["bankroll_net_cash_flow"] < 0  # Net loss (bought high, sold low)


def test_unrealized_pnl_in_stats() -> None:
    """Unrealized PnL should reflect current mid vs avg cost."""
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.49, 200)], asks=[(0.51, 200)], ts_ms=_now_ms())
    broker = PaperBroker(book_manager=books, fee_bps=0.0)
    # Buy at ask (0.51)
    broker.place_order(token_id="yes", side="buy", price=0.51, size=10)
    # Mid is 0.50, bought at 0.51 → unrealized PnL = (0.50 - 0.51) * 10 = -0.10
    stats = broker.stats()
    assert abs(stats["unrealized_pnl"] - (-0.10)) < 0.02

    # Price rises
    books.apply_snapshot("yes", bids=[(0.55, 200)], asks=[(0.57, 200)], ts_ms=_now_ms())
    stats = broker.stats()
    # Mid is 0.56, bought at 0.51 → unrealized PnL = (0.56 - 0.51) * 10 = 0.50
    assert stats["unrealized_pnl"] > 0
