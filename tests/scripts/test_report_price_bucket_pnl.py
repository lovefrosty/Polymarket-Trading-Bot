import json
import sqlite3
from pathlib import Path

import pytest

from scripts.report_price_bucket_pnl import build_price_bucket_report


def test_price_bucket_report_uses_reference_mid_and_quality(tmp_path: Path) -> None:
    runtime_root = tmp_path / "run"
    runtime_root.mkdir()
    cx = sqlite3.connect((runtime_root / "runtime.db").as_posix())
    cx.executescript(
        """
        CREATE TABLE fills (
          ts_ms INTEGER,
          event_id INTEGER,
          order_id TEXT,
          token_id TEXT,
          side TEXT,
          fill_price REAL,
          fill_qty REAL,
          payload_json TEXT
        );
        CREATE TABLE execution_quality (
          ts_ms INTEGER,
          event_id INTEGER,
          order_id TEXT,
          realized_spread_bps REAL,
          net_edge_bps REAL,
          markout_1s_bps REAL,
          markout_5s_bps REAL
        );
        """
    )
    cx.execute(
        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            1,
            "o1",
            "yes",
            "buy",
            0.09,
            10.0,
            json.dumps(
                {
                    "gross_notional": 0.9,
                    "fee_usdc": 0.01,
                    "realized_net_pnl_delta": -0.2,
                    "placement_metadata": {
                        "mid": 0.08,
                        "quote_mode": "normal",
                        "price_boundary_active": True,
                        "price_boundary_score": 0.57,
                        "price_boundary_reason": "adaptive_tail_adverse_selection_low",
                    },
                }
            ),
        ),
    )
    cx.execute(
        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            2,
            2,
            "o2",
            "no",
            "sell",
            0.55,
            5.0,
            json.dumps(
                {
                    "gross_notional": 2.75,
                    "fee_usdc": 0.0,
                    "realized_net_pnl_delta": 0.1,
                    "placement_metadata": {"mid": 0.55, "quote_mode": "risk_exit"},
                }
            ),
        ),
    )
    cx.execute(
        "INSERT INTO execution_quality VALUES (?, ?, ?, ?, ?, ?, ?)",
        (3, 1, "o1", 5.0, -6.0, -12.0, -20.0),
    )
    cx.commit()
    cx.close()

    report = build_price_bucket_report(runtime_root, bucket_size=0.10)

    assert report["total"]["fills"] == 2
    assert report["total"]["realized_net_pnl_delta"] == pytest.approx(-0.1)
    tail_bucket = report["buckets"][0]
    assert tail_bucket["bucket"] == "0.00-0.10"
    assert tail_bucket["fills"] == 1
    assert tail_bucket["buys"] == 1
    assert tail_bucket["boundary_active_fills"] == 1
    assert tail_bucket["avg_boundary_score"] == pytest.approx(0.57)
    assert tail_bucket["boundary_reasons"] == {"adaptive_tail_adverse_selection_low": 1}
    assert tail_bucket["avg_reference_price"] == pytest.approx(0.08)
    assert tail_bucket["avg_markout_1s_bps"] == pytest.approx(-12.0)
