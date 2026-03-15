import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import dashboard.app as app


class TestDashboardTerminalAlignment(unittest.TestCase):
    def test_runtime_schema_uses_live_tables_not_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"
            db_path.write_text("")
            with patch.object(app, "DB_PATH", db_path):
                with patch("dashboard.app.da.existing_tables", return_value=["decisions", "logs", "market_data_book"]):
                    self.assertFalse(app._runtime_schema_missing())

    def test_action_hint_uses_live_policy_thresholds(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "ts": "2026-01-01T00:00:00Z",
                    "decision_id": "d1",
                    "market": "btc-updown-15m-1769544900",
                    "token_id": "0xabc123456789",
                    "action": "SKIP",
                    "strategy": "mm",
                    "p_hat": 0.54,
                    "ev": 0.02,
                    "gate_result": "BLOCK",
                    "reason_codes": "C_SPREAD_TOO_WIDE",
                }
            ]
        )
        registry = {
            "btc-updown-15m-1769544900": {
                "market_label": "BTC 15m Up/Down",
                "token_to_outcome": {"0xabc123456789": "YES"},
            }
        }
        with patch("dashboard.app._active_policy_thresholds", return_value={"max_spread_bps": 500.0, "max_slippage_bps": 200.0, "maker_half_spread_bps": 40.0}):
            out = app.build_signals_table_for_view(df, "trader", registry)
        self.assertEqual(out.iloc[0]["Action hint"], "WAIT for spread <= 500 bps")

    def test_market_eta_detail_does_not_render_closes_in_closed(self) -> None:
        self.assertEqual(app._market_eta_detail("closed"), "closed")
        self.assertEqual(app._market_eta_detail("01:23"), "closes in 01:23")

    def test_recent_order_book_snapshot_reports_rows_and_bbo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.db"
            with sqlite3.connect(db_path) as cx:
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, market TEXT, token_id TEXT)")
                cx.execute("CREATE TABLE market_data_book(ts_ms INTEGER, token_id TEXT, side TEXT, price REAL, size REAL, source TEXT)")
                cx.execute("INSERT INTO decisions VALUES (1000, 'm1', 't1')")
                cx.execute("INSERT INTO market_data_book VALUES (1100, 't1', 'buy', 0.45, 12.0, 'ws')")
                cx.execute("INSERT INTO market_data_book VALUES (1100, 't1', 'sell', 0.47, 10.0, 'ws')")
                cx.commit()
            registry = {"m1": {"market_label": "BTC 15m Up/Down", "token_to_outcome": {"t1": "YES"}}}
            with patch.object(app, "DB_PATH", db_path):
                snapshot, bbo = app._recent_order_book_snapshot("m1", registry, True)
            self.assertEqual(snapshot["row_count"], 2)
            self.assertEqual(snapshot["token_count"], 1)
            self.assertFalse(bbo.empty)
            self.assertEqual(float(bbo.iloc[0]["Best bid"]), 0.45)
            self.assertEqual(float(bbo.iloc[0]["Best ask"]), 0.47)


if __name__ == "__main__":
    unittest.main()
