import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardQuoteRiskSummary(unittest.TestCase):
    def test_active_quote_summary_and_portfolio_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = root / "meta"
            meta.mkdir(parents=True, exist_ok=True)
            (meta / "status.json").write_text(
                json.dumps(
                    {
                        "mode": "PAPER",
                        "stage": "running",
                        "config": {"trade_size": 12.0, "max_size": 150.0, "fee_bps": 25.0, "fee_mode": "maker"},
                    }
                ),
                encoding="utf-8",
            )
            db_path = root / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT, action TEXT, p_hat REAL, expected_edge REAL, expected_cost REAL, policy_json TEXT)")
                cx.execute("CREATE TABLE inventory(ts_ms INTEGER, token_id TEXT, yes_qty REAL, no_qty REAL, usdc REAL, source TEXT)")
                cx.execute("CREATE TABLE fills(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, side TEXT, fill_price REAL, fill_qty REAL, payload_json TEXT)")
                cx.execute("CREATE TABLE open_orders_snapshot(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, side TEXT, price REAL, size REAL, status TEXT, client_order_id TEXT, quote_group_id TEXT)")
                cx.execute("CREATE TABLE system_state(as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
                cx.execute("CREATE TABLE paper_pnl(ts_ms INTEGER, event_id INTEGER, market_slug TEXT, token_id TEXT, realized_gross_pnl REAL, realized_net_pnl REAL, unrealized_pnl REAL, cumulative_fees REAL, turnover REAL, win_count INTEGER, loss_count INTEGER, payload_json TEXT)")
                cx.execute("CREATE TABLE execution_quality(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, side TEXT, fill_ts_ms INTEGER, realized_spread_bps REAL, markout_5s_bps REAL, net_edge_bps REAL)")

                cx.execute("INSERT INTO decisions VALUES (1000, 'd1', 'btc-updown-15m-1700000000', 't1', 'BUY', 0.50, 0.03, 0.01, '{}')")
                cx.execute("INSERT INTO inventory VALUES (1000, 't1', 10.0, 0.0, NULL, 'PAPER')")
                cx.execute("INSERT INTO fills VALUES (1000, 'f1', 'o1', 't1', 'buy', 0.50, 1.0, '{\"is_hedge\": false}')")
                cx.execute("INSERT INTO open_orders_snapshot VALUES (1000, 'e1', 'o-bid', 't1', 'buy', 0.49, 5.0, 'open', 'cid1', 'qg1')")
                cx.execute("INSERT INTO open_orders_snapshot VALUES (1000, 'e2', 'o-ask', 't1', 'sell', 0.51, 5.0, 'open', 'cid2', 'qg1')")
                cx.execute(
                    "INSERT INTO system_state VALUES (1000, 0, '', 'PAPER', ?)",
                    (json.dumps({"broker_stats": {"realized_net_pnl": 2.0}, "config": {"trade_size": 12.0, "max_size": 150.0}}),),
                )
                cx.execute("INSERT INTO paper_pnl VALUES (1000, 1, 'btc-updown-15m-1700000000', 't1', 2.2, 2.0, 1.0, 0.1, 12.0, 1, 0, '{}')")
                cx.execute("INSERT INTO execution_quality VALUES (1000, 'eq1', 'o1', 't1', 'buy', 1000, 7.0, 4.0, 6.0)")
                cx.commit()
            finally:
                cx.close()

            quotes = da.get_active_quote_summary(as_of_ts_ms=1500, db_path=db_path)
            summary = da.get_portfolio_risk_summary(as_of_ts_ms=1500, db_path=db_path)

        self.assertEqual(len(quotes), 1)
        self.assertEqual(str(quotes.iloc[0]["quote_state"]), "live")
        self.assertAlmostEqual(float(quotes.iloc[0]["offered_spread_bps"]), 400.0)
        self.assertAlmostEqual(float(summary["current_edge"]), 0.02)
        self.assertAlmostEqual(float(summary["offered_spread_bps"]), 400.0)
        self.assertAlmostEqual(float(summary["total_pnl"]), 3.0)
        self.assertAlmostEqual(float(summary["max_risk_per_trade_usd"]), 6.0)


if __name__ == "__main__":
    unittest.main()
