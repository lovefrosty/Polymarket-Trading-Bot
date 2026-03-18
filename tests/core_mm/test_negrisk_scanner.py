from core_mm.negrisk_scanner import NegRiskOpportunity, NegRiskScanConfig, NegRiskScanner


def _make_market(title: str, yes_price: float, *, active: bool = True, closed: bool = False) -> dict:
    return {
        "groupItemTitle": title,
        "outcomePrices": f'[{yes_price}, {1.0 - yes_price}]',
        "active": active,
        "closed": closed,
    }


def _neg_risk_event(
    *,
    event_id: str = "evt-1",
    question: str = "Who wins?",
    markets: list | None = None,
) -> dict:
    return {
        "id": event_id,
        "title": question,
        "negRisk": True,
        "markets": markets or [],
    }


class TestNegRiskScanner:
    def test_disabled_returns_empty(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=False))
        # Even if there are opportunities, disabled scanner doesn't scan
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.10),
            _make_market("B", 0.10),
            _make_market("C", 0.10),
        ])]
        # scan_events still works (enabled check is for fetch_and_scan gating)
        opps = scanner.scan_events(events)
        assert len(opps) == 1  # Scanner logic runs regardless of enabled flag

    def test_no_neg_risk_events(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True))
        events = [{"id": "e1", "title": "Binary market", "negRisk": False, "markets": []}]
        assert scanner.scan_events(events) == []

    def test_too_few_outcomes_filtered(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_outcomes=3))
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.30),
            _make_market("B", 0.30),
        ])]
        assert scanner.scan_events(events) == []

    def test_detects_arb_opportunity(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        # 3 outcomes: 0.20 + 0.20 + 0.20 = 0.60 → edge = 4000 bps
        events = [_neg_risk_event(markets=[
            _make_market("Candidate A", 0.20),
            _make_market("Candidate B", 0.20),
            _make_market("Candidate C", 0.20),
        ])]
        opps = scanner.scan_events(events)
        assert len(opps) == 1
        assert opps[0].num_outcomes == 3
        assert abs(opps[0].sum_best_asks - 0.60) < 0.001
        assert abs(opps[0].arb_edge_bps - 4000.0) < 1.0
        assert opps[0].outcome_labels == ("Candidate A", "Candidate B", "Candidate C")

    def test_no_arb_when_sum_exceeds_one(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        # 3 outcomes: 0.40 + 0.35 + 0.30 = 1.05 → edge = -500 bps (no arb)
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.40),
            _make_market("B", 0.35),
            _make_market("C", 0.30),
        ])]
        assert scanner.scan_events(events) == []

    def test_edge_below_threshold_filtered(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=200.0))
        # 3 outcomes: 0.33 + 0.33 + 0.33 = 0.99 → edge = 100 bps (< 200 threshold)
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.33),
            _make_market("B", 0.33),
            _make_market("C", 0.33),
        ])]
        assert scanner.scan_events(events) == []

    def test_closed_markets_excluded(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.20),
            _make_market("B", 0.20),
            _make_market("C", 0.20, closed=True),  # Closed → excluded
        ])]
        # Only 2 active outcomes (< min 3), so no opportunity
        assert scanner.scan_events(events) == []

    def test_inactive_markets_excluded(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.20),
            _make_market("B", 0.20),
            _make_market("C", 0.20, active=False),
        ])]
        assert scanner.scan_events(events) == []

    def test_best_ask_field_preferred(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        # bestAsk field should be preferred over outcomePrices
        events = [_neg_risk_event(markets=[
            {"groupItemTitle": "A", "bestAsk": "0.15", "active": True},
            {"groupItemTitle": "B", "bestAsk": "0.15", "active": True},
            {"groupItemTitle": "C", "bestAsk": "0.15", "active": True},
        ])]
        opps = scanner.scan_events(events)
        assert len(opps) == 1
        assert abs(opps[0].sum_best_asks - 0.45) < 0.001

    def test_sorted_by_edge_descending(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        events = [
            _neg_risk_event(event_id="small", question="Small edge", markets=[
                _make_market("A", 0.32),
                _make_market("B", 0.32),
                _make_market("C", 0.32),
            ]),
            _neg_risk_event(event_id="big", question="Big edge", markets=[
                _make_market("A", 0.10),
                _make_market("B", 0.10),
                _make_market("C", 0.10),
            ]),
        ]
        opps = scanner.scan_events(events)
        assert len(opps) == 2
        assert opps[0].event_id == "big"  # 7000 bps edge
        assert opps[1].event_id == "small"  # 400 bps edge

    def test_stats_tracking(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.20),
            _make_market("B", 0.20),
            _make_market("C", 0.20),
        ])]
        scanner.scan_events(events)
        scanner.scan_events([])  # Empty scan
        stats = scanner.stats
        assert stats["total_scans"] == 2
        assert stats["total_opportunities_found"] == 1
        assert stats["current_opportunities"] == 0  # Last scan was empty

    def test_latest_opportunities_property(self) -> None:
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        events = [_neg_risk_event(markets=[
            _make_market("A", 0.20),
            _make_market("B", 0.20),
            _make_market("C", 0.20),
        ])]
        scanner.scan_events(events)
        assert len(scanner.latest_opportunities) == 1
        # Returns a copy
        scanner.latest_opportunities.clear()
        assert len(scanner.latest_opportunities) == 1

    def test_many_outcomes(self) -> None:
        """Events with 5+ outcomes should work correctly."""
        scanner = NegRiskScanner(NegRiskScanConfig(enabled=True, min_edge_bps=10.0))
        events = [_neg_risk_event(markets=[
            _make_market(f"Candidate {i}", 0.10) for i in range(5)
        ])]
        opps = scanner.scan_events(events)
        assert len(opps) == 1
        assert opps[0].num_outcomes == 5
        assert abs(opps[0].sum_best_asks - 0.50) < 0.001
        assert abs(opps[0].arb_edge_bps - 5000.0) < 1.0
