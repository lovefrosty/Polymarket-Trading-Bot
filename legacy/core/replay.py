from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.decision_engine import DecisionEngine
from core.decision_tape import DecisionTape, TimeMapper
from core.metrics import Metrics
from core.order_book import OrderBook
from core.model_artifact import ModelArtifact
from core.onchain_signals import OnchainSignalState
from core.reference_price import ReferencePriceAggregator, parse_reference_event
from core.reference_store import ReferenceStore
from core.trade_tape_replayer import TradeTapeReplayResult, TradeTapeReplayer
from core.validators import OrderConstraints
from data.polymarket_ws import MarketWSClient, WSConfig


@dataclass(frozen=True)
class ReplayConfig:
    heartbeat_interval_ns: int = 1_000_000_000


class _NullTape:
    def write(self, *args, **kwargs) -> None:
        return None

    def close(self) -> None:
        return None


class ReplayRunner:
    def __init__(
        self,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        decision_tape: DecisionTape,
        order_size: float,
        fee_rate: float,
        fee_mode: str,
        market_meta: Optional[Dict[str, Dict[str, Any]]] = None,
        model_artifact: Optional[ModelArtifact] = None,
        model_path: Optional[str] = None,
        model_load_error: Optional[str] = None,
        reference_store: Optional[ReferenceStore] = None,
        reference_settings: Optional[Dict[str, object]] = None,
        policy_settings: Optional[Dict[str, object]] = None,
        onchain_whales: Optional[set[str]] = None,
        onchain_window_secs: float = 60.0,
    ) -> None:
        self.books = books
        self.constraints = constraints
        self.decision_tape = decision_tape
        self.metrics = Metrics()
        self.market_client = MarketWSClient(
            asset_ids=list(books.keys()),
            books=books,
            tape=_NullTape(),
            metrics=self.metrics,
            config=WSConfig(reconnect_base_ms=0, reconnect_max_ms=0),
            decision_engine=None,
        )
        self._time_mapper: Optional[TimeMapper] = None
        self._decision_engine: Optional[DecisionEngine] = None
        self._order_size = order_size
        self._fee_rate = fee_rate
        self._fee_mode = fee_mode
        self._market_meta = market_meta or {}
        self._model_artifact = model_artifact
        self._model_path = model_path
        self._model_load_error = model_load_error
        self._reference_store = reference_store
        self._reference_settings = reference_settings or {}
        self._reference_aggregator: Optional[ReferencePriceAggregator] = None
        self._policy_settings = policy_settings or {}
        self._onchain_whales = onchain_whales or set()
        self._onchain_window_secs = onchain_window_secs
        self._trade_tape_replayer = TradeTapeReplayer()
        self.trade_replay_result: Optional[TradeTapeReplayResult] = None

    def run(self, event_files: Iterable[str], trade_tape_files: Optional[Iterable[str]] = None) -> Optional[TradeTapeReplayResult]:
        events = self._load_events(event_files)
        if not events:
            if trade_tape_files:
                self.trade_replay_result = self._trade_tape_replayer.replay(trade_tape_files)
            return self.trade_replay_result
        first = events[0]
        wall_ms = _parse_wall_ms(first.get("t_recv_wall_iso"))
        mono_ns = int(first.get("t_recv_mono_ns", 0))
        self._time_mapper = TimeMapper.from_wall_and_mono(wall_ms=wall_ms, mono_ns=mono_ns)
        self._reference_aggregator = ReferencePriceAggregator(
            required_sources={"spot", "perp"},
            staleness_ms=int(self._reference_settings.get("staleness_ms", 5000)),
            disagreement_bps=float(self._reference_settings.get("disagreement_bps", 50.0)),
            min_confidence=float(self._reference_settings.get("min_confidence", 0.5)),
            allow_partial=bool(self._reference_settings.get("allow_partial", False)),
            partial_confidence=float(self._reference_settings.get("partial_confidence", 0.6)),
            disagreement_bps_soft=float(
                self._reference_settings.get("disagreement_bps_soft", self._reference_settings.get("disagreement_bps", 50.0))
            ),
            disagreement_bps_hard=float(
                self._reference_settings.get("disagreement_bps_hard", self._reference_settings.get("disagreement_bps", 50.0))
            ),
            disagreement_decay_k=float(self._reference_settings.get("disagreement_decay_k", 1.0)),
            allowed_symbols=self._reference_settings.get("allowed_symbols"),
        )
        onchain_state = OnchainSignalState(
            window_secs=self._onchain_window_secs,
            whales=self._onchain_whales,
        )
        self._decision_engine = DecisionEngine(
            books=self.books,
            constraints=self.constraints,
            tape=self.decision_tape,
            time_mapper=self._time_mapper,
            config=_decision_config(
                order_size=self._order_size,
                fee_rate=self._fee_rate,
                fee_mode=self._fee_mode,
                ref_half_life_sec=float(self._reference_settings.get("hl_vol_sec", 120.0)),
                reference_lag_guard_ms=int(self._reference_settings.get("lag_guard_ms", 0)),
                reference_staleness_ms=int(self._reference_settings.get("staleness_ms", 5000)),
                policy_settings=self._policy_settings,
            ),
            market_meta=self._market_meta,
            reference_aggregator=self._reference_aggregator,
            model_artifact=self._model_artifact,
            model_path=self._model_path,
            model_load_error=self._model_load_error,
            reference_store=self._reference_store,
            onchain_state=onchain_state,
        )
        self.market_client.decision_engine = self._decision_engine

        loop, loop_created = _ensure_loop()
        last_mono_ns = mono_ns
        try:
            for record in events:
                mono_ns = int(record.get("t_recv_mono_ns", 0))
                wall_iso = record.get("t_recv_wall_iso") or ""
                wall_ms = _parse_wall_ms(wall_iso)
                channel = record.get("channel")
                if channel == "reference":
                    if self._reference_aggregator is not None:
                        quote = parse_reference_event(
                            record.get("raw"),
                            mono_ns,
                            wall_iso,
                            record.get("t_recv_wall_ms"),
                        )
                        if quote is not None:
                            self._reference_aggregator.ingest(quote)
                            if self._decision_engine is not None:
                                self._decision_engine.on_reference_event(quote)
                    if self._reference_store is not None:
                        self._reference_store.ingest_record(record)
                    continue
                if channel == "onchain":
                    onchain_state.ingest_record(record)
                    continue
                if channel != "market":
                    continue
                if self._decision_engine is not None:
                    self._decision_engine.emit_heartbeats_until(mono_ns)
                raw = record.get("raw")
                if raw is None:
                    continue
                raw_str = json.dumps(raw, separators=(",", ":"), ensure_ascii=True)
                loop.run_until_complete(self.market_client._handle_message(raw_str, mono_ns, wall_ms, wall_iso))
                last_mono_ns = mono_ns

            if self._decision_engine is not None:
                self._decision_engine.emit_heartbeats_until(last_mono_ns + 1_000_000_000)
        finally:
            if loop_created:
                import asyncio

                asyncio.set_event_loop(None)
                loop.close()
        if trade_tape_files:
            self.trade_replay_result = self._trade_tape_replayer.replay(trade_tape_files)
        return self.trade_replay_result

    def replay_trade_tape(self, trade_tape_files: Iterable[str]) -> TradeTapeReplayResult:
        self.trade_replay_result = self._trade_tape_replayer.replay(trade_tape_files)
        return self.trade_replay_result

    def _load_events(self, event_files: Iterable[str]) -> List[Dict[str, object]]:
        records: List[Tuple[int, int, Dict[str, object]]] = []
        idx = 0
        for path in event_files:
            for line in Path(path).read_text().splitlines():
                if not line:
                    continue
                record = json.loads(line)
                mono_ns = int(record.get("t_recv_mono_ns", 0))
                records.append((mono_ns, idx, record))
                idx += 1
        records.sort(key=lambda item: (item[0], item[1]))
        return [record for _, _, record in records]


