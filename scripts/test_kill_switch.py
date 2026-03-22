from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import load_settings
from core_mm.execution import ExecutionAdapter
from core_mm.kalshi.client import KalshiClient, KalshiOrderArgs, load_private_key_from_path
from core_mm.kalshi.market_selector import KalshiMarketSelector, KalshiSelectorConfig


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Kalshi cancel_all kill-switch path with a tiny far-from-market order.")
    parser.add_argument("--ticker", default=None, help="Explicit Kalshi ticker to test, e.g. KXBTC-26MAR2211-B68650")
    parser.add_argument("--symbol", default="BTC", help="Symbol to discover if --ticker is omitted")
    parser.add_argument("--size", type=float, default=1.0, help="Contracts to place for the test order")
    parser.add_argument("--rest-price", type=float, default=None, help="Explicit YES buy price in dollars for the test order")
    parser.add_argument("--report-path", default=None, help="Optional JSON output path")
    parser.add_argument("--poll-secs", type=float, default=0.5, help="Polling interval while waiting for the order/cancellation")
    parser.add_argument("--timeout-secs", type=float, default=5.0, help="Timeout for order visibility and cancellation checks")
    return parser.parse_args()


def _normalize_price(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    return raw / 100.0 if raw > 1.0 else raw


def _extract_orders(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    orders = payload.get("orders") if isinstance(payload, dict) else []
    if not isinstance(orders, list):
        return []
    return [order for order in orders if isinstance(order, dict)]


def _discover_ticker(client: KalshiClient, symbol: str) -> str:
    selector = KalshiMarketSelector(
        client=client,
        config=KalshiSelectorConfig(
            series_ticker=symbol.upper(),
            min_price=0.10,
            max_price=0.90,
            max_results=5,
        ),
    )
    candidates = selector.select_markets()
    if not candidates:
        raise RuntimeError(f"no_quoteable_market_found_for_symbol: {symbol.upper()}")
    return candidates[0].slug


def _derive_rest_price(market: Dict[str, Any], explicit_price: Optional[float]) -> float:
    if explicit_price is not None:
        return max(0.01, min(0.99, round(float(explicit_price), 2)))
    best_bid = _normalize_price(market.get("yes_bid_dollars"))
    best_ask = _normalize_price(market.get("yes_ask_dollars"))
    if best_bid is None:
        best_bid = _normalize_price(market.get("yes_bid"))
    if best_ask is None:
        best_ask = _normalize_price(market.get("yes_ask"))
    anchor = best_bid if best_bid is not None else (best_ask if best_ask is not None else 0.10)
    return max(0.01, min(0.99, round(float(anchor) - 0.10, 2)))


def _find_matching_orders(orders: List[Dict[str, Any]], *, ticker: str, expected_action: str, expected_price: float) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for order in orders:
        if str(order.get("ticker") or "") != ticker:
            continue
        if str(order.get("action") or "").lower() != expected_action:
            continue
        price = _normalize_price(order.get("yes_price_dollars"))
        if price is None:
            price = _normalize_price(order.get("yes_price"))
        if price is None:
            continue
        if abs(price - expected_price) > 0.011:
            continue
        matches.append(order)
    return matches


def _wait_for_order_visibility(adapter: ExecutionAdapter, *, ticker: str, expected_action: str, expected_price: float, timeout_secs: float, poll_secs: float) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        result = adapter.get_open_orders()
        if result.success:
            matches = _find_matching_orders(_extract_orders(result.payload), ticker=ticker, expected_action=expected_action, expected_price=expected_price)
            if matches:
                return matches
        time.sleep(poll_secs)
    return []


def _wait_for_zero_orders(adapter: ExecutionAdapter, *, timeout_secs: float, poll_secs: float) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout_secs
    latest_orders: List[Dict[str, Any]] = []
    while time.time() < deadline:
        result = adapter.get_open_orders()
        if result.success:
            latest_orders = _extract_orders(result.payload)
            if not latest_orders:
                return []
        time.sleep(poll_secs)
    return latest_orders


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    if not settings.kalshi_api_key_id or not settings.kalshi_private_key_path:
        raise RuntimeError("Kalshi kill-switch test requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH.")
    private_key = load_private_key_from_path(settings.kalshi_private_key_path)
    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key=private_key,
        base_url=settings.kalshi_base_url,
    )
    adapter = ExecutionAdapter(
        client=client,
        order_args_type=KalshiOrderArgs,
        order_type=type("_KalshiTIF", (), {"GTC": "gtc"})(),
    )
    report_path = Path(args.report_path) if args.report_path else None

    report: Dict[str, Any] = {
        "status": "failed",
        "exchange": "kalshi",
        "base_url": settings.kalshi_base_url,
        "ticker": None,
        "symbol": str(args.symbol).upper(),
        "size": float(args.size),
        "rest_price": None,
        "before_open_order_count": None,
        "after_place_open_order_count": None,
        "after_cancel_open_order_count": None,
        "order_id": None,
        "checked_at_ms": int(time.time() * 1000),
        "reason": None,
    }

    try:
        ticker = str(args.ticker or _discover_ticker(client, args.symbol))
        market = client.get_market(ticker)
        rest_price = _derive_rest_price(market, args.rest_price)
        report["ticker"] = ticker
        report["rest_price"] = rest_price

        open_before = adapter.get_open_orders()
        if not open_before.success:
            raise RuntimeError(f"preflight_open_orders_failed: {open_before.error or 'unknown_error'}")
        before_orders = _extract_orders(open_before.payload)
        report["before_open_order_count"] = len(before_orders)
        if before_orders:
            raise RuntimeError("preflight_resting_orders_present")

        place_result = adapter.place_order(
            token_id=f"{ticker}:yes",
            side="buy",
            price=rest_price,
            size=float(args.size),
        )
        if not place_result.success:
            raise RuntimeError(f"place_order_failed: {place_result.error or 'unknown_error'}")
        report["order_id"] = str(place_result.payload.get("order_id") or place_result.payload.get("orderID") or "")

        visible_orders = _wait_for_order_visibility(
            adapter,
            ticker=ticker,
            expected_action="buy",
            expected_price=rest_price,
            timeout_secs=float(args.timeout_secs),
            poll_secs=float(args.poll_secs),
        )
        if not visible_orders:
            raise RuntimeError("resting_order_not_observed")
        report["after_place_open_order_count"] = len(visible_orders)

        cancel_result = adapter.cancel_all()
        if not cancel_result.success:
            raise RuntimeError(f"cancel_all_failed: {cancel_result.error or 'unknown_error'}")

        remaining_orders = _wait_for_zero_orders(
            adapter,
            timeout_secs=float(args.timeout_secs),
            poll_secs=float(args.poll_secs),
        )
        report["after_cancel_open_order_count"] = len(remaining_orders)
        if remaining_orders:
            raise RuntimeError("resting_orders_still_present_after_cancel_all")

        report["status"] = "passed"
    except Exception as exc:
        report["reason"] = str(exc)
        try:
            adapter.cancel_all()
        except Exception:
            pass
    finally:
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
