from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
import math
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from core_mm.book_manager import BookManager
from core_mm.market_selector import MarketCandidate


@dataclass(frozen=True)
class HedgeTokenDirective:
    token_id: str
    action: str = "NONE"
    cluster_id: Optional[str] = None
    control_state: Optional[str] = None
    action_reason: Optional[str] = None
    hedge_market_id: Optional[str] = None
    hedge_target_token_id: Optional[str] = None
    hedge_target_side: Optional[str] = None
    preferred_side: Optional[str] = None
    hedge_ratio: Optional[float] = None
    extra_skew_ticks: int = 0
    block_buy: bool = False
    block_sell: bool = False
    reduce_only: bool = False
    hedge_quality_score: Optional[float] = None
    hedge_execution_quality_score: Optional[float] = None
    hedge_covariance: Optional[float] = None
    hedge_correlation: Optional[float] = None
    hedge_beta_raw: Optional[float] = None
    hedge_beta: Optional[float] = None
    hedge_beta_shrunk: Optional[float] = None
    hedge_beta_clipped: Optional[float] = None
    hedge_covariance_sample_count: Optional[int] = None
    hedge_covariance_state: Optional[str] = None
    hedge_covariance_confidence: Optional[str] = None
    hedge_pair_score: Optional[float] = None
    hedgeability_tier: Optional[str] = None
    hedge_structural_score: Optional[float] = None
    hedge_covariance_score: Optional[float] = None
    hedge_beta_stability_score: Optional[float] = None
    hedge_execution_availability_score: Optional[float] = None
    hedge_realized_outcome_score: Optional[float] = None
    hedge_relation_confidence_state: Optional[str] = None
    hedge_permission_state: Optional[str] = None
    hedge_rejection_reason: Optional[str] = None
    hedge_model_state: Optional[str] = None
    hedge_realized_improvement_state: Optional[str] = None
    hedge_success_window_ms: Optional[int] = None
    hedge_failed_cooldown_until_ms: Optional[int] = None
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class HedgeClusterPlan:
    cluster_id: str
    action: str
    control_state: str = "NORMAL"
    action_reason: Optional[str] = None
    dominant_side: Optional[str] = None
    hedge_market_id: Optional[str] = None
    hedge_target_token_id: Optional[str] = None
    hedge_target_side: Optional[str] = None
    hedge_ratio: Optional[float] = None
    inventory_market_quality_score: Optional[float] = None
    hedge_quality_score: Optional[float] = None
    hedge_execution_quality_score: Optional[float] = None
    hedge_quality_gap: Optional[float] = None
    hedge_covariance: Optional[float] = None
    hedge_correlation: Optional[float] = None
    hedge_beta_raw: Optional[float] = None
    hedge_beta: Optional[float] = None
    hedge_beta_shrunk: Optional[float] = None
    hedge_beta_clipped: Optional[float] = None
    hedge_covariance_sample_count: Optional[int] = None
    hedge_covariance_state: Optional[str] = None
    hedge_covariance_confidence: Optional[str] = None
    hedge_pair_score: Optional[float] = None
    hedgeability_tier: Optional[str] = None
    hedge_structural_score: Optional[float] = None
    hedge_covariance_score: Optional[float] = None
    hedge_beta_stability_score: Optional[float] = None
    hedge_execution_availability_score: Optional[float] = None
    hedge_realized_outcome_score: Optional[float] = None
    hedge_relation_confidence_state: Optional[str] = None
    hedge_permission_state: Optional[str] = None
    hedge_rejection_reason: Optional[str] = None
    hedge_model_state: str = "unknown"
    hedge_realized_improvement_state: str = "pending"
    candidate_state: str = "deferred"
    hedge_success_window_ms: Optional[int] = None
    hedge_failed_cooldown_until_ms: Optional[int] = None
    rejection_reasons: tuple[str, ...] = ()
    affected_market_ids: tuple[str, ...] = ()
    token_directives: tuple[HedgeTokenDirective, ...] = ()
    pair_relations: tuple[Dict[str, Any], ...] = ()
    candidate_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HedgePairRelation:
    inventory_market_id: Optional[str]
    hedge_market_id: Optional[str]
    cluster_id: str
    underlying_symbol: Optional[str]
    event_family: Optional[str]
    expiry_bucket: Optional[str]
    contract_family: Optional[str]
    structural_score: float
    covariance_score: float
    beta_stability_score: float
    execution_availability_score: float
    realized_outcome_score: float
    pair_score: float
    hedgeability_tier: str
    confidence_state: str
    basis_accumulation_flag: bool
    accepted_hedge_count: int
    successful_hedge_count: int
    failed_hedge_count: int
    execution_observation_count: int
    execution_ok_count: int
    covariance_state: str
    covariance_confidence: str
    execution_state: str
    candidate_state: str
    rejection_reason: Optional[str]
    last_updated_at_ms: int


@dataclass
class _ClusterHedgeRuntimeState:
    last_action: str = "NONE"
    control_state: str = "NORMAL"
    hedge_started_at_ms: Optional[int] = None
    hedge_anchor_abs_net_exposure: Optional[float] = None
    hedge_failed_cooldown_until_ms: Optional[int] = None
    last_dominant_market_unrealized_pnl: Optional[float] = None
    last_realized_improvement_state: str = "pending"
    active_pair_key: Optional[Tuple[str, str]] = None


@dataclass(frozen=True)
class HedgeCovarianceMetrics:
    covariance: Optional[float]
    correlation: Optional[float]
    beta_raw: Optional[float]
    beta: Optional[float]
    beta_shrunk: Optional[float]
    beta_clipped: Optional[float]
    beta_sign_consistency: Optional[float]
    alignment_fraction: Optional[float]
    sample_count: int
    state: str
    confidence: str


@dataclass(frozen=True)
class HedgeExecutionMetrics:
    quality_score: Optional[float]
    state: str


