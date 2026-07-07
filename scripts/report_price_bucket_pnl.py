from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, Optional


@dataclass
class BucketStats:
    bucket: str
    low: float
    high: float
    fills: int = 0
    buys: int = 0
    sells: int = 0
    qty: float = 0.0
    gross_notional: float = 0.0
    fees: float = 0.0
    realized_net_pnl_delta: float = 0.0
    fill_price_qty_sum: float = 0.0
    reference_price_qty_sum: float = 0.0
    boundary_active_fills: int = 0
    boundary_blocked_fills: int = 0
    boundary_scores: list[float] = field(default_factory=list)
    boundary_reasons: Counter[str] = field(default_factory=Counter)
    realized_spread_bps: list[float] = field(default_factory=list)
    net_edge_bps: list[float] = field(default_factory=list)
    markout_1s_bps: list[float] = field(default_factory=list)
    markout_5s_bps: list[float] = field(default_factory=list)
    quote_modes: Counter[str] = field(default_factory=Counter)

    def add_fill(
        self,
        *,
        side: str,
        qty: float,
        fill_price: float,
        reference_price: float,
        gross_notional: float,
        fee_usdc: float,
        realized_net_pnl_delta: float,
        placement: Dict[str, Any],
        quality: Dict[str, Optional[float]],
    ) -> None:
        self.fills += 1
        if side == "buy":
            self.buys += 1
        elif side == "sell":
            self.sells += 1
        self.qty += qty
        self.gross_notional += gross_notional
        self.fees += fee_usdc
        self.realized_net_pnl_delta += realized_net_pnl_delta
        self.fill_price_qty_sum += fill_price * qty
        self.reference_price_qty_sum += reference_price * qty
        if bool(placement.get("price_boundary_active")):
            self.boundary_active_fills += 1
        if bool(placement.get("price_boundary_buy_blocked")):
            self.boundary_blocked_fills += 1
        boundary_score = _maybe_float(placement.get("price_boundary_score"))
        if boundary_score is not None:
            self.boundary_scores.append(boundary_score)
        boundary_reason = str(placement.get("price_boundary_reason") or "")
        if boundary_reason:
            self.boundary_reasons[boundary_reason] += 1
        quote_mode = str(placement.get("quote_mode") or "unknown")
        self.quote_modes[quote_mode] += 1
        for key, target in (
            ("realized_spread_bps", self.realized_spread_bps),
            ("net_edge_bps", self.net_edge_bps),
            ("markout_1s_bps", self.markout_1s_bps),
            ("markout_5s_bps", self.markout_5s_bps),
        ):
            value = quality.get(key)
            if value is not None:
                target.append(float(value))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "bucket": self.bucket,
            "low": self.low,
            "high": self.high,
            "fills": self.fills,
            "buys": self.buys,
            "sells": self.sells,
            "qty": round(self.qty, 8),
            "gross_notional": round(self.gross_notional, 8),
            "fees": round(self.fees, 8),
            "realized_net_pnl_delta": round(self.realized_net_pnl_delta, 8),
            "avg_fill_price": _safe_div(self.fill_price_qty_sum, self.qty),
            "avg_reference_price": _safe_div(self.reference_price_qty_sum, self.qty),
            "boundary_active_fills": self.boundary_active_fills,
            "boundary_blocked_fills": self.boundary_blocked_fills,
            "avg_boundary_score": _avg(self.boundary_scores),
            "boundary_reasons": dict(self.boundary_reasons.most_common()),
            "avg_realized_spread_bps": _avg(self.realized_spread_bps),
            "avg_net_edge_bps": _avg(self.net_edge_bps),
            "avg_markout_1s_bps": _avg(self.markout_1s_bps),
            "avg_markout_5s_bps": _avg(self.markout_5s_bps),
            "quote_modes": dict(self.quote_modes.most_common()),
        }


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 8)


def _avg(values: Iterable[float]) -> Optional[float]:
    items = list(values)
    if not items:
        return None
    return round(sum(items) / len(items), 8)


