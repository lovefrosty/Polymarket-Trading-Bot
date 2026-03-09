import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dashboard import data_access as da


class TestDashboardPaperTelemetryV1(unittest.TestCase):
    def test_runtime_risk_snapshot_parses_latest_system_state_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            now_ms = int(time.time() * 1000)
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute("CREATE TABLE system_state(as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
                payload = {
                    "alert_state": "OK",
                    "paper_trading_profile": {
                        "single_level_quoting": True,
                        "maker_quote_size": 2.0,
                        "maker_half_spread_bps": 35.0,
                        "inventory_skew_per_unit": 0.01,
                        "risk_padding_bps": 5.0,
                        "max_orders_per_min": 12,
                        "max_cancels_per_min": 8,
                        "max_daily_loss_usdc": 45.0,
                        "max_daily_notional_usdc": 900.0,
                        "caps": {"portfolio": {"gross": 100.0, "net": 50.0}},
                        "rollover_guard": {"active": False},
                        "sim_latency_ms": 7,
                        "sim_fee_mode": "TAKE",
                        "fill_model": "book_vwap",
                    },
                    "paper_trading_utilization": {
                        "orders_per_min": 3,
                        "cancels_per_min": 1,
                        "daily_notional_usdc": 120.0,
                        "daily_loss_usdc": 4.5,
                        "open_quote_count": 2,
                        "active_risk_reasons": ["RISK_ORDER_RATE_LIMIT"],
                        "orders_per_min_ratio": 0.25,
                        "cancels_per_min_ratio": 0.125,
                        "daily_notional_ratio": 0.1333,
                        "daily_loss_ratio": 0.10,
                        "cap_state_by_token": {
                            "t1": {
                                "yes_qty": 2.0,
                                "no_qty": 1.0,
                                "token_notional": 1.2,
                                "token_net_notional": 0.6,
                                "hard_breach": False,
                                "soft_breach": True,
                                "reason_codes": ["RISK_CAP_SOFT"],
                            }
                        },
                        "portfolio_cap_state": {
                            "gross": 1.2,
                            "net": 0.6,
                            "gross_limit": 100.0,
                            "net_limit": 50.0,
                        },
                    },
                }
                cx.execute(
                    "INSERT INTO system_state VALUES (1000, 0, '', 'PAPER', ?)",
                    (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
                )
                cx.commit()
            finally:
                cx.close()

            with sqlite3.connect(db_path.as_posix()) as cx_update:
                cx_update.execute("UPDATE system_state SET as_of_ts = ?", (now_ms,))
                cx_update.commit()

            snapshot = da.get_latest_runtime_risk_snapshot(db_path=db_path, stale_after_ms=10_000)

        self.assertTrue(bool(snapshot["available"]))
        self.assertFalse(bool(snapshot["stale"]))
        self.assertEqual(str(snapshot["mode"]), "PAPER")
        self.assertEqual(str(snapshot["alert_state"]), "OK")
        self.assertTrue(bool(snapshot["profile"]["single_level_quoting"]))
        self.assertEqual(int(snapshot["profile"]["sim_latency_ms"]), 7)
        self.assertEqual(str(snapshot["profile"]["sim_fee_mode"]), "TAKE")
        self.assertEqual(int(snapshot["utilization"]["orders_per_min"]), 3)
        self.assertEqual(snapshot["utilization"]["active_risk_reasons"], ["RISK_ORDER_RATE_LIMIT"])
        self.assertTrue(bool(snapshot["utilization"]["cap_state_by_token"]["t1"]["soft_breach"]))
        self.assertAlmostEqual(float(snapshot["utilization"]["portfolio_cap_state"]["gross_limit"]), 100.0)

    def test_runtime_risk_snapshot_defaults_when_contract_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute("CREATE TABLE system_state(as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
                cx.execute("INSERT INTO system_state VALUES (1000, 1, 'A_PSTAR_INVALID', 'PAPER', '{}')")
                cx.commit()
            finally:
                cx.close()

            snapshot = da.get_latest_runtime_risk_snapshot(db_path=db_path, stale_after_ms=10_000_000)

        self.assertFalse(bool(snapshot["available"]))
        self.assertTrue(bool(snapshot["is_frozen"]))
        self.assertEqual(snapshot["reasons"], ["A_PSTAR_INVALID"])
        self.assertEqual(snapshot["utilization"]["cap_state_by_token"], {})
        self.assertEqual(snapshot["utilization"]["portfolio_cap_state"]["gross"], 0.0)

    def test_paper_fill_telemetry_parses_sim_fill_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            cx = sqlite3.connect(db_path.as_posix())
            try:
                cx.execute(
                    "CREATE TABLE fills(ts_ms INTEGER, event_id TEXT, order_id TEXT, token_id TEXT, market_slug TEXT, side TEXT, fill_price REAL, fill_qty REAL, liquidity TEXT, payload_json TEXT)"
                )
                cx.execute("CREATE TABLE decisions(ts_ms INTEGER, decision_id TEXT, market TEXT, token_id TEXT)")
                cx.execute("INSERT INTO decisions VALUES (1000, 'd1', 'btc-updown-15m-1700000000', 't1')")
                payload = {
                    "broker": "sim",
                    "simulated": True,
                    "asset_id": "t1",
                    "mode": "TAKE",
                    "client_order_id": "cid-1",
                    "vwap_price": 0.51,
                    "depth_at_qty": 2.0,
                    "slippage_bps": 10.5,
                    "spread_bps": 400.0,
                    "book_age_ms": 25.0,
                    "fee_bps": 12.0,
                    "fee_mode": "TAKE",
                    "fill_model": "book_vwap",
                    "latency_ms": 7,
                }
                cx.execute(
                    "INSERT INTO fills VALUES (1000, 'f1', 'o1', 't1', NULL, 'buy', 0.51, 2.0, 'taker', ?)",
                    (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
                )
                cx.commit()
            finally:
                cx.close()

            out = da.get_paper_fill_telemetry(
                start_ts_ms=900,
                end_ts_ms=1100,
                limit=20,
                db_path=db_path,
            )

        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertTrue(bool(row["simulated"]))
        self.assertEqual(str(row["broker"]), "sim")
        self.assertEqual(str(row["market_slug"]), "btc-updown-15m-1700000000")
        self.assertAlmostEqual(float(row["slippage_bps"]), 10.5)
        self.assertAlmostEqual(float(row["depth_at_qty"]), 2.0)
        self.assertAlmostEqual(float(row["spread_bps"]), 400.0)
        self.assertAlmostEqual(float(row["book_age_ms"]), 25.0)
        self.assertEqual(str(row["fee_mode"]), "TAKE")
        self.assertEqual(str(row["fill_model"]), "book_vwap")


if __name__ == "__main__":
    unittest.main()
