# Attention OS Information Architecture
# Draft date: 2026-03-19

## Overview

The Attention OS prototype should behave like a coherent small product rather than a set of disconnected screens.

The current shell remains:

- `index.html`
- `graph.html`
- `timeline.html`
- `drift.html`
- `interventions.html`
- `sources.html`

No seventh primary route is added in this phase.

Onboarding and survey capture happen before the main shell as a staged pre-entry flow, then hand off into `index.html`.

## Experience Model

### Modes

- `Observe`: Reflection, Map, Replay
- `Guide`: Drift, intention-setting, substitutions, weekly recommendations
- `Protect`: Interventions, quiet windows, hard limits, override friction
- `Sources`: cross-cutting trust layer, not a mode

### Route ownership

- `index.html`: Observe first, with Guide cues
- `graph.html`: Observe
- `timeline.html`: Observe
- `drift.html`: Guide
- `interventions.html`: Protect
- `sources.html`: Trust layer

## Global Shell

Persistent shell elements:

- product brand
- route navigation
- mode chips
- current intention card
- protect-next card
- key signals rail
- top bar with route label, headline, and range switcher

Behavior rules:

- the 7-day, 30-day, and 90-day range state persists across routes
- selected graph node and replay sequence persist across linked routes
- pinned focus and intervention toggles persist inside browser storage
- lineage and transparency actions are accessible from multiple routes

Storage mechanism for the static prototype:

- browser `localStorage`

## Pre-Shell Onboarding Flow

The next prototype pass should implement onboarding as an overlay or staged welcome flow launched before the shell is visible.

Sequence:

1. consent and trust framing
2. source connection or import selection
3. short survey
4. seeded intention confirmation
5. first weekly reflection

### 1. Consent and trust framing

Must explain:

- local-first posture
- observed versus inferred data
- what the prototype can and cannot see
- that the product does not diagnose mental health conditions

Primary actions:

- continue
- review trust details

### 2. Source connection or import selection

Must show:

- supported source types
- collection method for each source
- coverage limits
- what remains unavailable

Primary actions:

- connect source
- import example history
- skip for now

### 3. Short survey

Must capture:

- vulnerable times of day
- primary motivations for opening feeds
- desired protected topics or goals
- draining versus energizing content types
- comfort with automation and limits

Primary actions:

- answer question
- skip optional items
- review responses

### 4. Seeded intention confirmation

Must show:

- primary intent
- one protected window
- one suggested substitution
- one recommended protect rule

Primary actions:

- accept defaults
- edit intention
- continue to reflection

## Route Breakdown

## 1. Reflection Home

Route:

- `index.html`

Mode:

- Observe, with Guide prompts

Purpose:

- deliver the main "aha" experience

Main modules:

- reflection headline
- memo
- insight cards
- attention budget
- top clusters
- drift summary
- protect-next panel
- survey-aware suggestion callout

Primary actions:

- switch range
- open lineage explanation
- mark an insight as accurate or inaccurate
- jump to map, replay, or drift

## 2. Attention Map

Route:

- `graph.html`

Mode:

- Observe

Purpose:

- visualize the personal attention graph

Main modules:

- graph canvas
- selected cluster detail
- related creators
- related emotions
- related goals
- current versus previous period comparison
- goal, tone, and source filters

Primary actions:

- select node
- filter view
- jump to related replay sequence
- open lineage explanation for the related insight

## 3. Rabbit-Hole Replay

Route:

- `timeline.html`

Mode:

- Observe

Purpose:

- reconstruct how attention drifted through time

Main modules:

- sequence list
- selected sequence detail
- step-by-step replay
- switching burst summary
- emotional shift note
- break point suggestions
- linked intervention teaser

Primary actions:

- select replay
- inspect each step
- jump to related graph cluster
- jump to the relevant protect rule

## 4. Drift View

Route:

- `drift.html`

Mode:

- Guide

Purpose:

- compare intended and actual attention allocation

Main modules:

- drift score
- intended versus actual goal bars
- neglected intentions
- displacement clusters
- recommended substitutions
- pinned focus for next week

Primary actions:

- pin a goal
- mark a displacement cluster as expected or unwanted
- edit intention
- navigate to interventions designed for that cluster

## 5. Intervention Studio

Route:

- `interventions.html`

Mode:

- Protect

Purpose:

- let the user configure gentle control systems

Main modules:

- rule cards
- enabled or disabled state
- category labels
- trigger conditions
- rule previews
- target patterns
- override posture

