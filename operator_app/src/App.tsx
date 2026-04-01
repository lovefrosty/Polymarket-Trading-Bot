import { useEffect, useMemo, useState } from "react";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  fetchHistory,
  fetchRuntimes,
  fetchSnapshot,
  openRuntimeSocket,
  sendCommand,
  startRuntime,
  stopRuntime,
  type HistoryPoint,
  type OperatorEvent,
  type OperatorSnapshot,
  type RuntimeSummary
} from "./api";

type ConnectionState = "idle" | "connecting" | "live" | "offline";
type PanelId = "hero" | "positions" | "fills" | "timeline" | "orderlog";
type TimelineLane = TimelineEntry["lane"];
type ChartRange = 15 | 30 | 60 | 120;
type WorkspaceProfileId = "trading" | "risk" | "postmortem";

type TimelineEntry = {
  key: string;
  ts_ms: number;
  lane: "fill" | "command" | "decision" | "alert" | "order" | "switch";
  title: string;
  status: string;
  detail: string;
  meta: string;
};

type PendingAction = {
  label: string;
  detail: string;
  runId: string;
  execute: () => Promise<void>;
};

type CommandFeedback = {
  phase: "idle" | "confirm" | "submitting" | "submitted" | "ack" | "error";
  label: string;
  detail: string;
  commandId?: string;
};

type WorkspaceProfile = {
  id: WorkspaceProfileId;
  name: string;
  selectedRunId: string;
  timelineIndex: number;
  focusedPanel: PanelId;
  chartRange: ChartRange;
  timelineFilters: Record<TimelineLane, boolean>;
  windowBounds?: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
  };
};

type DeskSetEntry = RuntimeSummary & {
  suite_key: string;
  short_label: string;
};

function money(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  const sign = value > 0 ? "+" : "";
  return `${sign}$${value.toFixed(2)}`;
}

