# Hedge Calibration Workflow

Last updated: 2026-03-25
Status: Active workflow

## Purpose

This workflow exists to keep hedge experiments disciplined.

The main rule is:

- do not mix logic validation with outcome validation

Those are different questions and they require different evidence.

## Validation Split

### Logic Validation

Use deterministic tests when the question is:

- did a hedge gate change behave correctly
- did a state-machine transition fire under the intended conditions
- did a fallback or exception stay blocked under the wrong conditions
- did telemetry fields get emitted correctly

Logic validation should answer:

- was the implementation correct
- did the guardrail fire only when intended
- did we avoid regressions in the hedge state machine

Preferred tools:

- unit tests
- runner / harness regression tests
- deterministic fixtures for hedge transitions

Examples:

- covariance gate rejects insufficient history
- execution gate blocks an otherwise valid covariance relation
- stale `maker_exit_failed` inventory escalates from `HEDGE_ELIGIBLE` to
  `HEDGE_ACTIVE` only when covariance and execution are both `ok`
- the same stale path stays blocked when covariance is not `ok`

Logic validation is complete when:

- the intended transition is covered by tests
- the opposite or blocked case is also covered
- targeted test suites pass

Paper runs are not required for this step.

### Outcome Validation

Use longer paper runs when the question is:

- did hedge behavior improve realized inventory outcomes
- did accepted hedges reduce hold-tail or force-flat reliance
- did broader hedge opportunity formation actually help
- did a scoring or ranking change improve realized hedge quality

Outcome validation should answer:

- did realized hedge quality improve
- did inventory risk improve after accepted hedges
- did the strategy avoid trading more just to look more active

Preferred tools:

- longer paper batches
- multi-profile sweeps
- pair-level runtime analysis from `hedge_pair_relations`
- scorecard comparisons across runs

Examples:

- compare `proof045_m3_control` vs `proof045_m4/m5/m6`
- evaluate accepted hedge count vs realized improvement count
- inspect whether lower `no_hedge_book` pressure produces better outcomes or
  just more candidate activity

Outcome validation is complete only when paper evidence shows:

- equal or better balanced score
- equal or lower force-flat reliance
- equal or lower hold-tail risk
- no material increase in churn without offsetting benefit
- accepted hedges improve realized outcomes, not just activation counts

## Workflow Order

Follow this order for hedge work:

1. Implement the hedge change.
2. Add deterministic regression coverage for the intended path.
3. Add deterministic coverage for the blocked / failure path.
4. Run the targeted local test suite.
5. Only then run longer paper batches if the question is about realized
   behavior.

Do not skip from implementation straight to paper runs when the real question
is state-machine correctness.

## What Belongs in Tests

Put these in deterministic tests:

- covariance insufficiency / stale / instability rejection
- execution rejection of otherwise plausible candidates
- pair-tier promotion and demotion logic
- stale inventory exceptions
- cooldown and `UNWIND_ONLY` transitions after hedge failure
- telemetry emission for hedge candidate and model-state fields

Tests are the source of truth for:

- correctness
- invariants
- safety boundaries

## What Belongs in Paper Runs

Put these in paper batches:

- whether accepted hedges improve realized inventory outcome
- whether `max_active_markets` expansion creates useful hedge opportunities
- whether pair scoring weights improve realized hedge quality
- whether new filters reduce bad candidates without suppressing good ones
- whether a change worsens churn, hold-tail, or force-flat reliance

Paper runs are the source of truth for:

- usefulness
- calibration quality
- deployment readiness

## Evidence Checklist

Before calling a hedge change "validated," collect both:

### Logic evidence

- deterministic regression tests added
- failure-path tests added
- targeted suites passing

### Outcome evidence

- paper batch artifacts saved
- scorecard comparison recorded
- hedge candidate / acceptance / realized outcome breakdown reviewed

If only logic evidence exists, the change is:

- implemented
- tested
- not yet outcome-validated

If only paper evidence exists, the change is:

- observed
- not yet reliably validated

That is not enough for hedge-policy promotion.

## Current Operating Rule

For hedge experiments in this repo:

- use tests to validate hedge control logic
- use longer paper batches to validate realized portfolio improvement
- do not treat paper path dependence as proof of logic correctness
- do not treat a passing deterministic test as proof of economic usefulness

Both are required, and they answer different questions.
