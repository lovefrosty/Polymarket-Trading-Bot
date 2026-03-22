import unittest

from core.decision_tape import TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStar, PStarBuilder
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class _DummyDB:
    def __init__(self) -> None:
        self.rows = []

    def insert(self, table: str, row: dict) -> None:
        self.rows.append((table, row))


def _constraints(tokens: list[str]) -> dict[str, OrderConstraints]:
    return {
        token: OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=200.0,
            max_slippage_bps=200.0,
            max_book_staleness_ms=10_000,
        )
        for token in tokens
    }


def _runtime(tokens: list[str], market_meta: dict[str, dict]) -> RuntimeEngine:
    books = {token: OrderBook(asset_id=token, bids={}, asks={}) for token in tokens}
    return RuntimeEngine(
        mode="OBSERVE",
        db=_DummyDB(),
        decision_tape=object(),  # type: ignore[arg-type]
        trade_tape=object(),  # type: ignore[arg-type]
        books=books,
        constraints=_constraints(tokens),
        market_meta=market_meta,
        pstar_builder=PStarBuilder(max_age_ms=10_000, freeze_disagree_bps=25.0),
        policy_thresholds=PolicyThresholds(),
        constitution={"trading": {}, "policy": {}, "execution": {}},
        time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1_000_000, mono_ns=1_000_000_000),
        broker=None,
        run_epoch_ms=1_000_000,
    )


class TestRolloverCausalityBoundary(unittest.TestCase):
    def test_commit_resets_per_market_state_even_when_token_ids_stay_same(self) -> None:
        tokens = ["tok_yes", "tok_no"]
        old_meta = {
            "tok_yes": {"slug": "btc-updown-15m-1700000000", "reference_symbol": "BTC"},
            "tok_no": {"slug": "btc-updown-15m-1700000000", "reference_symbol": "BTC"},
        }
        runtime = _runtime(tokens, old_meta)

        runtime._book_update_count_by_token["tok_yes"] = 9
        runtime._book_update_count_by_token["tok_no"] = 7
        runtime._book_seq_by_token["tok_yes"] = 3
        runtime._book_seq_by_token["tok_no"] = 4
        runtime._last_book_recv_mono_by_token["tok_yes"] = 1234
        runtime._last_book_recv_mono_by_token["tok_no"] = 5678
        runtime._latest_book_snapshot_by_token["tok_yes"] = object()  # type: ignore[assignment]
        runtime._latest_book_snapshot_by_token["tok_no"] = object()  # type: ignore[assignment]
        runtime._quote_revision[("tok_yes", "buy")] = 2
        runtime._quote_revision[("tok_no", "sell")] = 5
        runtime._latest_pstar_by_symbol["BTC"] = PStar(
            symbol="BTC",
            value=50_000.0,
            ts_event_ms=1_000,
            sources_used={"spot"},
            confidence=1.0,
            valid=True,
            diagnostics={},
        )
        runtime.pstar_builder.ingest("spot", "BTC", 50_000.0, ts_event_ms=1_000, ts_recv_wall_ms=1_000)

        new_meta = {
            "tok_yes": {"slug": "btc-updown-15m-1700000900", "reference_symbol": "BTC"},
            "tok_no": {"slug": "btc-updown-15m-1700000900", "reference_symbol": "BTC"},
        }
        runtime.commit_rollover_swap(
            books={token: OrderBook(asset_id=token, bids={}, asks={}) for token in tokens},
            constraints=_constraints(tokens),
            market_meta=new_meta,
            now_ms=2_000,
        )

        self.assertEqual(runtime._book_update_count_by_token["tok_yes"], 0)
        self.assertEqual(runtime._book_update_count_by_token["tok_no"], 0)
        self.assertEqual(runtime._book_seq_by_token["tok_yes"], 0)
        self.assertEqual(runtime._book_seq_by_token["tok_no"], 0)
        self.assertEqual(runtime._last_book_recv_mono_by_token["tok_yes"], 0)
        self.assertEqual(runtime._last_book_recv_mono_by_token["tok_no"], 0)
        self.assertNotIn("tok_yes", runtime._latest_book_snapshot_by_token)
        self.assertNotIn("tok_no", runtime._latest_book_snapshot_by_token)
        self.assertNotIn(("tok_yes", "buy"), runtime._quote_revision)
        self.assertNotIn(("tok_no", "sell"), runtime._quote_revision)
        self.assertNotIn("BTC", runtime._latest_pstar_by_symbol)
        self.assertNotIn("BTC", runtime.pstar_builder._latest)


if __name__ == "__main__":
    unittest.main()
