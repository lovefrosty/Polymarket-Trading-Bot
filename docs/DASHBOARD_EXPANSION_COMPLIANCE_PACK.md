# Dashboard Expansion Compliance Pack
# Last updated: 2026-05-20

## Purpose

Expand the current dashboard into a real operator terminal for:

- listed assets
- options
- prediction markets
- risk management
- research intake
- execution controls

without losing the safety properties needed for a real-money system.

This document is both a product brief and an operating-controls brief.

## Current Starting Point

This repo already has a dark terminal-style dashboard in:

- [dashboard/app.py](../dashboard/app.py)
- [dashboard/panels/portfolio.py](../dashboard/panels/portfolio.py)
- [dashboard/panels/core_mm_live.py](../dashboard/panels/core_mm_live.py)
- [dashboard/data_access.py](../dashboard/data_access.py)

The current CSS is already close to a terminal aesthetic:

- dark background
- neon accents
- dense metrics
- panelized layout

So the right move is expansion, not a visual reset.

## Design Direction

You mentioned three useful references:

1. **Fidelity Active Trader Pro**
2. **terminal-style dense operator UI**
3. **Roman Paolucci / Quant Guild GitHub work**

### What to take from Fidelity Active Trader Pro

From Fidelity’s own material, the relevant product ideas are:

- customizable layouts
- account and positions views
- watch lists
- option chain
- positions-by-underlying
- heat map view
- news / alerts / charts
- dense multi-window workflow

Those are good product requirements. The visual language is secondary.

### What to take from Roman Paolucci / Quant Guild

The GitHub references are useful more for **system decomposition** than for a
drop-in dashboard framework.

Relevant public repos and profile items:

- Roman’s GitHub profile:
  https://github.com/romanmichaelpaolucci
- `AI_Stock_Trading`:
  https://github.com/romanmichaelpaolucci/AI_Stock_Trading
- `Quant_Dev`:
  https://github.com/romanmichaelpaolucci/Quant_Dev
- `Q-Fin`:
  https://github.com/romanmichaelpaolucci/Q-Fin

What they suggest:

- treat trading systems as staged architecture
- separate finance math, execution, and experimentation
- build reusable finance components rather than burying them in UI code

That matches what this repo needs.

## Product Principle

This dashboard should become a **workspace**, not a page.

The operator should be able to answer, from one terminal:

- what do I own
- what is my risk
- what changed today
- what positions need action
- what options are sensible around those positions
- what prediction-market books are live
- what research is relevant right now
- what should be hedged, trimmed, rolled, or ignored

## Workspace Model

The dashboard should evolve into five workspaces.

### Workspace 1: Portfolio

Primary purpose:

- whole-account awareness

Views:

- net liquidation
- cash and buying power
- gross / net exposure
- realized / unrealized PnL
- drawdown
- concentration by symbol / sector / asset class
- account alerts and sync health

### Workspace 2: Options

Primary purpose:

- covered-call / cash-secured-put decision support

Views:

- option chains
- grouped equity + options exposure by underlying
- greeks by underlying and by expiry bucket
- assignment risk flags
- earnings / ex-dividend blockers
- premium, breakeven, and downside scenario summaries

### Workspace 3: Trading / Execution

Primary purpose:

- order-state visibility and control

Views:

- staged recommendations
- broker orders
- fills
- cancel / replace queue
- execution health
- session/auth health

### Workspace 4: Prediction Markets

Primary purpose:

- preserve the existing Polymarket / Kalshi strategy lane

Views:

- live books
- quote health
- adverse-selection metrics
- markout
- tail-price exposure
- market-selection and research overlays

### Workspace 5: Research

Primary purpose:

- convert external information into structured decisions

Views:

- incoming notes
- tagged symbols
- thesis registry
- macro regime labels
- paper summaries
- signal confidence and expiry

## Information Architecture

Recommended top-level navigation:

1. `Overview`
2. `Portfolio`
3. `Options`
4. `Execution`
5. `Prediction`
6. `Research`
7. `Controls`

Recommended persistent top bar:

- broker sync status
- market data status
- live / paper / read-only mode
- total PnL
- gross exposure
- active alerts
- kill-switch state
- last sync timestamp

## Compliance and Safety Rules

This is the non-negotiable part.

### Rule 1: Read-only is a first-class mode

The dashboard must support a mode where:

- broker data is visible
- recommendations are visible
- no execution path exists

This mode should be the default during build-out.

### Rule 2: Visual separation between read-only, paper, and live

The operator must never wonder whether the dashboard is in:

- read-only
- paper
- live

These modes should have unmistakable top-bar indicators.

### Rule 3: No mixed manual ambiguity

