from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backtests.scientific_method.io_utils import expand_paths, load_jsonl, write_json


@dataclass(frozen=True)
class FeatureDef:
    name: str
    path: str
    class_name: str
    units: str
    expected_range: Optional[Tuple[float, float]]
    missing: str


@dataclass(frozen=True)
class CalibrationSpec:
    raw: Dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "calibration")

    @property
    def inputs(self) -> Dict[str, Any]:
        return self.raw.get("inputs") or {}

    @property
    def label(self) -> Dict[str, Any]:
        return self.raw.get("label") or {}

    @property
    def model(self) -> Dict[str, Any]:
        return self.raw.get("model") or {}

    @property
    def orthogonalize(self) -> Dict[str, Any]:
        return self.raw.get("orthogonalize") or {}

    @property
    def standardize(self) -> Dict[str, Any]:
        return self.raw.get("standardize") or {}

    @property
    def stability(self) -> Dict[str, Any]:
        return self.raw.get("stability") or {}

    @property
    def random_seed(self) -> int:
        return int(self.raw.get("random_seed") or 0)

    @property
    def require_feature_asof(self) -> bool:
        return bool(self.raw.get("require_feature_asof", True))


def run_calibration(spec_path: Path, output_dir_override: Optional[Path] = None) -> Dict[str, Any]:
    spec = CalibrationSpec(json.loads(spec_path.read_text()))
    output_dir = output_dir_override or Path("backtests/scientific_method/calibration")
    output_dir.mkdir(parents=True, exist_ok=True)
    feature_defs = _load_feature_defs(spec.raw.get("features") or [])
    if not feature_defs:
        raise ValueError("no_feature_defs")

    decision_paths = _expand_with_logs(spec.inputs.get("decision_paths") or [])
    reference_paths = _expand_with_logs(spec.inputs.get("reference_paths") or [])
    if not decision_paths:
        raise ValueError("missing_decision_paths")
    if not reference_paths:
        raise ValueError("missing_reference_paths")

    decisions = load_jsonl(decision_paths)
    references = load_jsonl(reference_paths)

    label_mode = str(spec.label.get("mode") or "directional")
    horizon_sec = int(spec.label.get("horizon_sec") or 15)
    if horizon_sec <= 0:
        raise ValueError("label_horizon_invalid")

    ref_series = _build_reference_series(references)
    feature_contract = _load_feature_contract(spec.raw.get("feature_contract_path"))
    label_contract = _load_label_contract(spec.raw.get("label_contract_path"))
    required_features = _required_features(feature_defs, feature_contract)

    rows, dropped, coverage = _build_rows(
        decisions,
        ref_series,
        feature_defs,
        horizon_sec=horizon_sec,
        label_mode=label_mode,
        require_feature_asof=spec.require_feature_asof,
    )
    coverage_report = _coverage_report(coverage, required_features, label_contract)
    if not coverage_report["ok"]:
        report_path = output_dir / "calibration_drop_report.json"
        write_json(report_path, coverage_report)
        raise ValueError("calibration_contract_failed")

    rows = _filter_rows(rows, required_features)
    if not rows:
        report_path = output_dir / "calibration_drop_report.json"
        coverage_report["dropped_rows"] = dropped
        write_json(report_path, coverage_report)
        raise ValueError(f"no_calibration_rows:{json.dumps(dropped, sort_keys=True)}")

    rows.sort(key=lambda row: row["t_decision_ms"])
    feature_names = [feat.name for feat in feature_defs]
    X = [[_coerce_feature_value(row["features"][name]) for name in feature_names] for row in rows]
    y = [row["label"] for row in rows]
    times = [row["t_decision_ms"] for row in rows]

    train_frac = float(spec.model.get("train_frac") or 0.7)
    if not (0.0 < train_frac < 1.0):
        raise ValueError("train_frac_invalid")
    split_idx = max(1, int(len(rows) * train_frac))

    X_train = X[:split_idx]
    y_train = y[:split_idx]
    X_test = X[split_idx:]
    y_test = y[split_idx:]

    scaler = _fit_scaler(X_train, feature_names)
    X_train_scaled = _apply_scaler(X_train, scaler)
    X_test_scaled = _apply_scaler(X_test, scaler)

    ortho_params, X_train_ortho = _orthogonalize(
        X_train_scaled, feature_defs, float(spec.orthogonalize.get("ridge_lambda") or 1e-6)
    )
    X_test_ortho = _apply_orthogonalization(X_test_scaled, feature_defs, ortho_params)

    model_type = str(spec.model.get("type") or "ridge_linear")
    l2_lambda = float(spec.model.get("l2_lambda") or 1.0)
    max_iter = int(spec.model.get("max_iter") or 200)
    tol = float(spec.model.get("tol") or 1e-6)

    if model_type == "ridge_linear":
        w, b, train_metrics = _fit_ridge_linear(X_train_ortho, y_train, l2_lambda)
        test_metrics = _eval_ridge_linear(X_test_ortho, y_test, w, b)
        std_err = _ridge_std_err(X_train_ortho, y_train, w, l2_lambda)
    elif model_type == "ridge_logistic":
        y_train_bin = _to_binary_labels(y_train)
        y_test_bin = _to_binary_labels(y_test)
        w, b, train_metrics = _fit_ridge_logistic(
            X_train_ortho, y_train_bin, l2_lambda, max_iter, tol, spec.random_seed
        )
        test_metrics = _eval_ridge_logistic(X_test_ortho, y_test_bin, w, b)
        std_err = [None] * len(feature_names)
    else:
        raise ValueError(f"unknown_model_type:{model_type}")

    corr_matrix = _corr_matrix(X_train_ortho)
    stability = _weight_stability(
        X_train_ortho,
        y_train,
        times[:split_idx],
        feature_names,
        model_type,
        l2_lambda,
        max_iter,
        tol,
        spec.random_seed,
        int(spec.stability.get("time_slices") or 3),
        int(spec.stability.get("min_slice_size") or 50),
    )

    sign_stability = stability["sign_stability"]
    dominance = _weight_dominance(w)
    oos_ok = _oos_ok(model_type, test_metrics)
    sign_ok = all(val is None or val >= 0.5 for val in sign_stability.values())
    dominance_ok = dominance is None or dominance <= 0.5
    pass_fail = "PASS" if (oos_ok and sign_ok and dominance_ok) else "FAIL"

    weights_path = output_dir / "calibrated_weights.json"
    summary_path = output_dir / "calibration_summary.csv"
    report_path = output_dir / "calibration_report.md"

    weights_payload = {
        "schema_version": "calibrated_weights_v1",
        "name": spec.name,
        "label": {"mode": label_mode, "horizon_sec": horizon_sec},
        "model": {
            "type": model_type,
            "l2_lambda": l2_lambda,
            "max_iter": max_iter,
            "tol": tol,
        },
        "feature_order": feature_names,
        "weights": {name: float(weight) for name, weight in zip(feature_names, w)},
        "intercept": float(b),
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "scaler": scaler,
        "orthogonalization": ortho_params,
        "dropped_rows": dropped,
        "coverage": coverage_report,
        "stability": stability,
    }
    write_json(weights_path, weights_payload)

    _write_summary_csv(summary_path, feature_names, w, std_err, sign_stability)
    report_path.write_text(
        _render_report(
            pass_fail,
            feature_defs,
            weights_payload,
            corr_matrix,
            dominance,
            oos_ok,
        ),
        encoding="utf-8",
    )

    return {
        "weights_path": str(weights_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "pass_fail": pass_fail,
    }


def _expand_with_logs(patterns: Sequence[str], logs_dir: Path = Path("./logs")) -> List[str]:
    expanded = expand_paths(patterns)
    if expanded:
        return expanded
    fallback = [str(logs_dir / pattern) for pattern in patterns]
    return expand_paths(fallback)


def _load_feature_defs(items: Sequence[Dict[str, Any]]) -> List[FeatureDef]:
    defs: List[FeatureDef] = []
    for item in items:
        name = str(item.get("name") or "")
        path = str(item.get("path") or "")
        class_name = str(item.get("class") or "")
        units = str(item.get("units") or "unitless")
        expected_range = item.get("expected_range")
        missing = str(item.get("missing") or "keep")
        if not name or not path or not class_name:
            raise ValueError("feature_def_missing_fields")
        if expected_range is not None:
            expected_range = (float(expected_range[0]), float(expected_range[1]))
        defs.append(
            FeatureDef(
                name=name,
                path=path,
                class_name=class_name,
                units=units,
                expected_range=expected_range,
                missing=missing,
            )
        )
    return defs


def _load_feature_contract(path: Optional[str]) -> Dict[str, Any]:
    contract_path = Path(path) if path else Path("feature_contract.json")
    if not contract_path.exists():
        raise ValueError(f"missing_feature_contract:{contract_path}")
    return json.loads(contract_path.read_text())


def _load_label_contract(path: Optional[str]) -> Dict[str, Any]:
    contract_path = Path(path) if path else Path("label_contract.json")
    if not contract_path.exists():
        raise ValueError(f"missing_label_contract:{contract_path}")
    return json.loads(contract_path.read_text())


def _required_features(feature_defs: List[FeatureDef], contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = contract.get("features") or []
    if items:
        output = []
        for item in items:
            output.append(
                {
                    "name": str(item.get("name") or ""),
                    "required": bool(item.get("required", True)),
                    "missing_policy": str(item.get("missing_policy") or "keep"),
                }
            )
        return output
    output = []
    for feat in feature_defs:
        output.append(
            {"name": feat.name, "required": True, "missing_policy": feat.missing}
        )
    return output


def _coverage_report(
    coverage: Dict[str, Any],
    required_features: List[Dict[str, Any]],
    label_contract: Dict[str, Any],
) -> Dict[str, Any]:
    required_coverage = float(label_contract.get("required_coverage") or 0.8)
    feature_stats = coverage.get("features", {})
    total = int(coverage.get("total_rows") or 0)
    label_present = int(coverage.get("label_present") or 0)
    label_coverage = 0.0 if total == 0 else label_present / float(total)

    failures: List[str] = []
    if label_coverage < required_coverage:
        failures.append("label_coverage_below_threshold")

    feature_coverage: Dict[str, float] = {}
    for feat in required_features:
        name = feat.get("name")
        if not name:
            continue
        stats = feature_stats.get(name) or {}
        present = int(stats.get("present") or 0)
        coverage_val = 0.0 if total == 0 else present / float(total)
        feature_coverage[name] = coverage_val
        if feat.get("required", True) and coverage_val < required_coverage:
            failures.append(f"feature_coverage_below_threshold:{name}")

    return {
        "schema_version": "calibration_contract_report_v1",
        "total_rows": total,
        "label_present": label_present,
        "label_coverage": label_coverage,
        "feature_coverage": feature_coverage,
        "required_coverage": required_coverage,
        "failures": sorted(set(failures)),
        "ok": not failures,
    }


def _filter_rows(rows: List[Dict[str, Any]], required_features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    required = [feat for feat in required_features if feat.get("required", True)]
    output: List[Dict[str, Any]] = []
    for row in rows:
        present = row.get("feature_present") or {}
        missing = False
        for feat in required:
            name = feat.get("name")
            if not name:
                continue
            if not present.get(name, False):
                if feat.get("missing_policy") == "drop":
                    missing = True
                    break
        if missing:
            continue
        output.append(row)
    return output


def _build_reference_series(records: List[Dict[str, Any]]) -> Dict[str, List[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = {}
    for record in records:
        if record.get("channel") != "reference":
            continue
        raw = record.get("raw") or {}
        symbol = raw.get("symbol") or record.get("market")
        if symbol is None:
            continue
        t_recv = record.get("t_recv_wall_ms") or raw.get("t_recv_wall_ms")
        if t_recv is None:
            continue
        value = raw.get("value") or raw.get("mid") or raw.get("price")
        if value is None:
            continue
        try:
            ts = int(t_recv)
            px = float(value)
        except (TypeError, ValueError):
            continue
        series.setdefault(str(symbol), []).append((ts, px))
    for points in series.values():
        points.sort(key=lambda item: item[0])
    return series


def _build_rows(
    decisions: List[Dict[str, Any]],
    reference_series: Dict[str, List[Tuple[int, float]]],
    feature_defs: List[FeatureDef],
    horizon_sec: int,
    label_mode: str,
    require_feature_asof: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    dropped = {
        "missing_symbol": 0,
        "missing_label": 0,
        "missing_feature": 0,
        "missing_asof": 0,
    }
    feature_stats: Dict[str, Dict[str, int]] = {feat.name: {"present": 0} for feat in feature_defs}
    total_rows = 0
    label_present = 0
    for record in decisions:
        t_decision_ms = record.get("t_decision_wall_ms")
        if t_decision_ms is None:
            continue
        symbol = _extract_symbol(record)
        if symbol is None:
            dropped["missing_symbol"] += 1
            continue
        total_rows += 1
        points = reference_series.get(symbol)
        if not points:
            dropped["missing_label"] += 1
            continue

        label = _build_label(points, int(t_decision_ms), horizon_sec, label_mode)
        if label is None:
            dropped["missing_label"] += 1
            continue
        label_present += 1

        feature_asof_ts = _feature_asof_ts(record)
        if feature_asof_ts is None:
            if require_feature_asof:
                dropped["missing_asof"] += 1
                continue
        else:
            if int(feature_asof_ts) >= int(t_decision_ms):
                raise ValueError("feature_from_future")

        features: Dict[str, Optional[float]] = {}
        feature_present: Dict[str, bool] = {}
        missing = False
        for feat in feature_defs:
            value = _extract_feature(record, feat.path)
            if value is None or not math.isfinite(float(value)):
                feature_present[feat.name] = False
                if feat.missing == "drop":
                    missing = True
                features[feat.name] = None
            else:
                feature_present[feat.name] = True
                features[feat.name] = float(value)
                feature_stats[feat.name]["present"] += 1
        if missing:
            dropped["missing_feature"] += 1
            continue

        rows.append(
            {
                "t_decision_ms": int(t_decision_ms),
                "symbol": symbol,
                "label": label,
                "features": features,
                "feature_present": feature_present,
            }
        )
    coverage = {"total_rows": total_rows, "label_present": label_present, "features": feature_stats}
    return rows, dropped, coverage


def _extract_symbol(record: Dict[str, Any]) -> Optional[str]:
    notes = record.get("notes") or {}
    resolved = notes.get("resolved_market") or {}
    symbol = resolved.get("reference_symbol") or record.get("reference_symbol")
    if symbol is None:
        return None
    return str(symbol)


def _feature_asof_ts(record: Dict[str, Any]) -> Optional[int]:
    value = record.get("feature_asof_ts_ms")
    if value is not None:
        return int(value)
    notes = record.get("notes") or {}
    value = notes.get("feature_asof_ts_ms")
    if value is not None:
        return int(value)
    return None


def _build_label(
    points: List[Tuple[int, float]],
    t_decision_ms: int,
    horizon_sec: int,
    mode: str,
) -> Optional[float]:
    price_now = _price_at_or_after(points, t_decision_ms)
    price_future = _price_at_or_after(points, t_decision_ms + horizon_sec * 1000)
    if price_now is None or price_future is None:
        return None
    p0, _ = price_now
    p1, _ = price_future
    if p0 <= 0 or p1 <= 0:
        return None
    if mode == "directional":
        if p1 > p0:
            return 1.0
        if p1 < p0:
            return 0.0
        return 0.0
    if mode == "return_bps":
        return 10000.0 * math.log(p1 / p0)
    raise ValueError(f"unknown_label_mode:{mode}")


def _price_at_or_after(points: List[Tuple[int, float]], ts_ms: int) -> Optional[Tuple[float, int]]:
    lo = 0
    hi = len(points)
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid][0] < ts_ms:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(points):
        return None
    return points[lo][1], points[lo][0]


def _extract_feature(record: Dict[str, Any], path: str) -> Optional[float]:
    if path.startswith("calc."):
        return _calc_feature(record, path[len("calc.") :])
    current: Any = record
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def _coerce_feature_value(value: Optional[float]) -> float:
    if value is None or not math.isfinite(float(value)):
        return 0.0
    return float(value)


def _calc_feature(record: Dict[str, Any], name: str) -> Optional[float]:
    if name == "edge_bps_buy":
        p_fair = record.get("p_fair")
        p_exec = record.get("p_market_exec_buy")
        if p_fair is None or p_exec is None:
            return None
        return 10000.0 * (float(p_fair) - float(p_exec))
    if name == "edge_bps_sell":
        p_fair = record.get("p_fair")
        p_exec = record.get("p_market_exec_sell")
        if p_fair is None or p_exec is None:
            return None
        return 10000.0 * (float(p_exec) - float(p_fair))
    return None


def _fit_scaler(X: List[List[float]], names: List[str]) -> Dict[str, Dict[str, float]]:
    np = _require_numpy()
    arr = np.asarray(X, dtype=float)
    med = np.median(arr, axis=0)
    mad = np.median(np.abs(arr - med), axis=0)
    params: Dict[str, Dict[str, float]] = {}
    for idx, name in enumerate(names):
        scale = float(mad[idx])
        if not math.isfinite(scale) or scale == 0.0:
            scale = 1.0
        params[name] = {"median": float(med[idx]), "mad": scale}
    return params


def _apply_scaler(X: List[List[float]], params: Dict[str, Dict[str, float]]) -> List[List[float]]:
    scaled: List[List[float]] = []
    names = list(params.keys())
    for row in X:
        out: List[float] = []
        for idx, name in enumerate(names):
            meta = params[name]
            out.append((float(row[idx]) - meta["median"]) / meta["mad"])
        scaled.append(out)
    return scaled


def _orthogonalize(
    X: List[List[float]],
    feature_defs: List[FeatureDef],
    ridge_lambda: float,
) -> Tuple[Dict[str, Any], List[List[float]]]:
    np = _require_numpy()
    arr = np.asarray(X, dtype=float)
    params: Dict[str, Any] = {"method": "residualize", "ridge_lambda": ridge_lambda, "params": {}}
    by_class: Dict[str, List[int]] = {}
    for idx, feat in enumerate(feature_defs):
        by_class.setdefault(feat.class_name, []).append(idx)

    ortho = arr.copy()
    for class_name, indices in by_class.items():
        for pos, idx in enumerate(indices):
            if pos == 0:
                params["params"][feature_defs[idx].name] = {
                    "class": class_name,
                    "depends_on": [],
                    "coeffs": [],
                }
                continue
            prev_indices = indices[:pos]
            X_prev = ortho[:, prev_indices]
            y = ortho[:, idx]
            w = _ridge_solution(X_prev, y, ridge_lambda)
            resid = y - X_prev @ w
            ortho[:, idx] = resid
            params["params"][feature_defs[idx].name] = {
                "class": class_name,
                "depends_on": [feature_defs[j].name for j in prev_indices],
                "coeffs": [float(val) for val in w],
            }
    return params, ortho.tolist()


def _apply_orthogonalization(
    X: List[List[float]],
    feature_defs: List[FeatureDef],
    params: Dict[str, Any],
) -> List[List[float]]:
    np = _require_numpy()
    arr = np.asarray(X, dtype=float)
    meta = params.get("params") or {}
    name_to_idx = {feat.name: idx for idx, feat in enumerate(feature_defs)}
    ortho = arr.copy()
    for feat in feature_defs:
        cfg = meta.get(feat.name)
        if not cfg:
            continue
        depends = cfg.get("depends_on") or []
        coeffs = cfg.get("coeffs") or []
        if not depends:
            continue
        idx = name_to_idx[feat.name]
        prev_indices = [name_to_idx[name] for name in depends]
        X_prev = ortho[:, prev_indices]
        w = np.asarray(coeffs, dtype=float)
        ortho[:, idx] = ortho[:, idx] - X_prev @ w
    return ortho.tolist()


def _fit_ridge_linear(
    X: List[List[float]],
    y: List[float],
    l2_lambda: float,
) -> Tuple[List[float], float, Dict[str, float]]:
    np = _require_numpy()
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n = X_arr.shape[0]
    X_aug = np.column_stack([np.ones(n), X_arr])
    I = np.eye(X_aug.shape[1])
    I[0, 0] = 0.0
    w = np.linalg.solve(X_aug.T @ X_aug + l2_lambda * I, X_aug.T @ y_arr)
    b = float(w[0])
    weights = w[1:]
    preds = X_aug @ w
    metrics = _metrics_regression(preds, y_arr)
    return weights.tolist(), b, metrics


def _eval_ridge_linear(
    X: List[List[float]],
    y: List[float],
    w: List[float],
    b: float,
) -> Dict[str, float]:
    np = _require_numpy()
    if not X:
        return {"count": 0, "r2": None, "mse": None}
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    preds = X_arr @ np.asarray(w) + b
    return _metrics_regression(preds, y_arr)


def _fit_ridge_logistic(
    X: List[List[float]],
    y: List[float],
    l2_lambda: float,
    max_iter: int,
    tol: float,
    seed: int,
) -> Tuple[List[float], float, Dict[str, float]]:
    np = _require_numpy()
    np.random.seed(seed)
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n, d = X_arr.shape
    w = np.zeros(d, dtype=float)
    b = 0.0
    for i in range(max_iter):
        logits = X_arr @ w + b
        logits = np.clip(logits, -50.0, 50.0)
        p = 1.0 / (1.0 + np.exp(-logits))
        grad_w = (X_arr.T @ (p - y_arr)) / n + l2_lambda * w
        grad_b = float((p - y_arr).mean())
        grad_norm = float(np.sqrt((grad_w**2).sum() + grad_b**2))
        if grad_norm < tol:
            break
        step = 0.5 / (1.0 + 0.01 * i)
        w -= step * grad_w
        b -= step * grad_b
    preds = _sigmoid(X_arr @ w + b)
    metrics = _metrics_classification(preds, y_arr)
    metrics["iterations"] = i + 1
    return w.tolist(), float(b), metrics


def _eval_ridge_logistic(
    X: List[List[float]],
    y: List[float],
    w: List[float],
    b: float,
) -> Dict[str, float]:
    np = _require_numpy()
    if not X:
        return {"count": 0, "logloss": None, "brier": None, "accuracy": None}
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    preds = _sigmoid(X_arr @ np.asarray(w) + b)
    return _metrics_classification(preds, y_arr)


def _to_binary_labels(y: List[float]) -> List[float]:
    return [1.0 if val > 0 else 0.0 for val in y]


def _ridge_solution(X: Any, y: Any, l2_lambda: float):
    np = _require_numpy()
    n, d = X.shape
    if d == 0:
        return np.zeros(0)
    return np.linalg.solve(X.T @ X + l2_lambda * np.eye(d), X.T @ y)


def _metrics_regression(preds: Any, y: Any) -> Dict[str, float]:
    np = _require_numpy()
    preds = np.asarray(preds, dtype=float)
    y = np.asarray(y, dtype=float)
    if preds.size == 0:
        return {"count": 0, "r2": None, "mse": None}
    mse = float(((preds - y) ** 2).mean())
    denom = float(((y - y.mean()) ** 2).sum())
    r2 = None if denom == 0 else float(1.0 - ((preds - y) ** 2).sum() / denom)
    return {"count": int(len(y)), "r2": r2, "mse": mse}


def _metrics_classification(preds: Any, y: Any) -> Dict[str, float]:
    np = _require_numpy()
    eps = 1e-8
    preds = np.clip(np.asarray(preds, dtype=float), eps, 1.0 - eps)
    y = np.asarray(y, dtype=float)
    logloss = float(-(y * np.log(preds) + (1 - y) * np.log(1 - preds)).mean())
    brier = float(((preds - y) ** 2).mean())
    acc = float(((preds >= 0.5) == (y >= 0.5)).mean())
    return {"count": int(len(y)), "logloss": logloss, "brier": brier, "accuracy": acc}


def _sigmoid(x: Any) -> Any:
    np = _require_numpy()
    x = np.asarray(x, dtype=float)
    x = np.clip(x, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-x))


def _ridge_std_err(
    X: List[List[float]],
    y: List[float],
    w: List[float],
    l2_lambda: float,
) -> List[Optional[float]]:
    np = _require_numpy()
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n, d = X_arr.shape
    if n <= d:
        return [None] * d
    preds = X_arr @ np.asarray(w)
    resid = y_arr - preds
    sigma2 = float((resid**2).sum() / (n - d))
    cov = np.linalg.inv(X_arr.T @ X_arr + l2_lambda * np.eye(d)) * sigma2
    return [float(math.sqrt(max(cov[i, i], 0.0))) for i in range(d)]


def _corr_matrix(X: List[List[float]]) -> List[List[Optional[float]]]:
    np = _require_numpy()
    if not X:
        return []
    arr = np.asarray(X, dtype=float)
    n, d = arr.shape
    if d == 0:
        return []
    if d == 1:
        return [[1.0]]
    means = arr.mean(axis=0)
    centered = arr - means
    var = (centered**2).mean(axis=0)
    corr: List[List[Optional[float]]] = []
    for i in range(d):
        row: List[Optional[float]] = []
        for j in range(d):
            denom = math.sqrt(float(var[i] * var[j]))
            if denom == 0.0:
                row.append(None)
                continue
            cov = float((centered[:, i] * centered[:, j]).mean())
            row.append(cov / denom)
        corr.append(row)
    return corr


def _weight_stability(
    X: List[List[float]],
    y: List[float],
    times: List[int],
    feature_names: List[str],
    model_type: str,
    l2_lambda: float,
    max_iter: int,
    tol: float,
    seed: int,
    slices: int,
    min_slice_size: int,
) -> Dict[str, Any]:
    if slices <= 1 or len(X) < min_slice_size:
        return {"sign_stability": {name: None for name in feature_names}, "slice_weights": {}}
    np = _require_numpy()
    order = np.argsort(np.asarray(times))
    slice_size = max(min_slice_size, len(X) // slices)
    slice_weights: Dict[str, Dict[str, float]] = {}
    signs: Dict[str, List[int]] = {name: [] for name in feature_names}
    for i in range(slices):
        start = i * slice_size
        end = min(len(X), start + slice_size)
        if end - start < min_slice_size:
            continue
        idx = order[start:end]
        X_slice = [X[int(j)] for j in idx]
        y_slice = [y[int(j)] for j in idx]
        if model_type == "ridge_linear":
            w, _, _ = _fit_ridge_linear(X_slice, y_slice, l2_lambda)
        else:
            y_bin = _to_binary_labels(y_slice)
            w, _, _ = _fit_ridge_logistic(X_slice, y_bin, l2_lambda, max_iter, tol, seed)
        slice_weights[str(i)] = {name: float(val) for name, val in zip(feature_names, w)}
        for name, val in zip(feature_names, w):
            signs[name].append(1 if val > 0 else -1 if val < 0 else 0)

    sign_stability: Dict[str, Optional[float]] = {}
    for name, vals in signs.items():
        if not vals:
            sign_stability[name] = None
            continue
        majority = 1 if vals.count(1) >= vals.count(-1) else -1
        sign_stability[name] = sum(1 for val in vals if val == majority) / len(vals)
    return {"sign_stability": sign_stability, "slice_weights": slice_weights}


def _weight_dominance(weights: List[float]) -> Optional[float]:
    if not weights:
        return None
    total = sum(abs(w) for w in weights)
    if total == 0:
        return None
    return max(abs(w) for w in weights) / total


def _oos_ok(model_type: str, metrics: Dict[str, Any]) -> bool:
    if model_type == "ridge_linear":
        r2 = metrics.get("r2")
        return r2 is not None and r2 > 0.0
    logloss = metrics.get("logloss")
    return logloss is not None and math.isfinite(float(logloss))


def _write_summary_csv(
    path: Path,
    feature_names: List[str],
    weights: List[float],
    std_err: List[Optional[float]],
    sign_stability: Dict[str, Optional[float]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["feature", "weight", "std_error", "sign_stability"])
        for idx, name in enumerate(feature_names):
            se = std_err[idx] if idx < len(std_err) else None
            writer.writerow([name, weights[idx], se, sign_stability.get(name)])


def _render_report(
    pass_fail: str,
    feature_defs: List[FeatureDef],
    weights_payload: Dict[str, Any],
    corr_matrix: List[List[Optional[float]]],
    dominance: Optional[float],
    oos_ok: bool,
) -> str:
    lines: List[str] = []
    lines.append(f"{pass_fail} calibration")
    lines.append("")
    lines.append("Feature definitions:")
    for feat in feature_defs:
        lines.append(
            f"- {feat.name}: path={feat.path} class={feat.class_name} units={feat.units} "
            f"range={feat.expected_range} missing={feat.missing}"
        )
    lines.append("")
    lines.append("Model:")
    model = weights_payload.get("model") or {}
    lines.append(f"- type={model.get('type')} l2_lambda={model.get('l2_lambda')}")
    lines.append("")
    lines.append("Metrics:")
    lines.append(f"- train={json.dumps(weights_payload.get('train_metrics'), sort_keys=True)}")
    lines.append(f"- test={json.dumps(weights_payload.get('test_metrics'), sort_keys=True)}")
    lines.append(f"- oos_ok={oos_ok}")
    lines.append(f"- weight_dominance={dominance}")
    lines.append("")
    lines.append("Correlation (post-orthogonalization):")
    lines.append(json.dumps(corr_matrix, sort_keys=True))
    lines.append("")
    lines.append("Stability:")
    stability = weights_payload.get("stability") or {}
    lines.append(json.dumps(stability, sort_keys=True))
    lines.append("")
    lines.append("Forward compatibility:")
    lines.append("- regime_conditional_weights: not implemented")
    lines.append("- bayesian_shrinkage: not implemented")
    lines.append("- cross_asset_joint_calibration: not implemented")
    lines.append("- toxicity_weight_decay: not implemented")
    return "\n".join(lines)


def _require_numpy():
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("numpy_required") from exc
    return np
