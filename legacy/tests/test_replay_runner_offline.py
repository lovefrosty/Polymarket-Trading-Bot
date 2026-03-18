import tempfile
import unittest
from pathlib import Path

from config.settings import MarketConfig
from scripts.replay_runner import resolve_replay_markets


class TestReplayRunnerOffline(unittest.TestCase):
    def test_replay_offline_uses_artifact(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "resolved_markets_v1.json"
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            run_dir = log_dir / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            target = run_dir / "resolved_markets.json"
            target.write_text(fixture.read_text(), encoding="utf-8")

            markets = [
                MarketConfig(
                    name="BTC 15m Up/Down",
                    condition_id=None,
                    token_ids=[],
                    slug_prefix="btc-updown-15m-",
                    reference_symbol="BTC",
                    min_tick=0.01,
                    min_size=1.0,
                    max_price=0.99,
                    min_price=0.01,
                )
            ]
            resolved_markets, asset_meta = resolve_replay_markets(
                markets=markets,
                log_dir=str(log_dir),
                resolved_arg=None,
                auto_discover=False,
                no_network=True,
            )
            self.assertEqual(len(resolved_markets), 1)
            self.assertIn("token_down", asset_meta)

    def test_replay_no_network_requires_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            markets = [
                MarketConfig(
                    name="BTC 15m Up/Down",
                    condition_id=None,
                    token_ids=[],
                    slug_prefix="btc-updown-15m-",
                    reference_symbol="BTC",
                    min_tick=0.01,
                    min_size=1.0,
                    max_price=0.99,
                    min_price=0.01,
                )
            ]
            with self.assertRaises(ValueError) as ctx:
                resolve_replay_markets(
                    markets=markets,
                    log_dir=tmp,
                    resolved_arg=None,
                    auto_discover=False,
                    no_network=True,
                )
            self.assertIn("resolved_markets_artifact_missing_no_network", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
