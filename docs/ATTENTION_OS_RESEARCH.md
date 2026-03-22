# Attention OS Research Brief
# Research snapshot date: 2026-03-19

## Summary

Attention OS should be positioned as a metacognitive product for reflective knowledge workers, not as an anti-phone product, a generic blocker, or a pseudo-clinical mental health tool.

Core thesis:

- modern digital systems optimize for repeated engagement, not for user legibility
- existing well-being tools help users count time and enforce limits, but rarely help them understand what shaped that time
- a reflection-first, user-owned observatory can make attention patterns legible enough for better judgment, gentler interventions, and more durable agency

This brief separates:

- observed evidence
- plausible inference
- product hypothesis

That distinction matters because Attention OS sits at the boundary between measurable behavior, imperfect inference, and product ambition.

## Product Thesis Ladder

### Problem

People spend large portions of their day inside recommendation systems, browsers, chats, and AI tools without a coherent record of what they consumed, what themes repeated, or what those patterns displaced.

### Mechanism

Recommendation systems, notifications, and short-form interfaces repeatedly redirect attention toward novelty, urgency, and emotionally sticky content. Existing dashboards expose quantity better than meaning.

### User pain

Reflective users often feel:

- mentally scattered
- unable to reconstruct what dominated their week
- aware that they drifted, but unable to explain how the drift happened
- reliant on tools that either moralize, block, or oversimplify the problem

### Product intervention

Build a local-first Attention OS that:

- captures bounded usage and content traces where feasible
- organizes them into a personal knowledge graph
- distinguishes observed facts from inferred patterns
- uses a harness-driven agent layer to summarize, explain, and suggest interventions inside clear guardrails

### Why now

- short-form, social, browser, and AI-mediated attention are converging into one continuous digital environment
- OS-level well-being APIs and blocking controls now provide partial enforcement primitives
- users increasingly understand that algorithms shape attention, but still lack a commensurate interface for seeing that shaping
- policy, transparency, and mental-health discourse are moving toward greater user legibility and control

### Why this is not just screen time

Screen-time tools answer:

- how long did I spend?
- what app was open?

Attention OS should answer:

- what did I keep feeding my mind?
- what pattern kept repeating?
- what did that pattern displace?
- what deserves protection next?

## Research Areas

## 1. OS Digital Well-Being Tools

### Observed evidence

- Android Digital Wellbeing shows screen time, times opened, and notifications received, and also supports app timers, website timers in Chrome, Bedtime mode, and Focus mode.
  Source: https://support.google.com/android/answer/9346420?hl=en
- Apple Screen Time shows app and website activity, device pickups, and lets users schedule Downtime, set App Limits, and define Always Allowed apps and contacts.
  Sources:
  - https://support.apple.com/guide/iphone/get-started-with-screen-time-iphbfa595995/ios
  - https://support.apple.com/guide/iphone/set-schedules-and-limits-iphb0c7313c9/ios

### Plausible inference

- OS dashboards are useful as first-layer awareness and enforcement tools because they are system-level, persistent, and comparatively trustworthy.
- Their core limitation is semantic thinness: they mostly report app or site categories, not the ideas, emotional tones, creators, or behavioral chains that shaped the session.

### Product hypothesis

- Attention OS should treat Screen Time and Digital Wellbeing features as enforcement substrate, not as the finished product.
- The product layer should translate usage traces into meaning, goal alignment, and reflective action.

## 2. Productivity Trackers And Blockers

### Observed evidence

- RescueTime tracks apps and websites, exposes reports and goals, and offers Focus Sessions with distraction blocking and post-session summaries.
  Sources:
  - https://www.rescuetime.com/
  - https://help.rescuetime.com/article/295-the-assistant
  - https://help.rescuetime.com/article/374-get-to-know-focus-sessions
- Freedom blocks apps, websites, and even the broader internet across devices, supports scheduled sessions, offers a reflective "Pause" extension, and includes Locked Mode to increase friction during active sessions.
  Sources:
  - https://freedom.to/
  - https://support.freedom.to/en/articles/3149199-how-to-use-pause
  - https://support.freedom.to/en/articles/1802927-locked-mode
