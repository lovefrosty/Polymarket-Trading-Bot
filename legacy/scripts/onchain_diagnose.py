from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.onchain_ingest import (
    CTF_ADDRESS,
    CTF_EXCHANGE_ADDRESS,
    NEGRISK_CTF_EXCHANGE_ADDRESS,
)


def main() -> None:
    args = _parse_args()
    web3 = _init_web3(args.rpc)
    if web3 is None:
        raise SystemExit("web3_init_failed")
    try:
        latest = int(web3.eth.block_number)
    except Exception as exc:
        raise SystemExit(f"block_number_failed:{exc}") from exc

    if args.from_block is None:
        start = max(0, latest - int(args.lookback) + 1)
    else:
        start = int(args.from_block)
    end = int(args.to_block) if args.to_block is not None else latest

    contract = _build_contract(web3, args.contract)
    event = getattr(contract.events, args.event, None)
    if event is None:
        raise SystemExit(f"event_missing:{args.contract}:{args.event}")

    print(
        json.dumps(
            {
                "rpc": args.rpc,
                "contract": args.contract,
                "address": str(contract.address),
                "event": args.event,
                "from_block": start,
                "to_block": end,
                "latest_block": latest,
            },
            indent=2,
        )
    )

    try:
        logs = event.get_logs(from_block=start, to_block=end)
    except Exception as exc:
        raise SystemExit(f"get_logs_failed:{exc}") from exc

    print(f"logs={len(logs)}")
    if args.show:
        for entry in logs[: args.show]:
            print(json.dumps(_as_dict(entry), indent=2, default=str))


def _init_web3(url: str):
    if not url:
        return None
    try:
        from web3 import Web3
    except Exception:
        return None
    return Web3(Web3.HTTPProvider(url))


def _build_contract(web3, contract_name: str):
    name = contract_name.lower()
    if name == "ctf_exchange":
        abi = _load_abi(Path("abis/ctf_exchange.json"))
        return web3.eth.contract(address=CTF_EXCHANGE_ADDRESS, abi=abi)
    if name == "ctf":
        abi = _load_abi(Path("abis/ctf.json"))
        return web3.eth.contract(address=CTF_ADDRESS, abi=abi)
    if name == "negrisk_exchange":
        abi = _load_abi(Path("abis/negrisk_ctf_exchange.json"))
        return web3.eth.contract(address=NEGRISK_CTF_EXCHANGE_ADDRESS, abi=abi)
    raise SystemExit(f"unknown_contract:{contract_name}")


def _load_abi(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"abi_missing:{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"abi_invalid:{path}:{exc}") from exc


def _as_dict(entry: Any) -> Dict[str, Any]:
    if hasattr(entry, "__dict__"):
        return dict(entry.__dict__)
    if isinstance(entry, dict):
        return dict(entry)
    return {"value": str(entry)}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose Polygon on-chain getLogs")
    parser.add_argument("--rpc", required=True, help="HTTP RPC URL")
    parser.add_argument("--contract", default="ctf_exchange", help="ctf_exchange|ctf|negrisk_exchange")
    parser.add_argument("--event", default="OrderFilled", help="Event name (OrderFilled, OrdersMatched, etc)")
    parser.add_argument("--lookback", type=int, default=50, help="Blocks to look back (if from/to not set)")
    parser.add_argument("--from-block", type=int, default=None)
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--show", type=int, default=0, help="Print first N logs")
    return parser.parse_args()


if __name__ == "__main__":
    main()
