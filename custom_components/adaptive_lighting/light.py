"""Adaptive Lighting virtual room light (Lightener-style).

Each Adaptive Lighting config entry exposes one virtual light that represents
room brightness. Colour temperature still comes from Adaptive Lighting's sun
logic; per-light brightness is remapped through Lightener-style curves.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    brightness_supported,
    get_supported_color_modes,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .brightness_curves import BrightnessCurveSet
from .const import (
    CONF_BRIGHTNESS_CURVES,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
)
from .light_util import is_binary_light

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the virtual room light for an Adaptive Lighting instance."""
    async_add_entities([AdaptiveLightingRoomLight(hass, config_entry)])


class AdaptiveLightingRoomLight(LightEntity):
    """Virtual light representing Adaptive Lighting room brightness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Room"
    _attr_translation_key = "room"

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_room"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.data.get(CONF_NAME, config_entry.title),
            manufacturer="Adaptive Lighting",
            model="Room brightness",
        )
        self._attr_is_on = False
        self._attr_brightness = None
        self._unsub = None
        self._applying = False

    @property
    def _lights(self) -> list[str]:
        data = {**self._config_entry.data, **self._config_entry.options}
        return list(data.get(CONF_LIGHTS, []))

    @property
    def _curves(self) -> BrightnessCurveSet:
        data = {**self._config_entry.data, **self._config_entry.options}
        lights = list(data.get(CONF_LIGHTS, []))
        binary = {eid for eid in lights if is_binary_light(self.hass, eid)}
        return BrightnessCurveSet.from_options(
            {CONF_BRIGHTNESS_CURVES: data.get(CONF_BRIGHTNESS_CURVES, {})},
            binary_lights=binary,
        )

    async def async_added_to_hass(self) -> None:
        """Track member lights and seed state."""
        await super().async_added_to_hass()
        self._unsub = async_track_state_change_event(
            self.hass,
            self._lights,
            self._async_member_changed,
        )
        self._refresh_from_members()

        domain_data = self.hass.data.setdefault(DOMAIN, {})
        entry_data = domain_data.setdefault(self._config_entry.entry_id, {})
        entry_data["room_light"] = self

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        if entry_data and entry_data.get("room_light") is self:
            entry_data.pop("room_light", None)
        await super().async_will_remove_from_hass()

    @callback
    def _async_member_changed(self, _event) -> None:
        if self._applying:
            return
        self._refresh_from_members()
        self.async_write_ha_state()

    def _refresh_from_members(self) -> None:
        """Infer room on/brightness from controlled lights (Lightener-style)."""
        lights = self._lights
        if not lights:
            # Keep last commanded room level when no members are configured yet.
            return

        curves = self._curves
        any_on = False
        level_sets: list[set[int]] = []
        for entity_id in lights:
            state = self.hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                continue
            if state.state == STATE_ON:
                any_on = True
                brightness = state.attributes.get(ATTR_BRIGHTNESS, 255) or 0
            else:
                brightness = 0
            level_sets.append(set(curves.get(entity_id).translate_back(brightness)))

        if not level_sets:
            return

        self._attr_is_on = any_on
        if not any_on:
            self._attr_brightness = None
            return

        common: set[int] | None = None
        for levels in level_sets:
            common = levels if common is None else common & levels
        if common:
            # Prefer keeping the current room level when it still matches.
            if self._attr_brightness in common:
                return
            self._attr_brightness = sorted(common)[len(common) // 2]
        elif self._attr_brightness is None:
            self._attr_brightness = 128

    def apply_room_brightness(
        self,
        brightness: int,
        *,
        transition: float | None = None,
        color_temp_kelvin: int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
        context=None,
    ) -> None:
        """Schedule applying room brightness through curves (used by AL switch)."""
        self.hass.async_create_task(
            self.async_apply_room_brightness(
                brightness,
                transition=transition,
                color_temp_kelvin=color_temp_kelvin,
                rgb_color=rgb_color,
                context=context,
            )
        )

    def _color_payload_for_light(
        self,
        entity_id: str,
        *,
        color_temp_kelvin: int | None,
        rgb_color: tuple[int, int, int] | None,
        prefer_rgb: bool,
    ) -> dict[str, Any]:
        """Pick at most one HA colour descriptor for this light.

        Home Assistant rejects turn_on calls that set both color_temp and
        rgb_color (exclusion group 'Color descriptors').
        """
        try:
            modes = get_supported_color_modes(self.hass, entity_id) or set()
        except HomeAssistantError:
            modes = set()

        payload: dict[str, Any] = {}
        supports_ct = ColorMode.COLOR_TEMP in modes
        supports_rgb = bool(
            modes
            & {
                ColorMode.RGB,
                ColorMode.RGBW,
                ColorMode.RGBWW,
                ColorMode.HS,
                ColorMode.XY,
            }
        )

        use_rgb = prefer_rgb and supports_rgb and rgb_color is not None
        if use_rgb:
            payload[ATTR_RGB_COLOR] = list(rgb_color)
        elif supports_ct and color_temp_kelvin is not None:
            state = self.hass.states.get(entity_id)
            min_k = (state.attributes.get("min_color_temp_kelvin") if state else None) or 2000
            max_k = (state.attributes.get("max_color_temp_kelvin") if state else None) or 6500
            payload[ATTR_COLOR_TEMP_KELVIN] = max(min_k, min(max_k, int(color_temp_kelvin)))
        elif supports_rgb and rgb_color is not None:
            payload[ATTR_RGB_COLOR] = list(rgb_color)
        return payload

    async def async_apply_room_brightness(
        self,
        brightness: int | None,
        *,
        transition: float | None = None,
        color_temp_kelvin: int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
        prefer_rgb: bool = False,
        context=None,
        only_lights: list[str] | None = None,
    ) -> None:
        """Apply room brightness to member lights via Lightener curves."""
        self._applying = True
        try:
            if brightness is not None:
                self._attr_brightness = max(0, min(255, int(brightness)))
                self._attr_is_on = self._attr_brightness > 0

            curves = self._curves
            targets = only_lights if only_lights is not None else self._lights
            room = self._attr_brightness or 0

            for entity_id in targets:
                state = self.hass.states.get(entity_id)
                if state is None or state.state == STATE_UNAVAILABLE:
                    _LOGGER.debug(
                        "Skipping unavailable member light %s while applying room brightness",
                        entity_id,
                    )
                    continue

                entity_brightness = curves.translate(entity_id, room)
                data: dict[str, Any] = {"entity_id": entity_id}
                if transition is not None:
                    data[ATTR_TRANSITION] = transition

                binary = is_binary_light(self.hass, entity_id)

                if entity_brightness <= 0 or not self._attr_is_on:
                    try:
                        await self.hass.services.async_call(
                            "light",
                            SERVICE_TURN_OFF,
                            data,
                            blocking=True,
                            context=context,
                        )
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Failed to turn off %s from room light: %s",
                            entity_id,
                            err,
                        )
                    continue

                if binary:
                    # On/off lights: never send brightness or colour descriptors.
                    service = SERVICE_TURN_ON
                else:
                    data[ATTR_BRIGHTNESS] = entity_brightness
                    data.update(
                        self._color_payload_for_light(
                            entity_id,
                            color_temp_kelvin=color_temp_kelvin,
                            rgb_color=rgb_color,
                            prefer_rgb=prefer_rgb,
                        )
                    )
                    # Colour-only / brightness-only safety: never send brightness
                    # to lights that cannot dim (already handled via binary).
                    try:
                        modes = get_supported_color_modes(self.hass, entity_id)
                    except HomeAssistantError:
                        modes = None
                    if modes is not None and not brightness_supported(modes):
                        data.pop(ATTR_BRIGHTNESS, None)
                    service = SERVICE_TURN_ON

                try:
                    await self.hass.services.async_call(
                        "light",
                        service,
                        data,
                        blocking=True,
                        context=context,
                    )
                except HomeAssistantError as err:
                    _LOGGER.warning(
                        "Failed to apply room brightness to %s: %s (data=%s)",
                        entity_id,
                        err,
                        data,
                    )
        finally:
            self._applying = False
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set room brightness (and forward through curves)."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            brightness = self._attr_brightness or 255

        # Prefer Adaptive Lighting's current colour when available — but only
        # one descriptor at a time (HA forbids colour_temp + rgb together).
        color_temp = None
        rgb = None
        prefer_rgb = False
        transition = kwargs.get(ATTR_TRANSITION)
        switch = self._find_switch()
        if switch is not None and getattr(switch, "_settings", None):
            settings = switch._settings  # noqa: SLF001
            color_temp = settings.get("color_temp_kelvin")
            rgb = settings.get("rgb_color")
            prefer_rgb = bool(
                settings.get("force_rgb_color")
                or getattr(switch, "_prefer_rgb_color", False)
            )

        await self.async_apply_room_brightness(
            brightness,
            transition=transition,
            color_temp_kelvin=color_temp,
            rgb_color=tuple(rgb) if rgb else None,
            prefer_rgb=prefer_rgb,
            context=getattr(self, "_context", None),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all member lights."""
        transition = kwargs.get(ATTR_TRANSITION)
        self._attr_is_on = False
        await self.async_apply_room_brightness(
            0,
            transition=transition,
            context=getattr(self, "_context", None),
        )

    def _find_switch(self):
        """Return the AdaptiveSwitch for this config entry, if loaded."""
        from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN

        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
        return entry_data.get(SWITCH_DOMAIN)
