import pytest

from core_mm.spot_momentum import MomentumSignal, SpotMomentum
from core_mm.alpha_overlay import AlphaOverlayManager


class TestSpotMomentum:
    def test_insufficient_samples_returns_zero(self) -> None:
        m = SpotMomentum(window=10)
        s1 = m.update(100.0)
        assert s1.extra_skew_ticks == 0
        s2 = m.update(101.0)
        assert s2.extra_skew_ticks == 0
        assert s2.samples == 2

    def test_flat_price_no_signal(self) -> None:
        m = SpotMomentum(window=5, activation_bps=20.0)
        for _ in range(5):
            signal = m.update(100.0)
        assert signal.extra_skew_ticks == 0
        assert signal.momentum_bps == pytest.approx(0.0)

    def test_upward_momentum_positive_skew(self) -> None:
        m = SpotMomentum(window=5, max_skew_ticks=2, activation_bps=10.0, full_scale_bps=100.0)
        # Price rises: 100 → 101 = 100 bps
        prices = [100.0, 100.2, 100.4, 100.6, 101.0]
        for p in prices:
            signal = m.update(p)
        assert signal.extra_skew_ticks > 0
        assert signal.momentum_bps > 0

    def test_downward_momentum_negative_skew(self) -> None:
        m = SpotMomentum(window=5, max_skew_ticks=2, activation_bps=10.0, full_scale_bps=100.0)
        prices = [100.0, 99.8, 99.6, 99.4, 99.0]
        for p in prices:
            signal = m.update(p)
        assert signal.extra_skew_ticks < 0
        assert signal.momentum_bps < 0

    def test_below_activation_no_ticks(self) -> None:
        m = SpotMomentum(window=5, activation_bps=50.0)
        # 10 bps move: below 50 bps activation
        prices = [100.0, 100.02, 100.04, 100.06, 100.10]
        for p in prices:
            signal = m.update(p)
        assert signal.extra_skew_ticks == 0
        assert signal.momentum_bps == pytest.approx(10.0, rel=0.01)

    def test_max_skew_ticks_capped(self) -> None:
        m = SpotMomentum(window=3, max_skew_ticks=2, activation_bps=10.0, full_scale_bps=50.0)
        # Massive move: 100 → 110 = 1000 bps >> full_scale
        signal = m.update(100.0)
        signal = m.update(105.0)
        signal = m.update(110.0)
        assert abs(signal.extra_skew_ticks) <= 2

    def test_zero_price_ignored(self) -> None:
        m = SpotMomentum(window=5)
        signal = m.update(0.0)
        assert signal.extra_skew_ticks == 0
        assert signal.samples == 0

    def test_current_momentum_bps_property(self) -> None:
        m = SpotMomentum(window=5)
        assert m.current_momentum_bps == 0.0
        m.update(100.0)
        m.update(101.0)
        m.update(102.0)
        assert m.current_momentum_bps == pytest.approx(200.0, rel=0.01)

    def test_reset_clears_state(self) -> None:
        m = SpotMomentum(window=5)
        m.update(100.0)
        m.update(101.0)
        m.update(102.0)
        m.reset()
        assert m.current_momentum_bps == 0.0


class TestAlphaOverlayMomentum:
    def test_update_spot_wires_through(self) -> None:
        overlay = AlphaOverlayManager(momentum_activation_bps=10.0, momentum_full_scale_bps=100.0)
        # Feed spot prices with upward momentum
        for p in [100.0, 100.5, 101.0, 101.5, 102.0]:
            overlay.update_spot(p)
        signal = overlay.get_signal()
        assert signal.spot_momentum_bps > 0

    def test_no_spot_updates_zero_momentum(self) -> None:
        overlay = AlphaOverlayManager()
        signal = overlay.get_signal()
        assert signal.spot_momentum_bps == 0.0

    def test_momentum_adds_to_extra_skew(self) -> None:
        overlay = AlphaOverlayManager(
            momentum_activation_bps=10.0,
            momentum_full_scale_bps=100.0,
            momentum_max_ticks=2,
        )
        # No momentum → baseline signal
        baseline = overlay.get_signal()
        # Add upward momentum
        for p in [100.0, 100.5, 101.0, 101.5, 102.0]:
            overlay.update_spot(p)
        with_momentum = overlay.get_signal()
        # Momentum should add positive skew ticks
        assert with_momentum.extra_skew_ticks > baseline.extra_skew_ticks
