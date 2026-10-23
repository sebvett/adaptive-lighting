"""WebSocket API for Adaptive Lighting brightness curves."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .brightness_curves import (
    BrightnessCurveSet,
    binary_config_from_threshold,
    normalize_curve_config,
    threshold_from_config,
)
from .const import CONF_BRIGHTNESS_CURVES, CONF_LIGHTS, CONF_NAME, DOMAIN
from .light_util import is_binary_light

_LOGGER = logging.getLogger(__name__)


@callback
def async_register_websockets(hass: HomeAssistant) -> None:
    """Register Adaptive Lighting websocket commands."""
    if hass.data.setdefault(DOMAIN, {}).get("_ws_registered"):
        return
    websocket_api.async_register_command(hass, ws_list_instances)
    websocket_api.async_register_command(hass, ws_get_curves)
    websocket_api.async_register_command(hass, ws_save_curves)
    hass.data[DOMAIN]["_ws_registered"] = True


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    return hass.config_entries.async_get_entry(entry_id)


def _lights_for_entry(entry: ConfigEntry) -> list[str]:
    data = {**entry.data, **entry.options}
    return list(data.get(CONF_LIGHTS, []))


def _friendly_names(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, str]:
    registry = async_get_entity_registry(hass)
    names: dict[str, str] = {}
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        if entry and entry.name:
            names[entity_id] = entry.name
        elif state and state.name:
            names[entity_id] = state.name
        else:
            names[entity_id] = entity_id
    return names


@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/list_instances",
    }
)
@callback
def ws_list_instances(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List Adaptive Lighting config entries for the curve editor."""
    instances = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        lights = _lights_for_entry(entry)
        instances.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title or entry.data.get(CONF_NAME, entry.entry_id),
                "lights": lights,
                "names": _friendly_names(hass, lights),
            }
        )
    connection.send_result(msg["id"], {"instances": instances})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/get_curves",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_get_curves(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return per-light brightness curves for an Adaptive Lighting instance."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    lights = _lights_for_entry(entry)
    data = {**entry.data, **entry.options}
    binary_lights = {eid for eid in lights if is_binary_light(hass, eid)}
    curves = BrightnessCurveSet.from_options(
        {CONF_BRIGHTNESS_CURVES: data.get(CONF_BRIGHTNESS_CURVES, {})},
        binary_lights=binary_lights,
    )
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "title": entry.title or entry.data.get(CONF_NAME, entry.entry_id),
            "lights": lights,
            "names": _friendly_names(hass, lights),
            "binary_lights": sorted(binary_lights),
            "curves": curves.export_for_ui(lights),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "adaptive_lighting/save_curves",
        vol.Required("entry_id"): str,
        vol.Required("curves"): dict,
    }
)
@callback
def ws_save_curves(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist brightness curves into config entry options."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Config entry not found")
        return

    lights = set(_lights_for_entry(entry))
    binary_lights = {eid for eid in lights if is_binary_light(hass, eid)}
    raw_curves: dict[str, Any] = msg["curves"]
    cleaned: dict[str, dict[str, int]] = {}
    for entity_id, payload in raw_curves.items():
        if entity_id not in lights:
            continue
        if not isinstance(payload, dict):
            continue
        if entity_id in binary_lights or payload.get("binary"):
            if "threshold" in payload:
                threshold = int(payload["threshold"])
            elif "points" in payload and payload["points"]:
                threshold = threshold_from_config(
                    {
                        str(int(p["virtual"])): int(p.get("real", 100))
                        for p in payload["points"]
                        if int(p.get("virtual", 0)) >= 1
                    }
                )
            else:
                threshold = threshold_from_config(payload)
            cleaned[entity_id] = binary_config_from_threshold(threshold)
            continue
        if "points" in payload:
            points = {
                str(int(p["virtual"])): int(p["real"])
                for p in payload["points"]
                if int(p.get("virtual", 0)) >= 1
            }
        else:
            points = payload
        cleaned[entity_id] = normalize_curve_config(points)  # type: ignore[arg-type]

    new_options = {**entry.options, CONF_BRIGHTNESS_CURVES: cleaned}
    hass.config_entries.async_update_entry(entry, options=new_options)

    # Hot-update the running switch if present.
    switch = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("switch")
    if switch is not None and hasattr(switch, "brightness_curves"):
        switch.brightness_curves = BrightnessCurveSet.from_options(
            {CONF_BRIGHTNESS_CURVES: cleaned},
            binary_lights=binary_lights,
        )

    connection.send_result(msg["id"], {"ok": True, "curves": cleaned})
