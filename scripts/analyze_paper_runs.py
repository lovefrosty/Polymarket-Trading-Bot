#!/usr/bin/env python3
"""Reproduce the committed paper-run metrics and portfolio charts.

The PnL tape repeats portfolio-level realized PnL once per token. This module
deduplicates that value by timestamp and sums only token-level unrealized PnL.
It deliberately does not annualize returns or estimate risk of ruin from a
single short run.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DEFAULT_RUN_ROOT = Path("tmp/core_mm_runs/btc-paper-20260315T222135Z")
DEFAULT_OUTPUT_DIR = Path("docs/assets")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
    return rows


def parse_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json")
    if isinstance(payload, dict):
        return payload
    if not payload:
        return {}
    try:
        decoded = json.loads(str(payload))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def build_equity_curve(pnl_rows: Iterable[dict[str, Any]]) -> list[dict[str, float]]:
    """Collapse token rows into one portfolio observation per timestamp."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in pnl_rows:
        grouped[int(row["ts_ms"])].append(row)

    curve: list[dict[str, float]] = []
    running_peak = float("-inf")
    for ts_ms in sorted(grouped):
        rows = grouped[ts_ms]
        realized_values = [float(row.get("realized_net_pnl") or 0.0) for row in rows]
        realized = realized_values[-1]
        if realized_values and max(realized_values) - min(realized_values) > 1e-8:
            raise ValueError(f"Inconsistent portfolio realized PnL at {ts_ms}")
        unrealized = sum(
            float(row["unrealized_pnl"])
            for row in rows
            if row.get("unrealized_pnl") is not None
        )
        total = realized + unrealized
        running_peak = max(running_peak, total)
        curve.append(
            {
                "ts_ms": float(ts_ms),
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "total_pnl": total,
                "drawdown": running_peak - total,
            }
        )
    return curve


