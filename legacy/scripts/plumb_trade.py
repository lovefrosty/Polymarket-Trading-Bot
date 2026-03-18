from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import load_settings
from core.broker_base import OrderIntent
from core.broker_polymarket import EXPECTED_CLOB_CLIENT_VERSION, PolymarketBroker, PolymarketBrokerConfig


def _now_ms() -> int:
    return int(time.time() * 1000)


def _emit(event: str, payload: Dict[str, Any]) -> None:
    row = {
        "event": str(event),
        "ts_ms": _now_ms(),
        "payload": payload,
    }
    print(json.dumps(row, separators=(",", ":"), ensure_ascii=True, sort_keys=True))


def build_plumb_intent(
    *,
    run_epoch_ms: int,
    asset_id: str,
    side: str,
    price: float,
    size: float,
    cycle_idx: int,
    step_idx: int,
    post_only: bool,
) -> OrderIntent:
    decision_seq = int(cycle_idx * 10 + step_idx)
    order_id = f"plumb:{run_epoch_ms}:{asset_id}:{side}:{decision_seq}"
    client_order_id = f"plumb-cid:{run_epoch_ms}:{asset_id}:{side}:{decision_seq}"
    quote_group_id = f"plumb-qg:{run_epoch_ms}:{asset_id}:{side}:slot0"
    idempotency_key = f"plumb-idem:{run_epoch_ms}:{asset_id}:{side}:{decision_seq}"
    now_ms = _now_ms()
    return OrderIntent(
        order_id=order_id,
        client_order_id=client_order_id,
        asset_id=asset_id,
        side=side,
        size=float(size),
        price=float(price),
        mode="MAKE" if post_only else "TAKE",
        t_decision_wall_ms=now_ms,
        as_of_ts_ms=now_ms,
        decision_id=f"plumb:{run_epoch_ms}:{decision_seq}",
        reason="PLUMB_TRADE_DRILL",
        post_only=bool(post_only),
        time_in_force="GTC",
        reduce_only=False,
        quote_group_id=quote_group_id,
        idempotency_key=idempotency_key,
    )


def _order_open(snapshot: Dict[str, Dict[str, Any]], order_id: str) -> bool:
    row = snapshot.get(order_id)
    if not row:
        return False
    status = str(row.get("status") or "").lower()
    if status in {"canceled", "cancelled", "filled", "rejected", "closed"}:
        return False
    return True


def _wait_for_open(broker: PolymarketBroker, order_id: str, timeout_ms: int) -> bool:
    deadline = _now_ms() + max(200, int(timeout_ms))
    while _now_ms() < deadline:
        snap = broker.snapshot()
        if _order_open(snap.open_orders, order_id):
            return True
        time.sleep(0.25)
    return False


def _cancel_and_confirm_closed(broker: PolymarketBroker, order_id: str, timeout_ms: int) -> bool:
    events = broker.cancel(order_id)
    _emit("cancel_sent", {"order_id": order_id, "event_types": [e.event_type for e in events]})
    deadline = _now_ms() + max(200, int(timeout_ms))
    while _now_ms() < deadline:
        snap = broker.snapshot()
        if not _order_open(snap.open_orders, order_id):
            return True
        time.sleep(0.25)
    return False


def _run_side_cycle(
    *,
    broker: PolymarketBroker,
    run_epoch_ms: int,
    asset_id: str,
    side: str,
    price: float,
    size: float,
    cycle_idx: int,
    verify_timeout_ms: int,
) -> None:
    intent = build_plumb_intent(
        run_epoch_ms=run_epoch_ms,
        asset_id=asset_id,
        side=side,
        price=price,
        size=size,
        cycle_idx=cycle_idx,
        step_idx=0,
        post_only=True,
    )
    events = broker.submit(intent)
    _emit(
        "submit_sent",
        {
            "order_id": intent.order_id,
            "side": side,
            "price": float(price),
            "size": float(size),
            "event_types": [e.event_type for e in events],
        },
    )
    opened = _wait_for_open(broker, intent.order_id, timeout_ms=verify_timeout_ms)
    _emit("open_check", {"order_id": intent.order_id, "opened": bool(opened)})
    closed = _cancel_and_confirm_closed(broker, intent.order_id, timeout_ms=verify_timeout_ms)
    _emit("cancel_check", {"order_id": intent.order_id, "closed": bool(closed)})


