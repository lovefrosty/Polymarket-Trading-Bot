/*
 * Attention OS mock data contract.
 * Canonical shape is documented in docs/ATTENTION_OS_CONTENT_MODEL.md.
 */

window.AttentionOSData = {
  userProfile: {
    name: "Founder Lens",
    archetype: "Reflective builder",
    summary:
      "A curious, ambitious adult trying to protect depth, agency, and self-authorship inside an algorithmic environment."
  },
  intentions: {
    northStar: "Depth over novelty",
    weekly: "Protect evening reading, keep physics curiosity alive, and reduce reactive short-form loops after 9 PM.",
    protect: ["Reading", "Physics", "Deep work", "Real conversation"],
    reduce: ["Trading-guru loops", "Status comparison", "Late-night relationship spirals"]
  },
  ranges: {
    week: {
      key: "week",
      label: "7 Days",
      headline: "Your week bent toward urgency more than depth.",
      subhead:
        "The strongest pattern was not random distraction. It was a repeatable evening sequence from curiosity into comparison and urgency.",
      memoKicker: "This week, your digital environment repeatedly trained urgency.",
      memoBody:
        "Entrepreneurship clips, trading commentary, and relationship advice dominated your evenings. Reading and long-form learning stayed present, but mostly in the morning when your attention was calmer.",
      budget: [
        { id: "learning", label: "Learning", value: 22, note: "Physics, essays, lectures" },
        { id: "work", label: "Work", value: 18, note: "Research, writing, planning" },
        { id: "relationships", label: "Relationships", value: 14, note: "Advice content, messaging" },
        { id: "entertainment", label: "Entertainment", value: 17, note: "Passive video and browsing" },
        { id: "reactive", label: "Reactive Feed", value: 29, note: "Short-form loops and app switching" }
      ],
      topClusters: [
        {
          id: "markets",
          label: "Markets and urgency",
          tone: "pressurizing",
          summary: "Night-heavy cluster of market clips, status content, and self-optimization pressure."
        },
        {
          id: "reading",
          label: "Reading and deliberate thought",
          tone: "calming",
          summary: "Morning sessions created the cleanest depth and the least switching."
        },
        {
          id: "physics",
          label: "Physics and pure curiosity",
          tone: "curious",
          summary: "A small but high-value cluster that felt energizing without emotional drag."
        }
      ],
      protectNext: {
        title: "Physics and long-form curiosity",
        reason: "This cluster produced depth without the same pressure or social comparison.",
        suggestedAction: "Create a one-tap evening substitution that opens saved physics videos or essays instead of reels."
      },
      metrics: {
        depthRatio: "42%",
        reactiveSessions: "18",
        topRabbitHole: "Trading Guru Loop",
        goalAlignment: "6.4/10",
        driftDelta: "+31%"
      },
      sidebarSignals: [
        {
          id: "signal-night-switch",
          title: "Late-night switching spike",
          summary: "Instagram to YouTube to Safari loops rose after 9:40 PM.",
          tone: "amber"
        },
        {
          id: "signal-reading",
          title: "Reading sessions held",
          summary: "Morning reading blocks were longer and calmer this week.",
          tone: "green"
        },
        {
          id: "signal-trading",
          title: "Trading urgency cluster",
          summary: "Market commentary repeatedly displaced planned work.",
          tone: "red"
        }
      ]
    },
    month: {
      key: "month",
      label: "30 Days",
      headline: "Your month shows a split life: mornings for depth, nights for drift.",
      subhead:
        "Across the month, the pattern is not chaos but oscillation between deliberate curiosity and urgency-heavy feed behavior.",
      memoKicker: "The long arc is not chaos. It is oscillation.",
      memoBody:
        "Over 30 days, your attention repeatedly moved between disciplined learning phases and novelty-driven evenings. The strongest recurring clusters were markets, self-worth, relationships, and business ambition.",
      budget: [
        { id: "learning", label: "Learning", value: 27, note: "Books, lectures, technical curiosity" },
        { id: "work", label: "Work", value: 19, note: "Project planning and execution" },
        { id: "relationships", label: "Relationships", value: 16, note: "Advice content, messaging, social loops" },
        { id: "entertainment", label: "Entertainment", value: 13, note: "Unstructured browsing and leisure" },
        { id: "reactive", label: "Reactive Feed", value: 25, note: "Repeated short-form and switching loops" }
      ],
      topClusters: [
        {
          id: "status",
          label: "Status and self-measurement",
          tone: "pressurizing",
          summary: "Ambition and comparison showed up together across the month."
        },
        {
          id: "relationships",
          label: "Relationships and self-worth",
          tone: "mixed",
          summary: "Advice content often blended genuine care with rumination and self-evaluation."
        },
        {
          id: "physics",
          label: "Physics and curiosity return",
          tone: "curious",
          summary: "Intentional learning returned as a stabilizing force."
        }
      ],
      protectNext: {
        title: "A single evening theme",
        reason: "The strongest month-level improvement came when evenings had one declared purpose.",
        suggestedAction: "Lock each evening to one theme such as reading, physics, or friends."
      },
      metrics: {
        depthRatio: "48%",
        reactiveSessions: "62",
        topRabbitHole: "Status Spiral",
        goalAlignment: "6.9/10",
        driftDelta: "+24%"
      },
      sidebarSignals: [
        {
          id: "signal-reading-streak",
          title: "Reading streak established",
          summary: "Long-form sessions strengthened when mornings stayed clear of feeds.",
          tone: "green"
        },
        {
          id: "signal-self-worth",
          title: "Relationship content surge",
          summary: "Advice-heavy clips correlated with more late-night searching and app churn.",
          tone: "amber"
        },
        {
          id: "signal-pendulum",
          title: "Pendulum swing detected",
          summary: "Attention swung between calm curiosity and ambition-heavy status content.",
          tone: "red"
        }
      ]
    },
    quarter: {
      key: "quarter",
      label: "90 Days",
      headline: "Across 90 days, your attention identity is becoming legible.",
      subhead:
        "The long pattern is a search for orientation: ambition, philosophy, relationships, and scientific curiosity all compete for your mindshare.",
      memoKicker: "The strongest pattern is not distraction. It is a repeated search for orientation.",
      memoBody:
        "Over the quarter, you cycled through markets, philosophy, relationships, and scientific curiosity. The opportunity is to shape the environment so ambition feeds depth instead of constant urgency.",
      budget: [
        { id: "learning", label: "Learning", value: 31, note: "Books, technical topics, philosophy" },
        { id: "work", label: "Work", value: 21, note: "Business planning and execution" },
        { id: "relationships", label: "Relationships", value: 15, note: "Advice content and communication" },
        { id: "entertainment", label: "Entertainment", value: 11, note: "Leisure and passive drift" },
        { id: "reactive", label: "Reactive Feed", value: 22, note: "Residual short-form reactivity" }
      ],
      topClusters: [
        {
          id: "philosophy",
          label: "Philosophy and self-reflection",
          tone: "calming",
          summary: "This cluster deepened coherence and slowed switching."
        },
        {
          id: "selfworth",
          label: "Ambition and self-worth pressure",
          tone: "pressurizing",
          summary: "A recurring bridge between business content and emotional unease."
        },
        {
          id: "physics",
          label: "Scientific curiosity",
          tone: "curious",
          summary: "One of the cleanest long-horizon signals of authentic interest."
        }
      ],
      protectNext: {
        title: "Identity protection rules",
        reason: "The quarter shows which topics create depth without corrosive pressure.",
        suggestedAction: "Define protected clusters that deserve easier access than default feed behavior."
      },
      metrics: {
        depthRatio: "54%",
        reactiveSessions: "121",
        topRabbitHole: "Ambition to Anxiety Chain",
        goalAlignment: "7.3/10",
        driftDelta: "+17%"
      },
      sidebarSignals: [
        {
          id: "signal-identity",
          title: "Identity arc emerging",
          summary: "Philosophy, physics, and long-form reading correlate with calmer attention.",
          tone: "green"
        },
        {
          id: "signal-business",
          title: "Business pressure remains sticky",
          summary: "Ambition-heavy content still pulls attention into urgency clusters.",
          tone: "amber"
        },
        {
          id: "signal-quarter",
          title: "Autopilot remains episodic",
          summary: "You are not lost at the quarter level, but daily loops still undermine the long game.",
          tone: "red"
        }
      ]
    }
  },
  sources: [
    {
      id: "ios",
      name: "iPhone Screen Time",
      status: "connected",
      dataKinds: ["app duration", "category usage", "schedule windows"],
      coverageLevel: "Session-level and category-level visibility",
      privacyMode: "On-device only",
      notes: "Useful for duration, switching windows, and interventions; does not expose every item viewed inside native social feeds."
    },
    {
      id: "browser",
      name: "Browser Extension",
      status: "connected",
      dataKinds: ["URLs", "page titles", "reading time", "page text snippets"],
      coverageLevel: "High for desktop web behavior",
      privacyMode: "Local vault with user review",
      notes: "This is the strongest semantic source in the prototype because it can actually see content-level patterns."
    },
    {
      id: "youtube_takeout",
      name: "YouTube History Import",
      status: "imported",
      dataKinds: ["watch history", "search history", "creator frequency"],
      coverageLevel: "Historical backfill",
      privacyMode: "Imported archive only",
      notes: "Useful for long-range memory and trend views, but not a real-time source."
    },
    {
      id: "tiktok_export",
      name: "TikTok Data Export",
      status: "planned",
      dataKinds: ["activity archive", "settings metadata"],
      coverageLevel: "Partial and export-based",
      privacyMode: "User-initiated import",
      notes: "Helpful for backfill and account-level traces; limited for precise semantic replay."
    },
    {
      id: "manual_reflection",
      name: "Manual Reflection Layer",
      status: "connected",
      dataKinds: ["goals", "mood notes", "journals", "corrections"],
      coverageLevel: "Highest semantic trust",
      privacyMode: "Private user-authored",
      notes: "This is the product's grounding layer. It lets the user correct the model rather than being passively described by it."
    }
  ],
  insights: {
    week: [
      {
        id: "insight-week-urgency",
        title: "Evenings repeatedly trained urgency",
        summary: "The densest evening cluster combined trading commentary, self-optimization clips, and relationship advice.",
        type: "inferred_pattern",
        confidence: "Medium",
        evidenceIds: ["browser", "ios", "manual_reflection"],
        range: "week",
        observedVsInferred: "inferred"
      },
      {
        id: "insight-week-reading",
        title: "Reading sessions created the clearest depth windows",
        summary: "Morning reading had the longest uninterrupted sessions and the lowest switching rate.",
        type: "observed_pattern",
        confidence: "High",
        evidenceIds: ["browser", "ios"],
        range: "week",
        observedVsInferred: "observed"
      },
      {
        id: "insight-week-drift",
        title: "Your stated goal was depth, but late-night attention drifted toward reactivity",
        summary: "The biggest mismatch sat in the evening gap between what you meant to do and what the feed captured.",
        type: "goal_alignment",
        confidence: "Medium",
        evidenceIds: ["ios", "manual_reflection", "youtube_takeout"],
        range: "week",
        observedVsInferred: "inferred"
      }
    ],
    month: [
      {
        id: "insight-month-oscillation",
        title: "The month-level pattern is oscillation, not randomness",
        summary: "Your attention alternated between disciplined curiosity and novelty-heavy evenings.",
        type: "inferred_pattern",
        confidence: "Medium",
        evidenceIds: ["browser", "ios", "youtube_takeout"],
        range: "month",
        observedVsInferred: "inferred"
      },
      {
        id: "insight-month-physics",
        title: "Physics content is a high-signal cluster worth protecting",
        summary: "This topic repeatedly produced longer, calmer sessions and less app churn.",
        type: "observed_pattern",
        confidence: "High",
        evidenceIds: ["browser", "youtube_takeout"],
        range: "month",
        observedVsInferred: "observed"
      },
      {
        id: "insight-month-status",
        title: "Status-heavy content kept blending ambition with self-evaluation",
        summary: "Business and self-worth themes converged more often than the user intended.",
        type: "inferred_pattern",
        confidence: "Medium",
        evidenceIds: ["browser", "manual_reflection"],
        range: "month",
        observedVsInferred: "inferred"
      }
    ],
    quarter: [
      {
        id: "insight-quarter-orientation",
        title: "The quarter reveals a search for orientation",
        summary: "Ambition, philosophy, relationships, and scientific curiosity all competed to define the user's attention identity.",
        type: "inferred_pattern",
        confidence: "Medium",
        evidenceIds: ["browser", "youtube_takeout", "manual_reflection"],
        range: "quarter",
        observedVsInferred: "inferred"
      },
      {
        id: "insight-quarter-philosophy",
        title: "Philosophy and reflection clusters correlate with calmer attention states",
        summary: "Longer-form reflective content produced more coherent sessions and less switching.",
        type: "observed_pattern",
        confidence: "High",
        evidenceIds: ["browser", "manual_reflection"],
        range: "quarter",
        observedVsInferred: "observed"
      },
      {
        id: "insight-quarter-pressure",
        title: "Ambition remains the gateway into self-worth pressure",
        summary: "Business content did not just consume time; it often carried a specific emotional frame of catching up or proving value.",
        type: "inferred_pattern",
        confidence: "Medium",
        evidenceIds: ["browser", "manual_reflection", "ios"],
        range: "quarter",
        observedVsInferred: "inferred"
      }
    ]
  },
  graph: {
    nodes: [
      { id: "markets", label: "Markets", kind: "topic", x: 15, y: 18, style: "core" },
      { id: "status", label: "Status", kind: "emotion", x: 40, y: 8, style: "core" },
      { id: "relationships", label: "Relationships", kind: "topic", x: 70, y: 18, style: "accent" },
      { id: "reading", label: "Reading", kind: "goal", x: 31, y: 58, style: "default" },
      { id: "selfworth", label: "Self-Worth", kind: "emotion", x: 61, y: 58, style: "default" },
      { id: "physics", label: "Physics", kind: "topic", x: 81, y: 69, style: "accent" },
      { id: "philosophy", label: "Philosophy", kind: "topic", x: 52, y: 82, style: "default" }
    ],
    edges: [
      { id: "e1", source: "markets", target: "status" },
      { id: "e2", source: "status", target: "relationships" },
      { id: "e3", source: "status", target: "reading" },
      { id: "e4", source: "reading", target: "selfworth" },
      { id: "e5", source: "relationships", target: "selfworth" },
      { id: "e6", source: "selfworth", target: "physics" },
      { id: "e7", source: "reading", target: "philosophy" }
    ],
    ranges: {
      week: {
        defaultNodeId: "markets",
        comparison:
          "Compared with the prior week, urgency clusters strengthened more than curiosity clusters, especially after 8 PM.",
        filters: {
          goals: ["All", "Reading", "Physics", "Relationships"],
          tones: ["All", "Calming", "Pressurizing", "Curious"],
          sources: ["All", "Instagram", "YouTube", "Browser"]
        },
        nodeDetails: {
          markets: {
            title: "Markets and urgency content",
            summary:
              "This cluster grew strongest at night and often preceded rapid app switching, especially from Instagram reels into YouTube commentary.",
            trend: "Growing after 8 PM, especially on Tuesday and Thursday.",
            relatedCreators: ["Macro clips", "Trading personalities", "Business commentary"],
            relatedEmotions: ["Urgency", "Comparison", "Restlessness"],
            relatedGoals: ["Build intelligently", "Preserve calm"],
            relatedInsightId: "insight-week-urgency",
            relatedSequenceId: "sequence-week-evening-loop"
          },
          status: {
            title: "Status and self-measurement",
            summary:
              "This cluster appears when learning slides into comparison-heavy business and productivity content.",
            trend: "Most active immediately after market commentary and entrepreneurship clips.",
            relatedCreators: ["Productivity influencers", "Founder diaries", "Career advice"],
            relatedEmotions: ["Comparison", "Pressure", "Insufficiency"],
            relatedGoals: ["Depth over novelty", "Build without panic"],
            relatedInsightId: "insight-week-drift",
            relatedSequenceId: "sequence-week-course-loop"
          },
          relationships: {
            title: "Relationships and self-worth",
            summary:
              "Advice content and social interpretation loops tended to increase rumination and late-night checking behavior.",
            trend: "Peaks late at night and after ambiguous social interactions.",
            relatedCreators: ["Relationship advice channels", "Self-worth clips"],
            relatedEmotions: ["Rumination", "Hope", "Evaluation"],
            relatedGoals: ["Real conversation", "Calm evenings"],
            relatedInsightId: "insight-week-drift",
            relatedSequenceId: "sequence-week-relationship-loop"
          },
          reading: {
            title: "Reading and deliberate thought",
            summary:
              "This cluster is calmer, slower, and more aligned with the user's stated identity. It often begins in the morning and protects the rest of the day.",
            trend: "Stable but under-defended in the evening.",
            relatedCreators: ["Essayists", "Book summaries", "Saved reading queue"],
            relatedEmotions: ["Calm", "Coherence", "Focus"],
            relatedGoals: ["Reading", "Deep work"],
            relatedInsightId: "insight-week-reading",
            relatedSequenceId: "sequence-week-reading-window"
          },
          selfworth: {
            title: "Self-worth pressure",
            summary:
              "This cluster sits between ambition and emotion. It is where many high-friction sessions begin, especially after relationship or status content.",
            trend: "Most visible after short-form business and advice videos stack together.",
            relatedCreators: ["Self-improvement clips", "Status commentary"],
            relatedEmotions: ["Pressure", "Need to catch up", "Evaluation"],
            relatedGoals: ["Self-authorship", "Peace"],
            relatedInsightId: "insight-week-drift",
            relatedSequenceId: "sequence-week-course-loop"
          },
          physics: {
            title: "Physics and pure curiosity",
            summary:
              "This is a strong candidate for the product's protect-this feature because it creates depth without the same emotional drag.",
            trend: "Small cluster, high value, best after 7 AM and before evening feed use.",
            relatedCreators: ["Physics explainers", "Science essays", "Lecture clips"],
            relatedEmotions: ["Curiosity", "Wonder", "Steady focus"],
            relatedGoals: ["Learning", "Depth"],
            relatedInsightId: "insight-week-reading",
            relatedSequenceId: "sequence-week-reading-window"
          },
          philosophy: {
            title: "Philosophy and reflection",
            summary:
              "This cluster is quieter than physics but supports the same deeper identity of deliberate thought.",
            trend: "Appears after reading and journaling, not after reels.",
            relatedCreators: ["Philosophy talks", "Essay channels", "Long-form podcasts"],
            relatedEmotions: ["Reflection", "Calm", "Meaning"],
            relatedGoals: ["Thinking clearly", "Self-knowledge"],
            relatedInsightId: "insight-week-reading",
            relatedSequenceId: "sequence-week-reading-window"
          }
        }
      },
      month: {
        defaultNodeId: "status",
        comparison:
          "Compared with the prior month, the status cluster weakened slightly while physics and reading regained attention share.",
        filters: {
          goals: ["All", "Reading", "Physics", "Relationships"],
          tones: ["All", "Calming", "Pressurizing", "Curious"],
          sources: ["All", "YouTube", "Browser", "Manual reflection"]
        },
        nodeDetails: {
          markets: {
            title: "Markets and urgency content",
            summary:
              "Still prominent, but less dominant than the prior month once reading streaks stabilized.",
            trend: "Present most weeknights, but no longer totalizing.",
            relatedCreators: ["Market commentary", "Startup clips", "Finance newsletters"],
            relatedEmotions: ["Urgency", "Ambition", "Comparison"],
            relatedGoals: ["Build intelligently", "Stay informed"],
            relatedInsightId: "insight-month-status",
            relatedSequenceId: "sequence-month-status-loop"
          },
          status: {
            title: "Status and self-measurement",
            summary:
              "The month-level pattern shows business ambition and self-worth pressure appearing in the same attention corridor.",
            trend: "Most active on nights with course browsing or career-content runs.",
            relatedCreators: ["Founder content", "Productivity channels", "Status-oriented self-improvement"],
            relatedEmotions: ["Pressure", "Comparison", "Drive"],
            relatedGoals: ["Make progress", "Do not spiral"],
            relatedInsightId: "insight-month-status",
            relatedSequenceId: "sequence-month-status-loop"
          },
          relationships: {
            title: "Relationships and self-worth",
            summary:
              "This cluster rose during emotionally uncertain weeks and often pulled attention out of chosen learning plans.",
            trend: "Sharp spikes, then drop-offs.",
            relatedCreators: ["Advice channels", "Attachment content", "Interpretation clips"],
            relatedEmotions: ["Rumination", "Hope", "Sensitivity"],
            relatedGoals: ["Real connection", "Emotional steadiness"],
            relatedInsightId: "insight-month-status",
            relatedSequenceId: "sequence-month-relationship-loop"
          },
          reading: {
            title: "Reading and deliberate thought",
            summary:
              "Reading became the strongest stabilizer when it happened before apps rather than after them.",
            trend: "Up month over month.",
            relatedCreators: ["Saved essays", "Reading lists", "Notes"],
            relatedEmotions: ["Calm", "Depth", "Coherence"],
            relatedGoals: ["Reading", "Deep work"],
            relatedInsightId: "insight-month-physics",
            relatedSequenceId: "sequence-month-reading-streak"
          },
          selfworth: {
            title: "Self-worth pressure",
            summary:
              "A recurring emotional bridge between relationship content and ambition-heavy business content.",
            trend: "Less time-heavy than markets, but more emotionally sticky.",
            relatedCreators: ["Advice clips", "Status narratives", "Self-improvement content"],
            relatedEmotions: ["Self-evaluation", "Pressure", "Unease"],
            relatedGoals: ["Peace", "Self-authorship"],
            relatedInsightId: "insight-month-status",
            relatedSequenceId: "sequence-month-status-loop"
          },
          physics: {
            title: "Physics and curiosity return",
            summary:
              "Physics became a visible counterweight to comparison-heavy content and created longer, cleaner sessions.",
            trend: "Up significantly vs prior month.",
            relatedCreators: ["Physics explainers", "Science lectures", "Curiosity channels"],
            relatedEmotions: ["Wonder", "Interest", "Depth"],
            relatedGoals: ["Learning", "Curiosity"],
            relatedInsightId: "insight-month-physics",
            relatedSequenceId: "sequence-month-reading-streak"
          },
          philosophy: {
            title: "Philosophy and reflection",
            summary:
              "This cluster began to link learning content with questions about identity, peace, and long-term direction.",
            trend: "Gradually emerging, especially on weekends.",
            relatedCreators: ["Essay podcasts", "Philosophy channels", "Long-form notes"],
            relatedEmotions: ["Reflection", "Perspective", "Calm"],
            relatedGoals: ["Meaning", "Clear thinking"],
            relatedInsightId: "insight-month-oscillation",
            relatedSequenceId: "sequence-month-reading-streak"
          }
        }
      },
      quarter: {
        defaultNodeId: "philosophy",
        comparison:
          "Compared with the previous quarter, reflective clusters hold more ground, but business pressure still acts as a recurring gateway to reactivity.",
        filters: {
          goals: ["All", "Identity", "Learning", "Relationships"],
          tones: ["All", "Calming", "Pressurizing", "Curious"],
          sources: ["All", "Browser", "Import", "Manual reflection"]
        },
        nodeDetails: {
          markets: {
            title: "Markets and urgency content",
            summary:
              "At the quarter level this looks less like a core identity and more like a strong gravitational field that periodically pulls attention off course.",
            trend: "Still sticky, but no longer defines the whole graph.",
            relatedCreators: ["Market clips", "Business channels", "Founder strategy feeds"],
            relatedEmotions: ["Drive", "Urgency", "Restlessness"],
            relatedGoals: ["Build well", "Stay informed"],
            relatedInsightId: "insight-quarter-pressure",
            relatedSequenceId: "sequence-quarter-business-pressure"
          },
          status: {
            title: "Status and self-measurement",
            summary:
              "Over the quarter, status pressure became the repeatable translation layer between ambition and anxiety.",
            trend: "Persistent across many content categories.",
            relatedCreators: ["Founder diaries", "Status-coded productivity", "Career commentary"],
            relatedEmotions: ["Comparison", "Insufficiency", "Drive"],
            relatedGoals: ["Progress", "Self-direction"],
            relatedInsightId: "insight-quarter-pressure",
            relatedSequenceId: "sequence-quarter-business-pressure"
          },
          relationships: {
            title: "Relationships and self-worth",
            summary:
              "Relationship content contributed meaningfully to self-story and emotional interpretation, not just time spent.",
            trend: "Episodic but intense.",
            relatedCreators: ["Relationship experts", "Attachment clips", "Interpretive content"],
            relatedEmotions: ["Hope", "Rumination", "Sensitivity"],
            relatedGoals: ["Connection", "Calm"],
            relatedInsightId: "insight-quarter-orientation",
            relatedSequenceId: "sequence-quarter-relationship-wave"
          },
          reading: {
            title: "Reading and deliberate thought",
            summary:
              "Reading became a persistent anchor and one of the clearest signs of the attention identity the user actually wants.",
            trend: "Steady rise quarter over quarter.",
            relatedCreators: ["Books", "Long-form essays", "Notes"],
            relatedEmotions: ["Calm", "Perspective", "Depth"],
            relatedGoals: ["Reading", "Thinking clearly"],
            relatedInsightId: "insight-quarter-philosophy",
            relatedSequenceId: "sequence-quarter-reflection-arc"
          },
          selfworth: {
            title: "Ambition and self-worth pressure",
            summary:
              "This cluster became one of the most important explanatory nodes in the whole graph because it linked many seemingly unrelated attention loops.",
            trend: "Consistently high emotional influence.",
            relatedCreators: ["Status narratives", "Self-improvement", "Performance content"],
            relatedEmotions: ["Pressure", "Evaluation", "Need to catch up"],
            relatedGoals: ["Peace", "Self-authorship"],
            relatedInsightId: "insight-quarter-pressure",
            relatedSequenceId: "sequence-quarter-business-pressure"
          },
          physics: {
            title: "Scientific curiosity",
            summary:
              "This cluster is one of the clearest signals of authentic interest and the easiest candidate for a protected attention lane.",
            trend: "Growing slowly but with high quality.",
            relatedCreators: ["Physics explainers", "Science documentaries", "Lecture clips"],
            relatedEmotions: ["Wonder", "Curiosity", "Steady focus"],
            relatedGoals: ["Learning", "Depth"],
            relatedInsightId: "insight-quarter-orientation",
            relatedSequenceId: "sequence-quarter-reflection-arc"
          },
          philosophy: {
            title: "Philosophy and self-reflection",
            summary:
              "This cluster helped consolidate identity-level thinking and created the calmest, most coherent sessions across the quarter.",
            trend: "Up meaningfully versus the prior quarter.",
            relatedCreators: ["Philosophy essays", "Long-form interviews", "Reflective podcasts"],
            relatedEmotions: ["Perspective", "Calm", "Meaning"],
            relatedGoals: ["Self-knowledge", "Orientation"],
            relatedInsightId: "insight-quarter-philosophy",
            relatedSequenceId: "sequence-quarter-reflection-arc"
          }
        }
      }
    }
  },
  timeline: {
    week: {
      defaultSequenceId: "sequence-week-evening-loop",
      sequences: [
        {
          id: "sequence-week-evening-loop",
          label: "Evening urgency loop",
          timeLabel: "Tuesday 7:40 PM",
          summary: "A harmless open turns into a three-app urgency chain.",
          clusterNodeId: "markets",
          switchBurst: "3 apps in 12 minutes",
          emotionalShift: "Curiosity -> comparison -> urgency",
          breakPoint: "After the second market commentary clip, the user had enough signal to stop but did not get a friction prompt.",
          opportunities: [
            "Ask for intention when Instagram opens after 9 PM.",
            "Offer a saved physics queue after 25 minutes of reels.",
            "Detect three fast switches as a calm-reset trigger."
          ],
          steps: [
            {
              time: "7:40 PM",
              title: "Opened Instagram to check one message",
              detail: "The session began with a stated intention unrelated to browsing.",
              whyItMatters: "This is the point where a narrow intention became an open feed state.",
              observedVsInferred: "observed"
            },
            {
              time: "7:46 PM",
              title: "Short-form trading clips stacked quickly",
              detail: "Multiple clips with similar status and urgency tone appeared in sequence.",
              whyItMatters: "The content theme intensified the emotional frame of needing to catch up.",
              observedVsInferred: "inferred"
            },
            {
              time: "8:05 PM",
              title: "Switched into YouTube commentary",
              detail: "The content remained adjacent in theme but became slightly more long-form and persuasive.",
              whyItMatters: "The platform changed but the rabbit hole did not.",
              observedVsInferred: "observed"
            },
            {
              time: "8:44 PM",
              title: "Moved to Safari course pages and tools",
              detail: "Browsing adopted the same emotional register: optimize, catch up, get ahead.",
              whyItMatters: "The loop migrated into action-looking behavior without resolving the underlying pressure.",
              observedVsInferred: "inferred"
            }
          ]
        },
        {
          id: "sequence-week-relationship-loop",
          label: "Relationship interpretation spiral",
          timeLabel: "Thursday 10:12 PM",
          summary: "Advice content pulled the evening into interpretive rumination.",
          clusterNodeId: "relationships",
          switchBurst: "2 apps in 9 minutes",
          emotionalShift: "Concern -> hope -> rumination",
          breakPoint: "The second advice clip turned the session from curiosity into self-interpretation.",
          opportunities: [
            "Offer a mood check-in after repeated relationship advice views.",
            "Suggest journaling rather than continuing to scroll."
          ],
          steps: [
            {
              time: "10:12 PM",
              title: "Opened Instagram after a social interaction",
              detail: "The opening context mattered even before content appeared.",
              whyItMatters: "Attention state is shaped by both content and pre-existing emotional context.",
              observedVsInferred: "inferred"
            },
            {
              time: "10:15 PM",
              title: "Viewed multiple relationship advice clips",
              detail: "The clips shared a similar frame of decoding hidden meaning in behavior.",
              whyItMatters: "These clips amplified interpretive thinking rather than settling it.",
              observedVsInferred: "observed"
            },
            {
              time: "10:23 PM",
              title: "Switched to search and private browsing",
              detail: "The user moved from passive intake into active searching for certainty.",
              whyItMatters: "This is the clearest breakpoint for an intervention that restores perspective.",
              observedVsInferred: "observed"
            }
          ]
        },
        {
          id: "sequence-week-reading-window",
          label: "Morning reading protection window",
          timeLabel: "Saturday 8:10 AM",
          summary: "A calm session shows what protected attention looks like when the feed does not lead.",
          clusterNodeId: "reading",
          switchBurst: "0 switches for 41 minutes",
          emotionalShift: "Calm -> curiosity -> coherence",
          breakPoint: "No obvious break required; the sequence is valuable as a positive pattern to preserve.",
          opportunities: [
            "Turn this into a reusable morning ritual.",
            "Recommend similar content during late-night substitution moments."
          ],
          steps: [
            {
              time: "8:10 AM",
              title: "Opened saved essay instead of social apps",
              detail: "The session started with a deliberate choice rather than a feed opening.",
              whyItMatters: "This is the structural opposite of the rabbit hole.",
              observedVsInferred: "observed"
            },
            {
              time: "8:34 AM",
              title: "Saved two related physics resources",
              detail: "Curiosity deepened rather than fragmenting.",
              whyItMatters: "The system should learn from positive sequences, not just harmful ones.",
              observedVsInferred: "observed"
            }
          ]
        }
      ]
    },
    month: {
      defaultSequenceId: "sequence-month-reading-streak",
      sequences: [
        {
          id: "sequence-month-reading-streak",
          label: "Reading streak arc",
          timeLabel: "Week 1 to Week 4",
          summary: "Mornings stayed more coherent when feeds were delayed until after reading.",
          clusterNodeId: "reading",
          switchBurst: "Low switching across 11 sessions",
          emotionalShift: "Calm -> depth -> momentum",
          breakPoint: "The sequence usually broke only when an app was opened before reading.",
          opportunities: [
            "Protect morning depth with a no-feed-first rule.",
            "Promote reading and physics as the default substitute pair."
          ],
          steps: [
            {
              time: "Week 1",
              title: "Reading before feeds",
              detail: "Long-form sessions started the day before social input.",
              whyItMatters: "The ordering of inputs mattered more than total time alone.",
              observedVsInferred: "observed"
            },
            {
              time: "Week 3",
              title: "Reading linked to physics curiosity",
              detail: "The reading cluster started branching into deeper educational interests.",
              whyItMatters: "The graph should capture when one protected topic generates another.",
              observedVsInferred: "inferred"
            }
          ]
        },
        {
          id: "sequence-month-status-loop",
          label: "Status spiral",
          timeLabel: "Recurring weekday evenings",
          summary: "Business content, self-improvement, and course browsing converged into pressure-heavy attention states.",
          clusterNodeId: "status",
          switchBurst: "4 sources across repeated evenings",
          emotionalShift: "Ambition -> comparison -> insufficiency",
          breakPoint: "The second or third content handoff usually moved the session from useful ambition into self-measurement.",
          opportunities: [
            "Detect repeated business-to-status sequences as a pendulum alert.",
            "Offer a calmer substitution before browser searching begins."
          ],
          steps: [
            {
              time: "Evening start",
              title: "Opened business or founder content",
              detail: "The session often started with a legitimate learning motive.",
              whyItMatters: "Not all ambition content is harmful; the problem is when it tips into comparison.",
              observedVsInferred: "observed"
            },
            {
              time: "Mid sequence",
              title: "Shifted into status-coded advice",
              detail: "Content emphasized catching up, winning, or measuring worth by progress.",
              whyItMatters: "This is the emotional pivot point.",
              observedVsInferred: "inferred"
            }
          ]
        },
        {
          id: "sequence-month-relationship-loop",
          label: "Relationship advice wave",
          timeLabel: "Week 3",
          summary: "Advice-heavy content created more emotional stickiness than time volume alone would suggest.",
          clusterNodeId: "relationships",
          switchBurst: "Low switching, high emotional persistence",
          emotionalShift: "Sensitivity -> analysis -> rumination",
          breakPoint: "The loop becomes unhelpful when interpretation replaces action or direct communication.",
          opportunities: [
            "Offer journaling instead of another advice clip.",
            "Ask if the user wants clarity or just more analysis."
          ],
          steps: [
            {
              time: "Entry",
              title: "Relationship content cluster appears",
              detail: "The advice content is emotionally sticky even without heavy time usage.",
              whyItMatters: "Attention management must care about intensity, not just minutes.",
              observedVsInferred: "inferred"
            }
          ]
        }
      ]
    },
    quarter: {
      defaultSequenceId: "sequence-quarter-reflection-arc",
      sequences: [
        {
          id: "sequence-quarter-reflection-arc",
          label: "Reflection arc",
          timeLabel: "Month 2 to Month 3",
          summary: "Reading, philosophy, and science clusters began to reinforce one another over time.",
          clusterNodeId: "philosophy",
          switchBurst: "Low switching, high coherence",
          emotionalShift: "Curiosity -> meaning -> steadiness",
          breakPoint: "The main risk is simply not defending this lane strongly enough.",
          opportunities: [
            "Make protected clusters easier to reach than default feeds.",
            "Turn identity-building topics into explicit attention lanes."
          ],
          steps: [
            {
              time: "Month 2",
              title: "Philosophy and essay content rises",
              detail: "Reflective long-form content becomes a recurring part of the graph.",
              whyItMatters: "This is the strongest sign that the product should model identity, not just behavior.",
              observedVsInferred: "observed"
            },
            {
              time: "Month 3",
              title: "Physics and reflection begin to cross-link",
              detail: "The user's curiosity and self-reflection stop appearing as isolated islands.",
              whyItMatters: "The knowledge graph becomes meaningful when themes reinforce each other.",
              observedVsInferred: "inferred"
            }
          ]
        },
        {
          id: "sequence-quarter-business-pressure",
          label: "Business pressure chain",
          timeLabel: "Repeated across the quarter",
          summary: "Ambition-heavy content frequently translated into self-worth pressure rather than calm action.",
          clusterNodeId: "selfworth",
          switchBurst: "Moderate switching with high emotional residue",
          emotionalShift: "Motivation -> pressure -> anxiety",
          breakPoint: "The chain becomes corrosive when progress content becomes identity evaluation.",
          opportunities: [
            "Build a specific calm intervention for ambition-to-pressure sequences.",
            "Prompt the user to define what 'enough for tonight' looks like."
          ],
          steps: [
            {
              time: "Entry",
              title: "Business content opens with useful ambition",
              detail: "The initial motive is often legitimate and growth-oriented.",
              whyItMatters: "The product must avoid demonizing ambition.",
              observedVsInferred: "observed"
            },
            {
              time: "Later",
              title: "Attention migrates into self-worth pressure",
              detail: "The session becomes about evaluation, catching up, or proving value.",
              whyItMatters: "This is where the cluster becomes costly.",
              observedVsInferred: "inferred"
            }
          ]
        },
        {
          id: "sequence-quarter-relationship-wave",
          label: "Relationship wave",
          timeLabel: "Intermittent emotional windows",
          summary: "Relationship content mattered less by volume than by emotional leverage.",
          clusterNodeId: "relationships",
          switchBurst: "Short sessions, high persistence after closing apps",
          emotionalShift: "Hope -> uncertainty -> interpretation",
          breakPoint: "The unhelpful turn happens when content replaces real-world action or reflection.",
          opportunities: [
            "Suggest writing or actual outreach instead of another clip.",
            "Label this as high-leverage, not high-volume, content."
          ],
          steps: [
            {
              time: "Pattern",
              title: "Relationship advice spikes episodically",
              detail: "The graph shows relationship content as emotionally potent even when total minutes are modest.",
              whyItMatters: "The system needs to surface emotional leverage, not just time totals.",
              observedVsInferred: "inferred"
            }
          ]
        }
      ]
    }
  },
  drift: {
    week: {
      scoreLabel: "31% drift",
      scoreValue: 58,
      summary:
        "Your stated priority was reading and building. Most evening attention still went to reactive content, especially once a social feed opened after 9 PM.",
      goals: [
        { id: "goal-reading", label: "Reading", intended: 76, actual: 38, status: "underfed", note: "Protected in mornings, not defended at night." },
        { id: "goal-deep-work", label: "Deep Work", intended: 68, actual: 44, status: "underfed", note: "Often displaced by urgency content after dinner." },
        { id: "goal-short-form", label: "Short-form video", intended: 18, actual: 72, status: "overfed", note: "Reactivity clustered late in the day." }
      ],
      neglectedIntentions: ["Quiet reading", "Friend conversations", "Physics curiosity"],
      displacementClusters: [
        {
          id: "cluster-week-trading",
          label: "Trading urgency cluster",
          summary: "Market commentary and status-coded productivity repeatedly displaced planned depth."
        },
        {
          id: "cluster-week-relationship",
          label: "Relationship interpretation loop",
          summary: "Advice content shifted evenings into rumination more often than expected."
        }
      ]
    },
    month: {
      scoreLabel: "24% drift",
      scoreValue: 46,
      summary:
        "At the month level you are closer to your stated values, but evenings still undercut the identity you are trying to build.",
      goals: [
        { id: "goal-reading", label: "Reading", intended: 74, actual: 51, status: "underfed", note: "Trend is improving, but not yet defended in evening hours." },
        { id: "goal-learning", label: "Curiosity learning", intended: 71, actual: 56, status: "underfed", note: "Physics is returning, which is a strong sign." },
        { id: "goal-status", label: "Status-heavy content", intended: 20, actual: 49, status: "overfed", note: "This is the main month-level tension." }
      ],
      neglectedIntentions: ["Single-theme evenings", "Unstructured silence", "Consistent offline reset"],
      displacementClusters: [
        {
          id: "cluster-month-status",
          label: "Status spiral",
          summary: "Business content repeatedly crossed the line from motivation into self-measurement."
        },
        {
          id: "cluster-month-advice",
          label: "Advice saturation",
          summary: "Relationship and self-improvement content carried more emotional weight than expected."
        }
      ]
    },
    quarter: {
      scoreLabel: "17% drift",
      scoreValue: 34,
      summary:
        "At the long horizon, the user is not lost. The main problem is that daily loops still erode the attention identity they are trying to build.",
      goals: [
        { id: "goal-identity", label: "Identity-building learning", intended: 82, actual: 63, status: "underfed", note: "Directionally strong, but still vulnerable to feed gravity." },
        { id: "goal-relationships", label: "Real relationships", intended: 64, actual: 52, status: "balanced", note: "The issue is interpretation-heavy content, not relationships themselves." },
        { id: "goal-reactive", label: "Reactive feed time", intended: 15, actual: 33, status: "overfed", note: "Smaller than before, but still enough to undermine the week." }
      ],
      neglectedIntentions: ["Protected curiosity lanes", "Philosophy sessions", "Phone-free transitions"],
      displacementClusters: [
        {
          id: "cluster-quarter-pressure",
          label: "Ambition to anxiety chain",
          summary: "The quarter-level graph shows a repeatable path from ambition content into self-worth pressure."
        },
        {
          id: "cluster-quarter-fragmentation",
          label: "Fragmented evening openings",
          summary: "Many of the worst loops began with a narrow intention that turned into an open feed state."
        }
      ]
    }
  },
  interventions: {
    week: [
      {
        id: "rule-week-intention",
        title: "Late-night intention prompt",
        category: "prompt",
        description: "Ask what the user came for when a high-risk app opens after 9 PM.",
        trigger: "Instagram or YouTube opens after 9 PM",
        action: "Show a calm prompt with options: Messages Only, Saved Reading, Leave App",
        targetPatterns: ["Late-night switching spike", "Evening urgency loop"],
        preview: "Tue 9:18 PM -> the prompt appears before reels stack into market commentary.",
        defaultEnabled: true,
        tone: "soft"
      },
      {
        id: "rule-week-substitute",
        title: "Swap short-form with saved essays",
        category: "substitution",
        description: "Offer one better next action before a session becomes punitive or shame-laden.",
        trigger: "25 minutes of short-form video in one evening",
        action: "Offer three saved articles or physics videos instead of a hard block",
        targetPatterns: ["Trading urgency cluster", "Late-night scrolling"],
        preview: "Thu 9:47 PM -> the system surfaces three saved long-form options.",
        defaultEnabled: true,
        tone: "soft"
      },
      {
        id: "rule-week-reset",
        title: "90-second nervous-system reset",
        category: "quiet_window",
        description: "Interrupt frantic switching with a brief reset rather than a punishment.",
        trigger: "3 or more app switches in under 4 minutes during evening hours",
        action: "Trigger a 90-second breathing and orientation break",
        targetPatterns: ["Evening urgency loop", "Fast switching"],
        preview: "Tue 8:06 PM -> after the third switch, the reset appears.",
        defaultEnabled: false,
        tone: "alert"
      },
      {
        id: "rule-week-protect",
        title: "Protect physics lane",
        category: "protect_rule",
        description: "Make the most constructive curiosity cluster easier to access than the feed.",
        trigger: "User opens the app from the home screen at night",
        action: "Surface saved physics and long-form learning content first",
        targetPatterns: ["Neglected curiosity", "Reactive feed default"],
        preview: "Any night opening -> protected queue appears before social options.",
        defaultEnabled: false,
        tone: "soft"
      }
    ],
    month: [
      {
        id: "rule-month-theme",
        title: "Evening theme lock",
        category: "prompt",
        description: "Declare one theme for the evening so attention has a visible container.",
        trigger: "Start of evening attention block",
        action: "Select one theme: Reading, Physics, Friends, Recovery",
        targetPatterns: ["Oscillation", "Status spiral"],
        preview: "Mon 7:00 PM -> user picks Reading and the product biases suggestions around it.",
        defaultEnabled: true,
        tone: "soft"
      },
      {
        id: "rule-month-pendulum",
        title: "Pendulum alert",
        category: "prompt",
        description: "Call out when attention swings from a chosen goal into a recurring urgency cluster.",
        trigger: "Detected transition from reading or learning into status-heavy short-form content",
        action: "Show a simple note: Your attention just swung from depth into pressure",
        targetPatterns: ["Status spiral", "Pendulum swing"],
        preview: "Wed 8:32 PM -> calm learning turns into comparison-heavy founder content.",
        defaultEnabled: true,
        tone: "alert"
      },
      {
        id: "rule-month-checkin",
        title: "Relationship content check-in",
        category: "substitution",
        description: "Help the user decide whether they want clarity, comfort, or more content.",
        trigger: "Repeated relationship advice views in a single session",
        action: "Offer three choices: Journal, Text someone, Continue",
        targetPatterns: ["Advice saturation", "Rumination"],
        preview: "Week 3 late evening -> the product asks what the user actually needs.",
        defaultEnabled: false,
        tone: "soft"
      },
      {
        id: "rule-month-reading",
        title: "No-feed-first morning rule",
        category: "protect_rule",
        description: "Protect the clearest positive pattern in the month-level graph.",
        trigger: "Morning device opening",
        action: "Delay social apps until after reading or note review",
        targetPatterns: ["Reading streak protection", "Morning coherence"],
        preview: "Sat 8:00 AM -> saved reading queue opens first.",
        defaultEnabled: true,
        tone: "soft"
      }
    ],
    quarter: [
      {
        id: "rule-quarter-memo",
        title: "Quarterly attention memo",
        category: "prompt",
        description: "Generate a narrative about who the user is becoming, not just what they clicked.",
        trigger: "End of quarter reflection",
        action: "Produce a long-form founder-style memo with key clusters and tensions",
        targetPatterns: ["Identity drift", "Long-horizon coherence"],
        preview: "Quarter review -> the memo highlights orientation rather than productivity scorekeeping.",
        defaultEnabled: true,
        tone: "soft"
      },
      {
        id: "rule-quarter-lanes",
        title: "Protected attention lanes",
        category: "protect_rule",
        description: "Give protected clusters easier access than reactive defaults.",
        trigger: "User chooses a protected lane",
        action: "Surface saved content and rituals for philosophy, physics, or reading",
        targetPatterns: ["Identity-building learning", "Neglected curiosity"],
        preview: "Evening opening -> the chosen lane becomes the product's default surface.",
        defaultEnabled: true,
        tone: "soft"
      },
      {
        id: "rule-quarter-pressure",
        title: "Ambition-to-pressure warning",
        category: "quiet_window",
        description: "Flag the recurring emotional corridor from useful ambition into self-worth pressure.",
        trigger: "Business content followed by status and self-measurement cues",
        action: "Offer a stop point with a short question: Is this still helping?",
        targetPatterns: ["Ambition to anxiety chain"],
        preview: "Business content shifts into status comparison -> warning appears.",
        defaultEnabled: false,
        tone: "alert"
      },
      {
        id: "rule-quarter-archive",
        title: "Autopilot episode archive",
        category: "substitution",
        description: "Store repeated derailment sequences so the user can study them without shame.",
        trigger: "Repeated high-friction sequence detected across weeks",
        action: "Save as an episode in the replay archive",
        targetPatterns: ["Fragmented evening openings", "Recurring loops"],
        preview: "The product notices the same evening sequence for the third time and archives it.",
        defaultEnabled: true,
        tone: "soft"
      }
    ]
  },
  privacy: {
    storageModel: "Local-first encrypted vault",
    syncPolicy: "No account required for the prototype; any future sync should be end-to-end encrypted.",
    directObservability: [
      "app duration and category usage where OS APIs permit it",
      "browser URLs, page titles, time-on-page, and optional text snippets",
      "imported watch or read history archives",
      "manual reflections, goals, notes, and corrections"
    ],
    moderateInference: [
      "topic clusters and creator clusters",
      "goal alignment scores",
      "likely rabbit-hole sequences",
      "cluster-level emotional tone labels"
    ],
    speculative: [
      "precise emotional state from content traces alone",
      "guaranteed retraining of third-party recommendation systems",
      "clinical conclusions about depression, anxiety, ADHD, trauma, or addiction"
    ],
    controls: [
      "delete local vault",
      "export data as JSON or Markdown",
      "disconnect a source",
      "edit or correct inferred labels",
      "hide a cluster from future summaries"
    ]
  },
  lineage: {
    "insight-week-urgency": {
      insightId: "insight-week-urgency",
      observedFacts: [
        "Evening sessions showed repeated transitions from Instagram to YouTube to browser tabs.",
        "Market commentary and business clips were among the most frequent short-form categories in the week.",
        "The user reported wanting more reading and less reactive content."
      ],
      inferences: [
        "The dominant evening content cluster trained urgency more than depth.",
        "This cluster likely contributed to a feeling of catch-up pressure."
      ],
      confidenceReason:
        "Medium confidence because the sequence pattern is clear, but the emotional interpretation remains a model rather than a direct measurement.",
      userEditableFields: ["cluster label", "emotional tone", "whether this insight feels accurate"]
    },
    "insight-week-reading": {
      insightId: "insight-week-reading",
      observedFacts: [
        "Morning reading sessions were the longest uninterrupted sessions of the week.",
        "These sessions produced the lowest app-switching rates."
      ],
      inferences: [
        "Reading functions as a stabilizing attention anchor."
      ],
      confidenceReason:
        "High confidence because this insight depends mostly on observed session length and switching behavior.",
      userEditableFields: ["cluster importance", "protect-next priority"]
    },
    "insight-week-drift": {
      insightId: "insight-week-drift",
      observedFacts: [
        "The user's stated intention emphasized reading and building.",
        "Late-night short-form and commentary content occupied a larger share of evening attention than reading."
      ],
      inferences: [
        "The user's evenings drifted away from the identity they were trying to reinforce."
      ],
      confidenceReason:
        "Medium confidence because the behavior gap is observable, but the meaning of that gap is interpretive.",
      userEditableFields: ["goal weights", "drift interpretation"]
    },
    "insight-month-oscillation": {
      insightId: "insight-month-oscillation",
      observedFacts: [
        "The month showed repeated alternation between long-form reading phases and novelty-heavy evenings.",
        "The learning cluster strengthened after mornings stayed feed-free."
      ],
      inferences: [
        "The month-level pattern is better described as oscillation than as simple overuse."
      ],
      confidenceReason:
        "Medium confidence because the alternating pattern is clear, though its interpretation as oscillation is a narrative synthesis.",
      userEditableFields: ["pattern label", "time boundaries"]
    },
    "insight-month-physics": {
      insightId: "insight-month-physics",
      observedFacts: [
        "Physics content correlated with longer sessions and fewer switches.",
        "The user repeatedly saved or returned to physics resources."
      ],
      inferences: [
        "Physics is a high-signal cluster worth protecting."
      ],
      confidenceReason:
        "High confidence because the pattern rests on repeatable session-level observations and the user's own stated interest.",
      userEditableFields: ["protect status", "cluster priority"]
    },
    "insight-month-status": {
      insightId: "insight-month-status",
      observedFacts: [
        "Business and self-improvement content often appeared in the same evening sessions.",
        "Relationship and self-worth notes were common after those sessions."
      ],
      inferences: [
        "Status-heavy content blends ambition with self-evaluation."
      ],
      confidenceReason:
        "Medium confidence because the cluster is clear, but the label 'status-heavy' remains interpretive.",
      userEditableFields: ["cluster label", "relationship to goals"]
    },
    "insight-quarter-orientation": {
      insightId: "insight-quarter-orientation",
      observedFacts: [
        "Across 90 days, the dominant recurring themes were ambition, relationships, reflection, and scientific curiosity.",
        "These themes persisted across multiple sources and time ranges."
      ],
      inferences: [
        "The long-horizon pattern is a search for orientation rather than random drift."
      ],
      confidenceReason:
        "Medium confidence because the thematic repetition is observable, while the identity-level reading is interpretive.",
      userEditableFields: ["theme labels", "narrative interpretation"]
    },
    "insight-quarter-philosophy": {
      insightId: "insight-quarter-philosophy",
      observedFacts: [
        "Philosophy and reflective long-form content increased over the quarter.",
        "Those sessions correlated with lower switching and calmer notes."
      ],
      inferences: [
        "Reflective content helps consolidate a calmer attention identity."
      ],
      confidenceReason:
        "High confidence for the observed shift, medium confidence for the identity conclusion.",
      userEditableFields: ["cluster importance", "protect-next framing"]
    },
    "insight-quarter-pressure": {
      insightId: "insight-quarter-pressure",
      observedFacts: [
        "Business content frequently preceded self-evaluative notes and late-night searching.",
        "The self-worth node repeatedly connected ambition content to higher-friction sessions."
      ],
      inferences: [
        "Ambition remains the main gateway into self-worth pressure."
      ],
      confidenceReason:
        "Medium confidence because the sequence is recurring, but pressure remains a modeled emotional category.",
      userEditableFields: ["pressure label", "sensitivity level"]
    }
  }
};
