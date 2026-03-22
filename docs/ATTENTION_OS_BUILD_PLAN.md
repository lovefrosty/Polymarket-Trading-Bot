# Attention OS Build Plan
# Draft date: 2026-03-18

## Product Definition

Attention OS is a personal, user-owned intelligence layer that helps someone:

- observe where their attention goes
- understand which ideas and emotional triggers dominate their digital life
- compare actual behavior to stated goals
- make deliberate changes to what they consume next

This is not a diagnosis engine. It is a reflective system for metacognition and behavioral steering.

## V1 Outcome

If V1 works, a user should be able to open the product on Sunday night and answer:

- What took most of my attention this week?
- What topics and creators shaped me most?
- Where did I drift away from my stated goals?
- What should I change next week?

## Product Strategy

### Phase 1: Observatory

Build legibility first.

Inputs:

- app/session duration
- website/page history
- imported watch/read histories
- manual goals
- manual reflections
- optional mood check-ins

Outputs:

- weekly summaries
- topic clusters
- creator clusters
- attention budget views
- drift-from-goals insights
- "rabbit hole" chains

### Phase 2: Guidance

Add user-controlled steering.

Examples:

- intentional opening prompts
- time-boxing
- friction before high-risk apps
- replacements like "open saved reading list instead"
- notifications when the user is drifting from a declared goal

### Phase 3: Adaptive Control

Add personalized interventions and optional feed steering.

Examples:

- intervention recommendations based on prior patterns
- dynamic blocks during vulnerable periods
- automated content substitution

Inference:

Directly manipulating third-party recommendation systems with bots should be considered experimental, not core product infrastructure.

## Recommended V1 Scope

### Platforms

- iPhone app for usage reporting and interventions
- Safari/Chrome extension for semantic web capture
- macOS companion for history import and richer graphs

Android can become a strong later platform because its usage telemetry is more flexible, but the cleanest premium prototype is likely Apple-first plus browser extension.

## Core User Stories

1. As a user, I want to see where my attention went across apps, sites, and topics this week.
2. As a user, I want to know which consumed topics were aligned with my goals and which were not.
3. As a user, I want to see the people, ideas, and emotional patterns I repeatedly interact with.
4. As a user, I want the app to suggest actions that return attention to what I actually care about.
5. As a user, I want all sensitive data to remain mine and to be exportable and deletable.

## System Architecture

### 1. Ingestion Layer

Sources:

- iOS Screen Time APIs for activity, authorization, and interventions
- Android usage stats for session-level telemetry
- browser extension for page URL, title, DOM text snippets, and time on page
- imported archives from YouTube and TikTok
- future imports from other platforms
- manual entries:
  - goals
  - books
  - journaling
  - mood
  - intentional projects

### 2. Event Store

Canonical tables or collections:

- `sessions`
- `content_items`
- `creators`
- `topics`
- `goals`
- `reflections`
- `interventions`
- `mood_signals`
- `source_imports`
- `attention_edges`

Suggested event shape:

```json
{
  "event_id": "uuid",
  "source": "youtube_import",
  "user_id": "local_user",
  "ts_start": "2026-03-18T18:00:00Z",
  "ts_end": "2026-03-18T18:07:22Z",
  "app": "YouTube",
  "content_type": "short_video",
  "title": "Quantum mechanics explainer",
  "creator": "Physics Channel",
  "url": "https://...",
  "raw_text": "optional snippet",
  "engagement": {
    "liked": true,
    "saved": false,
    "commented": false
  },
  "import_confidence": 0.82
}
```

### 3. Understanding Layer

Pipelines:

- text cleanup and normalization
- topic extraction
- creator/entity resolution
- embedding generation
- clustering
- timeline summarization
- goal alignment scoring
- emotional valence or arousal estimation
- rabbit-hole sequence detection

Important rule:

- keep observed facts separate from inference

Observed fact:

- "You spent 44 minutes on YouTube Shorts."

Inference:

- "This cluster appears to be high-arousal trading content."