- Forest uses gamified focus sessions, allow lists, focus statistics, and a timeline to help users stay off their phones.
  Sources:
  - https://www.forestapp.cc/
  - https://apps.apple.com/us/app/forest-focus-for-productivity/id866450515
- Opal offers app and website block lists, allow lists, weekly Focus Reports, and device-local reporting tied to screen-time data on supported platforms.
  Sources:
  - https://www.opal.so/
  - https://www.opal.so/help/what-is-focus-report
  - https://www.opal.so/help/how-do-i-use-block-lists-and-allow-lists
  - https://www.opal.so/help/how-when-and-where-does-opal-report-your-screen-time

### Plausible inference

- The market has already proven demand for:
  - passive time tracking
  - precommitment and blocking
  - light reflective reporting
  - behavioral friction
- The main gap is not whether people want help with attention. It is that most tools stop at counting, blocking, or gamifying without building a semantic model of what the user actually consumed.

### Product hypothesis

- Attention OS should sit one layer above blockers and timers:
  - interpret what happened
  - connect it to goals and curiosity
  - recommend substitutions and protections
  - remain compatible with existing enforcement tools

## 3. Neurocognitive Effects, Health Outcomes, And Reward-Loop Pressure

### Observed evidence

- A 2024 systematic review found problematic short-form video viewing is associated with adverse mental-health symptoms, lower executive functioning, and poorer academic outcomes, while also noting causal evidence is still limited.
  Source: https://pubmed.ncbi.nlm.nih.gov/41231585/
- The U.S. Surgeon General's 2023 advisory states that social media may pose a risk of harm to the mental health and well-being of children and adolescents.
  Source: https://www.hhs.gov/surgeongeneral/priorities/youth-mental-health/social-media/index.html
- The "Brain Drain" paper found that the mere presence of one's smartphone can reduce available cognitive capacity, even when the device is not actively being used.
  Source: https://doi.org/10.1086/691462
- CDC's October 2024 Data Brief reported that U.S. teenagers with 4 or more hours of daily screen time were more likely to report anxiety symptoms, depression symptoms, irregular sleep, lower physical activity, and weight concerns than teens with less than 4 hours.
  Source: https://www.cdc.gov/nchs/products/databriefs/db513.htm
- A 2025 randomized controlled trial in *BMC Medicine* found that reducing smartphone screen time to `<= 2 h/day` for three weeks improved depressive symptoms, sleep quality, stress, and well-being in healthy students.
  Source: https://bmcmedicine.biomedcentral.com/articles/10.1186/s12916-025-03944-z

### Plausible inference

- It is reasonable to model many phone and feed experiences as intermittent-reward environments that encourage repeated checking, partial attention, and cognitive fragmentation.
- The popular cultural language around dopamine is directionally useful but frequently overspecified. Product copy should refer to reward-loop pressure and attentional fragmentation, not to biomarker-level certainty.

### Product hypothesis

- Attention OS can identify likely shallow-attention windows by combining:
  - switching frequency
  - repeated returns to the same content cluster
  - late-night or fatigue-prone timing
  - short-form dominance

## 4. Algorithmic Agency, Filter Bubbles, And Rabbit-Hole Experiences

### Observed evidence

- A 2025 systematic review on social-media algorithms, filter bubbles, echo chambers, and youth describes filter bubbles as personalization effects that reduce exposure to diverse viewpoints, while echo chambers arise through selective interaction and confirmation bias.
  Source: https://www.mdpi.com/2075-4698/15/11/301
- The same review argues youth-focused work should include intervention testing and algorithmic literacy rather than assuming awareness alone is enough.
  Source: https://www.mdpi.com/2075-4698/15/11/301
- A 2025 qualitative paper on young people's views of TikTok algorithms and eating-disorder content found participants describing the algorithm as "working against" them and not reliably respecting "not interested" signals.
  Source: https://pmc.ncbi.nlm.nih.gov/articles/PMC12831324/
- Mental Health America's 2025 *Breaking the Algorithm* report says young participants understood that social platforms are designed to keep them scrolling yet felt they had limited control over logging off.
  Source: https://www.mhanational.org/wp-content/uploads/2025/03/Breaking-the-Algorithm-report.pdf

### Plausible inference

- Many users experience recommendation systems as quasi-agentive forces rather than neutral utilities.
- The felt loss of control is a core product opportunity. A clear replay of how a session bent away from intention may be more psychologically useful than another daily time chart.