def _float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_dict(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _bucket_for_price(price: float, bucket_size: float) -> tuple[str, float, float]:
    clamped = min(max(float(price), 0.0), 1.0 - 1e-9)
    low = math.floor(clamped / bucket_size) * bucket_size
    high = min(1.0, low + bucket_size)
    return f"{low:.2f}-{high:.2f}", round(low, 8), round(high, 8)


def _latest_execution_quality_by_order(cx: sqlite3.Connection) -> Dict[str, Dict[str, Optional[float]]]:
    try:
        rows = cx.execute(
            """
            SELECT order_id, ts_ms, realized_spread_bps, net_edge_bps, markout_1s_bps, markout_5s_bps
            FROM execution_quality
            WHERE order_id IS NOT NULL
            ORDER BY ts_ms ASC, event_id ASC
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    quality: Dict[str, Dict[str, Optional[float]]] = {}
    for row in rows:
        order_id = str(row["order_id"] or "")
        if not order_id:
            continue
        quality[order_id] = {
            "realized_spread_bps": _maybe_float(row["realized_spread_bps"]),
            "net_edge_bps": _maybe_float(row["net_edge_bps"]),
            "markout_1s_bps": _maybe_float(row["markout_1s_bps"]),
            "markout_5s_bps": _maybe_float(row["markout_5s_bps"]),
        }
    return quality


def _maybe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_price_bucket_report(runtime_root: Path, *, bucket_size: float = 0.10) -> Dict[str, Any]:
    root = Path(runtime_root)
    db_path = root / "runtime.db"
    if not db_path.exists():
        raise FileNotFoundError(db_path.as_posix())
    if bucket_size <= 0.0 or bucket_size > 1.0:
        raise ValueError("bucket_size must be in (0, 1]")

    cx = sqlite3.connect(db_path.as_posix())
    cx.row_factory = sqlite3.Row
    quality_by_order = _latest_execution_quality_by_order(cx)
    buckets: Dict[str, BucketStats] = {}
    total = BucketStats(bucket="total", low=0.0, high=1.0)
    rows = cx.execute(
        """
        SELECT order_id, token_id, side, fill_price, fill_qty, payload_json
        FROM fills
        ORDER BY ts_ms ASC, event_id ASC
        """
    ).fetchall()
    for row in rows:
        payload = _json_dict(row["payload_json"])
        placement = dict(payload.get("placement_metadata") or {})
        fill_price = _float(row["fill_price"])
        qty = _float(row["fill_qty"])
        if qty <= 0.0:
            continue
        reference_price = _float(placement.get("mid"), fill_price)
        bucket_name, low, high = _bucket_for_price(reference_price, bucket_size)
        bucket = buckets.setdefault(bucket_name, BucketStats(bucket=bucket_name, low=low, high=high))
        side = str(row["side"] or "").lower()
        gross_notional = _float(payload.get("gross_notional"), fill_price * qty)
        fee_usdc = _float(payload.get("fee_usdc"))
        realized_net = _float(payload.get("realized_net_pnl_delta"))
        quality = quality_by_order.get(str(row["order_id"] or ""), {})
        for target in (bucket, total):
            target.add_fill(
                side=side,
                qty=qty,
                fill_price=fill_price,
                reference_price=reference_price,
                gross_notional=gross_notional,
                fee_usdc=fee_usdc,
                realized_net_pnl_delta=realized_net,
                placement=placement,
                quality=quality,
            )
    cx.close()
    ordered_buckets = [stats.as_dict() for stats in sorted(buckets.values(), key=lambda item: item.low)]
    return {
        "runtime_root": root.as_posix(),
        "db_path": db_path.as_posix(),
        "bucket_size": bucket_size,
        "total": total.as_dict(),
        "buckets": ordered_buckets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize core_mm fills by reference price bucket")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--bucket-size", type=float, default=0.10)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root)
    output = Path(args.output) if args.output else runtime_root / "meta" / "price_bucket_report.json"
    report = build_price_bucket_report(runtime_root, bucket_size=float(args.bucket_size))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(output.as_posix())


if __name__ == "__main__":
    main()
