"""Lightener-style brightness curve mapping for Adaptive Lighting.

Ported from https://github.com/fredck/lightener (MIT) so each real light can
follow its own response curve while Adaptive Lighting still drives room-level
brightness and colour temperature.

Binary (on/off) lights use a single room-brightness threshold: off below it,
fully on at/above it. In the editor they appear as vertical lines.
"""

from __future__ import annotations

from typing import Any

# Percent keys/values as stored in config (1–100 virtual → 0–100 real).
CurveConfig = dict[str, int | str]

DEFAULT_BINARY_THRESHOLD = 50


def _value_to_brightness(percent: int) -> int:
    """Map 1–100% to 1–255 (Home Assistant brightness scale)."""
    if percent <= 0:
        return 0
    return max(1, min(255, round(percent * 255 / 100)))


def _brightness_to_percent(brightness: int) -> int:
    """Map 0–255 brightness to 0–100%."""
    if brightness <= 0:
        return 0
    return max(1, min(100, round(brightness * 100 / 255)))


def translate_config_to_brightness(config: CurveConfig) -> dict[int, int]:
    """Convert 0–100% curve config to 0–255 brightness levels."""
    return {
        _value_to_brightness(int(k)): (
            0 if int(v) == 0 else _value_to_brightness(int(v))
        )
        for k, v in config.items()
    }


def prepare_brightness_config(config: CurveConfig) -> list[tuple[int, int]]:
    """Normalize curve points: force 0→0, default 255→255, sort by virtual level."""
    levels = translate_config_to_brightness(config)
    levels[0] = 0
    levels.setdefault(255, 255)
    return sorted(levels.items())


def scale_ranged_value_to_int_range(
    source_range: tuple[float, float],
    target_range: tuple[float, float],
    value: float,
) -> int:
    """Scale a value from one range to another and return an integer."""
    a, b = source_range
    c, d = target_range
    if a == b:
        return round(c)
    return round(c + ((value - a) * (d - c)) / (b - a))


def create_brightness_map(config: list[tuple[int, int]]) -> dict[int, int]:
    """Create a mapping of virtual (room) levels to entity levels (0–255)."""
    brightness_map = {0: 0}
    for i in range(1, len(config)):
        start, end = config[i - 1][0], config[i][0]
        start_value, end_value = config[i - 1][1], config[i][1]
        for j in range(start + 1, end + 1):
            brightness_map[j] = scale_ranged_value_to_int_range(
                (start, end),
                (start_value, end_value),
                j,
            )
    return brightness_map


def create_reverse_brightness_map(
    config: list[tuple[int, int]],
    lightener_levels: dict[int, int],
) -> dict[int, list[int]]:
    """Map each entity brightness to possible virtual (room) levels."""
    reverse_brightness_map: dict[int, list[int]] = {i: [] for i in range(256)}
    for k, v in lightener_levels.items():
        reverse_brightness_map[v].append(k)

    for i in range(1, len(config)):
        start, end = config[i - 1][0], config[i][0]
        start_value, end_value = config[i - 1][1], config[i][1]
        if start_value != end_value:
            order = 1 if start_value < end_value else -1
            for j in range(start_value, end_value + order, order):
                entity_level = scale_ranged_value_to_int_range(
                    (start_value, end_value),
                    (start, end),
                    j,
                )
                if entity_level not in reverse_brightness_map[j]:
                    reverse_brightness_map[j].append(entity_level)
    return reverse_brightness_map


def default_curve_config() -> CurveConfig:
    """Identity curve: room brightness maps 1:1 to the light."""
    return {"100": 100}


def normalize_curve_config(config: CurveConfig | None) -> CurveConfig:
    """Return a sanitized percent→percent curve config."""
    if not config:
        return default_curve_config()
    cleaned: CurveConfig = {}
    for key, value in config.items():
        try:
            left = int(key)
            right = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= left <= 100 and 0 <= right <= 100:
            cleaned[str(left)] = right
    return cleaned or default_curve_config()


def binary_config_from_threshold(threshold: int) -> CurveConfig:
    """Build a step config: off below threshold, on from threshold upward."""
    threshold = max(1, min(100, int(threshold)))
    if threshold <= 1:
        return {"1": 100}
    return {str(threshold - 1): 0, str(threshold): 100}


def threshold_from_config(config: CurveConfig | None) -> int:
    """Infer an on/off threshold from a stored curve config."""
    cleaned = normalize_curve_config(config)
    # Prefer the lowest virtual % where the light becomes non-zero.
    on_points = sorted(int(k) for k, v in cleaned.items() if int(v) > 0)
    if on_points:
        return max(1, min(100, on_points[0]))
    # All zeros (or only off markers): use the highest off marker.
    off_points = sorted(int(k) for k, v in cleaned.items() if int(v) == 0)
    if off_points:
        return max(1, min(100, off_points[-1]))
    return DEFAULT_BINARY_THRESHOLD


