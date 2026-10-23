"""Tests for Lightener-style brightness curve mapping (no Home Assistant import)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "custom_components/adaptive_lighting/brightness_curves.py"
_SPEC = importlib.util.spec_from_file_location("al_brightness_curves", _PATH)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

BrightnessCurve = _MOD.BrightnessCurve
BrightnessCurveSet = _MOD.BrightnessCurveSet
binary_config_from_threshold = _MOD.binary_config_from_threshold
normalize_curve_config = _MOD.normalize_curve_config
prepare_brightness_config = _MOD.prepare_brightness_config
threshold_from_config = _MOD.threshold_from_config


def test_identity_curve_maps_one_to_one() -> None:
    curve = BrightnessCurve({"100": 100})
    assert curve.translate(0) == 0
    assert curve.translate(128) == 128
    assert curve.translate(255) == 255


def test_late_starter_stays_off_then_ramps() -> None:
    curve = BrightnessCurve({"60": 0, "100": 100})
    assert curve.translate(153) == 0
    assert curve.translate(255) == 255
    mid = round(153 + (255 - 153) / 2)
    assert 100 < curve.translate(mid) < 200


def test_normalize_drops_invalid_points() -> None:
    cleaned = normalize_curve_config({"0": 10, "50": 80, "101": 5, "x": "y"})
    assert cleaned == {"50": 80}


def test_curve_set_defaults_missing_lights() -> None:
    curves = BrightnessCurveSet({"light.a": {"80": 100}})
    assert curves.translate("light.a", 255) == 255
    assert curves.translate("light.missing", 200) == 200


def test_prepare_always_includes_zero_and_full() -> None:
    prepared = prepare_brightness_config({"40": 20})
    assert prepared[0] == (0, 0)
    assert prepared[-1][0] == 255


def test_export_for_ui_includes_samples() -> None:
    curves = BrightnessCurveSet({"light.a": {"50": 100}})
    payload = curves.export_for_ui(["light.a"])
    assert "points" in payload["light.a"]
    assert len(payload["light.a"]["samples"]) == 101
    assert payload["light.a"]["samples"][0] == 0
    assert payload["light.a"]["samples"][50] == 100


def test_binary_threshold_is_step() -> None:
    curve = BrightnessCurve({"45": 100}, binary=True)
    assert curve.threshold == 45
    # 44% room → off, 45% → on
    assert curve.sample_percent(44) == 0
    assert curve.sample_percent(45) == 100
    assert curve.translate(round(44 * 255 / 100)) == 0
    assert curve.translate(round(45 * 255 / 100)) == 255


def test_binary_config_helpers() -> None:
    assert binary_config_from_threshold(1) == {"1": 100}
    assert binary_config_from_threshold(45) == {"44": 0, "45": 100}
    assert threshold_from_config({"44": 0, "45": 100}) == 45


def test_binary_export_marks_threshold() -> None:
    curves = BrightnessCurveSet(
        {"light.switch": {"40": 100}},
        binary_lights={"light.switch"},
    )
    payload = curves.export_for_ui(["light.switch"])
    assert payload["light.switch"]["binary"] is True
    assert payload["light.switch"]["threshold"] == 40