### 4. Knowledge Graph Layer

Core object model:

- `Person`
- `Goal`
- `Session`
- `ContentItem`
- `Creator`
- `Topic`
- `EmotionSignal`
- `Reflection`
- `HabitPattern`
- `Intervention`

Important edges:

- `CONSUMED`
- `LIKED`
- `COMMENTED_ON`
- `RELATED_TO`
- `ALIGNED_WITH`
- `DISTRACTED_FROM`
- `TRIGGERED`
- `FOLLOWED_BY`
- `SUMMARIZED_IN`

This graph is the foundation for the "digital reflection" experience.

### 5. Reflection Layer

Core generated outputs:

- daily summary
- weekly attention memo
- monthly trend brief
- "what shaped your mind this week" card
- "you said vs you did" comparison
- "top rabbit holes" breakdown

### 6. Intervention Layer

V1 interventions:

- open-intention prompt
- session time caps
- quiet windows
- redirect to reading queue
- "pause and reflect" prompt after high-switching behavior

V2 interventions:

- personalized substitution recommendations
- adaptive friction based on historical patterns
- attention budgeting by goal

## Feasibility By Source Type

### Strongest near-term sources

- browser activity
- desktop browsing
- imported history files
- OS usage metadata
- explicit user inputs

### Medium-feasibility sources

- mobile browsing capture
- AI chat exports
- note-taking integrations

### Weakest near-term sources

- full semantic understanding of native Instagram/TikTok feeds in real time
- stable automated interaction inside third-party apps

## Privacy Architecture

This product only works if trust is central.

Recommended default:

- local-first database
- encrypted local vault
- per-source permissioning
- raw event retention controls
- derived-summary retention controls
- explicit export to JSON/CSV/Markdown
- end-to-end encrypted sync only if added later

Recommended design rule:

- never make the user wonder whether their intimate behavioral graph is being used against them

## Suggested Tech Stack

### Client

- SwiftUI iOS app
- optional macOS companion in SwiftUI
- browser extension in TypeScript

### Data / local storage

- SQLite for local-first storage
- vector index for semantic retrieval
- graph projections generated from relational event data

### Intelligence layer

- embeddings for topic clustering
- LLM summarization for weekly/monthly writeups
- deterministic heuristics for usage metrics and rabbit-hole detection

### Sync

- no account required for single-device MVP
- optional encrypted backup later

## Concrete MVP Screens

1. Home
- "This week, your attention went here."

2. Timeline
- a chronological stream of sessions, topics, and reflections

3. Graph
- people, topics, creators, emotions, and goals

4. Drift
- where behavior diverged from declared priorities

5. Interventions
- set limits, prompts, and replacements

6. Weekly Memo
- auto-generated narrative summary with corrections

## 90-Day Build Plan

### Sprint 1: Data foundation

- define ontology
- build local event store
- ship browser extension prototype
- support manual goal and reflection entry

### Sprint 2: First observatory

- ingest browsing sessions
- cluster topics and creators
- build weekly summary
- ship first graph visualization

### Sprint 3: Mobile controls

- integrate iOS Screen Time APIs
- add intentional opening prompts
- add quiet windows and limits
- build "you said vs you did" screen

### Sprint 4: Historical imports

- import YouTube and TikTok exports
- backfill historical graphs
- add month and quarter trend views

### Sprint 5: Intervention intelligence

- add drift detection
- add substitution suggestions
- add intervention outcome tracking

## Design Constraints You Should Accept Early

1. V1 will be partially blind.
- You will not capture every native mobile feed interaction.

2. Reflection quality matters more than data completeness.
- A strong weekly memo beats a perfect but unusable raw log.

3. You should separate facts from models.
- Users must be able to see why an insight exists.

4. Trust is a feature, not a legal footnote.
- The product dies if it feels creepy.

## Recommended One-Sentence MVP

Build a local-first attention observatory that combines browser activity, device usage, imported histories, and user reflections into a weekly knowledge graph and action plan.