Primary actions:

- enable or disable a rule
- inspect trigger preview
- inspect which pattern a rule addresses
- review what will be logged if the rule fires

## 6. Sources, Transparency, And Privacy

Route:

- `sources.html`

Mode:

- Cross-cutting trust layer

Purpose:

- make trust, limits, and system behavior explicit

Main modules:

- source connection cards
- connector capability notes
- data coverage notes
- truth boundary section
- privacy architecture cards
- lineage list
- transparency log preview
- export and delete mock controls

Primary actions:

- inspect a source
- inspect connector limits
- open lineage explanation
- inspect a transparency log entry
- trigger export or delete prototype buttons

## Mode-To-Route Mapping Rules

Observe rules:

- Reflection, Map, and Replay must all prioritize reconstruction over prescription
- a user should always be able to move from memo -> graph -> replay without losing context

Guide rules:

- Drift and recommendation surfaces must always be tied back to explicit intentions or survey context
- if survey data is unavailable, the system must fall back to neutral defaults rather than fake personalization

Protect rules:

- Interventions must clearly distinguish prompts, substitutions, quiet windows, and hard limits
- every protect rule must declare how it triggers, what it does, and how it can be overridden

Trust rules:

- Sources must never imply total visibility over the user's digital life
- every inference-bearing feature must have a path back to observed facts or uncertainty language

## Shared State Rules

Persistent state across routes:

- selected time range
- selected graph node
- selected replay sequence
- pinned goal
- intervention toggle states
- insight feedback states
- survey completion state
- seeded intention state

Behavior rules:

- changing the time range updates home, map, replay, drift, and intervention copy coherently
- graph node selection persists when moving between map and replay
- pinned goal affects protect-next treatment on the home route
- survey completion affects recommendation copy, not raw observed data
- rule toggles persist when leaving and re-entering intervention pages

## State-To-Screen Mapping

### Range state

Used by:

- top bar
- memo
- insight cards
- budget
- graph detail
- replay list
- drift comparisons
- interventions

### Intention and survey state

Used by:

- onboarding flow
- current intention card
- drift route
- protect-next card
- substitution recommendations

### Graph state

Used by:

- graph route
- home top clusters
- replay cross-links

### Replay state

Used by:

- timeline route
- graph related-sequence links

### Feedback and trust state

Used by:

- home insight cards
- sources lineage list
- transparency log preview

## Lineage And Transparency Flows

### Lineage explanation flow

Trigger points:

- home insight cards
- map related insight detail
- sources lineage list

Modal contents:

- observed facts
- inferences
- confidence reason
- user-editable fields

### Transparency log flow

Trigger points:

- sources route
- intervention preview
- future guide and protect review surfaces

Entry contents:

- what source or rule was involved
- what the system observed
- what it inferred
- what action it suggested or took
- why the user is seeing the log entry

Design rule:

- observed facts use stronger styling
- inferred claims use softer styling plus confidence language
- agent actions are always labeled as suggested, triggered, or experimental

## Responsive Structure

Desktop:

- full two-column shell
- visible sidebar
- dense but calm cards

Tablet:

- sidebar collapses into a top drawer or compact rail
- graph and detail panels stack

Mobile:

- single-column flow
- shell collapses vertically
- range controls remain usable without clipping
- onboarding survey uses card-by-card progression

## Prototype Delta List For The Next Build Pass

Shell deltas:

- mode chips need clearer semantic ownership
- current intention card should point to structured intentions, not only static copy
- sources route should include connector capability and transparency-log previews

Pre-entry deltas:

- add onboarding overlay before `index.html`
- add consent screen, source selection, and survey stepper
- seed the first weekly reflection with survey-informed copy

Data-model deltas:

- add `survey`
- add `connectorManifests`
- add `transparencyLog`
- evolve `intentions` into a structured intention spec

Copy deltas:

- Reflection should reference user intention and one protected window
- Drift should reference substitutions and not only deviation
- Interventions should preview override behavior and logging

## Content Rules

- no guilt language
- no faux diagnosis
- no claims that the product sees more than it plausibly can
- every major insight should sound interpretable, not omniscient
- survey language should stay behavioral, not therapeutic

## Acceptance Notes

The IA is complete when:

- each route has a clear purpose and mode ownership
- onboarding is defined without introducing a seventh primary route
- shared state behavior is explicit
- lineage and transparency flows are explicit
- the delta from the existing mock is documented for a later implementation pass
