import pytest

from core_mm.rewards import RewardsScore, compute_reward_score, compute_tighten_ticks


class TestQuadraticScoring:
    def test_no_max_spread_returns_zero(self) -> None:
        score = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=None,
        )
        assert score.total_score == 0.0
        assert score.eligible is False

    def test_zero_mid_returns_zero(self) -> None:
        score = compute_reward_score(
            mid_price=0.0, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        assert score.total_score == 0.0

    def test_symmetric_quotes_equal_scores(self) -> None:
        # Bid 1c below mid, ask 1c above mid → symmetric
        score = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        assert score.buy_score == pytest.approx(score.sell_score)
        assert score.total_score == pytest.approx(score.buy_score)
        assert score.eligible is True

    def test_tighter_scores_higher(self) -> None:
        # 1c from mid vs 2c from mid, max spread = 6c
        tight = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        wide = compute_reward_score(
            mid_price=0.50, bid_price=0.48, ask_price=0.52,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        # Tight should score ~4x higher (quadratic)
        assert tight.total_score > wide.total_score * 3.5

    def test_quadratic_formula_exact(self) -> None:
        # v = 0.03 (half of 0.06), s = 0.01 (1c from mid)
        # Score = ((0.03 - 0.01) / 0.03)^2 * 100 = (2/3)^2 * 100 = 44.44
        score = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        expected = (2.0 / 3.0) ** 2 * 100.0
        assert score.buy_score == pytest.approx(expected, rel=1e-6)

    def test_outside_max_spread_scores_zero(self) -> None:
        # Max spread = 0.02, but our quotes are 2c from mid (= max per-side)
        score = compute_reward_score(
            mid_price=0.50, bid_price=0.48, ask_price=0.52,
            bid_size=100, ask_size=100, max_incentive_spread=0.02,  # per-side = 0.01
        )
        # 2c distance > 1c max per-side → zero
        assert score.buy_score == 0.0
        assert score.sell_score == 0.0

    def test_total_is_min_of_sides(self) -> None:
        # Asymmetric: bid tight (1c), ask wide (3c)
        score = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.53,
            bid_size=100, ask_size=100, max_incentive_spread=0.10,
        )
        assert score.total_score == pytest.approx(min(score.buy_score, score.sell_score))
        assert score.buy_score > score.sell_score

    def test_larger_size_proportional(self) -> None:
        small = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=50, ask_size=50, max_incentive_spread=0.06,
        )
        large = compute_reward_score(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        assert large.total_score == pytest.approx(small.total_score * 2.0)

    def test_missing_bid_zeroes_buy(self) -> None:
        score = compute_reward_score(
            mid_price=0.50, bid_price=None, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
        )
        assert score.buy_score == 0.0
        assert score.total_score == 0.0  # min(0, sell) = 0

    def test_distance_tracking(self) -> None:
        score = compute_reward_score(
            mid_price=0.50, bid_price=0.48, ask_price=0.53,
            bid_size=100, ask_size=100, max_incentive_spread=0.10,
        )
        assert score.buy_distance == pytest.approx(0.02)
        assert score.sell_distance == pytest.approx(0.03)


class TestTightenTicks:
    def test_no_max_spread_returns_zero(self) -> None:
        ticks = compute_tighten_ticks(
            mid_price=0.50, bid_price=0.48, ask_price=0.52,
            bid_size=100, ask_size=100, max_incentive_spread=None,
            tick_size=0.01, max_tighten=2,
        )
        assert ticks == 0

    def test_already_tight_no_tighten(self) -> None:
        # Bid at 0.49, ask at 0.51. Can't tighten to 0.50/0.50.
        ticks = compute_tighten_ticks(
            mid_price=0.50, bid_price=0.49, ask_price=0.51,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
            tick_size=0.01, max_tighten=2,
        )
        assert ticks == 0  # Can't cross mid

    def test_tightens_when_wide(self) -> None:
        # Bid at 0.46, ask at 0.54 (4c from mid). Lots of room to tighten.
        ticks = compute_tighten_ticks(
            mid_price=0.50, bid_price=0.46, ask_price=0.54,
            bid_size=100, ask_size=100, max_incentive_spread=0.10,
            tick_size=0.01, max_tighten=3,
        )
        assert ticks > 0

    def test_respects_max_tighten(self) -> None:
        ticks = compute_tighten_ticks(
            mid_price=0.50, bid_price=0.44, ask_price=0.56,
            bid_size=100, ask_size=100, max_incentive_spread=0.14,
            tick_size=0.01, max_tighten=2,
        )
        assert ticks <= 2

    def test_zero_tick_size_returns_zero(self) -> None:
        ticks = compute_tighten_ticks(
            mid_price=0.50, bid_price=0.48, ask_price=0.52,
            bid_size=100, ask_size=100, max_incentive_spread=0.06,
            tick_size=0.0, max_tighten=2,
        )
        assert ticks == 0
