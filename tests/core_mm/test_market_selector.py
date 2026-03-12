from core_mm.market_selector import MarketSelectionConfig, MarketSelector


def test_market_selector_keeps_contradictory_active_closed_candidate() -> None:
    events = [
        {
            "slug": "btc-updown-15m-1",
            "conditionId": "c1",
            "clobTokenIds": ["t1", "t2"],
            "outcomes": ["Yes", "No"],
            "active": True,
            "closed": True,
            "accepting_orders": False,
            "volatility_sum": 5,
            "spread": 0.04,
            "prices": [0.42, 0.58],
            "reward_per_100": 12,
        }
    ]
    selector = MarketSelector(config=MarketSelectionConfig())
    selected = selector.select_from_events(events)
    assert len(selected) == 1
    assert selected[0].condition_id == "c1"


def test_market_selector_scores_low_vol_high_reward_first() -> None:
    events = [
        {
            "slug": "btc-updown-15m-a",
            "conditionId": "a",
            "clobTokenIds": ["a1", "a2"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 10,
            "spread": 0.03,
            "prices": [0.45, 0.55],
            "reward_per_100": 10,
        },
        {
            "slug": "btc-updown-15m-b",
            "conditionId": "b",
            "clobTokenIds": ["b1", "b2"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 2,
            "spread": 0.03,
            "prices": [0.4, 0.6],
            "reward_per_100": 12,
        },
    ]
    selector = MarketSelector(config=MarketSelectionConfig())
    selected = selector.select_from_events(events)
    assert [entry.condition_id for entry in selected] == ["b", "a"]


def test_market_selector_fetches_paginated_events() -> None:
    calls = []
    pages = [
        [
            {
                "slug": "btc-updown-15m-a",
                "conditionId": "a",
                "clobTokenIds": ["a1", "a2"],
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "volatility_sum": 1,
                "spread": 0.02,
                "prices": [0.49, 0.51],
                "reward_per_100": 5,
            }
        ],
        [],
    ]

    def fetcher(url: str, timeout: float):
        calls.append((url, timeout))
        return pages[len(calls) - 1]

    selector = MarketSelector(config=MarketSelectionConfig(page_limit=1), fetcher=fetcher)
    selected = selector.select_markets()
    assert len(selected) == 1
    assert "offset=0" in calls[0][0]
    assert "offset=1" in calls[1][0]


def test_market_selector_parses_stringified_market_fields() -> None:
    markets = [{
        "slug": "btc-updown-15m-1",
        "conditionId": "c1",
        "clobTokenIds": "[\"t1\", \"t2\"]",
        "outcomes": "[\"Yes\", \"No\"]",
        "outcomePrices": "[\"0.49\", \"0.51\"]",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "volatility_sum": 1,
        "spread": 0.02,
        "umaReward": "5",
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
    }]
    selector = MarketSelector(config=MarketSelectionConfig())
    selected = selector.select_from_markets(markets)
    assert len(selected) == 1
    assert selected[0].token_ids == ("t1", "t2")
    assert selected[0].mid_price == 0.5
    assert selected[0].reward_per_100 == 5.0
