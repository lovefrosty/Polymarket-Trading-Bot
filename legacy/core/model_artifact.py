from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PlattParams:
    a: float
    c: float


@dataclass(frozen=True)
class ModelArtifact:
    schema_version: str
    feature_order: List[str]
    w: List[float]
    b: float
    offset_mode: Optional[str]
    platt: Optional[PlattParams]
    metadata: Dict[str, Any]


def load_model(path: str | Path) -> ModelArtifact:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError("model_invalid_json")

    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, str):
        raise ValueError("model_schema_missing")
    if schema_version not in {"model_ridge_logit_v1", "model_ridge_logit_offset_v1"}:
        raise ValueError(f"model_schema_unsupported:{schema_version}")

    feature_order = raw.get("feature_order")
    if not isinstance(feature_order, list) or not feature_order:
        raise ValueError("model_feature_order_missing")
    feature_order_list = [str(item) for item in feature_order]

    weights = raw.get("w")
    if not isinstance(weights, list) or not weights:
        raise ValueError("model_weights_missing")
    w = [float(item) for item in weights]

    if len(w) != len(feature_order_list):
        raise ValueError("model_weights_feature_mismatch")

    b_raw = raw.get("b")
    if b_raw is None:
        raise ValueError("model_bias_missing")
    b = float(b_raw)

    offset_mode = raw.get("offset_mode")
    if offset_mode is not None:
        offset_mode = str(offset_mode)

    platt = None
    platt_raw = raw.get("platt")
    if isinstance(platt_raw, dict) and "a" in platt_raw and "c" in platt_raw:
        platt = PlattParams(a=float(platt_raw["a"]), c=float(platt_raw["c"]))

    metadata = {
        key: value
        for key, value in raw.items()
        if key not in {"schema_version", "feature_order", "w", "b", "platt"}
    }

    return ModelArtifact(
        schema_version=schema_version,
        feature_order=feature_order_list,
        w=w,
        b=b,
        offset_mode=offset_mode,
        platt=platt,
        metadata=metadata,
    )