### Product hypothesis

- One of the strongest user moments is not "I spent four hours on my phone."
- It is "I can finally see the chain that took me from one intention to a whole evening of drift."

## 5. Digital Minimalism And Screen-Time Reduction

### Observed evidence

- A randomized online intervention combining self-monitoring, app blocking, mindfulness, and mood tracking reduced distraction from smartphone use.
  Source: https://pmc.ncbi.nlm.nih.gov/articles/PMC7369880/
- A 2024 systematic review and meta-analysis on mindfulness programs for problematic internet use found reductions in problematic use and some reduction in screen time, while rating the overall certainty of evidence as low to very low.
  Source: https://pmc.ncbi.nlm.nih.gov/articles/PMC11220809/
- Existing consumer tools operationalize digital-minimalism tactics through schedules, app limits, focus sessions, allow lists, pauses, and bedtime constraints.
  Sources:
  - Android Digital Wellbeing: https://support.google.com/android/answer/9346420?hl=en
  - Apple Screen Time: https://support.apple.com/guide/iphone/set-schedules-and-limits-iphb0c7313c9/ios
  - Freedom: https://freedom.to/
  - Opal: https://www.opal.so/

### Plausible inference

- Restriction can help, but pure restriction is brittle unless it is paired with context, reflection, and viable substitutions.
- The strongest behavioral pattern is likely:
  - visibility
  - brief pause
  - substitution
  - post-hoc reflection
  rather than raw lockout alone

### Product hypothesis

- Attention OS should not be blocker-first.
- It should use blocking, quiet windows, and friction as optional Protect-layer tools inside a reflection-first system.

## 6. Personal Knowledge Graphs As Metacognitive Infrastructure

### Observed evidence

- Google's *Personal Knowledge Graphs: A Research Agenda* positions PKGs as a serious direction for personal data organization and personalized services.
  Source: https://research.google/pubs/personal-knowledge-graphs-a-research-agenda/
- The survey *An Ecosystem for Personal Knowledge Graphs* defines PKGs as structured information resources about entities related to an individual, their attributes, and the relations between them, and emphasizes personal ownership plus ecosystem interfaces.
  Source: https://arxiv.org/abs/2304.09572
- Obsidian's official product and help docs show the continued demand for local-first linked-note systems and graph-based navigation for personal thinking.
  Sources:
  - https://obsidian.md/
  - https://help.obsidian.md/link-notes

### Plausible inference

- PKG-style systems are good at organizing what users explicitly capture, but weak at passively recording what shaped attention before the user chose to write anything down.
- Attention OS can bridge passive traces and explicit reflection by turning content, sessions, goals, and user annotations into one user-owned graph.

### Product hypothesis

- The dashboard should not just summarize usage.
- It should build a "digital mind map" where topics, creators, goals, emotions, and interventions remain explorable across time.

## 7. Harness Design For Agentic Attention Systems

### Observed evidence

- OpenAI's 2026 *Harness engineering* article argues that reliable agents depend less on exhorting the model to "try harder" and more on designing tools, abstractions, feedback loops, and mechanical rules that make work legible and enforceable for the agent.
  Source: https://openai.com/index/harness-engineering/
- The same article describes making logs, metrics, UI state, and repository rules legible to agents, then encoding "golden principles" as mechanical constraints rather than informal taste.
  Source: https://openai.com/index/harness-engineering/
- OpenAI's *A practical guide to building agents* describes the basic agent stack as model, tools, and instructions, recommends starting with simpler single-agent systems, and emphasizes guardrails, tool safeguards, and human intervention for high-risk actions.
  Source: https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- SWE-agent's official repository documents a production-style agent harness built around tools, structured tasks, and real repository feedback loops.
  Source: https://github.com/SWE-agent/SWE-agent

### Plausible inference

- Attention OS needs a harness, not just a model:
  - structured intentions
  - bounded connector results
  - progressive disclosure
  - transparent intervention logs
  - reversible actions
- Without that harness, the system will either overwhelm the model with raw traces or overreach into opaque automation.

### Product hypothesis

- The deepest product differentiator is not "an AI summary of your phone."
- It is a harness-driven attention system where user goals, connectors, summaries, interventions, and transparency are mechanically structured for safe agent assistance.

