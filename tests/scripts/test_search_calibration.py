from argparse import Namespace

from scripts.search_calibration import (
    DEFAULT_STALE_FAST,
    DEFAULT_STALE_HOLDTAIL,
    build_preset_variants,
    build_variants,
)


def test_build_preset_variants_uses_overnight_profiles() -> None:
    variants = build_preset_variants("overnight_profiles")

    assert [variant.key for variant in variants] == [
        "conservative",
        "proof045",
        "proof040",
        "holdtail",
    ]
    assert variants[0].max_active_markets == 2
    assert variants[1].hedge_threshold_fraction == 0.45
    assert variants[2].hedge_threshold_fraction == 0.40
    assert variants[3].stale_duration_scale == DEFAULT_STALE_HOLDTAIL


def test_build_variants_uses_grid_when_custom_values_are_supplied() -> None:
    args = Namespace(
        preset="overnight_profiles",
        max_active_markets_values="2,3",
        hedge_threshold_fraction_values="0.60,0.45",
        stale_duration_scale_values=str(DEFAULT_STALE_FAST),
        maker_exit_grace_secs_values="2.0",
        force_flat_before_expiry_secs_values="120",
    )

    variants = build_variants(args)

    assert len(variants) == 4
    assert variants[0].key.startswith("grid-01-")
    assert {variant.max_active_markets for variant in variants} == {2, 3}
    assert {variant.hedge_threshold_fraction for variant in variants} == {0.6, 0.45}


def test_build_preset_variants_uses_proof045_max_active_profile() -> None:
    variants = build_preset_variants("proof045_max_active")

    assert [variant.key for variant in variants] == [
        "proof045_m3_control",
        "proof045_m4",
        "proof045_m5",
        "proof045_m6",
    ]
    assert [variant.max_active_markets for variant in variants] == [3, 4, 5, 6]
    assert {variant.hedge_threshold_fraction for variant in variants} == {0.45}
