# Kalshi 4-Run Paper Comparison Template

Use this after the `kalshi-paper-4run-sess-*` suite completes.

## Session

- Date: `2026-03-25`
- Suite root: `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/core_mm_runs/kalshi-paper-4run-sess-20260325-193722`
- Intended launch scope: `Kalshi`, `BTC`, `max_active_markets = 1`
- Launch market followed: primarily `KXBTC-26MAR2522-B71150` and `KXBTC-26MAR2522-B71250`
- Start time: approximately `2026-03-25 19:37 EDT`
- End time: approximately `2026-03-25 21:37 EDT`
- Duration target: `2 hours`

## Executive Read

- Best overall run: `proof045`
- Safest run: `conservative`
- Best realized PnL run: `proof045`
- Lowest tail-risk run: `conservative` by policy intent; `proof045` still outperformed on outcome
- Recommended next candidate for constrained go-live paper follow-up: `proof045`
- Recommendation: `promote for more single-market paper`

## Scorecard

| Run | Stage | Market | Realized Net PnL | Unrealized PnL | Total PnL | Fill Count | Order Actions | Force-Flat Reliance | Hold Tail | Notes |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| conservative | stale-finished | KXBTC-26MAR2522-B71250 | 10.7408 | 0.2319 | 10.9727 | 538 | 689 | no evidence of dangerous reliance | acceptable | Strong result with cleaner conservative framing |
| proof045 | stale-finished | KXBTC-26MAR2522-B71150 | 11.8465 | 0.1808 | 12.0273 | 609 | 785 | no evidence of dangerous reliance | acceptable | Best overall result |
| proof040 | stale-finished | KXBTC-26MAR2522-B71150 | 11.0889 | 0.2841 | 11.3730 | 627 | 771 | no evidence of dangerous reliance | acceptable | Very competitive, slightly below proof045 |
| holdtail | stale-finished | KXBTC-26MAR2522-B71250 | 6.5642 | 0.3379 | 6.9021 | 519 | 702 | no evidence of dangerous reliance | acceptable but weakest | Positive, but clearly behind the others |

## Live Health Snapshot At Finish

| Run | Feed Connected | Last Update Age | Pending Commands | Quoteable | Kill Switch | Freeze Reasons |
| --- | --- | --- | ---: | --- | --- | --- |
| conservative | true | stale by a few minutes at inspection | 0 | not captured here | not triggered | none recorded |
| proof045 | true | stale by a few minutes at inspection | 0 | not captured here | not triggered | none recorded |
| proof040 | true | stale by a few minutes at inspection | 0 | not captured here | not triggered | none recorded |
| holdtail | true | stale by a few minutes at inspection | 0 | not captured here | not triggered | none recorded |

## Behavioral Comparison

### conservative

- Entry quality: good enough to stay strongly profitable without the most aggressive threshold
- Inventory behavior: controlled and consistent with single-market conservative launch framing
- Unwind behavior: no obvious evidence of unwind instability from final artifacts
- Tail behavior: acceptable in this run window
- Failure mode: not obvious from end-state artifacts; main tradeoff is lower upside than proof045

### proof045

- Entry quality: strongest balance of selectivity and activity in this batch
- Inventory behavior: active but still controlled enough to remain aligned with constrained launch scope
- Unwind behavior: no obvious operational problem in final artifacts
- Tail behavior: acceptable
- Failure mode: slightly more operational activity than conservative, but with clearly better PnL

### proof040

- Entry quality: aggressive enough to generate the highest fill count
- Inventory behavior: active and profitable, but not clearly safer than proof045
- Unwind behavior: no obvious end-state issue
- Tail behavior: acceptable in this batch
- Failure mode: more churn without beating proof045 on final total PnL

### holdtail

- Entry quality: acceptable but weaker realized performance
- Inventory behavior: still orderly, but the holdtail bias did not pay for itself here
- Unwind behavior: no obvious operational failure
- Tail behavior: designed to favor hold quality, but not rewarded in this window
- Failure mode: underperformance relative to all other variants

## Decision Quality Checks

- Did the selected market stay aligned with the intended launch market?
  - Yes. All four stayed on the intended single-market BTC Kalshi launch path, concentrated on two adjacent target markets.
- Did any run show dangerous force-flat dependence?
  - No obvious evidence from the final artifacts.
- Did any run rely on hedge for the safety case?
  - No. These results still support the no-hedge-constrained launch framing.
- Were stale positions resolved primarily through `SKEW` / `UNWIND`?
  - The final artifacts do not show a contrary story; nothing suggests the safety case depended on hedge.
- Was after-fee behavior still acceptable?
  - Yes. All four runs remained positive after fees, with proof045 leading.

## Winner Justification

- Why the winning run won:
  - `proof045` delivered the highest realized and total PnL while staying within the intended constrained single-market operating envelope.
- Why the others lost:
  - `proof040` was close, but higher activity did not convert into better final outcome.
  - `conservative` was safer by framing, but left some profit on the table.
  - `holdtail` materially underperformed the rest of the field.
- What single setting appears most responsible:
  - The `0.45` hedge-threshold profile appears to have struck the best balance in this specific window, though this should still be treated as paper evidence, not a final live promotion proof.

## Next Actions

1. Keep:
   `proof045` as the lead candidate and `conservative` as the safety baseline.
2. Change:
   Keep launch scope fixed at one Kalshi BTC market; do not widen to multi-market from this result alone.
3. Re-test on:
   the exact same single-market Kalshi launch path across additional independent 2-hour and longer windows.
4. Promote / do not promote:
   Promote `proof045` for more constrained single-market paper follow-up, not yet for broader hedge-credit live claims.