## 8. Digital Self-Tracking Critique And Ethical Guardrails

### Observed evidence

- The CHI stage-based model of personal informatics argues that useful self-tracking systems must support preparation, collection, integration, reflection, and action. Most products stop too early in that chain.
  Source: https://www.cs.cmu.edu/~jhm/Readings/2010-ianli-chi-stage-based-model.pdf
- *Living the metrics: Self-tracking and situated objectivity* argues that self-tracking can support reflection and learning, but data become meaningful only in context and should not be treated as self-evident truth.
  Source: https://pmc.ncbi.nlm.nih.gov/articles/PMC6001216/

### Plausible inference

- A product that turns attention into metrics without interpretation risks becoming another self-optimization trap.
- The product should help users inspect patterns and revise them, not claim to expose the final truth about their mind.

### Product hypothesis

- Attention OS should combine quantified traces with narrative reflection:
  - weekly memos
  - user corrections
  - survey context
  - journals
  - editable interpretations

## 9. Transparency, Regulation, And Well-Being Design

### Observed evidence

- Mental Health America's 2025 report recommends well-being-focused algorithms that go beyond passive reminders by prompting self-reflection, explaining why content appears, and surfacing protective features more clearly.
  Source: https://www.mhanational.org/wp-content/uploads/2025/03/Breaking-the-Algorithm-report.pdf
- The European Commission's July 1, 2025 update on the Digital Services Act states that harmonized transparency reporting rules are now in effect because inconsistent reporting practices had hindered comparison and assessment across services.
  Source: https://digital-strategy.ec.europa.eu/en/news/harmonised-transparency-reporting-rules-under-digital-services-act-now-effect

### Plausible inference

- The policy environment is moving toward explainability, but user-facing transparency remains weak and inconsistent.
- A user-centric product can differentiate itself by making every insight and intervention inspectable rather than mysterious.

### Product hypothesis

- Explainability should be a primary feature, not a legal appendix:
  - why am I seeing this insight?
  - what was directly observed?
  - what was inferred?
  - what action did the system take?
  - what remains unknown?

## Truth Boundary

### What the product can observe directly

- OS-level usage duration, pickups, app opens, or notification counts where APIs expose them
- browser history, page titles, and metadata via approved browser tooling
- imported watch history, reading exports, and archive files
- user-authored goals, reflections, corrections, and survey responses
- intervention acceptance or override events inside the product

### What the product can infer with moderate confidence

- recurring topics and creators
- likely rabbit-hole sequences
- drift from stated priorities
- cluster-level emotional tone labels
- likely high-friction windows such as late-night switching bursts

### What remains speculative or weakly inferable

- exact emotional state from content traces alone
- the inner ranking logic of third-party recommendation systems
- whether a given intervention will retrain a feed reliably
- any clinical conclusion about depression, anxiety, ADHD, trauma, or addiction

## Product Implications

The strongest product direction remains:

- reflection-first
- harness-driven
- local-first
- explanation-rich
- non-shaming

The strongest V1 wedge remains:

- a weekly observatory that shows what shaped the week
- where attention drifted from stated intentions
- one recommendation or pattern to protect next

The weakest V1 wedge remains:

- promising direct control over Instagram, TikTok, or YouTube recommendation systems as a default capability

That may remain future experimental research, but it should not define the first product.

## Verified Source Notes

All links below were verified on 2026-03-19.

### OS well-being and attention tools

- Android Help, *Manage how you spend time on your Android phone with Digital Wellbeing*
  https://support.google.com/android/answer/9346420?hl=en
- Apple Support, *Get started with Screen Time on iPhone*
  https://support.apple.com/guide/iphone/get-started-with-screen-time-iphbfa595995/ios
- Apple Support, *Set schedules with Screen Time on iPhone*
  https://support.apple.com/guide/iphone/set-schedules-and-limits-iphb0c7313c9/ios
- RescueTime homepage
  https://www.rescuetime.com/
- RescueTime Help, *The Assistant*
  https://help.rescuetime.com/article/295-the-assistant
- RescueTime Help, *Get to Know Focus Sessions*
  https://help.rescuetime.com/article/374-get-to-know-focus-sessions
- Freedom homepage
  https://freedom.to/
