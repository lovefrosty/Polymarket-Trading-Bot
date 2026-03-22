# Attention OS Content Model
# Draft date: 2026-03-19

## Overview

This document defines the shared contract for the standalone Attention OS prototype and the next implementation phase.

The prototype is static HTML, CSS, and JavaScript, but the model is written in TypeScript-style interfaces so the same vocabulary can carry forward into a real application.

The model preserves the existing `MockAppState` sections and extends them with survey, connector, and transparency data.

## Top-Level Contract

```ts
interface MockAppState {
  userProfile: UserProfile;
  intentions: IntentionSpec;
  ranges: Record<"week" | "month" | "quarter", TimeRangeState>;
  sources: SourceConnection[];
  insights: Record<"week" | "month" | "quarter", InsightCard[]>;
  graph: GraphState;
  timeline: Record<"week" | "month" | "quarter", TimelineRangeState>;
  drift: Record<"week" | "month" | "quarter", DriftRangeState>;
  interventions: Record<"week" | "month" | "quarter", InterventionRule[]>;
  privacy: PrivacyState;
  lineage: Record<string, LineageExplanation>;
  survey?: SurveyState;
  connectorManifests?: ConnectorManifest[];
  transparencyLog?: TransparencyLogEntry[];
  interventionOutcomes?: InterventionOutcome[];
}
```

## Required Types

```ts
interface TimeRangeState {
  key: "week" | "month" | "quarter";
  label: string;
  headline: string;
  subhead: string;
  memoKicker: string;
  memoBody: string;
  budget: AttentionBudgetSlice[];
  topClusters: TopicCluster[];
  protectNext: ProtectNextCard;
  metrics: RangeMetrics;
  sidebarSignals: SidebarSignal[];
}

interface InsightCard {
  id: string;
  title: string;
  summary: string;
  type: "observed_pattern" | "inferred_pattern" | "goal_alignment" | "risk_window";
  confidence: "Low" | "Medium" | "High";
  evidenceIds: string[];
  range: "week" | "month" | "quarter";
  observedVsInferred: "observed" | "inferred";
}

interface AttentionBudgetSlice {
  id: string;
  label: string;
  value: number;
  note: string;
}

interface TopicCluster {
  id: string;
  label: string;
  tone: "calming" | "pressurizing" | "curious" | "mixed";
  summary: string;
}

interface GraphNode {
  id: string;
  label: string;
  kind: "topic" | "goal" | "emotion" | "creator";
  x: number;
  y: number;
  style: "core" | "accent" | "default";
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
}

interface TimelineSequence {
  id: string;
  label: string;
  timeLabel: string;
  summary: string;
  clusterNodeId: string;
  switchBurst: string;
  emotionalShift: string;
  breakPoint: string;
  opportunities: string[];
  steps: TimelineStep[];
}

interface GoalAlignmentMetric {
  id: string;
  label: string;
  intended: number;
  actual: number;
  status: "underfed" | "overfed" | "balanced";
  note: string;
}

interface InterventionRule {
  id: string;
  title: string;
  category: "prompt" | "substitution" | "quiet_window" | "protect_rule";
  description: string;
  trigger: string;
  action: string;
  targetPatterns: string[];
  preview: string;
  defaultEnabled: boolean;
  tone: "soft" | "alert";
  mode: "guide" | "protect";
  experimental?: boolean;
}

interface SourceConnection {
  id: string;
  name: string;
  status: "connected" | "imported" | "planned";
  dataKinds: string[];
  coverageLevel: string;
  privacyMode: string;
  notes: string;
}

interface LineageExplanation {
  insightId: string;
  observedFacts: string[];
  inferences: string[];
  confidenceReason: string;
  userEditableFields: string[];
}

interface IntentionSpec {
  version: string;
  primaryIntent: string;
  protectedWindows: ProtectedWindow[];
  desiredClusters: string[];
  avoidCategories: string[];
  focusSubstitutions: FocusSubstitution[];
  hardLimits: HardLimit[];
  overridePolicy: OverridePolicy;
  reviewCadence: "daily" | "weekly" | "monthly";
}

interface ConnectorManifest {
  id: string;
  name: string;
  collectionMethod: "os_api" | "browser_extension" | "user_export" | "manual_entry" | "automation";
  permissions: string[];
  dataKinds: string[];
  limits: ConnectorLimit[];
  policyRisk: "low" | "medium" | "high";
  supportsRealtime: boolean;
  supportsExports: boolean;
}

interface ConnectorResultEnvelope {
  connectorId: string;
  collectedAt: string;
  sourceWindow: SourceWindow;
  items: ContentObservation[];
  summary: string;
  samplePolicy: string;
  truncated: boolean;
  confidence: "Low" | "Medium" | "High";
}

interface ContentObservation {
  id: string;
  sourceId: string;
  sessionId: string;
  itemType: "video" | "article" | "post" | "thread" | "chat" | "podcast" | "book" | "other";
  creator: string;
  topicTags: string[];
  format: string;
  dwellSeconds: number;
  interactionType: "view" | "like" | "comment" | "save" | "share" | "scroll" | "listen" | "read";
  observedAt: string;
  observedFields: string[];
  inferredFields: string[];
}

interface SurveyQuestion {
  id: string;
  prompt: string;
  type: "single_select" | "multi_select" | "scale" | "free_text";
  category: "timing" | "motivation" | "content" | "control" | "automation" | "goals";
  required: boolean;
  options?: string[];
}

interface SurveyAnswer {
  questionId: string;
  value: string | string[] | number;
  answeredAt: string;
}

interface SurveyProfile {
  vulnerabilityWindows: string[];
  openingMotives: string[];
  energizingTopics: string[];
  drainingTopics: string[];
  controlScore: number;
  currentTools: string[];
  preferredSubstitutions: string[];
  automationComfort: "low" | "medium" | "high";
  notes?: string;
}

interface InterventionOutcome {
  id: string;
  ruleId: string;
  triggeredAt: string;
  actionTaken: string;
  accepted: boolean;
  overrideReason?: string;
  subsequentDriftDelta?: number;
  selfReportedEffect?: "helpful" | "neutral" | "unhelpful";
}

interface TransparencyLogEntry {
  id: string;
  observedFacts: string[];
  inferences: string[];
  agentAction: string;
  confidence: "Low" | "Medium" | "High";
  userVisibleReason: string;
}
```

