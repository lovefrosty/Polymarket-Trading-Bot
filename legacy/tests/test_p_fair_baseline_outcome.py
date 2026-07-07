import unittest

from core.p_fair_baseline import BaselineCfg, p_fair_baseline


class TestPFairBaselineOutcome(unittest.TestCase):
    def test_outcome_complements(self) -> None:
        cfg = BaselineCfg(
            bias=0.0,
            w_mom=0.35,
            w_revert=0.15,
            z_clip=4.0,
            vol_dampen_enabled=True,
            vol_floor=0.6,
        )
        p_up = p_fair_baseline("Up", z_mom=1.0, z_revert=-0.5, vol=0.2, cfg=cfg)
        p_down = p_fair_baseline("Down", z_mom=1.0, z_revert=-0.5, vol=0.2, cfg=cfg)
        self.assertAlmostEqual(p_up + p_down, 1.0, places=12)

    def test_unknown_outcome_raises(self) -> None:
        cfg = BaselineCfg(
            bias=0.0,
            w_mom=0.35,
            w_revert=0.15,
            z_clip=4.0,
            vol_dampen_enabled=True,
            vol_floor=0.6,
        )
        with self.assertRaises(ValueError):
            p_fair_baseline("Sideways", z_mom=0.0, z_revert=0.0, vol=0.1, cfg=cfg)

    def test_clipping_deterministic(self) -> None:
        cfg = BaselineCfg(
            bias=0.0,
            w_mom=0.35,
            w_revert=0.15,
            z_clip=2.0,
            vol_dampen_enabled=False,
            vol_floor=0.6,
        )
        p_clip = p_fair_baseline("Up", z_mom=10.0, z_revert=-10.0, vol=0.1, cfg=cfg)
        p_cap = p_fair_baseline("Up", z_mom=2.0, z_revert=-2.0, vol=0.1, cfg=cfg)
        self.assertAlmostEqual(p_clip, p_cap, places=12)


if __name__ == "__main__":
    unittest.main()
