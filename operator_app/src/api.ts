export type RuntimeSummary = {
  run_id: string;
  runtime_root: string;
  db_path: string;
  mode: string;
  stage: string;
  market?: string | null;
  strategy_name?: string | null;
  updated_at_ms?: number | null;
  quoteable?: boolean | null;
  total_pnl?: number | null;
  managed: boolean;
  pid?: number | null;
  started_at_ms?: number | null;
};

export type SelectionCandidate = {
  ticker?: string | null;
  title?: string | null;
  reason?: string | null;
  quoteability_state?: string | null;
  score?: number | null;
  liquidity_score?: number | null;
  transition_risk?: number | null;
  spread?: number | null;
  mid?: number | null;
  blocking_reason?: string | null;
};

export type CandidateDecision = {
  allowed?: boolean;
  market_id?: string | null;
  cluster_id?: string | null;
  reason?: string | null;
};

export type SessionEpisode = {
  ts_ms?: number | null;
  market?: string | null;
  reason?: string | null;
};

export type OperatorSnapshot = {
  runtime: {
    run_id: string;
    mode: string;
    stage: string;
    started_at_ms?: number | null;
    pid?: number | null;
    runtime_root: string;
    db_path: string;
    service_managed: boolean;
  };
  controls: {
    trading_enabled: boolean;
    flatten_only_mode: boolean;
    kill_switch_enabled: boolean;
    pending_command_count: number;
    last_applied: Record<string, unknown>;
  };
  portfolio: {
    realized_net_pnl?: number | null;
    unrealized_pnl?: number | null;
    total_pnl?: number | null;
    gross_exposure?: number | null;
    active_positions?: number | null;
    positions: Array<Record<string, unknown>>;
  };
  market: {
    selected_market?: string | null;
    quoteable?: boolean | null;
    book_health?: string | null;
    selected_reason?: string | null;
    selection: {
      launch_scope?: string | null;
      max_active_markets?: number | null;
      selected_market?: Record<string, unknown>;
      selected_reason?: string | null;
      accepted_candidates: SelectionCandidate[];
      rejected_candidates: SelectionCandidate[];
      candidate_decisions: CandidateDecision[];
    };
  };
  decision: {
    current: {
      market?: string | null;
      token_id?: string | null;
      action?: string | null;
      reason_codes?: string | null;
      p_fair?: number | null;
      expected_edge?: number | null;
      expected_cost?: number | null;
      fee_type?: string | null;
      fee_multiplier?: number | null;
      buy_amount?: number | null;
      sell_amount?: number | null;
      buy_limiter?: string | null;
      sell_limiter?: string | null;
      buy_limiters?: string | null;
      sell_limiters?: string | null;
      risk_action?: string | null;
      risk_state?: string | null;
      hedge_action?: string | null;
      ts_ms?: number | null;
    };
  };
  session: {
    selection: {
      episode_count?: number | null;
      market_change_count?: number | null;
      current_episode_started_at_ms?: number | null;
      previous_market?: string | null;
      latest_switch_reason?: string | null;
      top_markets_by_decision_count: Array<{ market?: string | null; decision_count?: number | null }>;
      top_switch_reasons: Array<{ reason?: string | null; count?: number | null }>;
      recent_episodes: SessionEpisode[];
    };
    performance: {
      fill_count?: number | null;
      distinct_orders?: number | null;
      turnover?: number | null;
      cumulative_fees?: number | null;
      max_drawdown_abs?: number | null;
      max_drawdown_pct_peak?: number | null;
      control_state_counts: Record<string, number>;
      risk_action_counts: Record<string, number>;
      hedge_action_counts: Record<string, number>;
      latest_fill_fee: {
        fee_source?: string | null;
        fee_type?: string | null;
        fee_multiplier?: number | null;
        realized_net_pnl_delta?: number | null;
      };
      realized_net_pnl?: number | null;
      unrealized_pnl?: number | null;
      total_pnl?: number | null;
    };
  };
  health: {
    feed_connected: boolean;
    last_update_age_ms?: number | null;
    state?: string | null;
    freeze_reasons: string[];
  };
  monitor: {
    source?: string | null;
    path?: string | null;
    last_check_ts_ms?: number | null;
    warning_level?: string | null;
    summary?: Record<string, unknown>;
  };
  recent: {
    fills: Array<Record<string, unknown>>;
    decisions: Array<Record<string, unknown>>;
    alerts: Array<Record<string, unknown>>;
    commands: Array<Record<string, unknown>>;
    open_orders: Array<Record<string, unknown>>;
  };
};

export type OperatorEvent = {
  event: string;
  data: unknown;
};

export type HistoryPoint = {
  ts_ms: number;
  total_pnl: number | null;
  gross_exposure: number | null;
};

export type CommandResponse = {
  command_id: string;
  status?: string;
};

export type StopRuntimeResponse = {
  run_id: string;
  status: string;
  pid?: number | null;
};

const BASE_URL = "http://127.0.0.1:8765";

export async function fetchRuntimes(): Promise<RuntimeSummary[]> {
  const response = await fetch(`${BASE_URL}/api/runtimes`);
  const payload = await response.json();
  return payload.runtimes ?? [];
}

export async function fetchSnapshot(runId: string): Promise<OperatorSnapshot> {
  const response = await fetch(`${BASE_URL}/api/runtimes/${runId}/snapshot`);
  return response.json();
}

export async function fetchHistory(runId: string, points = 60): Promise<HistoryPoint[]> {
  const response = await fetch(`${BASE_URL}/api/runtimes/${runId}/history?points=${points}`);
  const payload = await response.json();
  return payload.points ?? [];
}

export async function startRuntime(payload: Record<string, unknown>): Promise<RuntimeSummary> {
  const response = await fetch(`${BASE_URL}/api/runtimes/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const json = await response.json();
  return json.runtime;
}

export async function stopRuntime(runId: string): Promise<StopRuntimeResponse> {
  const response = await fetch(`${BASE_URL}/api/runtimes/${runId}/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_kill_after_ms: 5000 })
  });
  return response.json();
}

export async function sendCommand(runId: string, command_type: string, payload: Record<string, unknown> = {}): Promise<CommandResponse> {
  const response = await fetch(`${BASE_URL}/api/runtimes/${runId}/commands`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command_type, payload, requested_by: "operator_app" })
  });
  return response.json();
}

export function openRuntimeSocket(runId: string, onEvent: (event: OperatorEvent) => void): WebSocket {
  const socket = new WebSocket(`ws://127.0.0.1:8765/ws/runtimes/${runId}`);
  socket.onmessage = (message) => onEvent(JSON.parse(message.data) as OperatorEvent);
  return socket;
}
