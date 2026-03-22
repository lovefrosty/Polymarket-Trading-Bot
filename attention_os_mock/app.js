(function () {
  const appData = window.AttentionOSData;
  const page = document.body.dataset.page || "home";
  const pageMount = document.getElementById("pageMount");
  const sidebar = document.getElementById("appSidebar");
  const topbar = document.getElementById("appTopbar");
  const dialog = document.getElementById("lineageDialog");
  const dialogTitle = document.getElementById("dialogTitle");
  const dialogBody = document.getElementById("dialogBody");
  const dialogClose = document.getElementById("dialogClose");
  const toast = document.getElementById("toast");

  const STORAGE_KEYS = {
    range: "attention-os-range",
    graphNode: "attention-os-graph-node",
    sequence: "attention-os-sequence",
    pinnedGoal: "attention-os-pinned-goal"
  };

  const ROUTES = {
    home: {
      href: "./index.html",
      label: "Weekly Reflection",
      navLabel: "Reflection",
      title: (range) => range.headline,
      subtitle:
        "Start with the story of the week, then inspect what reinforced it and what deserves protection next."
    },
    graph: {
      href: "./graph.html",
      label: "Attention Map",
      navLabel: "Map",
      title: (range) => `Map the clusters shaping your ${range.label.toLowerCase()}.`,
      subtitle:
        "See the personal graph of topics, emotions, creators, and goals that are competing for your attention."
    },
    timeline: {
      href: "./timeline.html",
      label: "Rabbit-Hole Replay",
      navLabel: "Replay",
      title: (range) => `Replay the sequences that captured your ${range.label.toLowerCase()}.`,
      subtitle:
        "Study how a harmless opening became a whole sequence so intervention points become visible."
    },
    drift: {
      href: "./drift.html",
      label: "Drift View",
      navLabel: "Drift",
      title: (range) => `Compare intention and behavior across the ${range.label.toLowerCase()}.`,
      subtitle:
        "Compare what you wanted your attention to serve with what your week or month actually optimized for."
    },
    interventions: {
      href: "./interventions.html",
      label: "Intervention Studio",
      navLabel: "Interventions",
      title: (range) => `Design gentle controls for the ${range.label.toLowerCase()} patterns.`,
      subtitle:
        "Design gentle rules that redirect attention without making the product feel punitive."
    },
    sources: {
      href: "./sources.html",
      label: "Sources, Transparency, And Privacy",
      navLabel: "Sources",
      title: (range) => `See what the system can and cannot know in the ${range.label.toLowerCase()} view.`,
      subtitle:
        "Make trust explicit by showing what the system can see, what it infers, and where the line still is."
    }
  };

  const uiState = {
    range: readStoredRange(),
    graphNode: window.localStorage.getItem(STORAGE_KEYS.graphNode) || "",
    sequence: window.localStorage.getItem(STORAGE_KEYS.sequence) || "",
    pinnedGoal: window.localStorage.getItem(STORAGE_KEYS.pinnedGoal) || "",
    graphFilters: {
      goals: "All",
      tones: "All",
      sources: "All"
    }
  };

  function readStoredRange() {
    const stored = window.localStorage.getItem(STORAGE_KEYS.range);
    if (stored && appData.ranges[stored]) {
      return stored;
    }
    return "week";
  }

  function writeStoredValue(key, value) {
    window.localStorage.setItem(key, value);
  }

  function feedbackKey(insightId) {
    return `attention-os-feedback-${insightId}`;
  }

  function ruleKey(ruleId) {
    return `attention-os-rule-${ruleId}`;
  }

  function patternKey(patternId) {
    return `attention-os-pattern-${patternId}`;
  }

  function ensureSelections() {
    const graphRange = appData.graph.ranges[uiState.range];
    if (!graphRange.nodeDetails[uiState.graphNode]) {
      uiState.graphNode = graphRange.defaultNodeId;
      writeStoredValue(STORAGE_KEYS.graphNode, uiState.graphNode);
    }

    const timelineRange = appData.timeline[uiState.range];
    const validSequence = timelineRange.sequences.some((sequence) => sequence.id === uiState.sequence);
    if (!validSequence) {
      uiState.sequence = timelineRange.defaultSequenceId;
      writeStoredValue(STORAGE_KEYS.sequence, uiState.sequence);
    }
  }

  function getCurrentRange() {
    return appData.ranges[uiState.range];
  }

  function getCurrentInsights() {
    return appData.insights[uiState.range];
  }

  function getCurrentDrift() {
    return appData.drift[uiState.range];
  }

  function getCurrentGraph() {
    return appData.graph.ranges[uiState.range];
  }

  function getCurrentTimeline() {
    return appData.timeline[uiState.range];
  }

  function getCurrentInterventions() {
    return appData.interventions[uiState.range];
  }

  function getPinnedGoal() {
    const drift = getCurrentDrift();
    return drift.goals.find((goal) => goal.id === uiState.pinnedGoal) || null;
  }

  function isRuleEnabled(rule) {
    const stored = window.localStorage.getItem(ruleKey(rule.id));
    if (stored === null) {
      return rule.defaultEnabled;
    }
    return stored === "true";
  }

  function getInsightFeedback(insightId) {
    return window.localStorage.getItem(feedbackKey(insightId)) || "";
  }

  function getPatternDecision(patternId) {
    return window.localStorage.getItem(patternKey(patternId)) || "";
  }

  function findInsightById(insightId) {
    const allInsights = Object.values(appData.insights).flat();
    return allInsights.find((insight) => insight.id === insightId) || null;
  }

  function findRuleById(ruleId) {
    const allRules = Object.values(appData.interventions).flat();
    return allRules.find((rule) => rule.id === ruleId) || null;
  }

  function rangeButtonsMarkup() {
    return Object.values(appData.ranges)
      .map((range) => {
        const active = range.key === uiState.range ? " active" : "";
        return `<button class="range-btn${active}" data-range="${range.key}">${range.label}</button>`;
      })
      .join("");
  }

  function renderSidebar() {
    const currentRange = getCurrentRange();
    const pinnedGoal = getPinnedGoal();
    const protectCard = pinnedGoal
      ? {
          title: pinnedGoal.label,
          reason: pinnedGoal.note,
          suggestedAction: "Pinned from Drift view"
        }
      : currentRange.protectNext;

    sidebar.innerHTML = `
      <div class="brand-block">
        <p class="eyebrow">Attention OS</p>
        <h1>Observatory</h1>
        <p class="brand-copy">
          A reflective control layer for seeing where attention went, what shaped it, and what to protect next.
        </p>
      </div>

      <section class="side-section">
        <p class="section-label">Modes</p>
        <div class="mode-stack">
          <button class="mode-chip mode-chip-active" type="button">Observe</button>
          <button class="mode-chip" type="button">Guide</button>
          <button class="mode-chip" type="button">Protect</button>
        </div>
      </section>

      <section class="side-section side-card">
        <div class="side-card-head">
          <p class="section-label">Current Intention</p>
          <span class="mini-badge">Live</span>
        </div>
        <h2 class="side-card-title">${appData.intentions.northStar}</h2>
        <p class="side-card-copy">${appData.intentions.weekly}</p>
        <div class="side-stat">
          <span>Attention drift</span>
          <strong>${currentRange.metrics.driftDelta}</strong>
        </div>
      </section>

      <nav class="side-section">
        <p class="section-label">Routes</p>
        <div class="route-nav">
          ${Object.entries(ROUTES)
            .map(([routeKey, routeValue]) => {
              const active = routeKey === page ? " route-link-active" : "";
              return `<a class="route-link${active}" href="${routeValue.href}">${routeValue.navLabel}</a>`;
            })
            .join("")}
        </div>
      </nav>

      <section class="side-section">
        <p class="section-label">Signals</p>
        <div class="signal-list">
          ${currentRange.sidebarSignals.map(renderSignalCard).join("")}
        </div>
      </section>

      <section class="side-section side-card">
        <div class="side-card-head">
          <p class="section-label">Protect Next</p>
          <span class="mini-badge mini-badge-muted">Priority</span>
        </div>
        <h2 class="side-card-title">${protectCard.title}</h2>
        <p class="side-card-copy">${protectCard.reason}</p>
        <div class="side-stat">
          <span>Suggested move</span>
          <strong>${protectCard.suggestedAction}</strong>
        </div>
      </section>
    `;
  }

  function renderSignalCard(signal) {
    return `
      <div class="signal-card">
        <span class="signal-dot signal-dot-${signal.tone}"></span>
        <div>
          <strong>${signal.title}</strong>
          <p>${signal.summary}</p>
        </div>
      </div>
    `;
  }

  function renderTopbar() {
    const route = ROUTES[page];
    const currentRange = getCurrentRange();

    topbar.innerHTML = `
      <div>
        <p class="section-label">${route.label}</p>
        <h2 class="topbar-title">${route.title(currentRange)}</h2>
        <p class="topbar-subtitle">${route.subtitle}</p>
      </div>
      <div class="topbar-actions">
        <div class="timeframe-switch" role="tablist" aria-label="Time range">
          ${rangeButtonsMarkup()}
        </div>
        <button class="ghost-btn" type="button" data-sim-action="export">Export Memo</button>
      </div>
    `;
  }

  function renderInsightCard(insight) {
    const feedback = getInsightFeedback(insight.id);
    const observedClass = insight.observedVsInferred === "observed" ? "insight-card-observed" : "insight-card-inferred";
    const accurateClass = feedback === "accurate" ? "button-chip-active" : "";
    const inaccurateClass = feedback === "inaccurate" ? "button-chip-active" : "";

    return `
      <article class="insight-card ${observedClass}">
        <div class="insight-head">
          <div class="insight-meta">
            <span class="mini-badge ${insight.observedVsInferred === "observed" ? "" : "mini-badge-muted"}">
              ${insight.observedVsInferred === "observed" ? "Observed" : "Inferred"}
            </span>
            <span class="confidence-chip">${insight.confidence} confidence</span>
          </div>
          <p class="insight-type">${insight.type.replaceAll("_", " ")}</p>
        </div>
        <h3>${insight.title}</h3>
        <p>${insight.summary}</p>
        <div class="insight-actions">
          <button class="ghost-btn small-btn" type="button" data-open-lineage="${insight.id}">Why this insight?</button>
          <div class="feedback-group">
            <button class="button-chip ${accurateClass}" type="button" data-feedback-insight="${insight.id}" data-feedback-value="accurate">Accurate</button>
            <button class="button-chip ${inaccurateClass}" type="button" data-feedback-insight="${insight.id}" data-feedback-value="inaccurate">Needs work</button>
          </div>
        </div>
      </article>
    `;
  }

  function renderBudgetRows(slices) {
    return slices
      .map(
        (slice) => `
          <div class="budget-row">
            <span>${slice.label}</span>
            <div class="budget-track">
              <div class="budget-fill" style="width: ${slice.value}%"></div>
            </div>
            <strong>${slice.value}%</strong>
          </div>
        `
      )
      .join("");
  }

  function renderMetricCard(label, value, foot) {
    return `
      <article class="stat-card panel">
        <p class="stat-label">${label}</p>
        <strong class="stat-value">${value}</strong>
        <p class="stat-foot">${foot}</p>
      </article>
    `;
  }

  function renderClusterTags(clusters) {
    return clusters
      .map(
        (cluster) => `
          <button class="tag tag-button" type="button" data-open-graph-node="${cluster.id}">
            ${cluster.label}
          </button>
        `
      )
      .join("");
  }

  function renderHomePage() {
    const currentRange = getCurrentRange();
    const drift = getCurrentDrift();
    const insights = getCurrentInsights();

    pageMount.innerHTML = `
      <section class="home-grid">
        <article class="panel memo-panel">
          <div class="panel-head">
            <p class="section-label">Digital Reflection</p>
            <span class="mini-badge">Narrative</span>
          </div>
          <p class="memo-kicker">${currentRange.memoKicker}</p>
          <p class="memo-body">${currentRange.memoBody}</p>
          <div class="memo-tags">${renderClusterTags(currentRange.topClusters)}</div>
        </article>

        <article class="panel budget-panel">
          <div class="panel-head">
            <p class="section-label">Attention Budget</p>
            <span class="mini-badge mini-badge-muted">Observed</span>
          </div>
          <div class="budget-bars">${renderBudgetRows(currentRange.budget)}</div>
        </article>

        <section class="insight-grid">
          ${insights.map(renderInsightCard).join("")}
        </section>

        <section class="stats-grid">
          ${renderMetricCard("Depth Ratio", currentRange.metrics.depthRatio, "Long-form reading, focused work, lectures")}
          ${renderMetricCard("Reactive Sessions", currentRange.metrics.reactiveSessions, "Rapid feed loops with 3+ app switches")}
          ${renderMetricCard("Top Rabbit Hole", currentRange.metrics.topRabbitHole, "The strongest repeated sequence in this range")}
          ${renderMetricCard("Goal Alignment", currentRange.metrics.goalAlignment, "Compared with your stated weekly intentions")}
        </section>

        <section class="dual-grid">
          <article class="panel drift-summary-panel">
            <div class="panel-head">
              <p class="section-label">Drift Summary</p>
              <span class="mini-badge">Interpretation</span>
            </div>
            <div class="drift-summary">
              <div class="drift-meter-track">
                <div class="drift-meter-fill" style="height: ${drift.scoreValue}%"></div>
              </div>
              <div>
                <strong class="summary-score">${drift.scoreLabel}</strong>
                <p class="memo-body">${drift.summary}</p>
                <a class="text-link" href="./drift.html">Open the full Drift view</a>
              </div>
            </div>
          </article>

          <article class="panel protect-panel">
            <div class="panel-head">
              <p class="section-label">Protect This Next</p>
              <span class="mini-badge mini-badge-muted">Action</span>
            </div>
            <h3>${currentRange.protectNext.title}</h3>
            <p>${currentRange.protectNext.reason}</p>
            <div class="action-row">
              <a class="primary-link" href="./interventions.html">Design an intervention</a>
              <button class="ghost-btn small-btn" type="button" data-open-graph-node="${currentRange.protectNext.title.toLowerCase().includes("physics") ? "physics" : "reading"}">Inspect cluster</button>
            </div>
          </article>
        </section>

        <section class="cta-grid">
          <a class="panel cta-card" href="./graph.html">
            <p class="section-label">Attention Map</p>
            <h3>Inspect the digital mind map</h3>
            <p>Follow the clusters, emotions, and goals shaping this range.</p>
          </a>
          <a class="panel cta-card" href="./timeline.html">
            <p class="section-label">Rabbit-Hole Replay</p>
            <h3>Replay the strongest sequence</h3>
            <p>Study the steps that pulled the evening away from the intended plan.</p>
          </a>
          <a class="panel cta-card" href="./sources.html">
            <p class="section-label">Transparency</p>
            <h3>See what the system actually knows</h3>
            <p>Understand what was directly observed, what was inferred, and what remains speculative.</p>
          </a>
        </section>
      </section>
    `;
  }

  function renderGraphNodes() {
    return appData.graph.nodes
      .map((node) => {
        const active = uiState.graphNode === node.id ? " active" : "";
        const styleClass = node.style === "core" ? "graph-node-core" : node.style === "accent" ? "graph-node-accent" : "";
        return `
          <button
            class="graph-node ${styleClass}${active}"
            type="button"
            data-graph-node="${node.id}"
            style="left: ${node.x}%; top: ${node.y}%;"
          >
            ${node.label}
          </button>
        `;
      })
      .join("");
  }

  function renderGraphFilters() {
    const currentGraph = getCurrentGraph();
    return ["goals", "tones", "sources"]
      .map((group) => {
        return `
          <div class="filter-group">
            <p class="section-label">${group}</p>
            <div class="filter-stack">
              ${currentGraph.filters[group]
                .map((value) => {
                  const active = uiState.graphFilters[group] === value ? " button-chip-active" : "";
                  return `<button class="button-chip${active}" type="button" data-filter-group="${group}" data-filter-value="${value}">${value}</button>`;
                })
                .join("")}
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderGraphPage() {
    const currentGraph = getCurrentGraph();
    const detail = currentGraph.nodeDetails[uiState.graphNode];

    pageMount.innerHTML = `
      <section class="graph-page-grid">
        <article class="panel graph-panel">
          <div class="panel-head">
            <p class="section-label">Mind Graph</p>
            <span class="mini-badge">Interactive</span>
          </div>
          <div class="graph-stage">
            <svg viewBox="0 0 640 380" class="graph-wires" aria-hidden="true">
              ${appData.graph.edges
                .map((edge) => {
                  const source = appData.graph.nodes.find((node) => node.id === edge.source);
                  const target = appData.graph.nodes.find((node) => node.id === edge.target);
                  return `<line x1="${source.x * 6.4}" y1="${source.y * 3.8}" x2="${target.x * 6.4}" y2="${target.y * 3.8}"></line>`;
                })
                .join("")}
            </svg>
            ${renderGraphNodes()}
          </div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Selected Cluster</p>
            <span class="mini-badge mini-badge-muted">${uiState.range}</span>
          </div>
          <h3>${detail.title}</h3>
          <p>${detail.summary}</p>
          <div class="detail-stack">
            <div class="detail-card">
              <p class="section-label">Trend</p>
              <p>${detail.trend}</p>
            </div>
            <div class="detail-card">
              <p class="section-label">Related Creators</p>
              <div class="chip-stack">${detail.relatedCreators.map((item) => `<span class="tag">${item}</span>`).join("")}</div>
            </div>
            <div class="detail-card">
              <p class="section-label">Related Emotions</p>
              <div class="chip-stack">${detail.relatedEmotions.map((item) => `<span class="tag">${item}</span>`).join("")}</div>
            </div>
            <div class="detail-card">
              <p class="section-label">Related Goals</p>
              <div class="chip-stack">${detail.relatedGoals.map((item) => `<span class="tag">${item}</span>`).join("")}</div>
            </div>
          </div>
          <div class="action-row action-row-wrap">
            <button class="ghost-btn small-btn" type="button" data-open-lineage="${detail.relatedInsightId}">Why this cluster?</button>
            <button class="ghost-btn small-btn" type="button" data-open-sequence="${detail.relatedSequenceId}">Open related replay</button>
          </div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Compare and Filter</p>
            <span class="mini-badge">Exploration</span>
          </div>
          <p class="memo-body">${currentGraph.comparison}</p>
          ${renderGraphFilters()}
          <div class="filter-summary">
            <strong>Current filter lens</strong>
            <p>
              Goal: ${uiState.graphFilters.goals} · Tone: ${uiState.graphFilters.tones} · Source: ${uiState.graphFilters.sources}
            </p>
          </div>
        </article>
      </section>
    `;
  }

  function renderSequenceList(sequences) {
    return sequences
      .map((sequence) => {
        const active = uiState.sequence === sequence.id ? " sequence-card-active" : "";
        return `
          <button class="sequence-card${active}" type="button" data-sequence-id="${sequence.id}">
            <p class="section-label">${sequence.timeLabel}</p>
            <strong>${sequence.label}</strong>
            <p>${sequence.summary}</p>
          </button>
        `;
      })
      .join("");
  }

  function renderSequenceSteps(sequence) {
    return sequence.steps
      .map(
        (step) => `
          <article class="timeline-step timeline-step-${step.observedVsInferred}">
            <div class="timeline-step-head">
              <span class="timeline-step-time">${step.time}</span>
              <span class="mini-badge ${step.observedVsInferred === "observed" ? "" : "mini-badge-muted"}">
                ${step.observedVsInferred === "observed" ? "Observed" : "Inferred"}
              </span>
            </div>
            <h3>${step.title}</h3>
            <p>${step.detail}</p>
            <div class="timeline-why">
              <strong>Why it matters</strong>
              <p>${step.whyItMatters}</p>
            </div>
          </article>
        `
      )
      .join("");
  }

  function renderTimelinePage() {
    const currentTimeline = getCurrentTimeline();
    const selectedSequence = currentTimeline.sequences.find((sequence) => sequence.id === uiState.sequence);

    pageMount.innerHTML = `
      <section class="timeline-page-grid">
        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Replay Library</p>
            <span class="mini-badge">Sequences</span>
          </div>
          <div class="sequence-list">${renderSequenceList(currentTimeline.sequences)}</div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Selected Replay</p>
            <span class="mini-badge mini-badge-muted">${selectedSequence.timeLabel}</span>
          </div>
          <h3>${selectedSequence.label}</h3>
          <p>${selectedSequence.summary}</p>
          <div class="detail-grid">
            <div class="detail-card">
              <p class="section-label">Switch Burst</p>
              <p>${selectedSequence.switchBurst}</p>
            </div>
            <div class="detail-card">
              <p class="section-label">Emotional Shift</p>
              <p>${selectedSequence.emotionalShift}</p>
            </div>
            <div class="detail-card">
              <p class="section-label">Break Point</p>
              <p>${selectedSequence.breakPoint}</p>
            </div>
          </div>
          <div class="action-row action-row-wrap">
            <button class="ghost-btn small-btn" type="button" data-open-graph-node="${selectedSequence.clusterNodeId}">Open in map</button>
            <a class="text-link" href="./interventions.html">See related interventions</a>
          </div>
        </article>

        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">Replay Steps</p>
            <span class="mini-badge">Observed vs inferred</span>
          </div>
          <div class="timeline-step-list">${renderSequenceSteps(selectedSequence)}</div>
        </article>

        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">Intervention Opportunities</p>
            <span class="mini-badge mini-badge-muted">Suggested</span>
          </div>
          <div class="list-stack">
            ${selectedSequence.opportunities.map((item) => `<div class="list-card">${item}</div>`).join("")}
          </div>
        </article>
      </section>
    `;
  }

  function renderGoalRows(goals) {
    return goals
      .map((goal) => {
        const pinned = uiState.pinnedGoal === goal.id ? " button-chip-active" : "";
        return `
          <div class="goal-row">
            <div class="goal-row-head">
              <strong>${goal.label}</strong>
              <button class="button-chip ${pinned}" type="button" data-pin-goal="${goal.id}">Pin for next week</button>
            </div>
            <div class="compare-bars">
              <div class="compare-bar-set">
                <span class="goal-bar-label">Intended</span>
                <span class="goal-bar goal-bar-intended" style="width: ${goal.intended}%"></span>
              </div>
              <div class="compare-bar-set">
                <span class="goal-bar-label">Actual</span>
                <span class="goal-bar goal-bar-actual" style="width: ${goal.actual}%"></span>
              </div>
            </div>
            <p class="memo-body">${goal.note}</p>
          </div>
        `;
      })
      .join("");
  }

  function renderPatternCards(patterns) {
    return patterns
      .map((pattern) => {
        const decision = getPatternDecision(pattern.id);
        const expected = decision === "expected" ? " button-chip-active" : "";
        const unwanted = decision === "unwanted" ? " button-chip-active" : "";

        return `
          <article class="pattern-card">
            <h3>${pattern.label}</h3>
            <p>${pattern.summary}</p>
            <div class="feedback-group">
              <button class="button-chip ${expected}" type="button" data-pattern-id="${pattern.id}" data-pattern-value="expected">Expected</button>
              <button class="button-chip ${unwanted}" type="button" data-pattern-id="${pattern.id}" data-pattern-value="unwanted">Unwanted</button>
            </div>
          </article>
        `;
      })
      .join("");
  }

  function renderDriftPage() {
    const drift = getCurrentDrift();

    pageMount.innerHTML = `
      <section class="drift-page-grid">
        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Drift Meter</p>
            <span class="mini-badge">Interpretation</span>
          </div>
          <div class="drift-summary">
            <div class="drift-meter-track">
              <div class="drift-meter-fill" style="height: ${drift.scoreValue}%"></div>
            </div>
            <div>
              <strong class="summary-score">${drift.scoreLabel}</strong>
              <p>${drift.summary}</p>
            </div>
          </div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Neglected Intentions</p>
            <span class="mini-badge mini-badge-muted">Human layer</span>
          </div>
          <div class="list-stack">
            ${drift.neglectedIntentions.map((item) => `<div class="list-card">${item}</div>`).join("")}
          </div>
        </article>

        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">You Said vs You Did</p>
            <span class="mini-badge">Honesty layer</span>
          </div>
          <div class="goal-stack">${renderGoalRows(drift.goals)}</div>
        </article>

        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">Recurring Displacement Clusters</p>
            <span class="mini-badge mini-badge-muted">Review</span>
          </div>
          <div class="pattern-grid">${renderPatternCards(drift.displacementClusters)}</div>
        </article>
      </section>
    `;
  }

  function renderRuleCards(rules) {
    return rules
      .map((rule) => {
        const enabled = isRuleEnabled(rule);
        return `
          <article class="rule-card ${enabled ? "rule-card-enabled" : ""}">
            <div class="rule-card-head">
              <div>
                <p class="section-label">${rule.category.replaceAll("_", " ")}</p>
                <h3>${rule.title}</h3>
              </div>
              <button class="toggle-btn ${enabled ? "toggle-btn-active" : ""}" type="button" data-toggle-rule="${rule.id}">
                ${enabled ? "Enabled" : "Disabled"}
              </button>
            </div>
            <p>${rule.description}</p>
            <div class="detail-grid">
              <div class="detail-card">
                <p class="section-label">Trigger</p>
                <p>${rule.trigger}</p>
              </div>
              <div class="detail-card">
                <p class="section-label">Action</p>
                <p>${rule.action}</p>
              </div>
              <div class="detail-card">
                <p class="section-label">Targets</p>
                <div class="chip-stack">${rule.targetPatterns.map((pattern) => `<span class="tag">${pattern}</span>`).join("")}</div>
              </div>
              <div class="detail-card">
                <p class="section-label">Preview</p>
                <p>${rule.preview}</p>
              </div>
            </div>
          </article>
        `;
      })
      .join("");
  }

  function renderInterventionsPage() {
    const rules = getCurrentInterventions();

    pageMount.innerHTML = `
      <section class="interventions-page-grid">
        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">Rule Set</p>
            <span class="mini-badge">Gentle control</span>
          </div>
          <div class="rule-grid">${renderRuleCards(rules)}</div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Design Notes</p>
            <span class="mini-badge mini-badge-muted">Boundary</span>
          </div>
          <div class="list-stack">
            <div class="list-card">Interventions should redirect attention, not punish it.</div>
            <div class="list-card">V1 should avoid promising reliable feed retraining on third-party platforms.</div>
            <div class="list-card">Prompts should feel calm, specific, and user-serving.</div>
          </div>
        </article>
      </section>
    `;
  }

  function renderSourceCards() {
    return appData.sources
      .map(
        (source) => `
          <article class="source-card">
            <div class="source-card-head">
              <div>
                <p class="section-label">${source.status}</p>
                <h3>${source.name}</h3>
              </div>
              <span class="mini-badge mini-badge-muted">${source.privacyMode}</span>
            </div>
            <p>${source.notes}</p>
            <div class="detail-grid">
              <div class="detail-card">
                <p class="section-label">Coverage</p>
                <p>${source.coverageLevel}</p>
              </div>
              <div class="detail-card">
                <p class="section-label">Data kinds</p>
                <div class="chip-stack">${source.dataKinds.map((kind) => `<span class="tag">${kind}</span>`).join("")}</div>
              </div>
            </div>
          </article>
        `
      )
      .join("");
  }

  function renderSourcesPage() {
    const currentInsights = getCurrentInsights();

    pageMount.innerHTML = `
      <section class="sources-page-grid">
        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">Connected Sources</p>
            <span class="mini-badge">Coverage</span>
          </div>
          <div class="source-grid">${renderSourceCards()}</div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Truth Boundary</p>
            <span class="mini-badge mini-badge-muted">Trust</span>
          </div>
          <div class="truth-stack">
            <div class="truth-card truth-card-observed">
              <strong>Can observe directly</strong>
              <ul>
                ${appData.privacy.directObservability.map((item) => `<li>${item}</li>`).join("")}
              </ul>
            </div>
            <div class="truth-card truth-card-inferred">
              <strong>Can infer with moderate confidence</strong>
              <ul>
                ${appData.privacy.moderateInference.map((item) => `<li>${item}</li>`).join("")}
              </ul>
            </div>
            <div class="truth-card">
              <strong>Still speculative</strong>
              <ul>
                ${appData.privacy.speculative.map((item) => `<li>${item}</li>`).join("")}
              </ul>
            </div>
          </div>
        </article>

        <article class="panel">
          <div class="panel-head">
            <p class="section-label">Local-First Controls</p>
            <span class="mini-badge">Privacy</span>
          </div>
          <p class="memo-body">${appData.privacy.storageModel}. ${appData.privacy.syncPolicy}</p>
          <div class="list-stack">
            ${appData.privacy.controls.map((item) => `<div class="list-card">${item}</div>`).join("")}
          </div>
          <div class="action-row action-row-wrap">
            <button class="ghost-btn small-btn" type="button" data-sim-action="export">Simulate export</button>
            <button class="ghost-btn small-btn" type="button" data-sim-action="delete">Simulate local delete</button>
          </div>
        </article>

        <article class="panel panel-span-2">
          <div class="panel-head">
            <p class="section-label">Insight Lineage</p>
            <span class="mini-badge mini-badge-muted">${uiState.range}</span>
          </div>
          <div class="lineage-list">
            ${currentInsights
              .map(
                (insight) => `
                  <div class="lineage-row">
                    <div>
                      <strong>${insight.title}</strong>
                      <p>${insight.summary}</p>
                    </div>
                    <button class="ghost-btn small-btn" type="button" data-open-lineage="${insight.id}">Open lineage</button>
                  </div>
                `
              )
              .join("")}
          </div>
        </article>
      </section>
    `;
  }

  function renderPage() {
    switch (page) {
      case "graph":
        renderGraphPage();
        break;
      case "timeline":
        renderTimelinePage();
        break;
      case "drift":
        renderDriftPage();
        break;
      case "interventions":
        renderInterventionsPage();
        break;
      case "sources":
        renderSourcesPage();
        break;
      case "home":
      default:
        renderHomePage();
        break;
    }
  }

  function openLineage(insightId) {
    const lineage = appData.lineage[insightId];
    const insight = findInsightById(insightId);
    if (!lineage || !insight) {
      return;
    }

    dialogTitle.textContent = insight.title;
    dialogBody.innerHTML = `
      <div class="lineage-section">
        <div class="truth-card truth-card-observed">
          <strong>Observed facts</strong>
          <ul>
            ${lineage.observedFacts.map((fact) => `<li>${fact}</li>`).join("")}
          </ul>
        </div>
        <div class="truth-card truth-card-inferred">
          <strong>Inferences</strong>
          <ul>
            ${lineage.inferences.map((fact) => `<li>${fact}</li>`).join("")}
          </ul>
        </div>
        <div class="truth-card">
          <strong>Confidence reason</strong>
          <p>${lineage.confidenceReason}</p>
        </div>
        <div class="truth-card">
          <strong>User-editable fields</strong>
          <ul>
            ${lineage.userEditableFields.map((field) => `<li>${field}</li>`).join("")}
          </ul>
        </div>
      </div>
    `;

    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
  }

  function closeLineage() {
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("toast-visible");
    window.clearTimeout(showToast.timeoutId);
    showToast.timeoutId = window.setTimeout(() => {
      toast.classList.remove("toast-visible");
    }, 2200);
  }

  function handleClick(event) {
    const button = event.target.closest("button, a");
    if (!button) {
      return;
    }

    if (button.dataset.range) {
      uiState.range = button.dataset.range;
      writeStoredValue(STORAGE_KEYS.range, uiState.range);
      ensureSelections();
      render();
      return;
    }

    if (button.dataset.openLineage) {
      openLineage(button.dataset.openLineage);
      return;
    }

    if (button.dataset.feedbackInsight) {
      window.localStorage.setItem(feedbackKey(button.dataset.feedbackInsight), button.dataset.feedbackValue);
      render();
      return;
    }

    if (button.dataset.graphNode) {
      uiState.graphNode = button.dataset.graphNode;
      writeStoredValue(STORAGE_KEYS.graphNode, uiState.graphNode);
      render();
      return;
    }

    if (button.dataset.openGraphNode) {
      uiState.graphNode = button.dataset.openGraphNode;
      writeStoredValue(STORAGE_KEYS.graphNode, uiState.graphNode);
      if (page === "graph") {
        render();
      } else {
        window.location.href = "./graph.html";
      }
      return;
    }

    if (button.dataset.sequenceId) {
      uiState.sequence = button.dataset.sequenceId;
      writeStoredValue(STORAGE_KEYS.sequence, uiState.sequence);
      render();
      return;
    }

    if (button.dataset.openSequence) {
      uiState.sequence = button.dataset.openSequence;
      writeStoredValue(STORAGE_KEYS.sequence, uiState.sequence);
      if (page === "timeline") {
        render();
      } else {
        window.location.href = "./timeline.html";
      }
      return;
    }

    if (button.dataset.pinGoal) {
      uiState.pinnedGoal = button.dataset.pinGoal;
      writeStoredValue(STORAGE_KEYS.pinnedGoal, uiState.pinnedGoal);
      showToast("Pinned for next week");
      render();
      return;
    }

    if (button.dataset.toggleRule) {
      const rule = findRuleById(button.dataset.toggleRule);
      const currentValue = rule ? isRuleEnabled(rule) : false;
      const nextValue = (!currentValue).toString();
      window.localStorage.setItem(ruleKey(button.dataset.toggleRule), nextValue);
      render();
      return;
    }

    if (button.dataset.patternId) {
      window.localStorage.setItem(patternKey(button.dataset.patternId), button.dataset.patternValue);
      render();
      return;
    }

    if (button.dataset.filterGroup) {
      uiState.graphFilters[button.dataset.filterGroup] = button.dataset.filterValue;
      render();
      return;
    }

    if (button.dataset.simAction) {
      const action = button.dataset.simAction === "delete" ? "Prototype only: local delete flow previewed." : "Prototype only: export flow previewed.";
      showToast(action);
      return;
    }
  }

  function render() {
    ensureSelections();
    renderSidebar();
    renderTopbar();
    renderPage();
  }

  dialogClose.addEventListener("click", closeLineage);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      closeLineage();
    }
  });
  document.addEventListener("click", handleClick);

  render();
})();
