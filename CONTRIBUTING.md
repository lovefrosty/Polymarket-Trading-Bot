# Contributing

Thanks for taking an interest in the project. Contributions are welcome when they are narrow, reviewable, and explicit about what kind of evidence supports them.

## Before opening work

For a material change, open an issue or discussion describing:

- the problem or hypothesis;
- why it matters to a user, operator, or research decision;
- the smallest test that could support or disprove it;
- whether it touches observation, paper execution, or live execution.

Never include wallet keys, API credentials, passphrases, seed phrases, funded addresses tied to a private identity, or raw `.env` files.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

## Pull requests

Keep each pull request focused. A useful description includes:

- what changed and why;
- the files and runtime paths affected;
- new assumptions or failure modes;
- tests and exact commands run;
- the evidence class: fixture, synthetic, historical, live public data, paper execution, or exchange-confirmed live fill;
- before/after output for user-facing or quantitative changes.

Do not describe simulated fills as live fills or a positive paper run as proven profitability. Missing data should be reported as a blocker, not silently filled with favorable assumptions.

## Trading and safety changes

Changes that can create, modify, cancel, settle, or fund real orders require a separate review. They should default to disabled, fail closed when inputs or credentials are missing, preserve an operator kill switch, and include explicit notional and loss limits.

The current Polymarket live path is blocked pending CLOB V2 migration and end-to-end validation. A pull request must not remove that warning based only on unit tests or paper results.

## Review standard

A maintainer should be able to reproduce the claimed result from the pull request. Strategy or economics changes should include an out-of-sample or forward-paper evaluation plan, fee and latency assumptions, drawdown reporting, and a clear disproof condition.

By contributing, you agree that the project maintainer may ask for changes, decline a strategy claim that lacks evidence, or keep live execution disabled even when the code compiles.
