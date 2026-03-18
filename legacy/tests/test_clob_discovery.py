import asyncio
import json
import unittest
from pathlib import Path

import core.clob_discovery as cd


class TestClobDiscovery(unittest.TestCase):
    def test_list_clob_candidates_filters(self) -> None:
        fixture = json.loads(Path("tests/fixtures/clob_sampling_markets.json").read_text())
        original_fetch = cd._fetch_json
        try:
            cd._fetch_json = lambda _url, timeout=5: fixture
            candidates = cd.list_clob_candidates()
        finally:
            cd._fetch_json = original_fetch
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].condition_id, "0x1")
        self.assertEqual(candidates[0].token_ids, ["t1", "t2"])

    def test_fee_rate_cache(self) -> None:
        calls = {"count": 0}

        def fetcher(_url, timeout=5):
            calls["count"] += 1
            return {"fee_rate_bps": 1000}

        client = cd.FeeRateClient(fetcher=fetcher, ttl_secs=300, time_fn=lambda: 0.0)
        first = client.get_fee_rate_bps("t1")
        second = client.get_fee_rate_bps("t1")
        self.assertEqual(first, 1000.0)
        self.assertEqual(second, 1000.0)
        self.assertEqual(calls["count"], 1)

    def test_fee_enabled_classification(self) -> None:
        candidate = cd.ClobCandidate(
            condition_id="0x1",
            token_ids=["t1", "t2"],
            outcomes=["Up", "Down"],
            prices=[0.5, 0.5],
            accepting_orders=True,
            active=True,
            closed=False,
            archived=False,
        )

        def fetcher(url, timeout=5):
            if "t1" in url:
                return {"fee_rate_bps": 0}
            return {"fee_rate_bps": 1000}

        client = cd.FeeRateClient(fetcher=fetcher, ttl_secs=0, time_fn=lambda: 0.0)
        gamma_markets = [
            {
                "conditionId": "0x1",
                "clobTokenIds": ["t1", "t2"],
                "outcomes": ["Up", "Down"],
                "slug": "btc-updown-15m-1690000000",
                "question": "BTC Up or Down 15m",
            }
        ]
        results = asyncio.run(
            cd.discover_fee_enabled_markets(
                reference_symbol="BTC",
                selection_regex="0x1",
                allow_unknown_symbol=True,
                fee_client=client,
                candidates=[candidate],
                gamma_markets=gamma_markets,
            )
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["conditionId"], "0x1")


if __name__ == "__main__":
    unittest.main()
