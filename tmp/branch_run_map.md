# Branch / Run Map

## Active branch
- Repo: `/Users/padraigjudge/Desktop/Polymarket Bot`
- Branch: `main`
- HEAD: `2034a4413715a9f5ba20158a6ca8851091e494aa`
- Rule: this is the only branch for ongoing live runtime work.

## Active run
- Root: `/Users/padraigjudge/Desktop/paperfirst-observe-livenessfix-20260311T185029Z`
- Current health: stale runtime on DB truth; `PAPER` gate is `CLOSED` until fresh book/decisions return and rollover failures stop.

## Archived runs
- Archive root: `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/`
- Old Desktop run roots live there and should not be reused for active validation.

## Inactive Desktop worktrees
- `/Users/padraigjudge/Desktop/agent-hygiene`
- `/Users/padraigjudge/Desktop/agent-offline-analysis`
- `/Users/padraigjudge/Desktop/agent-promo-fix`
- `/Users/padraigjudge/Desktop/agent-reference-model`
- `/Users/padraigjudge/Desktop/agent-rollover-fix`
- `/Users/padraigjudge/Desktop/agent-rollover-nightfix`

## Uncommitted work on main
- `/Users/padraigjudge/Desktop/Polymarket Bot/core/reference_ws.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/market_context.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/data/polymarket_ws.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/tests/test_market_rollover_ws_confirm.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/tests/test_microstructure_tradeable_badges.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/tests/test_dashboard_terminal_alignment.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/tests/test_paper_experiment_profile.py`
- `/Users/padraigjudge/Desktop/Polymarket Bot/tests/test_reference_ws.py`
- Untracked exports / archives remain outside the active runtime path.

## Rule of use
- Use `main` only for live runtime work.
- Do not resume old Desktop worktrees unless there is a specific recovery need.
