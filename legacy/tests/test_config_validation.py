import asyncio
import os
import unittest

from config.settings import AutoDiscoverSpec, MarketConfig, load_markets, load_settings, validate_markets_config
from core.market_discovery import resolve_markets
from pathlib import Path
import json
import tempfile


class TestConfigValidation(unittest.TestCase):
    def test_reference_poll_default_is_one_second(self) -> None:
        prior = os.environ.get("REFERENCE_POLL_SECS")
        try:
            if "REFERENCE_POLL_SECS" in os.environ:
                del os.environ["REFERENCE_POLL_SECS"]
            settings = load_settings()
            self.assertEqual(float(settings.reference_poll_secs), 1.0)
        finally:
            if prior is None:
                os.environ.pop("REFERENCE_POLL_SECS", None)
            else:
                os.environ["REFERENCE_POLL_SECS"] = prior

    def test_reference_poll_env_override_still_wins(self) -> None:
        prior = os.environ.get("REFERENCE_POLL_SECS")
        try:
            os.environ["REFERENCE_POLL_SECS"] = "2.5"
            settings = load_settings()
            self.assertEqual(float(settings.reference_poll_secs), 2.5)
        finally:
            if prior is None:
                os.environ.pop("REFERENCE_POLL_SECS", None)
            else:
                os.environ["REFERENCE_POLL_SECS"] = prior

    def test_reference_source_default_enables_spot_and_perp(self) -> None:
        prior = os.environ.get("REFERENCE_SOURCE")
        try:
            os.environ.pop("REFERENCE_SOURCE", None)
            settings = load_settings()
            self.assertEqual(settings.reference_source, "poll_coinbase,poll_binance_perp")
        finally:
            if prior is None:
                os.environ.pop("REFERENCE_SOURCE", None)
            else:
                os.environ["REFERENCE_SOURCE"] = prior

    def test_missing_ids_without_discovery_raises(self) -> None:
        markets = [
            MarketConfig(
                name="BTC 15m",
                condition_id=None,
                token_ids=[],
                slug_prefix=None,
                reference_symbol="BTC",
                min_tick=0.01,
                min_size=1.0,
                max_price=0.99,
                min_price=0.01,
            )
        ]
        with self.assertRaises(ValueError):
            validate_markets_config(markets, auto_discover=False)

    def test_auto_discover_allows_missing_ids(self) -> None:
        fixture_path = Path("tests/fixtures/gamma_markets_sample.json")
        data = json.loads(fixture_path.read_text())
        markets = [
            MarketConfig(
                name="BTC 15m",
                condition_id=None,
                token_ids=[],
                slug_prefix="btc-updown-15m-",
                reference_symbol="BTC",
                min_tick=0.01,
                min_size=1.0,
                max_price=0.99,
                min_price=0.01,
                discovery_backend=None,
                selection_regex=None,
                allow_unknown_symbol=False,
            )
        ]
        validate_markets_config(markets, auto_discover=True)
        resolved, _ = asyncio.run(
            resolve_markets(
                markets=markets,
                auto_discover=True,
                cache_path=Path("/tmp/ignore_cache.json"),
                markets_data=data,
                now_ts=1690000000,
            )
        )
        self.assertEqual(resolved[0].condition_id, "0xbtc2")
        self.assertEqual(resolved[0].token_ids, ["btc_new_down", "btc_new_up"])

    def test_load_markets_parses_auto_discover_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "markets.json"
            path.write_text(
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
                                "auto_discover": {
                                    "symbol": "BTC",
                                    "horizon": "15m",
                                    "mode": "latest_active",
                                },
                            }
                        ]
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            loaded = load_markets(path.as_posix())
        self.assertEqual(len(loaded), 1)
        self.assertIsNotNone(loaded[0].auto_discover)
        assert loaded[0].auto_discover is not None
        self.assertEqual(loaded[0].auto_discover.symbol, "BTC")
        self.assertEqual(loaded[0].auto_discover.horizon, "15m")
        self.assertEqual(loaded[0].auto_discover.mode, "latest_active")

    def test_invalid_auto_discover_tuple_raises(self) -> None:
        markets = [
            MarketConfig(
                name="BTC 15m",
                condition_id=None,
                token_ids=[],
                slug_prefix=None,
                reference_symbol="BTC",
                min_tick=0.01,
                min_size=1.0,
                max_price=0.99,
                min_price=0.01,
                auto_discover=AutoDiscoverSpec(symbol="ETH", horizon="1h", mode="latest_active"),
            )
        ]
        with self.assertRaises(ValueError) as exc:
            validate_markets_config(markets, auto_discover=True)
        self.assertIn("auto_discover unsupported tuple", str(exc.exception))

    def test_valid_auto_discover_tuple_passes(self) -> None:
        markets = [
            MarketConfig(
                name="BTC 15m",
                condition_id=None,
                token_ids=[],
                slug_prefix=None,
                reference_symbol="BTC",
                min_tick=0.01,
                min_size=1.0,
                max_price=0.99,
                min_price=0.01,
                auto_discover=AutoDiscoverSpec(symbol="BTC", horizon="15m", mode="latest_active"),
            )
        ]
        validate_markets_config(markets, auto_discover=True)


if __name__ == "__main__":
    unittest.main()