def _simulate_restart_adoption(broker: PolymarketBroker, order_id: str, timeout_ms: int) -> bool:
    deadline = _now_ms() + max(200, int(timeout_ms))
    while _now_ms() < deadline:
        snap = broker.snapshot()
        if _order_open(snap.open_orders, order_id):
            return True
        time.sleep(0.25)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic Polymarket trade actuator plumbing drill")
    parser.add_argument("--asset-id", required=True, help="Polymarket token_id to drill")
    parser.add_argument("--buy-price", type=float, default=0.45)
    parser.add_argument("--sell-price", type=float, default=0.55)
    parser.add_argument("--size", type=float, default=1.0)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--verify-timeout-ms", type=int, default=8000)
    parser.add_argument("--allow-intentional-fill", action="store_true")
    parser.add_argument("--intentional-fill-price", type=float, default=0.99)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    run_epoch_ms = _now_ms()
    run_id = f"plumb-{uuid.uuid4().hex[:12]}"

    broker = PolymarketBroker(
        api_key=settings.polymarket_api_key,
        secret=settings.polymarket_secret,
        passphrase=settings.polymarket_passphrase,
        private_key=settings.polymarket_private_key,
        config=PolymarketBrokerConfig(
            dry_run=bool(args.dry_run),
            expected_client_version=EXPECTED_CLOB_CLIENT_VERSION,
            strict_contract=True,
        ),
    )

    _emit(
        "plumb_start",
        {
            "run_id": run_id,
            "asset_id": str(args.asset_id),
            "cycles": int(args.cycles),
            "dry_run": bool(args.dry_run),
            "allow_intentional_fill": bool(args.allow_intentional_fill),
        },
    )

    for cycle in range(max(1, int(args.cycles))):
        _run_side_cycle(
            broker=broker,
            run_epoch_ms=run_epoch_ms,
            asset_id=str(args.asset_id),
            side="buy",
            price=float(args.buy_price),
            size=float(args.size),
            cycle_idx=int(cycle),
            verify_timeout_ms=int(args.verify_timeout_ms),
        )
        _run_side_cycle(
            broker=broker,
            run_epoch_ms=run_epoch_ms,
            asset_id=str(args.asset_id),
            side="sell",
            price=float(args.sell_price),
            size=float(args.size),
            cycle_idx=int(cycle),
            verify_timeout_ms=int(args.verify_timeout_ms),
        )

    # Restart-adoption check: submit one resting order, recreate broker object, verify order remains visible.
    restart_intent = build_plumb_intent(
        run_epoch_ms=run_epoch_ms,
        asset_id=str(args.asset_id),
        side="buy",
        price=float(args.buy_price),
        size=float(args.size),
        cycle_idx=999,
        step_idx=0,
        post_only=True,
    )
    broker.submit(restart_intent)
    broker_after_restart = PolymarketBroker(
        api_key=settings.polymarket_api_key,
        secret=settings.polymarket_secret,
        passphrase=settings.polymarket_passphrase,
        private_key=settings.polymarket_private_key,
        config=PolymarketBrokerConfig(
            dry_run=bool(args.dry_run),
            expected_client_version=EXPECTED_CLOB_CLIENT_VERSION,
            strict_contract=True,
        ),
    )
    adopted = _simulate_restart_adoption(
        broker=broker_after_restart,
        order_id=restart_intent.order_id,
        timeout_ms=int(args.verify_timeout_ms),
    )
    _emit("restart_adoption_check", {"order_id": restart_intent.order_id, "adopted": bool(adopted)})
    _cancel_and_confirm_closed(
        broker=broker_after_restart,
        order_id=restart_intent.order_id,
        timeout_ms=int(args.verify_timeout_ms),
    )

    if args.allow_intentional_fill:
        fill_intent = build_plumb_intent(
            run_epoch_ms=run_epoch_ms,
            asset_id=str(args.asset_id),
            side="buy",
            price=float(args.intentional_fill_price),
            size=float(args.size),
            cycle_idx=1000,
            step_idx=0,
            post_only=False,
        )
        fill_events = broker_after_restart.submit(fill_intent)
        _emit(
            "intentional_fill_submit",
            {
                "order_id": fill_intent.order_id,
                "event_types": [e.event_type for e in fill_events],
                "post_only": False,
            },
        )
        time.sleep(1.0)
        still_open = _order_open(broker_after_restart.snapshot().open_orders, fill_intent.order_id)
        _emit("intentional_fill_open_state", {"order_id": fill_intent.order_id, "still_open": bool(still_open)})
        if still_open:
            _cancel_and_confirm_closed(
                broker=broker_after_restart,
                order_id=fill_intent.order_id,
                timeout_ms=int(args.verify_timeout_ms),
            )

    _emit("plumb_done", {"run_id": run_id})


if __name__ == "__main__":
    main()
