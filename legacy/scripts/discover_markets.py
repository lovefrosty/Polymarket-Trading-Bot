from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import List

from config.settings import load_markets, validate_markets_config
from core.market_discovery import (
    GAMMA_BASE_URL,
    NoActiveMarketError,
    load_gamma_markets,
    resolve_markets,
    select_latest_by_prefix,
)


def main() -> None:
    args = _parse_args()
    if args.markets:
        markets_cfg = load_markets(args.markets)
        validate_markets_config(markets_cfg, auto_discover=True)
        summary: dict = {}
        try:
            resolved, _ = asyncio.run(
                resolve_markets(
                    markets=markets_cfg,
                    auto_discover=True,
                    cache_path=Path(args.cache_path),
                    gamma_base_url=args.gamma_url,
                    discovery_summary=summary,
                )
            )
        except NoActiveMarketError as exc:
            payload = {
                "error": str(exc),
                "diagnostics": exc.diagnostics,
                "request_payload": exc.request_payload,
                "discovery_summary": summary,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            raise
        except ValueError as exc:
            payload = {
                "error": str(exc),
                "discovery_summary": summary,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            raise
        output = json.dumps(
            [
                {
                    "name": market.name,
                    "slug": market.slug,
                    "conditionId": market.condition_id,
                    "clobTokenIds": market.token_ids,
                    "outcomes": market.outcomes,
                    "question": market.question,
                }
                for market in resolved
            ],
            indent=2,
            sort_keys=True,
        )
        if args.output:
            Path(args.output).write_text(output)
        else:
            print(output)
        return
    prefixes = list(args.slug_prefix or [])
    if args.symbols:
        prefixes.extend(_prefixes_from_symbols(args.symbols))
    if not prefixes:
        raise ValueError("no_slug_prefixes_or_symbols")

    cache_path = Path(args.cache_path)
    markets = load_gamma_markets(
        base_url=args.gamma_url,
        cache_path=cache_path,
        active=True,
        limit=args.limit,
        offset=0,
        cache_ttl_secs=args.cache_ttl,
    )

    resolved = []
    for prefix in prefixes:
        market = select_latest_by_prefix(markets, prefix)
        if market is None:
            resolved.append({"slug_prefix": prefix, "error": "not_found"})
            continue
        resolved.append(
            {
                "slug_prefix": prefix,
                "slug": market.get("slug"),
                "conditionId": market.get("conditionId") or market.get("condition_id"),
                "clobTokenIds": market.get("clobTokenIds"),
                "outcomes": market.get("outcomes"),
                "question": market.get("question"),
            }
        )

    output = json.dumps(resolved, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(output)
    else:
        print(output)


def _prefixes_from_symbols(symbols: List[str]) -> List[str]:
    return [f"{symbol.lower()}-updown-15m-" for symbol in symbols]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover latest Polymarket 15m markets")
    parser.add_argument("--markets", default=None, help="Markets config file (auto-discover)")
    parser.add_argument("--symbols", nargs="*", default=None, help="Symbols like BTC ETH")
    parser.add_argument("--slug-prefix", nargs="*", default=None, help="Slug prefixes")
    parser.add_argument("--gamma-url", default=GAMMA_BASE_URL, help="Gamma base URL")
    parser.add_argument("--cache-path", default="./logs/cache_gamma_markets.json")
    parser.add_argument("--cache-ttl", type=int, default=60)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output", default=None, help="Write resolved JSON to file")
    return parser.parse_args()


if __name__ == "__main__":
    main()