def _parse_wall_ms(wall_iso: Optional[str]) -> int:
    if not wall_iso:
        return 0
    try:
        if wall_iso.endswith("Z"):
            wall_iso = wall_iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(wall_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _decision_config(
    order_size: float,
    fee_rate: float,
    fee_mode: str,
    ref_half_life_sec: float,
    reference_lag_guard_ms: int,
    reference_staleness_ms: int,
    policy_settings: Dict[str, object],
):
    from core.decision_engine import DecisionEngineConfig

    return DecisionEngineConfig(
        order_size=order_size,
        execution_mode="TAKER_SIM",
        fee_rate=fee_rate,
        fee_mode=fee_mode,
        depth_within_ticks_n=int(policy_settings.get("depth_within_ticks_n", 5)),
        depth_at_notional_target=float(policy_settings.get("depth_at_notional_target", 10.0)),
        ref_half_life_sec=ref_half_life_sec,
        reference_lag_guard_ms=reference_lag_guard_ms,
        reference_staleness_ms=reference_staleness_ms,
        edge_min=float(policy_settings.get("edge_min", 0.015)),
        edge_exit=float(policy_settings.get("edge_exit", 0.00375)),
        edge_stop=float(policy_settings.get("edge_stop", 0.0075)),
        z_mom_min=float(policy_settings.get("z_mom_min", 1.0)),
        t_min_secs=float(policy_settings.get("t_min_secs", 90.0)),
        hold_max_secs=float(policy_settings.get("hold_max_secs", 480.0)),
        vol_pct_hi=float(policy_settings.get("vol_pct_hi", 95.0)),
        edge_min_mult_hivol=float(policy_settings.get("edge_min_mult_hivol", 1.5)),
        tox_max=float(policy_settings.get("tox_max", 0.0008)),
        hedge_min=float(policy_settings.get("hedge_min", 0.0)),
        hedge_max=float(policy_settings.get("hedge_max", 1.0)),
        hedge_required_vol_pct=float(policy_settings.get("hedge_required_vol_pct", 95.0)),
        pf_bias=float(policy_settings.get("pf_bias", 0.0)),
        pf_w_mom=float(policy_settings.get("pf_w_mom", 0.35)),
        pf_w_revert=float(policy_settings.get("pf_w_revert", 0.15)),
        pf_z_clip=float(policy_settings.get("pf_z_clip", 4.0)),
        pf_vol_dampen_enabled=bool(policy_settings.get("pf_vol_dampen_enabled", True)),
        pf_vol_floor=float(policy_settings.get("pf_vol_floor", 0.6)),
    )


def _ensure_loop():
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        return loop, False
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop, True
