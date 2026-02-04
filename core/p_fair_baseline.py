from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class BaselineCfg:
    bias: float
    w_mom: float
    w_revert: float
    z_clip: float
    vol_dampen_enabled: bool
    vol_floor: float
    model_version: str = "baseline_v1"


def p_fair_baseline(
    outcome: str,
    z_mom: float,
    z_revert: float,
    vol: float,
    cfg: BaselineCfg,
) -> float:
    z_mom_clip = _clip(z_mom, -cfg.z_clip, cfg.z_clip)
    z_revert_clip = _clip(z_revert, -cfg.z_clip, cfg.z_clip)
    base_logit = cfg.bias + cfg.w_mom * z_mom_clip + cfg.w_revert * z_revert_clip
    base_logit *= vol_dampen(vol, cfg)
    p_up = _sigmoid(base_logit)

    outcome_norm = outcome.strip().lower()
    if "up" in outcome_norm:
        return p_up
    if "down" in outcome_norm:
        return 1.0 - p_up
    raise ValueError(f"unknown_outcome:{outcome}")


def vol_dampen(vol: float, cfg: BaselineCfg) -> float:
    if not cfg.vol_dampen_enabled:
        return 1.0
    if vol is None or not math.isfinite(vol) or vol < 0:
        return 1.0
    scale = 1.0 / (1.0 + vol)
    return _clip(scale, cfg.vol_floor, 1.0)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
