# Position Sizing Contract

Last updated: 2026-03-25
Status: Working spec for implementation and test alignment

## Summary

This note defines how `get_buy_sell_amount` and its callers should interpret
the current sizing inputs. The goal is to make every final `buy_amount` and
`sell_amount` explainable in terms of one dominant limiter.

## Precedence Rules

Sizing precedence:

1. Hard safety limits: per-token `hard_position_cap`, explicit `max_size`,
   available inventory for sells, and higher-level exposure caps always win.
2. Buy-side risk budget: when risk-based share sizing is enabled, buy orders
   must be capped by the active per-trade loss budget before any softer sizing
   logic can expand them.
3. Kelly soft sizing: Kelly may reduce size below `trade_size`, but may not
   increase size above the configured baseline.
4. Baseline trade sizing: `trade_size` is the normal quoting ceiling when
   stricter controls are not active.
5. Inventory skew and affordability: skew may reduce or boost within the
   remaining safe envelope; buy affordability may reduce further.
6. Minimum order gating: if the final candidate size is below
   `min_order_size`, do not place the order.

## Field Meanings

- `position`: actual inventory in the token being quoted; this bounds sells.
- `max_size`: normal per-token operating limit before any global hard cap.
- `trade_size`: default maximum quote size in normal conditions.
- `avg_price`: inventory basis used to determine whether sell logic is active.
- `reverse_position`: related inventory on the opposite side used to estimate
  net exposure when `net_position` is not provided.
- `net_position`: preferred measure of directional exposure for skew and buy
  headroom decisions.
- `min_order_size`: final do-not-send threshold after all other sizing logic.
- `usdc_balance`: affordability gate for buys only.
- `buy_price`: expected entry price used for affordability and buy risk-budget
  share conversion.
- `sell_price`: expected exit price used for Kelly sell-side throttling.
- `hard_position_cap`: absolute per-token ceiling that overrides `max_size`.
- `inventory_skew_factor`: continuous aggressiveness adjustment based on net
  inventory direction and magnitude.
- `p_fair`: model fair value used only for Kelly soft sizing.
- `kelly_fraction`: conviction scaler applied to edge-derived notional sizing.
- `bankroll`: capital base supplied to Kelly calculations.
- `risk_per_trade_budget`: maximum allowed buy-side risk budget for a single
  entry cycle.
- `risk_based_share_sizing`: switch that activates the buy-side risk budget.

## Behavioral Decisions

- Buy-side sizing is treated as new risk. It must remain subordinate to hard
  caps, risk budget, affordability, and final minimum-size gating.
- Sell-side sizing is treated as risk reduction. It may be throttled by
  inventory skew or baseline sizing, but it must not be blocked by the buy-side
  risk budget.
- Negative net exposure may boost buy aggressiveness to reduce short-side risk,
  but only inside the remaining safe envelope. The boost is meant to neutralize
  risk faster, not to justify speculative oversizing.
- `hard_position_cap` is a per-token gross ceiling. Cluster-level net and gross
  limits should be enforced elsewhere, not folded implicitly into this helper.
- Kelly is a paper-first, subordinate input. It should never be the sole reason
  an order exceeds the baseline `trade_size`.

## Acceptance Matrix

| Scenario | Expected buy behavior | Expected sell behavior | Primary limiter |
| --- | --- | --- | --- |
| Flat inventory, no tighter gates | Buy up to `trade_size` | No sell | `trade_size` |
| Long inventory below cap | Buy reduced by long-side skew/headroom | Sell allowed up to inventory and sell sizing | skew or inventory |
| Net short exposure | Buy may be boosted within safe envelope | Sell damped to avoid worsening short exposure | skew within caps |
| Low USDC balance | Buy reduced to affordable shares or zero | Existing sells still allowed | affordability |
| Below minimum order size | Buy skipped | Sell skipped | `min_order_size` |
| Kelly edge present but risk budget tighter | Buy capped by risk budget | Sell remains governed by inventory and Kelly sell sizing | risk budget |
| Hard cap already reached | No buy | Sell may still reduce inventory | `hard_position_cap` |
| Large inventory with small `trade_size` | Buy follows remaining headroom rules | Sell uses actual inventory cap over net exposure | inventory and `trade_size` |

## Evidence And Rollout Expectations

- Unit tests in [tests/core_mm/test_sizing.py](/Users/padraigjudge/Desktop/Polymarket%20Bot/tests/core_mm/test_sizing.py) should cover each acceptance-matrix row.
- Telemetry should make it possible to tell whether an order was limited by hard
  cap, risk budget, Kelly reduction, affordability, skew, or minimum-order
  gating.
- Kelly remains paper-first until calibration runs show lower drawdown and no
  destabilizing increase in churn.

## Not Yet Decided

- Whether buy-side risk should continue to convert budget to shares using
  `risk_per_trade_budget / buy_price`, or migrate to a stop-distance/downside
  model.
- Whether negative-net buy boosts should remain perfectly symmetric with
  long-side buy reductions, or be damped to reduce churn.
- What exact paper evidence threshold is sufficient to promote Kelly-based
  sizing from paper-first to live-default behavior.