If the broker session is shared with manual activity:

- surface session warnings
- surface stale sync warnings
- surface “manual activity detected” where possible

### Rule 4: Recommendations are not orders

The system should distinguish clearly between:

- insight
- recommendation
- staged order
- submitted order
- acknowledged order
- fill

Do not collapse these into one object.

### Rule 5: Every execution decision needs provenance

For any staged or submitted action, store:

- source strategy
- triggering evidence
- timestamp
- account
- symbol / underlying
- risk check result
- operator confirmation state

### Rule 6: Options need additional safeguards

Before any option-selling workflow exists, require:

- position ownership verification for covered calls
- cash reservation check for cash-secured puts
- event blocker checks
- spread / liquidity filters
- max assignment concentration limits

### Rule 7: Dashboard must degrade gracefully

If broker sync fails:

- show stale last-known state
- show exact stale age
- suppress execution controls
- elevate warning state

## Data Model Expansion

The dashboard should stop depending only on current bot runtime tables.

Add broker-normalized sources for:

- account snapshots
- positions
- grouped exposures
- options contracts
- options chains
- executions
- sync status
- research items
- recommendations

## Suggested Views To Build First

### First wave

- account summary
- positions grid
- open orders grid
- recent executions grid
- concentration heat map
- sync health

### Second wave

- options-underlying grouped view
- option chain
- lot-aware position details
- pnl attribution
- factor / beta / VaR panel

### Third wave

- recommendation queue
- staged-order blotter
- manual approve / reject workflow
- research evidence panel

## UX Principles

### Dense but not chaotic

Use:

- fixed columns
- sortable grids
- symbol-linked panels
- drillthrough panes
- hot-state metrics at the top

Avoid:

- giant cards
- decorative landing-page layouts
- large empty whitespace

### Terminal-like, not ornamental

The current dark dashboard theme is good. Keep it practical.

Aim for:

- high information density
- low visual noise
- consistent color semantics
- keyboard-friendly operator flow

### Symbol context should propagate

When a user clicks `AAPL`, the following should update together:

- position detail
- options chain
- recent fills
- notes / research
- trade recommendations

That is how the dashboard starts behaving like a terminal.

## Product Gap Against Fidelity-Style Tools

Based on Fidelity’s published Active Trader Pro material, the gaps you should
close are:

- customizable workspaces
- positions/watchlist integration
- heat map visualization
- options chain and options-specific panels
- dense multi-window interaction
- linked symbol context

You do not need to copy Fidelity’s product literally. You need to match the
operator utility.

## Recommended Technical Direction For This Repo

Short term:

- keep the existing Streamlit dashboard alive
- add IBKR-backed data sources
- expand the current panel system

Medium term:

- if Streamlit starts constraining multi-pane interaction too much, move the
  terminal into a dedicated frontend app
- keep the backend data model and risk logic separate from the frontend

That means the likely long-term split is:

```text
backend/
  brokers/
  strategies/
  risk/
  research/
  telemetry/

frontend/
  terminal/
```

But that is not today’s migration.

## Immediate Build Plan

### Milestone A: IBKR portfolio terminal

Build:

- overview workspace
- portfolio workspace
- sync health

### Milestone B: options operator workspace

Build:

- grouped underlying view
- option chain
- event blockers
- sell-call / sell-put recommendation cards

### Milestone C: execution workspace

Build:

- recommendation queue
- staged orders
- paper execution state
- risk-gate panel

### Milestone D: unified terminal

Build:

- prediction-market lane
- research lane
- shared controls

## Decision Standard

A dashboard feature is only worth shipping if it helps with one of these:

- better risk control
- faster but clearer decision making
- better visibility into state
- fewer operational mistakes

If it is merely decorative, cut it.

## Sources

Official Fidelity product references used here:

- Active Trader Pro access and customization:
  https://www.fidelity.com/customer-service/how-to-access-active-trader-pro
- Active Trader Pro desktop FAQ:
  https://www.fidelity.com/trading/advanced-trading-tools/active-trader-pro/faqs-desktop
- Positions / Watch Lists help:
  https://www.fidelity.com/products/atbt/help/ActiveTraderTools_Watch_List_Help.html
- Fidelity trading platforms overview:
  https://www.fidelity.com/trading/trading-platforms

Roman Paolucci / Quant Guild references:

- GitHub profile:
  https://github.com/romanmichaelpaolucci
- AI Stock Trading repo:
  https://github.com/romanmichaelpaolucci/AI_Stock_Trading
- Quant Dev repo:
  https://github.com/romanmichaelpaolucci/Quant_Dev
- Q-Fin repo:
  https://github.com/romanmichaelpaolucci/Q-Fin
