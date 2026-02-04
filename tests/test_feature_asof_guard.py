import unittest

from backtests.scientific_method.engine import DecisionRow, _enforce_feature_asof


class TestFeatureAsOfGuard(unittest.TestCase):
    def test_feature_from_future_raises(self) -> None:
        row = DecisionRow(
            t_decision_ms=1000,
            t_decision_mono=None,
            asset_id="asset",
            condition_id=None,
            outcome=None,
            market_slug=None,
            reference_symbol="BTC",
            features={},
            feature_timestamps={},
            feature_asof_ts_ms=1000,
            feature_asof_source="ref_bars_5m",
            feature_asof_detail="bar_close_ts",
            label=None,
            label_time_ms=None,
            p_market_buy=0.5,
            p_market_sell=0.5,
            depth_buy=None,
            depth_sell=None,
            slippage_buy=None,
            slippage_sell=None,
            book_spread_bps=None,
            best_bid_size=None,
            best_ask_size=None,
            depth_within_ticks_bid=None,
            depth_within_ticks_ask=None,
            depth_within_ticks_n=None,
            depth_at_notional_bid=None,
            depth_at_notional_ask=None,
            depth_at_notional_target=None,
            depth_units="shares",
            imbalance_l1=None,
            imbalance_depth=None,
            diff_bps=None,
            alpha_basis=None,
            pstar_disagreement_extreme=False,
            belief_lag_sec=None,
            belief_lag_corr=None,
            alpha_lag=1.0,
            alpha_vov=1.0,
            sigma_t=None,
            sigma_prev=None,
            regime_pi=None,
            regime_id=None,
        )
        inv = {"feature_from_future": 0}
        with self.assertRaises(ValueError) as context:
            _enforce_feature_asof(row, inv)
        self.assertEqual(str(context.exception), "feature_from_future")
        self.assertEqual(inv["feature_from_future"], 1)


if __name__ == "__main__":
    unittest.main()
