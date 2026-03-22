# Attention OS Product Requirements Document
# Draft date: 2026-03-19

## Product Vision

Attention OS is a personal, user-owned observatory for understanding where attention went, what ideas reinforced it, what it displaced, and what to protect next.

The product should make digital life legible enough that a reflective knowledge worker can:

- inspect what shaped a week
- compare actual attention to stated intentions
- study rabbit-hole sequences without shame
- choose calmer, more deliberate interventions

## Product Promise

Show where attention went, what it reinforced, what it displaced, and what to protect next.

## Audience

Primary audience for this phase:

- the founder using this as a design, strategy, and implementation scaffold

Primary eventual user:

- a reflective adult knowledge worker or builder who feels their attention is being shaped faster than they can interpret it

Secondary future users:

- students
- creators
- ambitious lifelong learners

## Primary Persona

### Reflective Builder

Profile:

- spends substantial time across phone, browser, video platforms, chat tools, and AI tools
- values learning, depth, and self-authorship
- feels pulled into loops of urgency, comparison, novelty, and partial attention
- wants a system that clarifies attention rather than moralizes about it

Needs:

- legibility
- agency
- pattern recognition
- a bridge between quantified behavior and lived meaning

Does not want:

- a simplistic blocker
- a guilt dashboard
- faux-clinical diagnosis
- hidden automation or surveillance vibes

## User Problem Statement

Reflective users can usually tell when their attention drifted, but they cannot reconstruct the pattern clearly enough to intervene. Existing tools report time, not meaning. Recommendation systems shape what users see, but users do not get a commensurate model of what those systems are reinforcing in them.

## Jobs To Be Done

### Functional jobs

- Help me see what dominated my attention this week.
- Help me understand which topics, creators, and emotional tones kept repeating.
- Help me compare actual behavior to my stated intentions.
- Help me set and revisit rules without turning the product into punishment.
- Help me connect tracking, explanation, and intervention in one place.

### Emotional jobs

- Help me feel less at the mercy of my feeds and habits.
- Help me study my patterns without shame.
- Help me regain a sense of authorship over my digital environment.

### Identity jobs

- Help me become the kind of person who lives with more intention.
- Help me preserve curiosity, depth, and self-direction.

## Product Identity

Attention OS is:

- a reflection-first observatory
- a harness-driven agent product
- a user-owned attention layer
- a trust-sensitive system with explicit truth boundaries

Attention OS is not:

- a clinical mental health product
- a pure blocker or focus timer
- a guaranteed algorithm-retraining system
- an ad-supported attention broker

## Product Modes

### Observe

Owns:

- Reflection
- Map
- Replay

Goal:

- reconstruct what happened with enough clarity that the user can form a better judgment

### Guide

Owns:

- intention setting
- weekly recommendations
- drift interpretation
- substitutions informed by goals and survey context

Goal:

- help the user decide what deserves more protection, less exposure, or a cleaner substitution

### Protect

Owns:

- quiet windows
- hard limits
- override friction
- trust-sensitive enforcement controls

Goal:

- help the user uphold chosen boundaries without turning the system punitive

### Cross-Cutting Trust Layer

`Sources` remains a trust and transparency route, not a standalone product mode.

It owns:

- source coverage
- truth boundaries
- lineage explanations
- local-first posture
- export and deletion visibility

## Non-Goals

- diagnosing depression, anxiety, ADHD, trauma, or addiction
- guaranteeing that third-party recommendation systems can be retrained reliably
- building a production-grade ingestion stack in this phase
- replacing parental controls or enterprise monitoring tools
- collecting more data simply for its own sake

## Product Principles

### 1. Reflection before restriction

The product should first explain what happened before it recommends what to do.

### 2. User ownership first

Data should be local-first, understandable, and deletable.

### 3. Explain the model

Observed facts, inferred patterns, and product suggestions must be clearly separated.

### 4. No shame language

The product should sound reflective and calm, not punitive or self-optimization obsessed.

### 5. Mechanical trust

Guardrails, logs, and override flows should be explicit and inspectable, not hidden behind vague assurances.

### 6. Truth boundaries matter

The product must be explicit about what it can observe directly and what it can only infer.

## Core User Experience

Primary value moment:

> "I can see what shaped my mind this week, where I drifted, and what to change next."

Primary weekly flow:

1. enter the weekly reflection
2. read the memo
3. inspect one or two dominant clusters
4. review drift from intentions
5. choose one guide recommendation or one protect rule for the coming week

Primary onboarding flow:

1. consent to local-first data handling and truth-boundary language
2. connect or import one or two sources
3. answer a short survey about vulnerability windows, motivations, and desired protections
4. land in the first weekly reflection with a seeded intention

## Experience Requirements By Mode

### Observe requirements

- must summarize the week in narrative form before overwhelming the user with charts
- must provide visible lineage for every major insight
- must let the user inspect map and replay without losing the weekly memo as the center of gravity
- must distinguish observed facts from inferred patterns everywhere

### Guide requirements

- must store a structured intention spec
- must compare actual attention to user-authored intentions
- must suggest substitutions, not only constraints
- must use survey context to personalize recommendations without implying diagnosis

### Protect requirements

