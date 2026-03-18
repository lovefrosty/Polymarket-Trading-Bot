import unittest

from core.entry_exit_rules import EntryExitParams, entry_gate
from core.fees import fee_bps, taker_fee_bps_piecewise


class TestFeeModel(unittest.TestCase):
    def test_piecewise_symmetry(self) -> None:
        self.assertAlmostEqual(taker_fee_bps_piecewise(0.40), taker_fee_bps_piecewise(0.60), places=9)

    def test_piecewise_peak(self) -> None:
        mid = taker_fee_bps_piecewise(0.50)
        self.assertGreater(mid, taker_fee_bps_piecewise(0.49))
        self.assertGreater(mid, taker_fee_bps_piecewise(0.51))

    def test_piecewise_boundaries(self) -> None:
        self.assertEqual(taker_fee_bps_piecewise(0.01), 0.0)
        self.assertEqual(taker_fee_bps_piecewise(0.99), 0.0)

    def test_piecewise_monotonic_away(self) -> None:
        self.assertGreater(taker_fee_bps_piecewise(0.50), taker_fee_bps_piecewise(0.70))
        self.assertGreater(taker_fee_bps_piecewise(0.70), taker_fee_bps_piecewise(0.90))

    def test_make_always_zero(self) -> None:
        self.assertEqual(fee_bps(0.50, "MAKE", "ok"), 0.0)
        self.assertEqual(fee_bps(0.25, "MAKE", "unknown"), 0.0)

    def test_status_handling(self) -> None:
        base = fee_bps(0.50, "TAKE", "ok")
        self.assertEqual(fee_bps(0.50, "TAKE", "not_fee_addressable"), base)
        self.assertAlmostEqual(fee_bps(0.50, "TAKE", "unknown"), base * 1.2, places=9)

    def test_net_edge_bps_integration(self) -> None:
        edge_bps = 180.0
        fee = 156.0
        slippage = 20.0
        tox = 15.0
        net_edge_bps = edge_bps - fee - slippage - tox
        net_edge = net_edge_bps / 10000.0
        params = EntryExitParams(
            edge_min=0.015,
            edge_exit=0.00375,
            edge_stop=0.0075,
            z_mom_min=1.0,
            t_min_secs=90.0,
            hold_max_secs=480.0,
            vol_pct_hi=95.0,
            edge_min_mult_hivol=1.5,
        )
        snapshot = {
            "outcome": "Up",
            "token_id": "token",
            "p_fair": 0.52,
            "p_market_exec_buy": 0.52,
            "p_market_exec_sell": 0.48,
            "edge_net_override": {"buy": net_edge, "sell": None},
            "gates": {"allow": True, "reasons": []},
            "notes": {"signals": {"z_mom": 2.0, "time_remaining_sec": 200.0, "vol_regime": 0.2}},
        }
        result = entry_gate(snapshot, params)
        self.assertFalse(result.get("allow"))
        self.assertIn("EDGE_TOO_SMALL", result.get("reasons", []))


if __name__ == "__main__":
    unittest.main()