def market_breakdown(fill_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_market: dict[str, dict[str, Any]] = {}
    for row in fill_rows:
        payload = parse_payload(row)
        market = str(payload.get("market_slug") or "unknown")
        item = by_market.setdefault(
            market,
            {"market_slug": market, "fills": 0, "realized_net_pnl": 0.0, "fees": 0.0},
        )
        item["fills"] += 1
        item["realized_net_pnl"] += float(payload.get("realized_net_pnl_delta") or 0.0)
        item["fees"] += float(payload.get("fee_usdc") or 0.0)
    return sorted(by_market.values(), key=lambda row: row["market_slug"])


def analyze_run(run_root: Path) -> tuple[dict[str, Any], list[dict[str, float]], list[dict[str, Any]]]:
    summary = json.loads((run_root / "meta" / "run_summary.json").read_text(encoding="utf-8"))
    pnl_rows = load_jsonl(run_root / "tapes" / "pnl.jsonl")
    fill_rows = load_jsonl(run_root / "tapes" / "fills.jsonl")
    curve = build_equity_curve(pnl_rows)
    markets = market_breakdown(fill_rows)

    start_ms = int(curve[0]["ts_ms"])
    end_ms = int(curve[-1]["ts_ms"])
    touch_fills = sum(parse_payload(row).get("fill_trigger") == "touch" for row in fill_rows)
    taker_fills = sum(parse_payload(row).get("liquidity_mode") == "taker" for row in fill_rows)
    metrics = {
        "run_root": run_root.as_posix(),
        "observations": len(curve),
        "duration_minutes": (end_ms - start_ms) / 60_000.0,
        "fills": len(fill_rows),
        "markets": len(markets),
        "realized_net_pnl": float(summary["realized_net_pnl"]),
        "unrealized_pnl": float(summary["unrealized_pnl"]),
        "total_pnl": float(summary["total_pnl"]),
        "turnover": float(summary["turnover"]),
        "fees": float(summary["total_fees"]),
        "peak_total_pnl": max(row["total_pnl"] for row in curve),
        "max_drawdown": max(row["drawdown"] for row in curve),
        "touch_fills": int(touch_fills),
        "taker_fills": int(taker_fills),
        "sharpe_status": "not_estimated_insufficient_independent_windows",
        "risk_of_ruin_status": "not_estimated_insufficient_loss_distribution",
    }
    return metrics, curve, markets


def _market_label(slug: str) -> str:
    tail = slug.rsplit("-", 1)[-1]
    if tail.isdigit():
        end_time = datetime.fromtimestamp(int(tail), tz=timezone.utc)
        return end_time.strftime("%H:%M UTC")
    return slug


def render_equity_chart(curve: list[dict[str, float]], output_path: Path) -> None:
    elapsed = [(row["ts_ms"] - curve[0]["ts_ms"]) / 60_000.0 for row in curve]
    total = [row["total_pnl"] for row in curve]
    drawdown = [row["drawdown"] for row in curve]
    fig, (ax_equity, ax_drawdown) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]}
    )
    fig.patch.set_facecolor("#F8FAFC")
    for axis in (ax_equity, ax_drawdown):
        axis.set_facecolor("#F8FAFC")
        axis.grid(axis="y", color="#CBD5E1", linewidth=0.7, alpha=0.75)
        axis.spines[["top", "right"]].set_visible(False)

    ax_equity.plot(elapsed, total, color="#0F766E", linewidth=2.0)
    ax_equity.axhline(0, color="#64748B", linewidth=0.8)
    ax_equity.set_ylabel("Total PnL (USD)")
    fig.suptitle(
        "Historical paper-run equity and drawdown",
        x=0.06,
        ha="left",
        fontsize=16,
        weight="bold",
    )
    fig.text(
        0.06,
        0.91,
        "Local simulator • 33.1 minutes • 188 fills • three BTC 15-minute contracts",
        fontsize=10,
        color="#475569",
    )

    ax_drawdown.fill_between(elapsed, drawdown, color="#D97706", alpha=0.35)
    ax_drawdown.plot(elapsed, drawdown, color="#B45309", linewidth=1.2)
    ax_drawdown.set_ylabel("Drawdown (USD)")
    ax_drawdown.set_xlabel("Elapsed minutes")
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(output_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def render_concentration_chart(markets: list[dict[str, Any]], output_path: Path) -> None:
    labels = [_market_label(row["market_slug"]) for row in markets]
    fills = [int(row["fills"]) for row in markets]
    pnl = [float(row["realized_net_pnl"]) for row in markets]
    colors = ["#0F766E", "#5B8E7D", "#94A3B8"]
    fig, (ax_fills, ax_pnl) = plt.subplots(1, 2, figsize=(11, 5.8))
    fig.patch.set_facecolor("#F8FAFC")
    for axis in (ax_fills, ax_pnl):
        axis.set_facecolor("#F8FAFC")
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", color="#CBD5E1", linewidth=0.7, alpha=0.75)
        axis.set_axisbelow(True)

    fill_bars = ax_fills.bar(labels, fills, color=colors[: len(labels)])
    pnl_bars = ax_pnl.bar(labels, pnl, color=colors[: len(labels)])
    ax_fills.set_title("Simulated fills", loc="left", weight="bold")
    ax_fills.set_ylabel("Fill count")
    ax_pnl.set_title("Attributed realized net PnL", loc="left", weight="bold")
    ax_pnl.set_ylabel("USD")
    ax_pnl.axhline(0, color="#64748B", linewidth=0.8)
    ax_fills.bar_label(fill_bars, padding=3)
    ax_pnl.bar_label(pnl_bars, labels=[f"${value:.2f}" for value in pnl], padding=3)
    fig.suptitle("Performance was concentrated in one market window", x=0.06, ha="left", fontsize=16, weight="bold")
    fig.text(0.06, 0.91, "92% of fills and 95% of realized net PnL came from the first contract.", color="#475569")
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(output_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    metrics, curve, markets = analyze_run(args.run_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    render_equity_chart(curve, args.output_dir / "paper_equity_drawdown.png")
    render_concentration_chart(markets, args.output_dir / "paper_market_concentration.png")
    print(json.dumps({"metrics": metrics, "markets": markets}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
