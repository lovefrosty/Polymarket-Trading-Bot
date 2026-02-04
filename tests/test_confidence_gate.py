import unittest

from backtests.scientific_method.constraints import PortfolioConstraints
from backtests.scientific_method.engine import DecisionRow, PortfolioState, _simulate_trades
from backtests.scientific_method.sizing import SizingConfig


class TestConfidenceGate(unittest.TestCase):
    def test_low_confidence_blocks_trade(self) -> None:
        row = DecisionRow(
            t_decision_ms=1_000,
            t_decision_mono=None,
            asset_id="asset",
            condition_id=None,
            outcome=None,
            market_slug=None,
            reference_symbol="BTC",
            features={},
            feature_timestamps={},
            feature_asof_ts_ms=999,
            feature_asof_source="ref_ticks",
            feature_asof_detail="test",
            label=1,
            label_time_ms=2_000,
            p_market_buy=0.1,
            p_market_sell=0.1,
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
            diff_bps=20.0,
            alpha_basis=0.6,
            pstar_disagreement_extreme=False,
            belief_lag_sec=None,
            belief_lag_corr=None,
            alpha_lag=1.0,
            alpha_vov=1.0,
            sigma_t=None,
            sigma_prev=None,
            regime_pi=[0.4, 0.3, 0.3],
            regime_id=0,
        )
        portfolio = PortfolioState(equity=1000.0, peak_equity=1000.0, initial_equity=1000.0)
        constraints = PortfolioConstraints(
            max_gross_delta=10.0,
            max_net_delta=10.0,
            max_position_fraction=1.0,
            max_asset_fraction=1.0,
            max_drawdown_pct=1.0,
            drawdown_lookback_days=7,
            min_liquidity_ratio=0.0,
            max_open_positions=10,
        )
        sizing = SizingConfig(kelly_fraction_max=0.25)
        inv = {"low_confidence": 0, "pstar_disagreement_extreme": 0}

        pnl, trades, _, logs = _simulate_trades(
            [row],
            [0.6],
            portfolio,
            constraints,
            sizing,
            fee_rate=0.0,
            c_trade_min=0.9,
            inv=inv,
            mode="default",
        )

        self.assertEqual(pnl, 0.0)
        self.assertEqual(trades, 0)
        self.assertEqual(len(logs), 1)
        self.assertFalse(logs[0].trade_allowed)
        self.assertEqual(logs[0].size, 0.0)
        self.assertIn("low_confidence", logs[0].blockers)


if __name__ == "__main__":
    unittest.main()
