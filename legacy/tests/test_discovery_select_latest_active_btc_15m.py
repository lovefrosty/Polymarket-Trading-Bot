import unittest

from core.market_discovery import NoActiveMarketError, ResolvedMarket, select_latest_active_btc_15m


def _resolved(
    *,
    slug: str,
    condition_id: str,
    token_ids: list[str],
    end_ts_ms: int,
    end_ts_source: str = "metadata",
) -> ResolvedMarket:
    outcomes = ["Down", "Up"][: len(token_ids)]
    outcome_by_token = dict(zip(token_ids, outcomes))
    token_by_outcome = {outcome: token for token, outcome in outcome_by_token.items()}
    return ResolvedMarket(
        name="BTC 15m",
        reference_symbol="BTC",
        slug_prefix=None,
        slug=slug,
        condition_id=condition_id,
        token_ids=token_ids,
        outcomes=outcomes,
        outcome_by_token=outcome_by_token,
        token_by_outcome=token_by_outcome,
        question=None,
        min_tick=0.01,
        min_size=1.0,
        min_price=0.01,
        max_price=0.99,
        end_ts_ms=end_ts_ms,
        end_ts_source=end_ts_source,
    )


class TestSelectLatestActiveBtc15m(unittest.TestCase):
    def test_selects_sole_active_candidate(self) -> None:
        now_ms = 2_000_000_000
        active = _resolved(
            slug="btc-updown-15m-2000000",
            condition_id="c1",
            token_ids=["t1", "t2"],
            end_ts_ms=now_ms + 5_000,
        )
        not_btc = _resolved(
            slug="eth-updown-15m-2000000",
            condition_id="e1",
            token_ids=["e1", "e2"],
            end_ts_ms=now_ms + 5_000,
        )
        selected = select_latest_active_btc_15m([not_btc, active], now_ms=now_ms)
        self.assertEqual(selected.condition_id, "c1")

    def test_selects_highest_end_ts_among_active(self) -> None:
        now_ms = 1_500_000_000
        near = _resolved(
            slug="btc-updown-15m-1500000",
            condition_id="c1",
            token_ids=["a1", "a2"],
            end_ts_ms=now_ms + 200_000,
        )
        later = _resolved(
            slug="btc-updown-15m-1500900",
            condition_id="c2",
            token_ids=["b1", "b2"],
            end_ts_ms=now_ms + 500_000,
        )
        selected = select_latest_active_btc_15m([later, near], now_ms=now_ms)
        self.assertEqual(selected.condition_id, "c2")

    def test_tie_breaks_deterministically(self) -> None:
        now_ms = 1_900_000_000
        end_ts_ms = now_ms + 300_000
        candidates = [
            _resolved(
                slug="btc-updown-15m-a",
                condition_id="c9",
                token_ids=["x1", "x2"],
                end_ts_ms=end_ts_ms,
            ),
            _resolved(
                slug="btc-updown-15m-b",
                condition_id="c1",
                token_ids=["x1", "x2"],
                end_ts_ms=end_ts_ms,
            ),
            _resolved(
                slug="btc-updown-15m-b",
                condition_id="c2",
                token_ids=["a1", "a2"],
                end_ts_ms=end_ts_ms,
            ),
            _resolved(
                slug="btc-updown-15m-b",
                condition_id="c2",
                token_ids=["z1", "z2"],
                end_ts_ms=end_ts_ms,
            ),
        ]
        selected = select_latest_active_btc_15m(candidates, now_ms=now_ms)
        self.assertEqual(selected.slug, "btc-updown-15m-b")
        self.assertEqual(selected.condition_id, "c2")
        self.assertEqual(selected.token_ids, ["z1", "z2"])

    def test_prefers_metadata_over_slug_fallback(self) -> None:
        now_ms = 2_100_000_000
        end_ts_ms = now_ms + 100_000
        metadata = _resolved(
            slug="btc-updown-15m-2100000",
            condition_id="meta",
            token_ids=["m1", "m2"],
            end_ts_ms=end_ts_ms,
            end_ts_source="metadata",
        )
        fallback = _resolved(
            slug="btc-updown-15m-2100000",
            condition_id="fallback",
            token_ids=["f1", "f2"],
            end_ts_ms=end_ts_ms,
            end_ts_source="slug_fallback",
        )
        selected = select_latest_active_btc_15m([fallback, metadata], now_ms=now_ms)
        self.assertEqual(selected.condition_id, "meta")

    def test_raises_when_no_active_candidates(self) -> None:
        now_ms = 2_200_000_000
        ended = _resolved(
            slug="btc-updown-15m-2199000",
            condition_id="ended",
            token_ids=["e1", "e2"],
            end_ts_ms=now_ms - 1,
        )
        future = _resolved(
            slug="btc-updown-15m-2201000",
            condition_id="future",
            token_ids=["f1", "f2"],
            end_ts_ms=now_ms + 1_500_000,
        )
        with self.assertRaises(NoActiveMarketError):
            select_latest_active_btc_15m([ended, future], now_ms=now_ms)


if __name__ == "__main__":
    unittest.main()
