import itertools
import unittest

from core.market_discovery import (
    deterministic_market_selection_key,
    deterministic_market_selection_key_str,
    select_latest_by_prefix,
)


def _market(
    slug: str,
    condition_id: str,
    token_ids: list[str],
    *,
    active: bool = True,
    closed: bool = False,
    accepting_orders: bool = True,
    end_ts: int = 2_000_000_000,
) -> dict:
    return {
        "slug": slug,
        "conditionId": condition_id,
        "clobTokenIds": token_ids,
        "active": active,
        "closed": closed,
        "accepting_orders": accepting_orders,
        "endDate": end_ts,
    }


class TestRolloverSelectionDeterminism(unittest.TestCase):
    def test_selection_key_string_is_stable(self) -> None:
        market = _market("btc-updown-15m-2000000000", "c1", ["t2", "t1"])
        key_a = deterministic_market_selection_key_str(market)
        key_b = deterministic_market_selection_key_str(dict(market))
        self.assertEqual(key_a, key_b)

    def test_latest_selection_is_deterministic_under_permutations(self) -> None:
        markets = [
            _market("btc-updown-15m-2000000000", "c1", ["a1", "a2"], accepting_orders=False),
            _market("btc-updown-15m-2000000000", "c2", ["b1", "b2"], accepting_orders=True),
            _market("btc-updown-15m-2000000000", "c3", ["c1", "c2"], accepting_orders=True),
        ]
        expected = max(markets, key=deterministic_market_selection_key)
        expected_condition = expected["conditionId"]
        for perm in itertools.permutations(markets):
            selected = select_latest_by_prefix(perm, "btc-updown-15m-", now_ts=1_900_000_000)
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected["conditionId"], expected_condition)


if __name__ == "__main__":
    unittest.main()
