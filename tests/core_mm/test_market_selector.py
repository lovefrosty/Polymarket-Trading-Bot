from core_mm.market_selector import MarketSelectionConfig, MarketSelector


def _config(**overrides):
    return MarketSelectionConfig(require_clob_candidate=False, **overrides)


def test_market_selector_rejects_closed_or_non_tradable_candidate() -> None:
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
    selector = MarketSelector(config=_config(current_window_only=False))
    selected = selector.select_from_events(events)
    assert selected == []


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
    selector = MarketSelector(config=_config(current_window_only=False))
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

    selector = MarketSelector(config=_config(page_limit=1), fetcher=fetcher)
    selected = selector.select_from_events(selector.fetch_active_events())
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
    selector = MarketSelector(config=_config(current_window_only=False))
    selected = selector.select_from_markets(markets)
    assert len(selected) == 1
    assert selected[0].token_ids == ("t1", "t2")
    assert selected[0].mid_price == 0.49
    assert selected[0].reward_per_100 == 5.0


def test_market_selector_fetches_slug_window_with_slug_param() -> None:
    calls = []

    def fetcher(url: str, timeout: float):
        calls.append((url, timeout))
        return []

    selector = MarketSelector(config=_config(), fetcher=fetcher)
    selector.fetch_slug_window_markets()
    assert calls
    assert "slug=" in calls[0][0]
    assert "slug%5B%5D" not in calls[0][0]


def test_market_selector_prefers_active_15m_window() -> None:
    now_ts = 1_700_000_000
    markets = [
        {
            "slug": "btc-updown-15m-past",
            "conditionId": "past",
            "clobTokenIds": ["p1", "p2"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 0.5,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 9,
            "endDate": now_ts - 60,
        },
        {
            "slug": "btc-updown-15m-live",
            "conditionId": "live",
            "clobTokenIds": ["l1", "l2"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1.0,
            "spread": 0.02,
            "prices": [0.48, 0.52],
            "reward_per_100": 6,
            "endDate": now_ts + 300,
        },
    ]
    selector = MarketSelector(config=_config())
    selected = selector.select_from_markets(markets, now_ts=now_ts)
    assert [entry.condition_id for entry in selected] == ["live"]


def test_market_selector_ranks_across_multiple_symbols() -> None:
    markets = [
        {
            "slug": "btc-updown-15m-btc",
            "conditionId": "btc",
            "clobTokenIds": ["b1", "b2"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 4,
            "spread": 0.03,
            "prices": [0.47, 0.53],
            "reward_per_100": 8,
        },
        {
            "slug": "eth-updown-15m-eth",
            "conditionId": "eth",
            "clobTokenIds": ["e1", "e2"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1,
            "spread": 0.03,
            "prices": [0.46, 0.54],
            "reward_per_100": 8,
        },
    ]
    selector = MarketSelector(config=_config(symbol="BTC", symbols=("BTC", "ETH")))
    selected = selector.select_from_markets(markets)
    assert [entry.reference_symbol for entry in selected] == ["ETH", "BTC"]


def test_market_selector_does_not_require_global_clob_scan_for_direct_markets() -> None:
    selector = MarketSelector(config=MarketSelectionConfig(current_window_only=False))
    selector._known_clob_condition_ids = lambda: (_ for _ in ()).throw(AssertionError("unexpected_clob_scan"))
    selected = selector.select_from_markets(
        [
            {
                "slug": "btc-updown-15m-live",
                "conditionId": "live",
                "clobTokenIds": ["l1", "l2"],
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "volatility_sum": 1,
                "spread": 0.02,
                "prices": [0.48, 0.52],
                "reward_per_100": 6,
            }
        ],
        require_clob_candidate=False,
    )
    assert len(selected) == 1
    assert selected[0].condition_id == "live"


def test_market_selector_filters_extreme_binary_outcome_price() -> None:
    selector = MarketSelector(config=_config(current_window_only=False))
    selected = selector.select_from_markets(
        [
            {
                "slug": "btc-updown-15m-extreme",
                "conditionId": "extreme",
                "clobTokenIds": ["x1", "x2"],
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "volatility_sum": 1,
                "spread": 0.01,
                "outcomePrices": "[\"0.005\", \"0.995\"]",
                "reward_per_100": 6,
            }
        ]
    )
    assert selected == []
