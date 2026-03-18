import asyncio
import unittest

from core.clob_discovery import ClobCandidate
from core.market_discovery import discover_15m_crypto_by_slug, _generate_15m_slugs


class TestSlugDiscovery(unittest.TestCase):
    def test_slug_generation_windows(self) -> None:
        now_ts = 1_700_000_123
        slugs = _generate_15m_slugs("BTC", now_ts, back_windows=2, forward_windows=2)
        self.assertEqual(len(slugs), 5)
        base = (now_ts // 900) * 900
        expected_first = f"btc-updown-15m-{base - 2 * 900}"
        expected_last = f"btc-updown-15m-{base + 2 * 900}"
        self.assertEqual(slugs[0], expected_first)
        self.assertEqual(slugs[-1], expected_last)

    def test_gamma_parse_and_tradability(self) -> None:
        now_ts = 1_700_000_000
        slug = f"btc-updown-15m-{(now_ts // 900) * 900}"
        gamma_markets = [
            {
                "conditionId": "0x1",
                "slug": slug,
                "clobTokenIds": ["t1", "t2"],
                "outcomes": ["Down", "Up"],
                "question": "BTC Up or Down 15m",
            }
        ]
        candidates = [
            ClobCandidate(
                condition_id="0x1",
                token_ids=["t1", "t2"],
                outcomes=["Down", "Up"],
                prices=[0.5, 0.5],
                accepting_orders=True,
                active=True,
                closed=False,
                archived=False,
            )
        ]
        summary: dict = {}
        results = asyncio.run(
            discover_15m_crypto_by_slug(
                symbols=["BTC"],
                now_ts=now_ts,
                gamma_base_url="https://gamma-api.polymarket.com",
                cache_path=None,
                cache_ttl_secs=0,
                summary=summary,
                gamma_markets=gamma_markets,
                clob_candidates=candidates,
            )
        )
        self.assertIn("BTC", results)
        self.assertEqual(results["BTC"][0]["conditionId"], "0x1")
        self.assertEqual(summary.get("tradable_markets"), 1)

    def test_tradability_rejects_missing_candidate(self) -> None:
        now_ts = 1_700_000_000
        slug = f"btc-updown-15m-{(now_ts // 900) * 900}"
        gamma_markets = [
            {"conditionId": "0x2", "slug": slug, "clobTokenIds": ["t3", "t4"], "outcomes": ["Down", "Up"]}
        ]
        summary: dict = {}
        results = asyncio.run(
            discover_15m_crypto_by_slug(
                symbols=["BTC"],
                now_ts=now_ts,
                gamma_base_url="https://gamma-api.polymarket.com",
                cache_path=None,
                cache_ttl_secs=0,
                summary=summary,
                gamma_markets=gamma_markets,
                clob_candidates=[],
            )
        )
        self.assertEqual(len(results.get("BTC") or []), 1)
        rejected = summary.get("rejected_reason_counts") or {}
        self.assertGreater(rejected.get("missing_clob_candidate", 0), 0)


if __name__ == "__main__":
    unittest.main()
