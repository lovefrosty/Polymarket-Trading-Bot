from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: float
    event_ts: int


@dataclass(frozen=True)
class FeatureVector:
    names: List[str]
    values: List[float]
    event_ts: List[int]

    ORDER = ["mean_reversion", "momentum"]

    @classmethod
    def from_feature_map(cls, features: Dict[str, FeatureValue], decision_ts: int) -> "FeatureVector":
        missing = [name for name in cls.ORDER if name not in features]
        if missing:
            raise ValueError(f"missing_features:{','.join(missing)}")
        extra = [name for name in features.keys() if name not in cls.ORDER]
        if extra:
            raise ValueError(f"unexpected_features:{','.join(sorted(extra))}")

        values: List[float] = []
        event_ts: List[int] = []
        for name in cls.ORDER:
            feature = features[name]
            if feature.event_ts >= decision_ts:
                raise ValueError("feature_event_ts_not_before_decision")
            values.append(float(feature.value))
            event_ts.append(int(feature.event_ts))
        return cls(list(cls.ORDER), values, event_ts)

    def max_event_ts(self) -> int:
        return max(self.event_ts) if self.event_ts else 0
