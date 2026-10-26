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
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    brightness_supported,
    get_supported_color_modes,
)

# Legacy mireds colour attr — string form avoids importing deprecated ATTR_COLOR_TEMP.
_ATTR_COLOR_TEMP_MIREDS = "color_temp"
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
        # Optional in-memory curves for AL Curves playhead preview (not saved).
        self._preview_curves: BrightnessCurveSet | None = None

    @property
    def _lights(self) -> list[str]:
        data = {**self._config_entry.data, **self._config_entry.options}
        return list(data.get(CONF_LIGHTS, []))

    @property
    def _curves(self) -> BrightnessCurveSet:
        if self._preview_curves is not None:
            return self._preview_curves
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
        # While we are still issuing member commands, ignore intermediate
        # updates; after apply finishes we refresh once, and later HA state
        # changes (delayed device feedback) flow through here normally.
        if self._applying:
            return
        self._refresh_from_members()
        self.async_write_ha_state()

    def _refresh_from_members(
        self,
        *,
        prefer: int | None = None,
        previous: int | None = None,
    ) -> None:
        """Infer room on/brightness from controlled lights (Lightener-style).

        Room brightness mirrors underlying member feedback — the same signal
        you get when a single light snaps back after a failed ``turn_on``:

        * Prefer a room level consistent with **all** members' reverse-mapped
          brightness (curve ``translate_back``).
        * If ``prefer`` (the last commanded room level) is in that set, use it.
        * Else if ``previous`` (pre-command room level) is in that set, keep it —
          members never reached the target (no response / snap-back).
        * If members disagree (partial response), use the average of each
          light's preferred reverse-mapped room level.
        """
        lights = self._lights
        if not lights:
            # Keep last commanded room level when no members are configured yet.
            return

        curves = self._curves
        any_on = False
        level_sets: list[set[int]] = []
        preferred_levels: list[int] = []
        for entity_id in lights:
            state = self.hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                continue
            if state.state == STATE_ON:
                any_on = True
                brightness = state.attributes.get(ATTR_BRIGHTNESS, 255) or 0
            else:
                brightness = 0
            levels = set(curves.get(entity_id).translate_back(brightness))
            level_sets.append(levels)
            if levels:
                preferred_levels.append(sorted(levels)[len(levels) // 2])

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
            if prefer is not None and prefer in common:
                self._attr_brightness = prefer
            elif previous is not None and previous in common:
                # Members still consistent with the pre-command room level.
                self._attr_brightness = previous
            elif prefer is not None:
                self._attr_brightness = min(
                    common, key=lambda value: abs(value - prefer)
                )
            else:
                self._attr_brightness = sorted(common)[len(common) // 2]
        elif preferred_levels:
            # Partial / inconsistent members — average reverse-mapped room %.
            self._attr_brightness = max(
                0, min(255, round(sum(preferred_levels) / len(preferred_levels)))
            )
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
            modes = set(get_supported_color_modes(self.hass, entity_id) or [])
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
        """Apply room brightness to member lights via Lightener curves.

        The commanded room level is used only to translate member targets.
        Reported room brightness is derived afterwards from members' HA state
        (reverse-mapped through curves), so unresponsive lights do not leave
        the room stuck at the target — same feedback model as a single light.
        """
        if brightness is not None:
            commanded = max(0, min(255, int(brightness)))
        else:
            commanded = self._attr_brightness or 0
        want_on = commanded > 0
        # Capture pre-command level before holding the in-flight command so
        # ``_refresh_from_members`` can restore it when members never move.
        previous_brightness = self._attr_brightness

        self._applying = True
        try:
            # Hold the commanded level in-memory so concurrent AL adaptation
            # (e.g. ``_virtual_room_brightness``) sees the in-flight room
            # command. HA state is not published until members report — see
            # finally → ``_refresh_from_members``.
            if brightness is not None:
                self._attr_brightness = commanded
                self._attr_is_on = want_on

            curves = self._curves  # honors _preview_curves when set by Curves editor
            targets = only_lights if only_lights is not None else self._lights
            room = commanded

            # Mark member calls as Adaptive Lighting's own so the turn_on
            # interceptor does not re-adapt them with sun brightness and wipe
            # the curve-mapped levels we are about to send.
            call_context = self._member_call_context(context)

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

                if entity_brightness <= 0 or not want_on:
                    try:
                        await self.hass.services.async_call(
                            "light",
                            SERVICE_TURN_OFF,
                            data,
                            blocking=True,
                            context=call_context,
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
                        context=call_context,
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
            # Publish room state from member feedback, not the commanded target.
            self._refresh_from_members(
                prefer=commanded if brightness is not None else None,
                previous=previous_brightness,
            )
            self.async_write_ha_state()

    def _member_call_context(self, parent=None):
        """Build a context the AL interceptor treats as our own."""
        switch = self._find_switch()
        if switch is not None and hasattr(switch, "create_context"):
            return switch.create_context("room", parent=parent)
        return parent

    def _brightness_from_kwargs(self, kwargs: dict[str, Any]) -> int | None:
        """Return caller-supplied brightness, or None for a bare turn_on."""
        if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] is not None:
            return max(0, min(255, int(kwargs[ATTR_BRIGHTNESS])))
        if ATTR_BRIGHTNESS_PCT in kwargs and kwargs[ATTR_BRIGHTNESS_PCT] is not None:
            return max(0, min(255, round(255 * float(kwargs[ATTR_BRIGHTNESS_PCT]) / 100)))
        return None

    def _color_from_kwargs(
        self, kwargs: dict[str, Any]
    ) -> tuple[int | None, tuple[int, int, int] | None, bool]:
        """Return (kelvin, rgb, prefer_rgb) when the caller supplied colour."""
        if ATTR_RGB_COLOR in kwargs and kwargs[ATTR_RGB_COLOR] is not None:
            rgb = kwargs[ATTR_RGB_COLOR]
            return None, (int(rgb[0]), int(rgb[1]), int(rgb[2])), True
        if ATTR_COLOR_TEMP_KELVIN in kwargs and kwargs[ATTR_COLOR_TEMP_KELVIN] is not None:
            return int(kwargs[ATTR_COLOR_TEMP_KELVIN]), None, False
        if (
            _ATTR_COLOR_TEMP_MIREDS in kwargs
            and kwargs[_ATTR_COLOR_TEMP_MIREDS] is not None
        ):
            mireds = max(1, int(kwargs[_ATTR_COLOR_TEMP_MIREDS]))
            return max(1000, min(10000, round(1_000_000 / mireds))), None, False
        # HS / XY are colour equivalents — treat as "caller supplied colour" so
        # we do not overwrite with Adaptive Lighting's colour temp.
        if kwargs.get(ATTR_HS_COLOR) is not None or kwargs.get(ATTR_XY_COLOR) is not None:
            return None, None, False
        return None, None, False

    def _al_settings(self, switch, transition: float | None) -> dict[str, Any]:
        """Ensure Adaptive Lighting settings exist and return them."""
        if switch is None:
            return {}
        if not getattr(switch, "_settings", None) and hasattr(
            switch, "_sun_light_settings"
        ):
            switch._settings = switch._sun_light_settings.get_settings(  # noqa: SLF001
                switch.sleep_mode_switch.is_on,
                transition
                if transition is not None
                else getattr(switch, "_transition", None),
            )
        return getattr(switch, "_settings", None) or {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set room brightness (and forward through curves).

        Bare ``turn_on`` (no brightness / colour in the call) uses Adaptive
        Lighting's current brightness and colour temperature — same as turning
        an individual member light on. Explicit brightness or colour in the
        service call wins.
        """
        transition = kwargs.get(ATTR_TRANSITION)
        switch = self._find_switch()
        settings = self._al_settings(switch, transition)

        brightness = self._brightness_from_kwargs(kwargs)
        if brightness is None:
            bri_pct = settings.get("brightness_pct")
            if bri_pct is not None:
                brightness = max(0, min(255, round(255 * float(bri_pct) / 100)))
            else:
                brightness = self._attr_brightness or 255

        # Prefer caller-supplied colour; otherwise Adaptive Lighting's current
        # colour — but only one descriptor at a time (HA forbids colour_temp +
        # rgb together).
        color_temp, rgb, prefer_rgb = self._color_from_kwargs(kwargs)
        caller_supplied_color = any(
            key in kwargs and kwargs[key] is not None
            for key in (
                ATTR_COLOR_TEMP_KELVIN,
                _ATTR_COLOR_TEMP_MIREDS,
                ATTR_RGB_COLOR,
                ATTR_HS_COLOR,
                ATTR_XY_COLOR,
            )
        )
        if not caller_supplied_color and settings:
            color_temp = settings.get("color_temp_kelvin")
            rgb_setting = settings.get("rgb_color")
            rgb = tuple(rgb_setting) if rgb_setting else None
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
        """Turn off all member lights.

        Room off/brightness follows member feedback after commands land
        (see ``async_apply_room_brightness``), not an optimistic local clear.
        """
        transition = kwargs.get(ATTR_TRANSITION)
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
