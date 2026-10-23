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
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant, callback
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
        return BrightnessCurveSet.from_options(
            {CONF_BRIGHTNESS_CURVES: data.get(CONF_BRIGHTNESS_CURVES, {})}
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
            self._attr_is_on = False
            self._attr_brightness = None
            return

        curves = self._curves
        any_on = False
        level_sets: list[set[int]] = []
        for entity_id in lights:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if state.state == STATE_ON:
                any_on = True
                brightness = state.attributes.get(ATTR_BRIGHTNESS, 255) or 0
            else:
                brightness = 0
            level_sets.append(set(curves.get(entity_id).translate_back(brightness)))

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

    async def async_apply_room_brightness(
        self,
        brightness: int | None,
        *,
        transition: float | None = None,
        color_temp_kelvin: int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
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
                entity_brightness = curves.translate(entity_id, room)
                data: dict[str, Any] = {"entity_id": entity_id}
                if transition is not None:
                    data[ATTR_TRANSITION] = transition

                if entity_brightness <= 0 or not self._attr_is_on:
                    await self.hass.services.async_call(
                        "light",
                        SERVICE_TURN_OFF,
                        data,
                        blocking=True,
                        context=context,
                    )
                    continue

                data[ATTR_BRIGHTNESS] = entity_brightness
                if color_temp_kelvin is not None:
                    data[ATTR_COLOR_TEMP_KELVIN] = color_temp_kelvin
                if rgb_color is not None:
                    data[ATTR_RGB_COLOR] = rgb_color

                await self.hass.services.async_call(
                    "light",
                    SERVICE_TURN_ON,
                    data,
                    blocking=True,
                    context=context,
                )
        finally:
            self._applying = False
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set room brightness (and forward through curves)."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            brightness = self._attr_brightness or 255

        # Prefer Adaptive Lighting's current colour when available.
        color_temp = None
        rgb = None
        transition = kwargs.get(ATTR_TRANSITION)
        switch = self._find_switch()
        if switch is not None and getattr(switch, "_settings", None):
            settings = switch._settings  # noqa: SLF001
            color_temp = settings.get("color_temp_kelvin")
            rgb = settings.get("rgb_color")

        await self.async_apply_room_brightness(
            brightness,
            transition=transition,
            color_temp_kelvin=color_temp,
            rgb_color=tuple(rgb) if rgb else None,
            context=self._context,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all member lights."""
        transition = kwargs.get(ATTR_TRANSITION)
        self._attr_is_on = False
        await self.async_apply_room_brightness(
            0,
            transition=transition,
            context=self._context,
        )

    def _find_switch(self):
        """Return the AdaptiveSwitch for this config entry, if loaded."""
        from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN

        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
        return entry_data.get(SWITCH_DOMAIN)
