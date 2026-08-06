from pathlib import Path

from scripts.run_cluster_calibration_suite import build_suite_plan, _runtime_root_for, _build_three_hour_specs


def test_build_suite_plan_uses_three_hour_template() -> None:
    specs = build_suite_plan(budget_minutes=180)

    assert [spec.key for spec in specs] == [
        "open-market-safety-30m",
        "mixed-60m",
        "skew-proof-20m",
        "hedge-proof-20m",
        "unwind-proof-20m",
        "mixed-confirm-30m",
    ]


def test_build_suite_plan_truncates_to_budget() -> None:
    specs = build_suite_plan(budget_minutes=60)

    assert [spec.key for spec in specs] == ["open-market-safety-30m"]


def test_runtime_root_for_includes_ordered_prefix(tmp_path: Path) -> None:
    spec = _build_three_hour_specs()[1]

    runtime_root = _runtime_root_for(tmp_path, 2, spec)

    assert runtime_root == tmp_path / "02-mixed-60m"
