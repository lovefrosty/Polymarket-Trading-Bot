"""Tests for core_mm.memory — Epic 4 OpenViking Memory Layer."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core_mm.memory import MarketMemory, MemoryStore, SessionSummary


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "test_memory.db")


class TestMarketMemory:
    def test_adversity_ratio_no_fills(self) -> None:
        mem = MarketMemory(symbol="BTC")
        assert mem.adversity_ratio() == 0.0

    def test_adversity_ratio_with_fills(self) -> None:
        mem = MarketMemory(symbol="BTC", total_fills=100, adverse_fills=30)
        assert abs(mem.adversity_ratio() - 0.3) < 0.01

    def test_session_win_rate(self) -> None:
        mem = MarketMemory(symbol="ETH", total_sessions=10, win_sessions=7)
        assert abs(mem.session_win_rate() - 0.7) < 0.01

    def test_to_dict_roundtrip(self) -> None:
        mem = MarketMemory(symbol="SOL", total_pnl=42.5, total_fills=100)
        d = mem.to_dict()
        assert d["symbol"] == "SOL"
        assert d["total_pnl"] == 42.5
        mem2 = MarketMemory(**d)
        assert mem2.symbol == "SOL"
        assert mem2.total_pnl == 42.5


class TestMemoryStore:
    def test_recall_new_symbol(self, store: MemoryStore) -> None:
        mem = store.recall("BTC")
        assert mem.symbol == "BTC"
        assert mem.total_sessions == 0

    def test_save_and_recall(self, store: MemoryStore) -> None:
        mem = MarketMemory(symbol="ETH", total_pnl=25.0, total_fills=50)
        store.save(mem)

        recalled = store.recall("ETH")
        assert recalled.total_pnl == 25.0
        assert recalled.total_fills == 50

    def test_case_insensitive_recall(self, store: MemoryStore) -> None:
        mem = MarketMemory(symbol="BTC", total_pnl=10.0)
        store.save(mem)

        recalled = store.recall("btc")
        assert recalled.symbol == "BTC"

    def test_ingest_session_creates_memory(self, store: MemoryStore) -> None:
        summary = SessionSummary(
            run_id="run-001",
            symbol="XRP",
            total_fills=20,
            adverse_fills=5,
            fill_rate=0.7,
            realized_pnl=15.0,
            avg_spread_bps=120.0,
            avg_vol_bps=45.0,
            high_vol_fraction=0.1,
            max_position=80.0,
            decisions=100,
        )
        mem = store.ingest_session(summary)
        assert mem.symbol == "XRP"
        assert mem.total_sessions == 1
        assert mem.total_fills == 20
        assert mem.adverse_fills == 5
        assert mem.total_pnl == 15.0
        assert mem.win_sessions == 1
        assert mem.best_session_pnl == 15.0
        assert mem.max_position_seen == 80.0

    def test_ingest_multiple_sessions(self, store: MemoryStore) -> None:
        s1 = SessionSummary(
            run_id="run-001", symbol="BTC",
            total_fills=10, adverse_fills=3, fill_rate=0.6,
            realized_pnl=20.0, avg_spread_bps=100.0, decisions=50,
        )
        s2 = SessionSummary(
            run_id="run-002", symbol="BTC",
            total_fills=15, adverse_fills=7, fill_rate=0.8,
            realized_pnl=-5.0, avg_spread_bps=150.0, decisions=60,
        )
        store.ingest_session(s1)
        mem = store.ingest_session(s2)

        assert mem.total_sessions == 2
        assert mem.total_fills == 25
        assert mem.adverse_fills == 10
        assert mem.total_pnl == 15.0
        assert mem.win_sessions == 1
        assert mem.best_session_pnl == 20.0
        assert mem.worst_session_pnl == -5.0

    def test_list_symbols(self, store: MemoryStore) -> None:
        store.save(MarketMemory(symbol="BTC"))
        store.save(MarketMemory(symbol="ETH"))
        store.save(MarketMemory(symbol="SOL"))

        symbols = store.list_symbols()
        assert symbols == ["BTC", "ETH", "SOL"]

    def test_get_session_history(self, store: MemoryStore) -> None:
        for i in range(5):
            store.ingest_session(SessionSummary(
                run_id=f"run-{i:03d}", symbol="BTC",
                total_fills=10, realized_pnl=float(i),
                ts_ms=1000 * (i + 1),
            ))

        history = store.get_session_history("BTC", limit=3)
        assert len(history) == 3
        # Most recent first
        assert history[0].run_id == "run-004"

    def test_get_all_memories(self, store: MemoryStore) -> None:
        store.save(MarketMemory(symbol="BTC", total_pnl=10.0))
        store.save(MarketMemory(symbol="ETH", total_pnl=20.0))

        all_mem = store.get_all_memories()
        assert len(all_mem) == 2
        assert all_mem[0].symbol == "BTC"
        assert all_mem[1].symbol == "ETH"

    def test_persistence_across_connections(self, tmp_path: Path) -> None:
        db_path = tmp_path / "persist_test.db"

        store1 = MemoryStore(db_path)
        store1.save(MarketMemory(symbol="BTC", total_pnl=42.0))
        store1.close()

        store2 = MemoryStore(db_path)
        mem = store2.recall("BTC")
        assert mem.total_pnl == 42.0
        store2.close()

    def test_market_slug_isolation(self, store: MemoryStore) -> None:
        store.save(MarketMemory(symbol="BTC", market_slug="btc-updown-15m", total_pnl=10.0))
        store.save(MarketMemory(symbol="BTC", market_slug="btc-updown-1h", total_pnl=20.0))

        mem_15m = store.recall("BTC", "btc-updown-15m")
        mem_1h = store.recall("BTC", "btc-updown-1h")
        assert mem_15m.total_pnl == 10.0
        assert mem_1h.total_pnl == 20.0