- Freedom Help, *How to use Pause*
  https://support.freedom.to/en/articles/3149199-how-to-use-pause
- Freedom Help, *Locked Mode*
  https://support.freedom.to/en/articles/1802927-locked-mode
- Forest official site
  https://www.forestapp.cc/
- Forest App Store listing
  https://apps.apple.com/us/app/forest-focus-for-productivity/id866450515
- Opal homepage
  https://www.opal.so/
- Opal Help, *What is Focus Report?*
  https://www.opal.so/help/what-is-focus-report
- Opal Help, *How do I use a Block List or Allow List*
  https://www.opal.so/help/how-do-i-use-block-lists-and-allow-lists
- Opal Help, *How, when, and where does Opal report your Screen Time?*
  https://www.opal.so/help/how-when-and-where-does-opal-report-your-screen-time

### Mental health, cognition, and behavior

- U.S. Surgeon General, *Social Media and Youth Mental Health* (2023)
  https://www.hhs.gov/surgeongeneral/priorities/youth-mental-health/social-media/index.html
- PubMed, *Problematic short-form video viewing and its effects on mental health, cognitive functioning, and academic performance: A systematic review* (2024)
  https://pubmed.ncbi.nlm.nih.gov/41231585/
- Ward et al., *Brain Drain: The Mere Presence of One's Own Smartphone Reduces Available Cognitive Capacity* (2017)
  https://doi.org/10.1086/691462
- CDC NCHS Data Brief No. 513, *Daily Screen Time and Health Outcomes Among Teenagers* (2024)
  https://www.cdc.gov/nchs/products/databriefs/db513.htm
- BMC Medicine, *Smartphone screen time reduction improves mental health: a randomized controlled trial* (2025)
  https://bmcmedicine.biomedcentral.com/articles/10.1186/s12916-025-03944-z
- PMC, *Mind over Matter: Testing the Efficacy of an Online Randomized Controlled Trial to Reduce Distraction from Smartphone Use* (2020)
  https://pmc.ncbi.nlm.nih.gov/articles/PMC7369880/
- PMC, *Mindfulness programs for problematic usage of the internet: A systematic review and meta-analysis* (2024)
  https://pmc.ncbi.nlm.nih.gov/articles/PMC11220809/

### Algorithms, transparency, and youth agency

- MDPI, *Trap of Social Media Algorithms: A Systematic Review of Research on Filter Bubbles, Echo Chambers, and Their Impact on Youth* (2025)
  https://www.mdpi.com/2075-4698/15/11/301
- PMC, *'Falling down the rabbit hole': a thematic analysis of young people's views on TikTok algorithms and eating disorder content* (2025)
  https://pmc.ncbi.nlm.nih.gov/articles/PMC12831324/
- Mental Health America, *Breaking the Algorithm* (2025)
  https://www.mhanational.org/wp-content/uploads/2025/03/Breaking-the-Algorithm-report.pdf
- European Commission, *Harmonised transparency reporting rules under the Digital Services Act now in effect* (2025)
  https://digital-strategy.ec.europa.eu/en/news/harmonised-transparency-reporting-rules-under-digital-services-act-now-effect

### Personal informatics and PKGs

- Li et al., *A Stage-Based Model of Personal Informatics Systems* (CHI 2010)
  https://www.cs.cmu.edu/~jhm/Readings/2010-ianli-chi-stage-based-model.pdf
- PMC, *Living the metrics: Self-tracking and situated objectivity* (2018)
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6001216/
- Google Research, *Personal Knowledge Graphs: A Research Agenda* (2021)
  https://research.google/pubs/personal-knowledge-graphs-a-research-agenda/
- arXiv, *An Ecosystem for Personal Knowledge Graphs: A Survey and Research Roadmap* (2023 preprint / later journal publication)
  https://arxiv.org/abs/2304.09572
- Obsidian product site
  https://obsidian.md/
- Obsidian Help, *Link notes*
  https://help.obsidian.md/link-notes

### Harness and agent system design

- OpenAI, *Harness engineering: leveraging Codex in an agent-first world* (2026)
  https://openai.com/index/harness-engineering/
- OpenAI, *A practical guide to building agents* (2025)
  https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- SWE-agent official repository
  https://github.com/SWE-agent/SWE-agent
