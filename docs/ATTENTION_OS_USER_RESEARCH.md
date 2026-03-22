# Attention OS User Research Plan
# Draft date: 2026-03-19

## Summary

This document defines the first research program for Attention OS.

The goal is not to prove every long-term hypothesis at once. The goal is to refine the first user, validate the reflection-first thesis, and gather enough behavioral evidence to shape onboarding, guide recommendations, and protect rules.

Primary design center:

- reflective knowledge workers and builders

## Research Goals

- understand how reflective knowledge workers currently experience attention drift
- learn which moments feel most out of control
- identify which content patterns feel energizing versus depleting
- test whether weekly reflection and replay feel more valuable than time-only dashboards
- learn what degree of automation or enforcement feels acceptable

## Core Research Questions

- When do users most often lose track of what shaped their attention?
- Which triggers start reactive sessions: boredom, stress, fatigue, avoidance, curiosity, or social comparison?
- Which current tools are already in use, and where do they fail?
- Do users want explanation first, intervention first, or both?
- What level of trust, visibility, and control is required before users allow the system to enforce boundaries?

## Research Tracks

## 1. Discovery Interviews

Purpose:

- understand lived experience and language

Recommended sample:

- 8 to 12 reflective knowledge workers

Screening characteristics:

- spends significant time in browser, social, and AI tools
- self-identifies as wanting more depth or intentionality
- has tried at least one existing tool such as Screen Time, Focus Mode, RescueTime, Freedom, Opal, or manual habits

### Founder interview guide

Use these prompts:

1. Walk me through the last time you felt your phone or browser hijacked more of your evening than you intended.
2. What usually opens the door to that drift: stress, boredom, work avoidance, curiosity, loneliness, or something else?
3. When you look at Screen Time or a blocker app today, what feels useful and what feels missing?
4. What kinds of content leave you feeling clearer, calmer, or more energized?
5. What kinds of content leave you feeling scattered, pressured, or depleted?
6. Do you think in terms of topics, creators, moods, or sequences when you reflect on your attention?
7. If a product showed you a replay of how your session drifted, what would make that feel insightful rather than invasive?
8. What protections would you actually want to set for evenings, mornings, or work blocks?
9. How much automation would you tolerate before the product started to feel controlling?
10. What would make you trust that the product is serving you rather than optimizing you?
11. If the product could help you protect one thing next week, what would you choose?
12. What language would immediately make you close the app because it sounds judgmental or fake?

Outputs:

- trigger taxonomy
- user language bank
- high-trust versus low-trust patterns
- onboarding copy inputs

## 2. Survey Module

Purpose:

- collect structured behavioral context for onboarding
- support segmentation and future product research

Delivery:

- short onboarding survey inside the product
- longer external survey for research recruiting and hypothesis validation

### Onboarding survey design

Target completion time:

- 2 to 4 minutes

Question bank:

| ID | Prompt | Type | Why it matters |
| --- | --- | --- | --- |
| `vulnerable_time` | What time of day are you most likely to open a feed without meaning to? | single select | Seeds protected windows |
| `opening_motive` | What most often leads you to open reactive apps? | multi select | Distinguishes stress, boredom, curiosity, avoidance |
| `energizing_content` | What kinds of content usually leave you feeling better or clearer? | multi select | Seeds desired clusters |
| `draining_content` | What kinds of content usually leave you feeling scattered or depleted? | multi select | Seeds avoid categories |
| `control_score` | How in control do you currently feel over what you see online? | scale 1-5 | Establishes baseline agency |
| `current_tools` | Which tools or habits do you already use? | multi select | Prevents redundant recommendations |
| `preferred_substitution` | If we helped you replace one reactive moment, what should replace it? | single select or free text | Powers substitutions |
| `automation_comfort` | How much intervention feels acceptable? | single select | Sets guide/protect defaults |
| `primary_intent` | What do you most want to protect this month? | free text | Seeds intention spec |

Recommended answer themes:

- vulnerable times: late night, early morning, work breaks, post-work unwind
- motives: boredom, stress, avoidance, curiosity, social checking
- automation comfort: suggestions only, light friction, scheduled limits

### External survey additions

Use when gathering broader evidence:

- how often users can accurately recall what shaped their last week
- how often they feel algorithmically trapped
- which tools they have abandoned and why
- whether they value explanation more than restriction

## 3. Segmentation Logic

The first segmentation model should stay behavioral, not demographic.

### Reflective Builder

- values depth
- already notices drift
- wants better reconstruction and protection

### Reactive Operator

- high urgency and notification load
- wants relief from constant context switching
- may need stronger quiet-window defaults

### Curious Drifter

- opens feeds from genuine curiosity
- loses structure over time
- benefits from replay and substitutions more than hard limits

### Boundary Seeker

- already uses blockers or routines
- wants smarter targeting, not more restriction

Primary build target:

- Reflective Builder

## 4. Prototype Research

Purpose:

- validate whether the current six-route concept feels coherent and useful

Tasks:

- show weekly reflection home first
- ask users to interpret the memo in their own words
- ask them to move into map and replay without guidance
- ask whether drift and interventions feel helpful or moralizing
- inspect whether the sources route increases trust or feels like compliance theater

Key questions:

- can users explain the main pattern of the week after using the prototype?
- do they understand the difference between observed and inferred?
- do they perceive Protect as supportive or controlling?

## 5. Longitudinal Pilot Research

Use only after basic observatory and protect flows exist.

Goals:

- track whether perceived control improves over several weeks
- see whether users maintain or abandon protect rules
- evaluate whether substitutions reduce reliance on reactive feeds

## Hypotheses

### H1

Users will find a weekly semantic reflection more valuable than time-only dashboards.

### H2

Replay will make attention drift feel more legible than total-time metrics alone.

### H3

Survey-tailored substitutions will feel less punitive than generic blocking rules.

### H4

Visible observed-versus-inferred labeling will increase trust.

### H5

Knowledge workers will prefer explanation and selective protection over aggressive lockouts.

## Success Metrics

### Qualitative metrics

- users can accurately paraphrase the dominant attention pattern of the week
- users report feeling more understood than judged
- users can point to one protection they would actually keep

### Quantitative metrics

- percentage of users who complete onboarding survey
- percentage of users who set at least one protected window
- percentage of users who open lineage explanations
- percentage of users who accept at least one suggested substitution
- perceived control delta before and after weekly reflection

### Research quality metrics

- inference disagreement rate
- trust concern frequency
- abandonment points in onboarding

## Privacy And Ethics Notes

- survey questions should stay behavioral and optional where possible
- avoid clinical wording or pathologizing language
- explain how survey data shapes defaults and recommendations
- allow users to revise or delete survey answers
- do not frame discomfort, boredom, or distraction as diagnosis

## Research Outputs

This research plan should produce:

- onboarding question bank
- first-user segmentation model
- language rules for calm, high-trust copy
- prioritization inputs for Guide and Protect features
- evidence for whether the observatory wedge is stronger than a blocker-first wedge
