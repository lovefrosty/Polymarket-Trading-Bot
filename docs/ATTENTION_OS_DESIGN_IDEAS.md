# Attention OS Design Ideas
# Draft date: 2026-03-18

## Design Goal

The interface should feel like:

- a control center
- a mirror
- a map

It should not feel like:

- a guilt dashboard
- a generic productivity app
- a clinical diagnostics tool

The emotional target is:

> "I can finally see what has been shaping me."

## Product Metaphor

The strongest metaphor is an observatory.

Other viable metaphors:

- cockpit
- atlas
- reflection chamber
- mind operations center

The Palantir inspiration is useful for structure, not aesthetics. Borrow:

- ontology
- linked objects
- operational decision surfaces
- time-based investigation

Do not borrow:

- militaristic mood
- cluttered enterprise visuals

## Visual Direction 1: Observatory

Tone:

- calm
- intelligent
- spacious
- high-trust

Visual ingredients:

- midnight navy, graphite, muted gold, pale stone
- serif headline paired with a precise mono or neo-grotesk UI font
- star-map graph lines
- layered gradients
- motion that feels slow and intentional

Best for:

- premium, reflective, high-trust brand

## Visual Direction 2: Cognitive Atlas

Tone:

- editorial
- intellectual
- map-like
- analytical

Visual ingredients:

- paper white, forest, ink, rust
- map contour patterns
- timeline rails
- topic territories
- strong hierarchy and annotation

Best for:

- users who want to study themselves more than "optimize"

## Visual Direction 3: Quiet Command Center

Tone:

- modern
- composable
- direct

Visual ingredients:

- off-black, slate, sand, signal green
- cards mixed with network views
- clear system indicators
- strong contrast between observed facts and inferred insights

Best for:

- an early product that still needs to explain itself quickly

## Core Screens

### 1. The Home Screen: Your Week In Attention

Top modules:

- total attention by domain
- top topics
- top creators
- most repeated emotional patterns
- drift-from-goals score

Hero card examples:

- "Your attention concentrated around business content, relationship advice, and market commentary."
- "Your feed shifted from learning to reactive consumption after 9 PM."
- "Your stated goal was reading. Your actual default was short-form video."

### 2. Timeline Screen

Show:

- app switches
- content clusters
- reflections
- interventions
- mood notes

The timeline should answer:

- what happened first
- what followed
- where the rabbit hole began

### 3. Graph Screen

The graph is the signature experience.

Nodes:

- topics
- creators
- goals
- emotions
- content items
- recurring behaviors

Edges:

- consumed
- reinforced
- distracted-from
- calmed
- agitated
- led-to

Useful graph modes:

- last 7 days
- last 30 days
- compare now vs 6 months ago
- filter by goal

### 4. Drift Screen

This is where honesty lives.

Compare:

- stated priorities
- time allocation
- high-value vs low-value attention
- intention vs autopilot sessions

Example modules:

- "You said you wanted to learn physics. Most science attention actually went to popularized clips, not long-form learning."
- "You opened Instagram 31 times after 10 PM and almost always switched away within 90 seconds."

### 5. Intervention Screen

Show interventions as designable systems, not punishments.

Examples:

- when I open Instagram after 9 PM, ask me what I came for
- if I hit 30 minutes on short-form video, redirect me to saved articles
- if I revisit a topic I marked as draining, ask whether I want to continue

### 6. Weekly Memo Screen

This should feel almost like a note from a wise analyst, not a machine log.

Suggested sections:

- what shaped you
- what repeated
- what aligned
- what distracted
- one thing to protect next week

## Signature Features

### Attention Budget

A budget view by:

- learning
- work
- relationships
- entertainment
- passive recovery
- reactive consumption

This helps the user ask whether their digital life reflects their values.

### Rabbit Hole Chains

Render chains like:

- topic A -> creator B -> emotion C -> app-switch burst -> late-night session

This is better than isolated metrics because it shows flow.

### Pendulum View

Map swings in attention over time:

- spiritual content to finance content
- relationships to self-improvement
- optimism to outrage

This is close to your "pendulum" idea and can become a distinctive feature.

### Digital Reflection Card

Example:

- "This week, your digital environment repeatedly trained urgency."
- "Most consumed themes: markets, self-improvement, power, status."
- "Most neglected intention: quiet reading."

## Design Rules

### Rule 1: Separate fact from interpretation

Use visual distinction:

- solid cards for observed facts
- softer cards for inferred patterns
- confidence labels on interpretations

### Rule 2: Use narrative, not just metrics

People do not change because of pie charts alone.

A weekly narrative is more powerful than:

- 4h 12m Instagram
- 2h 09m YouTube

### Rule 3: Let users edit the model

The user should be able to say:

- this topic label is wrong
- this content energized me, not drained me
- this creator belongs under learning, not distraction

### Rule 4: Design for calm

The app should reduce cognitive load, not add more.

That means:

- low-noise charts
- few colors with meaning
- restrained motion
- generous spacing

## Suggested Copy System

Avoid:

- "You failed your limit"
- "Bad habit detected"
- "Addicted behavior"

Prefer:

- "Your attention drifted here"
- "This pattern repeated"
- "This cluster may be shaping your mood"
- "Would you like to redirect now?"

## MVP Wireframe Concepts

### Concept A: Observatory Dashboard

Top row:

- attention allocation
- drift score
- top topic cluster

Middle row:

- timeline strip
- graph preview
- weekly memo preview

Bottom row:

- interventions
- saved intentions
- trends

### Concept B: Story-First Reflection

Top:

- weekly memo

Middle:

- key evidence cards

Bottom:

- graph and intervention controls

This is strong if you want the product to feel human and premium.

### Concept C: Graph-First Explorer

Top:

- date and filter controls

Center:

- full graph canvas

Side panel:

- selected node details
- related sessions
- recommended intervention

This is strong for power users, founders, researchers, and creators.

## Suggested MVP Brand Names

- Attention OS
- Inner Mirror
- Observatory
- Drift
- Mind Atlas
- Reflective
- Signal of Self
- Quiet Graph

## Best Initial Experience

The first-run flow should be:

1. Ask what the user cares about.
2. Ask what they feel is hijacking attention now.
3. Connect the easiest sources first.
4. Build a first map quickly.
5. Deliver one honest, useful insight within minutes.

Example first insight:

> "Your current digital life is organized around novelty and urgency more than depth. Want to build a calmer feed?"
