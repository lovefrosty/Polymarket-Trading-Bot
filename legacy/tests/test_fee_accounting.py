import json
import tempfile
import unittest
from pathlib import Path

from core.decision_engine import DecisionEngine, DecisionEngineConfig
from core.decision_tape import DecisionTape, TimeMapper
from core.fees import taker_fee_bps_piecewise
from core.order_book import OrderBook
from core.reference_price import ReferencePriceAggregator, ReferenceQuote
from core.validators import OrderConstraints


class TestFeeAccounting(unittest.TestCase):
    def test_edge_net_single_fee(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            tape = DecisionTape(log_dir=str(log_dir), run_id="run")
            book = OrderBook(asset_id="token", bids={}, asks={})
            book.apply_snapshot(bids=[(0.49, 1.0)], asks=[(0.51, 1.0)], event_ts_ms=1000, recv_mono_ns=100)
            constraints = {
                "token": OrderConstraints(
                    min_tick=0.01,
                    min_size=1.0,
                    min_price=0.01,
                    max_price=0.99,
                    max_spread_bps=1000.0,
                    max_slippage_bps=1000.0,
                    max_book_staleness_ms=10_000,
                )
            }
            markets = {
                "token": {
                    "slug": "btc-updown-15m-1704067200",
                    "condition_id": "cond",
                    "outcome": "Up",
                    "outcome_by_token": {"token": "Up"},
                    "reference_symbol": "BTC",
                    "fee": {"status": "ok"},
                }
            }
            aggregator = ReferencePriceAggregator(
                required_sources={"spot", "perp"},
                staleness_ms=10_000,
                disagreement_bps=100.0,
                min_confidence=0.1,
                allowed_symbols={"BTC"},
            )
            time_mapper = TimeMapper.from_wall_and_mono(wall_ms=1704067200000, mono_ns=100)
            engine = DecisionEngine(
                books={"token": book},
                constraints=constraints,
                tape=tape,
                time_mapper=time_mapper,
                config=DecisionEngineConfig(order_size=1.0, fee_rate=0.0025),
                market_meta=markets,
                reference_aggregator=aggregator,
            )

            aggregator.ingest(
                ReferenceQuote(
                    source="spot",
                    symbol="BTC",
                    value=100.0,
                    t_event_ms=1704067190000,
                    t_recv_mono_ns=100,
                    t_recv_wall_iso="2024-01-01T00:00:00.000Z",
                    t_recv_wall_ms=1704067195000,
                )
            )
            aggregator.ingest(
                ReferenceQuote(
                    source="perp",
                    symbol="BTC",
                    value=100.0,
                    t_event_ms=1704067190000,
                    t_recv_mono_ns=100,
                    t_recv_wall_iso="2024-01-01T00:00:00.000Z",
                    t_recv_wall_ms=1704067195000,
                )
            )
            engine._emit_decision("token", 1_000_100, trigger="test")

            aggregator.ingest(
                ReferenceQuote(
                    source="spot",
                    symbol="BTC",
                    value=101.0,
                    t_event_ms=1704067200000,
                    t_recv_mono_ns=200,
                    t_recv_wall_iso="2024-01-01T00:00:00.100Z",
                    t_recv_wall_ms=1704067200000,
                )
            )
            aggregator.ingest(
                ReferenceQuote(
                    source="perp",
                    symbol="BTC",
                    value=101.0,
                    t_event_ms=1704067200000,
                    t_recv_mono_ns=200,
                    t_recv_wall_iso="2024-01-01T00:00:00.100Z",
                    t_recv_wall_ms=1704067200000,
                )
            )
            engine._emit_decision("token", 2_000_100, trigger="test")
            tape.close()

            files = list(log_dir.glob("decision_*.jsonl"))
            self.assertTrue(files)
            records = [json.loads(line) for line in files[0].read_text().splitlines() if line]
            record = records[-1]
            p_fair = record["p_fair"]
            exec_buy = record["p_market_exec_buy"]
            fee_bps = taker_fee_bps_piecewise(exec_buy)
            slippage_bps = record["exec_cost"]["slippage_bps"]
            edge_bps = (p_fair - exec_buy) * 10000.0
            net_edge_bps = edge_bps - fee_bps - float(slippage_bps)
            expected_edge = net_edge_bps / 10000.0
            self.assertAlmostEqual(record["edge_net_buy"], expected_edge, places=9)


if __name__ == "__main__":
    unittest.main()