function decimal(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  return value.toFixed(digits);
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function qty(value: unknown): string {
  const parsed = numeric(value);
  return parsed === null ? "0.0" : parsed.toFixed(1);
}

function timeLabel(value: unknown): string {
  const ts = typeof value === "number" ? value : 0;
  if (!ts) return "--:--:--";
  return new Date(ts).toLocaleTimeString([], {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

function age(ms: number | null | undefined): string {
  if (!ms && ms !== 0) return "N/A";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function classForPnl(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? "status-good" : "status-bad";
}

function sideClass(value: unknown): string {
  const side = String(value ?? "").toLowerCase();
  if (side === "buy") return "side-buy";
  if (side === "sell") return "side-sell";
  return "";
}

function badgeClass(status: string): string {
  const normalized = status.toLowerCase();
  if (["live", "running", "applied", "open", "on", "quoteable", "buy", "healthy", "submitted", "ack"].includes(normalized)) {
    return "badge badge-good";
  }
  if (["offline", "rejected", "failed", "error", "down", "kill", "sell"].includes(normalized)) {
    return "badge badge-bad";
  }
  if (["pending", "connecting", "paused", "stale", "flatten", "submitting", "confirm"].includes(normalized)) {
    return "badge badge-warn";
  }
  return "badge";
}

function scopeLabel(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "N/A";
  return text.replace(/_/g, " ");
}

function laneClass(lane: TimelineEntry["lane"]): string {
  return `timeline-item timeline-${lane}`;
}

function shortRunId(value: string | null | undefined): string {
  if (!value) return "N/A";
  if (value.length <= 20) return value;
  return `${value.slice(0, 9)}…${value.slice(-8)}`;
}

function suiteKeyForRuntime(runtimeRoot: string | null | undefined): string | null {
  if (!runtimeRoot) return null;
  const parts = runtimeRoot.split("/").filter(Boolean);
  return parts.length >= 2 ? parts[parts.length - 2] : null;
}

function shortLabelForRun(runId: string): string {
  const normalized = runId.toLowerCase();
  for (const label of ["conservative", "proof045", "proof040", "holdtail"]) {
    if (normalized.includes(label)) return label;
  }
  return shortRunId(runId);
}

function sparklinePath(values: Array<number | null>, width: number, height: number): string {
  const numericValues = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (numericValues.length === 0) return "";
  const min = Math.min(...numericValues);
  const max = Math.max(...numericValues);
  const range = max - min || 1;
  const step = values.length > 1 ? width / (values.length - 1) : width;
  let path = "";
  values.forEach((value, index) => {
    if (value === null) return;
    const x = index * step;
    const y = height - ((value - min) / range) * height;
    path += `${path ? " L" : "M"} ${x.toFixed(2)} ${y.toFixed(2)}`;
  });
  return path;
}

function stringifyResult(value: unknown): string {
  if (!value || (typeof value === "object" && Object.keys(value as Record<string, unknown>).length === 0)) {
    return "";
  }
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const STORAGE_KEY = "pmx_operator_desk_v1";

function defaultProfiles(): Record<WorkspaceProfileId, WorkspaceProfile> {
  return {
    trading: {
      id: "trading",
      name: "Trading",
      selectedRunId: "",
      timelineIndex: 0,
      focusedPanel: "timeline",
      chartRange: 30,
      timelineFilters: { fill: true, command: true, decision: true, alert: true, order: true, switch: true },
    },
    risk: {
      id: "risk",
      name: "Risk",
      selectedRunId: "",
      timelineIndex: 0,
      focusedPanel: "hero",
      chartRange: 60,
      timelineFilters: { fill: false, command: true, decision: true, alert: true, order: true, switch: true },
    },
    postmortem: {
      id: "postmortem",
      name: "Postmortem",
      selectedRunId: "",
      timelineIndex: 0,
      focusedPanel: "orderlog",
      chartRange: 120,
      timelineFilters: { fill: true, command: true, decision: true, alert: true, order: false, switch: true },
    },
  };
}

function Panel(props: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-header">
        <div>{props.title}</div>
        {props.subtitle ? <div className="panel-subtitle">{props.subtitle}</div> : null}
      </header>
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export default function App() {
  const windowProfileId = useMemo(() => {
    const value = new URLSearchParams(window.location.search).get("profile");
    if (value === "trading" || value === "risk" || value === "postmortem") {
      return value as WorkspaceProfileId;
    }
    return null;
  }, []);
  const persistedState = useMemo(() => {
    const defaults = defaultProfiles();
    try {
      const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}") as {
        lastMainProfileId?: WorkspaceProfileId;
        profiles?: Partial<Record<WorkspaceProfileId, Partial<WorkspaceProfile>>>;
      };
      const mergedProfiles = { ...defaults };
      for (const id of Object.keys(defaults) as WorkspaceProfileId[]) {
        const incoming = parsed.profiles?.[id];
        if (!incoming) continue;
        mergedProfiles[id] = {
          ...mergedProfiles[id],
          ...incoming,
          timelineFilters: {
            ...mergedProfiles[id].timelineFilters,
            ...(incoming.timelineFilters ?? {}),
          },
        };
      }
      return {
        lastMainProfileId: parsed.lastMainProfileId ?? "trading",
        profiles: mergedProfiles,
      };
    } catch {
      return { lastMainProfileId: "trading" as WorkspaceProfileId, profiles: defaults };
    }
  }, []);
  const [activeProfileId, setActiveProfileId] = useState<WorkspaceProfileId>(windowProfileId ?? persistedState.lastMainProfileId);
  const [profiles, setProfiles] = useState<Record<WorkspaceProfileId, WorkspaceProfile>>(persistedState.profiles);
  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const activeProfile = profiles[activeProfileId];
  const [selectedRunId, setSelectedRunId] = useState<string>(activeProfile.selectedRunId);
  const [snapshot, setSnapshot] = useState<OperatorSnapshot | null>(null);
  const [commandPalette, setCommandPalette] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [timelineIndex, setTimelineIndex] = useState<number>(activeProfile.timelineIndex);
  const [focusedPanel, setFocusedPanel] = useState<PanelId>(activeProfile.focusedPanel);
  const [chartRange, setChartRange] = useState<ChartRange>(activeProfile.chartRange);
  const [timelineFilters, setTimelineFilters] = useState<Record<TimelineLane, boolean>>(activeProfile.timelineFilters);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [commandFeedback, setCommandFeedback] = useState<CommandFeedback>({
    phase: "idle",
    label: "Desk Idle",
    detail: "No pending operator action"
  });
  const [history, setHistory] = useState<HistoryPoint[]>([]);

  const activeDeskSet = useMemo<DeskSetEntry[]>(() => {
    const grouped = new Map<string, DeskSetEntry[]>();
    for (const runtime of runtimes) {
      const suiteKey = suiteKeyForRuntime(runtime.runtime_root);
      if (!suiteKey || !suiteKey.startsWith("kalshi-paper-4run-sess-")) continue;
      const entry: DeskSetEntry = {
        ...runtime,
        suite_key: suiteKey,
        short_label: shortLabelForRun(runtime.run_id),
      };
      const rows = grouped.get(suiteKey) ?? [];
      rows.push(entry);
      grouped.set(suiteKey, rows);
    }
    const latestSuite = Array.from(grouped.entries())
      .sort((left, right) => {
        const leftTs = Math.max(...left[1].map((item) => Number(item.updated_at_ms ?? 0)));
        const rightTs = Math.max(...right[1].map((item) => Number(item.updated_at_ms ?? 0)));
        return rightTs - leftTs;
      })[0];
    if (!latestSuite) return [];
    const order = ["conservative", "proof045", "proof040", "holdtail"];
    return latestSuite[1].sort((left, right) => order.indexOf(left.short_label) - order.indexOf(right.short_label));
  }, [runtimes]);

  useEffect(() => {
    const profileName = profiles[activeProfileId]?.name ?? "Operator";
    const title = `${profileName} Desk`;
    document.title = title;
    if (isTauriRuntime()) {
      void getCurrentWindow().setTitle(title);
    }
  }, [activeProfileId, profiles]);

  useEffect(() => {
    if (!isTauriRuntime()) return;
    const appWindow = getCurrentWindow();
    let disposed = false;

    const syncBounds = async (): Promise<void> => {
      try {
        const [position, size] = await Promise.all([appWindow.outerPosition(), appWindow.outerSize()]);
        if (disposed) return;
        setProfiles((current) => ({
          ...current,
          [activeProfileId]: {
            ...current[activeProfileId],
            windowBounds: {
              x: position.x,
              y: position.y,
              width: size.width,
              height: size.height,
            },
          },
        }));
      } catch {
        return;
      }
    };

    void syncBounds();
    let moveUnlisten: (() => void) | undefined;
    let resizeUnlisten: (() => void) | undefined;
    void appWindow.onMoved(() => void syncBounds()).then((unlisten) => {
      moveUnlisten = unlisten;
    });
    void appWindow.onResized(() => void syncBounds()).then((unlisten) => {
      resizeUnlisten = unlisten;
    });

    return () => {
      disposed = true;
      moveUnlisten?.();
      resizeUnlisten?.();
    };
  }, [activeProfileId]);

  useEffect(() => {
    setProfiles((current) => ({
      ...current,
      [activeProfileId]: {
        ...current[activeProfileId],
        selectedRunId,
        timelineIndex,
        focusedPanel,
        chartRange,
        timelineFilters,
      },
    }));
  }, [activeProfileId, selectedRunId, timelineIndex, focusedPanel, chartRange, timelineFilters]);

  useEffect(() => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        lastMainProfileId: windowProfileId ?? activeProfileId,
        profiles,
      })
    );
  }, [activeProfileId, profiles, windowProfileId]);

  useEffect(() => {
    const nextProfile = profiles[activeProfileId];
    setSelectedRunId(nextProfile.selectedRunId);
    setTimelineIndex(nextProfile.timelineIndex);
    setFocusedPanel(nextProfile.focusedPanel);
    setChartRange(nextProfile.chartRange);
    setTimelineFilters(nextProfile.timelineFilters);
    setHistory([]);
  }, [activeProfileId, profiles]);

  useEffect(() => {
    let mounted = true;
    const loadRuntimes = async () => {
      const rows = await fetchRuntimes();
      if (!mounted) return;
      setRuntimes(rows);
      setSelectedRunId((current) => {
        if (current && rows.some((row) => row.run_id === current)) return current;
        return rows[0]?.run_id ?? "";
      });
    };
    void loadRuntimes();
    const interval = window.setInterval(() => void loadRuntimes(), 5000);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    setHistory([]);
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      setHistory([]);
      return;
    }
    let active = true;
    const loadHistory = async () => {
      try {
        const points = await fetchHistory(selectedRunId, chartRange);
        if (!active) return;
        setHistory(points);
      } catch {
        if (!active) return;
        setHistory([]);
      }
    };
    void loadHistory();
    const interval = window.setInterval(() => void loadHistory(), 2500);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [selectedRunId, chartRange]);

  useEffect(() => {
    if (!selectedRunId) {
      setSnapshot(null);
      setConnectionState("idle");
      return;
    }

    let active = true;
    setConnectionState("connecting");
    void fetchSnapshot(selectedRunId)
      .then((next) => {
        if (!active) return;
        setSnapshot(next);
      })
      .catch(() => {
        if (!active) return;
        setConnectionState("offline");
      });

    const socket = openRuntimeSocket(selectedRunId, (event: OperatorEvent) => {
      setConnectionState("live");
      setSnapshot((current) => {
        if (!current) return current;
        if (event.event === "runtime_status") {
          const data = event.data as Pick<OperatorSnapshot, "runtime" | "market" | "decision" | "session" | "health" | "controls" | "monitor">;
          return { ...current, ...data };
        }
        if (event.event === "portfolio_status") {
          return { ...current, portfolio: event.data as OperatorSnapshot["portfolio"] };
        }
        if (event.event === "fill_event") {
          return { ...current, recent: { ...current.recent, fills: event.data as OperatorSnapshot["recent"]["fills"] } };
        }
        if (event.event === "decision_event") {
          return { ...current, recent: { ...current.recent, decisions: event.data as OperatorSnapshot["recent"]["decisions"] } };
        }
        if (event.event === "alert_event") {
          return { ...current, recent: { ...current.recent, alerts: event.data as OperatorSnapshot["recent"]["alerts"] } };
        }
        if (event.event === "command_event") {
          const commandRows = event.data as OperatorSnapshot["recent"]["commands"];
          const latest = commandRows[0] as Record<string, unknown> | undefined;
          if (latest) {
            setCommandFeedback((currentFeedback) => {
              if (currentFeedback.commandId && String(latest.command_id ?? "") !== currentFeedback.commandId) {
                return currentFeedback;
              }
              const resultDetail = stringifyResult(latest.result);
              return {
                phase: "ack",
                label: String(latest.command_type ?? "command").replace(/_/g, " "),
                detail: resultDetail || `status ${String(latest.status ?? "unknown").toUpperCase()}`,
                commandId: String(latest.command_id ?? "")
              };
            });
          }
          return { ...current, recent: { ...current.recent, commands: commandRows } };
        }
        return current;
      });
    });
    socket.onopen = () => setConnectionState("live");
    socket.onclose = () => active && setConnectionState("offline");
    socket.onerror = () => active && setConnectionState("offline");

    return () => {
      active = false;
      socket.close();
    };
  }, [selectedRunId]);

  const timeline = useMemo<TimelineEntry[]>(() => {
    if (!snapshot) return [];
    const episodes = snapshot.session.selection.recent_episodes ?? [];
    const switchRows: TimelineEntry[] = [];
    episodes.forEach((episode, index) => {
      if (index === 0 || !episode.market) return;
      const previous = episodes[index - 1];
      switchRows.push({
          key: `switch-${episode.ts_ms ?? index}-${episode.market ?? "na"}`,
          ts_ms: numeric(episode.ts_ms) ?? 0,
          lane: "switch" as const,
          title: `SWITCH ${String(previous.market ?? "N/A")} -> ${String(episode.market ?? "N/A")}`,
          status: String(episode.reason ?? snapshot.market.selected_reason ?? "rotation"),
          detail: `Selector rotated to ${String(episode.market ?? "N/A")}`,
          meta: String(episode.reason ?? "rotation")
      });
    });
    const fillRows = snapshot.recent.fills.map((item, index) => {
      const row = item as Record<string, unknown>;
      return {
        key: `fill-${row.order_id ?? index}`,
        ts_ms: numeric(row.ts_ms) ?? 0,
        lane: "fill" as const,
        title: `${String(row.side ?? "").toUpperCase()} ${String(row.market_slug ?? "")}`.trim(),
        status: String(row.risk_action ?? "normal"),
        detail: `${String(row.fill_qty ?? "")} @ ${String(row.fill_price ?? "")}`,
        meta: String(row.order_id ?? "")
      };
    });
    const commandRows = snapshot.recent.commands.map((item, index) => {
      const row = item as Record<string, unknown>;
      return {
        key: `command-${row.command_id ?? index}`,
        ts_ms: numeric(row.requested_at_ms) ?? 0,
        lane: "command" as const,
        title: String(row.command_type ?? "").replace(/_/g, " "),
        status: String(row.status ?? "pending"),
        detail: stringifyResult(row.result) || JSON.stringify(row.payload ?? {}),
        meta: `${String(row.requested_by ?? "operator_app")} · ${String(row.command_id ?? "")}`
      };
    });
    const decisionRows = snapshot.recent.decisions.map((item, index) => {
      const row = item as Record<string, unknown>;
      const sizePlan = (row.size_plan ?? {}) as Record<string, unknown>;
      return {
        key: `decision-${row.ts_ms ?? index}`,
        ts_ms: numeric(row.ts_ms) ?? 0,
        lane: "decision" as const,
        title: String(row.action ?? "decision"),
        status: String(((row.risk_decision ?? {}) as Record<string, unknown>).action ?? "normal"),
        detail: `buy ${String(sizePlan.buy_amount ?? 0)} / sell ${String(sizePlan.sell_amount ?? 0)}`,
        meta: `${String(sizePlan.buy_limiter ?? "n/a")} · ${String(sizePlan.sell_limiter ?? "n/a")}`
      };
    });
    const alertRows = snapshot.recent.alerts.map((item, index) => {
      const row = item as Record<string, unknown>;
      return {
        key: `alert-${row.alert_type ?? index}-${row.ts_ms ?? 0}`,
        ts_ms: numeric(row.ts_ms) ?? 0,
        lane: "alert" as const,
        title: String(row.summary ?? ""),
        status: String(row.severity ?? "alert"),
        detail: String(row.next_action ?? ""),
        meta: String(row.alert_type ?? "")
      };
    });
    const orderRows = snapshot.recent.open_orders.map((item, index) => {
      const row = item as Record<string, unknown>;
      return {
        key: `order-${row.order_id ?? index}`,
        ts_ms: numeric(row.ts_ms) ?? 0,
        lane: "order" as const,
        title: `${String(row.side ?? "").toUpperCase()} ${String(row.market_slug ?? "")}`.trim(),
        status: String(row.status ?? "open"),
        detail: `${String(row.price ?? "")} x ${String(row.size ?? "")}`,
        meta: String(row.order_id ?? "")
      };
    });
    const allRows = [...switchRows, ...fillRows, ...commandRows, ...decisionRows, ...alertRows, ...orderRows];
    return allRows
      .filter((entry) => timelineFilters[entry.lane])
      .sort((left, right) => right.ts_ms - left.ts_ms)
      .slice(0, 32);
  }, [snapshot, timelineFilters]);

  useEffect(() => {
    setTimelineIndex((current) => {
      if (timeline.length === 0) return 0;
      return Math.max(0, Math.min(current, timeline.length - 1));
    });
  }, [timeline.length]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === "k") {
        event.preventDefault();
        setCommandPalette((value) => !value);
        return;
      }
      if (pendingAction || commandPalette) return;
      if (key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setTimelineIndex((current) => Math.min(current + 1, Math.max(0, timeline.length - 1)));
      }
      if (key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setTimelineIndex((current) => Math.max(current - 1, 0));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [commandPalette, pendingAction, timeline.length]);

  const runtime = snapshot?.runtime;
  const positions = snapshot?.portfolio.positions ?? [];
  const fills = snapshot?.recent.fills ?? [];
  const selectedRuntimeSummary = useMemo(
    () => runtimes.find((item) => item.run_id === selectedRunId) ?? null,
    [runtimes, selectedRunId]
  );
  const orderLog = useMemo(() => timeline.filter((entry) => entry.lane === "order" || entry.lane === "command").slice(0, 16), [timeline]);
  const selectedTimeline = timeline[timelineIndex];
  const latestDecision = snapshot?.recent.decisions[0] as Record<string, unknown> | undefined;
  const latestSizePlan = (latestDecision?.size_plan ?? {}) as Record<string, unknown>;
  const latestRisk = (latestDecision?.risk_decision ?? {}) as Record<string, unknown>;
  const currentDecision = snapshot?.decision.current;
  const marketSelection = snapshot?.market.selection;
  const sessionSelection = snapshot?.session.selection;
  const sessionPerformance = snapshot?.session.performance;
  const latestFillFee = sessionPerformance?.latest_fill_fee;
  const pnlPath = sparklinePath(history.map((item) => item.total_pnl), 320, 48);
  const exposurePath = sparklinePath(history.map((item) => item.gross_exposure), 320, 48);

  function toggleTimelineFilter(lane: TimelineLane): void {
    setTimelineFilters((current) => {
      const next = { ...current, [lane]: !current[lane] };
      const anyEnabled = Object.values(next).some(Boolean);
      return anyEnabled ? next : current;
    });
  }

  async function openProfileWindow(profileId: WorkspaceProfileId): Promise<void> {
    if (!isTauriRuntime()) {
      setActiveProfileId(profileId);
      return;
    }
    const label = `desk-${profileId}`;
    const existing = await WebviewWindow.getByLabel(label);
    if (existing) {
      await existing.setFocus();
      return;
    }
    const profileName = profiles[profileId].name;
    const bounds = profiles[profileId].windowBounds;
    const deskWindow = new WebviewWindow(label, {
      title: `${profileName} Desk`,
      url: `/?profile=${profileId}`,
      x: bounds?.x,
      y: bounds?.y,
      width: bounds?.width ?? 1680,
      height: bounds?.height ?? 1040,
      minWidth: 1280,
      minHeight: 800,
      resizable: true,
    });
    void deskWindow.once("tauri://created", async () => {
      await deskWindow.setFocus();
    });
  }

  async function handleStart(): Promise<void> {
    setCommandFeedback({
      phase: "submitting",
      label: "start runtime",
      detail: "Launching PAPER runtime"
    });
    try {
      const row = await startRuntime({ symbol: "BTC", safe_risk_profile: "500" });
      setSelectedRunId(row.run_id);
      setCommandFeedback({
        phase: "submitted",
        label: "start runtime",
        detail: `Runtime ${row.run_id} launched`
      });
    } catch (error) {
      setCommandFeedback({
        phase: "error",
        label: "start runtime",
        detail: error instanceof Error ? error.message : "start failed"
      });
    }
  }

  async function performCommand(runId: string, commandType: string, payload: Record<string, unknown>, label: string): Promise<void> {
    setCommandFeedback({
      phase: "submitting",
      label,
      detail: "Submitting command to control plane"
    });
    try {
      const response = await sendCommand(runId, commandType, payload);
      setCommandFeedback({
        phase: "submitted",
        label,
        detail: `Queued as ${response.command_id}`,
        commandId: response.command_id
      });
    } catch (error) {
      setCommandFeedback({
        phase: "error",
        label,
        detail: error instanceof Error ? error.message : "command failed"
      });
    }
  }

  async function performStop(runId: string): Promise<void> {
    setCommandFeedback({
      phase: "submitting",
      label: "stop runtime",
      detail: "Requesting graceful stop"
    });
    try {
      const result = await stopRuntime(runId);
      setCommandFeedback({
        phase: "ack",
        label: "stop runtime",
        detail: `Process ${result.status.toUpperCase()}`
      });
    } catch (error) {
      setCommandFeedback({
        phase: "error",
        label: "stop runtime",
        detail: error instanceof Error ? error.message : "stop failed"
      });
    }
  }

  function confirmCommand(label: string, detail: string, execute: () => Promise<void>): void {
    if (!selectedRunId) return;
    setPendingAction({
      label,
      detail,
      runId: selectedRunId,
      execute
    });
    setCommandFeedback({
      phase: "confirm",
      label,
      detail
    });
  }

  const topBar = snapshot ? (
    <div className="topbar">
      <div><span>Desk</span><strong>{profiles[activeProfileId].name}</strong></div>
      <div><span>Run</span><strong>{runtime?.run_id ?? "none"}</strong></div>
      <div><span>Mode</span><strong>{runtime?.mode ?? "N/A"}</strong></div>
      <div><span>State</span><strong className={badgeClass(connectionState)}>{connectionState}</strong></div>
      <div><span>Stage</span><strong>{runtime?.stage ?? "N/A"}</strong></div>
      <div><span>PnL</span><strong className={classForPnl(snapshot.portfolio.total_pnl)}>{money(snapshot.portfolio.total_pnl)}</strong></div>
      <div><span>Positions</span><strong>{snapshot.portfolio.active_positions ?? positions.length}</strong></div>
      <div><span>Fills</span><strong>{fills.length}</strong></div>
      <div><span>Order Log</span><strong>{orderLog.length}</strong></div>
    </div>
  ) : (
    <div className="topbar topbar-empty">
      <div><span>Desk</span><strong>{profiles[activeProfileId].name}</strong></div>
      <div><span>State</span><strong>{connectionState}</strong></div>
      <div><span>Runtime</span><strong>select a run</strong></div>
    </div>
  );

  return (
    <div className="workspace">
      {topBar}
      <div className="workspace-tabs">
        {(Object.values(profiles) as WorkspaceProfile[]).map((profile) => (
          <div
            key={profile.id}
            className={`workspace-tab ${activeProfileId === profile.id ? "is-active" : ""}`}
          >
            <button
              className="workspace-tab-main"
              onClick={() => setActiveProfileId(profile.id)}
            >
              <span>{profile.name}</span>
              <strong>{profile.focusedPanel.toUpperCase()}</strong>
            </button>
            <button
              className="workspace-tab-popout"
              onClick={() => void openProfileWindow(profile.id)}
              title={`Open ${profile.name} in a separate window`}
            >
              Open
            </button>
          </div>
        ))}
      </div>

      <aside className="rail">
        <div className="rail-brand">
          <div className="brand-code">PMX-OPS</div>
          <div className="brand-subtitle">RETRO TERMINAL / LOCAL DESK</div>
        </div>

        <div className="rail-section-label">Runtime Tape</div>
        <div className="runtime-tape">
          {runtimes.length === 0 ? (
            <div className="runtime-empty">No runtimes discovered</div>
          ) : runtimes.slice(0, 8).map((item) => (
            <button
              key={item.run_id}
              className={`runtime-card ${selectedRunId === item.run_id ? "is-active" : ""}`}
              onClick={() => setSelectedRunId(item.run_id)}
              title={item.run_id}
            >
              <div className="runtime-card-head">
                <strong>{shortRunId(item.run_id)}</strong>
                <span className={badgeClass(item.mode)}>{item.mode}</span>
              </div>
              <div className="runtime-card-grid">
                <div>
                  <span>Stage</span>
                  <strong>{item.stage}</strong>
                </div>
                <div>
                  <span>PnL</span>
                  <strong className={classForPnl(item.total_pnl)}>{money(item.total_pnl)}</strong>
                </div>
                <div>
                  <span>Market</span>
                  <strong>{item.market ?? "N/A"}</strong>
                </div>
                <div>
                  <span>Updated</span>
                  <strong>{age(item.updated_at_ms ? Date.now() - item.updated_at_ms : null)}</strong>
                </div>
              </div>
            </button>
          ))}
        </div>

        {activeDeskSet.length > 0 ? (
          <>
            <div className="rail-section-label">Active Desk Set</div>
            <div className="desk-set">
              <div className="desk-set-head">
                <span>Latest 4-Run Session</span>
                <strong>{activeDeskSet[0].suite_key.replace("kalshi-paper-4run-sess-", "")}</strong>
              </div>
              <div className="desk-set-grid">
                {activeDeskSet.map((item) => (
                  <button
                    key={item.run_id}
                    className={`desk-set-card ${selectedRunId === item.run_id ? "is-active" : ""}`}
                    onClick={() => setSelectedRunId(item.run_id)}
                    title={item.run_id}
                  >
                    <div className="desk-set-card-head">
                      <strong>{item.short_label}</strong>
                      <span className={badgeClass(item.stage)}>{item.stage}</span>
                    </div>
                    <div className="desk-set-card-grid">
                      <div>
                        <span>PnL</span>
                        <strong className={classForPnl(item.total_pnl)}>{money(item.total_pnl)}</strong>
                      </div>
                      <div>
                        <span>Market</span>
                        <strong>{item.market ?? "N/A"}</strong>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : null}

        <div className="rail-section-label">Controls</div>
        <button onClick={() => void handleStart()}>Start Paper</button>
        <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("stop runtime", "Gracefully stop the managed PAPER runtime", () => performStop(selectedRunId))}>Stop</button>
        <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("pause trading", "Pause quoting and trading activity", () => performCommand(selectedRunId, "pause_trading", {}, "pause trading"))}>Pause</button>
        <button disabled={!selectedRunId} onClick={() => selectedRunId && void performCommand(selectedRunId, "resume_trading", {}, "resume trading")}>Resume</button>
        <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("cancel quotes", "Cancel all currently open quotes", () => performCommand(selectedRunId, "cancel_all_quotes", {}, "cancel quotes"))}>Cancel Quotes</button>
        <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("flatten market", `Flatten inventory for ${snapshot?.market.selected_market ?? "selected market"}`, () => performCommand(selectedRunId, "flatten_market_inventory", { market_id: snapshot?.market.selected_market }, "flatten market"))}>Flatten Market</button>
        <button className="button-bad" disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("kill switch", "Turn on kill switch and halt trading immediately", () => performCommand(selectedRunId, "kill_switch_on", {}, "kill switch"))}>Kill Switch</button>
        <button onClick={() => setCommandPalette(true)}>Palette</button>

        <div className="rail-status">
          <div><span>Selected Run</span><strong>{selectedRuntimeSummary ? shortRunId(selectedRuntimeSummary.run_id) : "N/A"}</strong></div>
          <div><span>Scope</span><strong>{scopeLabel(marketSelection?.launch_scope ?? "single_market")}</strong></div>
          <div><span>Quoteable</span><strong className={badgeClass(snapshot?.market.quoteable ? "quoteable" : "down")}>{snapshot?.market.quoteable ? "YES" : "NO"}</strong></div>
          <div><span>Kill</span><strong className={badgeClass(snapshot?.controls.kill_switch_enabled ? "on" : "off")}>{snapshot?.controls.kill_switch_enabled ? "ON" : "OFF"}</strong></div>
          <div><span>Feed</span><strong className={badgeClass(snapshot?.health.feed_connected ? "live" : "down")}>{snapshot?.health.feed_connected ? "LIVE" : "DOWN"}</strong></div>
          <div><span>Book</span><strong>{snapshot?.market.book_health ?? "N/A"}</strong></div>
          <div><span>Market</span><strong>{snapshot?.market.selected_market ?? "N/A"}</strong></div>
          <div><span>Switches</span><strong>{String(sessionSelection?.market_change_count ?? 0)}</strong></div>
          <div><span>Updated</span><strong>{age(snapshot?.health.last_update_age_ms)}</strong></div>
        </div>

        <div className="rail-ack">
          <div className="rail-section-label">Command State</div>
          <div className="ack-status">
            <span className={badgeClass(commandFeedback.phase)}>{commandFeedback.phase}</span>
            <strong>{commandFeedback.label}</strong>
            <p>{commandFeedback.detail}</p>
          </div>
        </div>
      </aside>

      <main className="grid">
        <section className={`hero ${focusedPanel === "hero" ? "panel-focus" : ""}`} onClick={() => setFocusedPanel("hero")}>
          <Panel title="P&L Overview" subtitle="normalized session state">
            <div className="hero-pnl">
              <div className={`hero-total ${classForPnl(snapshot?.portfolio.total_pnl)}`}>
                {money(snapshot?.portfolio.total_pnl)}
              </div>
              <div className="hero-subgrid">
                <div>
                  <span>Realized</span>
                  <strong className={classForPnl(snapshot?.portfolio.realized_net_pnl)}>{money(snapshot?.portfolio.realized_net_pnl)}</strong>
                </div>
                <div>
                  <span>Unrealized</span>
                  <strong className={classForPnl(snapshot?.portfolio.unrealized_pnl)}>{money(snapshot?.portfolio.unrealized_pnl)}</strong>
                </div>
                <div>
                  <span>Gross Exposure</span>
                  <strong>{decimal(snapshot?.portfolio.gross_exposure, 1)}</strong>
                </div>
                <div>
                  <span>Pending Commands</span>
                  <strong>{snapshot?.controls.pending_command_count ?? 0}</strong>
                </div>
              </div>
            </div>

            <div className="chart-strip">
              <div className="chart-card">
                <div className="chart-meta">
                  <span>PnL Strip</span>
                  <strong className={classForPnl(snapshot?.portfolio.total_pnl)}>{money(snapshot?.portfolio.total_pnl)}</strong>
                </div>
                <svg viewBox="0 0 320 48" className="mini-chart" preserveAspectRatio="none">
                  <path d={pnlPath} className="chart-line chart-line-pnl" />
                </svg>
              </div>
              <div className="chart-card">
                <div className="chart-meta">
                  <span>Exposure Strip</span>
                  <strong>{decimal(snapshot?.portfolio.gross_exposure, 1)}</strong>
                </div>
                <svg viewBox="0 0 320 48" className="mini-chart" preserveAspectRatio="none">
                  <path d={exposurePath} className="chart-line chart-line-exposure" />
                </svg>
              </div>
            </div>

            <div className="workspace-controls">
              <div className="workspace-control-group">
                <span>Chart Range</span>
                <div className="workspace-chip-row">
                  {[15, 30, 60, 120].map((value) => (
                    <button
                      key={value}
                      className={`chip-button ${chartRange === value ? "is-active" : ""}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        setChartRange(value as ChartRange);
                      }}
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </div>
              <div className="workspace-control-group">
                <span>Focus</span>
                <div className="workspace-chip-row">
                  {[
                    ["hero", "PNL"],
                    ["positions", "POS"],
                    ["fills", "FILLS"],
                    ["timeline", "TL"],
                    ["orderlog", "LOG"],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      className={`chip-button ${focusedPanel === id ? "is-active" : ""}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        setFocusedPanel(id as PanelId);
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="hero-state-grid">
              <div className="mini-stat">
                <span>Selected Market</span>
                <strong>{snapshot?.market.selected_market ?? "N/A"}</strong>
              </div>
              <div className="mini-stat">
                <span>Selection Reason</span>
                <strong>{snapshot?.market.selected_reason ?? "N/A"}</strong>
              </div>
              <div className="mini-stat">
                <span>Trading</span>
                <strong className={badgeClass(snapshot?.controls.trading_enabled ? "live" : "paused")}>
                  {snapshot?.controls.trading_enabled ? "ENABLED" : "PAUSED"}
                </strong>
              </div>
              <div className="mini-stat">
                <span>Flatten Only</span>
                <strong className={badgeClass(snapshot?.controls.flatten_only_mode ? "flatten" : "live")}>
                  {snapshot?.controls.flatten_only_mode ? "ON" : "OFF"}
                </strong>
              </div>
              <div className="mini-stat">
                <span>Freeze Reasons</span>
                <strong>{snapshot?.health.freeze_reasons?.length ? snapshot.health.freeze_reasons.join(", ") : "NONE"}</strong>
              </div>
              <div className="mini-stat">
                <span>Service Managed</span>
                <strong className={badgeClass(snapshot?.runtime.service_managed ? "live" : "pending")}>
                  {snapshot?.runtime.service_managed ? "YES" : "NO"}
                </strong>
              </div>
            </div>

            <div className="hero-strip">
              <div className="mini-stat">
                <span>Latest Decision</span>
                <strong>{String(currentDecision?.action ?? latestDecision?.action ?? "N/A")}</strong>
              </div>
              <div className="mini-stat">
                <span>Buy Limiter</span>
                <strong>{String(currentDecision?.buy_limiter ?? latestSizePlan.buy_limiter ?? "N/A")}</strong>
              </div>
              <div className="mini-stat">
                <span>Sell Limiter</span>
                <strong>{String(currentDecision?.sell_limiter ?? latestSizePlan.sell_limiter ?? "N/A")}</strong>
              </div>
              <div className="mini-stat">
                <span>Risk Action</span>
                <strong>{String(currentDecision?.risk_action ?? latestRisk.action ?? "NORMAL")}</strong>
              </div>
            </div>

            <div className="hero-state-grid">
              <div className="mini-stat">
                <span>Launch Scope</span>
                <strong>{scopeLabel(marketSelection?.launch_scope ?? "single_market")}</strong>
              </div>
              <div className="mini-stat">
                <span>Max Active</span>
                <strong>{String(marketSelection?.max_active_markets ?? 1)}</strong>
              </div>
              <div className="mini-stat">
                <span>Episodes</span>
                <strong>{String(sessionSelection?.episode_count ?? 0)}</strong>
              </div>
              <div className="mini-stat">
                <span>Switches</span>
                <strong>{String(sessionSelection?.market_change_count ?? 0)}</strong>
              </div>
              <div className="mini-stat">
                <span>Current Episode Age</span>
                <strong>{age(snapshot?.health.last_update_age_ms !== undefined && sessionSelection?.current_episode_started_at_ms ? Date.now() - Number(sessionSelection.current_episode_started_at_ms) : null)}</strong>
              </div>
              <div className="mini-stat">
                <span>Latest Fee Source</span>
                <strong>{String(latestFillFee?.fee_source ?? "N/A")}</strong>
              </div>
            </div>

            <div className="workspace-controls workspace-controls-tight">
              <div className="workspace-control-group">
                <span>Why This Market</span>
                <div className="hero-state-grid">
                  <div className="mini-stat">
                    <span>Winner</span>
                    <strong>{String((marketSelection?.selected_market as Record<string, unknown> | undefined)?.ticker ?? snapshot?.market.selected_market ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Reason</span>
                    <strong>{String(marketSelection?.selected_reason ?? snapshot?.market.selected_reason ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Score</span>
                    <strong>{decimal(numeric((marketSelection?.selected_market as Record<string, unknown> | undefined)?.score), 3)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Spread</span>
                    <strong>{decimal(numeric((marketSelection?.selected_market as Record<string, unknown> | undefined)?.spread), 3)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Liquidity</span>
                    <strong>{decimal(numeric((marketSelection?.selected_market as Record<string, unknown> | undefined)?.liquidity_score), 3)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Transition Risk</span>
                    <strong>{decimal(numeric((marketSelection?.selected_market as Record<string, unknown> | undefined)?.transition_risk), 3)}</strong>
                  </div>
                </div>
                <table className="terminal-table">
                  <thead>
                    <tr>
                      <th>Accepted</th>
                      <th>Score</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(marketSelection?.accepted_candidates?.length ?? 0) > 0 ? marketSelection?.accepted_candidates.map((candidate, index) => (
                      <tr key={`accepted-${candidate.ticker ?? index}`}>
                        <td>{candidate.ticker ?? "N/A"}</td>
                        <td>{decimal(candidate.score, 3)}</td>
                        <td>{candidate.reason ?? candidate.quoteability_state ?? "N/A"}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan={3} className="empty-row">No accepted candidates</td></tr>
                    )}
                  </tbody>
                </table>
                <table className="terminal-table">
                  <thead>
                    <tr>
                      <th>Rejected</th>
                      <th>Score</th>
                      <th>Block</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(marketSelection?.rejected_candidates?.length ?? 0) > 0 ? marketSelection?.rejected_candidates.map((candidate, index) => (
                      <tr key={`rejected-${candidate.ticker ?? index}`}>
                        <td>{candidate.ticker ?? "N/A"}</td>
                        <td>{decimal(candidate.score, 3)}</td>
                        <td>{candidate.blocking_reason ?? candidate.reason ?? candidate.quoteability_state ?? "N/A"}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan={3} className="empty-row">No rejected candidates</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="workspace-controls workspace-controls-tight">
              <div className="workspace-control-group">
                <span>Current Decision</span>
                <div className="hero-state-grid">
                  <div className="mini-stat">
                    <span>Action</span>
                    <strong>{String(currentDecision?.action ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>P Fair</span>
                    <strong>{decimal(currentDecision?.p_fair, 4)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Expected Edge</span>
                    <strong>{decimal(currentDecision?.expected_edge, 4)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Expected Cost</span>
                    <strong>{decimal(currentDecision?.expected_cost, 4)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Fee Model</span>
                    <strong>{String(currentDecision?.fee_type ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Fee Multiplier</span>
                    <strong>{decimal(currentDecision?.fee_multiplier, 3)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Buy Size</span>
                    <strong>{decimal(currentDecision?.buy_amount, 2)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Sell Size</span>
                    <strong>{decimal(currentDecision?.sell_amount, 2)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Buy Limiters</span>
                    <strong>{String(currentDecision?.buy_limiters ?? currentDecision?.buy_limiter ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Sell Limiters</span>
                    <strong>{String(currentDecision?.sell_limiters ?? currentDecision?.sell_limiter ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Risk State</span>
                    <strong>{String(currentDecision?.risk_state ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Hedge Action</span>
                    <strong>{String(currentDecision?.hedge_action ?? "N/A")}</strong>
                  </div>
                </div>
              </div>
            </div>

            <div className="workspace-controls workspace-controls-tight">
              <div className="workspace-control-group">
                <span>Session Rotation</span>
                <div className="hero-state-grid">
                  <div className="mini-stat">
                    <span>Previous Market</span>
                    <strong>{String(sessionSelection?.previous_market ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Latest Switch Reason</span>
                    <strong>{String(sessionSelection?.latest_switch_reason ?? "N/A")}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Fill Count</span>
                    <strong>{String(sessionPerformance?.fill_count ?? 0)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Cumulative Fees</span>
                    <strong>{money(sessionPerformance?.cumulative_fees)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Turnover</span>
                    <strong>{decimal(sessionPerformance?.turnover, 1)}</strong>
                  </div>
                  <div className="mini-stat">
                    <span>Max Drawdown</span>
                    <strong>{money(sessionPerformance?.max_drawdown_abs)}</strong>
                  </div>
                </div>
                <table className="terminal-table">
                  <thead>
                    <tr>
                      <th>Top Market</th>
                      <th>Decisions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(sessionSelection?.top_markets_by_decision_count?.length ?? 0) > 0 ? sessionSelection?.top_markets_by_decision_count.map((row, index) => (
                      <tr key={`top-market-${row.market ?? index}`}>
                        <td>{row.market ?? "N/A"}</td>
                        <td>{String(row.decision_count ?? 0)}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan={2} className="empty-row">No market episodes yet</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </Panel>
        </section>

        <section className={`positions-panel ${focusedPanel === "positions" ? "panel-focus" : ""}`} onClick={() => setFocusedPanel("positions")}>
          <Panel title="Positions" subtitle="live inventory">
            <table className="terminal-table">
              <thead>
                <tr>
                  <th>Token</th>
                  <th>Qty</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 ? (
                  <tr><td colSpan={3} className="empty-row">No positions</td></tr>
                ) : positions.map((item, index) => {
                  const row = item as Record<string, unknown>;
                  return (
                    <tr key={`${row.token_id ?? index}`}>
                      <td>{String(row.token_id ?? "")}</td>
                      <td>{qty(row.yes_qty)}</td>
                      <td>{timeLabel(row.ts_ms)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
        </section>

        <section className={`fills-panel ${focusedPanel === "fills" ? "panel-focus" : ""}`} onClick={() => setFocusedPanel("fills")}>
          <Panel title="Fills" subtitle="recent executions">
            <table className="terminal-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Px</th>
                  <th>Market</th>
                  <th>Risk</th>
                </tr>
              </thead>
              <tbody>
                {fills.length === 0 ? (
                  <tr><td colSpan={6} className="empty-row">No fills</td></tr>
                ) : fills.map((item, index) => {
                  const row = item as Record<string, unknown>;
                  return (
                    <tr key={`${row.order_id ?? index}`}>
                      <td>{timeLabel(row.ts_ms)}</td>
                      <td className={sideClass(row.side)}>{String(row.side ?? "").toUpperCase()}</td>
                      <td>{String(row.fill_qty ?? "")}</td>
                      <td>{String(row.fill_price ?? "")}</td>
                      <td>{String(row.market_slug ?? "")}</td>
                      <td>{String(row.risk_action ?? "NORMAL")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
        </section>

        <section className={`orders-panel ${focusedPanel === "timeline" ? "panel-focus" : ""}`} onClick={() => setFocusedPanel("timeline")}>
          <Panel title="Blotter / Timeline" subtitle="J/K or arrows to move">
            <div className="workspace-controls workspace-controls-tight">
              <div className="workspace-control-group">
                <span>Timeline Filters</span>
                <div className="workspace-chip-row">
                  {(["fill", "command", "decision", "alert", "order", "switch"] as TimelineLane[]).map((lane) => (
                    <button
                      key={lane}
                      className={`chip-button ${timelineFilters[lane] ? "is-active" : ""}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleTimelineFilter(lane);
                      }}
                    >
                      {lane}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="timeline-layout">
              <div className="timeline-list" tabIndex={0}>
                {timeline.length === 0 ? (
                  <div className="timeline-empty">No history yet</div>
                ) : timeline.map((entry, index) => (
                  <button
                    key={entry.key}
                    className={`${laneClass(entry.lane)} ${timelineIndex === index ? "is-selected" : ""}`}
                    onClick={() => setTimelineIndex(index)}
                  >
                    <div className="timeline-head">
                      <span className={badgeClass(entry.status)}>{entry.status}</span>
                      <span>{timeLabel(entry.ts_ms)}</span>
                    </div>
                    <strong>{entry.title}</strong>
                    <div>{entry.detail}</div>
                  </button>
                ))}
              </div>
              <div className="timeline-detail">
                {selectedTimeline ? (
                  <>
                    <div className="detail-row"><span>Lane</span><strong>{selectedTimeline.lane.toUpperCase()}</strong></div>
                    <div className="detail-row"><span>Time</span><strong>{timeLabel(selectedTimeline.ts_ms)}</strong></div>
                    <div className="detail-row"><span>Status</span><strong className={badgeClass(selectedTimeline.status)}>{selectedTimeline.status}</strong></div>
                    <div className="detail-row"><span>Title</span><strong>{selectedTimeline.title}</strong></div>
                    <div className="detail-block">
                      <span>Detail</span>
                      <p>{selectedTimeline.detail || "N/A"}</p>
                    </div>
                    <div className="detail-block">
                      <span>Ref</span>
                      <p>{selectedTimeline.meta || "N/A"}</p>
                    </div>
                  </>
                ) : (
                  <div className="timeline-empty">Select an entry</div>
                )}
              </div>
            </div>
          </Panel>
        </section>

        <section className={`alerts-panel ${focusedPanel === "orderlog" ? "panel-focus" : ""}`} onClick={() => setFocusedPanel("orderlog")}>
          <Panel title="Order Log" subtitle="orders + commands">
            <table className="terminal-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {orderLog.length === 0 ? (
                  <tr><td colSpan={4} className="empty-row">No orders or commands</td></tr>
                ) : orderLog.map((row) => (
                  <tr key={row.key}>
                    <td>{timeLabel(row.ts_ms)}</td>
                    <td>{row.title}</td>
                    <td><span className={badgeClass(row.status)}>{row.status}</span></td>
                    <td>{row.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </section>
      </main>

      {pendingAction ? (
        <div className="palette-backdrop" onClick={() => setPendingAction(null)}>
          <div className="palette confirm-modal" onClick={(event) => event.stopPropagation()}>
            <div className="palette-title">Confirm Action</div>
            <div className="palette-subtitle">{pendingAction.label}</div>
            <p className="confirm-copy">{pendingAction.detail}</p>
            <button
              onClick={() => {
                const action = pendingAction;
                setPendingAction(null);
                void action.execute();
              }}
            >
              Confirm
            </button>
            <button onClick={() => setPendingAction(null)}>Cancel</button>
          </div>
        </div>
      ) : null}

      {commandPalette ? (
        <div className="palette-backdrop" onClick={() => setCommandPalette(false)}>
          <div className="palette" onClick={(event) => event.stopPropagation()}>
            <div className="palette-title">Desk Commands</div>
            <div className="palette-subtitle">Ctrl/Cmd+K | J/K timeline</div>
            <button onClick={() => void handleStart()}>Start Paper Runtime</button>
            <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("pause trading", "Pause quoting and trading activity", () => performCommand(selectedRunId, "pause_trading", {}, "pause trading"))}>Pause Trading</button>
            <button disabled={!selectedRunId} onClick={() => selectedRunId && void performCommand(selectedRunId, "resume_trading", {}, "resume trading")}>Resume Trading</button>
            <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("cancel quotes", "Cancel all currently open quotes", () => performCommand(selectedRunId, "cancel_all_quotes", {}, "cancel quotes"))}>Cancel All Quotes</button>
            <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("flatten market", `Flatten inventory for ${snapshot?.market.selected_market ?? "selected market"}`, () => performCommand(selectedRunId, "flatten_market_inventory", { market_id: snapshot?.market.selected_market }, "flatten market"))}>Flatten Market</button>
            <button className="button-bad" disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("kill switch", "Turn on kill switch and halt trading immediately", () => performCommand(selectedRunId, "kill_switch_on", {}, "kill switch"))}>Kill Switch</button>
            <button disabled={!selectedRunId} onClick={() => selectedRunId && confirmCommand("stop runtime", "Gracefully stop the managed PAPER runtime", () => performStop(selectedRunId))}>Stop Runtime</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
