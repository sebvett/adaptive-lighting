"""Light capability helpers (from Lightener's on/off vs dimmable split)."""

from __future__ import annotations

from homeassistant.components.light import (
    brightness_supported,
    get_supported_color_modes,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

TYPE_DIMMABLE = "dimmable"
TYPE_ONOFF = "on_off"


def get_light_type(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return TYPE_DIMMABLE or TYPE_ONOFF for a light entity."""
    try:
        supported_color_modes = get_supported_color_modes(hass, entity_id)
    except HomeAssistantError:
        return None
    if not supported_color_modes:
        return None
    return (
        TYPE_DIMMABLE
        if brightness_supported(supported_color_modes)
        else TYPE_ONOFF
    )


def is_binary_light(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True when the light has no brightness control."""
    return get_light_type(hass, entity_id) == TYPE_ONOFF
