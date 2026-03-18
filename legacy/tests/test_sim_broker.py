from __future__ import annotations

import unittest

from core.broker_base import OrderIntent
from core.broker_sim import SimBroker, SimBrokerConfig
from core.decision_tape import TimeMapper
from core.order_book import OrderBook
from core.validators import OrderConstraints


class TestSimBroker(unittest.TestCase):
    def _make_broker(self, last_recv_mono_ns: int) -> SimBroker:
        book = OrderBook(asset_id="asset", bids={}, asks={})
        book.apply_snapshot(
            bids=[(0.49, 10.0)],
            asks=[(0.51, 10.0)],
            event_ts_ms=1000,
            recv_mono_ns=last_recv_mono_ns,
        )
        constraints = {
            "asset": OrderConstraints(
                min_tick=0.01,
                min_size=1.0,
                min_price=0.01,
                max_price=0.99,
                max_spread_bps=10000.0,
                max_slippage_bps=10000.0,
                max_book_staleness_ms=2000,
            )
        }
        books = {"asset": book}
        time_mapper = TimeMapper.from_wall_and_mono(wall_ms=1000, mono_ns=1_000_000_000)
        return SimBroker(
            books=books,
            constraints=constraints,
            time_mapper=time_mapper,
            config=SimBrokerConfig(latency_ms=0),
        )

    def test_sim_broker_determinism(self) -> None:
        broker = self._make_broker(last_recv_mono_ns=1_000_000_000)
        intent = OrderIntent(
            order_id="o1",
            client_order_id="c1",
            asset_id="asset",
            side="buy",
            size=1.0,
            price=0.51,
            mode="TAKE",
            t_decision_wall_ms=1000,
            as_of_ts_ms=1000,
        )
        first = broker.submit(intent)
        second = broker.submit(intent)
        self.assertEqual(first, second)

    def test_leakage_guard(self) -> None:
        broker = self._make_broker(last_recv_mono_ns=2_000_000_000)
        intent = OrderIntent(
            order_id="o1",
            client_order_id="c1",
            asset_id="asset",
            side="buy",
            size=1.0,
            price=0.51,
            mode="TAKE",
            t_decision_wall_ms=1000,
            as_of_ts_ms=1000,
        )
        events = broker.submit(intent)
        self.assertEqual(events[0].event_type, "order_reject")
        self.assertEqual(events[0].payload.get("error_code"), "LEAKAGE_GUARD")
