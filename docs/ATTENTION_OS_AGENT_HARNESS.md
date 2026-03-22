# Attention OS Agent Harness
# Draft date: 2026-03-19

## Summary

Attention OS needs a harness, not just a model.

The harness is the environment that makes agent assistance safe, legible, and useful:

- structured user intentions
- bounded source connectors
- progressive disclosure
- mechanical guardrails
- reversible actions
- transparent logs

Without this layer, the product either floods the model with raw attention traces or overreaches into opaque automation.

## Harness Goals

- keep the system reflection-first
- keep collection bounded and inspectable
- keep suggestions tied to explicit user intentions
- prevent silent or high-risk automation
- preserve reversibility and user control

## System Components

### 1. Intention spec

The user's goals and boundaries live in a structured `IntentionSpec`.

This becomes the agent's first source of truth for:

- what matters
- what should be protected
- what should be reduced
- what kind of friction is acceptable

### 2. Connectors

Each source connector is described by a `ConnectorManifest` and emits `ConnectorResultEnvelope`.

Connector examples:

- OS usage APIs
- browser extension
- user exports
- manual entry

### 3. Observation store

Observed items are normalized into `ContentObservation` objects and stored locally.

### 4. Summarization and clustering layer

The system turns raw observations into:

- cluster candidates
- replay sequences
- insight candidates
- drift candidates

### 5. Transparency layer

Every inference-heavy insight maps to `LineageExplanation`.

Every agent recommendation or action maps to `TransparencyLogEntry`.

### 6. Intervention layer

Guide and Protect logic operate only on:

- user-authored intentions
- declared connector inputs
- reversible OS or browser-level rules

## Core Harness Rules

## 1. Progressive disclosure

Agents should not receive all raw history at once.

Collection flow:

1. connector returns a bounded envelope
2. envelope includes summary and truncation status
3. clustering operates on bounded observations
4. only the most relevant observations are promoted into memo, replay, or drift generation

Default budgets:

- per connector run: maximum 50 content observations exposed to the agent
- per reflection generation: maximum 12 candidate insights
- per range: maximum 8 surfaced clusters and 3 surfaced replay sequences
- if more content exists, the system must summarize or sample rather than expand context unboundedly

## 2. Separate observed from inferred

Observed data:

- timestamps
- app or source
- duration
- titles or metadata
- direct user actions
- user-entered survey answers and goals

Inferred data:

- topic clusters
- emotional tone
- drift interpretation
- suggested substitution

Rule:

- observed and inferred data must remain separate in storage and UI

## 3. Prefer low-risk collection paths

Preferred order:

1. official OS API
2. browser extension or user export
3. manual input
4. browser automation

Rules:

- every connector must declare `collectionMethod`
- every connector must declare `policyRisk`
- `automation` connectors are disallowed in V1 by default unless explicitly marked experimental

## 4. Keep actions reversible

Allowed V1 and V1.5 actions:

- generate memo
- suggest substitutions
- recommend quiet windows
- activate or deactivate user-authored limits
- log overrides and outcomes

Disallowed by default:

- hidden likes, comments, follows, or saves on third-party platforms
- background account actions outside explicit user confirmation
- message ingestion by default
- irreversible automation

## 5. Mechanical guardrails over vague intent

The harness should enforce rules structurally:

- no connector without a manifest
- no agent-facing collection result without bounded envelope
- no intervention without declared trigger and action
- no inference surfaced without lineage
- no protect rule with stronger enforcement without override policy

## Intention Spec Example

```json
{
  "version": "1.0",
  "primaryIntent": "Protect evening depth for reading and focused study.",
  "protectedWindows": [
    {
      "label": "Evening depth block",
      "start": "21:00",
      "end": "23:00",
      "days": ["Mon", "Tue", "Wed", "Thu", "Sun"],
      "reason": "Preserve long-form reading and reflection."
    }
  ],
  "desiredClusters": ["physics curiosity", "long-form reading", "close friends"],
  "avoidCategories": ["trading hype", "late-night reels", "comparison loops"],
  "focusSubstitutions": [
    {
      "triggerPattern": "Late-night urge to open short-form video",
      "replacement": "Open saved physics playlist",
      "sourceType": "saved_playlist"
    }
  ],
  "hardLimits": [
    {
      "category": "reactive feeds",
      "minutesPerDay": 30,
      "enforcement": "soft_lock"
    }
  ],
  "overridePolicy": {
    "frictionLevel": "medium",
    "requireReason": true,
    "allowEmergencyBypass": true
  },
  "reviewCadence": "weekly"
}
```

## Connector Contract

Each connector must expose:

- identity
- collection method
- permissions
- data kinds
- explicit limits
- policy risk
- real-time or export support

Each connector run must return:

- `connectorId`
- `collectedAt`
- `sourceWindow`
- `items`
- `summary`
- `samplePolicy`
- `truncated`
- `confidence`

Mandatory connector behaviors:

- summarize if the raw result set exceeds the budget
- identify missing fields instead of fabricating them
- mark uncertainty rather than normalizing it away

## Context Management Rules

### Weekly reflection

Promote:

- top 3 to 5 dominant clusters
- top 3 insight candidates
- top 1 to 3 replay sequences
- top 1 protect recommendation

### Monthly review

Promote:

- cluster-level trend changes
- repeated replay families
- intention drift over time

### Quarterly review

Promote:

- durable patterns only
- longitudinal shifts in curiosity, urgency, or relationships

Rule:

- longer ranges should compress, not accumulate detail

## Guide Layer Rules

Guide can:

- compare actual versus intended attention
- propose substitutions
- highlight neglected intentions
- recommend low-friction rules

Guide cannot:

- silently activate enforcement
- imply the user has a disorder
- pretend inferred emotional tone is a fact

## Protect Layer Rules

Protect can:

- trigger quiet windows
- apply user-authored limits
- add friction before high-risk openings
- log outcomes

Protect cannot:

- exceed the user's declared override policy
- silently escalate enforcement
- mutate third-party feeds by default

## Override Flow

Required flow for soft and hard constraints:

1. show the relevant intention or protected window
2. show the rule that fired
3. allow override
4. if friction level is `medium` or higher, require a short reason or pause
5. log the decision as `InterventionOutcome`

Override copy must remain calm and non-punitive.

## Feedback Loops

Every intervention should produce an `InterventionOutcome`.

Minimum tracked fields:

- when it fired
- what it did
- whether the user accepted it
- whether the user overrode it
- whether the user later reported it as helpful

Weekly review should compare:

- which rules fired
- which rules were accepted
- whether drift improved afterward

## Failure Modes And Responses

### Sparse data

Response:

- reduce confidence
- prefer neutral summaries
- ask for manual context rather than inventing certainty

### Noisy classification

Response:

- expose editable tags
- let the user mark insights inaccurate
- store corrections for future summaries

### Rule fatigue

Response:

- prefer substitutions and quiet windows over constant prompts
- surface which rules are ignored most often

### Privacy discomfort

Response:

- keep coverage explicit
- allow source-by-source disconnect
- keep data local by default

### Overreach into automation

Response:

- gate all automation behind experimental labeling and explicit opt-in
- keep third-party feed shaping out of default scope

## Acceptance Criteria

The harness spec is complete when:

- an implementer can define connectors without inventing new trust rules
- every intervention path has a declared trigger, action, and override posture
- every inference-bearing output has a lineage or transparency path
- context budgets are explicit enough to prevent uncontrolled prompt growth