class HedgeEngine:
    def __init__(
        self,
        *,
        skew_threshold_fraction: float = 0.25,
        hedge_threshold_fraction: float = 0.60,
        hedge_requires_stale_inventory: bool = True,
        hedge_quality_must_beat_inventory_market: bool = True,
        min_quality_score: float = 10_000.0,
        hedge_max_temp_gross_increase_fraction: float = 0.10,
        hedge_failure_cooldown_scale: float = 1.0,
        hedge_success_window_ms: int = 5_000,
        negative_pnl_reduce_only_enabled: bool = True,
        negative_pnl_unwind_requires_worsening: bool = True,
        negative_pnl_unwind_requires_stale_or_worsening: bool = True,
        hedge_search_profile: str = "production",
        proof_only_bucket_distance: int = 2,
        proof_only_expiry_slack_ms: int = 60_000,
        proof_only_crypto_symbols: Sequence[str] = ("BTC", "ETH", "SOL", "XRP"),
        hedge_covariance_enabled: bool = True,
        hedge_covariance_window_secs: float = 600.0,
        hedge_covariance_min_samples: int = 5,
        hedge_covariance_min_correlation: float = 0.25,
        hedge_covariance_min_abs_beta: float = 0.05,
        hedge_covariance_beta_clip: float = 1.0,
        hedge_covariance_gate_required: bool = True,
        hedge_covariance_beta_shrinkage: float = 0.35,
        hedge_covariance_max_sample_age_ms: int = 30_000,
        hedge_covariance_max_update_gap_ms: int = 2_000,
        hedge_covariance_boundary_buffer: float = 0.08,
        hedge_covariance_boundary_max_fraction: float = 0.50,
        hedge_covariance_strong_correlation: float = 0.60,
        hedge_covariance_strong_min_samples: int = 8,
        hedge_covariance_stability_ratio_max: float = 3.0,
    ) -> None:
        self._skew_threshold_fraction = float(skew_threshold_fraction)
        self._hedge_threshold_fraction = float(hedge_threshold_fraction)
        self._hedge_requires_stale_inventory = bool(hedge_requires_stale_inventory)
        self._hedge_quality_must_beat_inventory_market = bool(hedge_quality_must_beat_inventory_market)
        self._min_quality_score = float(min_quality_score)
        self._hedge_max_temp_gross_increase_fraction = max(0.0, float(hedge_max_temp_gross_increase_fraction))
        self._hedge_failure_cooldown_scale = max(0.1, float(hedge_failure_cooldown_scale))
        self._hedge_success_window_ms = max(1_000, int(hedge_success_window_ms))
        self._negative_pnl_reduce_only_enabled = bool(negative_pnl_reduce_only_enabled)
        self._negative_pnl_unwind_requires_worsening = bool(negative_pnl_unwind_requires_worsening)
        self._negative_pnl_unwind_requires_stale_or_worsening = bool(negative_pnl_unwind_requires_stale_or_worsening)
        self._hedge_search_profile = str(hedge_search_profile or "production").strip().lower()
        self._proof_only_bucket_distance = max(1, int(proof_only_bucket_distance))
        self._proof_only_expiry_slack_ms = max(0, int(proof_only_expiry_slack_ms))
        self._proof_only_crypto_symbols = tuple(
            str(symbol).strip().upper() for symbol in proof_only_crypto_symbols if str(symbol).strip()
        )
        self._hedge_covariance_enabled = bool(hedge_covariance_enabled)
        self._hedge_covariance_window_ms = max(10_000, int(max(1.0, float(hedge_covariance_window_secs)) * 1000.0))
        self._hedge_covariance_min_samples = max(2, int(hedge_covariance_min_samples))
        self._hedge_covariance_min_correlation = max(0.0, float(hedge_covariance_min_correlation))
        self._hedge_covariance_min_abs_beta = max(0.0, float(hedge_covariance_min_abs_beta))
        self._hedge_covariance_beta_clip = max(0.0, float(hedge_covariance_beta_clip))
        self._hedge_covariance_gate_required = bool(hedge_covariance_gate_required)
        self._hedge_covariance_beta_shrinkage = min(0.95, max(0.0, float(hedge_covariance_beta_shrinkage)))
        self._hedge_covariance_max_sample_age_ms = max(0, int(hedge_covariance_max_sample_age_ms))
        self._hedge_covariance_max_update_gap_ms = max(0, int(hedge_covariance_max_update_gap_ms))
        self._hedge_covariance_boundary_buffer = min(0.49, max(0.0, float(hedge_covariance_boundary_buffer)))
        self._hedge_covariance_boundary_max_fraction = min(1.0, max(0.0, float(hedge_covariance_boundary_max_fraction)))
        self._hedge_covariance_strong_correlation = max(
            self._hedge_covariance_min_correlation,
            float(hedge_covariance_strong_correlation),
        )
        self._hedge_covariance_strong_min_samples = max(
            self._hedge_covariance_min_samples,
            int(hedge_covariance_strong_min_samples),
        )
        self._hedge_covariance_stability_ratio_max = max(1.0, float(hedge_covariance_stability_ratio_max))
        self._cluster_states: Dict[str, _ClusterHedgeRuntimeState] = {}
        self._mid_history_by_token: Dict[str, Deque[Tuple[int, float]]] = {}
        self._pair_execution_stats: Dict[Tuple[str, str], Dict[str, int]] = {}
        self._pair_outcome_stats: Dict[Tuple[str, str], Dict[str, int]] = {}

    def plan(
        self,
        *,
        mode: str,
        now_ms: int,
        cluster_payload: Dict[str, Any],
        active_markets: Sequence[MarketCandidate],
        hedge_search_markets: Optional[Sequence[MarketCandidate]] = None,
        book_manager: BookManager,
    ) -> Dict[str, Any]:
        history_markets = tuple(hedge_search_markets or active_markets)
        self._record_mid_history(now_ms=now_ms, active_markets=history_markets, book_manager=book_manager)
        plans: List[HedgeClusterPlan] = []
        directives_by_token: Dict[str, HedgeTokenDirective] = {}
        directives_by_market: Dict[str, Dict[str, Any]] = {}
        active_by_cluster: Dict[str, List[MarketCandidate]] = {}
        for market in active_markets:
            cluster_id = self._cluster_id_for_market(market)
            active_by_cluster.setdefault(cluster_id, []).append(market)
        hedge_search_by_cluster: Dict[str, List[MarketCandidate]] = {}
        for market in history_markets:
            cluster_id = self._cluster_id_for_market(market)
            hedge_search_by_cluster.setdefault(cluster_id, []).append(market)

        live_mode = str(mode).upper() != "PAPER"
        for cluster in list(cluster_payload.get("clusters") or []):
            cluster_id = str(cluster.get("cluster_id") or "")
            if not cluster_id:
                continue
            cluster_state = self._cluster_states.setdefault(cluster_id, _ClusterHedgeRuntimeState())
            if live_mode:
                plans.append(
                    HedgeClusterPlan(
                        cluster_id=cluster_id,
                        action="NONE",
                        control_state="NORMAL",
                        action_reason="paper_only",
                        rejection_reasons=("paper_only",),
                    )
                )
                continue
            net_yes = float(cluster.get("net_yes_exposure_notional") or 0.0)
            abs_net_yes = abs(net_yes)
            cap = float(
                cluster.get("max_event_exposure_notional")
                or max(abs_net_yes, float(cluster.get("gross_exposure") or 0.0), 1.0)
            )
            skew_trigger = max(1.0, cap * self._skew_threshold_fraction)
            hedge_trigger = max(1.0, cap * self._hedge_threshold_fraction)
            normalize_trigger = max(0.0, cap * 0.15)
            dominant_side = "yes" if net_yes > 0 else ("no" if net_yes < 0 else None)
            if dominant_side is None or abs_net_yes < skew_trigger:
                cluster_state.last_action = "NONE"
                cluster_state.control_state = "NORMAL"
                cluster_state.hedge_started_at_ms = None
                cluster_state.hedge_anchor_abs_net_exposure = None
                cluster_state.last_dominant_market_unrealized_pnl = None
                plans.append(HedgeClusterPlan(cluster_id=cluster_id, action="NONE"))
                continue

            cluster_markets = active_by_cluster.get(cluster_id, [])
            hedge_search_cluster_markets = hedge_search_by_cluster.get(cluster_id, cluster_markets)
            near_expiry = self._near_expiry(cluster)
            force_flat_expiry = self._force_flat_expiry(cluster)
            stale_inventory = bool(cluster.get("has_stale_inventory"))
            stale_dominant_inventory = bool(cluster.get("stale_dominant_inventory"))
            maker_exit_failed = bool(cluster.get("maker_exit_failed"))
            explicit_forced_reduction = bool(cluster.get("explicit_forced_reduction"))
            current_inventory_market_id = (
                str(cluster.get("dominant_inventory_market_id") or "")
                if cluster.get("dominant_inventory_market_id") not in (None, "")
                else None
            )
            current_inventory_token_id = self._token_for_market_id_side(
                hedge_search_cluster_markets,
                current_inventory_market_id,
                dominant_side,
            )
            inventory_market_quality_score = (
                float(cluster.get("dominant_inventory_market_quality_score"))
                if cluster.get("dominant_inventory_market_quality_score") not in (None, "")
                else None
            )
            remaining_event_exposure = (
                float(cluster.get("remaining_event_exposure_notional"))
                if cluster.get("remaining_event_exposure_notional") not in (None, "")
                else 0.0
            )
            stale_after_ms = (
                int(cluster.get("stale_after_ms"))
                if cluster.get("stale_after_ms") not in (None, "")
                else 5_000
            )
            dominant_inventory_market_unrealized_pnl = (
                float(cluster.get("dominant_inventory_market_unrealized_pnl"))
                if cluster.get("dominant_inventory_market_unrealized_pnl") not in (None, "")
                else None
            )
            negative_dominant_inventory = bool(cluster.get("negative_dominant_inventory"))
            worsening_negative_inventory = bool(
                negative_dominant_inventory
                and dominant_inventory_market_unrealized_pnl is not None
                and cluster_state.last_dominant_market_unrealized_pnl is not None
                and float(dominant_inventory_market_unrealized_pnl)
                < float(cluster_state.last_dominant_market_unrealized_pnl) - 1e-9
            )
            cluster["negative_dominant_inventory_worsening"] = worsening_negative_inventory
            max_temp_gross_increase = min(
                cap * self._hedge_max_temp_gross_increase_fraction,
                max(0.0, remaining_event_exposure),
            )
            cooldown_until_ms = cluster_state.hedge_failed_cooldown_until_ms
            in_failed_hedge_cooldown = bool(
                cooldown_until_ms is not None and int(now_ms) < int(cooldown_until_ms)
            )

            market_stats_by_slug = {
                str(market_row.get("market_id") or ""): dict(market_row)
                for market_row in list(cluster.get("markets") or [])
            }
            (
                hedge_market,
                hedge_token_id,
                hedge_execution_metrics,
                hedge_covariance_metrics,
                hedge_pair_relation,
                hedge_market_summary,
            ) = self._select_hedge_market(
                dominant_side=dominant_side,
                cluster_id=cluster_id,
                now_ms=now_ms,
                markets=hedge_search_cluster_markets,
                market_stats_by_slug=market_stats_by_slug,
                book_manager=book_manager,
                current_inventory_market_id=current_inventory_market_id,
                current_inventory_token_id=current_inventory_token_id,
                current_inventory_market_quality_score=inventory_market_quality_score,
                current_inventory_market_end_ts_ms=self._market_end_ts_ms_for_market_id(
                    hedge_search_cluster_markets,
                    current_inventory_market_id,
                ),
            )
            hedge_execution_quality_score = hedge_execution_metrics.quality_score
            hedge_quality_score = hedge_execution_quality_score
            hedge_quality_gap = None
            if hedge_quality_score is not None and inventory_market_quality_score is not None:
                hedge_quality_gap = float(hedge_quality_score) - float(inventory_market_quality_score)

            rejection_reasons: List[str] = []
            if near_expiry:
                rejection_reasons.append("stop_open_window")
            if force_flat_expiry:
                rejection_reasons.append("force_flat_window")
            if explicit_forced_reduction:
                rejection_reasons.append("forced_reduction")
            if in_failed_hedge_cooldown:
                rejection_reasons.append("hedge_failed_cooldown")
            if self._hedge_requires_stale_inventory and not stale_dominant_inventory:
                rejection_reasons.append("stale_inventory_required")
            if stale_dominant_inventory and not maker_exit_failed:
                rejection_reasons.append("maker_exit_window_active")
            if hedge_market is None or hedge_token_id is None:
                if hedge_execution_metrics.state not in {"ok", "disabled"}:
                    rejection_reasons.append(hedge_execution_metrics.state)
                elif self._hedge_covariance_gate_required and hedge_covariance_metrics.state not in {"ok", "disabled"}:
                    rejection_reasons.append(hedge_covariance_metrics.state)
                else:
                    rejection_reasons.append("no_hedge_market")
            elif hedge_execution_quality_score is None or hedge_execution_quality_score < self._min_quality_score:
                rejection_reasons.append("poor_hedge_quality")
            if current_inventory_market_id and inventory_market_quality_score is None:
                rejection_reasons.append("inventory_market_quality_unknown")
            if (
                hedge_quality_score is not None
                and inventory_market_quality_score is not None
                and self._hedge_quality_must_beat_inventory_market
                and float(hedge_quality_score) <= float(inventory_market_quality_score)
            ):
                rejection_reasons.extend(
                    ("not_better_than_inventory_market", "hedge_not_better_than_inventory_market")
                )
            if max_temp_gross_increase <= 0.0:
                rejection_reasons.extend(("gross_increase_too_large", "gross_increase_ceiling_exhausted"))
            dominant_rejection_reason = self._dominant_rejection_reason(rejection_reasons)
            hedge_permission_state = self._hedge_permission_state(
                action="HEDGE" if hedge_market is not None and hedge_token_id is not None else "NONE",
                dominant_rejection_reason=dominant_rejection_reason,
            )

            acceptable_hedge = not any(
                reason in rejection_reasons
                for reason in (
                    "stop_open_window",
                    "force_flat_window",
                    "forced_reduction",
                    "hedge_failed_cooldown",
                    "stale_inventory_required",
                    "no_hedge_book",
                    "no_hedge_depth_or_spread",
                    "no_hedge_market",
                    "poor_hedge_quality",
                    "stale_covariance_history",
                    "asynchronous_book_updates",
                    "boundary_distortion_risk",
                    "tradability_disappeared",
                    "insufficient_covariance_history",
                    "weak_co_movement",
                    "unstable_beta",
                    "covariance_hedge_ratio_too_small",
                    "not_better_than_inventory_market",
                    "hedge_not_better_than_inventory_market",
                    "gross_increase_too_large",
                    "gross_increase_ceiling_exhausted",
                )
            )

            negative_unwind_trigger = False
            if self._negative_pnl_unwind_requires_stale_or_worsening and negative_dominant_inventory:
                if stale_dominant_inventory:
                    negative_unwind_trigger = True
                elif self._negative_pnl_unwind_requires_worsening:
                    negative_unwind_trigger = worsening_negative_inventory
                else:
                    negative_unwind_trigger = True

            control_state = "SKEW"
            action = "SKEW"
            action_reason = "cluster_skew_threshold_breached"
            if abs_net_yes < normalize_trigger and not stale_inventory:
                control_state = "NORMAL"
                action = "NONE"
                action_reason = "cluster_rebalanced"
                cluster_state.last_realized_improvement_state = "pending"
            elif force_flat_expiry:
                control_state = "UNWIND_ONLY"
                action = "UNWIND"
                action_reason = "force_flat_window"
            elif near_expiry:
                control_state = "UNWIND_ONLY"
                action = "UNWIND"
                action_reason = "stop_open_window"
            elif explicit_forced_reduction:
                control_state = "UNWIND_ONLY"
                action = "UNWIND"
                action_reason = "forced_reduction"
            elif in_failed_hedge_cooldown:
                control_state = "UNWIND_ONLY"
                action = "UNWIND"
                action_reason = "failed_hedge_cooldown_active"
            elif negative_unwind_trigger:
                if acceptable_hedge and abs_net_yes >= hedge_trigger:
                    control_state = "HEDGE_ACTIVE"
                    action = "HEDGE"
                    action_reason = "negative_mark_to_market_with_acceptable_hedge"
                else:
                    control_state = "UNWIND_ONLY"
                    action = "UNWIND"
                    action_reason = (
                        "negative_mark_to_market_stale"
                        if stale_dominant_inventory
                        else "negative_mark_to_market_worsening"
                    )
            elif stale_dominant_inventory and maker_exit_failed:
                if acceptable_hedge:
                    control_state = "HEDGE_ELIGIBLE"
                    action = "SKEW"
                    action_reason = "stale_maker_exit_failed"
                    stale_exception_trigger = hedge_trigger
                    if (
                        hedge_execution_metrics.state == "ok"
                        and hedge_covariance_metrics.state == "ok"
                    ):
                        stale_exception_trigger = min(hedge_trigger, skew_trigger)
                    if abs_net_yes >= stale_exception_trigger:
                        control_state = "HEDGE_ACTIVE"
                        action = "HEDGE"
                        action_reason = (
                            "stale_maker_exit_failed_exception"
                            if stale_exception_trigger < hedge_trigger
                            else "hedge_quality_beats_inventory_market"
                        )
                else:
                    control_state = "UNWIND_ONLY"
                    action = "UNWIND"
                    action_reason = "stale_without_acceptable_hedge"
            elif stale_dominant_inventory:
                if not acceptable_hedge and len(hedge_search_cluster_markets) <= 1:
                    control_state = "UNWIND_ONLY"
                    action = "UNWIND"
                    action_reason = "stale_without_acceptable_hedge"
                else:
                    control_state = "SKEW"
                    action = "SKEW"
                    action_reason = "maker_exit_window_active"
            elif negative_dominant_inventory and self._negative_pnl_reduce_only_enabled:
                control_state = "SKEW"
                action = "SKEW"
                action_reason = "negative_mark_to_market_reduce_only"

            if action == "HEDGE":
                if cluster_state.last_action != "HEDGE":
                    cluster_state.hedge_started_at_ms = int(now_ms)
                    cluster_state.hedge_anchor_abs_net_exposure = abs_net_yes
                    cluster_state.last_realized_improvement_state = "pending"
                    cluster_state.active_pair_key = self._pair_key(current_inventory_market_id, hedge_market.slug if hedge_market is not None else None)
                    self._record_pair_acceptance(cluster_state.active_pair_key)
                elif (
                    cluster_state.hedge_started_at_ms is not None
                    and int(now_ms) - int(cluster_state.hedge_started_at_ms) >= self._hedge_success_window_ms
                ):
                    anchor = float(cluster_state.hedge_anchor_abs_net_exposure or abs_net_yes)
                    if abs_net_yes < anchor - 1e-9:
                        cluster_state.hedge_started_at_ms = int(now_ms)
                        cluster_state.hedge_anchor_abs_net_exposure = abs_net_yes
                        cluster_state.last_realized_improvement_state = "improved"
                        self._record_pair_outcome(cluster_state.active_pair_key, improved=True)
                    else:
                        cluster_state.hedge_started_at_ms = None
                        cluster_state.hedge_anchor_abs_net_exposure = None
                        cluster_state.hedge_failed_cooldown_until_ms = int(
                            now_ms + max(1_000, stale_after_ms) * self._hedge_failure_cooldown_scale
                        )
                        cluster_state.last_realized_improvement_state = "no_improvement"
                        self._record_pair_outcome(cluster_state.active_pair_key, improved=False)
                        control_state = "UNWIND_ONLY"
                        action = "UNWIND"
                        action_reason = "hedge_failed_no_improvement"
                        rejection_reasons.append("hedge_failed_no_improvement")
                        dominant_rejection_reason = self._dominant_rejection_reason(rejection_reasons)
                        hedge_permission_state = self._hedge_permission_state(
                            action=action,
                            dominant_rejection_reason=dominant_rejection_reason,
                        )
            else:
                if action != "HEDGE" and cluster_state.last_action != "HEDGE":
                    cluster_state.hedge_started_at_ms = None
                    cluster_state.hedge_anchor_abs_net_exposure = None
                    cluster_state.active_pair_key = None
                    if action == "UNWIND" and cluster_state.last_realized_improvement_state == "pending":
                        cluster_state.last_realized_improvement_state = "no_improvement"

            token_directives: List[HedgeTokenDirective] = []
            affected_market_ids: List[str] = []
            hedge_ratio = None
            covariance_ratio_cap = None
            if hedge_covariance_metrics.beta_clipped is not None:
                covariance_ratio_cap = max(0.0, min(1.0, float(hedge_covariance_metrics.beta_clipped)))
                confidence_cap = self._confidence_ratio_cap(hedge_covariance_metrics.confidence)
                if confidence_cap is not None:
                    covariance_ratio_cap = min(covariance_ratio_cap, confidence_cap)
            gross_ratio_cap = None
            if abs_net_yes > 0.0:
                gross_ratio_cap = max(0.0, min(1.0, max_temp_gross_increase / abs_net_yes))
            if action == "HEDGE" and abs_net_yes > 0.0:
                hedge_ratio = self._resolve_hedge_ratio(covariance_ratio_cap, gross_ratio_cap)
            elif control_state == "HEDGE_ELIGIBLE" and abs_net_yes > 0.0:
                hedge_ratio = self._resolve_hedge_ratio(covariance_ratio_cap, gross_ratio_cap)

            if action == "SKEW":
                extra_skew_ticks = 2 if action_reason == "negative_mark_to_market_reduce_only" else 1
                for market in cluster_markets:
                    affected_market_ids.append(market.slug)
                    for token_id in market.token_ids:
                        token_side = self._token_outcome_side(market, token_id)
                        if token_side != dominant_side:
                            continue
                        token_directives.append(
                            HedgeTokenDirective(
                                token_id=str(token_id),
                                action="SKEW",
                                cluster_id=cluster_id,
                                control_state=control_state,
                                action_reason=action_reason,
                                preferred_side="sell",
                                    hedge_ratio=hedge_ratio,
                                    extra_skew_ticks=extra_skew_ticks,
                                    block_buy=True,
                                    hedge_quality_score=hedge_quality_score,
                                    hedge_execution_quality_score=hedge_execution_quality_score,
                                    hedge_covariance=hedge_covariance_metrics.covariance,
                                    hedge_correlation=hedge_covariance_metrics.correlation,
                                    hedge_beta_raw=hedge_covariance_metrics.beta_raw,
                                    hedge_beta=hedge_covariance_metrics.beta,
                                    hedge_beta_shrunk=hedge_covariance_metrics.beta_shrunk,
                                    hedge_beta_clipped=hedge_covariance_metrics.beta_clipped,
                                    hedge_covariance_sample_count=hedge_covariance_metrics.sample_count,
                                    hedge_covariance_state=hedge_covariance_metrics.state,
                                    hedge_covariance_confidence=hedge_covariance_metrics.confidence,
                                    hedge_pair_score=(hedge_pair_relation.pair_score if hedge_pair_relation is not None else None),
                                    hedgeability_tier=(hedge_pair_relation.hedgeability_tier if hedge_pair_relation is not None else None),
                                    hedge_structural_score=(hedge_pair_relation.structural_score if hedge_pair_relation is not None else None),
                                    hedge_covariance_score=(hedge_pair_relation.covariance_score if hedge_pair_relation is not None else None),
                                    hedge_beta_stability_score=(hedge_pair_relation.beta_stability_score if hedge_pair_relation is not None else None),
                                    hedge_execution_availability_score=(hedge_pair_relation.execution_availability_score if hedge_pair_relation is not None else None),
                                    hedge_realized_outcome_score=(hedge_pair_relation.realized_outcome_score if hedge_pair_relation is not None else None),
                                    hedge_relation_confidence_state=(hedge_pair_relation.confidence_state if hedge_pair_relation is not None else None),
                                    hedge_permission_state=hedge_permission_state,
                                    hedge_rejection_reason=dominant_rejection_reason,
                                    hedge_model_state=self._hedge_model_state(
                                        action=action,
                                        permission_state=hedge_permission_state,
                                        dominant_rejection_reason=dominant_rejection_reason,
                                    ),
                                    hedge_realized_improvement_state=cluster_state.last_realized_improvement_state,
                                    hedge_success_window_ms=self._hedge_success_window_ms,
                                    hedge_failed_cooldown_until_ms=cluster_state.hedge_failed_cooldown_until_ms,
                                    rejection_reasons=tuple(rejection_reasons),
                            )
                        )
            elif action == "HEDGE" and hedge_market is not None and hedge_token_id is not None:
                hedge_target_side = "buy"
                for market in cluster_markets:
                    affected_market_ids.append(market.slug)
                    for token_id in market.token_ids:
                        token_side = self._token_outcome_side(market, token_id)
                        token_key = str(token_id)
                        if token_key == hedge_token_id:
                            token_directives.append(
                                HedgeTokenDirective(
                                    token_id=token_key,
                                    action="HEDGE",
                                    cluster_id=cluster_id,
                                    control_state=control_state,
                                    action_reason=action_reason,
                                    hedge_market_id=hedge_market.slug,
                                    hedge_target_token_id=hedge_token_id,
                                    hedge_target_side=hedge_target_side,
                                    preferred_side="buy",
                                    hedge_ratio=hedge_ratio,
                                    extra_skew_ticks=-2,
                                    block_sell=True,
                                    hedge_quality_score=hedge_quality_score,
                                    hedge_execution_quality_score=hedge_execution_quality_score,
                                    hedge_covariance=hedge_covariance_metrics.covariance,
                                    hedge_correlation=hedge_covariance_metrics.correlation,
                                    hedge_beta_raw=hedge_covariance_metrics.beta_raw,
                                    hedge_beta=hedge_covariance_metrics.beta,
                                    hedge_beta_shrunk=hedge_covariance_metrics.beta_shrunk,
                                    hedge_beta_clipped=hedge_covariance_metrics.beta_clipped,
                                    hedge_covariance_sample_count=hedge_covariance_metrics.sample_count,
                                    hedge_covariance_state=hedge_covariance_metrics.state,
                                    hedge_covariance_confidence=hedge_covariance_metrics.confidence,
                                    hedge_pair_score=(hedge_pair_relation.pair_score if hedge_pair_relation is not None else None),
                                    hedgeability_tier=(hedge_pair_relation.hedgeability_tier if hedge_pair_relation is not None else None),
                                    hedge_structural_score=(hedge_pair_relation.structural_score if hedge_pair_relation is not None else None),
                                    hedge_covariance_score=(hedge_pair_relation.covariance_score if hedge_pair_relation is not None else None),
                                    hedge_beta_stability_score=(hedge_pair_relation.beta_stability_score if hedge_pair_relation is not None else None),
                                    hedge_execution_availability_score=(hedge_pair_relation.execution_availability_score if hedge_pair_relation is not None else None),
                                    hedge_realized_outcome_score=(hedge_pair_relation.realized_outcome_score if hedge_pair_relation is not None else None),
                                    hedge_relation_confidence_state=(hedge_pair_relation.confidence_state if hedge_pair_relation is not None else None),
                                    hedge_permission_state=hedge_permission_state,
                                    hedge_rejection_reason=dominant_rejection_reason,
                                    hedge_model_state=self._hedge_model_state(
                                        action=action,
                                        permission_state=hedge_permission_state,
                                        dominant_rejection_reason=dominant_rejection_reason,
                                    ),
                                    hedge_realized_improvement_state=cluster_state.last_realized_improvement_state,
                                    hedge_success_window_ms=self._hedge_success_window_ms,
                                    hedge_failed_cooldown_until_ms=cluster_state.hedge_failed_cooldown_until_ms,
                                )
                            )
                        elif token_side == dominant_side:
                            token_directives.append(
                                HedgeTokenDirective(
                                    token_id=token_key,
                                    action="HEDGE",
                                    cluster_id=cluster_id,
                                    control_state=control_state,
                                    action_reason=action_reason,
                                    hedge_market_id=hedge_market.slug,
                                    hedge_target_token_id=hedge_token_id,
                                    hedge_target_side=hedge_target_side,
                                    preferred_side="sell",
                                    hedge_ratio=hedge_ratio,
                                    extra_skew_ticks=1,
                                    block_buy=True,
                                    hedge_quality_score=hedge_quality_score,
                                    hedge_execution_quality_score=hedge_execution_quality_score,
                                    hedge_covariance=hedge_covariance_metrics.covariance,
                                    hedge_correlation=hedge_covariance_metrics.correlation,
                                    hedge_beta_raw=hedge_covariance_metrics.beta_raw,
                                    hedge_beta=hedge_covariance_metrics.beta,
                                    hedge_beta_shrunk=hedge_covariance_metrics.beta_shrunk,
                                    hedge_beta_clipped=hedge_covariance_metrics.beta_clipped,
                                    hedge_covariance_sample_count=hedge_covariance_metrics.sample_count,
                                    hedge_covariance_state=hedge_covariance_metrics.state,
                                    hedge_covariance_confidence=hedge_covariance_metrics.confidence,
                                    hedge_pair_score=(hedge_pair_relation.pair_score if hedge_pair_relation is not None else None),
                                    hedgeability_tier=(hedge_pair_relation.hedgeability_tier if hedge_pair_relation is not None else None),
                                    hedge_structural_score=(hedge_pair_relation.structural_score if hedge_pair_relation is not None else None),
                                    hedge_covariance_score=(hedge_pair_relation.covariance_score if hedge_pair_relation is not None else None),
                                    hedge_beta_stability_score=(hedge_pair_relation.beta_stability_score if hedge_pair_relation is not None else None),
                                    hedge_execution_availability_score=(hedge_pair_relation.execution_availability_score if hedge_pair_relation is not None else None),
                                    hedge_realized_outcome_score=(hedge_pair_relation.realized_outcome_score if hedge_pair_relation is not None else None),
                                    hedge_relation_confidence_state=(hedge_pair_relation.confidence_state if hedge_pair_relation is not None else None),
                                    hedge_permission_state=hedge_permission_state,
                                    hedge_rejection_reason=dominant_rejection_reason,
                                    hedge_model_state=self._hedge_model_state(
                                        action=action,
                                        permission_state=hedge_permission_state,
                                        dominant_rejection_reason=dominant_rejection_reason,
                                    ),
                                    hedge_realized_improvement_state=cluster_state.last_realized_improvement_state,
                                    hedge_success_window_ms=self._hedge_success_window_ms,
                                    hedge_failed_cooldown_until_ms=cluster_state.hedge_failed_cooldown_until_ms,
                                )
                            )
                plans.append(
                    HedgeClusterPlan(
                        cluster_id=cluster_id,
                        action="HEDGE",
                        control_state=control_state,
                        action_reason=action_reason,
                        dominant_side=dominant_side,
                        hedge_market_id=hedge_market.slug,
                        hedge_target_token_id=hedge_token_id,
                        hedge_target_side=hedge_target_side,
                        hedge_ratio=hedge_ratio,
                        inventory_market_quality_score=inventory_market_quality_score,
                        hedge_quality_score=hedge_quality_score,
                        hedge_execution_quality_score=hedge_execution_quality_score,
                        hedge_quality_gap=hedge_quality_gap,
                        hedge_covariance=hedge_covariance_metrics.covariance,
                        hedge_correlation=hedge_covariance_metrics.correlation,
                        hedge_beta_raw=hedge_covariance_metrics.beta_raw,
                        hedge_beta=hedge_covariance_metrics.beta,
                        hedge_beta_shrunk=hedge_covariance_metrics.beta_shrunk,
                        hedge_beta_clipped=hedge_covariance_metrics.beta_clipped,
                        hedge_covariance_sample_count=hedge_covariance_metrics.sample_count,
                        hedge_covariance_state=hedge_covariance_metrics.state,
                        hedge_covariance_confidence=hedge_covariance_metrics.confidence,
                        hedge_pair_score=(hedge_pair_relation.pair_score if hedge_pair_relation is not None else None),
                        hedgeability_tier=(hedge_pair_relation.hedgeability_tier if hedge_pair_relation is not None else None),
                        hedge_structural_score=(hedge_pair_relation.structural_score if hedge_pair_relation is not None else None),
                        hedge_covariance_score=(hedge_pair_relation.covariance_score if hedge_pair_relation is not None else None),
                        hedge_beta_stability_score=(hedge_pair_relation.beta_stability_score if hedge_pair_relation is not None else None),
                        hedge_execution_availability_score=(hedge_pair_relation.execution_availability_score if hedge_pair_relation is not None else None),
                        hedge_realized_outcome_score=(hedge_pair_relation.realized_outcome_score if hedge_pair_relation is not None else None),
                        hedge_relation_confidence_state=(hedge_pair_relation.confidence_state if hedge_pair_relation is not None else None),
                        hedge_permission_state=hedge_permission_state,
                        hedge_rejection_reason=dominant_rejection_reason,
                        hedge_model_state=self._hedge_model_state(
                            action="HEDGE",
                            permission_state=hedge_permission_state,
                            dominant_rejection_reason=dominant_rejection_reason,
                        ),
                        hedge_realized_improvement_state=cluster_state.last_realized_improvement_state,
                        candidate_state="rejected" if rejection_reasons else "accepted",
                        hedge_success_window_ms=self._hedge_success_window_ms,
                        hedge_failed_cooldown_until_ms=cluster_state.hedge_failed_cooldown_until_ms,
                        rejection_reasons=tuple(rejection_reasons),
                        affected_market_ids=tuple(sorted(set(affected_market_ids))),
                        token_directives=tuple(token_directives),
                        pair_relations=tuple(hedge_market_summary.get("pair_relations") or ()),
                        candidate_summary=dict(hedge_market_summary),
                    )
                )
                for directive in token_directives:
                    directives_by_token[directive.token_id] = directive
                directives_by_market[cluster_id] = {
                    "action": "HEDGE",
                    "manual_force_flat": False,
                    "control_state": control_state,
                    "hedge_action_reason": action_reason,
                    "hedge_ratio": hedge_ratio,
                    "hedge_target_market": hedge_market.slug,
                    "hedge_target_token": hedge_token_id,
                    "hedge_target_side": hedge_target_side,
                    "hedge_execution_quality_score": hedge_execution_quality_score,
                    "hedge_success_window_ms": self._hedge_success_window_ms,
                    "hedge_failed_cooldown_until_ms": cluster_state.hedge_failed_cooldown_until_ms,
                    "hedge_quality_gap": hedge_quality_gap,
                    "inventory_market_quality_score": inventory_market_quality_score,
                    "hedge_covariance": hedge_covariance_metrics.covariance,
                    "hedge_correlation": hedge_covariance_metrics.correlation,
                    "hedge_beta_raw": hedge_covariance_metrics.beta_raw,
                    "hedge_beta": hedge_covariance_metrics.beta,
                    "hedge_beta_shrunk": hedge_covariance_metrics.beta_shrunk,
                    "hedge_beta_clipped": hedge_covariance_metrics.beta_clipped,
                    "hedge_covariance_sample_count": hedge_covariance_metrics.sample_count,
                    "hedge_covariance_state": hedge_covariance_metrics.state,
                    "hedge_covariance_confidence": hedge_covariance_metrics.confidence,
                    "hedge_permission_state": hedge_permission_state,
                    "hedge_rejection_reason": dominant_rejection_reason,
                    "hedge_model_state": self._hedge_model_state(
                        action="HEDGE",
                        permission_state=hedge_permission_state,
                        dominant_rejection_reason=dominant_rejection_reason,
                    ),
                    "hedge_realized_improvement_state": cluster_state.last_realized_improvement_state,
                }
                cluster_state.last_action = action
                cluster_state.control_state = control_state
                cluster_state.last_dominant_market_unrealized_pnl = dominant_inventory_market_unrealized_pnl
                continue
            else:
                for market in cluster_markets:
                    affected_market_ids.append(market.slug)
                    directives_by_market[market.slug] = {
                        "action": "UNWIND",
                        "manual_force_flat": True,
                        "control_state": control_state,
                        "hedge_action_reason": action_reason,
                        "hedge_ratio": hedge_ratio,
                        "hedge_target_market": hedge_market.slug if hedge_market is not None else None,
                        "hedge_target_token": hedge_token_id,
                        "hedge_target_side": "buy" if hedge_market is not None and hedge_token_id is not None else None,
                        "hedge_execution_quality_score": hedge_execution_quality_score,
                        "hedge_success_window_ms": self._hedge_success_window_ms,
                        "hedge_failed_cooldown_until_ms": cluster_state.hedge_failed_cooldown_until_ms,
                        "hedge_covariance": hedge_covariance_metrics.covariance,
                        "hedge_correlation": hedge_covariance_metrics.correlation,
                        "hedge_beta_raw": hedge_covariance_metrics.beta_raw,
                        "hedge_beta": hedge_covariance_metrics.beta,
                        "hedge_beta_shrunk": hedge_covariance_metrics.beta_shrunk,
                        "hedge_beta_clipped": hedge_covariance_metrics.beta_clipped,
                        "hedge_covariance_sample_count": hedge_covariance_metrics.sample_count,
                        "hedge_covariance_state": hedge_covariance_metrics.state,
                        "hedge_covariance_confidence": hedge_covariance_metrics.confidence,
                        "hedge_permission_state": hedge_permission_state,
                        "hedge_rejection_reason": dominant_rejection_reason,
                        "hedge_model_state": self._hedge_model_state(
                            action=action,
                            permission_state=hedge_permission_state,
                            dominant_rejection_reason=dominant_rejection_reason,
                        ),
                        "hedge_realized_improvement_state": cluster_state.last_realized_improvement_state,
                    }
                    for token_id in market.token_ids:
                        token_side = self._token_outcome_side(market, token_id)
                        token_directives.append(
                            HedgeTokenDirective(
                                token_id=str(token_id),
                                action="UNWIND",
                                cluster_id=cluster_id,
                                control_state=control_state,
                                action_reason=action_reason,
                                preferred_side="sell" if token_side == dominant_side else None,
                                hedge_ratio=hedge_ratio,
                                extra_skew_ticks=1 if token_side == dominant_side else 0,
                                block_buy=True,
                                reduce_only=True,
                                rejection_reasons=tuple(rejection_reasons),
                                hedge_market_id=(hedge_market.slug if hedge_market is not None else None),
                                hedge_target_token_id=hedge_token_id,
                                hedge_target_side=("buy" if hedge_market is not None and hedge_token_id is not None else None),
                                hedge_quality_score=hedge_quality_score,
                                hedge_execution_quality_score=hedge_execution_quality_score,
                                hedge_covariance=hedge_covariance_metrics.covariance,
                                hedge_correlation=hedge_covariance_metrics.correlation,
                                hedge_beta_raw=hedge_covariance_metrics.beta_raw,
                                hedge_beta=hedge_covariance_metrics.beta,
                                hedge_beta_shrunk=hedge_covariance_metrics.beta_shrunk,
                                hedge_beta_clipped=hedge_covariance_metrics.beta_clipped,
                                hedge_covariance_sample_count=hedge_covariance_metrics.sample_count,
                                hedge_covariance_state=hedge_covariance_metrics.state,
                                hedge_covariance_confidence=hedge_covariance_metrics.confidence,
                                hedge_pair_score=(hedge_pair_relation.pair_score if hedge_pair_relation is not None else None),
                                hedgeability_tier=(hedge_pair_relation.hedgeability_tier if hedge_pair_relation is not None else None),
                                hedge_structural_score=(hedge_pair_relation.structural_score if hedge_pair_relation is not None else None),
                                hedge_covariance_score=(hedge_pair_relation.covariance_score if hedge_pair_relation is not None else None),
                                hedge_beta_stability_score=(hedge_pair_relation.beta_stability_score if hedge_pair_relation is not None else None),
                                hedge_execution_availability_score=(hedge_pair_relation.execution_availability_score if hedge_pair_relation is not None else None),
                                hedge_realized_outcome_score=(hedge_pair_relation.realized_outcome_score if hedge_pair_relation is not None else None),
                                hedge_relation_confidence_state=(hedge_pair_relation.confidence_state if hedge_pair_relation is not None else None),
                                hedge_permission_state=hedge_permission_state,
                                hedge_rejection_reason=dominant_rejection_reason,
                                hedge_model_state=self._hedge_model_state(
                                    action=action,
                                    permission_state=hedge_permission_state,
                                    dominant_rejection_reason=dominant_rejection_reason,
                                ),
                                hedge_realized_improvement_state=cluster_state.last_realized_improvement_state,
                                hedge_success_window_ms=self._hedge_success_window_ms,
                                hedge_failed_cooldown_until_ms=cluster_state.hedge_failed_cooldown_until_ms,
                            )
                        )

            plans.append(
                HedgeClusterPlan(
                    cluster_id=cluster_id,
                    action=action,
                    control_state=control_state,
                    action_reason=action_reason,
                    dominant_side=dominant_side,
                    hedge_market_id=(hedge_market.slug if hedge_market is not None else None),
                    hedge_target_token_id=hedge_token_id,
                    hedge_target_side=("buy" if action == "HEDGE" else None),
                    hedge_ratio=hedge_ratio,
                    inventory_market_quality_score=inventory_market_quality_score,
                    hedge_quality_score=hedge_quality_score,
                    hedge_execution_quality_score=hedge_execution_quality_score,
                    hedge_quality_gap=hedge_quality_gap,
                    hedge_covariance=hedge_covariance_metrics.covariance,
                    hedge_correlation=hedge_covariance_metrics.correlation,
                    hedge_beta_raw=hedge_covariance_metrics.beta_raw,
                    hedge_beta=hedge_covariance_metrics.beta,
                    hedge_beta_shrunk=hedge_covariance_metrics.beta_shrunk,
                    hedge_beta_clipped=hedge_covariance_metrics.beta_clipped,
                    hedge_covariance_sample_count=hedge_covariance_metrics.sample_count,
                    hedge_covariance_state=hedge_covariance_metrics.state,
                    hedge_covariance_confidence=hedge_covariance_metrics.confidence,
                    hedge_pair_score=(hedge_pair_relation.pair_score if hedge_pair_relation is not None else None),
                    hedgeability_tier=(hedge_pair_relation.hedgeability_tier if hedge_pair_relation is not None else None),
                    hedge_structural_score=(hedge_pair_relation.structural_score if hedge_pair_relation is not None else None),
                    hedge_covariance_score=(hedge_pair_relation.covariance_score if hedge_pair_relation is not None else None),
                    hedge_beta_stability_score=(hedge_pair_relation.beta_stability_score if hedge_pair_relation is not None else None),
                    hedge_execution_availability_score=(hedge_pair_relation.execution_availability_score if hedge_pair_relation is not None else None),
                    hedge_realized_outcome_score=(hedge_pair_relation.realized_outcome_score if hedge_pair_relation is not None else None),
                    hedge_relation_confidence_state=(hedge_pair_relation.confidence_state if hedge_pair_relation is not None else None),
                    hedge_permission_state=hedge_permission_state,
                    hedge_rejection_reason=dominant_rejection_reason,
                    hedge_model_state=self._hedge_model_state(
                        action=action,
                        permission_state=hedge_permission_state,
                        dominant_rejection_reason=dominant_rejection_reason,
                    ),
                    hedge_realized_improvement_state=cluster_state.last_realized_improvement_state,
                    candidate_state="rejected" if rejection_reasons else ("accepted" if action == "HEDGE" else "deferred"),
                    hedge_success_window_ms=self._hedge_success_window_ms,
                    hedge_failed_cooldown_until_ms=cluster_state.hedge_failed_cooldown_until_ms,
                    rejection_reasons=tuple(rejection_reasons),
                    affected_market_ids=tuple(sorted(set(affected_market_ids))),
                    token_directives=tuple(token_directives),
                    pair_relations=tuple(hedge_market_summary.get("pair_relations") or ()),
                    candidate_summary=dict(hedge_market_summary),
                )
            )
            for directive in token_directives:
                directives_by_token[directive.token_id] = directive
            if action != "UNWIND":
                directives_by_market[cluster_id] = {
                    "action": action,
                    "manual_force_flat": False,
                    "control_state": control_state,
                    "hedge_action_reason": action_reason,
                    "hedge_ratio": hedge_ratio,
                    "hedge_target_market": hedge_market.slug if hedge_market is not None else None,
                    "hedge_target_token": hedge_token_id,
                    "hedge_target_side": ("buy" if hedge_market is not None and hedge_token_id is not None else None),
                    "hedge_success_window_ms": self._hedge_success_window_ms,
                    "hedge_failed_cooldown_until_ms": cluster_state.hedge_failed_cooldown_until_ms,
                    "hedge_quality_gap": hedge_quality_gap,
                    "inventory_market_quality_score": inventory_market_quality_score,
                    "hedge_covariance": hedge_covariance_metrics.covariance,
                    "hedge_correlation": hedge_covariance_metrics.correlation,
                    "hedge_beta": hedge_covariance_metrics.beta,
                    "hedge_beta_clipped": hedge_covariance_metrics.beta_clipped,
                    "hedge_covariance_sample_count": hedge_covariance_metrics.sample_count,
                    "hedge_covariance_state": hedge_covariance_metrics.state,
                }
            cluster_state.last_action = action
            cluster_state.control_state = control_state
            cluster_state.last_dominant_market_unrealized_pnl = dominant_inventory_market_unrealized_pnl

        return {
            "payload": {
                "enabled": not live_mode,
                "paper_only": True,
                "clusters": [self._cluster_plan_payload(plan) for plan in plans],
            },
            "directives_by_token": directives_by_token,
            "directives_by_market": directives_by_market,
        }

    def _cluster_plan_payload(self, plan: HedgeClusterPlan) -> Dict[str, Any]:
        payload = asdict(plan)
        payload["token_directives"] = [asdict(directive) for directive in plan.token_directives]
        return payload

    def _near_expiry(self, cluster: Dict[str, Any]) -> bool:
        time_to_expiry_ms = cluster.get("time_to_expiry_ms")
        stop_open_window_ms = cluster.get("stop_open_window_ms")
        if time_to_expiry_ms is None or stop_open_window_ms is None:
            return False
        return int(time_to_expiry_ms) <= int(stop_open_window_ms)

    def _force_flat_expiry(self, cluster: Dict[str, Any]) -> bool:
        time_to_expiry_ms = cluster.get("time_to_expiry_ms")
        force_flat_window_ms = cluster.get("force_flat_window_ms")
        if time_to_expiry_ms is None or force_flat_window_ms is None:
            return False
        return int(time_to_expiry_ms) <= int(force_flat_window_ms)

    def _select_hedge_market(
        self,
        *,
        dominant_side: str,
        cluster_id: str,
        now_ms: int,
        markets: Sequence[MarketCandidate],
        market_stats_by_slug: Dict[str, Dict[str, Any]],
        book_manager: BookManager,
        current_inventory_market_id: Optional[str],
        current_inventory_token_id: Optional[str],
        current_inventory_market_quality_score: Optional[float],
        current_inventory_market_end_ts_ms: Optional[int],
    ) -> tuple[
        Optional[MarketCandidate],
        Optional[str],
        HedgeExecutionMetrics,
        HedgeCovarianceMetrics,
        Optional[HedgePairRelation],
        Dict[str, Any],
    ]:
        target_side = "no" if dominant_side == "yes" else "yes"
        best_market: Optional[MarketCandidate] = None
        best_token_id: Optional[str] = None
        best_score: Optional[float] = None
        best_pair_relation: Optional[HedgePairRelation] = None
        best_execution_metrics = HedgeExecutionMetrics(quality_score=None, state="no_hedge_book")
        best_covariance_metrics = HedgeCovarianceMetrics(
            covariance=None,
            correlation=None,
            beta_raw=None,
            beta=None,
            beta_shrunk=None,
            beta_clipped=None,
            beta_sign_consistency=None,
            alignment_fraction=None,
            sample_count=0,
            state="insufficient_covariance_history",
            confidence="unknown",
        )
        best_summary: Dict[str, Any] = {}
        candidate_count = 0
        accepted_count = 0
        rejection_counts: Dict[str, int] = {}
        pair_relations: List[Dict[str, Any]] = []
        scored_candidates: List[Tuple[float, MarketCandidate, str, HedgeExecutionMetrics, HedgeCovarianceMetrics, HedgePairRelation, Optional[float], Optional[int]]] = []
        current_bucket_anchor = self._bucket_anchor_for_market_id(current_inventory_market_id)
        proof_only_lane = self._is_proof_only_lane()
        current_market = next((market for market in markets if str(market.slug) == str(current_inventory_market_id)), None)
        for market in markets:
            if current_inventory_market_id and str(market.slug) == str(current_inventory_market_id):
                continue
            candidate_count += 1
            if proof_only_lane and not self._is_crypto_market(market):
                rejection_counts["non_crypto_cluster_out_of_scope"] = int(
                    rejection_counts.get("non_crypto_cluster_out_of_scope", 0)
                ) + 1
                continue
            token_id = self._token_for_side(market, target_side)
            if token_id is None:
                rejection_counts["no_hedge_token"] = int(rejection_counts.get("no_hedge_token", 0)) + 1
                continue
            pair_key = self._pair_key(current_inventory_market_id, market.slug)
            covariance_metrics = self._covariance_metrics(current_inventory_token_id, token_id, now_ms=now_ms)
            execution_metrics = self._execution_metrics(
                now_ms=now_ms,
                inventory_token_id=current_inventory_token_id,
                hedge_token_id=token_id,
                book_manager=book_manager,
            )
            self._record_pair_execution_observation(pair_key, execution_metrics.state in {"ok", "disabled"})
            execution_score = float(execution_metrics.quality_score or 0.0)
            market_stats = market_stats_by_slug.get(str(market.slug), {})
            dominant_market_exposure = float(market_stats.get(f"{dominant_side}_exposure_notional") or 0.0)
            if dominant_market_exposure <= 0.0:
                execution_score += 1_000.0
            candidate_bucket_anchor = self._bucket_anchor_for_market(market)
            bucket_distance = self._bucket_distance(current_bucket_anchor, candidate_bucket_anchor)
            pair_relation = self._pair_relation(
                cluster_id=cluster_id,
                now_ms=now_ms,
                inventory_market=current_market,
                hedge_market=market,
                covariance_metrics=covariance_metrics,
                execution_metrics=execution_metrics,
                bucket_distance=bucket_distance,
            )
            pair_relations.append(asdict(pair_relation))
            if proof_only_lane and self._is_crypto_market(market):
                if bucket_distance is None:
                    rejection_counts["expiry_mismatch"] = int(rejection_counts.get("expiry_mismatch", 0)) + 1
                    continue
                if bucket_distance > self._proof_only_bucket_distance:
                    rejection_counts["bucket_too_far"] = int(rejection_counts.get("bucket_too_far", 0)) + 1
                    continue
                if current_inventory_market_end_ts_ms is None or market.end_ts_ms is None:
                    rejection_counts["expiry_mismatch"] = int(rejection_counts.get("expiry_mismatch", 0)) + 1
                    continue
                if abs(int(market.end_ts_ms) - int(current_inventory_market_end_ts_ms)) > self._proof_only_expiry_slack_ms:
                    rejection_counts["expiry_mismatch"] = int(rejection_counts.get("expiry_mismatch", 0)) + 1
                    continue
            if self._hedge_covariance_gate_required and covariance_metrics.state not in {"ok", "disabled"}:
                rejection_counts[covariance_metrics.state] = int(rejection_counts.get(covariance_metrics.state, 0)) + 1
                continue
            if execution_metrics.state not in {"ok", "disabled"}:
                rejection_counts[execution_metrics.state] = int(rejection_counts.get(execution_metrics.state, 0)) + 1
                continue
            if (
                current_inventory_market_quality_score is not None
                and execution_score <= float(current_inventory_market_quality_score)
            ):
                rejection_counts["not_better_than_inventory_market"] = int(
                    rejection_counts.get("not_better_than_inventory_market", 0)
                ) + 1
                continue
            combined_score = self._combined_hedge_score(
                execution_score=execution_score,
                covariance_metrics=covariance_metrics,
                pair_relation=pair_relation,
            )
            scored_candidates.append(
                (
                    combined_score,
                    market,
                    token_id,
                    HedgeExecutionMetrics(quality_score=execution_score, state=execution_metrics.state),
                    covariance_metrics,
                    pair_relation,
                    (
                        None
                        if current_inventory_market_quality_score is None
                        else float(execution_score) - float(current_inventory_market_quality_score)
                    ),
                    bucket_distance,
                )
            )
        if scored_candidates:
            scored_candidates.sort(key=lambda item: item[0], reverse=True)
            top_score, market, token_id, execution_metrics, covariance_metrics, pair_relation, quality_gap, bucket_distance = scored_candidates[0]
            if pair_relation.hedgeability_tier in {"usable", "preferred"}:
                accepted_count += 1
            elif self._cluster_relative_promotes(
                pair_relation=pair_relation,
                covariance_metrics=covariance_metrics,
                execution_metrics=execution_metrics,
                top_score=top_score,
                second_score=(scored_candidates[1][0] if len(scored_candidates) > 1 else None),
            ):
                pair_relation = replace(
                    pair_relation,
                    hedgeability_tier="usable",
                    candidate_state="accepted",
                    rejection_reason=None,
                )
                for idx, relation in enumerate(pair_relations):
                    if (
                        relation.get("inventory_market_id") == pair_relation.inventory_market_id
                        and relation.get("hedge_market_id") == pair_relation.hedge_market_id
                    ):
                        pair_relations[idx] = asdict(pair_relation)
                        break
                accepted_count += 1
            else:
                rejection_counts[pair_relation.hedgeability_tier] = int(
                    rejection_counts.get(pair_relation.hedgeability_tier, 0)
                ) + 1
            if pair_relation.hedgeability_tier in {"usable", "preferred"}:
                best_market = market
                best_token_id = token_id
                best_score = top_score
                best_pair_relation = pair_relation
                best_execution_metrics = execution_metrics
                best_covariance_metrics = covariance_metrics
                best_summary = {
                    "market_id": market.slug,
                    "condition_id": market.condition_id,
                    "token_id": token_id,
                    "bucket_distance": bucket_distance,
                    "quality_score": execution_metrics.quality_score,
                    "combined_score": top_score,
                    "quality_gap": quality_gap,
                    "covariance": covariance_metrics.covariance,
                    "correlation": covariance_metrics.correlation,
                    "beta_raw": covariance_metrics.beta_raw,
                    "beta": covariance_metrics.beta,
                    "beta_shrunk": covariance_metrics.beta_shrunk,
                    "beta_clipped": covariance_metrics.beta_clipped,
                    "beta_sign_consistency": covariance_metrics.beta_sign_consistency,
                    "alignment_fraction": covariance_metrics.alignment_fraction,
                    "covariance_sample_count": covariance_metrics.sample_count,
                    "covariance_state": covariance_metrics.state,
                    "covariance_confidence": covariance_metrics.confidence,
                    "execution_state": execution_metrics.state,
                    "pair_score": pair_relation.pair_score,
                    "hedgeability_tier": pair_relation.hedgeability_tier,
                    "structural_score": pair_relation.structural_score,
                    "covariance_score": pair_relation.covariance_score,
                    "beta_stability_score": pair_relation.beta_stability_score,
                    "execution_availability_score": pair_relation.execution_availability_score,
                    "realized_outcome_score": pair_relation.realized_outcome_score,
                    "confidence_state": pair_relation.confidence_state,
                    "relative_rank_promoted": covariance_metrics.state in {"ok", "disabled"} and pair_relation.hedgeability_tier == "usable",
                }
        summary = {
            "cluster_id": cluster_id,
            "candidate_count": int(candidate_count),
            "accepted_count": int(accepted_count),
            "rejection_counts": {key: int(value) for key, value in sorted(rejection_counts.items())},
            "best_candidate": best_summary,
            "pair_relations": pair_relations,
            "search_profile": self._hedge_search_profile,
            "proof_only_lane": bool(proof_only_lane),
            "proof_only_bucket_distance": int(self._proof_only_bucket_distance),
            "proof_only_expiry_slack_ms": int(self._proof_only_expiry_slack_ms),
            "crypto_symbol_scope": list(self._proof_only_crypto_symbols),
        }
        known_rejections = sum(
            int(value)
            for key, value in summary["rejection_counts"].items()
            if key != "not_better_than_inventory_market"
        )
        implied_not_better = max(0, int(candidate_count) - int(accepted_count) - known_rejections)
        summary["rejection_counts"]["not_better_than_inventory_market"] = max(
            int(summary["rejection_counts"].get("not_better_than_inventory_market", 0)),
            implied_not_better,
        )
        return best_market, best_token_id, best_execution_metrics, best_covariance_metrics, best_pair_relation, summary

    def _record_mid_history(
        self,
        *,
        now_ms: int,
        active_markets: Sequence[MarketCandidate],
        book_manager: BookManager,
    ) -> None:
        cutoff_ms = int(now_ms) - self._hedge_covariance_window_ms
        active_tokens: set[str] = set()
        for market in active_markets:
            for token_id in market.token_ids:
                token_key = str(token_id)
                active_tokens.add(token_key)
                book = book_manager.get_book(token_key)
                if book is None or book.mid_price is None:
                    continue
                history = self._mid_history_by_token.setdefault(token_key, deque())
                if not history or history[-1][0] != int(now_ms):
                    history.append((int(now_ms), float(book.mid_price)))
                while history and history[0][0] < cutoff_ms:
                    history.popleft()
        for token_key, history in list(self._mid_history_by_token.items()):
            while history and history[0][0] < cutoff_ms:
                history.popleft()
            if not history and token_key not in active_tokens:
                self._mid_history_by_token.pop(token_key, None)

    def _returns_by_ts(self, token_id: Optional[str]) -> Dict[int, float]:
        if not token_id:
            return {}
        history = self._mid_history_by_token.get(str(token_id))
        if not history or len(history) < 2:
            return {}
        returns: Dict[int, float] = {}
        previous_mid: Optional[float] = None
        for ts_ms, mid in history:
            current_mid = float(mid)
            if previous_mid is not None and previous_mid > 0.0 and current_mid > 0.0:
                returns[int(ts_ms)] = (current_mid - previous_mid) / previous_mid
            previous_mid = current_mid
        return returns

    def _aligned_return_pairs(
        self,
        inventory_returns: Dict[int, float],
        hedge_returns: Dict[int, float],
    ) -> List[Tuple[int, float, float]]:
        if not inventory_returns or not hedge_returns:
            return []
        tolerance_ms = max(0, int(self._hedge_covariance_max_update_gap_ms))
        hedge_items = sorted((int(ts), float(ret)) for ts, ret in hedge_returns.items())
        used_hedge_ts: set[int] = set()
        aligned: List[Tuple[int, float, float]] = []
        for inv_ts, inv_ret in sorted((int(ts), float(ret)) for ts, ret in inventory_returns.items()):
            best_match: Optional[Tuple[int, float]] = None
            best_gap: Optional[int] = None
            for hedge_ts, hedge_ret in hedge_items:
                if hedge_ts in used_hedge_ts:
                    continue
                gap = abs(int(inv_ts) - int(hedge_ts))
                if tolerance_ms > 0 and gap > tolerance_ms:
                    continue
                if best_gap is None or gap < best_gap:
                    best_match = (hedge_ts, hedge_ret)
                    best_gap = gap
            if best_match is None:
                continue
            used_hedge_ts.add(int(best_match[0]))
            aligned.append((int(inv_ts), float(inv_ret), float(best_match[1])))
        return aligned

    def _alignment_fraction(self, *, inventory_count: int, hedge_count: int, aligned_count: int) -> float:
        denominator = max(1, min(int(inventory_count), int(hedge_count)))
        return max(0.0, min(1.0, float(aligned_count) / float(denominator)))

    def _covariance_metrics(
        self,
        inventory_token_id: Optional[str],
        hedge_token_id: Optional[str],
        *,
        now_ms: int,
    ) -> HedgeCovarianceMetrics:
        if not self._hedge_covariance_enabled:
            return HedgeCovarianceMetrics(
                covariance=None,
                correlation=None,
                beta_raw=None,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=None,
                sample_count=0,
                state="disabled",
                confidence="unknown",
            )
        inventory_returns = self._returns_by_ts(inventory_token_id)
        hedge_returns = self._returns_by_ts(hedge_token_id)
        aligned_pairs = self._aligned_return_pairs(inventory_returns, hedge_returns)
        common_ts = [int(inv_ts) for inv_ts, _, _ in aligned_pairs]
        alignment_fraction = self._alignment_fraction(
            inventory_count=len(inventory_returns),
            hedge_count=len(hedge_returns),
            aligned_count=len(aligned_pairs),
        )
        if len(common_ts) < self._hedge_covariance_min_samples:
            return HedgeCovarianceMetrics(
                covariance=None,
                correlation=None,
                beta_raw=None,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state=(
                    "asynchronous_book_updates"
                    if inventory_returns and hedge_returns and alignment_fraction < 0.5
                    else "insufficient_covariance_history"
                ),
                confidence="unknown",
            )
        if self._hedge_covariance_max_sample_age_ms > 0 and int(now_ms) - int(common_ts[-1]) > self._hedge_covariance_max_sample_age_ms:
            return HedgeCovarianceMetrics(
                covariance=None,
                correlation=None,
                beta_raw=None,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="stale_covariance_history",
                confidence="unknown",
            )
        if self._boundary_distortion_risk(inventory_token_id) or self._boundary_distortion_risk(hedge_token_id):
            return HedgeCovarianceMetrics(
                covariance=None,
                correlation=None,
                beta_raw=None,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="boundary_distortion_risk",
                confidence="weak",
            )
        x_vals = [float(inv_ret) for _, inv_ret, _ in aligned_pairs]
        y_vals = [float(hedge_ret) for _, _, hedge_ret in aligned_pairs]
        mean_x = sum(x_vals) / len(x_vals)
        mean_y = sum(y_vals) / len(y_vals)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals)) / len(common_ts)
        var_x = sum((x - mean_x) ** 2 for x in x_vals) / len(common_ts)
        var_y = sum((y - mean_y) ** 2 for y in y_vals) / len(common_ts)
        if not math.isfinite(var_y) or var_y <= 1e-12:
            return HedgeCovarianceMetrics(
                covariance=cov,
                correlation=None,
                beta_raw=None,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="unstable_beta",
                confidence="unknown",
            )
        beta_raw = cov / var_y
        if not math.isfinite(beta_raw):
            return HedgeCovarianceMetrics(
                covariance=cov,
                correlation=None,
                beta_raw=None,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="unstable_beta",
                confidence="unknown",
            )
        correlation: Optional[float] = None
        if math.isfinite(var_x) and var_x > 1e-12:
            correlation = cov / math.sqrt(var_x * var_y)
        if correlation is None or not math.isfinite(correlation) or correlation > -self._hedge_covariance_min_correlation:
            return HedgeCovarianceMetrics(
                covariance=cov,
                correlation=correlation,
                beta_raw=beta_raw,
                beta=beta_raw,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=None,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="weak_co_movement",
                confidence="weak",
            )
        beta_sign_consistency = self._beta_sign_consistency_score(common_ts=common_ts, x_vals=x_vals, y_vals=y_vals)
        if beta_sign_consistency < 0.50:
            return HedgeCovarianceMetrics(
                covariance=cov,
                correlation=correlation,
                beta_raw=beta_raw,
                beta=None,
                beta_shrunk=None,
                beta_clipped=None,
                beta_sign_consistency=beta_sign_consistency,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="unstable_beta",
                confidence="weak",
            )
        beta_shrunk = beta_raw * (1.0 - self._hedge_covariance_beta_shrinkage)
        beta_clipped = min(abs(beta_shrunk), self._hedge_covariance_beta_clip)
        if beta_clipped < self._hedge_covariance_min_abs_beta:
            return HedgeCovarianceMetrics(
                covariance=cov,
                correlation=correlation,
                beta_raw=beta_raw,
                beta=beta_raw,
                beta_shrunk=beta_shrunk,
                beta_clipped=beta_clipped,
                beta_sign_consistency=beta_sign_consistency,
                alignment_fraction=alignment_fraction,
                sample_count=len(common_ts),
                state="covariance_hedge_ratio_too_small",
                confidence="weak",
            )
        confidence = self._covariance_confidence(correlation=correlation, sample_count=len(common_ts))
        return HedgeCovarianceMetrics(
            covariance=cov,
            correlation=correlation,
            beta_raw=beta_raw,
            beta=beta_raw,
            beta_shrunk=beta_shrunk,
            beta_clipped=beta_clipped,
            beta_sign_consistency=beta_sign_consistency,
            alignment_fraction=alignment_fraction,
            sample_count=len(common_ts),
            state="ok",
            confidence=confidence,
        )

    def _combined_hedge_score(
        self,
        *,
        execution_score: float,
        covariance_metrics: HedgeCovarianceMetrics,
        pair_relation: HedgePairRelation,
    ) -> float:
        confidence_bonus = {
            "strong": 0.10,
            "usable": 0.05,
            "weak": 0.01,
            "unknown": 0.0,
        }.get(str(covariance_metrics.confidence or "unknown"), 0.0)
        beta_usefulness = max(
            0.0,
            min(self._hedge_covariance_beta_clip or 1.0, float(covariance_metrics.beta_clipped or 0.0)),
        )
        pair_multiplier = 0.70 + (0.30 * max(0.0, min(1.0, float(pair_relation.pair_score))))
        return float(execution_score) * (1.0 + confidence_bonus + (0.02 * beta_usefulness)) * pair_multiplier

    def _pair_relation(
        self,
        *,
        cluster_id: str,
        now_ms: int,
        inventory_market: Optional[MarketCandidate],
        hedge_market: MarketCandidate,
        covariance_metrics: HedgeCovarianceMetrics,
        execution_metrics: HedgeExecutionMetrics,
        bucket_distance: Optional[int],
    ) -> HedgePairRelation:
        pair_key = self._pair_key(inventory_market.slug if inventory_market is not None else None, hedge_market.slug)
        structural_score = self._structural_score(inventory_market=inventory_market, hedge_market=hedge_market, bucket_distance=bucket_distance)
        covariance_score = self._covariance_score(covariance_metrics)
        beta_stability_score = self._beta_stability_score(covariance_metrics)
        execution_availability_score = self._execution_availability_score(pair_key)
        realized_outcome_score, confidence_state = self._realized_outcome_score(pair_key)
        pair_score = (
            0.20 * structural_score
            + 0.15 * covariance_score
            + 0.25 * beta_stability_score
            + 0.30 * execution_availability_score
            + 0.10 * realized_outcome_score
        )
        basis_accumulation_flag = self._basis_accumulation_flag(pair_key)
        tier = self._hedgeability_tier(
            pair_score=pair_score,
            structural_score=structural_score,
            covariance_metrics=covariance_metrics,
            beta_stability_score=beta_stability_score,
            execution_availability_score=execution_availability_score,
            realized_outcome_score=realized_outcome_score,
            confidence_state=confidence_state,
            basis_accumulation_flag=basis_accumulation_flag,
        )
        exec_stats = self._pair_execution_stats.get(pair_key, {})
        outcome_stats = self._pair_outcome_stats.get(pair_key, {})
        return HedgePairRelation(
            inventory_market_id=(inventory_market.slug if inventory_market is not None else None),
            hedge_market_id=hedge_market.slug,
            cluster_id=cluster_id,
            underlying_symbol=str(hedge_market.reference_symbol or "").upper() or None,
            event_family=self._event_family_for_market(hedge_market),
            expiry_bucket=self._expiry_bucket_for_market(hedge_market),
            contract_family=self._contract_family_for_market(hedge_market),
            structural_score=round(structural_score, 6),
            covariance_score=round(covariance_score, 6),
            beta_stability_score=round(beta_stability_score, 6),
            execution_availability_score=round(execution_availability_score, 6),
            realized_outcome_score=round(realized_outcome_score, 6),
            pair_score=round(pair_score, 6),
            hedgeability_tier=tier,
            confidence_state=confidence_state,
            basis_accumulation_flag=basis_accumulation_flag,
            accepted_hedge_count=int(outcome_stats.get("accepted", 0)),
            successful_hedge_count=int(outcome_stats.get("improved", 0)),
            failed_hedge_count=int(outcome_stats.get("failed", 0)),
            execution_observation_count=int(exec_stats.get("observed", 0)),
            execution_ok_count=int(exec_stats.get("ok", 0)),
            covariance_state=covariance_metrics.state,
            covariance_confidence=covariance_metrics.confidence,
            execution_state=execution_metrics.state,
            candidate_state=("accepted" if tier in {"usable", "preferred"} and execution_metrics.state in {"ok", "disabled"} and covariance_metrics.state in {"ok", "disabled"} else "rejected"),
            rejection_reason=(None if tier in {"usable", "preferred"} else tier),
            last_updated_at_ms=int(now_ms),
        )

    def _pair_key(self, inventory_market_id: Optional[str], hedge_market_id: Optional[str]) -> Tuple[str, str]:
        return (str(inventory_market_id or ""), str(hedge_market_id or ""))

    def _record_pair_execution_observation(self, pair_key: Tuple[str, str], execution_ok: bool) -> None:
        stats = self._pair_execution_stats.setdefault(pair_key, {"observed": 0, "ok": 0})
        stats["observed"] += 1
        if execution_ok:
            stats["ok"] += 1

    def _record_pair_acceptance(self, pair_key: Optional[Tuple[str, str]]) -> None:
        if not pair_key:
            return
        stats = self._pair_outcome_stats.setdefault(pair_key, {"accepted": 0, "improved": 0, "failed": 0})
        stats["accepted"] += 1

    def _record_pair_outcome(self, pair_key: Optional[Tuple[str, str]], *, improved: bool) -> None:
        if not pair_key:
            return
        stats = self._pair_outcome_stats.setdefault(pair_key, {"accepted": 0, "improved": 0, "failed": 0})
        if improved:
            stats["improved"] += 1
        else:
            stats["failed"] += 1

    def _execution_availability_score(self, pair_key: Tuple[str, str]) -> float:
        stats = self._pair_execution_stats.get(pair_key, {})
        observed = int(stats.get("observed", 0))
        if observed <= 0:
            return 0.5
        ok_rate = max(0.0, min(1.0, float(stats.get("ok", 0)) / float(observed)))
        # Penalize flash tradability without making cold-start pairs unusable.
        persistence = 0.5 + (0.5 * max(0.0, min(1.0, float(observed) / 4.0)))
        return max(0.0, min(1.0, ok_rate * persistence))

    def _realized_outcome_score(self, pair_key: Tuple[str, str]) -> Tuple[float, str]:
        stats = self._pair_outcome_stats.get(pair_key, {})
        accepted = int(stats.get("accepted", 0))
        improved = int(stats.get("improved", 0))
        failed = int(stats.get("failed", 0))
        if accepted >= 2 and failed >= 2 and improved == 0:
            return 0.0, "validated"
        if accepted < 3:
            return 0.5, "low_confidence"
        return max(0.0, min(1.0, float(improved) / float(accepted))), "validated"

    def _basis_accumulation_flag(self, pair_key: Tuple[str, str]) -> bool:
        stats = self._pair_outcome_stats.get(pair_key, {})
        accepted = int(stats.get("accepted", 0))
        failed = int(stats.get("failed", 0))
        improved = int(stats.get("improved", 0))
        return accepted >= 2 and failed >= 2 and improved == 0

    def _structural_score(
        self,
        *,
        inventory_market: Optional[MarketCandidate],
        hedge_market: MarketCandidate,
        bucket_distance: Optional[int],
    ) -> float:
        if inventory_market is None:
            return 0.0
        score = 0.0
        if self._event_family_for_market(inventory_market) == self._event_family_for_market(hedge_market):
            score += 0.40
        if str(inventory_market.reference_symbol or "").upper() == str(hedge_market.reference_symbol or "").upper():
            score += 0.20
        if self._expiry_bucket_for_market(inventory_market) == self._expiry_bucket_for_market(hedge_market):
            score += 0.20
        if (
            self._contract_family_for_market(inventory_market) == self._contract_family_for_market(hedge_market)
            or (bucket_distance is not None and int(bucket_distance) <= 1)
        ):
            score += 0.20
        return max(0.0, min(1.0, score))

    def _covariance_score(self, covariance_metrics: HedgeCovarianceMetrics) -> float:
        if covariance_metrics.state == "disabled":
            return 0.5
        if covariance_metrics.state != "ok" or covariance_metrics.correlation is None:
            return 0.0
        strength = abs(float(covariance_metrics.correlation))
        floor = max(self._hedge_covariance_min_correlation, 1e-9)
        ceiling = max(self._hedge_covariance_strong_correlation, floor)
        if strength <= floor:
            return 0.0
        return max(0.0, min(1.0, (strength - floor) / max(ceiling - floor, 1e-9)))

    def _beta_stability_score(self, covariance_metrics: HedgeCovarianceMetrics) -> float:
        if covariance_metrics.state == "disabled":
            return 0.5
        if covariance_metrics.state != "ok":
            return 0.0
        clipped = float(covariance_metrics.beta_clipped or 0.0)
        if clipped <= 0.0:
            return 0.0
        usefulness = max(0.0, min(1.0, clipped / max(self._hedge_covariance_beta_clip or 1.0, 1e-9)))
        sign_consistency = max(0.0, min(1.0, float(covariance_metrics.beta_sign_consistency or 0.0)))
        return max(0.0, min(1.0, usefulness * sign_consistency))

    def _cluster_relative_promotes(
        self,
        *,
        pair_relation: HedgePairRelation,
        covariance_metrics: HedgeCovarianceMetrics,
        execution_metrics: HedgeExecutionMetrics,
        top_score: float,
        second_score: Optional[float],
    ) -> bool:
        if pair_relation.hedgeability_tier != "plausible":
            return False
        if execution_metrics.state not in {"ok", "disabled"}:
            return False
        if covariance_metrics.state not in {"ok", "disabled"}:
            return False
        if pair_relation.structural_score < 0.60:
            return False
        if pair_relation.beta_stability_score < 0.40:
            return False
        if pair_relation.execution_availability_score < 0.55:
            return False
        if pair_relation.pair_score < 0.50:
            return False
        if covariance_metrics.state == "ok" and pair_relation.covariance_score <= 0.0:
            return False
        if second_score is not None and float(top_score) < float(second_score) + 500.0:
            return False
        return True

    def _hedgeability_tier(
        self,
        *,
        pair_score: float,
        structural_score: float,
        covariance_metrics: HedgeCovarianceMetrics,
        beta_stability_score: float,
        execution_availability_score: float,
        realized_outcome_score: float,
        confidence_state: str,
        basis_accumulation_flag: bool,
    ) -> str:
        if basis_accumulation_flag or structural_score < 0.35 or execution_availability_score <= 0.05:
            return "not_hedgeable"
        if (
            pair_score >= 0.75
            and structural_score >= 0.60
            and covariance_metrics.confidence == "strong"
            and execution_availability_score >= 0.60
            and realized_outcome_score >= 0.55
            and confidence_state == "validated"
        ):
            return "preferred"
        if (
            pair_score >= 0.55
            and structural_score >= 0.60
            and covariance_metrics.state not in {"weak_co_movement", "insufficient_covariance_history", "stale_covariance_history", "unstable_beta", "boundary_distortion_risk", "covariance_hedge_ratio_too_small"}
            and beta_stability_score >= 0.40
            and execution_availability_score >= 0.40
        ):
            return "usable"
        if pair_score >= 0.35:
            return "plausible"
        return "not_hedgeable"

    def _event_family_for_market(self, market: MarketCandidate) -> Optional[str]:
        raw = market.raw if isinstance(market.raw, dict) else {}
        for key in ("event_ticker", "eventTicker", "series_ticker", "seriesTicker"):
            value = raw.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _expiry_bucket_for_market(self, market: MarketCandidate) -> Optional[str]:
        if market.end_ts_ms is None:
            return None
        return str(int(market.end_ts_ms) // 3_600_000)

    def _contract_family_for_market(self, market: MarketCandidate) -> Optional[str]:
        slug = str(market.slug or "")
        if not slug:
            return None
        parts = slug.split("-")
        if len(parts) <= 1:
            return slug
        return "-".join(parts[:-1])

    def _resolve_hedge_ratio(
        self,
        covariance_ratio_cap: Optional[float],
        gross_ratio_cap: Optional[float],
    ) -> Optional[float]:
        candidates = [value for value in (covariance_ratio_cap, gross_ratio_cap, 1.0) if value is not None]
        if not candidates:
            return None
        return max(0.0, min(1.0, min(candidates)))

    def _execution_metrics(
        self,
        *,
        now_ms: int,
        inventory_token_id: Optional[str],
        hedge_token_id: Optional[str],
        book_manager: BookManager,
    ) -> HedgeExecutionMetrics:
        inventory_book = book_manager.get_book(str(inventory_token_id)) if inventory_token_id else None
        hedge_book = book_manager.get_book(str(hedge_token_id)) if hedge_token_id else None
        if hedge_book is None or hedge_book.best_bid is None or hedge_book.best_ask is None:
            return HedgeExecutionMetrics(None, "no_hedge_book")
        if inventory_book is None or inventory_book.best_bid is None or inventory_book.best_ask is None:
            return HedgeExecutionMetrics(None, "tradability_disappeared")
        if self._hedge_covariance_max_sample_age_ms > 0:
            if (
                hedge_book.last_update_ms is None
                or inventory_book.last_update_ms is None
                or int(now_ms) - int(hedge_book.last_update_ms) > self._hedge_covariance_max_sample_age_ms
                or int(now_ms) - int(inventory_book.last_update_ms) > self._hedge_covariance_max_sample_age_ms
            ):
                return HedgeExecutionMetrics(None, "tradability_disappeared")
        if (
            self._hedge_covariance_max_update_gap_ms > 0
            and hedge_book.last_update_ms is not None
            and inventory_book.last_update_ms is not None
            and abs(int(hedge_book.last_update_ms) - int(inventory_book.last_update_ms)) > self._hedge_covariance_max_update_gap_ms
        ):
            return HedgeExecutionMetrics(None, "asynchronous_book_updates")
        spread = max(0.0, float(hedge_book.best_ask) - float(hedge_book.best_bid))
        depth = float(hedge_book.best_bid_size or 0.0) + float(hedge_book.best_ask_size or 0.0)
        if spread <= 0.0 or depth <= 0.0:
            return HedgeExecutionMetrics(None, "no_hedge_depth_or_spread")
        return HedgeExecutionMetrics(depth / max(spread, 0.01), "ok")

    def _latest_mid_ts(self, token_id: Optional[str]) -> Optional[int]:
        if not token_id:
            return None
        history = self._mid_history_by_token.get(str(token_id))
        if not history:
            return None
        return int(history[-1][0])

    def _boundary_distortion_risk(self, token_id: Optional[str]) -> bool:
        if not token_id:
            return False
        history = self._mid_history_by_token.get(str(token_id))
        if not history:
            return False
        boundary_hits = 0
        total = 0
        lower = self._hedge_covariance_boundary_buffer
        upper = 1.0 - self._hedge_covariance_boundary_buffer
        for _, mid in history:
            total += 1
            if float(mid) <= lower or float(mid) >= upper:
                boundary_hits += 1
        return total > 0 and (boundary_hits / float(total)) >= self._hedge_covariance_boundary_max_fraction

    def _beta_stable(
        self,
        *,
        common_ts: Sequence[int],
        x_vals: Sequence[float],
        y_vals: Sequence[float],
    ) -> bool:
        return self._beta_sign_consistency_score(common_ts=common_ts, x_vals=x_vals, y_vals=y_vals) >= 0.50

    def _beta_sign_consistency_score(
        self,
        *,
        common_ts: Sequence[int],
        x_vals: Sequence[float],
        y_vals: Sequence[float],
    ) -> float:
        if len(common_ts) < max(self._hedge_covariance_min_samples, 6):
            return 1.0
        midpoint = len(common_ts) // 2
        first_beta = self._window_beta(x_vals[:midpoint], y_vals[:midpoint])
        second_beta = self._window_beta(x_vals[midpoint:], y_vals[midpoint:])
        if first_beta is None or second_beta is None:
            return 0.0
        if first_beta == 0.0 or second_beta == 0.0:
            return 0.0
        if (first_beta > 0.0) != (second_beta > 0.0):
            return 0.0
        ratio = max(abs(first_beta), abs(second_beta)) / max(min(abs(first_beta), abs(second_beta)), 1e-12)
        if not math.isfinite(ratio):
            return 0.0
        if ratio <= 1.0:
            return 1.0
        limit = max(1.0, self._hedge_covariance_stability_ratio_max)
        if ratio >= limit:
            return 0.0
        return max(0.0, min(1.0, (limit - ratio) / max(limit - 1.0, 1e-9)))

    def _window_beta(self, x_vals: Sequence[float], y_vals: Sequence[float]) -> Optional[float]:
        if len(x_vals) < 2 or len(y_vals) < 2:
            return None
        mean_x = sum(x_vals) / len(x_vals)
        mean_y = sum(y_vals) / len(y_vals)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals)) / len(x_vals)
        var_y = sum((y - mean_y) ** 2 for y in y_vals) / len(y_vals)
        if not math.isfinite(var_y) or var_y <= 1e-12:
            return None
        beta = cov / var_y
        if not math.isfinite(beta):
            return None
        return beta

    def _covariance_confidence(self, *, correlation: Optional[float], sample_count: int) -> str:
        corr_strength = abs(float(correlation or 0.0))
        if (
            corr_strength >= self._hedge_covariance_strong_correlation
            and int(sample_count) >= self._hedge_covariance_strong_min_samples
        ):
            return "strong"
        if corr_strength >= self._hedge_covariance_min_correlation and int(sample_count) >= self._hedge_covariance_min_samples:
            return "usable"
        if corr_strength > 0.0:
            return "weak"
        return "unknown"

    def _confidence_ratio_cap(self, confidence: Optional[str]) -> Optional[float]:
        return {
            "strong": 1.0,
            "usable": 0.5,
            "weak": 0.25,
        }.get(str(confidence or "").strip().lower())

    def _dominant_rejection_reason(self, rejection_reasons: Sequence[str]) -> Optional[str]:
        if not rejection_reasons:
            return None
        priority = (
            "hedge_failed_no_improvement",
            "stop_open_window",
            "force_flat_window",
            "forced_reduction",
            "hedge_failed_cooldown",
            "tradability_disappeared",
            "no_hedge_book",
            "no_hedge_depth_or_spread",
            "poor_hedge_quality",
            "stale_covariance_history",
            "asynchronous_book_updates",
            "insufficient_covariance_history",
            "weak_co_movement",
            "unstable_beta",
            "boundary_distortion_risk",
            "covariance_hedge_ratio_too_small",
            "gross_increase_too_large",
            "gross_increase_ceiling_exhausted",
            "no_hedge_market",
        )
        reason_set = {str(reason) for reason in rejection_reasons}
        for reason in priority:
            if reason in reason_set:
                return reason
        return str(rejection_reasons[0])

    def _hedge_permission_state(self, *, action: str, dominant_rejection_reason: Optional[str]) -> str:
        if str(action).upper() == "HEDGE" and not dominant_rejection_reason:
            return "accepted"
        reason = str(dominant_rejection_reason or "")
        if reason in {
            "tradability_disappeared",
            "no_hedge_book",
            "no_hedge_depth_or_spread",
            "poor_hedge_quality",
            "stop_open_window",
            "force_flat_window",
            "forced_reduction",
            "hedge_failed_cooldown",
        }:
            return "rejected_execution"
        if reason in {
            "stale_covariance_history",
            "asynchronous_book_updates",
            "insufficient_covariance_history",
            "weak_co_movement",
            "unstable_beta",
            "boundary_distortion_risk",
            "covariance_hedge_ratio_too_small",
        }:
            return "rejected_covariance"
        if reason in {"gross_increase_too_large", "gross_increase_ceiling_exhausted"}:
            return "rejected_caps"
        if reason == "hedge_failed_no_improvement":
            return "hedge_failed"
        return "deferred"

    def _hedge_model_state(
        self,
        *,
        action: str,
        permission_state: Optional[str],
        dominant_rejection_reason: Optional[str],
    ) -> str:
        permission = str(permission_state or "").strip().lower()
        if str(action).upper() == "HEDGE":
            return "accepted"
        if permission:
            return permission
        if dominant_rejection_reason:
            return "rejected_covariance"
        return "unknown"

    def _is_proof_only_lane(self) -> bool:
        return self._hedge_search_profile == "proof-only"

    def _is_crypto_market(self, market: MarketCandidate) -> bool:
        return str(market.reference_symbol or "").upper() in set(self._proof_only_crypto_symbols)

    def _market_end_ts_ms_for_market_id(
        self,
        markets: Sequence[MarketCandidate],
        market_id: Optional[str],
    ) -> Optional[int]:
        if not market_id:
            return None
        for market in markets:
            if str(market.slug) == str(market_id):
                return market.end_ts_ms
        return None

    def _bucket_anchor_for_market(self, market: MarketCandidate) -> Optional[float]:
        raw = market.raw if isinstance(market.raw, dict) else {}
        for key in ("bucket_start", "bucketStart", "floor_strike", "floorStrike", "floor", "strike_floor"):
            value = raw.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return self._bucket_anchor_for_market_id(market.slug)

    def _bucket_anchor_for_market_id(self, market_id: Optional[str]) -> Optional[float]:
        if not market_id:
            return None
        import re

        match = re.search(r"-B(\d+(?:\.\d+)?)", str(market_id))
        if match is None:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    def _bucket_distance(self, anchor_a: Optional[float], anchor_b: Optional[float]) -> Optional[int]:
        if anchor_a is None or anchor_b is None:
            return None
        return int(round(abs(float(anchor_a) - float(anchor_b)) / 100.0))

    def _cluster_id_for_market(self, market: MarketCandidate) -> str:
        raw = market.raw if isinstance(market.raw, dict) else {}
        for key in ("event_ticker", "eventTicker", "series_ticker", "seriesTicker"):
            value = raw.get(key)
            if value not in (None, ""):
                return str(value)
        slug = str(market.slug or market.condition_id or "")
        if "-B" in slug:
            return slug.rsplit("-B", 1)[0]
        return slug or str(market.condition_id)

    def _token_for_side(self, market: MarketCandidate, side: str) -> Optional[str]:
        for token_id in market.token_ids:
            if self._token_outcome_side(market, token_id) == side:
                return str(token_id)
        return None

    def _token_for_market_id_side(
        self,
        markets: Sequence[MarketCandidate],
        market_id: Optional[str],
        side: Optional[str],
    ) -> Optional[str]:
        if not market_id or not side:
            return None
        for market in markets:
            if str(market.slug) == str(market_id):
                return self._token_for_side(market, str(side))
        return None

    def _token_outcome_side(self, market: MarketCandidate, token_id: str) -> str:
        token_key = str(token_id or "").strip().lower()
        if (
            token_key == "yes"
            or token_key.startswith("yes_")
            or token_key.startswith("yes-")
            or token_key.endswith(":yes")
            or token_key.endswith("_yes")
            or token_key.endswith("-yes")
        ):
            return "yes"
        if (
            token_key == "no"
            or token_key.startswith("no_")
            or token_key.startswith("no-")
            or token_key.endswith(":no")
            or token_key.endswith("_no")
            or token_key.endswith("-no")
        ):
            return "no"
        try:
            token_index = list(market.token_ids).index(str(token_id))
        except ValueError:
            token_index = -1
        if 0 <= token_index < len(market.outcomes):
            outcome = str(market.outcomes[token_index] or "").strip().lower()
            if outcome in {"yes", "true", "up", "above", "long"}:
                return "yes"
            if outcome in {"no", "false", "down", "below", "short"}:
                return "no"
        return "unknown"