- must keep enforcement user-authored and reversible
- must use friction and scheduling before hard lockout whenever feasible
- must log triggers, overrides, and outcomes
- must keep any feed-shaping or automation behind explicit future/experimental labeling

### Trust requirements

- must show what sources are connected, how data entered the system, and what the system cannot see
- must explain why a given insight exists
- must expose local-first, export, and deletion posture plainly

## Current Prototype Scope

This phase remains doc-first. The existing `attention_os_mock/` stays valid and separate from `dashboard/`.

Prototype requirements remain:

- six-route shell
- shared mock state
- reflection-first weekly home
- explicit lineage explanations
- desktop and mobile coherence

Prototype additions are documented as deltas for a later build pass rather than being implemented here.

## V1

V1 is the first real product scope, not the current static prototype.

### In scope

- local-first user profile and intention spec
- onboarding survey and consent flow
- OS-level usage ingestion where officially supported
- imported histories and browser metadata where user-provided or extension-mediated
- weekly reflection memo
- attention map
- rabbit-hole replay
- drift view
- guide recommendations and substitutions
- protect rules using existing OS or browser-level enforcement primitives
- trust route with lineage and transparency log

### Out of scope

- cloud sync by default
- hidden background automation on third-party social platforms
- automatic liking, following, commenting, or feed manipulation
- message content ingestion by default
- mental health diagnosis or treatment claims

## V1.5

V1.5 extends the observatory without changing the trust posture.

Possible additions:

- richer connector coverage
- improved survey-driven personalization
- intervention outcome tracking and weekly efficacy summaries
- note-taking and PKG export integrations
- better cross-device reconciliation

## Future Research

These items remain explicitly future-facing and experimental:

- agent-mediated feed steering on third-party platforms
- physiological signals such as sleep, heart rate, or stress sensors
- longitudinal comparative studies against existing well-being tools
- collaborative or household attention planning
- federated or privacy-preserving shared learning systems

## Key Features

### Reflection Home

Purpose:

- deliver the main product insight in a narrative form

Must include:

- reflection headline
- memo
- insights with lineage
- attention budget
- top clusters
- drift summary
- protect-next card

### Attention Map

Purpose:

- show the user's digital mind map

Must include:

- graph canvas
- selected node detail
- related creators
- related emotions
- related goals
- current versus previous period comparison

### Rabbit-Hole Replay

Purpose:

- let the user inspect how attention drifted through time

Must include:

- replay sequence list
- selected sequence detail
- step-by-step chain
- switching burst summary
- emotional shift note
- suggested break points

### Drift View

Purpose:

- compare intended and actual attention allocation

Must include:

- drift score
- intended versus actual goal bars
- neglected intentions
- displacement clusters
- next-week protection selection

### Intervention Studio

Purpose:

- let the user design gentle control systems

Must include:

- prompts
- substitutions
- quiet windows
- protect rules
- trigger previews
- outcomes and override language in future product versions

### Sources, Transparency, And Privacy

Purpose:

- establish trust and make limits explicit

Must include:

- source connection cards
- coverage notes
- direct versus inferred data
- lineage explanations
- transparency log
- export and deletion visibility

## Success Criteria

### Prototype and strategy phase

- docs are internally coherent and implementation-oriented
- every inference-heavy concept has a visible trust boundary
- the six-route prototype remains valid
- future implementation work is decision complete

### Product indicators

- users can name the dominant patterns that shaped their week
- users report more perceived control over attention allocation
- suggested substitutions feel more assistive than punitive
- users can distinguish observed behavior from inferred patterning
- protect rules are used intentionally rather than reactively

## Risks

### Risk 1: It feels like surveillance

Mitigation:

- local-first defaults
- bounded collection
- clear source coverage
- deletion and export visibility

### Risk 2: It overstates what it knows

Mitigation:

- observed versus inferred styling
- lineage explanations
- user correction loops
- explicit truth-boundary language

### Risk 3: It becomes a moralizing productivity app

Mitigation:

- reflection-first framing
- non-shaming copy
- curiosity and protection language over discipline theater

### Risk 4: It tries to do too much too early

Mitigation:

- keep V1 centered on observability, intentions, and user-authored rules
- defer feed steering, biometrics, and broad connector sprawl

## Phased Roadmap

### Phase 0: Founder strategy package and static mock

- research brief
- strategy package
- harness architecture
- user research plan
- aligned PRD, IA, and content model

### Phase 1: Observatory MVP

- local-first profile
- consent and survey onboarding
- one or two official data sources plus imports
- weekly reflection, map, replay, and trust route

### Phase 2: Guide layer

- richer intentions
- drift interpretation
- substitutions
- weekly recommendation memory

### Phase 3: Protect layer

- quiet windows
- hard limits
- override friction
- intervention outcomes and efficacy review

### Phase 4: Experimental research

- selective automation
- algorithm-shaping experiments
- broader data integrations

## Explicit Defaults

- knowledge workers are the first user, not the whole market
- the weekly memo is the hero experience
- the graph supports the memo rather than replacing it
- survey data informs defaults but does not imply diagnosis
- emotion and valence remain low- or medium-confidence unless explicitly user-reported
- `attention_os_mock/` remains separate from `dashboard/`
