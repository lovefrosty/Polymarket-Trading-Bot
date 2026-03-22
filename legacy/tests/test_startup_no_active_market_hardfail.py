import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scripts.run_system as run_system
from core.market_discovery import NoActiveMarketError


class TestStartupNoActiveMarketHardFail(unittest.TestCase):
    def test_startup_raises_and_writes_none_found_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "runtime.db"
            log_dir = tmp_path / "logs"
            markets_path = tmp_path / "markets.json"
            markets_path.write_text(
                json.dumps(
                    {
                        "markets": [
                            {
                                "name": "BTC 15m",
                                "condition_id": "",
                                "token_ids": [],
                                "slug_prefix": None,
                                "reference_symbol": "BTC",
                                "min_tick": 0.01,
                                "min_size": 1.0,
                                "max_price": 0.99,
                                "min_price": 0.01,
                                "auto_discover": {"symbol": "BTC", "horizon": "15m", "mode": "latest_active"},
                            }
                        ]
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                mode="OBSERVE",
                markets=str(markets_path),
                log_dir=str(log_dir),
                db_path=str(db_path),
                constitution=None,
                auto_discover=True,
                reference_source=None,
                quote_interval_ms=None,
                stats_interval_ms=None,
                dry_run=False,
            )

            async def _raise_no_active(*_args, **_kwargs):
                raise NoActiveMarketError(
                    market_key="btc_15m",
                    now_ms=1_800_000_000_000,
                    diagnostics={"n_active_now": 0},
                    request_payload={
                        "event": "DISCOVERY_REQUEST",
                        "status": "NONE_FOUND",
                        "requested_symbol": "BTC",
                        "requested_horizon": "15m",
                        "requested_mode": "latest_active",
                        "now_wall_ms": 1_800_000_000_000,
                        "n_total": 1,
                        "n_btc_15m": 1,
                        "n_with_end_ts": 1,
                        "n_active_now": 0,
                        "error_code": "NO_ACTIVE_BTC_15M",
                    },
                )

            with mock.patch("scripts.run_system._parse_args", return_value=args), mock.patch(
                "scripts.run_system.resolve_markets",
                side_effect=_raise_no_active,
            ):
                with self.assertRaises(NoActiveMarketError):
                    asyncio.run(run_system._run())

            cx = sqlite3.connect(db_path.as_posix())
            try:
                row = cx.execute(
                    "SELECT status, reason_code FROM discovery_requests ORDER BY ts_ms DESC LIMIT 1"
                ).fetchone()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row[0], "NONE_FOUND")
                self.assertEqual(row[1], "NO_ACTIVE_BTC_15M")
            finally:
                cx.close()


if __name__ == "__main__":
    unittest.main()