class BrightnessCurve:
    """Interpolated brightness curve for one real light."""

    def __init__(
        self,
        config: CurveConfig | None = None,
        *,
        binary: bool = False,
    ) -> None:
        self.binary = binary
        if binary:
            self.threshold = threshold_from_config(config)
            self.config = binary_config_from_threshold(self.threshold)
        else:
            self.threshold = DEFAULT_BINARY_THRESHOLD
            self.config = normalize_curve_config(config)

        prepared = prepare_brightness_config(self.config)
        self._levels = create_brightness_map(prepared)
        self._reverse = create_reverse_brightness_map(prepared, self._levels)

    def set_binary(self, binary: bool) -> None:
        """Switch between continuous curve and on/off threshold mode."""
        if binary == self.binary:
            return
        if binary:
            self.threshold = threshold_from_config(self.config)
            self.config = binary_config_from_threshold(self.threshold)
        self.binary = binary
        prepared = prepare_brightness_config(self.config)
        self._levels = create_brightness_map(prepared)
        self._reverse = create_reverse_brightness_map(prepared, self._levels)

    def set_threshold(self, threshold: int) -> None:
        """Update the on/off threshold (binary lights only)."""
        self.threshold = max(1, min(100, int(threshold)))
        self.config = binary_config_from_threshold(self.threshold)
        prepared = prepare_brightness_config(self.config)
        self._levels = create_brightness_map(prepared)
        self._reverse = create_reverse_brightness_map(prepared, self._levels)

    def translate(self, room_brightness: int) -> int:
        """Map room brightness (0–255) to this light's brightness (0–255)."""
        level = max(0, min(255, int(room_brightness)))
        if self.binary:
            # On/off lights: fully on once room brightness reaches the threshold.
            room_pct = 0 if level <= 0 else _brightness_to_percent(level)
            return 255 if room_pct >= self.threshold else 0
        mapped = self._levels.get(level, level)
        return mapped

    def translate_back(self, entity_brightness: int) -> list[int]:
        """Map entity brightness back to possible room levels."""
        if entity_brightness is None:
            return []
        if self.binary:
            if int(entity_brightness) <= 0:
                # Any room level below the threshold.
                return list(range(0, _value_to_brightness(self.threshold)))
            return list(range(_value_to_brightness(self.threshold), 256))
        return list(self._reverse.get(int(entity_brightness), []))

    def as_percent_points(self) -> list[dict[str, int]]:
        """Return user-facing control points as percent pairs."""
        if self.binary:
            return [{"virtual": self.threshold, "real": 100}]
        return [
            {"virtual": int(k), "real": int(v)}
            for k, v in sorted(
                ((int(k), int(v)) for k, v in self.config.items()),
                key=lambda item: item[0],
            )
        ]

    def sample_percent(self, virtual_pct: int) -> int:
        """Sample the curve at a virtual percent (0–100), returning real percent."""
        if self.binary:
            return 100 if virtual_pct >= self.threshold else 0
        room = 0 if virtual_pct <= 0 else _value_to_brightness(virtual_pct)
        return _brightness_to_percent(self.translate(room))


class BrightnessCurveSet:
    """Per-light brightness curves for one Adaptive Lighting instance."""

    def __init__(
        self,
        curves: dict[str, CurveConfig] | None = None,
        *,
        binary_lights: set[str] | None = None,
    ) -> None:
        binary_lights = binary_lights or set()
        self._curves = {
            entity_id: BrightnessCurve(
                config,
                binary=entity_id in binary_lights,
            )
            for entity_id, config in (curves or {}).items()
        }
        self._binary_lights = set(binary_lights)

    @classmethod
    def from_options(
        cls,
        options: dict[str, Any],
        *,
        binary_lights: set[str] | None = None,
    ) -> BrightnessCurveSet:
        """Build from config entry options (`brightness_curves` key)."""
        raw = options.get(CONF_BRIGHTNESS_CURVES, {}) or {}
        if not isinstance(raw, dict):
            return cls(binary_lights=binary_lights)
        return cls(
            {eid: normalize_curve_config(cfg) for eid, cfg in raw.items()},
            binary_lights=binary_lights,
        )

    def update_binary_lights(self, binary_lights: set[str]) -> None:
        """Refresh which entity IDs are treated as on/off."""
        self._binary_lights = set(binary_lights)
        for entity_id, curve in self._curves.items():
            curve.set_binary(entity_id in self._binary_lights)

    def get(self, entity_id: str) -> BrightnessCurve:
        """Return the curve for a light, defaulting to identity."""
        if entity_id not in self._curves:
            self._curves[entity_id] = BrightnessCurve(
                binary=entity_id in self._binary_lights,
            )
        return self._curves[entity_id]

    def translate(self, entity_id: str, room_brightness: int) -> int:
        """Translate room brightness for a specific light."""
        return self.get(entity_id).translate(room_brightness)

    def set_curve(self, entity_id: str, config: CurveConfig) -> None:
        """Replace the curve for a light."""
        self._curves[entity_id] = BrightnessCurve(
            config,
            binary=entity_id in self._binary_lights,
        )

    def remove_curve(self, entity_id: str) -> None:
        """Drop a light's custom curve (reverts to identity)."""
        self._curves.pop(entity_id, None)

    def prune(self, entity_ids: set[str]) -> None:
        """Remove curves for lights no longer in the instance."""
        for entity_id in list(self._curves):
            if entity_id not in entity_ids:
                del self._curves[entity_id]

    def as_options(self) -> dict[str, CurveConfig]:
        """Serialize for config entry options."""
        return {
            entity_id: curve.config for entity_id, curve in self._curves.items()
        }

    def export_for_ui(self, entity_ids: list[str]) -> dict[str, Any]:
        """Payload for the curve editor (includes defaults for listed lights)."""
        result: dict[str, Any] = {}
        for entity_id in entity_ids:
            curve = self.get(entity_id)
            payload: dict[str, Any] = {
                "binary": curve.binary,
                "points": curve.as_percent_points(),
                "samples": [curve.sample_percent(i) for i in range(101)],
            }
            if curve.binary:
                payload["threshold"] = curve.threshold
            result[entity_id] = payload
        return result


# Deferred import-friendly constant (also declared in const.py).
CONF_BRIGHTNESS_CURVES = "brightness_curves"
DEFAULT_BRIGHTNESS_CURVES: dict[str, CurveConfig] = {}