## Supporting Types

```ts
interface UserProfile {
  name: string;
  archetype: string;
  summary: string;
}

interface ProtectedWindow {
  label: string;
  start: string;
  end: string;
  days: string[];
  reason: string;
}

interface FocusSubstitution {
  triggerPattern: string;
  replacement: string;
  sourceType: "saved_playlist" | "reading_queue" | "notes" | "breathing_prompt" | "walk" | "custom";
}

interface HardLimit {
  category: string;
  minutesPerDay: number;
  enforcement: "nudge" | "soft_lock" | "hard_lock";
}

interface OverridePolicy {
  frictionLevel: "none" | "light" | "medium" | "high";
  requireReason: boolean;
  allowEmergencyBypass: boolean;
}

interface ConnectorLimit {
  label: string;
  description: string;
}

interface SourceWindow {
  from: string;
  to: string;
}

interface SurveyState {
  version: string;
  questionBank: SurveyQuestion[];
  answers: SurveyAnswer[];
  profile?: SurveyProfile;
  completedAt?: string;
}

interface ProtectNextCard {
  title: string;
  reason: string;
  suggestedAction: string;
}

interface RangeMetrics {
  depthRatio: string;
  reactiveSessions: string;
  topRabbitHole: string;
  goalAlignment: string;
  driftDelta: string;
}

interface SidebarSignal {
  id: string;
  title: string;
  summary: string;
  tone: "amber" | "green" | "red";
}

interface GraphRangeState {
  defaultNodeId: string;
  comparison: string;
  filters: {
    goals: string[];
    tones: string[];
    sources: string[];
  };
  nodeDetails: Record<string, GraphNodeDetail>;
}

interface GraphNodeDetail {
  title: string;
  summary: string;
  trend: string;
  relatedCreators: string[];
  relatedEmotions: string[];
  relatedGoals: string[];
  relatedInsightId: string;
  relatedSequenceId: string;
}

interface GraphState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  ranges: Record<"week" | "month" | "quarter", GraphRangeState>;
}

interface TimelineStep {
  time: string;
  title: string;
  detail: string;
  whyItMatters: string;
  observedVsInferred: "observed" | "inferred";
}

interface TimelineRangeState {
  defaultSequenceId: string;
  sequences: TimelineSequence[];
}

interface DriftRangeState {
  scoreLabel: string;
  scoreValue: number;
  summary: string;
  goals: GoalAlignmentMetric[];
  neglectedIntentions: string[];
  displacementClusters: DriftCluster[];
}

interface DriftCluster {
  id: string;
  label: string;
  summary: string;
}

interface PrivacyState {
  storageModel: string;
  syncPolicy: string;
  directObservability: string[];
  moderateInference: string[];
  speculative: string[];
  controls: string[];
}
```

## Implementation Notes For The Static Prototype

- The canonical mock state still lives in `attention_os_mock/data.js`.
- Range keys remain fixed to `week`, `month`, and `quarter`.
- Cross-route state persists in `localStorage`.
- `LineageExplanation` remains the source of truth for every "Why this insight?" interaction.
- `TransparencyLogEntry` becomes the source of truth for "What did the system do or infer?" interactions.
- `SourceConnection` notes must clearly distinguish:
  - what the product can directly observe
  - what it imports
  - what remains unavailable

## Compatibility Notes

- The current static mock may still use a simplified `intentions` shape. The next prototype pass should map that simplified object into `IntentionSpec`.
- `survey`, `connectorManifests`, `transparencyLog`, and `interventionOutcomes` are optional so the current mock can remain valid while the strategy package is implemented.
- Future real connectors should always emit `ConnectorResultEnvelope` rather than free-form blobs.

## Content Modeling Rules

### Insight rules

- Every visible insight card must map to a `LineageExplanation`.
- Every insight card must declare `observedVsInferred`.
- Confidence must be displayed in UI, not hidden in the data layer.

### Survey rules

- Survey data may shape defaults and recommendations.
- Survey data must never be used to imply diagnosis or emotional certainty.
- Survey answers should remain editable and skippable wherever possible.

### Connector rules

- Connector manifests must declare collection method and policy risk.
- Connector result envelopes must be bounded and may declare truncation.
- `observedFields` and `inferredFields` must remain explicitly separated.

### Transparency rules

- Every intervention or agent action should map to a `TransparencyLogEntry`.
- Every transparency entry must state why the user is seeing it.
- Experimental or future-facing automation must be labeled as such in both data and UI.

### Graph rules

- Graph nodes are stable across ranges.
- Node details can change by range.
- Cross-links from graph to replay use `relatedSequenceId`.

### Drift rules

- Goals must support intended versus actual comparison.
- A goal can be pinned in the UI, but pinning is transient prototype state rather than canonical history.

### Intervention rules

- Intervention descriptions must remain non-punitive.
- Rules that imply feed steering should be marked as experimental or future-facing.
- Protect rules with stronger enforcement should always reference `overridePolicy`.
